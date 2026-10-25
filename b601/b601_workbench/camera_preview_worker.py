"""Explicit camera preview session. No robot or motor imports."""
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback
from recording import write_json
from live_preview import PreviewPublisher


def main(folder, lock_fd):
    os.fstat(lock_fd)
    request=json.loads((folder/'request.json').read_text());stop=[];camera=publisher=None
    secondary=secondary_publisher=None
    def update(state,message,**extra):
        write_json(folder/'status.json',{**request,'state':state,'message':message,**extra})
    signal.signal(signal.SIGINT,lambda *_:stop.append(True));signal.signal(signal.SIGTERM,lambda *_:stop.append(True))
    def stopped():
        return stop or (folder/'stop').exists() or time.time()-(folder/'heartbeat').stat().st_mtime>15
    try:
        from lerobot.cameras.orbbec.configuration_orbbec import OrbbecColorCameraConfig
        from lerobot.cameras.orbbec.camera_orbbec import OrbbecColorCamera
        if stopped():raise RuntimeError('预览已取消。')
        camera=OrbbecColorCamera(OrbbecColorCameraConfig(**request['camera']))
        camera.connect()
        if request.get('role')=='both':
            secondary=OrbbecColorCamera(OrbbecColorCameraConfig(**request['secondary_camera']))
            secondary.connect()
            (folder/'head').mkdir(exist_ok=True)
            secondary_publisher=PreviewPublisher(secondary,folder/'head',request['secondary_camera']['fps'])
            secondary_publisher.start()
        publisher=PreviewPublisher(camera,folder,request['camera']['fps']);publisher.start()
        while not stopped():
            camera.async_read(timeout_ms=1000)
            if secondary is not None:secondary.async_read(timeout_ms=1000)
            update('preview','双相机预览中；电机未连接。' if secondary is not None else '相机预览中；电机未连接。',
                   **publisher.metrics(),head_preview=secondary_publisher.metrics() if secondary_publisher else None)
            time.sleep(.1)
        update('completed','相机预览已关闭。')
    except BaseException as exc:
        traceback.print_exc();update('error',str(exc) or type(exc).__name__)
    finally:
        if publisher is not None:publisher.close()
        if secondary_publisher is not None:secondary_publisher.close()
        if secondary is not None:
            try:
                if secondary.is_connected:secondary.disconnect()
            except BaseException as exc:update('error','第三视角关闭失败：'+str(exc))
        if camera is not None:
            try:
                if camera.is_connected:camera.disconnect()
            except BaseException as exc:update('error','相机关闭失败：'+str(exc))


if __name__=='__main__':main(Path(sys.argv[1]),int(sys.argv[2]))
