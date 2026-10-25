"""One replay, one follower owner. --plan never opens a device."""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
import traceback
from collections import deque
from recording import write_json
from record_worker import cleanup
from replay_data import NAMES, DIRECTIONS, prepare, digest

ROOT=Path(__file__).resolve().parent


class Stopped(Exception):pass


def build_robot(profile):
    from lerobot_robot_seeed_b601 import SeeedB601RSFollowerConfig
    from b601_driver_adapter import WorkbenchB601RSFollower
    robot=WorkbenchB601RSFollower(SeeedB601RSFollowerConfig(id=profile['robot_id'],port=profile['can_port'],cameras={}))
    robot._skip_safe_zero_on_disconnect=True
    if not robot.is_calibrated:raise ValueError('缺少已有标定；不会进入自动标定。')
    return robot


def prepared(root,episode,robot):
    config=json.loads(json.dumps(asdict(robot.config),default=str))
    calibration=json.loads(robot.calibration_fpath.read_text())
    plan,actions,intervals=prepare(root,episode,config,calibration)
    code=hashlib.sha256()
    import inspect
    for path in (Path(inspect.getfile(type(robot).__bases__[0])),
                 Path(inspect.getfile(type(robot).__bases__[0].__bases__[0])),
                 ROOT/'b601_driver_adapter.py',ROOT/'replay_worker.py',ROOT/'replay_data.py',Path(robot.config.gravity_urdf_path)):
        code.update(path.read_bytes())
    plan.pop('digest');plan['implementation_sha256']=code.hexdigest();plan['digest']=digest(plan)
    return plan,actions,intervals


def positions(robot):
    obs=robot.get_observation();result={}
    for n in NAMES:
        state=robot.motors[n].get_state()
        pos=float(obs[n+'.pos'])
        if state is None or not math.isfinite(pos):raise RuntimeError('Replay 缺少有效关节反馈：'+n)
        lo,hi=robot.config.joint_limits[n]
        if not lo-5<=pos<=hi+5:raise RuntimeError('Replay 反馈位置越界：'+n)
        result[n]=pos
    if getattr(robot,'_emergency_disable_requested',False):raise RuntimeError('从臂触发保护性失能。')
    return result


def move_to_start(robot,targets,check,progress,clock=time.monotonic,sleep=time.sleep):
    """Quintic move to first effective recorded target, then verify a full window."""
    starts=positions(robot)
    duration=max(3.,max(1.875*abs(targets[n]-starts[n])/(60 if n=='gripper' else 20) for n in NAMES))
    frames=math.ceil(duration*50);index=0;begin=clock();history=deque()
    while True:
        tick=clock();check();actual=positions(robot)
        u=min(1.,index/frames);blend=u**3*(10+u*(-15+6*u))
        goal={n:starts[n]+blend*(targets[n]-starts[n]) for n in NAMES}
        if any(abs(goal[n]-actual[n])>(150 if n=='gripper' else 20) for n in NAMES):
            raise RuntimeError('进入示范起始位时跟随差过大，未开始 Replay。')
        check();robot.send_action({n+'.pos':goal[n]/DIRECTIONS[n] for n in NAMES})
        if getattr(robot,'_emergency_disable_requested',False):raise RuntimeError('从臂触发保护性失能。')
        errors={n:abs(actual[n]-targets[n]) for n in NAMES}
        if u==1 and all(errors[n]<=(8 if n=='gripper' else 3) for n in NAMES):
            history.append((clock(),actual))
            while len(history)>1 and history[1][0]<=clock()-.4:history.popleft()
        else:history.clear()
        if len(history)>=10 and clock()-history[0][0]>=.4 and all(
            max(p[n] for _,p in history)-min(p[n] for _,p in history)<=(2 if n=='gripper' else 1) for n in NAMES):return
        if index%10==0:progress(round(u*100,1),round(max(errors.values()),2))
        if clock()-begin>duration+8:raise TimeoutError('进入示范起始位未确认稳定，未开始 Replay。')
        index+=1;sleep(max(0,.02-(clock()-tick)))


def run(folder,request,robot,actions,intervals,check_external=lambda:None,preview=lambda:None,
        clock=time.monotonic,sleep=time.sleep,move=move_to_start):
    state={**request,'frames':0,'total_frames':len(actions),'elapsed':0,'acceptance':'pending','return_completed':False}
    stop=[];errors=[];connected=False;normal=False;completed=False;begin=clock()
    previous={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
    for sig in previous:signal.signal(sig,lambda *_:stop.append(True))
    def update(phase,message,**extra):
        state.update(state=phase,message=message,**extra);write_json(folder/'status.json',state)
    def check():
        if stop or (folder/'stop').exists():raise Stopped('手动结束')
        if time.time()-(folder/'heartbeat').stat().st_mtime>15:raise RuntimeError('页面失联超过 15 秒，停止下发动作。')
        check_external()
        if getattr(robot,'_emergency_disable_requested',False):raise RuntimeError('从臂触发保护性失能。')
    try:
        check();update('connecting','正在连接从臂；不连接引导臂。')
        robot.connect(calibrate=False);connected=True;check()
        update('positioning','正在缓慢进入本条示范起始位，请等待到位。')
        move(robot,actions[0]['motor'],check,lambda p,e:update('positioning','正在进入示范起始位…',startup_progress=p,startup_error_deg=e),clock,sleep)
        begin=clock();last_preview=-math.inf;max_overrun=0.
        update('replaying','正在按录制节奏 Replay；可正常结束并回水平位。')
        for action,interval in zip(actions,intervals):
            tick=clock();check();actual=positions(robot)
            # Distinct arm/gripper guards in measured motor degrees, before send.
            for n in NAMES:
                if abs(action['motor'][n]-actual[n])>(150 if n=='gripper' else 20):
                    raise RuntimeError('Replay 目标与实测位置相差过大：'+n+'；已停止，不继续跳向远处目标。')
            check();sent=robot.send_action(action['raw'])
            if any(abs(sent[n+'.pos']-action['motor'][n])>.1 for n in NAMES):raise RuntimeError('驱动下发值与录制目标不一致，Replay 中止。')
            state['frames']+=1;state['elapsed']=round(clock()-begin,2)
            if tick-last_preview>=.2:
                preview();update('replaying','正在 Replay…');last_preview=tick
            max_overrun=max(max_overrun,clock()-tick-interval)
            if clock()-tick>max(.25,interval*3):raise RuntimeError('Replay 执行耗时明显超过录制间隔，已停止；不会加速追帧。')
            # Preserve every sample. Slow work extends replay; never send bursts
            # to catch up or skip the last target's holding interval.
            remaining=max(0,interval-(clock()-tick))
            while remaining>0:
                check();step=min(.02,remaining);sleep(step);remaining-=step
        check();normal=True;completed=True;state['max_overrun_ms']=round(max_overrun*1000,2)
    except Stopped as exc:
        normal=connected;state['stop_reason']=str(exc)
    except BaseException as exc:
        traceback.print_exc();errors.append(str(exc) or type(exc).__name__)
    finally:
        try:update('returning','正在回水平位并失能，请等待。' if normal else '执行异常，正在停止并失能；不强行回位。')
        except BaseException as exc:errors.append('状态写入失败：'+str(exc))
        return_attempted=connected and normal and not getattr(robot,'_emergency_disable_requested',False)
        failures=cleanup(robot,None,return_attempted);errors.extend(failures)
        state['return_completed']=return_attempted and not failures
        state['return_attempted']=return_attempted;state['errors']=errors
        for sig,handler in previous.items():signal.signal(sig,handler)
    phase='error' if errors else 'completed' if completed else 'stopped'
    message='；'.join(errors) if errors else '完整 Replay 已结束，已回水平位并失能。请人工确认任务结果。' if completed else '已手动结束，已回水平位并失能；本次未验收。'
    update(phase,message,finished_at=time.time())
    return state


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',nargs='?');parser.add_argument('--lock-fd',type=int)
    parser.add_argument('--plan',nargs=2,metavar=('ROOT','EPISODE'));args=parser.parse_args()
    profile=json.loads((ROOT/'capture_profile.json').read_text());robot=None;camera=None
    try:
        robot=build_robot(profile)
        if args.plan:
            value,_,_=prepared(args.plan[0],int(args.plan[1]),robot);print(json.dumps(value,ensure_ascii=False));return
        if args.lock_fd is None:raise ValueError('请从工作台启动 Replay。')
        os.fstat(args.lock_fd);folder=Path(args.folder);request=json.loads((folder/'request.json').read_text())
        plan,actions,intervals=prepared(request['root'],request['episode'],robot)
        if plan['digest']!=request['plan']['digest']:raise ValueError('启动前数据或配置变化，未连接设备。请重新预览。')
        from lerobot.cameras.orbbec.configuration_orbbec import OrbbecColorCameraConfig
        from lerobot.cameras.orbbec.camera_orbbec import OrbbecColorCamera
        from execution_timing import LatestFrameReader
        import cv2
        camera=OrbbecColorCamera(OrbbecColorCameraConfig(**profile['camera']))
        camera.connect();camera.async_read(timeout_ms=1000);reader=LatestFrameReader(camera);reader.latest()
        def preview():
            ok,jpg=cv2.imencode('.jpg',cv2.cvtColor(reader.latest(),cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,75])
            if ok:
                temp=folder/'preview.tmp';temp.write_bytes(jpg.tobytes());temp.replace(folder/'preview.jpg')
        preview();run(folder,request,robot,actions,intervals,reader.latest,preview)
    except BaseException as exc:
        if args.plan:
            print(str(exc) or type(exc).__name__,file=sys.stderr)
            raise SystemExit(1)
        traceback.print_exc()
        if args.folder:
            folder=Path(args.folder);state=json.loads((folder/'status.json').read_text())
            write_json(folder/'status.json',{**state,'state':'error','message':str(exc) or type(exc).__name__,'return_completed':False})
    finally:
        if robot is not None and not args.plan:cleanup(robot,None,False)
        if camera is not None:
            try:
                if camera.is_connected:camera.disconnect()
            except BaseException as exc:
                if args.folder:
                    state=json.loads((Path(args.folder)/'status.json').read_text());write_json(Path(args.folder)/'status.json',{**state,'state':'error','message':'相机退出失败：'+str(exc),'return_completed':False})


if __name__=='__main__':main()
