"""Standalone teleoperation / gravity teaching; never starts on page load."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import time
import traceback
from recording import write_json
from record_worker import cleanup, ROOT
from modes import POSE


def build(profile,mode):
    from lerobot_robot_seeed_b601.config_seeed_b601_rs_follower import SeeedB601RSFollowerConfig
    from lerobot_robot_seeed_b601.seeed_b601_rs_follower import SeeedB601RSFollower
    robot=SeeedB601RSFollower(SeeedB601RSFollowerConfig(id=profile['robot_id'],port=profile['can_port'],cameras={}))
    if not robot.is_calibrated:raise RuntimeError('缺少从臂标定。')
    if mode=='gravity':
        robot._build_gravity_pairing()
        if not robot._gravity_pairing:raise RuntimeError('重力模型不可用，未连接设备。')
    leader=None
    if mode=='teleop':
        from lerobot_teleoperator_rebot_arm_102.config_rebot_arm_102_leader import RebotArm102LeaderConfig
        from lerobot_teleoperator_rebot_arm_102.rebot_arm_102_leader import RebotArm102Leader
        leader=RebotArm102Leader(RebotArm102LeaderConfig(id=profile['leader_id'],port=profile['leader_port']))
        if not leader.is_calibrated:raise RuntimeError('缺少引导臂标定。')
    return robot,leader


def create_gravity(robot):
    from official_gravity import Controller
    return Controller(robot)


def measured(robot, live=False):
    for motor in robot.motors.values():motor.request_feedback()
    robot.bus.poll_feedback_once()
    result={};speeds=[]
    for name,motor in robot.motors.items():
        state=motor.get_state()
        if state is None or not all(math.isfinite(float(v)) for v in ((state.pos,state.t_mos,state.t_rotor) if live else (state.pos,state.vel,state.t_mos,state.t_rotor))):
            raise RuntimeError('关节反馈缺失或无效：'+name)
        # Match the official normal path: explicit feedback request + poll,
        # then read the returned state. Do not add a blocking SDO transaction
        # per joint per tick. Missing states fail above, never become zero.
        position=state.pos
        if not math.isfinite(position):raise RuntimeError('关节实时位置无效：'+name)
        value=math.degrees(position);lo,hi=robot.config.joint_limits[name]
        if value<lo-2 or value>hi+2:raise RuntimeError('关节超出调姿范围：'+name)
        result[name]=value;speeds.append(abs(state.vel))
    return result,max(speeds)


def run(folder,request,robot,leader,profile):
    state={**request};connected=False;errors=[];stop=[];original_kp=dict(robot.config.mit_kp)
    def update(phase,message):state.update(state=phase,message=message);write_json(folder/'status.json',state)
    def stopped():return bool(stop or (folder/'stop').exists() or time.time()-(folder/'heartbeat').stat().st_mtime>15)
    signal.signal(signal.SIGTERM,lambda *_:stop.append(True));signal.signal(signal.SIGINT,lambda *_:stop.append(True))
    try:
        if stopped():raise RuntimeError('连接前已停止或页面已断开。')
        update('connecting','正在连接设备…')
        if leader:leader.connect(calibrate=False)
        if stopped():raise RuntimeError('已取消连接。')
        controller=create_gravity(robot) if request['mode']=='gravity' else None
        if stopped():raise RuntimeError('已取消连接。')
        robot.connect(calibrate=False);connected=True
        positions,_=measured(robot,live=controller is not None);gripper=positions['gripper']
        last=0
        while not stopped():
            tick=time.monotonic();positions,speed=measured(robot,live=controller is not None)
            if stopped():break
            if controller:
                command=controller.step(positions)
                if stopped():break
                controller.send(command,gripper)
                speed=controller.raw_speed
                state.update(controller.status())
            else:
                robot.send_action(leader.get_action())
            if (folder/'save_pose').exists():
                (folder/'save_pose').unlink()
                if speed>.1 or (controller and controller._lock_status!='LOCKED'):state['pose_message']='尚未稳定锁定，未保存；请静止后重新点击。'
                else:
                    pose={'enabled':False,'saved_at':time.time(),'motor_degrees':positions,'profile':profile,'calibration':json.loads(robot.calibration_fpath.read_text()),'source_job':request['id'],'controller':controller.status() if controller else None}
                    POSE.parent.mkdir(parents=True,exist_ok=True);write_json(POSE,pose)
                    state['pose_message']='候选初始姿态已保存，尚未启用。'
            if tick-last>.2:
                state['positions']=positions;update(request['mode'],'遥操作运行中，不采集数据。' if leader else {'STARTUP':'官方调姿：正在平滑进入补偿模式…','FOLLOW':'官方调姿：手扶跟随中，停稳后自动锁定。','LOCKED':'官方调姿：姿态已锁定，可保存候选初始姿态。'}.get(state.get('lock_state'),'官方调姿运行中。'));last=tick
            time.sleep(max(0,1/30-(time.monotonic()-tick)))
        if time.time()-(folder/'heartbeat').stat().st_mtime>15:errors.append('页面失联，已自动退出。')
    except BaseException as exc:traceback.print_exc();errors.append(str(exc) or type(exc).__name__)
    finally:
        robot.config.mit_kp=original_kp
        try:update('returning','正在按原流程缓慢回零并失能…')
        finally:
            cleanup_errors=cleanup(robot,leader,connected)
            errors.extend(cleanup_errors)
    state['cleanup_ok']=not cleanup_errors
    if cleanup_errors:
        state['hardware_state']='unconfirmed'
        errors.insert(0,'退出未完整完成，不能确认已回零或失能；请先人工确认设备安全。')
    update('error' if errors else 'completed','；'.join(errors) if errors else '模式结束，已回零并失能。')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);parser.add_argument('--lock-fd',type=int,required=True);args=parser.parse_args();os.fstat(args.lock_fd)
    request=json.loads((args.folder/'request.json').read_text())
    try:
        profile=json.loads((ROOT/'capture_profile.json').read_text());robot,leader=build(profile,request['mode']);run(args.folder,request,robot,leader,profile)
    except BaseException as exc:traceback.print_exc();write_json(args.folder/'status.json',{**request,'state':'error','message':str(exc)})

if __name__=='__main__':main()
