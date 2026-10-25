"""Local inference controls. No hardware or ML imports in the web process."""
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
import recording
import camera_contract
from rtc.config import validate as validate_rtc
from inference_limits import MAX_COMMAND_HZ, LEGACY_MAX_COMMAND_HZ
from working_pose import selection as startup_selection, VERSION as WORKING_POSE_VERSION

ROOT=Path(__file__).resolve().parent
JOBS=ROOT/'.runtime/policies'
DEFAULT='b601_pi05_fullft_10k_v2crop_finalonly_20260901_231516/9999'
PROMPT='Pick up the red-handled screwdriver and place it in the orange container.'
RUNNING={'queued','connecting','positioning','executing','returning'}


def choices(config):
    root=Path(config['checkpoint_root']).resolve();result=[]
    for marker in root.glob('*/*/FINAL_PARAMS_ONLY.txt'):
        p=marker.parent
        if p.resolve()!=p or not (p/'params').is_dir():continue
        norms=list((p/'assets').glob('local/*/norm_stats.json'))
        if len(norms)!=1:continue
        ident=str(p.relative_to(root))
        try:inputs=camera_contract.model(p)
        except (ValueError,OSError):continue
        result.append({'id':ident,'label':('推荐 · 已有 10K 模型' if ident==DEFAULT else p.parent.name+' / '+p.name)+' · '+camera_contract.label(inputs),'norm':str(norms[0]),'path':str(p),'camera_contract':inputs})
    return sorted(result,key=lambda x:(x['id']!=DEFAULT,x['id']))


def model_folder():
    try:
        ident=(JOBS/'current').read_text().strip()
        if len(ident)==32 and all(c in '0123456789abcdef' for c in ident):return JOBS/ident
    except OSError:pass


def busy(lock_path):
    if not lock_path.parent.exists():return False
    with lock_path.open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
        except BlockingIOError:return True


def model_status():
    folder=model_folder()
    if not folder:return {'active':False,'state':'off','message':'尚未加载模型'}
    state=json.loads((folder/'status.json').read_text());state['active']=busy(JOBS/'model.lock')
    if not state['active'] and state['state'] not in ('off','error'):
        state.update(state='off',message='模型进程已退出，可重新加载。')
    log=folder/'worker.log'
    if log.exists():
        with log.open('rb') as f:f.seek(max(0,log.stat().st_size-6000));state['log']=f.read().decode(errors='replace')
    return state


def session():
    state=recording.status()
    if state.get('mode')!='inference':return {'active':False,'state':'off','message':'相机预览未启动'}
    # Camera updates remain visible while a policy call blocks the control loop.
    folder=recording.current_dir()
    if state.get('active') and state.get('preview_stream') and folder:
        try:
            metadata=json.loads((folder/'preview_meta.json').read_text())
            if metadata.get('id')==state.get('id'):
                for key in ('image_at','preview_fps','preview_frames','preview_error','preview_target_fps'):
                    if key in metadata:state[key]=metadata[key]
        except (OSError,ValueError):pass
        if 'head_rgb' in state.get('camera_views',[]):
            try:state['head_preview']=json.loads((folder/'head/preview_meta.json').read_text())
            except (OSError,ValueError):pass
    return state


def status(config):
    s=session();folder=recording.current_dir();history=[]
    if folder and s.get('id')==folder.name:
        history=[json.loads(p.read_text()) for p in sorted(folder.glob('result_*.json'))][-20:]
    return {'model':model_status(),'session':s,'history':history,'models':choices(config),'default_prompt':PROMPT,
            'limits':{'command_hz':MAX_COMMAND_HZ,'native_command_hz':MAX_COMMAND_HZ,'timed_command_hz':MAX_COMMAND_HZ,'legacy_command_hz':LEGACY_MAX_COMMAND_HZ,'working_pose_version':WORKING_POSE_VERSION}}


def start_model(data,config):
    import experiments
    with experiments.LOCK,recording.LOCK:
        if experiments.status().get('active'):raise ValueError('请等待训练或数据准备任务结束。')
        if model_status().get('active'):raise ValueError('模型已经加载或正在加载，请先卸载。')
        selected=next((m for m in choices(config) if m['id']==data.get('model')),None)
        if not selected:raise ValueError('请选择已有最终参数模型。')
        apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader'],text=True,timeout=5).strip()
        if apps:raise ValueError('显卡正被其他进程使用，请先结束相应任务。')
        JOBS.mkdir(parents=True,exist_ok=True)
        with (JOBS/'model.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('模型任务正运行。')
            ident=uuid.uuid4().hex;folder=JOBS/ident;folder.mkdir()
            req={'id':ident,**selected};req['id']=ident
            recording.write_json(folder/'request.json',req)
            recording.write_json(folder/'status.json',{'id':ident,'state':'loading','message':'正在加载已有模型并预热，首次需要一些时间…','model':selected['id']})
            env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',XLA_PYTHON_CLIENT_PREALLOCATE='false',XLA_PYTHON_CLIENT_MEM_FRACTION='0.5',WANDB_MODE='disabled')
            with (folder/'worker.log').open('ab') as log:
                proc=subprocess.Popen([config['openpi_python'],'-B',str(ROOT/'inference_policy.py'),str(folder)],env=env,cwd=ROOT.parent/'openpi',stdout=log,stderr=log,stdin=subprocess.DEVNULL,pass_fds=(lock.fileno(),),start_new_session=True)
            recording.write_json(folder/'process.json',{'pid':proc.pid})
            (JOBS/'current').write_text(ident);threading.Thread(target=proc.wait,daemon=True).start()
    return model_status()


def start_preview(config):
    with recording.LOCK:
        recording.JOBS.mkdir(parents=True,exist_ok=True)
        with (recording.JOBS/'device.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('设备被采集或机械臂模式占用，请先正常结束。')
            ident=uuid.uuid4().hex;folder=recording.JOBS/ident;folder.mkdir()
            req={'id':ident,'mode':'inference','profile':json.loads((ROOT/'capture_profile.json').read_text())}
            recording.write_json(folder/'request.json',req)
            recording.write_json(folder/'status.json',{**req,'state':'starting','message':'正在启动相机；此步骤不连接电机。'})
            (folder/'heartbeat').touch()
            with (folder/'worker.log').open('ab') as log:
                proc=subprocess.Popen([config['lerobot_python'],'-B',str(ROOT/'inference_worker.py'),str(folder),str(lock.fileno())],stdout=log,stderr=log,stdin=subprocess.DEVNULL,pass_fds=(lock.fileno(),),start_new_session=True)
            recording.write_json(folder/'process.json',{'pid':proc.pid});(recording.JOBS/'current').write_text(ident)
            threading.Thread(target=proc.wait,daemon=True).start()
    return session()


def validate_task(data):
    prompt=data.get('prompt')
    if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=500:raise ValueError('请输入 1–500 字的实际任务指令。')
    if data.get('prepared') is not True:raise ValueError('请确认从臂处于现有水平启动姿态、回零路径畅通、没有其他控制程序。')
    continuous=data.get('continuous',False)
    if type(continuous) is not bool:raise ValueError('持续执行选项必须为布尔值。')
    mode=data.get('timing_mode','legacy')
    if mode not in ('legacy','native','timed'):raise ValueError('请选择有效的动作执行方式。')
    command_max=LEGACY_MAX_COMMAND_HZ if mode=='legacy' else MAX_COMMAND_HZ
    values={'continuous':continuous,'rtc':validate_rtc(data.get('rtc')),
            'startup_pose':startup_selection(data.get('startup_pose','horizontal'))}
    for key,low,high,default in [('seconds',1,120,30),('hz',1,command_max,30),('chunk',1,50,10),('relative_limit',1,45,5),('gripper_relative_limit',1,150,data.get('relative_limit',5))]:
        v=data.get(key,default)
        if type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high or (key=='chunk' and int(v)!=v):raise ValueError(f'{key} 必须在 {low}–{high} 范围内。')
        values[key]=int(v) if key=='chunk' else float(v)
    # Without interpolation each command consumes exactly one model action.
    # A disabled interpolation field must not silently impose a second timebase.
    policy_hz=data.get('policy_hz',30) if mode=='timed' else values['hz']
    if mode=='timed' and (type(policy_hz) not in (int,float) or not math.isfinite(policy_hz) or not 1<=policy_hz<=50):
        raise ValueError('模型动作频率必须在 1–50 Hz 范围内。')
    if mode=='timed' and values['hz']<policy_hz:
        raise ValueError('独立下发频率不能低于模型动作频率；当前模型建议保持 30 Hz 动作节奏。')
    values.update(timing_mode=mode,interpolation=mode=='timed',policy_hz=float(policy_hz))
    return {'prompt':prompt.strip(),**values}


def action(name,data,config):
    if name=='load':return start_model(data,config)
    if name=='preview':return start_preview(config)
    with recording.LOCK:
        s=session();folder=recording.current_dir()
        if name=='unload':
            if s.get('active') and (s['state'] in RUNNING or (folder/'command.json').exists()):raise ValueError('请先结束当前动作。')
            model=model_status()
            if data.get('model_id')!=model.get('id'):raise ValueError('模型任务已变化，请刷新。')
            if model.get('active'):(model_folder()/'stop').touch()
            return model_status()
        if not s.get('active') or data.get('id')!=s.get('id'):raise ValueError('推理会话已变化，请重新启动预览。')
        if name=='execute':
            import can_control
            task=validate_task(data)
            if task['startup_pose']!='horizontal' and (not s.get('supports_working_pose') or s.get('working_pose_version',0)<WORKING_POSE_VERSION):
                raise ValueError('当前预览尚未支持工作待命位，请关闭预览后重新启动；模型无需重载。')
            if task['timing_mode']=='native' and not s.get('supports_native_actions'):
                raise ValueError('当前预览尚未启用原生动作执行，请关闭预览后重新启动。')
            if task['timing_mode']=='timed' and not s.get('supports_timed_actions'):
                raise ValueError('当前预览使用旧执行程序，请关闭预览后重新启动，以启用独立下发与插值。')
            if task['hz']>LEGACY_MAX_COMMAND_HZ and task['hz']>s.get('max_command_hz',LEGACY_MAX_COMMAND_HZ):
                raise ValueError('当前预览尚未启用高频下发适配，请关闭预览后重新启动。')
            if task['continuous'] and not s.get('supports_continuous'):raise ValueError('当前预览使用旧版本，请关闭预览后重新启动，再开启持续执行。')
            if 'gripper_relative_limit' in data and not s.get('supports_separate_limits'):raise ValueError('请关闭旧相机预览并重新启动，以启用夹爪独立限幅。')
            if s['state']!='preview' or (folder/'command.json').exists():raise ValueError('请等上一条任务退出完成。')
            if not can_control.ready(can_control.read_state()):raise ValueError('请先在工作概览启动 CAN。')
            model=model_status()
            if not model.get('active') or model['state']!='ready':raise ValueError('请先加载模型，等待预热完成。')
            if task['rtc']['enabled'] and (not model.get('rtc_ready') or not s.get('supports_rtc')):raise ValueError('RTC 需要重新加载模型并重开预览，等待 RTC 预热完成。')
            inputs=camera_contract.validate(model.get('camera_contract',camera_contract.contract()))
            if not set(inputs['views']).issubset(s.get('camera_views',['wrist_rgb'])):
                raise ValueError('此模型需要双视角。请启用第三视角，关闭并重新打开预览后再执行。')
            task.update(task_id=uuid.uuid4().hex,model_id=model['id'],port=model['port'],checkpoint=model['model'],camera_contract=inputs)
            recording.write_json(folder/'command.json',task)
        elif name in ('finish','stop_task'):
            if s['state'] not in RUNNING or data.get('task_id')!=s.get('task_id'):raise ValueError('当前没有对应的执行任务。')
            if name=='finish' and (s['state']!='executing' or not s.get('steps')):raise ValueError('请等动作执行后再确认成功。')
            recording.write_json(folder/'end.json',{'task_id':s['task_id'],'reason':'success' if name=='finish' else 'stopped'})
        elif name=='close':
            if s['state'] in RUNNING or (folder/'command.json').exists():raise ValueError('请先结束并回零，再关闭预览。')
            (folder/'stop').touch()
        else:raise ValueError('未知推理操作。')
    return session()
