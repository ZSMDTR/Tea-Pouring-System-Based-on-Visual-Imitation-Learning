"""One durable preparation/training job at a time, separate from hardware jobs."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
from recording import write_json
import experiment_data
import checkpoint_catalog

ROOT=Path(__file__).resolve().parent
JOBS=ROOT/'.runtime/experiments'
LOCK=threading.RLock()


def current():
    try:
        name=(JOBS/'current').read_text().strip()
        if len(name)==32 and all(c in '0123456789abcdef' for c in name):return JOBS/name
    except OSError:pass


def status():
    folder=current()
    if not folder:return {'active':False,'state':'idle','message':'尚无数据准备或训练任务。'}
    d=json.loads((folder/'status.json').read_text())
    with (JOBS/'job.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);busy=False
        except BlockingIOError:busy=True
    d['active']=busy
    if not busy and d['state'] not in ('completed','error','cancelled'):
        d.update(state='error',message='任务进程已退出，请检查日志；不自动重试或覆盖文件。')
    p=folder/'worker.log'
    if p.exists():
        with p.open('rb') as f:f.seek(max(0,p.stat().st_size-12000));d['log']=f.read().decode(errors='replace')
    progress=folder/'progress.json'
    if progress.is_file():d['progress']=json.loads(progress.read_text())
    return d


def start(kind,data,config,source):
    if kind not in ('convert','train','export','import_hf','upload_hf','audit','simulation'):raise ValueError('未知实验操作。')
    transfer=kind in ('import_hf','upload_hf')
    report={'compatible':True,'needs_conversion':False} if kind in ('export','audit','simulation') or transfer else experiment_data.inspect(source)
    if not report['compatible']:raise ValueError('；'.join(report['issues']))
    if kind=='convert' and not report['needs_conversion']:raise ValueError('已是 v2.1，无需重复转换。')
    options={}
    if transfer:
        import data_exchange
        options=data_exchange.options(kind,data,config,source)
    if kind=='train':
        if report['needs_conversion']:raise ValueError('请先转换为 v2.1。')
        if data.get('acknowledged') is not True:raise ValueError('请确认数据已人工检查，并了解所选保存方式。')
        for key,limit in [('steps',30000),('batch',32)]:
            value=data.get(key)
            if type(value) is not int or not 1<=value<=limit:raise ValueError(f'{key} 必须在 1–{limit} 范围内。')
        if data['batch']>report['frames']:raise ValueError('批大小不能大于数据帧数。')
        if not (Path(config['base_model'])/'params').is_dir():raise ValueError('本机 π0.5 基础模型参数不存在。')
        options=checkpoint_catalog.validate_options(data,config,source)
        import camera_contract
        options['camera_contract']=camera_contract.for_training(source,options.get('resume_from'))
    if kind!='upload_hf':
        import workspace_state
        workspace_state.check_space(config['checkpoint_root'] if kind=='train' else config['dataset_root'])
    with LOCK:
        if kind=='train':
            import inference
            if inference.model_status().get('active'):raise ValueError('请先在交互推理页面卸载模型，再开始训练。')
        JOBS.mkdir(parents=True,exist_ok=True)
        lock=(JOBS/'job.lock').open('a')
        try:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('已有数据准备或训练任务正在运行。')
            ident=uuid.uuid4().hex;folder=JOBS/ident;folder.mkdir()
            name='wb_'+kind+'_'+time.strftime('%Y%m%d_%H%M%S_')+ident[:8]
            request={'id':ident,'kind':kind,'dataset':source.name,'source':str(source),'report':report,
                     'output':str(Path(config['dataset_root'])/name) if kind in ('convert','export','import_hf','simulation') else str(Path(config['checkpoint_root'])/name),
                     'run_name':name,'steps':data.get('steps'),'batch':data.get('batch'),'config':config}
            request.update(options)
            import workspace_state
            request['project']=workspace_state.current()
            if kind=='export':request['export_plan']=data['export_plan']
            if kind=='audit':request.update(audit_items=data['audit_items'],audit_name=data['audit_name'])
            write_json(folder/'request.json',request)
            write_json(folder/'status.json',{**request,'config':None,'state':'starting','message':'正在准备任务…'})
            with (folder/'worker.log').open('ab') as log:
                proc=subprocess.Popen(['/usr/bin/python3','-B',str(ROOT/'experiment_worker.py'),str(folder),str(lock.fileno())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,pass_fds=(lock.fileno(),),start_new_session=True)
            (JOBS/'current').write_text(ident)
            threading.Thread(target=proc.wait,daemon=True).start()
        finally:lock.close()
    return status()


def cancel(data):
    with LOCK:
        state=status();folder=current()
        if not folder or data.get('id')!=folder.name or not state.get('active'):
            raise ValueError('该任务已经结束或发生变化，请刷新后重试。')
        (folder/'cancel').touch()
    return {'ok':True,'message':'已请求停止；已完成的存档保留，未保存的训练进度会丢失。'}
