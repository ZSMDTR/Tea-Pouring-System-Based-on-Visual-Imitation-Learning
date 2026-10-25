"""Freeze reviewed collection membership; no ML imports or source writes."""
import hashlib
import json
from pathlib import Path
import experiment_data


def inventory(root):
    root=Path(root).resolve();result=[]
    for file in sorted(root.rglob('*')):
        if file.is_symlink():raise ValueError('导出源中存在符号链接，暂不支持：'+str(file))
        if file.is_file():
            st=file.stat();result.append([str(file.relative_to(root)),st.st_size,st.st_mtime_ns,st.st_ino])
    return result


def plan(snapshot,ident,describe):
    group=next((g for g in snapshot['collections'] if g['id']==ident),None)
    if group is None:raise ValueError('请选择已整理的数据集。')
    all_members=[m for m in snapshot['members'] if m['collection']==ident]
    selected=sorted((m for m in all_members if m['review']=='keep' and not m['trashed']),key=lambda m:(m['dataset'],m['episode']))
    sources={};items=[];signature=None;camera=None
    for m in selected:
        if m['dataset'] not in sources:
            detail=describe(m['dataset'],m['episode']);root=Path(detail['root'])
            report=experiment_data.inspect(root)
            if not report['compatible']:raise ValueError(m['dataset']+'：'+'；'.join(report['issues']))
            info=json.loads((root/'meta/info.json').read_text())
            keys=['action','observation.state',*[k for k,v in info['features'].items() if v.get('dtype')=='video']]
            current={'robot':info.get('robot_type'),'fps':info['fps'],'features':{k:{f:info['features'][k].get(f) for f in ('dtype','shape','names')} for k in keys}}
            if signature is not None and signature!=current:raise ValueError('所选示范的相机或关节格式不一致。')
            signature=current
            known={k:detail['signature'].get(k) for k in ('camera','secondary_camera')}
            if any(v is not None for v in known.values()):
                if camera is not None and camera!=known:raise ValueError('所选示范的相机采集配置不一致。')
                camera=known
            sources[m['dataset']]={'root':str(root),'version':report['version'],'inventory':inventory(root),'metadata_sha256':detail['metadata_sha256']}
        detail=describe(m['dataset'],m['episode']);ep=detail['episode']
        items.append({'dataset':m['dataset'],'episode':m['episode'],'frames':ep['frames'],'tasks':ep['tasks'],'videos':ep['videos']})
    result={'collection':ident,'name':group['name'],'sources':sources,'items':items,'episodes':len(items),'frames':sum(i['frames'] for i in items),
            'excluded':len(all_members)-len(items),'format':'v2.1','replay':'unverified',
            'warnings':['仅导出保留的完整示范；保留标记不代表已通过 Replay。','请确认所选示范的相机视角与任务含义一致，历史数据未必有配置快照。']}
    result['digest']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return result


def mixture_plan(snapshot,ident,describe):
    mix=next((m for m in snapshot['mixtures'] if m['id']==ident),None)
    if mix is None:raise ValueError('训练组合不存在。')
    current={(m['dataset'],m['episode']):m for m in snapshot['members']}
    selected=[]
    for source in mix['sources']:
        for frozen in source['episodes']:
            member=current.get((frozen['dataset'],frozen['episode']))
            if not member or member['trashed'] or member['review']!='keep':raise ValueError('组合中有示范已删除或不再保留，请重新整理组合。')
            detail=describe(member['dataset'],member['episode'])
            if detail['metadata_sha256']!=frozen['source']['metadata_sha256']:raise ValueError('组合的源文件已变化，请重建组合。')
            selected.append({**member,'collection':ident})
    result=plan({'collections':[{'id':ident,'name':mix['name']}],'members':selected},ident,describe)
    result['mixture']=ident
    result['sampling']='natural_frames'
    result['warnings'].append('合并导出按原始帧数训练，不应用草稿中的采样权重。加权加载器仍待接入。')
    result['digest']=hashlib.sha256(json.dumps({k:v for k,v in result.items() if k!='digest'},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return result
