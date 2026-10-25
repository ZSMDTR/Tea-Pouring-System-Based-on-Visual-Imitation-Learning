"""Workbench-owned complete checkpoints; safe discovery without ML imports."""
import hashlib
import json
from pathlib import Path
import re
import shutil

RUNTIME = Path(__file__).resolve().parent.parent/'openpi_fullsave'
FULL_PYTHON = RUNTIME / '.venv/bin/python'
FORMAT = 'workbench-full-v1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def read(path):
    path = Path(path)
    meta = json.loads((path / 'complete.json').read_text())
    if not isinstance(meta, dict): raise ValueError('存档完成标记无效。')
    details = meta.get('workbench', {})
    if not isinstance(details, dict): raise ValueError('存档说明无效。')
    if (meta.get('format') != 'orbax-four-block-v1' or meta.get('complete') is not True
            or details.get('format') != FORMAT or type(meta.get('step')) is not int or meta['step'] < 1):
        raise ValueError('不是工作台完整训练存档。')
    if not all((path / name / '_METADATA').is_file() for name in ('params', 'model', 'adam_mu', 'adam_nu', 'training')):
        raise ValueError('训练存档缺少数据块。')
    repo = details['identity']['repo']
    if not isinstance(repo, str) or not re.fullmatch(r'local/[A-Za-z0-9_.-]+', repo):
        raise ValueError('存档数据集标识无效。')
    norm = path / 'assets' / repo / 'norm_stats.json'
    if norm.resolve() != norm.absolute(): raise ValueError('存档资产包含符号链接。')
    if digest(norm) != details['identity']['norm_sha256']:
        raise ValueError('存档的归一化统计缺失或已改变。')
    return meta


def resolve(root, ident):
    if not isinstance(ident, str) or not re.fullmatch(r'wb_train_[A-Za-z0-9_]+/[1-9][0-9]*', ident):
        raise ValueError('请选择列表中的完整训练存档。')
    root = Path(root).resolve(); path = root / ident
    if path.resolve() != path or not path.is_dir():
        raise ValueError('存档路径无效或包含符号链接。')
    return path, read(path)


def listing(root):
    root = Path(root)
    result = []
    for p in root.glob('wb_train_*/*/complete.json'):
        try:
            ident = str(p.parent.relative_to(root))
            path, meta = resolve(root, ident)
            identity = meta['workbench']['identity']
            import camera_contract
            inputs=camera_contract.model(path)
            result.append({'id': ident, 'step': meta['step'], 'dataset': identity['dataset'],
                           'batch': identity['batch'], 'path': str(path),
                           'camera_contract':inputs,'camera_label':camera_contract.label(inputs)})
        except (ValueError, OSError, KeyError, TypeError): continue
    return sorted(result, key=lambda x: (x['id'].split('/')[0], x['step']), reverse=True)


def validate_options(data, config, source):
    mode = data.get('save_mode', 'final')
    if mode not in ('final', 'full'): raise ValueError('未知保存方式。')
    resume = data.get('resume_checkpoint') or ''
    if mode == 'final':
        if resume: raise ValueError('最终参数模式不支持恢复训练。')
        return {'save_mode': 'final'}
    interval = data.get('save_interval', 1000)
    if type(interval) is not int or not 1 <= interval <= 30000:
        raise ValueError('完整保存间隔必须在 1–30000 步之间。')
    if not FULL_PYTHON.is_file(): raise ValueError('完整训练的独立环境不存在。')
    # Keep this release within the actual validated configuration.
    if data['batch'] != 1: raise ValueError('完整保存首版仅开放已验证的批大小 1。')
    options = {'save_mode': 'full', 'save_interval': interval}
    start = 0
    if resume:
        path, meta = resolve(config['checkpoint_root'], resume)
        identity = meta['workbench']['identity']
        if source.name != identity['dataset'] or str(source.resolve()) != identity['source'] or data['batch'] != identity['batch']:
            raise ValueError('恢复训练必须使用存档原来的数据和批大小。')
        start = meta['step']
        if data['steps'] <= start: raise ValueError(f'目标总步数必须大于存档的 {start} 步。')
        options['resume_from'] = str(path)
    saves = data['steps'] // interval - start // interval + (data['steps'] % interval != 0)
    required = saves * 51 * 2**30 + 10 * 2**30
    root = Path(config['checkpoint_root'])
    while not root.exists(): root = root.parent
    if shutil.disk_usage(root).free < required:
        raise ValueError(f'磁盘空间不足：本次计划保留 {saves} 份完整存档，需预留约 {required / 2**30:.0f} GiB。请增大保存间隔或减少总步数。')
    options['planned_saves'] = saves
    return options
