"""Uses real LeRobot storage and synthetic devices only; all output is temporary."""
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import record_worker as worker

class FakeRobot:
    name='seeed_b601_rs_follower'
    action_features={f'joint{i}.pos':float for i in range(7)}
    observation_features={**{f'joint{i}.pos':float for i in range(7)},'wrist_rgb':(48,64,3)}
    def __init__(self, folder, mode):
        self.folder=folder; self.mode=mode; self.n=0; self.motors={}; self.cameras={}; self.bus=None
        self._emergency_disable_requested=False;self.returned=False
    def connect(self,calibrate=False):
        if self.mode=='connect_error': raise RuntimeError('synthetic connect failure')
    def get_observation(self):
        return {**{f'joint{i}.pos':float(self.n) for i in range(7)},'wrist_rgb':np.full((48,64,3),self.n%255,dtype=np.uint8)}
    def send_action(self,action):
        self.n+=1
        if self.n==3:
            if self.mode=='manual':(self.folder/'stop').touch()
            if self.mode=='lost_page':
                import os
                os.utime(self.folder/'heartbeat',(1,1))
            if self.mode=='action_error':raise RuntimeError('synthetic motor failure')
        return {'joint.pos':999.0} # Must not accidentally double-map replay actions.
    def safe_zero(self,exit_on_complete=False):
        self.returned=True
        if self.mode=='return_error':raise RuntimeError('synthetic return failure')

class FakeLeader:
    is_connected=False
    def connect(self,calibrate=False):self.is_connected=True
    def get_action(self):return {f'joint{i}.pos':2.0 for i in range(7)}
    def disconnect(self):self.is_connected=False

class CaptureIntegration(unittest.TestCase):
    def run_case(self,mode):
        handlers=[signal.getsignal(signal.SIGINT),signal.getsignal(signal.SIGTERM)]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp)/'job';folder.mkdir();(folder/'heartbeat').touch()
                request={'id':'test','dataset':'synthetic','root':str(Path(tmp)/'data'),'task':'synthetic test','seconds':0 if mode in ('manual','lost_page') else .18}
                robot=FakeRobot(folder,mode);leader=FakeLeader()
                dataset=worker.make_dataset(robot,request,30)
                if mode=='save_error':dataset.save_episode=lambda:(_ for _ in ()).throw(RuntimeError('synthetic save failure'))
                result=worker.capture(folder,request,robot,leader,dataset,{'fps':30})
                self.assertFalse(leader.is_connected)
                self.assertEqual(robot.returned,mode!='connect_error')
                if mode=='save_error' or mode=='connect_error':self.assertFalse(result['saved'])
                else:
                    self.assertTrue(result['saved'])
                    info=json.loads((Path(request['root'])/'meta/info.json').read_text())
                    self.assertEqual(info['total_episodes'],1)
                    self.assertEqual(info['total_frames'],result['frames'])
                    import pyarrow.parquet as pq
                    parquet=next((Path(request['root'])/'data').rglob('*.parquet'))
                    self.assertTrue(all(row==[2.0]*7 for row in pq.read_table(parquet).column('action').to_pylist()))
                    import cv2
                    video=cv2.VideoCapture(str(next((Path(request['root'])/'videos').rglob('*.mp4'))))
                    self.assertTrue(video.read()[0]);video.release()
                self.assertEqual(result['state'],'completed' if mode in ('timed','manual') else 'error')
                if mode=='manual':self.assertEqual(result['frames'],3)
                self.assertEqual(result['acceptance'],'pending_replay')
        finally:
            signal.signal(signal.SIGINT,handlers[0]);signal.signal(signal.SIGTERM,handlers[1])
    def test_modes(self):
        for mode in ('timed','manual','lost_page','connect_error','return_error','action_error','save_error'):
            with self.subTest(mode=mode):self.run_case(mode)

if __name__=='__main__':unittest.main()
