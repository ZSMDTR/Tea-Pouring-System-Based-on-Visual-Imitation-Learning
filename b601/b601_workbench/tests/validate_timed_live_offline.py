"""Real running model + historical observations + simulated 30 FPS producer.

Never constructs a hardware robot or camera. Refuses to share the model with an
executing device session. Run explicitly with the existing LeRobot environment.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inference
import inference_worker as worker
from rtc.config import DEFAULTS

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--rates',nargs='+',type=float,help='RTC command rates to test, with policy timebase fixed at 30Hz')
parser.add_argument('--seconds',type=float,default=4)
parser.add_argument('--output',type=Path,default=ROOT/'.runtime/timed_execution_offline_result.json')
args=parser.parse_args()
if not 1<=args.seconds<=30:parser.error('--seconds must be in 1..30')
if args.rates:
    for hz in args.rates:
        inference.validate_task({'prompt':'offline','prepared':True,'timing_mode':'timed','policy_hz':30,'hz':hz})


def ensure_idle():
    session=inference.session()
    if session.get('active') and session.get('state') not in ('preview','completed','off'):
        raise RuntimeError('A device task is active; offline validation refused.')
    folder=inference.recording.current_dir()
    if folder and (folder/'command.json').exists():
        raise RuntimeError('A device task is queued; offline validation refused.')


ensure_idle()
model=inference.model_status()
assert model.get('active') and model.get('state')=='ready' and model.get('rtc_ready')

# Import only the dataset reader. No robot/camera factory is used below.
from lerobot.datasets.lerobot_dataset import LeRobotDataset
repo='local/b601_rs_red_screwdriver_orange_container_v2crop_20260901'
data=LeRobotDataset(repo,root=Path(json.loads((inference.ROOT/'config.json').read_text())['dataset_root']).parent/repo)
observations=[]
for index in (0,15,30,45):
    frame=data[index]
    observations.append({'state':frame['observation.state'].numpy(),
                         'wrist_rgb':(frame['observation.images.wrist_rgb'].numpy().transpose(1,2,0)*255).round().clip(0,255).astype(np.uint8)})


class BufferedHistoricalCamera:
    def __init__(self):
        self.pipeline=SimpleNamespace(frame_lock=threading.Lock(),frames_seq=1,
                                      latest_frames=0,last_frames_ts=time.time())
        self.stop=threading.Event()
        self.conversions=0
        self.thread=threading.Thread(target=self.produce,name='offline-30fps-camera',daemon=True)
        self.thread.start()

    def produce(self):
        start=time.monotonic()
        while not self.stop.wait(1/30):
            with self.pipeline.frame_lock:
                self.pipeline.latest_frames=min(int(time.monotonic()-start),len(observations)-1)
                self.pipeline.frames_seq+=1
                self.pipeline.last_frames_ts=time.time()

    def _read_from_frames(self,index):
        self.conversions+=1
        return observations[index]['wrist_rgb']

    def async_read(self,**kwargs):
        raise AssertionError('Timed execution attempted a blocking frame read')

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


class SimulatedRobot:
    _emergency_disable_requested=False

    def __init__(self,camera):
        self.camera=camera
        self.sent=[]
        self.threads=set()

    def connect(self,**kwargs):
        pass

    def get_observation(self):
        with self.camera.pipeline.frame_lock:
            state=observations[self.camera.pipeline.latest_frames]['state']
        return {name+'.pos':float(value) for name,value in zip(worker.JOINTS,state)}

    def send_action(self,action):
        assert np.isfinite(list(action.values())).all()
        self.sent.append(time.monotonic())
        self.threads.add(threading.current_thread().name)


measurements=[]
cases=[(True,hz,args.seconds) for hz in args.rates] if args.rates else [(True,30,4),(True,50,4),(False,50,2)]
for enabled,hz,seconds in cases:
    ensure_idle()
    camera=BufferedHistoricalCamera()
    robot=SimulatedRobot(camera)
    task={'task_id':'offline','model_id':model['id'],'port':model['port'],
          'prompt':inference.PROMPT,'timing_mode':'timed','policy_hz':30,
          'hz':hz,'seconds':seconds,'chunk':10,'relative_limit':5,'gripper_relative_limit':25,
          'rtc':{**DEFAULTS,'enabled':enabled}}
    checked=[0.0]
    def reason():
        if time.monotonic()-checked[0]>.25:
            ensure_idle()
            checked[0]=time.monotonic()
    try:
        with patch.object(worker,'cleanup',return_value=[]) as cleanup, \
             patch.object(worker,'build_robot',side_effect=AssertionError('Hardware factory forbidden')):
            result=worker.execute(task,{'camera':{'fps':30}},camera,reason,lambda *a,**k:None,
                                  lambda _:None,lambda *_:robot)
        assert result['outcome']=='timeout' and not result['errors'],result
        assert cleanup.call_args.args[0] is robot
        assert robot.threads=={threading.current_thread().name}
        if enabled:
            assert result['rtc_replans']>=3,result
            assert hz*.95<=result['control_hz']<=hz*1.05,result
        gaps=np.diff(robot.sent)*1000
        result.update(robot_connected=False,camera_simulated=True,camera_conversions=camera.conversions,
                      command_gap_p50_ms=round(float(np.percentile(gaps,50)),2),
                      command_gap_p95_ms=round(float(np.percentile(gaps,95)),2))
        # Worker messages describe normal hardware cleanup; do not present those
        # as physical evidence in an offline report.
        result['message']='Offline validation only; no hardware connection or motion.'
        measurements.append(result)
    finally:
        camera.close()

report={'model_id':model['id'],'checkpoint':model['model'],'dataset':repo,
        'robot_connected':False,'camera_simulated':True,'measurements':measurements}
path=args.output
path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'report':str(path),'robot_connected':False,
                  'measurements':[{key:r[key] for key in ('hz','policy_hz','rtc','steps','control_hz','max_action_gap_ms','command_gap_p95_ms','late_intervals','infer_ms','camera_max_frame_age_ms','step_timing_ms')} for r in measurements]},indent=2))
