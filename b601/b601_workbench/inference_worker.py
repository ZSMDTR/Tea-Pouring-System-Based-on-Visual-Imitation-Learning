"""Camera-only preview; explicit commands alone create/connect the robot."""
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback
from recording import write_json
from record_worker import cleanup
from inference_limits import MAX_COMMAND_HZ, LEGACY_MAX_COMMAND_HZ
from working_pose import ArrivalTimeout, VERSION as WORKING_POSE_VERSION
from live_preview import PreviewPublisher
import camera_contract

JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_yaw','wrist_roll','gripper')

class Interrupted(Exception):
    def __init__(self,reason):self.reason=reason;super().__init__(reason)


def policy_observation(raw,image,prompt,head=None):
    import numpy as np
    state=np.asarray([raw[name+'.pos'] for name in JOINTS],dtype=np.float32)
    image=np.asarray(image)
    if not np.isfinite(state).all() or image.shape!=(480,640,3):raise ValueError('相机或关节观测无效。')
    result={'state':state,'wrist_rgb':image,'prompt':prompt}
    if head is not None:
        head=np.asarray(head)
        if head.shape!=(480,640,3) or head.dtype!=np.uint8:raise ValueError('第三视角观测无效。')
        result['head_rgb']=head
    return result


def action_chunk(result,chunk):
    import numpy as np
    a=np.asarray(result['actions'],dtype=np.float32)
    if a.ndim!=2 or a.shape[1]!=7 or len(a)<chunk or not np.isfinite(a).all():raise ValueError('策略返回了无效动作，已拒绝发送。')
    return a[:chunk]


def relative_limits(task):
    # Driver applies direction/scale first; these caps are motor degrees.
    return {name:float(task.get('gripper_relative_limit',task['relative_limit']) if name=='gripper' else task['relative_limit']) for name in JOINTS}


def build_robot(profile,limit):
    from lerobot_robot_seeed_b601 import SeeedB601RSFollowerConfig
    from b601_driver_adapter import WorkbenchB601RSFollower
    robot=WorkbenchB601RSFollower(SeeedB601RSFollowerConfig(id=profile['robot_id'],port=profile['can_port'],can_adapter='socketcan',max_relative_target=limit,cameras={}))
    robot._skip_safe_zero_on_disconnect=True
    if not robot.is_calibrated:raise ValueError('缺少既有标定；不自动进入标定流程。')
    return robot


class PolicyConnection:
    def __init__(self,task):
        from websockets.sync.client import connect
        from openpi_client import msgpack_numpy
        self.codec=msgpack_numpy;self.ws=connect('ws://127.0.0.1:'+str(task['port']),open_timeout=5,close_timeout=1,compression=None,max_size=8*1024*1024)
        try:
            meta=msgpack_numpy.unpackb(self.ws.recv(timeout=5))
            if meta.get('workbench_model_id')!=task['model_id']:raise ValueError('模型服务身份不匹配。')
            if camera_contract.validate(meta.get('camera_contract',camera_contract.contract())) != camera_contract.validate(task.get('camera_contract',camera_contract.contract())):
                raise ValueError('模型服务的相机输入约定不匹配。')
        except BaseException:self.ws.close();raise
    def infer(self,obs,reason):
        self.ws.send(self.codec.packb(obs));deadline=time.monotonic()+10
        while True:
            why=reason()
            if why:raise Interrupted(why)
            try:response=self.ws.recv(timeout=.1);break
            except TimeoutError:
                if time.monotonic()>deadline:raise TimeoutError('策略响应超过 10 秒；停止下发动作。')
        if isinstance(response,str):raise RuntimeError('模型服务出错：'+response[-1000:])
        return self.codec.unpackb(response)
    def close(self):self.ws.close()


def execute(task,profile,camera,reason,update,preview,robot_factory=build_robot,policy_factory=PolicyConnection,secondary_camera=None):
    """Returns a truthful result; faults disable without attempting a blind return."""
    first_send=last_send=None;max_gap=0.0;late_intervals=0
    robot=policy=rtc_queue=player=frame_reader=timings=None;connected=False;normal=False;outcome='error';errors=[];steps=0;start=time.monotonic()
    head_reader=None
    inputs=camera_contract.validate(task.get('camera_contract',camera_contract.contract()))
    startup_result=None;startup_timeout_return=False;return_attempted=False;return_completed=False
    mode=task.get('timing_mode','legacy')
    timed=mode=='timed'
    cached_camera=mode in ('native','timed')
    policy_hz=task.get('policy_hz',30) if timed else task['hz']
    def metrics():
        result={'timing_mode':mode,'interpolation':timed,'camera_read_mode':'latest' if cached_camera else 'wait_new','policy_hz':policy_hz,'target_command_hz':task['hz'],'camera_fps':profile.get('camera',{}).get('fps'),'camera_frame_age_ms':None,'camera_max_frame_age_ms':None,'camera_frame_sequence':None,
                'late_intervals':late_intervals,'late_interval_percent':round(100*late_intervals/max(1,steps-1),2)}
        if timings is not None:result.update(timings.metrics())
        if frame_reader is not None:result.update(frame_reader.metrics())
        result['camera_contract']=inputs
        if head_reader is not None:
            result['head_camera_metrics']=head_reader.metrics()
            if frame_reader is not None and frame_reader.received_at is not None and head_reader.received_at is not None:
                result['camera_arrival_skew_ms']=round(abs(frame_reader.received_at-head_reader.received_at)*1000,1)
        if player is not None:result.update(player.metrics())
        elif rtc_queue is not None:result.update(rtc_queue.metrics())
        return result
    try:
        from execution_timing import LatestFrameReader,TimedSyncPlayer,TimedRTCQueue,LoopTiming
        timings=LoopTiming()
        if reason():raise Interrupted(reason())
        if cached_camera:
            # Camera is already connected for preview; fail before enabling motors
            # if its cache is unavailable, unsupported, invalid, or stale.
            frame_reader=LatestFrameReader(camera)
            frame_reader.latest()
        if inputs['mode']==camera_contract.DUAL:
            if secondary_camera is None:raise ValueError('双视角模型需要第三视角相机，尚未连接。')
            head_reader=LatestFrameReader(secondary_camera)
            head_reader.latest()  # Both views must be healthy before enabling motors.
        policy=policy_factory(task)
        if reason():raise Interrupted(reason())
        update('connecting','正在连接从臂；随后执行页面所示指令。',task_id=task['task_id'],prompt=task['prompt'],continuous=task.get('continuous',False),rtc_enabled=task.get('rtc',{}).get('enabled',False),rtc_config=task.get('rtc',{}),steps=0,elapsed=0,**metrics())
        robot=robot_factory(profile,relative_limits(task))
        from working_pose import selection,preset,validate_robot,move_to_ready
        startup=selection(task.get('startup_pose','horizontal'))
        ready_pose=preset() if startup!='horizontal' else None
        if ready_pose is not None:validate_robot(robot,ready_pose)
        def configure_command_timing():
            if cached_camera and task['hz']>LEGACY_MAX_COMMAND_HZ:
                # Keep the existing high-rate gripper adaptation. Startup itself
                # uses the original 50 Hz driver path, before this opt-in.
                configure_timing=getattr(robot,'configure_command_timing',None)
                if callable(configure_timing):configure_timing(task['hz'])
        if ready_pose is None:configure_command_timing()
        robot.connect(calibrate=False);connected=True
        if ready_pose is not None:
            preview_due=[-float('inf')]
            def startup_check():
                why=reason()
                if why:raise Interrupted(why)
                image=frame_reader.latest() if frame_reader is not None else camera.async_read(timeout_ms=1000)
                if head_reader is not None:head_reader.latest()
                if time.monotonic()-preview_due[0]>=.2:
                    preview(image);preview_due[0]=time.monotonic()
            def startup_update(**progress):
                update('positioning','正在缓慢进入官方居中待命位；等待关节实际到位。',
                       startup_pose=startup,startup_progress=progress,steps=0,elapsed=0)
            startup_update(progress=0,max_error_deg=None,elapsed=0)
            startup_result=move_to_ready(robot,ready_pose,startup_check,startup_update)
            update('executing','已确认到达官方待命位，正在获取模型首段动作。',
                   startup_pose=startup,startup_pose_result=startup_result,steps=0,elapsed=0)
            configure_command_timing()
        start=time.monotonic();chunk=[];index=0;preview_at=0;next_tick=start
        if timed:
            if task.get('rtc',{}).get('enabled'):
                player=rtc_queue=TimedRTCQueue(policy,task['rtc'],policy_hz)
            else:player=TimedSyncPlayer(policy,policy_hz,task['chunk'])
        elif task.get('rtc',{}).get('enabled'):
            from rtc.runtime import RTCQueue
            rtc_queue=RTCQueue(policy,task['rtc'])
        while True:
            tick=time.monotonic();why=reason()
            if why:raise Interrupted(why)
            if not task.get('continuous',False) and tick-start>=task['seconds']:outcome='timeout';normal=True;break
            raw=timings.call('observe',robot.get_observation)
            image=timings.call('frame',frame_reader.latest) if cached_camera else timings.call('frame',lambda:camera.async_read(timeout_ms=1000))
            head=timings.call('head_frame',head_reader.latest) if head_reader is not None else None
            obs=policy_observation(raw,image,task['prompt'],head)
            infer_ms=None
            if player is not None:
                next_action=timings.call('policy',player.action_at,obs,reason)
                infer_ms=player.infer_ms
            elif rtc_queue is not None:
                next_action=timings.call('policy',rtc_queue.next_action,obs,reason)
                infer_ms=rtc_queue.infer_ms
            else:
                if index>=len(chunk):
                    t=time.monotonic();result=timings.call('policy',policy.infer,obs,reason);infer_ms=(time.monotonic()-t)*1000
                    chunk=action_chunk(result,task['chunk']);index=0
                next_action=chunk[index];index+=1
            why=reason()
            if why:raise Interrupted(why)
            if not task.get('continuous',False) and time.monotonic()-start>=task['seconds']:outcome='timeout';normal=True;break
            if frame_reader is not None:
                # A blocking initial/synchronous inference must not bypass frame
                # freshness protection. This poll never waits for a new image.
                image=timings.call('frame',frame_reader.latest)
            if head_reader is not None:timings.call('head_frame',head_reader.latest)
            timings.call('send',robot.send_action,{name+'.pos':float(v) for name,v in zip(JOINTS,next_action)});steps+=1
            sent=time.monotonic()
            if first_send is None:first_send=sent
            if last_send is not None:
                gap=sent-last_send;max_gap=max(max_gap,gap)
                if gap>1.5/task['hz']:late_intervals+=1
            last_send=sent
            control_hz=round((steps-1)/(sent-first_send),2) if sent>first_send else 0.0
            if tick-preview_at>=.2:
                timings.call('preview',preview,image);update('executing',('持续执行中：不断观察并重新推理，直到手动结束。' if task.get('continuous',False) else '正在执行；任务成功需要你人工确认。'),task_id=task['task_id'],prompt=task['prompt'],continuous=task.get('continuous',False),rtc_enabled=task.get('rtc',{}).get('enabled',False),rtc_config=task.get('rtc',{}),steps=steps,elapsed=round(time.monotonic()-start,1),control_hz=control_hz,max_action_gap_ms=round(max_gap*1000,1),**metrics(),**({'infer_ms':round(infer_ms,1)} if player is None and rtc_queue is None and infer_ms is not None else {}));preview_at=tick
            if rtc_queue is None and not cached_camera:time.sleep(max(0,1/task['hz']-(time.monotonic()-tick)))
            else:
                next_tick+=1/task['hz']
                now=time.monotonic()
                if next_tick<now:next_tick=now+1/task['hz']  # Never burst overdue commands.
                time.sleep(max(0,next_tick-now))
    except Interrupted as exc:
        outcome=exc.reason;normal=outcome in ('success','stopped')
        if not normal:errors.append('页面失联或会话关闭，停止执行。')
    except ArrivalTimeout as exc:
        # A healthy arm already near the ready pose can use the existing normal
        # parking path. Feedback/transport/protection failures never enter here.
        errors.append(str(exc))
        startup_result={**exc.diagnostics,'id':startup,'label':ready_pose['label'],
                        'verified':False,'failure':str(exc)}
        if exc.near_target and connected and getattr(robot,'_emergency_disable_requested',False) is not True:
            try:
                why=reason()
                if why not in (None,'stopped','success'):raise RuntimeError('页面或设备会话已断开。')
                if frame_reader is not None:frame_reader.latest()
                from working_pose import measured
                current,_=measured(robot)
                if max(abs(current[n]-ready_pose['motor_degrees'][n]) for n in ready_pose['motor_degrees'])>ready_pose['tolerance_deg']:
                    raise RuntimeError('关节已离开待命位附近。')
                if getattr(robot,'_emergency_disable_requested',False) is True:raise RuntimeError('驱动已触发保护性失能。')
                startup_timeout_return=True
            except BaseException as guard_error:
                errors.append('未执行自动回位：'+str(guard_error))
    except BaseException as exc:
        traceback.print_exc();errors.append(str(exc) or type(exc).__name__)
    finally:
        return_attempted=connected and (normal or startup_timeout_return)
        try:update('returning',('待命位确认超时，模型未启动；正在缓慢回水平位再失能。' if startup_timeout_return else '正在缓慢回到原有水平位并失能。') if return_attempted else '停止下发动作并清理设备；异常不强行回零。')
        finally:
            if rtc_queue is not None:
                try:rtc_queue.close()
                except Exception as exc:errors.append('RTC 退出失败：'+str(exc))
            cleanup_errors=cleanup(robot,None,return_attempted)
            errors.extend(cleanup_errors)
            return_completed=return_attempted and not cleanup_errors and getattr(robot,'_emergency_disable_requested',False) is not True
            if robot is not None and getattr(robot,'_emergency_disable_requested',False) is True:
                errors.append('驱动触发保护性失能，回零未确认完成。')
            if policy is not None:
                try:policy.close()
                except Exception as exc:errors.append(str(exc))
    if not connected and outcome=='success':errors.append('尚未完成设备连接，不能确认任务成功。')
    if errors:outcome='error'
    result={**task,**metrics(),'startup_pose_result':startup_result,'return_attempted':return_attempted,'return_completed':return_completed,'control_hz':round((steps-1)/(last_send-first_send),2) if last_send is not None and last_send>first_send else 0.0,'max_action_gap_ms':round(max_gap*1000,1),'late_intervals':late_intervals,'outcome':outcome,'errors':errors,'steps':steps,'elapsed':round(time.monotonic()-start,2),'finished_at':time.time(),
            'message':{'success':'OK · 人工确认任务完成，已回水平位并失能','stopped':'已手动停止，已回水平位并失能','timeout':'到时结束，已回水平位并失能；任务是否成功未确认','error':'执行异常；请检查设备，未确认已安全回位'}[outcome]}
    if not connected and outcome=='stopped':result['message']='已取消，尚未完成从臂连接。'
    if startup_result is not None and not startup_result['verified']:
        result['message']='待命位确认未通过，模型未启动；'+('已按原流程回水平位并失能' if return_completed else '未确认已安全回位')
    return result


def run(folder,request,camera,secondary_camera=None):
    state={'id':request['id'],'mode':'inference','supports_continuous':True,'supports_separate_limits':True,'supports_rtc':True,'supports_timed_actions':True,'supports_native_actions':True,'supports_working_pose':True,'working_pose_version':WORKING_POSE_VERSION,'max_command_hz':MAX_COMMAND_HZ,'state':'starting','message':'正在打开相机…'};stop=[]
    publisher=None
    head_publisher=None
    state['camera_views']=['wrist_rgb']+(['head_rgb'] if secondary_camera is not None else [])
    def update(phase,message,**extra):
        state.update(state=phase,message=message,**extra)
        if publisher is not None:state.update(publisher.metrics())
        if head_publisher is not None:state['head_preview']=head_publisher.metrics()
        write_json(folder/'status.json',state)
        if phase=='positioning' and 'startup_progress' in extra:
            with (folder/('startup_'+state['task_id']+'.jsonl')).open('a') as trace:
                trace.write(json.dumps({'time':time.time(),**extra['startup_progress']},ensure_ascii=False)+'\n')
    def session_reason():
        if stop or (folder/'stop').exists() or time.time()-(folder/'heartbeat').stat().st_mtime>15:return 'disconnected'
    signal.signal(signal.SIGTERM,lambda *_:stop.append(True));signal.signal(signal.SIGINT,lambda *_:stop.append(True))
    try:
        if session_reason():raise RuntimeError('会话已取消。')
        camera.connect()
        if secondary_camera is not None:
            secondary_camera.connect()
            (folder/'head').mkdir(exist_ok=True)
            head_publisher=PreviewPublisher(secondary_camera,folder/'head',request['profile']['secondary_camera'].get('fps',30))
            head_publisher.start()
        publisher=PreviewPublisher(camera,folder,request['profile'].get('camera',{}).get('fps',30))
        publisher.start()
        update('preview','相机预览就绪；电机未连接。可以填写下一条指令。')
        while not session_reason():
            camera.async_read(timeout_ms=1000)
            command=folder/'command.json'
            if command.exists():
                task=json.loads(command.read_text())
                update('queued','已接收指令。',task_id=task['task_id'],prompt=task['prompt'],continuous=task.get('continuous',False),rtc_enabled=task.get('rtc',{}).get('enabled',False),rtc_config=task.get('rtc',{}),timing_mode=task.get('timing_mode','legacy'),interpolation=task.get('timing_mode')=='timed',policy_hz=task.get('policy_hz',30),target_command_hz=task['hz'],startup_pose=task.get('startup_pose','horizontal'),startup_pose_result=None,startup_progress=None,steps=0,elapsed=0,infer_ms=None)
                command.unlink()
                def reason():
                    if session_reason():return 'disconnected'
                    end=folder/'end.json'
                    if end.exists():
                        d=json.loads(end.read_text())
                        if d.get('task_id')==task['task_id']:return d['reason']
                # UI encoding runs independently, including while inference is
                # blocked. The control loop's preview callback does no work.
                extra={'secondary_camera':secondary_camera} if secondary_camera is not None else {}
                result=execute(task,request['profile'],camera,reason,update,lambda _image:None,**extra)
                write_json(folder/('result_'+str(time.time_ns())+'.json'),result)
                if result['outcome']=='error':
                    state.update(last_result=result,startup_pose_result=result.get('startup_pose_result'))
                    raise RuntimeError(result['message']+'；'+'；'.join(result['errors']))
                update('preview',result['message']+'。可重新摆放物体后执行下一条。',last_result=result)
            else:update('preview',state['message'])
            time.sleep(.2)
        update('completed','预览已关闭；没有正在执行的任务。')
    except BaseException as exc:
        traceback.print_exc();update('error',str(exc) or type(exc).__name__)
    finally:
        if publisher is not None:publisher.close()
        if head_publisher is not None:head_publisher.close()
        if secondary_camera is not None:
            try:
                if secondary_camera.is_connected:secondary_camera.disconnect()
            except BaseException as exc:update('error','第三视角退出失败：'+str(exc))
        try:
            if camera.is_connected:camera.disconnect()
        except BaseException as exc:update('error','相机退出失败：'+str(exc))


if __name__=='__main__':
    folder=Path(sys.argv[1]);os.fstat(int(sys.argv[2]));request=json.loads((folder/'request.json').read_text())
    try:
        from lerobot.cameras.orbbec.configuration_orbbec import OrbbecColorCameraConfig
        from lerobot.cameras.orbbec.camera_orbbec import OrbbecColorCamera
        camera=OrbbecColorCamera(OrbbecColorCameraConfig(**request['profile']['camera']))
        secondary=OrbbecColorCamera(OrbbecColorCameraConfig(**request['profile']['secondary_camera'])) if request['profile'].get('secondary_enabled') else None
        run(folder,request,camera,secondary)
    except BaseException as exc:
        traceback.print_exc();write_json(folder/'status.json',{'id':request['id'],'mode':'inference','state':'error','message':str(exc)})
