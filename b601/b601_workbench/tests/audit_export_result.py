"""Read-only audit of a completed real-data export; no source or review writes."""
import json
from pathlib import Path
import subprocess
import sys
import pyarrow.dataset as pads
import pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dataset_export import inventory
from export_worker import helper
root=Path(sys.argv[1]);meta=json.loads((root/'workbench_export.json').read_text());plan=meta['source_plan'];converter=helper()
tasks={r['task_index']:r['task'] for r in [json.loads(l) for l in (root/'meta/tasks.jsonl').read_text().splitlines()]}
offset=0
for i,item in enumerate(plan['items']):
    source=Path(plan['sources'][item['dataset']]['root'])
    assert inventory(source)==plan['sources'][item['dataset']]['inventory']
    original=pads.dataset([str(p) for p in (source/'data').rglob('*.parquet')],format='parquet').to_table(filter=pads.field('episode_index')==item['episode']).sort_by('frame_index').to_pydict()
    output=pq.read_table(root/f'data/chunk-{i//1000:03d}/episode_{i:06d}.parquet').to_pydict()
    for k in ('action','observation.state','frame_index'):assert original[k]==output[k],k
    assert output['index']==list(range(offset,offset+item['frames']))
    assert output['episode_index']==[i]*item['frames']
    source_tasks=converter.load_tasks(source) if plan['sources'][item['dataset']]['version']=='v3.0' else [json.loads(l) for l in (source/'meta/tasks.jsonl').read_text().splitlines()]
    source_tasks={r['task_index']:r['task'] for r in source_tasks}
    assert [source_tasks[t] for t in original['task_index']]==[tasks[t] for t in output['task_index']]
    v=next(v for v in item['videos'] if v['camera']=='observation.images.wrist_rgb');first=round(v['start']*30);last=first+item['frames']-1
    def pixels(path,start,end):
        result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vf',f'select=eq(n\\,{start})+eq(n\\,{end})','-vsync','0','-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,check=True)
        assert len(result.stdout)==2*640*480*3
        return result.stdout
    assert pixels(source/v['path'],first,last)==pixels(root/f'videos/chunk-{i//1000:03d}/observation.images.wrist_rgb/episode_{i:06d}.mp4',0,item['frames']-1)
    offset+=item['frames']
print(json.dumps({'episodes':len(plan['items']),'frames':offset,'tasks':len(tasks),'all_joint_values':'identical','all_task_texts':'identical','video_boundary_pixels':'identical','sources':'unchanged'}))
