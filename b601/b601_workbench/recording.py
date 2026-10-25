"""Local single-episode job management; this module never imports robot drivers."""
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
import library

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / '.runtime/recordings'
LOCK = threading.Lock()
ACTIVE = {'starting', 'connecting', 'positioning', 'recording', 'returning', 'saving', 'teleop', 'gravity', 'preview', 'queued', 'executing', 'replaying', 'resting', 'processing'}


def write_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


def current_dir():
    try:
        name = (JOBS / 'current').read_text().strip()
        if len(name) == 32 and all(c in '0123456789abcdef' for c in name):
            return JOBS / name
    except OSError:
        pass
    return None


def status():
    folder = current_dir()
    if not folder:
        return {'state': 'idle', 'message': '填写任务描述，准备好后开始。', 'active': False}
    d = json.loads((folder / 'status.json').read_text())
    # The lock survives a workbench restart because the worker inherits it.
    with (JOBS / 'device.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = False
        except BlockingIOError:
            busy = True
    if d['state'] in ACTIVE and not busy:
        d.update(state='error', message='采集进程已退出。请检查设备和日志；未完成的数据保留，未标记为可用。')
    d['active'] = busy
    return d


def validate_request(data):
    if not isinstance(data, dict) or set(data) - {'task', 'seconds', 'prepared', 'collection', 'action_name', 'series_count', 'rest_seconds', 'auto_convert'}:
        raise ValueError('采集参数无效')
    task = data.get('task', '')
    if not isinstance(task, str) or not 1 <= len(task.strip()) <= 500:
        raise ValueError('请填写 1–500 字的任务描述。')
    seconds = data.get('seconds', 0)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 600:
        raise ValueError('自动结束时间应为 0–600 秒；0 表示手动结束。')
    if data.get('prepared') is not True:
        raise ValueError('请确认主从臂处于现有启动姿态、周围留有回零空间，且没有其他程序控制设备。')
    if 'action_name' in data:
        library.text(data['action_name'], 80)
    count=data.get('series_count',1);rest=data.get('rest_seconds',10)
    if type(count) is not int or not 1<=count<=100 or type(rest) is not int or not 0<=rest<=120 or type(data.get('auto_convert',False)) is not bool:
        raise ValueError('批量段数应为 1–100，休息时间应为 0–120 秒。')
    if count>1 and not seconds:raise ValueError('批量自动录制需要设置每条时长；手动单条请将段数设为 1。')
    if count>1 and 'action_name' in data:raise ValueError('命名动作每次录制一条。')
    return task.strip(), seconds


def start(data, config):
    task, seconds = validate_request(data)
    group = library.collection(data.get('collection'))
    import can_control
    if not can_control.ready(can_control.read_state()):
        raise ValueError('请先在工作概览启动 CAN。')
    profile = json.loads((ROOT/'capture_profile.json').read_text())
    if not Path(profile['leader_port']).exists():
        raise ValueError('未找到配置的引导臂串口：'+profile['leader_port'])
    with LOCK:
        JOBS.mkdir(parents=True, exist_ok=True)
        lock = (JOBS / 'device.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise ValueError('已有采集任务正在运行，请先结束当前任务。')
        try:
            import workspace_state
            workspace_state.check_space(config['dataset_root'])
            ident = uuid.uuid4().hex
            folder = JOBS / ident
            folder.mkdir()
            name = time.strftime('wb_%Y%m%d_%H%M%S_') + ident[:8]
            request = {'id': ident, 'task': task, 'seconds': seconds,
                       'collection': group['id'],
                       'dataset': name, 'root': str(Path(config['dataset_root']) / name)}
            request.update(series_count=data.get('series_count',1),rest_seconds=data.get('rest_seconds',10),auto_convert=data.get('auto_convert',False))
            pipeline=request['series_count']>1 or request['auto_convert']
            if pipeline:request['pipeline_config']=config
            if data.get('action_name'):
                request['action_name'] = data['action_name'].strip()
                import workspace_state
                request['action_project'] = workspace_state.current()
            write_json(folder / 'request.json', request)
            write_json(folder / 'status.json', {**request, 'state': 'starting', 'message': '正在加载采集环境…', 'frames': 0, 'elapsed': 0, 'saved': False})
            (folder / 'heartbeat').touch()
            library.add_members(group['id'], name, [{'index':0,'tasks':[task],'frames':0}])
            with (folder / 'worker.log').open('ab') as log:
                process = subprocess.Popen([config['lerobot_python'], '-B', str(ROOT / ('capture_pipeline.py' if pipeline else 'record_worker.py')), str(folder), '--lock-fd', str(lock.fileno())],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, pass_fds=(lock.fileno(),), start_new_session=True)
            write_json(folder / 'process.json', {'pid': process.pid})
            (JOBS / 'current').write_text(ident)
            # Reap the child without owning the job or hardware lifecycle.
            threading.Thread(target=process.wait, daemon=True).start()
        finally:
            lock.close()
    return status()


def control(data, action):
    with LOCK:
        folder = current_dir()
        if not folder or not isinstance(data, dict) or data.get('id') != folder.name:
            raise ValueError('采集任务已变更，请刷新页面。')
        if action == 'stop':
            (folder / 'stop').touch()
        elif action == 'next':
            job=status()
            if not job.get('active') or job.get('series_count',1)<=1 or job.get('state')!='recording':raise ValueError('仅批量录制中可提前结束本条并继续下一条。')
            (folder/'next').touch()
        elif action == 'heartbeat':
            (folder / 'heartbeat').touch()
        else:
            raise ValueError('无效操作')
    return status()
