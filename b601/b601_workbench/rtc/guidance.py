"""PI RTC Eq. (2)-(5), adapted to OpenPI's reversed flow time (noise t=1).

Guidance and mask adapted from Physical Intelligence's MIT-licensed
real-time-chunking-kinetix/src/model.py; pinned revision in references.json.
Copyright (c) 2025 Physical Intelligence. See LICENSE-PI.
No changes to model parameters or the original sample_actions method.
"""
import jax
import jax.numpy as jnp
import einops
from openpi.models import model as model_lib
from openpi.models.pi0 import make_attn_mask


def prefix_weights(delay,overlap,horizon=50):
    start=jnp.minimum(delay,overlap)
    weights=jnp.clip((start-1-jnp.arange(horizon))/(overlap-start+1)+1,0,1)
    weights=weights*jnp.expm1(weights)/(jnp.e-1)
    return jnp.where(jnp.arange(horizon)>=overlap,0,weights)


def guided_velocity(velocity,x,t,target,weights,beta):
    # Paper tau = 1-t. OpenPI integrates with NEGATIVE dt, so subtract correction.
    def denoiser(value):
        v=velocity(value)
        return value-t*v,v
    clean,pullback,v=jax.vjp(denoiser,x,has_aux=True)
    correction=pullback((target-clean)*weights)[0]
    tau=1-t
    # Equivalent to min(beta, (tau^2+t^2)/(tau*t)); finite at noise endpoint.
    strength=jnp.minimum(beta,(tau*tau+t*t)/jnp.maximum(tau*t,1e-8))
    return v-strength*correction


def sample_actions_rtc(self,rng,observation,previous,delay,overlap,beta,num_steps,noise=None):
    observation=model_lib.preprocess_observation(None,observation,train=False)
    batch=observation.state.shape[0]
    if noise is None:noise=jax.random.normal(rng,(batch,self.action_horizon,self.action_dim))
    prefix,prefix_mask,prefix_ar=self.embed_prefix(observation)
    prefix_attention=make_attn_mask(prefix_mask,prefix_ar)
    positions=jnp.cumsum(prefix_mask,axis=1)-1
    _,cache=self.PaliGemma.llm([prefix,None],mask=prefix_attention,positions=positions)
    weights=prefix_weights(delay,overlap,self.action_horizon)[None,:,None]
    # B601 exposes seven real action dimensions. Padded dimensions have no observed targets.
    weights=weights*(jnp.arange(self.action_dim)<7)[None,None,:]
    dt=-1.0/num_steps
    def step(carry):
        x,t=carry
        def velocity(value):
            suffix,mask,ar,cond=self.embed_suffix(observation,value,jnp.broadcast_to(t,batch))
            suffix_attention=make_attn_mask(mask,ar)
            prefix_attention=einops.repeat(prefix_mask,'b p -> b s p',s=suffix.shape[1])
            full_mask=jnp.concatenate([prefix_attention,suffix_attention],axis=-1)
            positions=jnp.sum(prefix_mask,axis=-1)[:,None]+jnp.cumsum(mask,axis=-1)-1
            (_,output),_=self.PaliGemma.llm([None,suffix],mask=full_mask,positions=positions,kv_cache=cache,adarms_cond=[None,cond])
            return self.action_out_proj(output[:,-self.action_horizon:])
        v=guided_velocity(velocity,x,t,previous,weights,beta)
        return x+dt*v,t+dt
    result,_=jax.lax.while_loop(lambda c:c[1]>=-dt/2,step,(noise,1.0))
    return result
