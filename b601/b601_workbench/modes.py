"""Standalone device sessions sharing recording's process lock and heartbeat."""
import fcntl
import json
from pathlib import Path
import subprocess
import threading
import uuid
import recording
import can_control

ROOT=Path(__file__).resolve().parent
POSE=ROOT/'data/candidate_pose.json'


def start(data,config):
    mode=data.get('mode')
    if mode not in ('teleop','gravity') or data.get('prepared') is not True:
        raise ValueError('请选择模式并确认设备准备条件。')
    if not can_control.ready(can_control.read_state()):raise ValueError('请先启动 CAN。')
    profile=json.loads((ROOT/'capture_profile.json').read_text())
    if mode=='teleop' and not Path(profile['leader_port']).exists():raise ValueError('未找到配置的引导臂串口。')
    with recording.LOCK:
        recording.JOBS.mkdir(parents=True,exist_ok=True)
        with (recording.JOBS/'device.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('设备正被采集或其他模式占用，请先正常结束。')
            ident=uuid.uuid4().hex;folder=recording.JOBS/ident;folder.mkdir()
            request={'id':ident,'mode':mode}
            recording.write_json(folder/'request.json',request)
            recording.write_json(folder/'status.json',{**request,'state':'starting','message':'正在加载机械臂环境…'})
            (folder/'heartbeat').touch()
            with (folder/'worker.log').open('ab') as log:
                proc=subprocess.Popen([config['lerobot_python'],'-B',str(ROOT/'mode_worker.py'),str(folder),'--lock-fd',str(lock.fileno())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,pass_fds=(lock.fileno(),),start_new_session=True)
            recording.write_json(folder/'process.json',{'pid':proc.pid})
            (recording.JOBS/'current').write_text(ident)
            threading.Thread(target=proc.wait,daemon=True).start()
    return recording.status()


def save(data):
    with recording.LOCK:
        job=recording.status();folder=recording.current_dir()
        if job.get('id')!=data.get('id') or not job.get('active') or job.get('mode')!='gravity' or job.get('state')!='gravity' or job.get('lock_state')!='LOCKED':raise ValueError('请等官方调姿显示姿态已锁定后再保存。')
        (folder/'save_pose').touch()
    return {'message':'已请求保存；请保持姿态静止，等待保存结果。'}


def pose():
    return json.loads(POSE.read_text()) if POSE.is_file() else None
