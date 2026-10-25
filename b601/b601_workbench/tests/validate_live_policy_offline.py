"""Actual model + historical observation, no camera or motor connections."""
import os
os.environ['HF_HUB_OFFLINE']='1'
import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import inference
from inference_worker import PolicyConnection,action_chunk
from lerobot.datasets.lerobot_dataset import LeRobotDataset
repo='local/b601_rs_red_screwdriver_orange_container_v2crop_20260901'
data=LeRobotDataset(repo,root=Path(json.loads((inference.ROOT/'config.json').read_text())['dataset_root']).parent/repo)
frame=data[0];image=(frame['observation.images.wrist_rgb'].numpy().transpose(1,2,0)*255).round().clip(0,255).astype(np.uint8)
obs={'state':frame['observation.state'].numpy(),'wrist_rgb':image,'prompt':inference.PROMPT}
s=inference.model_status();assert s['state']=='ready' and s['active']
conn=PolicyConnection({'port':s['port'],'model_id':s['id']})
try:
 start=time.monotonic();reply=conn.infer(obs,lambda:None);a=action_chunk(reply,10)
 result={'model':s['model'],'historical_observation':repo,'shape':list(np.asarray(reply['actions']).shape),'finite':bool(np.isfinite(a).all()),'request_ms':round((time.monotonic()-start)*1000,1),'robot_connected':False}
 print(json.dumps(result));inference.ROOT/'.runtime/inference_offline_result.json'.write_text(json.dumps(result,indent=2))
finally:conn.close()
