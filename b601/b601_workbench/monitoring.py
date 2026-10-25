"""Bounded, read-only training curves, history and host resource samples."""
import json
import math
from pathlib import Path
import re
import subprocess
import threading
import time
import experiments

RESOURCE_LOCK=threading.Lock()
RESOURCE_CACHE=None


def tail(path, limit=2_000_000):
    path=Path(path)
    if not path.is_file():return ''
    with path.open('rb') as f:
        size=path.stat().st_size;f.seek(max(0,size-limit));value=f.read(limit).decode(errors='replace')
    return value.split('\n',1)[-1] if size>limit else value


def curves(folder):
    folder=Path(folder);rows=[]
    if (folder/'metrics.jsonl').is_file():
        for line in tail(folder/'metrics.jsonl').splitlines():
            try:
                row=json.loads(line)
                if type(row.get('step')) is not int:continue
                row={k:v for k,v in row.items() if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)}
                if 'step' in row:rows.append(row)
            except (ValueError,TypeError):continue
    else:
        for match in re.finditer(r'Step\s+(\d+):([^\r\n]*)',tail(folder/'worker.log')):
            row={'step':int(match[1])}
            for key,value in re.findall(r'([a-zA-Z_][\w/]*)\s*[:=]\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)',match[2]):
                number=float(value)
                if math.isfinite(number):row[key]=number
            if len(row)>1:rows.append(row)
    if len(rows)>600:
        rows=[rows[round(i*(len(rows)-1)/599)] for i in range(600)]
    memory=[]
    for line in tail(folder/'memory.jsonl',200_000).splitlines():
        try:
            value=json.loads(line)
            memory.append({k:v for k,v in value.items() if type(v) in (int,float) and math.isfinite(v)})
        except ValueError:continue
    return {'points':rows,'keys':sorted({key for row in rows for key in row if key!='step'}),'memory':memory[-300:]}


def folder(ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('任务编号无效。')
    path=experiments.JOBS/ident
    if path.is_symlink() or not (path/'request.json').is_file():raise ValueError('任务不存在。')
    return path


def history(project='default'):
    current=experiments.current();rows=[]
    live=experiments.status()
    for request in sorted(experiments.JOBS.glob('*/request.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:250]:
        if not re.fullmatch('[0-9a-f]{32}',request.parent.name) or request.parent.is_symlink():continue
        try:
            r=json.loads(request.read_text())
            if r.get('project','default')!=project:continue
            s=live if current==request.parent else json.loads((request.parent/'status.json').read_text())
            row={key:r.get(key) for key in ('id','kind','dataset','steps','batch','output','save_mode','run_name','resume_from')}
            row['label']=r.get('audit_name') or r.get('export_plan',{}).get('name') or r.get('dataset')
            row.update(state=s.get('state'),message=s.get('message'),active=bool(current==request.parent and live.get('active')),
                       created=request.stat().st_mtime,dataset_output=s.get('dataset_output'))
            if not row['active'] and row['state'] not in ('completed','cancelled','error'):row.update(state='error',message='任务进程已退出。')
            rows.append(row)
        except (ValueError,OSError):continue
    return rows


def detail(ident):
    path=folder(ident);request=json.loads((path/'request.json').read_text())
    state=experiments.status() if experiments.current()==path else json.loads((path/'status.json').read_text())
    state={k:v for k,v in state.items() if k not in ('config','export_plan','report','log')}
    return {'id':ident,'state':state,'curves':curves(path),'log':tail(path/'worker.log',24_000),
            'request':{**{k:request.get(k) for k in ('kind','dataset','steps','batch','save_mode','save_interval','output','resume_from','project')},
                       'label':request.get('audit_name') or request.get('export_plan',{}).get('name') or request.get('dataset')}}


def resources():
    global RESOURCE_CACHE
    with RESOURCE_LOCK:
        if RESOURCE_CACHE and time.time()-RESOURCE_CACHE['sampled_at']<2:return RESOURCE_CACHE
        memory={}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key,value=line.split(':',1)
            if key in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):memory[key]=int(value.split()[0])*1024
        gpu=[];error=None
        try:
            data=subprocess.run(['nvidia-smi','--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,check=True)
            for line in data.stdout.splitlines():
                name,util,used,total,temp=[v.strip() for v in line.split(',')]
                gpu.append({'name':name,'utilization':float(util),'used_mib':float(used),'total_mib':float(total),'temperature_c':float(temp)})
        except (OSError,ValueError,subprocess.SubprocessError):error='GPU 监视暂时不可用。'
        RESOURCE_CACHE={'sampled_at':time.time(),'memory':memory,'gpu':gpu,'gpu_error':error}
        return RESOURCE_CACHE
