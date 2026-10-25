"""CPU-only: numerical parity with pinned PI code and reversed-time guidance."""
import os
os.environ['JAX_PLATFORMS']='cpu'
import ast,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import jax,jax.numpy as jnp
import numpy as np
from rtc.guidance import prefix_weights,guided_velocity

class MathTests(unittest.TestCase):
 def test_mask_matches_official_function(self):
  path=Path(__file__).resolve().parents[2]/'rtc_lab/references/pi-official-rtc/src/model.py'
  node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='get_prefix_weights')
  ns={'jax':jax,'jnp':jnp,'PrefixAttentionSchedule':str}
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
  for d,end in [(0,0),(5,35),(2,6),(5,5),(8,4)]:
   np.testing.assert_allclose(prefix_weights(d,end),ns['get_prefix_weights'](d,end,50,'exp'),rtol=1e-6)
 def test_negative_time_matches_paper_positive_time_vjp(self):
  x=jnp.arange(14,dtype=jnp.float32).reshape(2,7)/10;y=jnp.ones_like(x)
  weights=jnp.asarray([[1.],[.3]])
  def vp(value):return jnp.sin(value)*.2+.1
  for tau in [0.,.2,.8]:
   def denoise(value):
    v=vp(value);return value+(1-tau)*v,v
   clean,pullback,v=jax.vjp(denoise,x,has_aux=True)
   r2=(1-tau)**2/(tau**2+(1-tau)**2)
   weight=5 if tau==0 else min(5,(1-tau)/(tau*r2))
   expected=v+weight*pullback((y-clean)*weights)[0]
   actual=-guided_velocity(lambda a:-vp(a),x,1-tau,y,weights,5.)
   np.testing.assert_allclose(actual,expected,rtol=1e-6,atol=1e-6)
 def test_zero_mask_preserves_base_velocity(self):
  x=jnp.ones((1,50,32));v=lambda x:x*.1
  np.testing.assert_allclose(guided_velocity(v,x,1.,x*2,jnp.zeros_like(x),5.),v(x))

class AdapterTests(unittest.TestCase):
 def test_disabled_is_exact_delegation(self):
  from unittest.mock import Mock
  from rtc.policy import RTCPolicy
  wrapper=object.__new__(RTCPolicy);wrapper.base=Mock()
  obs={'state':'test'};noise=np.zeros((50,32))
  result=wrapper.infer(obs,noise=noise)
  self.assertIs(result,wrapper.base.infer.return_value)
  self.assertIs(wrapper.base.infer.call_args.args[0],obs)
  self.assertIs(wrapper.base.infer.call_args.kwargs['noise'],noise)
 def test_prefix_reanchors_relative_joints_but_not_gripper(self):
  from types import SimpleNamespace
  from openpi import transforms as t
  from openpi.policies.b601_policy import B601Inputs,B601Outputs
  from rtc.policy import RTCPolicy
  from openpi.shared.normalize import NormStats
  stats={'state':NormStats(mean=np.zeros(7),std=np.ones(7),q01=-np.ones(7)*200,q99=np.ones(7)*200),
         'actions':NormStats(mean=np.zeros(7),std=np.ones(7),q01=-np.ones(7)*100,q99=np.ones(7)*100)}
  mask=[True]*6+[False]
  captured=[]
  def sample(rng,observation,previous,*a,**kw):captured.append(np.asarray(previous));return previous
  wrapper=object.__new__(RTCPolicy);wrapper.sample=sample
  wrapper.base=SimpleNamespace(_rng=jax.random.key(0),
   _input_transform=t.compose([B601Inputs(),t.DeltaActions(mask),t.Normalize(stats,use_quantiles=True),t.PadStatesAndActions(32)]),
   _output_transform=t.compose([t.Unnormalize(stats,use_quantiles=True),t.AbsoluteActions(mask),B601Outputs()]))
  prefix=np.tile(np.arange(7,dtype=np.float32),(35,1))+10
  obs={'state':np.ones(7,dtype=np.float32)*5,'wrist_rgb':np.zeros((480,640,3),np.uint8),
       '_rtc':{'config':{'enabled':True},'prefix':prefix,'delay':5}}
  out1=wrapper.infer(obs)['actions']
  obs['state']=np.ones(7,dtype=np.float32)*15
  out2=wrapper.infer(obs)['actions']
  np.testing.assert_allclose(out1[:35],prefix,atol=1e-4)
  np.testing.assert_allclose(out2[:35],prefix,atol=1e-4)
  self.assertNotEqual(captured[0][0,0,0],captured[1][0,0,0])
  self.assertNotEqual(captured[0][0,0,2],captured[1][0,0,2])
  np.testing.assert_array_equal(captured[0][:,:,6],captured[1][:,:,6])

if __name__=='__main__':unittest.main()
