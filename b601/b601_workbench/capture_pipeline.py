"""Optional batch capture and conversion around the existing single-episode worker.

Every episode uses its existing return-and-disable cleanup. A shared lock and
heartbeat cover the whole series. No new robot commands are introduced here.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import traceback
import experiments
import library
from recording import ROOT,write_json


def run(folder,lock_fd,request,worker=None):
    config=request['pipeline_config'];worker=worker or ROOT/'record_worker.py'
    count=request.get('series_count',1);completed=[];converted=[];last={**request};last.pop('pipeline_config',None)
    def stopped():return (folder/'stop').exists() or time.time()-(folder/'heartbeat').stat().st_mtime>15
    def update(state,message,**extra):
        last.update(state=state,message=message,**extra);write_json(folder/'status.json',last)
    def signal_stop(*_):(folder/'stop').touch()
    signal.signal(signal.SIGINT,signal_stop);signal.signal(signal.SIGTERM,signal_stop)
    try:
        for index in range(count):
            if stopped():break
            name=request['dataset'] if index==0 else request['dataset']+f'_s{index+1:03d}'
            child=folder/'episodes'/f'{index+1:03d}';child.mkdir(parents=True)
            child_request={k:v for k,v in request.items() if k not in ('pipeline_config','auto_convert')}
            child_request.update(dataset=name,root=str(Path(config['dataset_root'])/name))
            write_json(child/'request.json',child_request)
            (child/'heartbeat').symlink_to(folder/'heartbeat')
            if index:library.add_members(request['collection'],name,[{'index':0,'tasks':[request['task']],'frames':0}])
            preview=folder/'preview.jpg'
            if preview.exists() or preview.is_symlink():preview.unlink()
            preview.symlink_to(child/'preview.jpg')
            last.update(dataset=name,root=child_request['root'],series_index=index+1,frames=0,elapsed=0,saved=False)
            with (child/'worker.log').open('ab') as log:
                with subprocess.Popen([config['lerobot_python'],'-B',str(worker),str(child),'--lock-fd',str(lock_fd)],pass_fds=(lock_fd,),stdout=log,stderr=log,stdin=subprocess.DEVNULL) as proc:
                    while proc.poll() is None:
                        if stopped() or (folder/'next').exists():(child/'stop').touch()
                        try:
                            status=json.loads((child/'status.json').read_text());last.update({k:v for k,v in status.items() if k!='pipeline_config'})
                            last.update(series_index=index+1,series_count=count,completed_datasets=list(completed));write_json(folder/'status.json',last)
                        except (OSError,ValueError):pass
                        time.sleep(.1)
            if (folder/'next').exists():(folder/'next').unlink()
            result=json.loads((child/'status.json').read_text());last.update(result)
            if proc.returncode or result.get('state')!='completed' or not result.get('saved'):
                raise RuntimeError(result.get('message') or '本条采集未完成，后续自动录制已停止。')
            completed.append(name)
            if index<count-1 and not stopped():
                deadline=time.monotonic()+request.get('rest_seconds',10)
                while not stopped() and time.monotonic()<deadline:
                    update('resting',f'第 {index+1}/{count} 条已保存并回位；约 {max(0,int(deadline-time.monotonic()))} 秒后开始下一条。',completed_datasets=list(completed))
                    time.sleep(.1)
        # A normal manual stop ends capture, then still runs the selected
        # conversion. A lost heartbeat never starts additional background work.
        heartbeat_ok=time.time()-(folder/'heartbeat').stat().st_mtime<=15
        if request.get('auto_convert') and completed and heartbeat_ok:
            if (folder/'stop').exists():(folder/'stop').unlink()
            for name in completed:
                if stopped():break
                update('processing',f'设备已回位，正在转换 {name}；不会自动标记 Replay 通过。')
                job=experiments.start('convert',{},config,Path(config['dataset_root'])/name)
                while experiments.status().get('active'):
                    if stopped():
                        try:experiments.cancel({'id':job['id']})
                        except ValueError:pass
                    time.sleep(.25)
                result=experiments.status()
                if result.get('state')!='completed':raise RuntimeError('采集已保存；转换未完成：'+result.get('message','请检查转换任务日志。'))
                converted.append(result['dataset_output'])
        update('completed',f'已保存 {len(completed)}/{count} 条示范'+(f'，已转换 {len(converted)} 条' if request.get('auto_convert') else '')+'；均待 Replay 验收。',
               completed_datasets=completed,converted_datasets=converted,saved=bool(completed))
    except BaseException as exc:
        traceback.print_exc();update('error',str(exc) or type(exc).__name__,completed_datasets=completed,converted_datasets=converted)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);parser.add_argument('--lock-fd',type=int,required=True);args=parser.parse_args();os.fstat(args.lock_fd)
    run(args.folder,args.lock_fd,json.loads((args.folder/'request.json').read_text()))
