"""Read-only replay contract. No device imports or connection side effects."""
import hashlib
import json
import math
from pathlib import Path

NAMES=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_yaw','wrist_roll','gripper')
KEYS=[n+'.pos' for n in NAMES]
DIRECTIONS=dict(zip(NAMES,(1,1,-1,-1,-1,1,6)))


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def source_identity(root):
    # Includes video identity without reading GBs of video; action/metadata bytes
    # are additionally hashed in prepare(). Reject symlinks throughout the source.
    from dataset_export import inventory
    return digest(inventory(Path(root)))


def episode_rows(root,episode):
    import pyarrow.dataset as ds
    paths=sorted((Path(root)/'data').rglob('*.parquet'))
    if not paths:raise ValueError('没有关节数据文件。')
    return ds.dataset([str(p) for p in paths],format='parquet').to_table(
        columns=['action','observation.state','timestamp','frame_index'],filter=ds.field('episode_index')==episode).sort_by([('frame_index','ascending')]).to_pylist()


def validate_rows(rows, fps, timing, limits, directions):
    if not rows or len(rows)>18000:raise ValueError('示范为空或超过 10 分钟，不能回放。')
    if directions!=DIRECTIONS:raise ValueError('当前从臂映射与 B601 记录约定不一致。')
    actions=[];clipped=0;timestamps=[]
    for i,row in enumerate(rows):
        if row['frame_index']!=i:raise ValueError('帧序号不连续，不能回放。')
        for field in ('action','observation.state'):
            values=row[field]
            if len(values)!=7 or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=2000 for v in values):
                raise ValueError('关节数据缺失、非有限值或量纲异常。')
        timestamp=float(row['timestamp'])
        if not math.isfinite(timestamp) or (timestamps and timestamp<=timestamps[-1]):raise ValueError('数据时间戳不连续。')
        timestamps.append(timestamp)
        # Match exactly the official capture driver's direction then joint-limit
        # clipping. Store raw actions for send_action to apply mapping just once.
        goal={n:max(limits[n][0],min(limits[n][1],row['action'][j]*directions[n])) for j,n in enumerate(NAMES)}
        clipped+=int(any(abs(goal[n]-row['action'][j]*directions[n])>1e-4 for j,n in enumerate(NAMES)))
        actions.append({'raw':dict(zip(KEYS,row['action'])),'motor':goal})
    if timing is not None:
        if len(timing)!=len(rows):raise ValueError('采集时间记录与帧数不一致。')
        timestamps=[float(t['observation_start_s']) for t in timing]
        clock_source='采集实测时间'
    else:clock_source='数据集时间戳（历史记录无实测时间）'
    intervals=[timestamps[i+1]-timestamps[i] for i in range(len(rows)-1)]+[1/fps]
    if not all(math.isfinite(v) and .005<=v<=2 for v in intervals):raise ValueError('采集间隔异常；请检查时间记录，不能直接回放。')
    return actions,intervals,clock_source,clipped


def prepare(root, episode, config, calibration):
    """Load one episode only. Runs in LeRobot Python, without robot connection."""
    import pyarrow.dataset as ds
    root=Path(root)
    before=source_identity(root)
    info=json.loads((root/'meta/info.json').read_text())
    if type(episode) is not int or not 0<=episode<info['total_episodes']:raise ValueError('示范编号无效。')
    if info.get('robot_type')!='seeed_b601_rs_follower' or info.get('fps')!=30:raise ValueError('Replay 首版仅支持本机 B601 RS 的 30 FPS 示范。')
    for key in ('action','observation.state'):
        feature=info.get('features',{}).get(key,{})
        if feature.get('shape')!=[7] or feature.get('names')!=KEYS:raise ValueError('Replay 需要约定顺序的 7 维关节数据。')
    manifest=root/'workbench_capture.json'
    capture=json.loads(manifest.read_text()) if manifest.is_file() else {}
    legacy=not bool(capture);reference=None;reference_rows=None
    if legacy:
        if info.get('codebase_version')!='v3.0':
            original=root.with_name(root.name[:-7]) if root.name.endswith('_openpi') else None
            if info.get('codebase_version')!='v2.1' or not original or original.is_symlink() or not (original/'meta/info.json').is_file() or json.loads((original/'meta/info.json').read_text()).get('codebase_version')!='v3.0':
                raise ValueError('历史转换／导出副本缺少动作语义快照，请选择对应的原始 v3 数据集回放。')
            source_plan,_,_=prepare(original,episode,config,calibration)
            reference_rows=episode_rows(original,episode)
            if source_identity(original)!=source_plan['source_identity']:raise ValueError('核对期间原始数据发生变化。')
            reference={'dataset':original.name,'episode':episode,'source_identity':source_plan['source_identity'],'data_sha256':source_plan['data_sha256']}
    else:
        if not capture.get('saved') or capture.get('state')!='completed' or info['total_episodes']!=1 or episode!=0:
            raise ValueError('采集未完整保存或示范编号与采集快照不一致。')
        if capture.get('action_semantics')!='leader_command_before_follower_mapping':raise ValueError('采集动作语义未确认，不能回放。')
        profile=capture.get('profile',{})
        recorded=profile.get('robot_config',{})
        # Cameras and source serial port do not affect follower action semantics.
        current={k:v for k,v in config.items() if k!='cameras'}
        if {k:v for k,v in recorded.items() if k!='cameras'}!=current:
            raise ValueError('当前从臂控制配置与采集快照不同，已阻止回放。')
        if profile.get('robot_calibration')!=calibration:raise ValueError('当前标定与采集时不同，已阻止回放。')
    paths=sorted((root/'data').rglob('*.parquet'));rows=episode_rows(root,episode)
    if reference_rows is not None and (len(rows)!=len(reference_rows) or any(
        a['action']!=b['action'] or a['observation.state']!=b['observation.state'] or a['frame_index']!=b['frame_index'] or abs(a['timestamp']-b['timestamp'])>1e-4
        for a,b in zip(rows,reference_rows))):raise ValueError('转换副本与对应原始示范的关节数值／时间轴不一致，请使用原始数据。')
    if info.get('codebase_version')=='v3.0':
        meta_paths=sorted((root/'meta/episodes').rglob('*.parquet'))
        records=ds.dataset([str(p) for p in meta_paths],format='parquet').to_table(columns=['episode_index','length'],filter=ds.field('episode_index')==episode).to_pylist() if meta_paths else []
    else:
        records=[r for r in (json.loads(line) for line in (root/'meta/episodes.jsonl').read_text().splitlines() if line.strip()) if r['episode_index']==episode]
    if len(records)!=1 or records[0]['length']!=len(rows):raise ValueError('关节帧数与示范元信息不一致。')
    if not legacy and len(rows)!=capture.get('frames'):raise ValueError('保存的帧数不一致。')
    actions,intervals,clock_source,clipped=validate_rows(rows,30,capture.get('frame_timing'),config['joint_limits'],config['joint_directions'])
    content=hashlib.sha256()
    for p in sorted((root/'meta').rglob('*'))+paths+([manifest] if manifest.is_file() else []):
        if p.is_file():content.update(str(p.relative_to(root)).encode());content.update(p.read_bytes())
    if before!=source_identity(root):raise ValueError('读取期间源文件发生变化，请重新预览。')
    plan={'dataset':root.name,'episode':episode,'frames':len(rows),'duration_s':round(sum(intervals),3),
          'fps':30,'clock_source':clock_source,'legacy':legacy,'clipped_frames':clipped,
          'start_motor_degrees':actions[0]['motor'],'source_identity':before,'data_sha256':content.hexdigest(),
          'config_sha256':digest(config),'calibration_sha256':digest(calibration),'verified_original':reference,
          'warnings':(['历史记录缺少采集配置／标定快照；须确认它来自本机、使用相同标定和官方原始动作语义。'] if legacy else [])}
    if clipped:plan['warnings'].append(f'{clipped} 帧包含超出关节软范围的原始目标；回放沿用官方采集时相同的裁剪。')
    if reference:plan['warnings'].append('已与对应原始 v3 示范逐帧核对动作、观测关节数值和时间轴；没有再次转换关节方向。')
    plan['digest']=digest(plan)
    return plan,actions,intervals
