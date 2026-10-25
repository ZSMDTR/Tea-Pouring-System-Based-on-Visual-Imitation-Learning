"""CPU-only atomic save/restore and seeded loader replay regression."""
import os
os.environ['JAX_PLATFORMS']='cpu';os.environ['CUDA_VISIBLE_DEVICES']=''
import sys,json,tempfile,dataclasses
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'full_training'),str(Path(__file__).resolve().parents[1])]
import jax,numpy as np,optax
import jax.numpy as jnp
import flax.nnx as nnx
from openpi.training.utils import TrainState
from openpi.shared import array_typing as at
from openpi.training.data_loader import TorchDataLoader
import orbax_adapter as adapter
import checkpoint_catalog as catalog
import loop
import torch

p=nnx.State({'w':nnx.VariableState(nnx.Param,jnp.arange(32,dtype=jnp.float32))})
tx=optax.adam(1e-3)
with at.disable_typechecking():state=TrainState(step=jnp.array(1),params=p,model_def=None,opt_state=tx.init(p),tx=tx,ema_decay=.99,ema_params=p)
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);norm=root/'norm.json';norm.write_text('{}')
 metadata={'format':catalog.FORMAT,'identity':{'repo':'local/demo','norm_sha256':catalog.digest(norm)}}
 adapter.save(root/'1',state,workbench=metadata,norm_path=norm)
 assert catalog.read(root/'1')['step']==1
 restored=adapter.restore(root/'1',jax.eval_shape(lambda:state))
 for a,b in zip(jax.tree.leaves(state),jax.tree.leaves(restored),strict=True):np.testing.assert_array_equal(a,b)
 with patch.object(adapter.shutil,'copyfile',side_effect=OSError('injected asset failure')):
  try:adapter.save(root/'2',state,workbench=metadata,norm_path=norm)
  except OSError:pass
  else:raise AssertionError('failure not injected')
 assert not (root/'2').exists()
 assert catalog.read(root/'1')['step']==1
 try:adapter.save(root/'1',state,workbench=metadata,norm_path=norm)
 except FileExistsError:pass
 else:raise AssertionError('overwrite allowed')

class Dataset:
 def __len__(self):return 7
 def __getitem__(self,index):return {'i':np.array(index),'n':np.array(np.random.random()),'t':torch.rand(()).numpy()}
class Config:seed=42

def loader(*args,**kwargs):return TorchDataLoader(Dataset(),local_batch_size=1,shuffle=True,num_workers=0,seed=42,framework='pytorch')
with patch.object(loop.data_loader,'create_data_loader',side_effect=loader):
 continuous=loop.batches(Config(),None,0)
 expected=[next(continuous) for _ in range(12)]
 resumed=loop.batches(Config(),None,9)
 for index in range(9,12):
  actual=next(resumed)
  for key in actual:np.testing.assert_array_equal(actual[key],expected[index][key])
print(json.dumps({'orbax_roundtrip':True,'asset_failure_not_published':True,'overwrite_rejected':True,'seeded_shuffle_and_random_replay_across_epoch':True}))
