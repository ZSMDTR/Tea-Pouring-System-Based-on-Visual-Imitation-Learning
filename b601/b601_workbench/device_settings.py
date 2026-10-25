"""Validated device bindings; save only while the shared device lock is free."""
import copy
import fcntl
import json
import re
from pathlib import Path
import time
import subprocess
import threading
import uuid
from recording import write_json

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT/'capture_profile.json'


def validate(data, existing, ports, cameras):
    if not isinstance(data, dict) or set(data) - {'leader_port', 'camera', 'secondary_enabled', 'secondary_camera'}:
        raise ValueError('设备配置参数无效。')
    result = copy.deepcopy(existing)
    if 'leader_port' in data:
        port = data['leader_port']
        if port not in ports or not re.fullmatch(r'/dev/(tty(?:USB|ACM)\d+|serial/by-id/[^/]+)', port):
            raise ValueError('请选择已检测到的引导臂端口。')
        result['leader_port'] = port
    enabled = data.get('secondary_enabled', existing.get('secondary_enabled', False))
    if type(enabled) is not bool:
        raise ValueError('第三视角开关无效。')
    for key in ('camera', 'secondary_camera'):
        if key not in data:
            continue
        cfg = data[key]
        if not isinstance(cfg, dict) or set(cfg) - {'serial_number_or_name', 'width', 'height', 'fps'}:
            raise ValueError('相机参数无效。')
        serial = cfg.get('serial_number_or_name')
        if not isinstance(serial, str) or not serial or serial not in {c['serial'] for c in cameras if c['serial']}:
            raise ValueError('请选择已识别的相机序列号。')
        width, height, fps = cfg.get('width'), cfg.get('height'), cfg.get('fps')
        if type(width) is not int or type(height) is not int or not 160 <= width <= 3840 or not 120 <= height <= 2160 or width % 2 or height % 2:
            raise ValueError('相机分辨率无效。')
        if type(fps) is not int or fps != existing['fps']:
            raise ValueError('当前数据和训练链路使用 30 FPS，请保持相机与采集帧率一致。')
        result[key] = {**existing.get(key, {}), **cfg, 'output_width': 640, 'output_height': 480, 'resize_mode': 'letterbox', 'warmup_s': 1}
    result['secondary_enabled'] = enabled
    if enabled and (not result.get('secondary_camera') or result['secondary_camera']['serial_number_or_name'] == result['camera']['serial_number_or_name']):
        raise ValueError('腕部与第三视角必须选择不同的相机。')
    return result


def save(data, overview):
    current = json.loads(PROFILE.read_text())
    validated = validate(data, current, overview['ports'], overview['cameras'])
    folder = ROOT/'.runtime/config_history'
    folder.mkdir(parents=True, exist_ok=True)
    history = folder/f'capture_{time.time_ns()}.json'
    write_json(history, current)
    write_json(PROFILE, validated)
    return {'ok': True, 'message': '配置已保存；请打开双相机预览确认两路画面。新双视角数据会使用两路输入训练；旧模型仍按其原有单视角运行。', 'backup': str(history)}


def start_preview(data, config):
    import recording
    role=data.get('role')
    if role not in ('wrist', 'head', 'both'):
        raise ValueError('请选择腕部或第三视角。')
    profile=json.loads(PROFILE.read_text())
    camera=profile.get('secondary_camera' if role=='head' else 'camera')
    if not camera:
        raise ValueError('请先保存这个视角的相机配置。')
    if role=='both' and (not profile.get('secondary_enabled') or not profile.get('secondary_camera')):
        raise ValueError('请先配置并启用第三视角。')
    with recording.LOCK:
        recording.JOBS.mkdir(parents=True,exist_ok=True)
        with (recording.JOBS/'device.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('设备或相机正被其他任务占用，请先正常结束。')
            ident=uuid.uuid4().hex;folder=recording.JOBS/ident;folder.mkdir()
            request={'id':ident,'mode':'camera_preview','role':role,'camera':camera}
            if role=='both':request['secondary_camera']=profile['secondary_camera']
            write_json(folder/'request.json',request)
            write_json(folder/'status.json',{**request,'state':'starting','message':'正在打开所选相机；不连接电机。'})
            (folder/'heartbeat').touch()
            with (folder/'worker.log').open('ab') as log:
                proc=subprocess.Popen([config['lerobot_python'],'-B',str(ROOT/'camera_preview_worker.py'),str(folder),str(lock.fileno())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,pass_fds=(lock.fileno(),),start_new_session=True)
            write_json(folder/'process.json',{'pid':proc.pid})
            (recording.JOBS/'current').write_text(ident)
            threading.Thread(target=proc.wait,daemon=True).start()
    return recording.status()
