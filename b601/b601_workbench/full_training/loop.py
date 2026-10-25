"""Single GPU, full-state periodic saving; stable final-only runner is independent."""
import functools
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import random
import shutil
import subprocess
import threading
import time

import jax
import numpy as np
from openpi.training import data_loader, sharding
import orbax_adapter as checkpoints
from checkpoint_catalog import FORMAT, RUNTIME, digest, read
from dataset_export import inventory
from recording import write_json


def identity(config, request):
    files = sorted(Path(__file__).parent.glob('*.py'))
    files += [RUNTIME / 'scripts/train.py', RUNTIME / 'src/openpi/training/data_loader.py',
              RUNTIME / 'src/openpi/training/config.py']
    return {'dataset': request['dataset'], 'source': str(Path(request['source']).resolve()),
        'source_inventory': inventory(request['source']), 'repo': config.data.repo_id,
        'batch': config.batch_size, 'seed': config.seed, 'model': repr(config.model),
        'optimizer': repr(config.optimizer), 'schedule': repr(config.lr_schedule),
        'freeze': repr(config.freeze_filter), 'ema': config.ema_decay,
        'norm_sha256': digest(config.assets_dirs / config.data.repo_id / 'norm_stats.json'),
        'versions': {p: importlib.metadata.version(p) for p in ('jax','jaxlib','orbax-checkpoint','flax','optax','torch','lerobot')},
        'implementation': {str(p): digest(p) for p in files},
        'data_order': 'seeded-replay-v1', 'deterministic_gpu': True}


def batches(config, shard, start):
    import torch
    # Rebuild shuffle and all CPU-side transform random streams, then replay
    # consumed batches. num_workers=0 is required for this contract.
    random.seed(config.seed); np.random.seed(config.seed); torch.manual_seed(config.seed)
    loader = data_loader.create_data_loader(config, sharding=shard, shuffle=True)
    iterator = iter(loader)
    for index in range(start):
        next(iterator)
        if (index + 1) % 100 == 0: logging.info('恢复数据位置 %d/%d', index + 1, start)
    return iterator


def watch_memory(folder, stop):
    while not stop.is_set():
        def values(path):
            return {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path(path).read_text().splitlines()
                    if len(line.split()) > 1 and line.split()[1].isdigit()}
        proc = values('/proc/self/status'); host = values('/proc/meminfo')
        row = {'time': time.time(), 'rss_gib': proc['VmRSS']/2**30,
               'available_gib': host['MemAvailable']/2**30, 'process_swap_gib': proc.get('VmSwap',0)/2**30}
        with (folder / 'memory.jsonl').open('a') as f: f.write(json.dumps(row)+'\n')
        if row['available_gib'] < 10 or row['rss_gib'] > 44 or row['process_swap_gib'] > 1:
            write_json(folder/'memory_guard.json', row)
            print('内存保护停止训练；已完成的存档保留。', flush=True)
            os._exit(77)
        stop.wait(1)


def main(config, folder, runner):
    logging.basicConfig(level=logging.INFO)
    request = json.loads((folder/'request.json').read_text())
    processes = subprocess.check_output(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader'], text=True).strip()
    if processes: raise ValueError('显卡正被其他任务使用，请先结束该任务后重试。')
    metadata = identity(config, request)
    resume = request.get('resume_from')
    if resume:
        previous = read(resume)
        if previous['workbench']['identity'] != metadata:
            raise ValueError('数据、归一化统计、训练配置或程序版本已改变，拒绝恢复。请新建训练任务。')
        if previous['step'] >= config.num_train_steps: raise ValueError('目标总步数须大于存档步数。')
    root = Path(config.checkpoint_dir); root.mkdir(parents=True, exist_ok=False)
    stop = threading.Event()
    watcher = threading.Thread(target=watch_memory, args=(folder, stop), daemon=True); watcher.start()
    try:
        mesh = sharding.make_mesh(config.fsdp_devices)
        shard = jax.sharding.NamedSharding(mesh, jax.sharding.PartitionSpec(sharding.DATA_AXIS))
        replicated = jax.sharding.NamedSharding(mesh, jax.sharding.PartitionSpec())
        train_rng, init_rng = jax.random.split(jax.random.key(config.seed))
        state, state_shard = runner.init_train_state(config, init_rng, mesh, resume=bool(resume))
        if resume: state = checkpoints.restore(resume, state)
        jax.block_until_ready(state)
        start = int(state.step)
        if resume and start != previous['step']: raise ValueError('存档计数器与完成标记不一致。')
        write_json(folder/'progress.json', {'step': start, 'phase': '恢复数据位置' if start else '准备训练', 'checkpoint': resume})
        iterator = batches(config, shard, start)
        train = jax.jit(functools.partial(runner.train_step, config),
            in_shardings=(replicated, state_shard, shard), out_shardings=(state_shard, replicated), donate_argnums=(1,))
        latest = resume
        for step in range(start, config.num_train_steps):
            with sharding.set_mesh(mesh): state, info = train(train_rng, state, next(iterator))
            metrics = {key: float(value) for key, value in info.items()}
            write_json(folder/'progress.json', {'step': step+1, 'phase': '训练中', 'checkpoint': latest, 'metrics': metrics})
            with (folder/'metrics.jsonl').open('a') as f: f.write(json.dumps({'step':step+1, **metrics})+'\n')
            if (step+1) % config.log_interval == 0 or step == start: logging.info('Step %d: %s', step+1, metrics)
            if (step+1) % request['save_interval'] == 0 or step+1 == config.num_train_steps:
                if inventory(request['source']) != metadata['source_inventory']: raise ValueError('训练数据在运行期间被修改，停止保存。')
                if shutil.disk_usage(root).free < 61*2**30: raise ValueError('剩余磁盘不足以安全保存完整状态；此前完整存档仍可恢复。')
                jax.block_until_ready(state)
                write_json(folder/'progress.json', {'step': step+1, 'phase': '串行保存中', 'checkpoint': latest, 'metrics': metrics})
                destination = root/str(step+1)
                checkpoints.save(destination, state, workbench={'format': FORMAT, 'identity': metadata,
                    'parent': resume, 'completed_batches': step+1},
                    norm_path=config.assets_dirs/config.data.repo_id/'norm_stats.json')
                latest = str(destination)
                write_json(folder/'progress.json', {'step': step+1, 'phase': '完整存档已保存', 'checkpoint': latest, 'metrics': metrics})
                logging.info('完整存档已提交：%s', latest)
    finally:
        stop.set(); watcher.join(timeout=2)
