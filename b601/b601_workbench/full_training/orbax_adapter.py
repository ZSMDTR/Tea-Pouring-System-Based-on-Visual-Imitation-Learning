"""Experimental single-host adapter. No installed files or stable code modified.

Keep Orbax array encoding. Copy one device array at a time into a short-lived
array so host caches cannot attach to the live optimizer/model state.
"""
from contextlib import contextmanager
from pathlib import Path
import dataclasses
import gc
import ctypes
import json
import os
import uuid
import shutil
import importlib.metadata
import jax
import orbax.checkpoint as ocp
from orbax.checkpoint._src.serialization import replica_slices

@contextmanager
def temporary_transfer():
    if importlib.metadata.version('orbax-checkpoint')!='0.11.23' or jax.process_count()!=1:
        raise RuntimeError('Experimental adapter validated only for single-host Orbax 0.11.23')
    original=replica_slices.transfer_arrays_to_host
    def transfer(arrays,*args,**kwargs):
        result=[]
        for array in arrays:
            temp=array.copy()
            try:
                pieces=original([temp],*args,**kwargs)
                result.extend(pieces)
            finally:
                temp.delete()
        return result
    replica_slices.transfer_arrays_to_host=transfer
    try:yield
    finally:replica_slices.transfer_arrays_to_host=original

def trim():
    gc.collect();ctypes.CDLL(None).malloc_trim(0)

def structure(state):
    pairs,treedef=jax.tree_util.tree_flatten_with_path(state)
    spec=[{'path':jax.tree_util.keystr(p),'shape':list(getattr(v,'shape',())), 'dtype':str(getattr(v,'dtype',type(v).__name__))} for p,v in pairs]
    groups={k:{} for k in ('model','adam_mu','adam_nu','training')}
    for i,(path,v) in enumerate(pairs):
        text=jax.tree_util.keystr(path)
        if text.startswith('.ema_params'):continue
        group='model' if text.startswith('.params') else 'adam_mu' if '.mu' in text else 'adam_nu' if '.nu' in text else 'training'
        groups[group][str(i)]=v
    return spec,groups,treedef

def save(destination,state, *, workbench=None, norm_path=None):
    destination=Path(destination).resolve()
    if destination.exists():raise FileExistsError(destination)
    if state.ema_params is None:raise ValueError('This experimental schema requires EMA')
    spec,groups,_=structure(state)
    staging=destination.parent/('.saving_'+uuid.uuid4().hex);staging.mkdir(parents=True)
    with temporary_transfer():
        for name,payload in [('params',{'params':state.ema_params}),*groups.items()]:
            if not payload:continue
            with ocp.Checkpointer(ocp.PyTreeCheckpointHandler(save_concurrent_gb=8,save_device_host_concurrent_gb=8,enable_pinned_host_transfer=False)) as cp:
                cp.save(staging/name,args=ocp.args.PyTreeSave(payload))
            # Flush and evict only this completed test item's file cache, never
            # system-wide caches. Dirty checkpoint writes can otherwise evict
            # unrelated cold anonymous pages even with ample MemAvailable.
            for file in (staging/name).rglob('*'):
                if file.is_file():
                    fd=os.open(file,os.O_RDONLY)
                    try:
                        os.fsync(fd)
                        os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
                    finally:os.close(fd)
            trim()
    if workbench is not None:
        norm_destination=staging/'assets'/workbench['identity']['repo']/'norm_stats.json'
        norm_destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(norm_path,norm_destination)
        fd=os.open(norm_destination,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    # Publication only after all blocking Orbax saves/finalization succeeded.
    manifest={'format':'orbax-four-block-v1','complete':True,'step':int(state.step),'structure':spec}
    if workbench is not None:manifest['workbench']=workbench
    with (staging/'complete.json').open('x') as f:json.dump(manifest,f);f.flush();os.fsync(f.fileno())
    staging.rename(destination)
    fd=os.open(destination.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def restore(source,template):
    source=Path(source).resolve();meta=json.loads((source/'complete.json').read_text())
    spec,groups,treedef=structure(template)
    if meta.get('format')!='orbax-four-block-v1' or not meta.get('complete') or spec!=meta['structure']:raise ValueError('Incomplete or incompatible checkpoint')
    pairs,_=jax.tree_util.tree_flatten_with_path(template);leaves=[None]*len(pairs)
    for name,payload in [('params',{'params':template.ema_params}),*groups.items()]:
        if not payload:continue
        with ocp.Checkpointer(ocp.PyTreeCheckpointHandler(restore_concurrent_gb=8)) as cp:
            result=cp.restore(source/name,args=ocp.args.PyTreeRestore(item=payload))
        if name=='params':
            values=iter(jax.tree.leaves(result['params']))
            for i,(path,_) in enumerate(pairs):
                if jax.tree_util.keystr(path).startswith('.ema_params'):leaves[i]=next(values)
        else:
            for key,value in result.items():leaves[int(key)]=value
    if any(v is None for v in leaves):raise ValueError('Missing checkpoint leaves')
    return jax.tree_util.tree_unflatten(treedef,leaves)
