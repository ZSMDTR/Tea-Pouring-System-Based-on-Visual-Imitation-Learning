"""Exercise the production runner with a real policy and simulated devices only."""
from contextlib import ExitStack
import fcntl
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import experiments
import inference
import inference_worker as worker
from rtc.runtime import RTCQueue
from rtc.config import DEFAULTS

def counters():
    return {name:int((Path('/sys/class/net/can0/statistics')/name).read_text())
            for name in ('tx_packets','rx_packets','tx_errors','rx_errors')}

assert not inference.session().get('active'), 'Close preview or device task first'
assert not experiments.status().get('active'), 'Wait for training/data preparation'
folder=inference.recording.current_dir()
assert not folder or not (folder/'command.json').exists(), 'A device command is queued'
model=inference.model_status()
assert model.get('active') and model.get('state')=='ready' and model.get('rtc_ready')

with ExitStack() as stack:
    for path in (inference.recording.JOBS/'device.lock',experiments.JOBS/'job.lock'):
        handle=stack.enter_context(path.open('a'))
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    before=counters()
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    repo='local/b601_rs_red_screwdriver_orange_container_v2crop_20260901'
    dataset=LeRobotDataset(repo,root=Path(json.loads((inference.ROOT/'config.json').read_text())['dataset_root']).parent/repo)
    frames=[]
    for index in (0,15,30,45):
        frame=dataset[index]
        frames.append((frame['observation.state'].numpy(),
                       (frame['observation.images.wrist_rgb'].numpy().transpose(1,2,0)*255).round().clip(0,255).astype(np.uint8)))

    report={'model_id':model['id'],'checkpoint':model['model'],'hardware_connected':False,
            'scope':'Real production execute(), real model, simulated robot/camera; no motor transport or physical feedback.',
            'can_before':before,'measurements':[]}
    for mode,hz in (('native',100),('native',200),('native',250),('timed',100)):
        state=inference.session()
        assert state.get('state') in ('off','completed','preview')
        assert inference.model_status().get('id')==model['id']
        started=time.monotonic();expected=[];sent=[];returns=[]

        class Camera:
            def __init__(self):
                self.pipeline=SimpleNamespace(frame_lock=threading.Lock(),latest_frames=0,frames_seq=0,last_frames_ts=time.time())
                self.blocking_reads=0
            def produce(self):
                sequence=int((time.monotonic()-started)*30)
                with self.pipeline.frame_lock:
                    if sequence!=self.pipeline.frames_seq:
                        self.pipeline.frames_seq=sequence
                        self.pipeline.latest_frames=sequence
                        self.pipeline.last_frames_ts=time.time()-(time.monotonic()-started-sequence/30)
            def _read_from_frames(self,sequence):return frames[(sequence//15)%4][1]
            def async_read(self,**kwargs):
                self.blocking_reads+=1
                raise AssertionError('No blocking camera read is allowed')

        camera=Camera()
        class Robot:
            def configure_command_timing(self,rate):self.adapted_hz=rate
            def connect(self,**kwargs):pass
            def get_observation(self):
                camera.produce()
                state=frames[int((time.monotonic()-started)*2)%4][0]
                return {name+'.pos':float(v) for name,v in zip(worker.JOINTS,state)}
            def send_action(self,action):
                values=np.array([action[name+'.pos'] for name in worker.JOINTS],np.float32)
                if mode=='native':np.testing.assert_array_equal(values,expected[len(sent)])
                sent.append(values)
        robot=Robot()

        class TrackedQueue(RTCQueue):
            def next_action(self,*args):
                action=super().next_action(*args)
                np.testing.assert_array_equal(action,self.chunk[self.index-1])
                expected.append(action.copy())
                return action

        task={'task_id':'offline','model_id':model['id'],'port':model['port'],
              'prompt':inference.PROMPT,'relative_limit':5,'gripper_relative_limit':25,
              'continuous':False,'seconds':5,'hz':hz,'chunk':10,'timing_mode':mode,'policy_hz':30,
              'rtc':{**DEFAULTS,'enabled':True,'s_min':15 if hz==100 else 20,'delay_steps':10 if mode=='native' and hz==100 else 20 if mode=='native' else 5}}
        def cleanup(robot_arg,camera_arg,normal):
            assert robot_arg is robot and camera_arg is None
            returns.append(normal);return []
        def preview(image):
            ok,_=cv2.imencode('.jpg',cv2.cvtColor(image,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,80])
            assert ok
        with patch.object(worker,'cleanup',side_effect=cleanup),patch('rtc.runtime.RTCQueue',TrackedQueue):
            if mode=='native':
                with patch('execution_timing.sample_actions',side_effect=AssertionError('Native mode interpolated')):
                    result=worker.execute(task,{'camera':{'fps':30}},camera,lambda:None,lambda *a,**k:None,preview,lambda *_:robot)
            else:
                result=worker.execute(task,{'camera':{'fps':30}},camera,lambda:None,lambda *a,**k:None,preview,lambda *_:robot)
        result['simulated_devices']=True
        result['blocking_camera_reads']=camera.blocking_reads
        result['all_sent_actions_exact_model_rows']=len(sent)==len(expected) if mode=='native' else None
        result['normal_return_requested']=returns==[True]
        report['measurements'].append(result)
        report['can_after']=counters()
        report['can_counters_unchanged']=report['can_after']==before
        (ROOT/'.runtime/native_rtc_worker_offline_20261010.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({key:result[key] for key in ('timing_mode','hz','control_hz','steps','outcome','errors','max_action_gap_ms','late_interval_percent','blocking_camera_reads','all_sent_actions_exact_model_rows')},ensure_ascii=False),flush=True)
        assert result['outcome']=='timeout' and result['normal_return_requested']
        assert report['can_counters_unchanged']
print('Offline production-runner test completed; no hardware commands sent.')
