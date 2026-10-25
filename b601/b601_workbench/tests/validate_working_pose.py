"""Actual installed action mapping with simulated motors only. Never opens CAN."""
import copy
import math
import logging
import json
from pathlib import Path
import sys
import tempfile
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inference
import recording
import inference_worker as worker
import working_pose as pose
from b601_driver_adapter import WorkbenchB601RSFollower
from lerobot_robot_seeed_b601 import SeeedB601RSFollowerConfig


class Clock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0., seconds)


class Motor:
    def __init__(self, name, angle, owner):
        self.name, self.owner = name, owner
        self.state = SimpleNamespace(pos=math.radians(angle), vel=0., torq=0., t_mos=25.)

    def get_state(self):
        return self.state

    def request_feedback(self):
        pass

    def send_mit(self, position, *args):
        self.owner.events.append((self.owner.clock(), self.name, position))
        self.owner.operations.append(('send',self.name))
        if self.name != 'gripper' and not self.owner.stuck:
            self.state.pos = position

    def disable(self):
        self.owner.operations.append(('disable',self.name))

    def close(self):
        self.owner.operations.append(('close',self.name))


class Robot(WorkbenchB601RSFollower):
    # Overrides construct/lifecycle only; send_action/get_observation are real.
    def __init__(self, clock):
        self.clock, self.events, self.stuck = clock, [], False
        self.operations=[]
        self.config = SeeedB601RSFollowerConfig(id='follower1', port='NO_CAN', cameras={},
                                              gravity_compensation=False,
                                              max_relative_target={n:150. if n=='gripper' else 10.
                                                                   for n in pose.JOINTS})
        self.id = 'follower1'
        dataset_root = Path(json.loads((inference.ROOT/'config.json').read_text())['dataset_root'])
        self.calibration_fpath = dataset_root.parent/'calibration/robots/seeed_b601_rs_follower/follower1.json'
        self.bus = Mock()
        self.cameras = {}
        self.motor_names = list(pose.JOINTS)
        self.motors = {n:Motor(n, 30. if n=='gripper' else 0., self) for n in pose.JOINTS}
        self._in_safe_zero = False
        self._emergency_disable_requested = False
        self._skip_safe_zero_on_disconnect = True
        self.connect = Mock()


class Camera:
    def __init__(self, clock):
        self.clock = clock

    def async_read(self, **kwargs):
        return np.full((480, 640, 3), int(self.clock()*10)%256, dtype=np.uint8)


class PoseTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.robot = Robot(self.clock)
        self.value = pose.preset()
        self.addCleanup(lambda:setattr(self.robot, 'bus', None))
        quiet=patch.object(logging,'warning');quiet.start();self.addCleanup(quiet.stop)

    def move(self, check=lambda:None):
        return pose.move_to_ready(self.robot, self.value, check, lambda **_:None,
                                  clock=self.clock, sleep=self.clock.sleep)

    def test_exact_motor_mapping_gripper_hold_and_bounded_speed(self):
        caps = self.robot.config.max_relative_target
        kp = dict(self.robot.config.mit_kp)
        result = self.move()
        self.assertTrue(result['verified'])
        for n, target in self.value['motor_degrees'].items():
            self.assertAlmostEqual(math.degrees(self.robot.motors[n].state.pos), target)
        self.assertAlmostEqual(result['measured_motor_degrees']['gripper'], 30.)
        self.assertIs(self.robot.config.max_relative_target, caps)
        self.assertEqual(self.robot.config.mit_kp, kp)
        for n in pose.ARM_JOINTS:
            sends = [(t, math.degrees(a)) for t, name, a in self.robot.events if name==n]
            for (ta, a), (tb, b) in zip(sends, sends[1:]):
                self.assertGreater(tb, ta)
                self.assertLessEqual(abs(b-a)/(tb-ta), 20.0001)
        self.assertGreaterEqual(result['elapsed'], 7.5)

    def test_missing_feedback_fails_before_commands(self):
        self.robot.motors['shoulder_lift'].state = None
        with self.assertRaisesRegex(RuntimeError, '缺少关节反馈'):
            self.move()
        self.assertEqual(self.robot.events, [])

    def test_nonfinite_feedback_fails_before_commands(self):
        self.robot.motors['elbow_flex'].state.pos = math.nan
        with self.assertRaisesRegex(RuntimeError, '反馈无效'):
            self.move()
        self.assertEqual(self.robot.events, [])

    def test_mapping_model_calibration_and_range_are_checked(self):
        for change in ['mapping', 'model', 'calibration', 'range']:
            with self.subTest(change=change):
                cfg, value = copy.deepcopy(self.robot.config), copy.deepcopy(self.value)
                if change=='mapping':cfg.joint_directions['elbow_flex']=1
                if change=='model':value['urdf_sha256']='changed'
                if change=='calibration':value['calibration_sha256']='changed'
                if change=='range':value['motor_degrees']['shoulder_lift']=180
                original=self.robot.config;self.robot.config=cfg
                try:
                    with self.assertRaises(ValueError):pose.validate_robot(self.robot,value)
                finally:self.robot.config=original
        self.assertEqual(self.robot.events, [])

    def test_stuck_joint_times_out(self):
        self.robot.stuck = True
        with self.assertRaisesRegex(pose.ArrivalTimeout, '确认超时.*模型未启动'):
            self.move()

    def test_stationary_positions_pass_despite_biased_reported_speed(self):
        self.robot.motors['shoulder_lift'].state.vel = math.radians(20)
        result=self.move()
        self.assertTrue(result['verified'])
        self.assertAlmostEqual(result['reported_speed_deg_s'],20)
        self.assertEqual(result['window_motion_deg'],0)
        self.assertGreaterEqual(result['window_duration_s'],.4)

    def oscillate_near_pose(self, amplitude=2):
        original=self.robot.get_observation
        self.robot.stuck=True
        def observe():
            for name,target in self.value['motor_degrees'].items():
                offset=amplitude*(1 if int(self.clock()*50)%2 else -1) if name=='shoulder_lift' else 0
                self.robot.motors[name].state.pos=math.radians(target+offset)
            return original()
        self.robot.get_observation=observe

    def test_oscillating_positions_never_pass_even_if_reported_speed_is_zero(self):
        self.oscillate_near_pose()
        with self.assertRaises(pose.ArrivalTimeout) as error:self.move()
        diagnostics=error.exception.diagnostics
        self.assertAlmostEqual(diagnostics['max_error_deg'],2)
        self.assertAlmostEqual(diagnostics['window_motion_deg'],4)
        self.assertEqual(diagnostics['reported_speed_deg_s'],0)
        self.assertIn('位置仍在变化',diagnostics['settle_status'])

    def test_small_position_jitter_can_settle(self):
        self.oscillate_near_pose(amplitude=.2)
        self.robot.motors['shoulder_lift'].state.vel=math.radians(20)
        result=self.move()
        self.assertTrue(result['verified'])
        self.assertAlmostEqual(result['window_motion_deg'],.4)

    def test_nonfinite_velocity_is_still_rejected(self):
        self.robot.motors['shoulder_lift'].state.vel=math.nan
        with self.assertRaisesRegex(RuntimeError,'反馈无效'):self.move()
        self.assertEqual(self.robot.events,[])

    def test_cancel_during_trajectory_prevents_next_send(self):
        def check():
            if self.robot.events:raise worker.Interrupted('stopped')
        with self.assertRaises(worker.Interrupted):self.move(check)
        self.assertEqual(len(self.robot.events), 7)

    def test_camera_failure_prevents_commands(self):
        def check():raise RuntimeError('camera stale')
        with self.assertRaisesRegex(RuntimeError, 'camera stale'):self.move(check)
        self.assertEqual(self.robot.events, [])

    def test_validation_preserves_old_api_and_rejects_arbitrary_poses(self):
        data={'prompt':'task', 'prepared':True}
        self.assertEqual(inference.validate_task(data)['startup_pose'], 'horizontal')
        self.assertEqual(inference.validate_task({**data,'startup_pose':'official_center_ready'})['startup_pose'], 'official_center_ready')
        for invalid in [None, False, 1, 'custom', {'shoulder_lift':90}]:
            with self.assertRaises(ValueError):inference.validate_task({**data,'startup_pose':invalid})
        self.assertIn('positioning', inference.RUNNING)
        self.assertIn('positioning', recording.ACTIVE)

    def execute(self, reason, infer, camera=None, timing_mode='legacy', rtc=None,cleanup_impl=None):
        task={'task_id':'test', 'model_id':'simulated', 'port':1, 'prompt':'task',
              'startup_pose':'official_center_ready','relative_limit':10,'gripper_relative_limit':150,
              'seconds':30,'hz':120,'chunk':15,'timing_mode':timing_mode,'continuous':True}
        if rtc is not None:task['rtc']=rtc
        camera=camera or Camera(self.clock)
        def advance(seconds):
            self.clock.sleep(seconds)
            if hasattr(camera,'produce'):camera.produce()
        policy=Mock();policy.infer.side_effect=infer
        events=[]
        with patch.object(worker.time,'monotonic',side_effect=self.clock), \
             patch.object(worker.time,'time',side_effect=lambda:1000+self.clock()), \
             patch.object(worker.time,'sleep',side_effect=advance), \
             patch.object(worker,'cleanup',return_value=[],side_effect=cleanup_impl) as cleanup:
            result=worker.execute(task,{},camera,reason,
                                  lambda *a,**kw:events.append((a,kw)),lambda _:None,
                                  lambda *_:self.robot,lambda _:policy)
        return result,policy,cleanup.call_args,events

    def test_model_waits_for_verified_pose_and_sees_new_image(self):
        inferred=[]
        def infer(obs,*_):
            for n in pose.ARM_JOINTS:
                self.assertAlmostEqual(float(obs['state'][pose.JOINTS.index(n)]),
                                       self.value['motor_degrees'][n],places=4)
            self.assertGreater(int(obs['wrist_rgb'][0,0,0]), 0)
            inferred.append(True)
            return {'actions':np.zeros((50,7))}
        result,policy,cleanup,events=self.execute(lambda:'stopped' if inferred else None,infer)
        self.assertEqual(result['outcome'],'stopped')
        self.assertTrue(result['startup_pose_result']['verified'])
        self.assertEqual(result['steps'],0)  # Startup commands are not policy steps.
        self.assertEqual(policy.infer.call_count,1)
        self.assertTrue(cleanup.args[2])
        self.assertTrue(any(a[0]=='positioning' for a,kw in events))

    def test_failed_pose_never_calls_policy_or_forces_return(self):
        self.robot.stuck=True
        result,policy,cleanup,_=self.execute(lambda:None,lambda *_:None)
        self.assertEqual(result['outcome'],'error')
        self.assertTrue(any('官方待命位确认超时' in e for e in result['errors']))
        policy.infer.assert_not_called()
        self.assertFalse(cleanup.args[2])

    def test_near_pose_timeout_returns_all_joints_before_disabling(self):
        from record_worker import cleanup as real_cleanup
        self.oscillate_near_pose()
        def clean(robot,leader,requested):
            self.robot.stuck=False
            return real_cleanup(robot,leader,requested)
        result,policy,cleanup,events=self.execute(lambda:None,lambda *_:None,cleanup_impl=clean)
        self.assertEqual(result['outcome'],'error')
        self.assertTrue(cleanup.args[2])
        self.assertTrue(result['return_attempted'])
        self.assertTrue(result['return_completed'])
        self.assertFalse(result['startup_pose_result']['verified'])
        policy.infer.assert_not_called()
        for name in pose.ARM_JOINTS:self.assertAlmostEqual(self.robot.motors[name].state.pos,0)
        sends=[i for i,event in enumerate(self.robot.operations) if event[0]=='send']
        disables=[i for i,event in enumerate(self.robot.operations) if event[0]=='disable']
        self.assertEqual(len(disables),7)
        self.assertLess(max(sends),min(disables))
        self.assertIn('已按原流程回水平位并失能',result['message'])

    def test_failed_timeout_return_is_not_reported_as_completed(self):
        self.oscillate_near_pose()
        result,policy,cleanup,_=self.execute(lambda:None,lambda *_:None,
                              cleanup_impl=lambda *_:['回零失败：synthetic failure'])
        self.assertTrue(cleanup.args[2])
        self.assertFalse(result['return_completed'])
        self.assertEqual(result['outcome'],'error')
        self.assertIn('未确认已安全回位',result['message'])

    def test_feedback_fault_at_timeout_prevents_forced_return(self):
        self.robot.motors['shoulder_lift'].state=None
        error=pose.ArrivalTimeout({'max_error_deg':.7,'settle_status':'位置仍在变化'},True)
        with patch.object(pose,'move_to_ready',side_effect=error):
            result,policy,cleanup,_=self.execute(lambda:None,lambda *_:None)
        self.assertFalse(cleanup.args[2])
        self.assertFalse(result['return_attempted'])
        self.assertTrue(any('缺少关节反馈' in e for e in result['errors']))
        policy.infer.assert_not_called()

    def test_startup_trace_records_diagnostics_and_worker_revision(self):
        handlers=[signal.getsignal(signal.SIGTERM),signal.getsignal(signal.SIGINT)]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp);(folder/'heartbeat').touch()
                task={'task_id':'test','prompt':'task','hz':30}
                (folder/'command.json').write_text(json.dumps(task))
                camera=Mock();camera.async_read.return_value=np.zeros((480,640,3),np.uint8)
                sample={'progress':100,'max_error_deg':.7,'window_motion_deg':.2,
                        'reported_speed_deg_s':20,'measured_motor_degrees':{'shoulder_lift':56.1}}
                def execute(task,profile,camera,reason,update,preview):
                    update('positioning','test',startup_progress=sample)
                    (folder/'stop').touch()
                    return {'outcome':'stopped','message':'test complete','startup_pose_result':None}
                with patch.object(worker,'execute',side_effect=execute),patch.object(worker.time,'sleep'):
                    worker.run(folder,{'id':'s','profile':{}},camera)
                row=json.loads((folder/'startup_test.jsonl').read_text())
                self.assertEqual(row['reported_speed_deg_s'],20)
                self.assertEqual(row['window_motion_deg'],.2)
                state=json.loads((folder/'status.json').read_text())
                self.assertEqual(state['working_pose_version'],2)
        finally:
            signal.signal(signal.SIGTERM,handlers[0]);signal.signal(signal.SIGINT,handlers[1])

    def test_manual_stop_during_pose_requests_original_return(self):
        result,policy,cleanup,_=self.execute(lambda:'stopped' if self.robot.events else None,lambda *_:None)
        self.assertEqual(result['outcome'],'stopped')
        policy.infer.assert_not_called()
        self.assertTrue(cleanup.args[2])

    def test_bad_mapping_fails_before_enable(self):
        self.robot.config.joint_directions['elbow_flex']=1.
        result,policy,cleanup,_=self.execute(lambda:None,lambda *_:None)
        self.robot.connect.assert_not_called()
        self.assertEqual(self.robot.events,[])
        self.assertFalse(cleanup.args[2])
        self.assertEqual(result['outcome'],'error')

    def test_native_rtc_uses_fresh_cache_and_adapts_timing_after_arrival(self):
        from validate_execution_timing import Camera as CachedCamera
        camera=CachedCamera(self.clock)
        inferred=[]
        original=self.robot.configure_command_timing
        def configure(hz):
            self.assertGreaterEqual(self.clock(),7.5)
            for name,target in self.value['motor_degrees'].items():
                self.assertAlmostEqual(math.degrees(self.robot.motors[name].state.pos),target)
            original(hz)
        self.robot.configure_command_timing=Mock(side_effect=configure)
        def infer(obs,*_):
            self.assertEqual(obs['_rtc']['config']['denoise_steps'],5)
            self.assertGreater(int(obs['wrist_rgb'][0,0,0]),0)
            inferred.append(True)
            return {'actions':np.zeros((50,7)),'rtc':{'guided':True}}
        result,policy,cleanup,_=self.execute(lambda:'stopped' if inferred else None,infer,
            camera=camera,timing_mode='native',rtc={'enabled':True,'s_min':15,'denoise_steps':5})
        self.assertEqual(result['outcome'],'stopped')
        self.assertTrue(result['startup_pose_result']['verified'])
        self.assertEqual(result['steps'],0)
        self.assertEqual(camera.blocking_reads,0)
        self.robot.configure_command_timing.assert_called_once_with(120)
        self.assertTrue(cleanup.args[2])

    def test_camera_stalling_during_startup_prevents_model_execution(self):
        from validate_execution_timing import Camera as CachedCamera
        camera=CachedCamera(self.clock)
        camera.produce=Mock()  # Initial cache is valid, then never refreshes.
        result,policy,cleanup,_=self.execute(lambda:None,lambda *_:None,
                                            camera=camera,timing_mode='native')
        self.assertEqual(result['outcome'],'error')
        self.assertTrue(any('相机' in e for e in result['errors']))
        self.assertLess(self.clock(),.3)
        self.assertEqual(result['steps'],0)
        policy.infer.assert_not_called()
        self.assertFalse(cleanup.args[2])

    def test_api_rejects_old_worker_before_queueing_new_pose(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            for extra in ({},{'supports_working_pose':True,'working_pose_version':1}):
                with patch.object(inference,'session',return_value={'id':'s','active':True,'state':'preview',**extra}), \
                     patch.object(recording,'current_dir',return_value=folder):
                    with self.assertRaisesRegex(ValueError,'关闭预览后重新启动'):
                        inference.action('execute',{'id':'s','prompt':'task','prepared':True,
                                         'startup_pose':'official_center_ready'}, {})
            self.assertFalse((folder/'command.json').exists())

    def test_api_allows_stop_but_blocks_finish_close_and_unload_during_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            state={'id':'s','task_id':'t','active':True,'state':'positioning','steps':0}
            with patch.object(inference,'session',return_value=state), \
                 patch.object(recording,'current_dir',return_value=folder):
                for action in ('finish','close','unload'):
                    with self.subTest(action=action),self.assertRaises(ValueError):
                        inference.action(action,{'id':'s','task_id':'t'}, {})
                self.assertFalse((folder/'stop').exists())
                inference.action('stop_task',{'id':'s','task_id':'t'}, {})
            self.assertEqual(json.loads((folder/'end.json').read_text()),
                             {'task_id':'t','reason':'stopped'})


if __name__=='__main__':unittest.main()
