"""Serve the existing trained policy with a loopback-only, private ephemeral port."""
import asyncio
import dataclasses
import json
import os
from pathlib import Path
import sys
import threading
import time
from recording import write_json

folder=Path(sys.argv[1]);request=json.loads((folder/'request.json').read_text())
state=json.loads((folder/'status.json').read_text())
def update(phase,message,**extra):
    state.update(state=phase,message=message,**extra);write_json(folder/'status.json',state)
def watch():
    while True:
        if (folder/'stop').exists():
            os._exit(0)
        time.sleep(.2)
threading.Thread(target=watch,daemon=True).start()
try:
    import numpy as np
    from openpi.training import config
    from openpi.policies import policy_config
    from openpi.shared import normalize
    from openpi.serving.websocket_policy_server import WebsocketPolicyServer
    from websockets.asyncio.server import serve
    cfg=config.get_config('pi05_b601')
    import camera_contract
    inputs=camera_contract.validate(request.get('camera_contract',camera_contract.contract()))
    if inputs!=camera_contract.model(request['path']):raise ValueError('模型相机输入记录已变化，请重新选择模型。')
    if inputs['mode']==camera_contract.DUAL:
        from multi_view_config import with_dual_cameras
        cfg=with_dual_cameras(cfg)
    # Use this checkpoint's own normalization, including older dataset IDs.
    norms=normalize.load(Path(request['norm']).parent)
    policy=policy_config.create_trained_policy(cfg,request['path'],norm_stats=norms)
    update('warming','模型已加载，正在做无设备预热…')
    warm={view:np.zeros((480,640,3),dtype=np.uint8) for view in inputs['views']}
    warm.update(state=np.zeros(7,dtype=np.float32),prompt='Pick up the red-handled screwdriver and place it in the orange container.')
    result=policy.infer(warm)
    actions=np.asarray(result['actions'])
    if actions.ndim!=2 or actions.shape[1]!=7 or not np.isfinite(actions).all():raise ValueError('预热动作维度或数值无效。')
    # RTC initialization is isolated: baseline remains usable if optional warmup fails.
    rtc_ready=False;rtc_error=None
    try:
        from rtc.policy import RTCPolicy
        from rtc.config import DEFAULTS
        wrapped=RTCPolicy(policy)
        update('warming','原有推理已就绪，正在预热 RTC；此步骤不连接设备…')
        rtc_obs={**warm,
                 '_rtc':{'config':{**DEFAULTS,'enabled':True},'prefix':np.asarray(actions[15:]),'delay':5}}
        wrapped.infer(rtc_obs)
        policy=wrapped;rtc_ready=True
    except Exception as exc:
        import traceback;traceback.print_exc();rtc_error=str(exc)
    server=WebsocketPolicyServer(policy,host='127.0.0.1',port=0,metadata={'workbench_model_id':request['id'],'supports_rtc':rtc_ready,'camera_contract':inputs})
    async def run():
        async with serve(server._handler,'127.0.0.1',0,compression=None,max_size=8*1024*1024) as srv:
            update('ready','模型已就绪，可接收语言指令。',port=srv.sockets[0].getsockname()[1],rtc_ready=rtc_ready,rtc_error=rtc_error,camera_contract=inputs)
            await asyncio.Future()
    asyncio.run(run())
except BaseException as exc:
    import traceback;traceback.print_exc();update('error',str(exc) or type(exc).__name__)
