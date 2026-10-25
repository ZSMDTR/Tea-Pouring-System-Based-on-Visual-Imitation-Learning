"""OpenPI environment reader smoke test; no policy weights or GPU execution."""
import json
from pathlib import Path
import sys
import numpy as np
from lerobot.common.datasets.lerobot_dataset import LeRobotDataset
from openpi.policies.b601_policy import B601Inputs
import camera_contract
root=Path(sys.argv[1])
inputs=camera_contract.dataset(root)
adapter=B601Inputs()
if inputs['mode']==camera_contract.DUAL:
    from multi_view_policy import DualCameraInputs
    adapter=DualCameraInputs()
ds=LeRobotDataset('local/workbench_export_validation',root=root,delta_timestamps={'action':[i/30 for i in range(50)]})
eps=[json.loads(l) for l in (root/'meta/episodes.jsonl').read_text().splitlines() if l.strip()]
offset=0;count=0
for ep in eps:
    for index in sorted({offset,offset+ep['length']-1}):
        frame=ds[index]
        observation={'state':np.asarray(frame['observation.state']),'wrist_rgb':np.asarray(frame['observation.images.wrist_rgb']),'actions':np.asarray(frame['action']),'prompt':frame['task']}
        if inputs['mode']==camera_contract.DUAL:observation['head_rgb']=np.asarray(frame['observation.images.head_rgb'])
        result=adapter(observation)
        assert bool(result['image_mask']['base_0_rgb'])==(inputs['mode']==camera_contract.DUAL)
        assert result['actions'].shape==(50,7)
        assert result['state'].shape==(7,)
        assert frame['task'] in ep['tasks']
        count+=1
    offset+=ep['length']
assert len(ds)==offset
print(json.dumps({'frames':len(ds),'episodes':len(eps),'boundary_samples':count,'openpi_reader':'passed'}))
