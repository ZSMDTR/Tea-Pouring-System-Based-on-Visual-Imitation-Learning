"""Historical dataset + running local model only. No robot or camera imports."""
import json,sys,time
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import inference
from inference_worker import PolicyConnection
from rtc.config import DEFAULTS
from rtc.runtime import RTCQueue
from lerobot.datasets.lerobot_dataset import LeRobotDataset
repo='local/b601_rs_red_screwdriver_orange_container_v2crop_20260901'
data=LeRobotDataset(repo,root=Path(json.loads((inference.ROOT/'config.json').read_text())['dataset_root']).parent/repo)
def obs(i):
 f=data[i]
 return {'state':f['observation.state'].numpy(),'wrist_rgb':(f['observation.images.wrist_rgb'].numpy().transpose(1,2,0)*255).round().clip(0,255).astype(np.uint8),'prompt':inference.PROMPT}
frames=[obs(i) for i in [0,15,30,45]]
s=inference.model_status();assert s['active'] and s['state']=='ready' and s.get('rtc_ready'),s
conn=PolicyConnection({'port':s['port'],'model_id':s['id']});measurements=[]
try:
 t=time.monotonic();baseline=conn.infer(frames[0],lambda:None)
 measurements.append({'mode':'baseline_n10','ms':round((time.monotonic()-t)*1000,2)})
 assert np.asarray(baseline['actions']).shape==(50,7)
 for n in [5,10]:
  old=baseline['actions']
  for i in range(3):
   req={**frames[i+1],'_rtc':{'config':{**DEFAULTS,'enabled':True,'denoise_steps':n},'prefix':np.asarray(old)[15:],'delay':5}}
   t=time.monotonic();reply=conn.infer(req,lambda:None);ms=(time.monotonic()-t)*1000
   a=np.asarray(reply['actions']);assert a.shape==(50,7) and np.isfinite(a).all();assert reply['rtc']['guided']
   measurements.append({'mode':'rtc_n'+str(n),'ms':round(ms,2),'overlap':reply['rtc']['overlap'],'finite':True})
   old=a
 q=RTCQueue(conn,{**DEFAULTS,'enabled':True})
 times=[]
 try:
  for i in range(100):
   tick=time.monotonic();action=q.next_action(frames[min(i//25,3)],lambda:None)
   assert action.shape==(7,) and np.isfinite(action).all()
   times.append(time.monotonic());time.sleep(max(0,1/30-(time.monotonic()-tick)))
  assert q.replans>=3,q.metrics()
  metrics=q.metrics();metrics['offline_consumer_hz']=round(99/(times[-1]-times[0]),2)
 finally:q.close()
 # Baseline remains callable after RTC in same model process.
 plain=conn.infer(frames[0],lambda:None);assert 'rtc' not in plain and np.isfinite(plain['actions']).all()
 result={'model':s['model'],'robot_connected':False,'historical_dataset':repo,'measurements':measurements,'queue':metrics,'baseline_after_rtc':True}
 inference.ROOT/'.runtime/rtc_offline_result.json'.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:conn.close()
