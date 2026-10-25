"""Run REAL installed safe_zero/send_action with simulated motors; no CAN access."""
import math,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from inference_worker import JOINTS
from lerobot_robot_seeed_b601 import SeeedB601RSFollower,SeeedB601RSFollowerConfig
from b601_driver_adapter import WorkbenchB601RSFollower
from record_worker import cleanup

class Motor:
 def __init__(self,name,angle,events):
  self.name=name;self.events=events;self.state=SimpleNamespace(pos=math.radians(angle),vel=0,torq=0,t_mos=25)
 def get_state(self):return self.state
 def request_feedback(self):pass
 def send_mit(self,pos,*args):
  self.events.append(('send',self.name,pos))
  if self.name!='gripper':self.state.pos=pos
 def disable(self):self.events.append(('disable',self.name))
 def close(self):self.events.append(('close',self.name))

class RecordingRobot(WorkbenchB601RSFollower):
 def send_action(self,action):
  result=super().send_action(action);self.goals.append(result);return result

class ReturnTests(unittest.TestCase):
 def make(self,cls=RecordingRobot,gripper=30):
  r=object.__new__(cls)
  r.config=SeeedB601RSFollowerConfig(id='simulated',port='NO_CAN',cameras={},gravity_compensation=False,max_relative_target={n:150. if n=='gripper' else 30. for n in JOINTS})
  r.events=[];r.goals=[];r.bus=Mock();r.cameras={};r.motor_names=list(JOINTS)
  starts=[2,3,4,-2,-1,2,gripper]
  r.motors={n:Motor(n,v,r.events) for n,v in zip(JOINTS,starts)}
  r._in_safe_zero=False;r._emergency_disable_requested=False;r._skip_safe_zero_on_disconnect=True
  self.addCleanup(lambda:setattr(r,'bus',None))
  return r
 def test_reproduce_unadapted_dictionary_failure(self):
  r=self.make(SeeedB601RSFollower)
  with patch('time.sleep'),self.assertRaisesRegex(ValueError,'keys must match'):r.safe_zero(exit_on_complete=False)
  self.assertFalse(r._in_safe_zero)
 def test_all_arm_joints_return_before_any_disable(self):
  r=self.make();caps=r.config.max_relative_target
  with patch('time.sleep'):errors=cleanup(r,None,True)
  self.assertEqual(errors,[])
  for name in JOINTS[:-1]:self.assertAlmostEqual(r.motors[name].state.pos,0)
  self.assertTrue(all('gripper.pos' not in g for g in r.goals))
  send=[i for i,e in enumerate(r.events) if e[0]=='send'];disable=[i for i,e in enumerate(r.events) if e[0]=='disable']
  self.assertEqual(len(disable),7);self.assertLess(max(send),min(disable));self.assertIs(r.config.max_relative_target,caps)
 def test_wide_gripper_stage_adds_seventh_target(self):
  r=self.make(gripper=185);caps=r.config.max_relative_target
  with patch('time.sleep'):self.assertEqual(cleanup(r,None,True),[])
  self.assertNotIn('gripper.pos',r.goals[0]);self.assertAlmostEqual(r.goals[-1]['gripper.pos'],170)
  self.assertIs(r.config.max_relative_target,caps)
 def test_cap_restored_on_motor_error(self):
  r=self.make();caps=r.config.max_relative_target
  r.motors['shoulder_pan'].send_mit=Mock(side_effect=RuntimeError('simulated CAN failure'))
  with patch('time.sleep'):errors=cleanup(r,None,True)
  self.assertTrue(any('simulated CAN failure' in e for e in errors));self.assertIs(r.config.max_relative_target,caps)
  self.assertEqual(sum(e[0]=='disable' for e in r.events),7)
 def test_distinct_caps_and_implicit_wrist_yaw_preserved(self):
  r=self.make();r._in_safe_zero=True
  result=r.send_action({'shoulder_pan.pos':100.,'gripper.pos':45.})
  self.assertAlmostEqual(result['shoulder_pan.pos'],32.)
  self.assertAlmostEqual(result['gripper.pos'],180.)
  self.assertIn('wrist_yaw.pos',result)
 def test_scalar_cap_still_supported(self):
  r=self.make();r.config.max_relative_target=5.;r._in_safe_zero=True
  self.assertAlmostEqual(r.send_action({'shoulder_pan.pos':100.})['shoulder_pan.pos'],7.)
 def test_high_rate_gripper_adapter_still_returns_then_disables(self):
  r=self.make(gripper=185);r.configure_command_timing(200);caps=r.config.max_relative_target
  with patch('time.sleep'):errors=cleanup(r,None,True)
  self.assertEqual(errors,[])
  for name in JOINTS[:-1]:self.assertAlmostEqual(r.motors[name].state.pos,0)
  self.assertAlmostEqual(r.goals[-1]['gripper.pos'],170)
  self.assertIs(r.config.max_relative_target,caps)
  sends=[i for i,e in enumerate(r.events) if e[0]=='send'];disables=[i for i,e in enumerate(r.events) if e[0]=='disable']
  self.assertEqual(len(disables),7);self.assertLess(max(sends),min(disables))

if __name__=='__main__':unittest.main()
