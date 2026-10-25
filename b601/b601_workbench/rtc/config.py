"""Paper notation and request validation; no ML/hardware imports."""
import math

HORIZON=50
DEFAULTS={'enabled':False,'s_min':15,'delay_steps':5,'delay_buffer':10,'denoise_steps':5,'beta':5.0}

def validate(data):
    if data is None:data={}
    if not isinstance(data,dict):raise ValueError('RTC 参数必须为对象。')
    if set(data)-set(DEFAULTS):raise ValueError('未知 RTC 参数。')
    result={**DEFAULTS,**data}
    if type(result['enabled']) is not bool:raise ValueError('RTC 开关必须为布尔值。')
    for name,low,high in [('s_min',1,40),('delay_steps',1,20),('delay_buffer',1,100),('denoise_steps',1,20)]:
        value=result[name]
        if type(value) is not int or not low<=value<=high:raise ValueError(f'RTC {name} 必须为 {low}–{high} 的整数。')
    beta=result['beta']
    if type(beta) not in (int,float) or not math.isfinite(beta) or not 0<beta<=10:raise ValueError('RTC β 必须大于 0 且不超过 10。')
    if result['enabled'] and not result['delay_steps']<=result['s_min']<=HORIZON-result['delay_steps']:
        raise ValueError('RTC 需要 d ≤ s_min ≤ 50−d，请调整最小执行步数或初始延迟。')
    result['beta']=float(beta)
    return result
