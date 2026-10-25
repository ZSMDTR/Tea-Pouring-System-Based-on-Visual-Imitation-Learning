"""Versioned model camera inputs. No SDK/ML imports or hardware side effects."""
import json
from pathlib import Path

VERSION = 1
WRIST = 'wrist'
DUAL = 'wrist_head'


def contract(mode=WRIST):
    if mode not in (WRIST, DUAL):
        raise ValueError('未知的模型相机输入约定。')
    return {'version': VERSION, 'mode': mode,
            'views': ['wrist_rgb', 'head_rgb'] if mode == DUAL else ['wrist_rgb'],
            'slots': {'wrist_rgb': 'left_wrist_0_rgb', **({'head_rgb': 'base_0_rgb'} if mode == DUAL else {})}}


def validate(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value != contract(value.get('mode')):
        raise ValueError('模型相机输入记录无效，拒绝猜测输入视角。')
    return contract(value['mode'])


def label(value):
    return '双视角 · 腕部 + 第三视角' if validate(value)['mode'] == DUAL else '单视角 · 腕部'


def dataset(root):
    info = json.loads((Path(root)/'meta/info.json').read_text())
    features = info.get('features', {})
    for name in ('wrist_rgb', 'head_rgb'):
        feature = features.get('observation.images.'+name)
        if feature is None and name == 'head_rgb':
            continue
        if not feature or feature.get('dtype') != 'video' or feature.get('shape') != [480, 640, 3]:
            raise ValueError(name+' 必须是 640×480 RGB 视频。')
    return contract(DUAL if 'observation.images.head_rgb' in features else WRIST)


def sidecar(run):
    run = Path(run)
    return run.parent/('.'+run.name+'.camera_contract.json')


def model(checkpoint):
    """Legacy checkpoints have wrist-only semantics, regardless of current USBs."""
    checkpoint = Path(checkpoint)
    records = []
    for path in (checkpoint/'camera_contract.json', sidecar(checkpoint.parent)):
        if path.is_file():
            records.append(validate(json.loads(path.read_text())))
    complete = checkpoint/'complete.json'
    if complete.is_file():
        identity = json.loads(complete.read_text()).get('workbench', {}).get('identity', {})
        if 'camera_contract' in identity:
            records.append(validate(identity['camera_contract']))
    if records and any(item != records[0] for item in records):
        raise ValueError('存档中的相机输入记录不一致。')
    return records[0] if records else contract()


def for_training(source, resume=None):
    available = dataset(source)
    required = model(resume) if resume else available
    if not set(required['views']).issubset(available['views']):
        raise ValueError('此模型需要腕部和第三视角，所选数据缺少第三视角。')
    return required
