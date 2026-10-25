"""Simulated devices only. Run with LeRobot Python; never creates a real robot."""
from pathlib import Path
import sys,tempfile,unittest,json
from unittest.mock import Mock,patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import inference,inference_worker as w

class InferenceTests(unittest.TestCase):
 def setUp(self):
  self.task={'task_id':'a','model_id':'m','port':1,'prompt':'custom language command','relative_limit':5,'seconds':.08,'hz':50,'chunk':2}
  self.robot=Mock();self.robot.get_observation.return_value={name+'.pos':0 for name in w.JOINTS}
  self.camera=Mock();self.camera.async_read.return_value=np.zeros((480,640,3),np.uint8)
  self.policy=Mock();self.policy.infer.return_value={'actions':np.tile(np.arange(7,dtype=np.float32),(50,1))}
  self.events=[]
 def execute(self,reason=lambda:None):
  with patch.object(w,'cleanup',return_value=[]) as clean:
   result=w.execute(self.task,{},self.camera,reason,lambda *a,**k:self.events.append((a,k)),lambda _:None,lambda *_:self.robot,lambda _:self.policy)
   return result,clean.call_args
 def test_timeout_is_not_success_and_prompt_passes_through(self):
  result,clean=self.execute();self.assertEqual(result['outcome'],'timeout');self.assertTrue(clean.args[2])
  self.assertEqual(self.policy.infer.call_args.args[0]['prompt'],self.task['prompt'])
  self.assertEqual(self.robot.send_action.call_args_list[0].args[0],{name+'.pos':float(i) for i,name in enumerate(w.JOINTS)})
 def test_cancel_before_connect_never_connects(self):
  result,_=self.execute(lambda:'disconnected');self.robot.connect.assert_not_called();self.robot.send_action.assert_not_called();self.assertEqual(result['outcome'],'error')
 def test_fault_never_forces_return(self):
  self.policy.infer.side_effect=TimeoutError('test unavailable')
  result,clean=self.execute();self.assertFalse(clean.args[2]);self.robot.send_action.assert_not_called();self.assertEqual(result['outcome'],'error')
 def test_bad_actions_not_sent(self):
  self.policy.infer.return_value={'actions':np.full((50,7),np.nan)}
  result,clean=self.execute();self.robot.send_action.assert_not_called();self.assertEqual(result['outcome'],'error');self.assertFalse(clean.args[2])
 def test_stop_after_infer_prevents_stale_action(self):
  stop=[]
  def infer(*_):stop.append(True);return {'actions':np.zeros((50,7))}
  self.policy.infer.side_effect=infer
  result,clean=self.execute(lambda:'stopped' if stop else None)
  self.robot.send_action.assert_not_called();self.assertEqual(result['outcome'],'stopped');self.assertTrue(clean.args[2])
 def test_each_new_task_fetches_a_new_chunk(self):
  self.execute();count=self.policy.infer.call_count
  self.task['prompt']='next task';self.execute()
  self.assertGreater(self.policy.infer.call_count,count);self.assertEqual(self.policy.infer.call_args.args[0]['prompt'],'next task')
 def test_cleanup_failure_cannot_be_ok(self):
  def reason():return 'success' if self.robot.send_action.called else None
  with patch.object(w,'cleanup',return_value=['disable failed']):
   result=w.execute(self.task,{},self.camera,reason,lambda *a,**k:None,lambda _:None,lambda *_:self.robot,lambda _:self.policy)
  self.assertEqual(result['outcome'],'error')
 def test_preview_does_not_construct_robot(self):
  import signal
  handlers=[signal.getsignal(signal.SIGTERM),signal.getsignal(signal.SIGINT)]
  try:
   with tempfile.TemporaryDirectory() as tmp:
    folder=Path(tmp);(folder/'heartbeat').touch()
    def read(**_):(folder/'stop').touch();return np.zeros((480,640,3),np.uint8)
    self.camera.async_read.side_effect=read
    with patch.object(w,'build_robot') as build,patch.object(w,'execute') as execute,patch.object(w,'PreviewPublisher') as publisher:
     publisher.return_value.metrics.return_value={}
     w.run(folder,{'id':'x','profile':{}},self.camera);build.assert_not_called();execute.assert_not_called()
    self.assertEqual(json.loads((folder/'status.json').read_text())['state'],'completed')
  finally:signal.signal(signal.SIGTERM,handlers[0]);signal.signal(signal.SIGINT,handlers[1])
 def test_driver_protective_disable_cannot_be_ok(self):
  self.robot._emergency_disable_requested=True
  result,_=self.execute();self.assertEqual(result['outcome'],'error')
 def test_observation_shape_and_finite_guard(self):
  raw={name+'.pos':0 for name in w.JOINTS};raw['gripper.pos']=float('nan')
  with self.assertRaises(ValueError):w.policy_observation(raw,np.zeros((480,640,3)),'task')
  raw['gripper.pos']=0
  with self.assertRaises(ValueError):w.policy_observation(raw,np.zeros((240,320,3)),'task')
 def test_continuous_replans_with_fresh_images_past_duration_then_stops(self):
  self.task['continuous']=True
  clock=[0.0]
  def read(**_):return np.full((480,640,3),self.robot.send_action.call_count,np.uint8)
  self.camera.async_read.side_effect=read
  def infer(*_):
   clock[0]+=.1  # Each inference alone exceeds the old duration.
   return {'actions':np.zeros((50,7))}
  self.policy.infer.side_effect=infer
  with patch.object(w.time,'monotonic',side_effect=lambda:clock[0]),patch.object(w.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
   result,clean=self.execute(lambda:'stopped' if self.robot.send_action.call_count>=6 else None)
  self.assertEqual(result['outcome'],'stopped');self.assertEqual(result['steps'],6)
  self.assertGreater(result['elapsed'],self.task['seconds']);self.assertTrue(clean.args[2])
  self.assertEqual(self.policy.infer.call_count,3);self.policy.close.assert_called_once()
  self.assertEqual([int(c.args[0]['wrist_rgb'][0,0,0]) for c in self.policy.infer.call_args_list],[0,2,4])
  self.assertTrue(self.events[0][1]['continuous'])
 def test_continuous_disconnect_still_disables_without_return(self):
  self.task['continuous']=True
  result,clean=self.execute(lambda:'disconnected' if self.robot.send_action.called else None)
  self.assertEqual(result['outcome'],'error');self.assertFalse(clean.args[2])
 def test_continuous_fault_still_disables_without_return(self):
  self.task['continuous']=True;self.policy.infer.side_effect=TimeoutError('test unavailable')
  result,clean=self.execute();self.assertEqual(result['outcome'],'error');self.assertFalse(clean.args[2])
 def test_mode_validation_and_backwards_compatibility(self):
  d={'prompt':'test','prepared':True}
  self.assertFalse(inference.validate_task(d)['continuous'])
  self.assertTrue(inference.validate_task({**d,'continuous':True})['continuous'])
  for invalid in ['true',1,None]:
   with self.assertRaises(ValueError):inference.validate_task({**d,'continuous':invalid})
 def test_separate_limits_and_legacy_fallback(self):
  from lerobot.robots.utils import ensure_safe_goal_position
  task={**self.task,'relative_limit':5,'gripper_relative_limit':150}
  caps=w.relative_limits(task)
  self.assertEqual(caps['gripper'],150.0)
  self.assertTrue(all(caps[name]==5 for name in w.JOINTS if name!='gripper'))
  goals={name:(200,0) for name in w.JOINTS}
  limited=ensure_safe_goal_position(goals,caps)
  self.assertEqual(limited['gripper'],150);self.assertEqual(limited['elbow_flex'],5)
  self.assertEqual(w.relative_limits(self.task)['gripper'],self.task['relative_limit'])
  self.task=task
  with patch.object(w,'cleanup',return_value=[]):
   factory=Mock(return_value=self.robot)
   w.execute(task,{},self.camera,lambda:None,lambda *a,**k:None,lambda _:None,factory,lambda _:self.policy)
   self.assertEqual(factory.call_args.args[1],caps)
 def test_gripper_limit_validation(self):
  d={'prompt':'test','prepared':True,'relative_limit':25}
  self.assertEqual(inference.validate_task(d)['gripper_relative_limit'],25)
  self.assertEqual(inference.validate_task({**d,'gripper_relative_limit':150})['gripper_relative_limit'],150)
  for invalid in [0,151,float('nan'),float('inf'),True,'50']:
   with self.assertRaises(ValueError):inference.validate_task({**d,'gripper_relative_limit':invalid})
 def test_rtc_execution_uses_only_control_thread_for_motors(self):
  import threading
  self.task.update(continuous=True,rtc={'enabled':True,'s_min':2,'delay_steps':1})
  self.policy.infer.return_value['rtc']={'guided':True}
  threads=[];self.robot.send_action.side_effect=lambda a:threads.append(threading.current_thread().name)
  result,clean=self.execute(lambda:'stopped' if len(threads)>=10 else None)
  self.assertEqual(result['outcome'],'stopped');self.assertTrue(clean.args[2])
  self.assertGreaterEqual(result['rtc_replans'],1);self.assertEqual(set(threads),{threading.current_thread().name})
  self.policy.close.assert_called_once()
 def test_rtc_stop_after_initial_infer_sends_nothing(self):
  self.task['rtc']={'enabled':True}
  stop=[]
  def infer(*_):stop.append(True);return {'actions':np.zeros((50,7)),'rtc':{'guided':True}}
  self.policy.infer.side_effect=infer
  result,clean=self.execute(lambda:'stopped' if stop else None)
  self.robot.send_action.assert_not_called();self.assertEqual(result['outcome'],'stopped')
 def test_rtc_error_never_reports_success(self):
  self.task['rtc']={'enabled':True};self.policy.infer.side_effect=RuntimeError('RTC failed')
  result,clean=self.execute();self.assertEqual(result['outcome'],'error');self.assertFalse(clean.args[2]);self.robot.send_action.assert_not_called()
 def test_invalid_request(self):
  for d in ({},{'prompt':'x','prepared':True,'seconds':float('nan')},{'prompt':'x','prepared':True,'chunk':1.1},{'prompt':'x','prepared':True,'relative_limit':46}):
   with self.assertRaises(ValueError):inference.validate_task(d)

if __name__=='__main__':unittest.main()
