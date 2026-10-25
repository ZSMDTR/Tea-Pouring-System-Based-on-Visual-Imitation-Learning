"""Camera contracts and fail-closed execution, using synthetic frames only."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app  # Load the app/sqlite before numerical libraries in this environment.
import camera_contract as cameras
import inference_worker as worker
from live_preview import view_folder
import numpy as np


class ContractTests(unittest.TestCase):
    def dataset(self, root, dual):
        (root/'meta').mkdir(exist_ok=True)
        features={'observation.images.'+view:{'dtype':'video','shape':[480,640,3]}
                  for view in (['wrist_rgb','head_rgb'] if dual else ['wrist_rgb'])}
        (root/'meta/info.json').write_text(json.dumps({'features':features}))

    def test_new_training_uses_available_views_but_old_resume_keeps_wrist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.dataset(root,True)
            self.assertEqual(cameras.for_training(root),cameras.contract(cameras.DUAL))
            self.assertEqual(cameras.for_training(root,root/'old_run/10'),cameras.contract())

    def test_dual_resume_rejects_missing_head_and_corrupt_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.dataset(root,False)
            checkpoint=root/'dual_run/10';checkpoint.mkdir(parents=True)
            (checkpoint/'camera_contract.json').write_text(json.dumps(cameras.contract(cameras.DUAL)))
            with self.assertRaises(ValueError):cameras.for_training(root,checkpoint)
            (checkpoint/'camera_contract.json').write_text('{"mode":"wrist_head"}')
            with self.assertRaises(ValueError):cameras.model(checkpoint)

    def test_early_sidecar_and_complete_checkpoint_contract_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint=Path(tmp)/'run/10';checkpoint.mkdir(parents=True)
            cameras.sidecar(checkpoint.parent).write_text(json.dumps(cameras.contract(cameras.DUAL)))
            self.assertEqual(cameras.model(checkpoint)['mode'],cameras.DUAL)
            (checkpoint/'complete.json').write_text(json.dumps({'workbench':{'identity':{'camera_contract':cameras.contract()}}}))
            with self.assertRaises(ValueError):cameras.model(checkpoint)

    def test_preview_roles_cannot_escape_session_directory(self):
        self.assertEqual(view_folder('/tmp/session','head'),Path('/tmp/session/head'))
        with self.assertRaises(ValueError):view_folder('/tmp/session','../../other')


class Camera:
    def __init__(self,value=1,stale=False):
        self.pipeline=SimpleNamespace(frame_lock=threading.Lock(),frames_seq=1,
            latest_frames=value,last_frames_ts=time.time()-(1 if stale else 0))
        self.async_read=Mock(side_effect=AssertionError('must not wait for a new frame'))

    def _read_from_frames(self, value):
        return np.full((480,640,3),value,np.uint8)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.task={'task_id':'dual','model_id':'model','prompt':'task','relative_limit':5,'seconds':1,
                   'hz':120,'chunk':10,'timing_mode':'native','camera_contract':cameras.contract(cameras.DUAL)}
        self.wrist=Camera(11);self.head=Camera(22)
        self.robot=Mock();self.robot.get_observation.return_value={name+'.pos':0 for name in worker.JOINTS}
        self.policy=Mock();self.policy.infer.return_value={'actions':np.zeros((50,7))}

    def run_task(self,head):
        with patch.object(worker,'cleanup',return_value=[]) as cleanup:
            result=worker.execute(self.task,{},self.wrist,
                lambda:'stopped' if self.robot.send_action.called else None,
                lambda *a,**k:None,lambda _:None,lambda *_:self.robot,lambda _:self.policy,secondary_camera=head)
            return result,cleanup.call_args

    def test_both_views_reach_policy_without_camera_wait_at_120hz(self):
        result,cleanup=self.run_task(self.head)
        self.assertEqual(result['outcome'],'stopped');self.assertTrue(cleanup.args[2])
        observation=self.policy.infer.call_args.args[0]
        self.assertEqual(observation['wrist_rgb'].mean(),11)
        self.assertEqual(observation['head_rgb'].mean(),22)
        self.wrist.async_read.assert_not_called();self.head.async_read.assert_not_called()
        self.assertEqual(result['camera_contract'],cameras.contract(cameras.DUAL))

    def test_missing_or_frozen_required_camera_never_enables_robot(self):
        for camera in (None,Camera(22,stale=True)):
            result,_=self.run_task(camera)
            self.assertEqual(result['outcome'],'error')
            self.robot.connect.assert_not_called();self.robot.send_action.assert_not_called()

    def test_camera_freezes_during_inference_no_action_is_sent(self):
        def infer(*args):
            self.head.pipeline.last_frames_ts=time.time()-1
            self.head.pipeline.frames_seq+=1
            return {'actions':np.zeros((50,7))}
        self.policy.infer.side_effect=infer
        result,cleanup=self.run_task(self.head)
        self.assertEqual(result['outcome'],'error');self.robot.send_action.assert_not_called()
        self.assertFalse(cleanup.args[2])

    def test_legacy_model_ignores_optional_head_without_changing_payload(self):
        self.task['camera_contract']=cameras.contract()
        result,_=self.run_task(Camera(22,stale=True))
        self.assertEqual(result['outcome'],'stopped')
        self.assertNotIn('head_rgb',self.policy.infer.call_args.args[0])


if __name__=='__main__':unittest.main()
