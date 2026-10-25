"""Whole-episode quality report; never changes acceptance or source files."""
import json
from pathlib import Path
import sys
from episode_inspection import inspect
from recording import write_json


def run(folder):
    request=json.loads((folder/'request.json').read_text());rows=[]
    for i,item in enumerate(request['audit_items']):
        try:
            result=inspect(Path(item['root']),item['episode'])
            row={key:result.get(key) for key in ('dataset','episode','frames','duration_s','status','checks','capture_quality','motion')}
        except Exception as exc:
            row={'dataset':Path(item['root']).name,'episode':item['episode'],'status':'error','error':str(exc)}
        rows.append(row)
        write_json(folder/'progress.json',{'phase':'检查示范','step':i+1,'total':len(request['audit_items'])})
    report={'name':request['audit_name'],'rows':rows,'summary':{k:sum(row['status']==k for row in rows) for k in ('ok','warning','error')},
            'note':'静态与结构检查仅用于筛选候选，不自动判断任务成功，不修改源数据或保留标记。'}
    write_json(folder/'quality_report.json',report)
    return report


if __name__=='__main__':run(Path(sys.argv[1]))
