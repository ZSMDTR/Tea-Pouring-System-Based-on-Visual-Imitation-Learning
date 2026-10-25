"""Benchmark direct RTCQueue consumption with the loaded model, never hardware.

This deliberately bypasses UI rate limits in a separate consumer. Every output
must equal one returned model row exactly. No interpolation or robot/camera
factory is imported or constructed. Results measure scheduling, not task quality.
"""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inference
import experiments
from inference_worker import PolicyConnection
from rtc.config import DEFAULTS
from rtc.runtime import RTCQueue

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--rates',type=float,nargs='+',default=[50,100,150,200,225,250,275,300,350,0],help='0 means unpaced consumer, offline only')
parser.add_argument('--seconds',type=float,default=5)
parser.add_argument('--s-min',type=int,default=15)
parser.add_argument('--denoise',type=int,default=5)
parser.add_argument('--output',type=Path,default=ROOT/'.runtime/native_rtc_benchmark_20261010.json')
args=parser.parse_args()
if not 1<=args.seconds<=30 or any(not math.isfinite(hz) or hz<0 for hz in args.rates):parser.error('Invalid duration or rate')

def device_idle():
    s=inference.session()
    if s.get('active') and s.get('state') not in ('completed','off','preview'):
        raise RuntimeError('A device task is active; benchmark refused')
    folder=inference.recording.current_dir()
    if folder and (folder/'command.json').exists():raise RuntimeError('A device task is queued')

def can_counters():
    return {name:int((Path('/sys/class/net/can0/statistics')/name).read_text())
            for name in ('tx_packets','rx_packets','tx_errors','rx_errors')}

device_idle()
if inference.session().get('active'):raise RuntimeError('Close camera preview before this benchmark')
if experiments.status().get('active'):raise RuntimeError('Another experiment is active')
model=inference.model_status()
assert model.get('active') and model.get('state')=='ready' and model.get('rtc_ready')

with ExitStack() as locks:
    # Prevent workbench device/training starts during a short offline run. These
    # locks never open a CAN socket or cause a motor/camera connection.
    for p in (inference.recording.JOBS/'device.lock',experiments.JOBS/'job.lock'):
        handle=locks.enter_context(p.open('a'))
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    device_idle()
    before=can_counters()
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    repo='local/b601_rs_red_screwdriver_orange_container_v2crop_20260901'
    dataset=LeRobotDataset(repo,root=Path(json.loads((inference.ROOT/'config.json').read_text())['dataset_root']).parent/repo)
    frames=[]
    for index in (0,15,30,45):
        frame=dataset[index]
        frames.append({'state':frame['observation.state'].numpy(),
                       'wrist_rgb':(frame['observation.images.wrist_rgb'].numpy().transpose(1,2,0)*255).round().clip(0,255).astype(np.uint8),
                       'prompt':inference.PROMPT})
    report={'robot_connected':False,'camera_connected':False,'interpolation':False,
            'model_id':model['id'],'checkpoint':model['model'],'dataset':repo,
            'scope':'Separate offline direct-action consumer; not the workbench hardware runner or task-quality validation.',
            'rtc_runtime_sha256':hashlib.sha256((ROOT/'rtc/runtime.py').read_bytes()).hexdigest(),
            'requested_seconds_per_case':args.seconds,'can_before':before,'measurements':[]}
    last_check=[0.]
    def guard():
        now=time.monotonic()
        if now-last_check[0]>.25:
            device_idle()
            state=inference.model_status()
            if state.get('id')!=model['id'] or state.get('state')!='ready':raise RuntimeError('Loaded model changed')
            last_check[0]=now

    def publish():
        report['can_after']=can_counters()
        report['can_counters_unchanged']=report['can_after']==before
        report['finished_at']=time.time()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))

    for hz in args.rates:
        guard()
        connection=PolicyConnection({'model_id':model['id'],'port':model['port']})
        latencies=[];timestamps=[];delay_samples=[];query_indices=[]
        class TrackedPolicy:
            def infer(self,obs,reason):
                started=time.monotonic()
                reply=connection.infer(obs,reason)
                latencies.append({'ms':(time.monotonic()-started)*1000,'overlap':reply['rtc']['overlap'],'delay':reply['rtc']['delay']})
                return reply
        # Seed delay in the same unit as direct action consumption. Initial UI
        # d is capped at 20; runtime history may subsequently grow beyond 20.
        initial_delay=min(20,max(1,math.ceil(.1*hz))) if hz else 20
        s_min=max(args.s_min,initial_delay)
        config={**DEFAULTS,'enabled':True,'s_min':s_min,'delay_steps':initial_delay,'denoise_steps':args.denoise}
        queue=RTCQueue(TrackedPolicy(),config)
        status='completed';error=None;initial_ms=None;start=None
        try:
            t=time.monotonic();action=queue.next_action(frames[0],lambda:None)
            initial_ms=(time.monotonic()-t)*1000
            assert np.array_equal(action,queue.chunk[queue.index-1])
            start=time.monotonic();timestamps.append(start)
            period=1/hz if hz else 0
            next_tick=start+period
            while True:
                guard()
                if period:time.sleep(max(0,next_tick-time.monotonic()))
                now=time.monotonic()
                if now-start>=args.seconds:break
                old_replans=queue.replans
                action=queue.next_action(frames[int((now-start)*2)%len(frames)],lambda:None)
                assert np.isfinite(action).all() and np.array_equal(action,queue.chunk[queue.index-1])
                sent=time.monotonic();timestamps.append(sent)
                if queue.replans!=old_replans:delay_samples.append(queue.last_delay)
                if queue.future is not None and queue.query_index not in query_indices:query_indices.append(queue.query_index)
                next_tick+=period
                if period and next_tick<sent:next_tick=sent+period
        except Exception as exc:
            status='failed';error=str(exc)
        finally:
            # Let an in-flight model response finish before starting the next
            # case, keeping its latency out of subsequent initial measurements.
            if queue.future is not None:
                try:queue.future.result(timeout=12)
                except Exception:pass
            metrics=queue.metrics();queue.close();connection.close()
        gaps=np.diff(timestamps)*1000
        steady=latencies[1:]
        result={'target_hz':hz or 'unpaced','status':status,'error':error,'rtc_config':config,
                'initial_inference_ms':round(initial_ms,2) if initial_ms is not None else None,
                'consumed_direct_model_actions':len(timestamps),'all_consumed_actions_exact_model_rows':True,
                'active_consumer_seconds':round(timestamps[-1]-timestamps[0],3) if timestamps else 0,
                'actual_consumer_hz':round((len(timestamps)-1)/(timestamps[-1]-timestamps[0]),2) if len(timestamps)>1 else 0,
                'gap_p95_ms':round(float(np.percentile(gaps,95)),3) if len(gaps) else None,
                'max_gap_ms':round(float(max(gaps)),3) if len(gaps) else None,
                'late_interval_percent':round(float(np.mean(gaps>1500/hz)*100),2) if len(gaps) and hz else None,
                'inference_mean_ms':round(float(np.mean([x['ms'] for x in steady])),2) if steady else None,
                'inference_max_ms':round(max(x['ms'] for x in steady),2) if steady else None,
                'measured_delay_steps':delay_samples,'query_start_indices':query_indices,
                'inference_requests':latencies,'queue':metrics}
        report['measurements'].append(result);publish()
        print(json.dumps({k:result[k] for k in ['target_hz','status','error','actual_consumer_hz','active_consumer_seconds','inference_mean_ms','inference_max_ms','max_gap_ms','late_interval_percent']},ensure_ascii=False),flush=True)
    publish()
print('Report:',args.output)
