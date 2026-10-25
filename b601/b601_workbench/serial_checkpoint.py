# UNCONNECTED PROTOTYPE: not enabled; Orbax backport investigation takes priority.
"""Single-host streaming checkpoints. No pickle or patched Orbax save path.

Each leaf uses a temporary device copy so JAX's host cache belongs to the
short-lived copy, never the live training state. Publish only after fsync.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

FORMAT = 'b601-serial-v1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8*1024*1024), b''): h.update(block)
    return h.hexdigest()


def committed(root):
    result=[]
    for p in Path(root).glob('[0-9]*'):
        try:
            m=json.loads((p/'checkpoint.json').read_text())
            if p.name==str(m['step']) and m['format']==FORMAT and m['complete']:
                result.append((m['step'],p))
        except (OSError,ValueError,KeyError): pass
    return sorted(result)


def save(root, state, metadata):
    import jax
    import numpy as np
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    step=int(state.step);target=root/str(step)
    if target.exists(): raise FileExistsError(target)
    pairs,_=jax.tree_util.tree_flatten_with_path(state)
    needed=sum(getattr(x,'size',1)*getattr(getattr(x,'dtype',None),'itemsize',8) for _,x in pairs)
    if shutil.disk_usage(root).free < needed+1024**3: raise OSError('磁盘空间不足以保存完整 checkpoint。')
    staging=root/('.saving_'+uuid.uuid4().hex);staging.mkdir()
    leaves=[]
    for i,(path,leaf) in enumerate(pairs):
        # Synchronous copy, transfer, write, release: only one host leaf at a time.
        copy=leaf.copy() if isinstance(leaf,jax.Array) else leaf
        host=np.asarray(jax.device_get(copy))
        file=staging/f'{i:05d}.npy'
        with file.open('xb') as f:
            # Store raw bytes for bfloat16 too (NumPy otherwise loses its dtype).
            np.save(f,host.view(np.uint8).reshape(-1) if host.ndim else host.reshape(1).view(np.uint8),allow_pickle=False)
            f.flush();os.fsync(f.fileno())
        leaves.append({'path':jax.tree_util.keystr(path),'shape':list(host.shape),'dtype':str(host.dtype),'file':file.name,'sha256':digest(file)})
        del host,copy
    manifest={'format':FORMAT,'complete':True,'step':step,'leaves':leaves,'metadata':metadata}
    with (staging/'checkpoint.json').open('x') as f:
        json.dump(manifest,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.rename(staging,target)
    fd=os.open(root,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return target


def restore(path, template, expected_metadata=None):
    import jax
    import numpy as np
    import ml_dtypes  # Registers bfloat16 with NumPy.
    path=Path(path)
    manifest=json.loads((path/'checkpoint.json').read_text())
    if manifest.get('format')!=FORMAT or not manifest.get('complete'):raise ValueError('不是完整串行 checkpoint。')
    if expected_metadata is not None and manifest['metadata']!=expected_metadata:raise ValueError('训练配置或数据来源已变化，拒绝续训。')
    pairs,treedef=jax.tree_util.tree_flatten_with_path(template)
    if len(pairs)!=len(manifest['leaves']):raise ValueError('checkpoint 结构不匹配。')
    restored=[]
    for (key,shape),entry in zip(pairs,manifest['leaves'],strict=True):
        dtype=np.dtype(shape.dtype) if hasattr(shape,'dtype') else np.asarray(shape).dtype
        if entry['path']!=jax.tree_util.keystr(key) or entry['shape']!=list(getattr(shape,'shape',())) or entry['dtype']!=str(dtype):raise ValueError('checkpoint 参数结构不匹配。')
        file=path/entry['file']
        if file.parent!=path or file.is_symlink() or digest(file)!=entry['sha256']:raise ValueError('checkpoint 文件校验失败。')
        raw=np.load(file,allow_pickle=False,mmap_mode='r')
        host=raw.view(dtype).reshape(entry['shape'])
        value=jax.device_put(host,getattr(shape,'sharding',None));value.block_until_ready()
        restored.append(value)
        del raw,host
    state=jax.tree_util.tree_unflatten(treedef,restored)
    if int(state.step)!=manifest['step']:raise ValueError('checkpoint 步数不匹配。')
    return state
