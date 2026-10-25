"""Explicit isolated-runtime entry, using the tested official OpenPI checkout."""
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from checkpoint_catalog import RUNTIME, read
from recording import write_json


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def make_config(folder):
    request = json.loads((folder / 'request.json').read_text())
    os.environ['HF_LEROBOT_HOME'] = str(Path(request['config']['dataset_root']).parent)
    from openpi.training import config as c, weight_loaders
    from openpi.models import pi0_config
    from b601_config import LeRobotB601DataConfig
    return c.TrainConfig(name='pi05_b601', exp_name=request['run_name'],
        model=pi0_config.Pi0Config(pi05=True),
        data=LeRobotB601DataConfig(repo_id='local/' + request['dataset'], base_config=c.DataConfig(prompt_from_task=True)),
        weight_loader=weight_loaders.CheckpointWeightLoader(str(Path(request['config']['base_model']) / 'params')),
        assets_base_dir=str(folder / 'assets'), checkpoint_base_dir=str(Path(request['output']).parent.parent),
        batch_size=request['batch'], num_workers=0, num_train_steps=request['steps'],
        wandb_enabled=False, overwrite=False, resume=False)


def main(mode, folder):
    from compatibility import enable
    enable()
    config = make_config(folder)
    request = json.loads((folder / 'request.json').read_text())
    if Path(config.checkpoint_dir).resolve() != Path(request['output']).resolve():
        raise ValueError('训练输出路径与请求不一致。')
    if mode == 'check':
        print(json.dumps({'checkpoint': str(config.checkpoint_dir), 'dataset': config.data.repo_id, 'batch': config.batch_size})); return
    if mode == 'norm':
        if request.get('resume_from'):
            meta = read(request['resume_from'])
            target = config.assets_dirs / config.data.repo_id / 'norm_stats.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(request['resume_from']) / 'assets' / config.data.repo_id / 'norm_stats.json', target)
            print('沿用存档内归一化统计。', flush=True)
        else:
            import numpy as np
            from openpi.shared import normalize
            helper = module('wb_full_norm', RUNTIME / 'scripts/compute_norm_stats.py')
            data = config.data.create(config.assets_dirs, config.model)
            loader, _ = helper.create_torch_dataloader(data, config.model.action_horizon, 1, config.model, 0)
            stats = {key: normalize.RunningStats() for key in ('state', 'actions')}
            for index, batch in enumerate(loader, 1):
                for key in stats: stats[key].update(np.asarray(batch[key]))
                if index % 100 == 0:
                    write_json(folder / 'progress.json', {'phase':'计算归一化统计', 'frames':index})
                    print(f'归一化统计已读取 {index} 帧', flush=True)
            normalize.save(config.assets_dirs / data.repo_id, {key: value.get_statistics() for key, value in stats.items()})
    elif mode == 'train':
        from loop import main as train
        train(config, folder, module('wb_official_train', RUNTIME / 'scripts/train.py'))
    else: raise ValueError('Unknown stage')


if __name__ == '__main__': main(sys.argv[1], Path(sys.argv[2]))
