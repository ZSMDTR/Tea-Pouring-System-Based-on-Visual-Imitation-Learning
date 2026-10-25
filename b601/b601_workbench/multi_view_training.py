"""Opt-in dual-view entry. The validated legacy runners remain byte-identical."""
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main(mode, folder):
    import camera_contract as cameras
    request = json.loads((folder/'request.json').read_text())
    dataset_root=Path(request['config']['dataset_root'])
    if dataset_root.name!='local' or Path(request['source']).resolve()!=(dataset_root/request['dataset']).resolve():
        raise ValueError('双视角训练的数据位置与本地数据根目录不一致。')
    # LeRobot reads this constant at import time, including imports via loop/config.
    os.environ['HF_LEROBOT_HOME']=str(dataset_root.parent)
    required = cameras.validate(request['camera_contract'])
    if required['mode'] != cameras.DUAL or cameras.for_training(request['source'], request.get('resume_from')) != required:
        raise ValueError('双视角训练请求与数据或恢复存档不一致。')
    full = request.get('save_mode') == 'full'
    if full:
        sys.path.insert(0, str(ROOT/'full_training'))
    path = ROOT/('full_training/entry.py' if full else 'training_runner.py')
    spec = importlib.util.spec_from_file_location('wb_dual_entry', path)
    entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
    from multi_view_config import with_dual_cameras
    original_config = entry.make_config
    entry.make_config = lambda location: with_dual_cameras(original_config(location))
    if full:
        # Only dual runs get an extra identity. Existing single-camera restore
        # checks and all protected full_training sources remain unchanged.
        import loop
        from checkpoint_catalog import digest
        original_identity = loop.identity
        def identity(config, req):
            value = original_identity(config, req)
            value['camera_contract'] = required
            value['implementation'].update({str(ROOT/name): digest(ROOT/name) for name in (
                'camera_contract.py', 'multi_view_training.py', 'multi_view_policy.py', 'multi_view_config.py')})
            return value
        loop.identity = identity
    entry.main(mode, folder)


if __name__ == '__main__':
    main(sys.argv[1], Path(sys.argv[2]))
