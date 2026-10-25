"""Selected episodes -> independent OpenPI v2.1 dataset. LeRobot env only."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq
from dataset_export import inventory
from experiment_data import inspect

CAM='observation.images.wrist_rgb'
NUM=['action','observation.state','timestamp','frame_index','episode_index','index','task_index']


def helper():
    converter=Path(__file__).resolve().parent.parent/'openpi/scripts/b601/convert_dataset.py'
    spec=importlib.util.spec_from_file_location('converter',converter);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False))


def jsonl(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in rows))


def confined(root,relative):
    p=(root/relative).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file():raise ValueError('源文件路径无效：'+relative)
    return p


def stats(values):
    a=np.asarray(values).reshape(len(values),-1)
    if not np.all(np.isfinite(a)):raise ValueError('示范包含非有限数值。')
    return {'min':a.min(0).tolist(),'max':a.max(0).tolist(),'mean':a.mean(0).tolist(),'std':a.std(0).tolist(),'count':[len(a)]}


def export(plan,target,progress=lambda *a:None):
    converter=helper();target=Path(target)
    if target.exists():raise ValueError('输出目录已存在，拒绝覆盖。')
    if not plan['items']:raise ValueError('没有保留的示范。')
    cache={};cameras=None
    for name,s in plan['sources'].items():
        root=Path(s['root'])
        if inventory(root)!=s['inventory']:raise ValueError('源文件已变化，请重新生成导出清单：'+name)
        info=json.loads((root/'meta/info.json').read_text())
        current_cameras=sorted(k for k,v in info['features'].items() if v.get('dtype')=='video')
        if cameras is not None and current_cameras!=cameras:raise ValueError('导出源相机角色不一致。')
        cameras=current_cameras
        if s['version']=='v3.0':
            task_rows=converter.load_tasks(root)
            epstats={int(row['episode_index']):converter.episode_stats_from_row(row,set(cameras)) for _,row in converter.load_episode_metadata(root).iterrows()}
        else:
            task_rows=[json.loads(l) for l in (root/'meta/tasks.jsonl').read_text().splitlines() if l.strip()]
            epstats={r['episode_index']:r['stats'] for r in [json.loads(l) for l in (root/'meta/episodes_stats.jsonl').read_text().splitlines() if l.strip()]}
        cache[name]={'root':root,'info':info,'tasks':{r['task_index']:r['task'] for r in task_rows},'stats':epstats,
                     'data':pads.dataset([str(p) for p in sorted((root/'data').rglob('*.parquet'))],format='parquet')}
    target.mkdir(parents=True);tasks=[];episodes=[];episode_stats=[];mapping=[];offset=0
    features=copy.deepcopy(next(iter(cache.values()))['info']['features']);features={k:features[k] for k in [*NUM,*cameras]}
    for cam in cameras:
        h,w,c=features[cam]['shape']
        features[cam]['info']={'video.height':h,'video.width':w,'video.codec':'h264','video.pix_fmt':'yuv420p','video.is_depth_map':False,'video.fps':30,'video.channels':c,'has_audio':False}
    for out_ep,item in enumerate(plan['items']):
        progress(out_ep,len(plan['items']));src=cache[item['dataset']];ep=item['episode'];n=item['frames']
        table=src['data'].to_table(columns=NUM,filter=pads.field('episode_index')==ep).sort_by('frame_index')
        if len(table)!=n or table['frame_index'].to_pylist()!=list(range(n)):raise ValueError('示范帧数或帧编号不连续。')
        original=table.to_pydict();texts=[src['tasks'][int(t)] for t in original['task_index']]
        if not all(isinstance(t,str) and t.strip() for t in texts):raise ValueError('任务文本缺失。')
        mapped=[]
        for t in texts:
            if t not in tasks:tasks.append(t)
            mapped.append(tasks.index(t))
        values={**original,'index':list(range(offset,offset+n)),'episode_index':[out_ep]*n,'frame_index':list(range(n)),'timestamp':(np.arange(n)/30).tolist(),'task_index':mapped}
        # Preserve Arrow types, strip stale HuggingFace schema metadata.
        arrays=[pa.array(values[k],type=table.schema.field(k).type) for k in NUM]
        output=pa.Table.from_arrays(arrays,names=NUM)
        data_path=target/f'data/chunk-{out_ep//1000:03d}/episode_{out_ep:06d}.parquet';data_path.parent.mkdir(parents=True,exist_ok=True);pq.write_table(output,data_path)
        for key in ('action','observation.state'):
            if output[key].to_pylist()!=original[key]:raise ValueError('导出修改了关节数值。')
        for cam in cameras:
            v=next(v for v in item['videos'] if v['camera']==cam);video=confined(src['root'],v['path'])
            dst=target/f'videos/chunk-{out_ep//1000:03d}/{cam}/episode_{out_ep:06d}.mp4'
            converter.reencode_episode_lossless_h264(video,dst,round(v['start']*30),n,30)
            if converter.ffprobe_frame_count(dst)!=n:raise ValueError('导出视频帧数不匹配：'+cam)
        st={k:stats(output[k].to_pylist()) for k in NUM}
        for cam in cameras:
            if cam not in src['stats'].get(ep,{}):raise ValueError('缺少示范图像统计：'+cam)
            st[cam]={k:src['stats'][ep][cam][k] for k in ('min','max','mean','std','count')}
        episode_stats.append({'episode_index':out_ep,'stats':st});episodes.append({'episode_index':out_ep,'tasks':list(dict.fromkeys(texts)),'length':n})
        mapping.append({'output_episode':out_ep,'source_dataset':item['dataset'],'source_episode':ep,'frames':n,'tasks':list(dict.fromkeys(texts))});offset+=n
    from lerobot.datasets.compute_stats import aggregate_stats
    combined=aggregate_stats([{key:{k:np.asarray(v) for k,v in stat.items()} for key,stat in r['stats'].items()} for r in episode_stats])
    info={'codebase_version':'v2.1','robot_type':next(iter(cache.values()))['info']['robot_type'],'total_episodes':len(episodes),'total_frames':offset,'total_tasks':len(tasks),'total_videos':len(episodes)*len(cameras),'total_chunks':(len(episodes)+999)//1000,'chunks_size':1000,'fps':30,'splits':{'train':f'0:{len(episodes)}'},'data_path':'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet','video_path':'videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4','features':features}
    write(target/'meta/info.json',info);jsonl(target/'meta/tasks.jsonl',[{'task_index':i,'task':t} for i,t in enumerate(tasks)]);jsonl(target/'meta/episodes.jsonl',episodes);jsonl(target/'meta/episodes_stats.jsonl',episode_stats)
    write(target/'meta/stats.json',{k:{s:a.tolist() for s,a in v.items()} for k,v in combined.items()})
    for name,s in plan['sources'].items():
        if inventory(Path(s['root']))!=s['inventory']:raise ValueError('导出期间源文件发生变化：'+name)
    if not inspect(target)['compatible']:raise ValueError('导出格式检查失败。')
    write(target/'workbench_export.json',{'state':'completed','collection':plan['collection'],'collection_name':plan['name'],'plan_digest':plan['digest'],'replay':'unverified','episodes':mapping,'source_plan':plan})
    return info


if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text());export(request['export_plan'],Path(sys.argv[2]),lambda i,n:print(f'正在导出 {i+1}/{n} 条',flush=True))
