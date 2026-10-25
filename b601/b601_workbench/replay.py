"""Replay process management and human acceptance. No hardware imports."""
import fcntl
import json
from pathlib import Path
import subprocess
import threading
import uuid
import can_control
import library
import recording
from replay_data import source_identity

ROOT=Path(__file__).resolve().parent


def plan(root,episode,config):
    result=subprocess.run([config['lerobot_python'],'-B',str(ROOT/'replay_worker.py'),'--plan',str(root),str(episode)],
                          capture_output=True,text=True,timeout=45)
    if result.returncode:
        lines=[line.strip() for line in result.stderr.splitlines() if line.strip()]
        raise ValueError('Replay 检查未通过：'+(lines[-1][:600] if lines else '读取环境退出，请查看日志。'))
    return json.loads(result.stdout)


def start(root,data,config):
    if data.get('prepared') is not True:raise ValueError('请先复原物体并确认从臂、抬臂与回零路径已准备好。')
    checked=plan(root,data['episode'],config)
    if data.get('digest')!=checked['digest']:raise ValueError('数据或配置已变化，请重新预览 Replay。')
    if checked['legacy'] and data.get('legacy_confirmed') is not True:raise ValueError('请确认历史示范的来源、标定和动作语义。')
    if not can_control.ready(can_control.read_state()):raise ValueError('请先在工作概览启动 CAN。')
    # Caller holds the same recording/recycle/experiment locks as data mutations.
    recording.JOBS.mkdir(parents=True,exist_ok=True)
    with (recording.JOBS/'device.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('设备被占用，请先结束采集、机械臂模式或推理相机预览。')
        ident=uuid.uuid4().hex;folder=recording.JOBS/ident;folder.mkdir()
        member=next((m for m in library.snapshot()['members'] if m['dataset']==root.name and m['episode']==data['episode']),{})
        request={'id':ident,'mode':'replay','root':str(root),'dataset':root.name,'episode':data['episode'],
                 'collection':member.get('collection'),'plan':checked}
        recording.write_json(folder/'request.json',request)
        state={**request,'state':'starting','message':'正在加载 Replay 环境…','frames':0,'total_frames':checked['frames'],'elapsed':0,'acceptance':'pending'}
        recording.write_json(folder/'status.json',state);(folder/'heartbeat').touch()
        library.begin_replay(ident,root.name,data['episode'],checked['source_identity'])
        try:
            with (folder/'worker.log').open('ab') as log:
                proc=subprocess.Popen([config['lerobot_python'],'-B',str(ROOT/'replay_worker.py'),str(folder),'--lock-fd',str(lock.fileno())],
                    stdin=subprocess.DEVNULL,stdout=log,stderr=log,pass_fds=(lock.fileno(),),start_new_session=True)
            recording.write_json(folder/'process.json',{'pid':proc.pid})
            (recording.JOBS/'current').write_text(ident)
            threading.Thread(target=proc.wait,daemon=True).start()
        except BaseException:
            recording.write_json(folder/'status.json',{**state,'state':'error','message':'Replay 进程未能启动。'})
            raise
    return recording.status()


def summaries():
    results={}
    current=recording.status()
    for row in library.replay_attempts():
        key=(row['dataset'],row['episode'])
        if key in results:continue
        try:
            state=json.loads((recording.JOBS/row['id']/'status.json').read_text())
            phase=state['state']
            if phase in recording.ACTIVE and (current.get('id')!=row['id'] or not current.get('active')):phase='error'
        except (OSError,ValueError,KeyError):state={};phase='error'
        result=row['verdict'] if row['verdict']!='pending' else 'pending' if phase=='completed' else 'stopped' if phase=='stopped' else 'error' if phase=='error' else 'running'
        results[key]={**row,'status':result,'state':phase,'message':state.get('message','Replay 记录不可用。')}
    return results


def review(root,data):
    ident=data.get('id');verdict=data.get('verdict');note=data.get('note','')
    if verdict not in ('passed','failed') or not isinstance(note,str) or len(note)>2000:raise ValueError('Replay 验收参数无效。')
    row=next((r for r in library.replay_attempts() if r['id']==ident),None)
    if not row or row['dataset']!=root.name or row['episode']!=data['episode']:raise ValueError('Replay 记录与所选示范不匹配。')
    latest=summaries()[(root.name,data['episode'])]
    if latest['id']!=ident:raise ValueError('已有较新的 Replay，请验收最新一次。')
    state=json.loads((recording.JOBS/ident/'status.json').read_text())
    if state.get('state')!='completed' or state.get('frames')!=state.get('total_frames') or not state.get('return_completed'):
        raise ValueError('只有完整回放并正常回位后才能验收；中断或异常的 Replay 不能通过。')
    if source_identity(root)!=row['source_identity']:raise ValueError('源文件已变化，请重新回放。')
    library.review_replay(ident,verdict,note.strip())
    return {'id':ident,'status':verdict,'message':'已验收通过并标记保留。' if verdict=='passed' else '已标记失败，需要重录。'}
