"""Compare real transformed B601 batches after reconstruction of data position."""
import os
os.environ['JAX_PLATFORMS']='cpu';os.environ['CUDA_VISIBLE_DEVICES']='';os.environ['HF_HUB_OFFLINE']='1'
import sys,json
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'full_training'),str(Path(__file__).resolve().parents[1])]
from entry import make_config
config=make_config(Path(sys.argv[1]))
import jax,numpy as np
from loop import batches
continuous=batches(config,None,0)
expected=[next(continuous) for _ in range(4)]
resumed=batches(config,None,2)
for batch in expected[2:]:
 actual=next(resumed)
 for a,b in zip(jax.tree.leaves(batch),jax.tree.leaves(actual),strict=True):np.testing.assert_array_equal(a,b)
print(json.dumps({'dataset':config.data.repo_id,'resume_at':2,'next_real_batches_exact':2}))
