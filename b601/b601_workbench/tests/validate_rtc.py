"""RTC scheduling tests: no motors, cameras, model weights or network."""
import sys,unittest
from pathlib import Path
from concurrent.futures import Future
from unittest.mock import Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rtc.config import validate
from rtc.runtime import RTCQueue

class ManualExecutor:
 def __init__(self):self.jobs=[]
 def submit(self,fn,*args):
  f=Future();self.jobs.append((f,fn,args));return f
 def finish(self):
  f,fn,args=self.jobs[-1]
  try:f.set_result(fn(*args))
  except BaseException as e:f.set_exception(e)
 def shutdown(self,**kwargs):pass

class RTCTests(unittest.TestCase):
 def setUp(self):
  self.p=Mock();self.calls=[]
  def infer(obs,reason):
   self.calls.append(obs)
   return {'actions':np.tile((np.arange(50)+100*(len(self.calls)-1))[:,None],(1,7)), 'rtc':{'guided':True}}
  self.p.infer.side_effect=infer;self.e=ManualExecutor()
  self.q=RTCQueue(self.p,{'enabled':True,'s_min':5,'delay_steps':2},self.e)
  self.obs={'state':np.zeros(7),'wrist_rgb':np.zeros((2,2,3),np.uint8),'prompt':'one'}
 def tearDown(self):self.q.close()
 def step(self):return self.q.next_action(self.obs,lambda:None)[0]
 def test_skip_consumed_prefix_and_fresh_observation(self):
  self.assertEqual([self.step() for _ in range(5)],list(range(5)))
  self.obs['state'][0]=5
  self.assertEqual(self.step(),5);self.assertEqual(self.step(),6)
  self.obs['state'][0]=100
  self.e.finish()
  self.assertEqual(self.step(),102)
  self.assertEqual(self.calls[1]['state'][0],5)
  np.testing.assert_array_equal(self.calls[1]['_rtc']['prefix'][:,0],np.arange(5,50))
  self.assertEqual(self.q.last_delay,2)
 def test_no_background_access_to_hardware_and_no_repeated_requests(self):
  for _ in range(10):self.step()
  self.assertEqual(len(self.e.jobs),1)
  self.assertEqual(len(self.calls),1)
 def test_queue_exhaustion_stops_without_replay(self):
  for _ in range(50):self.step()
  with self.assertRaisesRegex(RuntimeError,'耗尽'):self.step()
 def test_late_result_rejected(self):
  for _ in range(50):self.step()
  self.e.finish()
  with self.assertRaisesRegex(RuntimeError,'覆盖范围'):self.step()
 def test_inference_error_propagates(self):
  for _ in range(6):self.step()
  self.p.infer.side_effect=RuntimeError('model failed');self.e.finish()
  with self.assertRaisesRegex(RuntimeError,'model failed'):self.step()
 def test_reject_fake_rtc_and_nan(self):
  self.p.infer.side_effect=None;self.p.infer.return_value={'actions':np.zeros((50,7))}
  with self.assertRaises(ValueError):self.step()
 def test_close_cancels_pending_and_disallows_next_action(self):
  for _ in range(6):self.step()
  self.q.close();self.assertTrue(self.e.jobs[0][0].cancelled())
  with self.assertRaises(RuntimeError):self.step()
 def test_new_queue_has_no_old_prompt_or_actions(self):
  self.step();self.q.close()
  self.q=RTCQueue(self.p,{'enabled':True},self.e);self.obs['prompt']='two';self.step()
  self.assertEqual(len(self.calls[-1]['_rtc']['prefix']),0);self.assertEqual(self.calls[-1]['prompt'],'two')
 def test_config_paper_defaults_and_bounds(self):
  cfg=validate({'enabled':True});self.assertEqual(cfg['beta'],5);self.assertEqual(cfg['denoise_steps'],5)
  for d in [{'enabled':'true'},{'beta':float('nan')},{'s_min':0},{'denoise_steps':2.5},{'enabled':True,'s_min':3,'delay_steps':5}]:
   with self.assertRaises(ValueError):validate(d)

if __name__=='__main__':unittest.main()
