"""Storage inventory, verified project backups and redacted support bundles."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / '.runtime/maintenance'
LOCK = threading.RLock()
STATE = {'active': False, 'state': 'idle', 'message': '尚未执行维护任务。'}
STORAGE = None
try:
    stored=json.loads((RUNTIME/'state.json').read_text())
    STATE=stored.get('job',STATE);STORAGE=stored.get('storage')
    if STATE.get('active'):STATE.update(active=False,state='error',message='维护进程因服务重启而中断；请检查未完成输出后重试。')
except (OSError,ValueError):pass


def persist():
    from recording import write_json
    RUNTIME.mkdir(parents=True,exist_ok=True)
    write_json(RUNTIME/'state.json',{'job':STATE,'storage':STORAGE})


def disk(path):
    usage = shutil.disk_usage(path)
    return {'path': str(path), 'total': usage.total, 'used': usage.used, 'free': usage.free}


def tree_size(root):
    root = Path(root)
    size = count = skipped = 0
    if root.is_symlink():
        return {'bytes': 0, 'files': 0, 'skipped': 1}
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base)/d).is_symlink()]
        for name in files:
            path = Path(base)/name
            try:
                if path.is_symlink():
                    skipped += 1
                else:
                    st = path.stat()
                    size += st.st_blocks * 512
                    count += 1
            except OSError:
                skipped += 1
    return {'bytes': size, 'files': count, 'skipped': skipped}


def status(config):
    import workspace_state
    with LOCK:
        return {'job': dict(STATE), 'storage': STORAGE,
                'reserve_gib':workspace_state.reserve_gib(),
                'disks': [disk(p) for p in dict.fromkeys([config['dataset_root'], config['checkpoint_root'], str(ROOT)]) if Path(p).exists()]}


def inventory(config):
    rows = []
    data_root = Path(config['dataset_root'])
    for path in sorted(data_root.iterdir()):
        if not path.is_dir() or path.is_symlink():
            continue
        if path.name == '.b601-trash':
            category = '回收站'
        elif path.name.startswith('.preparing_'):
            category = '未完成的数据准备'
        elif (path/'workbench_export.json').exists() or (path/'workbench_conversion.json').exists() or path.name.endswith('_openpi'):
            category = '训练副本'
        elif (path/'meta/info.json').is_file():
            category = '原始数据'
        else:
            category = '其他数据目录'
        rows.append({'name': path.name, 'path': str(path), 'category': category, **tree_size(path)})
    for title, path in [('模型与训练存档', Path(config['checkpoint_root'])), ('基础模型', Path(config['base_model'])),
                        ('任务日志与界面备份', ROOT/'.runtime'), ('整理记录与项目配置', ROOT/'data')]:
        if path.exists():
            rows.append({'name': title, 'path': str(path), 'category': title, **tree_size(path)})
    return {'scanned_at': time.time(), 'rows': rows,
            'note': '按目录统计已分配空间；硬链接可能重复计数。扫描不会删除内容。训练存档不自动清理。'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def backup(destination, config, include_data=False, progress=lambda message: None, include_models=False):
    """New folder only; SQLite uses its snapshot API and every copied file is hashed."""
    parent = Path(destination).expanduser()
    if not parent.is_absolute() or not parent.is_dir() or parent.is_symlink():
        raise ValueError('请选择已存在的绝对目录作为备份位置。')
    parent = parent.resolve()
    sources = [ROOT, Path(config['dataset_root']).resolve(), Path(config['checkpoint_root']).resolve(), Path(config['base_model']).resolve()]
    if any(parent == p or parent.is_relative_to(p) for p in sources):
        raise ValueError('备份位置不能放在工作台、数据或模型目录内部。')
    name = time.strftime('b601_backup_%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
    target = parent/name
    staging = parent/('.'+name+'.partial')
    selected = []
    for folder in ('web', 'docs', 'rtc', 'full_training', 'data', 'vendor', 'tests'):
        for p in (ROOT/folder).rglob('*'):
            if p.is_symlink():raise ValueError('备份源中有符号链接，请先核对：'+str(p))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '-wal', '-shm') and not p.name.endswith(('-wal', '-shm', '.tmp')):
                selected.append((p, Path('workbench')/p.relative_to(ROOT)))
    for p in [*ROOT.glob('*.py'), *ROOT.glob('*.json'), *ROOT.glob('*.md')]:
        selected.append((p, Path('workbench')/p.name))
    if include_data:
        for p in Path(config['dataset_root']).rglob('*'):
            if p.is_symlink():raise ValueError('数据备份源中有符号链接，请先核对：'+str(p))
            if p.is_file():
                selected.append((p, Path('datasets')/p.relative_to(config['dataset_root'])))
    if include_models:
        for key,folder in [('checkpoint_root','checkpoints'),('base_model','base_model')]:
            base=Path(config[key]).resolve()
            if not base.is_dir():raise ValueError('模型备份源不存在：'+str(base))
            for p in base.rglob('*'):
                if p.is_symlink():raise ValueError('模型备份源中有符号链接，请先核对：'+str(p))
                if p.is_file():selected.append((p,Path(folder)/p.relative_to(base)))
    if any(p.is_symlink() for p, _ in selected):
        raise ValueError('备份源存在符号链接，请先核对；未创建备份。')
    needed = sum(p.stat().st_size for p, _ in selected)
    if shutil.disk_usage(parent).free < needed + 2*2**30:
        raise ValueError('备份位置空间不足，需要额外保留 2 GiB。')
    staging.mkdir()
    manifest = {'version': 1, 'created': time.time(), 'contains_datasets': include_data,
                'contains_models': include_models, 'source_dataset_root': config['dataset_root'], 'files': []}
    try:
        for i, (source, relative) in enumerate(selected):
            if source.is_symlink():
                raise ValueError('备份源在操作期间变为符号链接。')
            progress(f'正在备份并校验 {i+1}/{len(selected)} 个文件…')
            dest = staging/relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix in ('.sqlite3', '.sqlite', '.db'):
                with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(dest) as dst:
                    src.backup(dst)
                source_hash = digest(dest)
            else:
                before = source.stat()
                shutil.copy2(source, dest)
                source_hash = digest(source)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError('备份期间源文件发生变化，请重新备份。')
            if digest(dest) != source_hash:
                raise ValueError('备份校验失败，未提交。')
            manifest['files'].append({'path': str(relative), 'bytes': dest.stat().st_size, 'sha256': source_hash})
        (staging/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        staging.rename(target)
    except BaseException:
        # Keep failed output clearly separate for diagnosis; never delete sources.
        raise
    return {'path': str(target), 'files': len(selected), 'bytes': needed, 'contains_datasets': include_data, 'contains_models': include_models}


def redacted(value, profile):
    if isinstance(value, dict):
        return {k: ('[已移除]' if any(s in k.lower() for s in ('token', 'password', 'secret', 'authorization', 'serial')) else redacted(v, profile)) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(v, profile) for v in value]
    if isinstance(value, str):
        value = value.replace(str(Path.home()), '<HOME>')
        for camera in [profile.get('camera', {}), profile.get('secondary_camera', {})]:
            serial = str(camera.get('serial_number_or_name', ''))
            if serial:
                value = value.replace(serial, '<CAMERA>')
        return value
    return value


def support_bundle(config):
    import can_control
    import recording
    import experiments
    profile = json.loads((ROOT/'capture_profile.json').read_text())
    try:
        can = can_control.read_state()
    except Exception as exc:
        can = {'error': str(exc)}
    job = recording.status()
    experiment = experiments.status()
    # Deliberately exclude prompts, notes, raw images, logs and credentials.
    data = {'created': time.time(), 'config': config, 'profile': profile, 'can': can,
            'device_job': {k: job.get(k) for k in ('mode', 'state', 'active', 'cleanup_ok', 'errors')},
            'experiment': {k: experiment.get(k) for k in ('kind', 'state', 'active', 'progress')},
            'storage': status(config)['disks']}
    RUNTIME.mkdir(parents=True, exist_ok=True)
    path = RUNTIME/('support_'+uuid.uuid4().hex+'.json')
    path.write_text(json.dumps(redacted(data, profile), ensure_ascii=False, indent=2))
    return {'path': str(path), 'download': '/api/manage/download?name='+path.name}


def start(kind, data, config):
    global STATE, STORAGE
    if kind not in ('scan', 'backup', 'support'):
        raise ValueError('未知维护任务。')
    if kind == 'backup' and any(type(data.get(key,False)) is not bool for key in ('include_data','include_models')):
        raise ValueError('备份选项无效。')
    with LOCK:
        if STATE['active']:
            raise ValueError('已有维护任务正在执行。')
        STATE = {'id': uuid.uuid4().hex, 'active': True, 'kind': kind, 'state': 'running', 'message': '正在准备…'}
        persist()
    def progress(message):
        with LOCK:
            STATE['message'] = message
            persist()
    def run():
        global STORAGE
        try:
            if kind == 'scan':
                result = inventory(config)
                with LOCK:
                    STORAGE = result
                message = '空间统计已更新。'
            elif kind == 'backup':
                result = backup(data.get('destination', ''), config, data.get('include_data', False), progress, data.get('include_models',False))
                message = '备份与逐文件校验完成。'
            else:
                result = support_bundle(config)
                message = '已生成脱敏诊断文件。'
            with LOCK:
                STATE.update(active=False, state='completed', message=message, result=result)
                persist()
        except Exception as exc:
            with LOCK:
                STATE.update(active=False, state='error', message=str(exc))
                persist()
    threading.Thread(target=run, daemon=True).start()
    return status(config)
