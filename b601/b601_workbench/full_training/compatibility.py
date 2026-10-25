"""Experiment-local metadata compatibility bridge; never edits OpenPI files."""
import inspect
from openpi.models import model

def enable():
 source=inspect.getsource(model.restore_params)
 needle='item = {"params": metadata["params"]}'
 assert source.count(needle)==1
 source=source.replace(needle,'metadata = getattr(metadata, "item_metadata", metadata)\n        '+needle)
 namespace=dict(vars(model));exec(compile(source,__file__,'exec'),namespace)
 model.restore_params=namespace['restore_params']
