"""Execution regression tests with simulated frames/motors and a fake clock."""
from concurrent.futures import Future
from pathlib import Path
import json
import sys
import threading
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inference
import inference_worker as worker
from execution_timing import LatestFrameReader, TimedRTCQueue, TimedSyncPlayer, sample_actions


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Camera:
    def __init__(self, clock):
        self.clock = clock
        self.pipeline = SimpleNamespace(frame_lock=threading.Lock(), frames_seq=1,
                                        latest_frames=0, last_frames_ts=1000.0)
        self.converted = 0
        self.blocking_reads = 0

    def produce(self):
        sequence = int((self.clock() + 1e-8) * 30) + 1
        with self.pipeline.frame_lock:
            self.pipeline.frames_seq = sequence
            self.pipeline.latest_frames = sequence - 1
            self.pipeline.last_frames_ts = 1000.0 + (sequence - 1) / 30

    def _read_from_frames(self, frame):
        self.converted += 1
        return np.full((480, 640, 3), frame % 256, np.uint8)

    def async_read(self, **kwargs):
        self.blocking_reads += 1
        raise AssertionError("Timed execution must never wait for another camera frame")


class Executor:
    def __init__(self, immediate=False):
        self.jobs = []
        self.immediate = immediate

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((future, fn, args))
        if self.immediate:
            self.finish()
        return future

    def finish(self):
        future, fn, args = self.jobs[-1]
        try:
            future.set_result(fn(*args))
        except BaseException as exc:
            future.set_exception(exc)

    def shutdown(self, **kwargs):
        pass


def ramp(offset=0):
    return np.tile((np.arange(50, dtype=np.float32) + offset)[:, None], (1, 7))


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.camera = Camera(self.clock)
        self.reader = LatestFrameReader(self.camera, clock=self.clock, wall_clock=lambda:1000 + self.clock())

    def test_reuses_30fps_frame_without_blocking_or_reconverting_at_50hz(self):
        first = self.reader.latest()
        self.clock.now = .02
        self.assertIs(self.reader.latest(), first)
        self.assertEqual(self.camera.converted, 1)
        self.clock.now = .04
        self.camera.produce()
        self.assertIsNot(self.reader.latest(), first)
        self.assertEqual(self.camera.blocking_reads, 0)
        self.assertEqual(self.camera.converted, 2)
        self.assertFalse(first.flags.writeable)

    def test_frozen_frame_is_rejected_even_if_repeatedly_polled(self):
        self.reader.latest()
        for ms in (50, 100, 150, 200):
            self.clock.now = ms / 1000
            self.reader.latest()
        self.clock.now = .251
        with self.assertRaisesRegex(TimeoutError, '停止更新|过期'):
            self.reader.latest()

    def test_already_stale_frame_and_invalid_rgb_are_rejected(self):
        self.clock.now = .3
        with self.assertRaises(TimeoutError):
            self.reader.latest()
        self.clock.now = 0
        self.camera._read_from_frames = lambda _:np.zeros((1, 2, 3), np.uint8)
        with self.assertRaises(ValueError):
            self.reader.latest()

    def test_conversion_time_counts_toward_freshness(self):
        original = self.camera._read_from_frames
        def slow(frame):
            self.clock.now += .3
            return original(frame)
        self.camera._read_from_frames = slow
        with self.assertRaises(TimeoutError):
            self.reader.latest()

    def test_no_blocking_fallback_for_unsupported_backend(self):
        camera = SimpleNamespace(async_read=Mock())
        with self.assertRaises(ValueError):
            LatestFrameReader(camera)
        camera.async_read.assert_not_called()


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.obs = {'state':np.zeros(7), 'wrist_rgb':np.zeros((2, 2, 3),np.uint8), 'prompt':'task'}
        self.policy = Mock()
        self.policy.infer.return_value = {'actions':ramp(), 'rtc':{'guided':True}}

    def test_30_to_50_keeps_same_time_scale_and_interpolates_all_seven_axes(self):
        player = TimedSyncPlayer(self.policy, 30, 15, self.clock)
        values = []
        for i in range(11):
            self.clock.now = i / 50
            values.append(player.action_at(self.obs,lambda:None))
        np.testing.assert_allclose(np.asarray(values),np.tile((np.arange(11)*.6)[:,None],(1,7)),atol=1e-5)
        self.assertEqual(self.policy.infer.call_count,1)
        # At the common t=0.1 second both command rates sample policy action 3.
        np.testing.assert_array_equal(values[5],ramp()[3])

    def test_delayed_tick_skips_old_positions_without_bursting(self):
        player = TimedSyncPlayer(self.policy,30,15,self.clock)
        player.action_at(self.obs,lambda:None)
        self.clock.now = .2
        np.testing.assert_array_equal(player.action_at(self.obs,lambda:None),ramp()[6])
        self.assertEqual(self.policy.infer.call_count,1)

    def test_sync_chunk_endpoint_and_replan_are_not_extrapolated(self):
        player = TimedSyncPlayer(self.policy,30,3,self.clock)
        player.action_at(self.obs,lambda:None)
        self.clock.now = .09
        np.testing.assert_array_equal(player.action_at(self.obs,lambda:None),ramp()[2])
        self.clock.now = .1
        player.action_at(self.obs,lambda:None)
        self.assertEqual(self.policy.infer.call_count,2)

    def test_rtc_fractional_prefix_and_delay_use_policy_time_not_command_count(self):
        executor=Executor()
        queue=TimedRTCQueue(self.policy,{'enabled':True,'s_min':5,'delay_steps':2},30,executor,self.clock)
        try:
            queue.action_at(self.obs,lambda:None)
            for i in range(1,10):
                self.clock.now = i / 50
                queue.action_at(self.obs,lambda:None)
            # First eligible 50Hz tick is .18s: 5.4 policy steps.
            self.assertEqual(len(executor.jobs),1)
            np.testing.assert_allclose(executor.jobs[0][2][1][0],np.full(7,5.4),atol=1e-5)
            self.policy.infer.return_value={'actions':ramp(100),'rtc':{'guided':True}}
            for t in (.20,.22,.24,.26):
                self.clock.now=t
                queue.action_at(self.obs,lambda:None)
            executor.finish()
            self.clock.now=.28
            value=queue.action_at(self.obs,lambda:None)
            # .10s elapsed since query: 3 model steps, although 5 command ticks passed.
            np.testing.assert_allclose(value,np.full(7,103),atol=1e-4)
            self.assertEqual(queue.last_delay,3)
            self.assertEqual(queue.replans,1)
        finally:
            queue.close()

    def test_rtc_result_is_sampled_at_fractional_elapsed_time(self):
        executor=Executor()
        queue=TimedRTCQueue(self.policy,{'enabled':True,'s_min':5,'delay_steps':2},30,executor,self.clock)
        try:
            queue.action_at(self.obs,lambda:None)
            self.clock.now=.18
            queue.action_at(self.obs,lambda:None)
            self.policy.infer.return_value={'actions':ramp(100),'rtc':{'guided':True}}
            executor.finish()
            self.clock.now=.265
            np.testing.assert_allclose(queue.action_at(self.obs,lambda:None),np.full(7,102.55),atol=1e-4)
            self.assertEqual(queue.last_delay,3)
        finally:
            queue.close()

    def test_rtc_empty_or_late_plan_never_replays_last_target(self):
        for complete in (False,True):
            self.clock.now=0
            executor=Executor()
            queue=TimedRTCQueue(self.policy,{'enabled':True,'s_min':5,'delay_steps':2},30,executor,self.clock)
            try:
                queue.action_at(self.obs,lambda:None)
                self.clock.now=.2
                queue.action_at(self.obs,lambda:None)
                if complete:executor.finish()
                self.clock.now=2
                with self.assertRaisesRegex(RuntimeError,'耗尽'):
                    queue.action_at(self.obs,lambda:None)
            finally:queue.close()

    def test_invalid_interpolation_is_not_sent(self):
        for position in (-1,50,float('nan')):
            with self.assertRaises(RuntimeError):sample_actions(ramp(),position)
        actions=ramp();actions[1,0]=np.nan
        with self.assertRaises(ValueError):sample_actions(actions,.5)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock()
        self.camera=Camera(self.clock)
        self.robot=Mock()
        self.raw={name+'.pos':0 for name in worker.JOINTS}
        def observe():
            self.camera.produce()
            return self.raw
        self.robot.get_observation.side_effect=observe
        self.policy=Mock()
        def infer(obs,reason):
            # Continuation follows the simulated robot state, so values also test
            # that gripper/sign mapping is not duplicated by interpolation.
            prefix=obs.get('_rtc',{}).get('prefix')
            offset=0 if prefix is None or len(prefix)==0 else float(prefix[0,0])
            return {'actions':ramp(offset),'rtc':{'guided':True}}
        self.policy.infer.side_effect=infer
        self.task={'task_id':'test','model_id':'m','port':1,'prompt':'task','relative_limit':5,
                   'gripper_relative_limit':150,'seconds':.5,'hz':50,'chunk':10,
                   'timing_mode':'timed','policy_hz':30,
                   'rtc':{'enabled':True,'s_min':5,'delay_steps':2}}
        self.events=[]

    def execute(self,reason=lambda:None,executor=None):
        with patch.object(worker.time,'monotonic',side_effect=self.clock), \
             patch.object(worker.time,'time',side_effect=lambda:1000+self.clock()), \
             patch.object(worker.time,'sleep',side_effect=self.clock.sleep), \
             patch.object(worker,'cleanup',return_value=[]) as cleanup, \
             patch('rtc.runtime.ThreadPoolExecutor',side_effect=lambda **_:executor or Executor(True)):
            result=worker.execute(self.task,{'camera':{'fps':30}},self.camera,reason,
                                  lambda *a,**k:self.events.append((a,k)),lambda _:None,
                                  lambda *_:self.robot,lambda _:self.policy)
        return result,cleanup

    def test_50hz_commands_run_with_30fps_camera_and_30hz_policy(self):
        result,cleanup=self.execute()
        self.assertEqual(result['outcome'],'timeout')
        self.assertAlmostEqual(result['control_hz'],50,places=2)
        self.assertEqual(result['steps'],25)
        self.assertEqual(result['late_intervals'],0)
        self.assertEqual(self.camera.blocking_reads,0)
        self.assertLess(self.camera.converted,result['steps'])
        self.assertEqual(result['policy_hz'],30)
        self.robot.configure_command_timing.assert_not_called()
        self.assertTrue(cleanup.call_args.args[2])
        self.assertGreater(result['rtc_replans'],0)
        self.assertIn('send',result['step_timing_ms'])
        actions=np.asarray([list(c.args[0].values()) for c in self.robot.send_action.call_args_list])
        np.testing.assert_allclose(actions[:5],np.tile((np.arange(5)*.6)[:,None],(1,7)),atol=1e-4)
        json.dumps(result)  # All diagnostics remain serializable.

    def test_stop_at_interpolated_tick_prevents_any_further_action(self):
        result,cleanup=self.execute(lambda:'stopped' if self.robot.send_action.call_count>=2 else None)
        self.assertEqual(result['outcome'],'stopped')
        self.assertEqual(result['steps'],2)
        self.assertTrue(cleanup.call_args.args[2])

    def test_higher_rates_keep_30hz_trajectory_and_configure_driver_before_connect(self):
        for hz in (75,100,125,200):
            with self.subTest(hz=hz):
                self.setUp()
                self.task['hz']=hz
                result,_=self.execute()
                self.assertEqual(result['outcome'],'timeout')
                self.assertAlmostEqual(result['control_hz'],hz,places=2)
                self.assertLessEqual(abs(result['steps']-.5*hz),1)
                self.assertEqual(result['late_interval_percent'],0)
                self.robot.configure_command_timing.assert_called_once_with(hz)
                names=[c[0] for c in self.robot.method_calls]
                self.assertLess(names.index('configure_command_timing'),names.index('connect'))
                actions=np.asarray([list(c.args[0].values()) for c in self.robot.send_action.call_args_list])
                np.testing.assert_allclose(actions[:5],np.tile((np.arange(5)*30/hz)[:,None],(1,7)),atol=1e-4)
                np.testing.assert_allclose(actions[int(hz*.4)],np.full(7,12),atol=1e-4)
                self.assertEqual(self.camera.blocking_reads,0)

    def test_slow_sender_reports_missed_cadence_without_catchup_bursts(self):
        self.task['hz']=100
        timestamps=[]
        def slow_send(_):
            self.clock.sleep(.025)
            timestamps.append(self.clock())
        self.robot.send_action.side_effect=slow_send
        result,_=self.execute()
        self.assertEqual(result['outcome'],'timeout')
        self.assertLess(result['control_hz'],100)
        self.assertEqual(result['late_interval_percent'],100)
        self.assertTrue(all(b-a>=.0349 for a,b in zip(timestamps,timestamps[1:])))

    def test_stale_camera_disables_without_forcing_return(self):
        self.robot.get_observation.side_effect=lambda:self.raw  # Camera freezes at t=0.
        result,cleanup=self.execute()
        self.assertEqual(result['outcome'],'error')
        self.assertTrue(any('过期' in error or '停止更新' in error for error in result['errors']))
        self.assertFalse(cleanup.call_args.args[2])
        self.assertLess(result['steps'],15)

    def test_stop_during_initial_inference_sends_no_action(self):
        stopped=[]
        def infer(*_):
            stopped.append(True)
            return {'actions':ramp(),'rtc':{'guided':True}}
        self.policy.infer.side_effect=infer
        result,cleanup=self.execute(lambda:'stopped' if stopped else None)
        self.assertEqual(result['outcome'],'stopped')
        self.robot.send_action.assert_not_called()
        self.assertTrue(cleanup.call_args.args[2])

    def test_sync_mode_also_decouples_camera_and_interpolates(self):
        self.task['rtc']={'enabled':False}
        result,cleanup=self.execute()
        self.assertEqual(result['outcome'],'timeout')
        self.assertAlmostEqual(result['control_hz'],50,places=2)
        self.assertEqual(self.camera.blocking_reads,0)
        self.assertEqual(self.policy.infer.call_count,2)
        values=np.asarray([list(c.args[0].values()) for c in self.robot.send_action.call_args_list])
        np.testing.assert_allclose(values[:5],np.tile((np.arange(5)*.6)[:,None],(1,7)),atol=1e-4)
        self.assertTrue(cleanup.call_args.args[2])

    def test_background_rtc_failure_stops_without_sending_old_plan(self):
        initial=self.policy.infer.side_effect
        def infer(obs,reason):
            if len(obs['_rtc']['prefix']):raise RuntimeError('background model failure')
            return initial(obs,reason)
        self.policy.infer.side_effect=infer
        result,cleanup=self.execute()
        self.assertEqual(result['outcome'],'error')
        self.assertIn('background model failure',result['errors'])
        # At .18s the background request starts; failure surfaces on the next tick.
        self.assertEqual(result['steps'],10)
        self.assertFalse(cleanup.call_args.args[2])

    def test_invalid_cache_rejected_before_motor_connect(self):
        self.camera.pipeline.latest_frames=None
        result,cleanup=self.execute()
        self.assertEqual(result['outcome'],'error')
        self.robot.connect.assert_not_called()
        self.robot.send_action.assert_not_called()

    def test_new_parameters_reject_unsupported_or_inconsistent_values(self):
        base={'prompt':'task','prepared':True,'timing_mode':'timed','policy_hz':30,'hz':50}
        self.assertEqual(inference.validate_task(base)['policy_hz'],30)
        self.assertEqual(inference.validate_task({'prompt':'task','prepared':True})['timing_mode'],'legacy')
        self.assertEqual(inference.validate_task({**base,'hz':250})['hz'],250)
        for change in [{'hz':20},{'hz':251},{'hz':True},{'hz':float('nan')},{'hz':100,'timing_mode':'legacy'},{'policy_hz':True},{'policy_hz':float('nan')},{'policy_hz':0},{'timing_mode':'unknown'}]:
            with self.assertRaises(ValueError):inference.validate_task({**base,**change})

    def test_native_rates_use_exact_model_rows_without_interpolation_or_camera_wait(self):
        for hz in (30,50,100,200,250):
            with self.subTest(hz=hz):
                self.setUp();self.task.update(timing_mode='native',hz=hz)
                with patch('execution_timing.sample_actions',side_effect=AssertionError('Native mode must never interpolate')):
                    result,cleanup=self.execute()
                self.assertEqual(result['outcome'],'timeout')
                self.assertAlmostEqual(result['control_hz'],hz,places=2)
                self.assertFalse(result['interpolation'])
                self.assertEqual(result['policy_hz'],hz)
                self.assertEqual(result['camera_read_mode'],'latest')
                self.assertEqual(self.camera.blocking_reads,0)
                self.assertTrue(cleanup.call_args.args[2])
                sent=np.asarray([list(c.args[0].values()) for c in self.robot.send_action.call_args_list])
                np.testing.assert_array_equal(sent,np.tile(np.arange(len(sent))[:,None],(1,7)))
                if hz>50:self.robot.configure_command_timing.assert_called_once_with(hz)
                else:self.robot.configure_command_timing.assert_not_called()

    def test_native_rtc_keeps_supplying_direct_actions_during_85ms_background_inference(self):
        for hz in (100,200):
            with self.subTest(hz=hz):
                self.setUp();self.task.update(timing_mode='native',hz=hz)
                self.task['rtc']={'enabled':True,'s_min':15 if hz==100 else 20,'delay_steps':10 if hz==100 else 20}
                executor=Executor()
                due=[]
                original_submit=executor.submit
                def submit(*args):
                    future=original_submit(*args);due.append(self.clock()+.085);return future
                executor.submit=submit
                def observe():
                    self.camera.produce()
                    if due and self.clock()+1e-8>=due[-1] and not executor.jobs[-1][0].done():executor.finish()
                    return self.raw
                self.robot.get_observation.side_effect=observe
                result,_=self.execute(executor=executor)
                self.assertEqual(result['outcome'],'timeout')
                self.assertGreater(result['rtc_replans'],1)
                self.assertEqual(result['rtc_delay_steps'],9 if hz==100 else 17)
                self.assertAlmostEqual(result['control_hz'],hz,places=2)
                sent=np.asarray([list(c.args[0].values()) for c in self.robot.send_action.call_args_list])
                np.testing.assert_array_equal(sent,np.tile(np.arange(len(sent))[:,None],(1,7)))

    def test_native_sync_uses_same_frequency_and_no_camera_wait(self):
        self.task.update(timing_mode='native',hz=100,rtc={'enabled':False})
        with patch('execution_timing.sample_actions',side_effect=AssertionError('Unexpected interpolation')):
            result,cleanup=self.execute()
        self.assertEqual(result['outcome'],'timeout')
        self.assertEqual(self.camera.blocking_reads,0)
        self.assertEqual(self.policy.infer.call_count,5)
        values=np.asarray([list(c.args[0].values()) for c in self.robot.send_action.call_args_list])
        np.testing.assert_array_equal(values,np.tile((np.arange(50)%10)[:,None],(1,7)))
        self.assertTrue(cleanup.call_args.args[2])

    def test_native_stop_and_stale_camera_keep_existing_cleanup_rules(self):
        self.task.update(timing_mode='native',hz=100)
        result,cleanup=self.execute(lambda:'stopped' if self.robot.send_action.call_count>=3 else None)
        self.assertEqual(result['steps'],3)
        self.assertEqual(result['outcome'],'stopped')
        self.assertTrue(cleanup.call_args.args[2])
        self.setUp();self.task.update(timing_mode='native',hz=100)
        self.robot.get_observation.side_effect=lambda:self.raw
        result,cleanup=self.execute()
        self.assertEqual(result['outcome'],'error')
        self.assertFalse(cleanup.call_args.args[2])
        self.assertTrue(any('过期' in error or '停止更新' in error for error in result['errors']))

    def test_native_does_not_repeat_old_actions_when_queue_exhausts(self):
        self.task.update(timing_mode='native',hz=200)
        result,cleanup=self.execute(executor=Executor())
        self.assertEqual(result['outcome'],'error')
        self.assertIn('RTC 动作队列耗尽，停止执行。',result['errors'])
        self.assertEqual(result['steps'],50)
        self.assertFalse(cleanup.call_args.args[2])

    def test_native_validation_ignores_disabled_interpolation_timebase(self):
        base={'prompt':'task','prepared':True,'timing_mode':'native'}
        for hz in (30,100,250):
            task=inference.validate_task({**base,'hz':hz,'policy_hz':'disabled field'})
            self.assertEqual(task['policy_hz'],hz)
            self.assertFalse(task['interpolation'])
        for hz in (0,251,True,float('inf')):
            with self.assertRaises(ValueError):inference.validate_task({**base,'hz':hz})

    def test_old_preview_cannot_silently_run_native_task_as_legacy(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(inference,'session',return_value={'id':'old','active':True,'state':'preview','supports_timed_actions':True,'max_command_hz':250}), \
             patch.object(inference.recording,'current_dir',return_value=Path(tmp)):
            with self.assertRaisesRegex(ValueError,'原生动作执行'):
                inference.action('execute',{'id':'old','prompt':'task','prepared':True,'timing_mode':'native','hz':100}, {})
            self.assertFalse((Path(tmp)/'command.json').exists())

    def test_native_api_queues_explicit_mode_and_single_action_timebase(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(inference,'session',return_value={'id':'new','active':True,'state':'preview','supports_native_actions':True,'supports_rtc':True,'max_command_hz':250}), \
             patch.object(inference.recording,'current_dir',return_value=Path(tmp)), \
             patch.object(inference,'model_status',return_value={'id':'model','active':True,'state':'ready','rtc_ready':True,'port':123,'model':'checkpoint'}), \
             patch('can_control.read_state',return_value={}),patch('can_control.ready',return_value=True):
            inference.action('execute',{'id':'new','prompt':'task','prepared':True,'timing_mode':'native','hz':100,'policy_hz':30,'rtc':{'enabled':True}}, {})
            queued=json.loads((Path(tmp)/'command.json').read_text())
            self.assertEqual(queued['timing_mode'],'native')
            self.assertEqual(queued['hz'],100)
            self.assertEqual(queued['policy_hz'],100)
            self.assertFalse(queued['interpolation'])
            self.assertTrue(queued['rtc']['enabled'])

    def test_native_high_rate_still_requires_worker_gripper_adapter_capability(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(inference,'session',return_value={'id':'old','active':True,'state':'preview','supports_native_actions':True,'max_command_hz':50}), \
             patch.object(inference.recording,'current_dir',return_value=Path(tmp)):
            with self.assertRaisesRegex(ValueError,'高频下发适配'):
                inference.action('execute',{'id':'old','prompt':'task','prepared':True,'timing_mode':'native','hz':100}, {})
            self.assertFalse((Path(tmp)/'command.json').exists())

    def test_old_preview_cannot_accept_timed_task(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(inference,'session',return_value={'id':'old','active':True,'state':'preview'}), \
             patch.object(inference.recording,'current_dir',return_value=Path(tmp)):
            with self.assertRaisesRegex(ValueError,'旧执行程序'):
                inference.action('execute',{'id':'old','prompt':'task','prepared':True,'timing_mode':'timed'}, {})
            self.assertFalse((Path(tmp)/'command.json').exists())

    def test_existing_50hz_worker_cannot_receive_high_rate_without_gripper_adapter(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(inference,'session',return_value={'id':'old','active':True,'state':'preview','supports_timed_actions':True}), \
             patch.object(inference.recording,'current_dir',return_value=Path(tmp)):
            with self.assertRaisesRegex(ValueError,'高频下发适配'):
                inference.action('execute',{'id':'old','prompt':'task','prepared':True,'timing_mode':'timed','hz':100}, {})
            self.assertFalse((Path(tmp)/'command.json').exists())


if __name__=='__main__':
    unittest.main()
