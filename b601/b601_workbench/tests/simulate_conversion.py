"""Temporary two-episode conversion; uses storage libraries, no hardware."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import numpy as np
import pyarrow.parquet as pq
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lerobot.datasets.lerobot_dataset import LeRobotDataset
import experiment_data

with tempfile.TemporaryDirectory(prefix='wb-format-test-') as tmp:
    src=Path(tmp)/'raw';dst=Path(tmp)/'local/converted';dst.parent.mkdir()
    features={k:{'dtype':'float32','shape':(7,),'names':experiment_data.JOINTS} for k in ('action','observation.state')}
    features['observation.images.wrist_rgb']={'dtype':'video','shape':(480,640,3),'names':['height','width','channels']}
    ds=LeRobotDataset.create('local/synthetic',fps=30,root=src,robot_type='seeed_b601_rs_follower',features=features,use_videos=True,vcodec='h264')
    for ep in range(2):
        for i in range(4):
            ds.add_frame({'action':np.arange(7,dtype=np.float32)+ep,'observation.state':-np.arange(7,dtype=np.float32)-i,'observation.images.wrist_rgb':np.full((480,640,3),ep*80+i,np.uint8),'task':f'synthetic task {ep}'})
        ds.save_episode()
    ds.finalize();ds.stop_image_writer()
    subprocess.run([sys.executable,str(ROOT.parent/'openpi/scripts/b601/convert_dataset.py'),'--src',str(src),'--dst',str(dst),'--video-mode','reencode-h264','--video-key','observation.images.wrist_rgb'],check=True)
    assert experiment_data.inspect(dst)['compatible']
    def rows(root):
        result=[]
        for file in sorted((root/'data').rglob('*.parquet')):result.extend(pq.read_table(file).to_pylist())
        return result
    before,after=rows(src),rows(dst)
    assert len(before)==len(after)==8
    for a,b in zip(before,after):
        for key in ('action','observation.state','episode_index','task_index'):assert a[key]==b[key],key
    episodes=[json.loads(line) for line in (dst/'meta/episodes.jsonl').read_text().splitlines()]
    assert [e['length'] for e in episodes]==[4,4]
    job=Path(tmp)/'job';job.mkdir()
    config=json.loads((ROOT/'config.json').read_text())
    request={'dataset':'converted','run_name':'synthetic_norm','output':str(Path(tmp)/'checkpoints/pi05_b601/synthetic_norm'),'steps':2,'batch':1,'config':{'dataset_root':str(dst.parent),'base_model':config['base_model']}}
    (job/'request.json').write_text(json.dumps(request))
    env=os.environ.copy();env.update(JAX_PLATFORMS='cpu',CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1')
    subprocess.run([config['openpi_python'],str(ROOT/'training_runner.py'),'norm',str(job)],env=env,check=True)
    assert (job/'assets/pi05_b601/local/converted/norm_stats.json').is_file()
    print('PASS: OpenPI reads converted temporary data and computes normalization without GPU training.')
    print('PASS: two episode boundaries, tasks, states and actions preserved; source retained.')
