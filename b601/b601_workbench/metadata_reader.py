"""Run only in the LeRobot environment; never import robot/camera drivers."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
info = json.loads((root / 'meta/info.json').read_text())
if info['codebase_version'].startswith('v3'):
    import pyarrow.parquet as pq
    records = []
    for p in sorted((root / 'meta/episodes').rglob('*.parquet')):
        table = pq.read_table(p)
        cols = [c for c in table.column_names if not c.startswith('stats/')]
        records.extend(table.select(cols).to_pylist())
else:
    p = root / 'meta/episodes.jsonl'
    records = [json.loads(s) for s in p.read_text().splitlines() if s.strip()] if p.exists() else []
result = []
for r in records:
    ep = int(r['episode_index'])
    videos = []
    for key, feature in info['features'].items():
        if feature.get('dtype') != 'video':
            continue
        if info['codebase_version'].startswith('v3'):
            path = info['video_path'].format(video_key=key, chunk_index=int(r[f'videos/{key}/chunk_index']), file_index=int(r[f'videos/{key}/file_index']))
            start = float(r[f'videos/{key}/from_timestamp'])
            end = float(r[f'videos/{key}/to_timestamp'])
        else:
            path = info['video_path'].format(video_key=key, episode_index=ep, episode_chunk=ep // int(info['chunks_size']))
            start, end = 0.0, int(r['length']) / float(info['fps'])
        videos.append({'camera': key, 'path': path, 'start': start, 'end': end})
    result.append({'index': ep, 'frames': int(r['length']), 'tasks': r.get('tasks', []), 'videos': videos})
print(json.dumps(result, ensure_ascii=False))
