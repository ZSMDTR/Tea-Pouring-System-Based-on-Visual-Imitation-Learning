"""Read-only B601/OpenPI format contract, independent of both ML environments."""
import json
from pathlib import Path
import camera_contract

JOINTS = [n+'.pos' for n in ('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_yaw','wrist_roll','gripper')]


def inspect(root):
    root=Path(root); info=json.loads((root/'meta/info.json').read_text()); issues=[]
    if (root/'workbench_simulation.json').is_file():issues.append('软件设备测试数据不能用于实机模型训练或回放。')
    version=info.get('codebase_version','')
    if version not in ('v3.0','v2.1'):issues.append('仅支持已使用的 LeRobot v3.0 / v2.1。')
    features=info.get('features',{})
    for key in ('action','observation.state'):
        f=features.get(key,{})
        if f.get('shape')!=[7] or f.get('names')!=JOINTS:issues.append(key+' 必须是约定顺序的 7 维关节数据。')
    camera=features.get('observation.images.wrist_rgb',{})
    if camera.get('dtype')!='video' or camera.get('shape')!=[480,640,3]:issues.append('缺少 640×480 的 wrist_rgb 视频。')
    if info.get('fps')!=30:issues.append('当前训练配置要求 30 FPS。')
    if info.get('total_frames',0)<=0 or info.get('total_episodes',0)<=0:issues.append('数据集没有完整示范。')
    provenance=root/'workbench_capture.json'
    capture=json.loads(provenance.read_text()) if provenance.is_file() else {}
    if capture and (not capture.get('saved') or capture.get('state')!='completed'):issues.append('本条采集异常或未完整保存。')
    warnings=['格式检查不代表 Replay 已通过，也不代表模型已能完成任务。']
    if not capture.get('profile'):warnings.append('历史数据没有工作台配置快照，相机视角与裁剪需人工确认。')
    camera_inputs=None
    try:camera_inputs=camera_contract.dataset(root)
    except ValueError as exc:issues.append(str(exc))
    return {'name':root.name,'version':version,'episodes':info.get('total_episodes',0),'frames':info.get('total_frames',0),
            'compatible':not issues,'needs_conversion':version=='v3.0','issues':issues,'warnings':warnings,
            'camera_contract':camera_inputs,'camera_label':camera_contract.label(camera_inputs) if camera_inputs else '相机格式不符合要求',
            'contract':'7 维 action / state，原始角度与动作语义保持不变；方向转换由 B601Inputs 执行一次。'}
