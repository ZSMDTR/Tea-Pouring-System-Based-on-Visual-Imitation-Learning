"""Read one episode using the data environment; never import hardware modules."""
import json
import math
from pathlib import Path
import sys


def safe_file(root, relative, folder):
    path = (root / relative).resolve()
    if not path.is_relative_to((root / folder).resolve()) or not path.is_relative_to(root):
        raise ValueError('数据文件位置无效。')
    return path


def inspect(root, episode):
    import numpy as np
    import pyarrow.parquet as pq
    root = Path(root).resolve()
    if type(episode) is not int or episode < 0:
        raise ValueError('示范编号无效。')
    info = json.loads((root / 'meta/info.json').read_text())
    fps = float(info.get('fps', 0))
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('数据集帧率无效。')
    v3 = str(info.get('codebase_version', '')).startswith('v3')
    record = None
    if v3:
        for file in sorted((root / 'meta/episodes').rglob('*.parquet')):
            file = safe_file(root, file.relative_to(root), 'meta')
            schema = pq.ParquetFile(file).schema_arrow.names
            cols = [n for n in schema if not n.startswith('stats/')]
            rows = pq.read_table(file, columns=cols, filters=[('episode_index', '=', episode)]).to_pylist()
            if rows:
                record = rows[0]
                break
    else:
        for line in (root / 'meta/episodes.jsonl').read_text().splitlines():
            row = json.loads(line)
            if row.get('episode_index') == episode:
                record = row
                break
    if not record:
        raise ValueError('找不到这条示范。')
    variables = {'episode_index': episode, 'episode_chunk': episode // int(info.get('chunks_size', 1000))}
    if v3:
        variables.update(chunk_index=int(record['data/chunk_index']), file_index=int(record['data/file_index']))
    path = safe_file(root, info['data_path'].format(**variables), 'data')
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows > 2_000_000:
        raise ValueError('单个数据文件过大，暂不在网页中扫描。')
    columns = [n for n in ('episode_index', 'frame_index', 'timestamp', 'action', 'observation.state')
               if n in parquet.schema_arrow.names]
    table = pq.read_table(path, columns=columns, filters=[('episode_index', '=', episode)])
    rows = table.to_pylist()
    count = len(rows)
    if not count:
        raise ValueError('这条示范没有动作数据。')
    checks = []

    def check(key, label, status, detail):
        checks.append({'key': key, 'label': label, 'status': status, 'detail': detail})

    expected = int(record['length'])
    check('frames', '帧数一致性', 'ok' if count == expected else 'error', f'数据 {count} 帧 / 元信息 {expected} 帧')
    raw_times = [r.get('timestamp') for r in rows]
    timed = all(isinstance(x, (int, float)) and math.isfinite(x) for x in raw_times)
    times = np.asarray(raw_times, dtype=float) if timed else np.arange(count) / fps
    delta = np.diff(times)
    non_increasing = int(np.sum(delta <= 0)) if timed else 0
    long_gaps = int(np.sum(delta > 1.5 / fps)) if timed else 0
    duration = float(times[-1] - times[0]) if timed and not non_increasing else max(0, count - 1) / fps
    check('timing', '数据时间戳', 'warning' if not timed or non_increasing or long_gaps else 'ok',
          '缺少有效时间戳，曲线按标称帧率显示。' if not timed else
          f'{non_increasing} 处未递增 · {long_gaps} 处间隔超过标称周期的 1.5 倍')
    features = info.get('features', {})
    curves = {};motion={}
    selected = np.unique(np.linspace(0, count - 1, min(count, 240), dtype=int))
    for key, label in [('action', '动作'), ('observation.state', '关节状态')]:
        feature = features.get(key, {})
        shape = feature.get('shape') or [7]
        width = int(shape[0])
        if not 1 <= width <= 64:
            raise ValueError('数据维度超出网页检查范围。')
        matrix = []
        invalid = 0
        for row in rows:
            try:
                vector = np.asarray(row.get(key), dtype=float).reshape(-1)
            except (TypeError, ValueError):
                vector = np.array([])
            if len(vector) != width or not np.isfinite(vector).all():
                invalid += 1
                matrix.append([None] * width)
            else:
                matrix.append(vector.tolist())
        check(key, label + '数值', 'error' if invalid else 'ok', f'{width} 维 · {invalid} 帧缺失、维度异常或包含非有限值')
        names = feature.get('names')
        if not isinstance(names, list) or len(names) != width:
            names = [f'维度 {i + 1}' for i in range(width)]
        curves[key] = {'names': names, 'values': [matrix[i] for i in selected]}
        if not invalid:
            values=np.asarray(matrix,dtype=float)
            motion[key]={'range':np.ptp(values,axis=0).tolist(),'max_step':np.max(np.abs(np.diff(values,axis=0)),axis=0).tolist() if count>1 else [0.]*width,
                         'names':names,'near_static_candidate':bool(np.max(np.ptp(values,axis=0))<1.)}
    videos = []
    for key, feature in features.items():
        if feature.get('dtype') != 'video':
            continue
        video_vars = {**variables, 'video_key': key}
        if v3:
            video_vars.update(chunk_index=int(record[f'videos/{key}/chunk_index']), file_index=int(record[f'videos/{key}/file_index']))
        video = safe_file(root, info['video_path'].format(**video_vars), 'videos')
        videos.append({'camera': key, 'exists': video.is_file(), 'shape': feature.get('shape'),
                       'codec': feature.get('info', {}).get('video.codec', '')})
    missing = sum(not v['exists'] for v in videos)
    check('video', '视频文件', 'error' if missing or not videos else 'ok',
          f'{len(videos)} 路视频 · {missing} 个文件缺失；未解码检验全部视频帧')
    capture_path = root / 'workbench_capture.json'
    capture = json.loads(capture_path.read_text()) if capture_path.is_file() else {}
    recorded_camera = capture.get('profile', {}).get('camera')
    quality=capture.get('capture_quality')
    if quality:
        check('capture_quality','实际采集循环','warning' if quality.get('actual_fps',0)<fps*.9 or quality.get('slow_intervals',0)>count*.05 else 'ok',
              f"实际约 {quality.get('actual_fps',0):.1f} Hz · 慢周期 {quality.get('slow_intervals',0)} 处 · 最长间隔 {quality.get('max_interval_ms',0):.1f} ms；不是电机反馈或相机曝光频率")
    check('provenance', '采集配置记录', 'ok' if recorded_camera else 'warning',
          '有采集配置快照。' if recorded_camera else '历史记录没有相机配置快照，视角与裁剪需要人工核对。')
    relative = [round(float(times[i] - times[0]), 4) for i in selected]
    return {'dataset': root.name, 'episode': episode, 'frames': count, 'fps': fps,
            'duration_s': round(duration, 3), 'version': info.get('codebase_version'),
            'status': 'error' if any(c['status'] == 'error' for c in checks) else
                      'warning' if any(c['status'] == 'warning' for c in checks) else 'ok',
            'checks': checks, 'videos': videos, 'camera': recorded_camera,'capture_quality':quality,'motion':motion,
            'plot': {'times': relative, **curves},
            'note': '结构检查不判断任务成功；时间戳不代表实际相机或电机周期。曲线保持源数据值，不做方向或夹爪倍率转换。'}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(Path(sys.argv[1]), int(sys.argv[2])), ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
