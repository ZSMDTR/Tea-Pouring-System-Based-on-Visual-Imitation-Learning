"""One CPU batch including resizing/tokenization; no model/optimizer allocation."""
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
mode=sys.argv[1];folder=ROOT/'.runtime/dual_camera_qa'/mode
request=json.loads((folder/'request.json').read_text())
os.environ['HF_LEROBOT_HOME']=str(Path(request['config']['dataset_root']).parent)
sys.path.insert(0,str(ROOT))
if mode=='full':sys.path.insert(0,str(ROOT/'full_training'))
path=ROOT/('full_training/entry.py' if mode=='full' else 'training_runner.py')
spec=importlib.util.spec_from_file_location('qa_entry',path)
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
from multi_view_config import with_dual_cameras
from openpi.training import data_loader
import numpy as np
config=with_dual_cameras(entry.make_config(folder))
observation,actions=next(iter(data_loader.create_data_loader(config,num_batches=1,shuffle=False)))
assert observation.images['base_0_rgb'].shape==(1,224,224,3)
assert observation.images['left_wrist_0_rgb'].shape==(1,224,224,3)
assert bool(np.asarray(observation.image_masks['base_0_rgb'])[0])
assert bool(np.asarray(observation.image_masks['left_wrist_0_rgb'])[0])
assert not bool(np.asarray(observation.image_masks['right_wrist_0_rgb'])[0])
assert actions.shape==(1,50,32) and np.isfinite(np.asarray(actions)).all()
print(json.dumps({'mode':mode,'base_shape':list(observation.images['base_0_rgb'].shape),
 'wrist_shape':list(observation.images['left_wrist_0_rgb'].shape),'actions':list(actions.shape),'device':'CPU','passed':True}))
