# UNCONNECTED PROTOTYPE: not enabled; Orbax backport investigation takes priority.
"""Workbench training loop using bounded host transfers and complete state saves."""
import dataclasses
import functools
import hashlib
import json
import logging
from pathlib import Path
import time

import jax
import jax.numpy as jnp
import numpy as np
from openpi.training import data_loader, sharding
import serial_checkpoint as checkpoints


def identity(config, request):
    from dataset_export import inventory
    return {'dataset':request['dataset'],'source_inventory':inventory(Path(request['source'])),
            'batch':config.batch_size,'seed':config.seed,'model':repr(config.model),
            'optimizer':repr(config.optimizer),'schedule':repr(config.lr_schedule),
            'freeze':repr(config.freeze_filter),'ema':config.ema_decay,
            'norm_sha256':checkpoints.digest(config.assets_dirs/config.data.repo_id/'norm_stats.json')}


def main(config, folder, runner):
    request=json.loads((folder/'request.json').read_text())
    logging.basicConfig(level=logging.INFO)
    root=Path(config.checkpoint_dir);resume_root=root/'resume'
    metadata=identity(config,request)
    rng=jax.random.key(config.seed);train_rng,init_rng=jax.random.split(rng)
    mesh=sharding.make_mesh(config.fsdp_devices)
    data_sharding=jax.sharding.NamedSharding(mesh,jax.sharding.PartitionSpec(sharding.DATA_AXIS))
    replicated=jax.sharding.NamedSharding(mesh,jax.sharding.PartitionSpec())
    resuming=bool(request.get('resume_from'))
    if not resuming:
        root.mkdir(parents=True,exist_ok=False)
    state,state_sharding=runner.init_train_state(config,init_rng,mesh,resume=resuming)
    if resuming:
        state=checkpoints.restore(Path(request['resume_from']),state,metadata)
    jax.block_until_ready(state)
    start=int(state.step)
    if start>=config.num_train_steps:raise ValueError('目标总步数必须大于已保存步数。')
    loader=data_loader.create_data_loader(config,sharding=data_sharding,shuffle=True)
    iterator=iter(loader)
    # Recreate seeded sample order and advance to the saved next batch.
    # This costs data reads on resume, but avoids silently restarting the shuffle.
    for _ in range(start):next(iterator)
    train=jax.jit(functools.partial(runner.train_step,config),in_shardings=(replicated,state_sharding,data_sharding),out_shardings=(state_sharding,replicated),donate_argnums=(1,))
    interval=request.get('save_interval',1000)
    for step in range(start,config.num_train_steps):
        batch=next(iterator)
        with sharding.set_mesh(mesh):state,info=train(train_rng,state,batch)
        if step%config.log_interval==0:
            logging.info('Step %d: %s',step+1,{k:float(v) for k,v in jax.device_get(info).items()})
        if (step+1)%interval==0 or step+1==config.num_train_steps:
            jax.block_until_ready(state)
            logging.info('SERIAL checkpoint begin step=%d',step+1)
            checkpoint=checkpoints.save(resume_root,state,metadata)
            logging.info('SERIAL checkpoint committed: %s',checkpoint)
            (folder/'progress.json').write_text(json.dumps({'step':step+1,'checkpoint':str(checkpoint)}))
    # Keep standard OpenPI inference export compatible with the existing policy loader.
    # Full recovery checkpoints are under resume/<completed-step>.
    runner.save_final_inference_checkpoint(config,state,loader,config.num_train_steps-1)
