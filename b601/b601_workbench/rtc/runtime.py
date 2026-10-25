"""Paper Algorithm 1 scheduling. Only the control thread touches robot/camera.

Inference runs in one background thread. Queue ownership remains on control
thread, using consumed step counts to discard the expired prefix on swap.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import numpy as np
from rtc.config import validate,HORIZON

class RTCQueue:
    def __init__(self,policy,config,executor=None):
        self.policy=policy;self.config=validate(config)
        self.executor=executor or ThreadPoolExecutor(max_workers=1,thread_name_prefix='rtc-inference')
        self.cancel=threading.Event();self.future=None;self.chunk=None;self.index=0
        self.delays=deque([self.config['delay_steps']],maxlen=self.config['delay_buffer'])
        self.infer_ms=None;self.replans=0;self.last_delay=0;self.closed=False
    def _infer(self,obs,prefix,delay):
        start=time.monotonic()
        request={**obs,'_rtc':{'config':self.config,'prefix':prefix,'delay':int(delay)}}
        reply=self.policy.infer(request,lambda:'stopped' if self.cancel.is_set() else None)
        actions=np.asarray(reply['actions'],dtype=np.float32)
        if not reply.get('rtc',{}).get('guided'):raise ValueError('模型服务未执行 RTC；已拒绝静默回退。')
        if actions.shape!=(HORIZON,7) or not np.isfinite(actions).all():raise ValueError('RTC 返回无效动作段。')
        return actions,(time.monotonic()-start)*1000
    def next_action(self,obs,reason):
        if self.closed:raise RuntimeError('RTC 队列已关闭。')
        if self.chunk is None:
            # First call has no prior plan; caller checks stop again before sending any action.
            # Unlike background calls, initial inference also observes user/heartbeat cancellation.
            start=time.monotonic()
            reply=self.policy.infer({**obs,'_rtc':{'config':self.config,'prefix':np.empty((0,7),np.float32),'delay':0}},reason)
            self.chunk=np.asarray(reply['actions'],dtype=np.float32)
            if not reply.get('rtc',{}).get('guided') or self.chunk.shape!=(HORIZON,7) or not np.isfinite(self.chunk).all():raise ValueError('RTC 初始动作无效或模型未启用 RTC。')
            self.infer_ms=(time.monotonic()-start)*1000
        if self.future is not None and self.future.done():
            new,ms=self.future.result()  # Exceptions propagate; never continue an old plan silently.
            consumed=self.index-self.query_index
            if self.index>=HORIZON or consumed>=HORIZON-self.query_index:raise RuntimeError('RTC 推理超过旧动作段覆盖范围，停止执行。')
            self.chunk=new;self.index=consumed;self.delays.append(consumed)
            self.last_delay=consumed;self.infer_ms=ms;self.replans+=1;self.future=None
        if self.index>=HORIZON:raise RuntimeError('RTC 动作队列耗尽，停止执行。')
        if self.future is None and self.index>=self.config['s_min']:
            delay=max(self.delays)
            if delay>=HORIZON-self.index:raise RuntimeError('RTC 预计延迟超过剩余动作范围，请减小 s_min 或降低控制频率。')
            self.query_index=self.index
            # Observations and prefix are snapshots; no hardware calls in background thread.
            snapshot={k:(v.copy() if isinstance(v,np.ndarray) else v) for k,v in obs.items()}
            self.future=self.executor.submit(self._infer,snapshot,self.chunk[self.index:].copy(),delay)
        action=self.chunk[self.index].copy();self.index+=1
        return action
    def metrics(self):
        return {'infer_ms':None if self.infer_ms is None else round(self.infer_ms,1),
                'rtc_delay_steps':self.last_delay,'rtc_delay_estimate':max(self.delays),
                'rtc_remaining':0 if self.chunk is None else HORIZON-self.index,'rtc_replans':self.replans}
    def close(self):
        self.closed=True;self.cancel.set()
        if self.future is not None:self.future.cancel()
        # PolicyConnection polls cancellation every 100 ms. No device use occurs here.
        self.executor.shutdown(wait=True,cancel_futures=True)
