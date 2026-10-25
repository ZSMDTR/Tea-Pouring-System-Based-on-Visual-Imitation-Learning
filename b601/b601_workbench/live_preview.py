"""Latest-frame camera preview, independent of policy inference and motor ticks.

Only the publisher thread converts and encodes images. The web process uses
standard-library code to stream its atomically replaced JPEG; slow clients skip
old frames instead of creating a queue. Importing this module opens no devices.
"""
from collections import deque
import json
import os
from pathlib import Path
import socket
import threading
import time


STREAM_SLOTS = threading.BoundedSemaphore(4)


def view_folder(folder, role='wrist'):
    if role not in ('wrist','head'):
        raise ValueError('无效的相机视角。')
    return Path(folder)/'head' if role=='head' else Path(folder)


def encode_jpeg(image):
    import cv2
    ok, data = cv2.imencode('.jpg', cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                            [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise RuntimeError('预览图像编码失败。')
    return data.tobytes()


class PreviewPublisher:
    def __init__(self, camera, folder, fps=30, encoder=encode_jpeg):
        self.camera = camera
        self.folder = Path(folder)
        self.fps = fps
        self.encoder = encoder
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.times = deque()
        self.metadata_at = 0.0
        self.state = {'preview_stream': True, 'preview_target_fps': fps,
                      'preview_fps': 0.0, 'preview_frames': 0,
                      'image_at': None, 'preview_error': None}

    def start(self):
        pipeline = self.camera.pipeline
        if not callable(getattr(pipeline, 'wait_for_next_frames', None)):
            raise ValueError('当前相机不支持独立实时预览。')
        self.thread = threading.Thread(target=self._run, name='CameraPreview', daemon=True)
        self.thread.start()

    def _run(self):
        pipeline = self.camera.pipeline
        sequence = -1
        while not self.stop.is_set():
            try:
                pipeline.wait_for_next_frames(sequence, .2)
                if self.stop.is_set():
                    break
                # Snapshot only; do not consume async_read's sequence or hold the
                # camera lock during conversion, encoding, or file operations.
                with pipeline.frame_lock:
                    frames = pipeline.latest_frames
                    current = pipeline.frames_seq
                    arrived = pipeline.last_frames_ts
                if current == sequence:
                    continue
                sequence = current
                if frames is None:
                    continue
                if arrived is None or time.time() - arrived > 1:
                    continue
                image = self.camera._read_from_frames(frames)
                if image is None:
                    continue
                data = self.encoder(image)
                if self.stop.is_set():
                    break
                temp = self.folder / 'preview.tmp'
                temp.write_bytes(data)
                temp.replace(self.folder / 'preview.jpg')
                now = time.monotonic()
                with self.lock:
                    self.times.append(now)
                    while len(self.times) > 1 and self.times[0] < now - 2:
                        self.times.popleft()
                    rate = ((len(self.times) - 1) / (now - self.times[0])
                            if len(self.times) > 1 and now > self.times[0] else 0.0)
                    self.state.update(image_at=arrived, preview_fps=round(rate, 1),
                                      preview_frames=self.state['preview_frames'] + 1,
                                      preview_error=None)
                    metadata = dict(self.state)
                if now - self.metadata_at >= .2:
                    temp = self.folder / 'preview_meta.tmp'
                    temp.write_text(json.dumps({'id': self.folder.name, **metadata}))
                    temp.replace(self.folder / 'preview_meta.json')
                    self.metadata_at = now
            except Exception as exc:
                with self.lock:
                    self.state['preview_error'] = str(exc)
                self.stop.wait(.1)

    def metrics(self):
        with self.lock:
            return dict(self.state)

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1)


def stream_jpegs(handler, folder, alive, stale_s=2):
    """Serve a bounded, uncached MJPEG connection; never read the camera SDK."""
    if not STREAM_SLOTS.acquire(blocking=False):
        return handler.reply({'error': '预览连接过多，请关闭多余的预览页面。'}, 429)
    path = Path(folder) / 'preview.jpg'
    previous = None
    checked_at = 0.0
    last_frame = time.monotonic()
    old_timeout = handler.connection.gettimeout()
    try:
        handler.close_connection = True
        handler.connection.settimeout(1)
        handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        handler.send_response(200)
        handler.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=b601frame')
        handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        while True:
            now = time.monotonic()
            if now - checked_at >= .25:
                if not alive():
                    break
                checked_at = now
            try:
                stamp = path.stat().st_mtime_ns
                if stamp != previous:
                    with path.open('rb') as frame:
                        stamp = os.fstat(frame.fileno()).st_mtime_ns
                        data = frame.read()
                    if stamp != previous:
                        # An existing but frozen JPEG must not appear live when
                        # reconnecting after a stopped/failed camera producer.
                        if time.time_ns() - stamp > stale_s * 1e9:
                            break
                        header = (f'--b601frame\r\nContent-Type: image/jpeg\r\n'
                                  f'Content-Length: {len(data)}\r\n'
                                  f'X-Frame-Id: {stamp}\r\n\r\n').encode()
                        handler.wfile.write(header + data + b'\r\n')
                        handler.wfile.flush()
                        previous = stamp
                        last_frame = now
            except FileNotFoundError:
                pass
            if now - last_frame > stale_s:
                break
            time.sleep(.004)
    except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
        pass
    finally:
        handler.connection.settimeout(old_timeout)
        STREAM_SLOTS.release()
