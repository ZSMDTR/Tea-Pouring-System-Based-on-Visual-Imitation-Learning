"""Adapts the existing local pi05_b601 runner without modifying its repository."""
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import sys

OPENPI=Path(__file__).resolve().parent.parent/'openpi'


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def make_config(folder):
    request=json.loads((folder/'request.json').read_text())
    dataset_root=Path(request['config']['dataset_root'])
    if dataset_root.name!='local':raise ValueError('当前训练入口要求数据根目录以 local 结尾。')
    os.environ['HF_LEROBOT_HOME']=str(dataset_root.parent)
    from openpi.training import config as configs
    from openpi.training import weight_loaders
    request=json.loads((folder/'request.json').read_text())
    base=configs.get_config('pi05_b601')
    repo='local/'+request['dataset']
    return dataclasses.replace(base,exp_name=request['run_name'],data=dataclasses.replace(base.data,repo_id=repo,assets=configs.AssetsConfig()),
        assets_base_dir=str(folder/'assets'),checkpoint_base_dir=str(Path(request['output']).parent.parent),
        weight_loader=weight_loaders.CheckpointWeightLoader(str(Path(request['config']['base_model'])/'params')),
        batch_size=request['batch'],num_train_steps=request['steps'],num_workers=0,overwrite=False,resume=False,wandb_enabled=False)


def main(mode,folder):
    config=make_config(folder)
    if mode=='check':
        print(json.dumps({'dataset':config.data.repo_id,'checkpoint':str(config.checkpoint_dir),'steps':config.num_train_steps,'batch':config.batch_size,'resume':config.resume,'overwrite':config.overwrite,'wandb_enabled':config.wandb_enabled}));return
    if mode=='norm':
        import numpy as np
        from openpi.shared import normalize
        helper=module('wb_norm',OPENPI/'scripts/compute_norm_stats.py')
        data=config.data.create(config.assets_dirs,config.model)
        # A batch of one includes every frame, including the last partial batch.
        loader,_=helper.create_torch_dataloader(data,config.model.action_horizon,1,config.model,0)
        stats={key:normalize.RunningStats() for key in ('state','actions')}
        for batch in loader:
            for key in stats:stats[key].update(np.asarray(batch[key]))
        normalize.save(config.assets_dirs/data.repo_id,{key:value.get_statistics() for key,value in stats.items()})
    elif mode=='train':
        runner=module('wb_training',OPENPI/'scripts/train_final_params_only.py');runner.init_logging();runner.main(config)
    else:raise ValueError('Unknown training stage')

if __name__=='__main__':main(sys.argv[1],Path(sys.argv[2]))
