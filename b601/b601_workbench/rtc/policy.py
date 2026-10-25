"""Opt-in wrapper sharing the loaded OpenPI policy and all its transforms."""
import copy
import time
import types
import jax
import jax.numpy as jnp
import numpy as np
from openpi.models import model as model_lib
from openpi.shared import nnx_utils
from rtc.config import validate,HORIZON
from rtc.guidance import sample_actions_rtc

class RTCPolicy:
    def __init__(self,base):
        if base._is_pytorch_model:raise ValueError('本适配器仅支持当前 JAX π0.5。')
        if base._model.action_horizon!=HORIZON or not base._model.pi05:raise ValueError('RTC 适配要求 π0.5、H=50。')
        self.base=base
        self.sample=nnx_utils.module_jit(types.MethodType(sample_actions_rtc,base._model))
    @property
    def metadata(self):return self.base.metadata
    def infer(self,obs,*,noise=None):
        control=obs.get('_rtc')
        if control is None:return self.base.infer(obs,noise=noise)
        cfg=validate(control.get('config'))
        if not cfg['enabled']:return self.base.infer({k:v for k,v in obs.items() if k!='_rtc'},noise=noise)
        prefix=np.asarray(control.get('prefix',[]),dtype=np.float32)
        if prefix.size==0:prefix=np.empty((0,7),dtype=np.float32)
        if prefix.ndim!=2 or prefix.shape[1]!=7 or len(prefix)>HORIZON or not np.isfinite(prefix).all():raise ValueError('RTC 前缀必须为有限的 N×7 动作。')
        overlap=len(prefix);delay=control.get('delay',0)
        if type(delay) is not int or not 0<=delay<=overlap:raise ValueError('RTC 延迟必须在可用前缀范围内。')
        # Re-run the SAME input transform with absolute policy-space actions and NEW raw state.
        # This handles B601 sign conversion, relative joints, absolute gripper and normalization.
        raw={k:copy.deepcopy(v) for k,v in obs.items() if k!='_rtc'}
        padded=np.zeros((HORIZON,7),np.float32);padded[:overlap]=prefix
        raw['actions']=padded
        inputs=self.base._input_transform(raw)
        previous=jnp.asarray(inputs.pop('actions'))[None,...]
        inputs=jax.tree.map(lambda x:jnp.asarray(x)[None,...],inputs)
        observation=model_lib.Observation.from_dict(inputs)
        self.base._rng,rng=jax.random.split(self.base._rng)
        if noise is not None:
            noise=jnp.asarray(noise)
            if noise.ndim==2:noise=noise[None,...]
        start=time.monotonic()
        a=self.sample(rng,observation,previous,jnp.asarray(delay),jnp.asarray(overlap),jnp.asarray(cfg['beta']),jnp.asarray(cfg['denoise_steps']),noise=noise)
        outputs={'state':np.asarray(inputs['state'][0]),'actions':np.asarray(a[0])}
        outputs=self.base._output_transform(outputs)
        if outputs['actions'].shape!=(HORIZON,7) or not np.isfinite(outputs['actions']).all():raise ValueError('RTC 返回无效动作。')
        outputs['rtc']={'guided':True,'overlap':overlap,'delay':delay,'denoise_steps':cfg['denoise_steps'],'beta':cfg['beta']}
        outputs['policy_timing']={'infer_ms':(time.monotonic()-start)*1000}
        return outputs
