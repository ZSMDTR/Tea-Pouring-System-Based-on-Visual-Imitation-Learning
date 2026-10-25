"""One explicit recording per process. Imports hardware only inside build_hardware()."""
import argparse
import json
import math
import signal
import time
import traceback
from pathlib import Path
from recording import write_json

ROOT = Path(__file__).resolve().parent


def build_hardware(profile):
    from lerobot.cameras.orbbec.configuration_orbbec import OrbbecColorCameraConfig
    from lerobot_robot_seeed_b601.config_seeed_b601_rs_follower import SeeedB601RSFollowerConfig
    from lerobot_robot_seeed_b601.seeed_b601_rs_follower import SeeedB601RSFollower
    from lerobot_teleoperator_rebot_arm_102.config_rebot_arm_102_leader import RebotArm102LeaderConfig
    from lerobot_teleoperator_rebot_arm_102.rebot_arm_102_leader import RebotArm102Leader
    cameras = {'wrist_rgb': OrbbecColorCameraConfig(**profile['camera'])}
    if profile.get('secondary_enabled'):
        cameras['head_rgb'] = OrbbecColorCameraConfig(**profile['secondary_camera'])
    robot = SeeedB601RSFollower(SeeedB601RSFollowerConfig(id=profile['robot_id'], port=profile['can_port'], cameras=cameras))
    leader = RebotArm102Leader(RebotArm102LeaderConfig(id=profile['leader_id'], port=profile['leader_port']))
    if not robot.is_calibrated or not leader.is_calibrated:
        raise RuntimeError('缺少已有标定，已停止；工作台不会自动进入标定流程。')
    return robot, leader


def make_dataset(robot, request, fps):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.datasets.utils import combine_feature_dicts
    from lerobot.datasets.pipeline_features import aggregate_pipeline_dataset_features, create_initial_features
    from lerobot.processor import make_default_processors
    ap, _, op = make_default_processors()
    features = combine_feature_dicts(
        aggregate_pipeline_dataset_features(pipeline=ap, initial_features=create_initial_features(action=robot.action_features), use_videos=True),
        aggregate_pipeline_dataset_features(pipeline=op, initial_features=create_initial_features(observation=robot.observation_features), use_videos=True))
    return LeRobotDataset.create('local/' + request['dataset'], fps, root=request['root'], robot_type=robot.name,
                                features=features, use_videos=True, image_writer_threads=4, vcodec='h264')


def cleanup(robot, leader, connected):
    """Report return failures instead of swallowing them; clean partial connects too."""
    errors = []
    if robot is not None and not getattr(robot, '_workbench_cleanup_done', False):
        robot._workbench_cleanup_done = True
        if connected:
            try:
                if not robot._emergency_disable_requested:
                    robot.safe_zero(exit_on_complete=False)
            except BaseException as exc:
                errors.append('回零失败：' + str(exc))
        # Prevent Robot.__del__ from retrying safe_zero on closed handles.
        # This does not claim that a failed disable succeeded.
        robot._skip_safe_zero_on_disconnect = True
        # Explicit cleanup handles partially connected cameras (driver is_connected
        # would otherwise prevent disconnect) and never retries a failed motion.
        for motor in robot.motors.values():
            for method in ('disable', 'close'):
                try:
                    getattr(motor, method)()
                except BaseException as exc:
                    errors.append(method + '：' + str(exc))
        if robot.bus is not None:
            try:
                robot.bus.close()
            except BaseException as exc:
                errors.append(str(exc))
            finally:
                robot.bus = None
        for camera in robot.cameras.values():
            try:
                if camera.is_connected:
                    camera.disconnect()
            except BaseException as exc:
                errors.append(str(exc))
    if leader is not None and leader.is_connected:
        try:
            leader.disconnect()
        except BaseException as exc:
            errors.append(str(exc))
    return errors


def check_batch_alignment(robot,leader):
    """Read-only gate before each automatic episode; never drive into alignment."""
    robot.get_observation()
    action=leader.get_action()
    for name,motor in robot.motors.items():
        state=motor.get_state()
        try:value=float(action[name+'.pos'])
        except (KeyError,ValueError,TypeError):raise RuntimeError('批量开始前缺少引导臂反馈，已停止。') from None
        if state is None or not math.isfinite(value) or not math.isfinite(state.pos):
            raise RuntimeError('批量开始前缺少有效姿态反馈，已停止。')
        goal=value*robot.config.joint_directions[name]
        lo,hi=robot.config.joint_limits[name];goal=max(lo,min(hi,goal))
        tolerance=150 if name=='gripper' else 8
        if abs(goal-math.degrees(state.pos))>tolerance:
            raise RuntimeError('批量开始前主从臂姿态不一致：'+name+'。请将引导臂放回与从臂一致的姿态后重新开始。')


def capture(folder, request, robot, leader, dataset, profile):
    import cv2
    from lerobot.datasets.utils import build_dataset_frame
    state = {**request, 'frames': 0, 'elapsed': 0, 'saved': False, 'acceptance': 'pending_replay'}
    state['camera_views']=['wrist_rgb']+(['head_rgb'] if profile.get('secondary_enabled') else [])
    def update(phase, message):
        state.update(state=phase, message=message)
        write_json(folder / 'status.json', state)
    stopped_by_signal = []
    def stop_signal(*_):
        stopped_by_signal.append(True)
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    def reason():
        if stopped_by_signal or (folder / 'stop').exists():
            return '手动结束'
        if time.time() - (folder / 'heartbeat').stat().st_mtime > 15:
            return '页面连接中断'
        return None
    connected = False
    errors = []
    stop_reason = None
    sample_times = []
    try:
        if reason():
            raise RuntimeError('连接设备前已收到停止请求或页面已断开。')
        update('connecting', '正在连接设备；连接完成后开始本条采集。')
        leader.connect(calibrate=False)
        if reason():
            raise RuntimeError('已取消连接。')
        robot.connect(calibrate=False)
        connected = True
        if request.get('series_count',1)>1:check_batch_alignment(robot,leader)
        start = time.monotonic()
        preview_at = 0
        update('recording', '正在采集；结束按钮会停止记录并正常回零、失能。')
        while True:
            tick = time.monotonic()
            state['elapsed'] = round(tick - start, 2)
            stop_reason = reason()
            if request['seconds'] and state['elapsed'] >= request['seconds']:
                stop_reason = stop_reason or '到时结束'
            if stop_reason:
                break
            obs = robot.get_observation()
            observation_at = time.monotonic()
            if reason():
                stop_reason = reason()
                break
            action = leader.get_action()
            robot.send_action(action)
            action_at = time.monotonic()
            # Preserve official pre-mapping action semantics. Replaying these
            # actions must call send_action once (direction/gripper scale once).
            frame = {**build_dataset_frame(dataset.features, obs, prefix='observation'),
                     **build_dataset_frame(dataset.features, action, prefix='action'), 'task': request['task']}
            dataset.add_frame(frame)
            written_at = time.monotonic()
            sample_times.append({'observation_start_s': tick - start, 'frame_written_s': written_at - start,
                'observation_s': observation_at-tick, 'action_s': action_at-observation_at, 'write_s': written_at-action_at})
            state['frames'] += 1
            if tick - preview_at >= .2:
                for view in state['camera_views']:
                    target=folder if view=='wrist_rgb' else folder/'head'
                    target.mkdir(exist_ok=True)
                    ok, jpg = cv2.imencode('.jpg', cv2.cvtColor(obs[view], cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok:
                        tmp = target / 'preview.tmp'
                        tmp.write_bytes(jpg.tobytes())
                        tmp.replace(target / 'preview.jpg')
                update('recording', '正在采集…')
                preview_at = tick
            time.sleep(max(0, 1 / profile['fps'] - (time.monotonic() - tick)))
    except BaseException as exc:
        traceback.print_exc()
        errors.append(str(exc) or type(exc).__name__)
    finally:
        try:
            update('returning', '正在按现有流程回零并失能，请等待完成。')
        finally:
            errors.extend(cleanup(robot, leader, connected))
    try:
        if state['frames']:
            update('saving', '设备退出完成，正在保存本条数据和视频…')
            dataset.save_episode()
            dataset.finalize()
            info = json.loads((Path(request['root']) / 'meta/info.json').read_text())
            if info['total_episodes'] != 1 or info['total_frames'] != state['frames']:
                raise RuntimeError('保存后的条数或帧数检查不一致。')
            state['saved'] = True
        else:
            errors.append('没有采到帧，未保存示范。')
    except BaseException as exc:
        traceback.print_exc()
        errors.append('保存失败：' + str(exc))
    finally:
        try:
            dataset.finalize()
        except BaseException as exc:
            errors.append('数据文件关闭失败：' + str(exc))
        try:
            dataset.stop_image_writer()
        except BaseException as exc:
            errors.append('图像写入退出失败：' + str(exc))
    if stop_reason == '页面连接中断':
        errors.append('页面失联超过 15 秒，自动停止；本条需检查。')
    state['stop_reason'] = stop_reason
    state['errors'] = errors
    if len(sample_times)>1:
        intervals=[b['observation_start_s']-a['observation_start_s'] for a,b in zip(sample_times,sample_times[1:])]
        state['capture_quality']={'actual_fps':(len(sample_times)-1)/(sample_times[-1]['observation_start_s']-sample_times[0]['observation_start_s']),
            'max_interval_ms':max(intervals)*1000, 'slow_intervals':sum(v>1.5/profile['fps'] for v in intervals),
            'frames':len(sample_times), 'max_observation_ms':max(v['observation_s'] for v in sample_times)*1000,
            'max_write_ms':max(v['write_s'] for v in sample_times)*1000,
            'note':'根据采集循环计时；不是相机曝光帧率或电机反馈频率，也不代表任务成功。'}
    update('error' if errors else 'completed', '；'.join(errors) if errors else '已保存 1 条示范，待实机 Replay 验收。')
    write_json(Path(request['root']) / 'workbench_capture.json', {**state, 'profile': profile, 'action_semantics': 'leader_command_before_follower_mapping', 'frame_timing': sample_times})
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', nargs='?')
    parser.add_argument('--lock-fd', type=int)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    profile = json.loads((ROOT / 'capture_profile.json').read_text())
    if args.check:
        robot, leader = build_hardware(profile)
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        print(json.dumps({'profile': profile, 'robot_calibrated': robot.is_calibrated, 'leader_calibrated': leader.is_calibrated, 'devices_connected': False}))
        return
    if args.lock_fd is None:
        raise SystemExit('请从工作台开始采集。')
    import os
    os.fstat(args.lock_fd)
    folder = Path(args.folder)
    request = json.loads((folder / 'request.json').read_text())
    try:
        robot, leader = build_hardware(profile)
        from dataclasses import asdict
        profile['robot_config'] = json.loads(json.dumps(asdict(robot.config), default=str))
        profile['leader_config'] = json.loads(json.dumps(asdict(leader.config), default=str))
        profile['robot_calibration'] = json.loads(robot.calibration_fpath.read_text())
        profile['leader_calibration'] = json.loads(leader.calibration_fpath.read_text())
        dataset = make_dataset(robot, request, profile['fps'])
        # No connections are made until all imports, calibration and dataset
        # setup succeed. capture owns all subsequent device cleanup.
        capture(folder, request, robot, leader, dataset, profile)
    except BaseException as exc:
        traceback.print_exc()
        previous = json.loads((folder / 'status.json').read_text())
        write_json(folder / 'status.json', {**previous, 'state': 'error', 'message': str(exc) or type(exc).__name__})


if __name__ == '__main__':
    main()
