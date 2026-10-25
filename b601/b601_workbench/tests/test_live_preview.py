"""Preview concurrency and HTTP lifecycle checks; no hardware or ML imports."""
from http.server import ThreadingHTTPServer
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import inference
from live_preview import PreviewPublisher, stream_jpegs


class Pipeline:
    def __init__(self):
        self.frame_lock = threading.Lock()
        self.condition = threading.Condition(self.frame_lock)
        self.frames_seq = 0
        self.latest_frames = None
        self.last_frames_ts = None

    def feed(self, value):
        with self.condition:
            self.frames_seq += 1
            self.latest_frames = value
            self.last_frames_ts = time.time()
            self.condition.notify_all()

    def wait_for_next_frames(self, sequence, timeout):
        with self.condition:
            self.condition.wait_for(lambda: self.frames_seq != sequence, timeout)


class Camera:
    def __init__(self):
        self.pipeline = Pipeline()

    def _read_from_frames(self, frames):
        return frames

    def async_read(self, **kwargs):
        raise AssertionError('Preview must not consume the control reader')


def until(predicate, timeout=2):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('Timed out waiting for preview')
        time.sleep(.005)


class PublisherTests(unittest.TestCase):
    def test_each_camera_frame_once_and_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera = Camera()
            publisher = PreviewPublisher(camera, tmp, encoder=lambda image: str(image).encode())
            publisher.start()
            try:
                camera.pipeline.feed(7)
                until(lambda: publisher.metrics()['preview_frames'] == 1)
                time.sleep(.05)
                self.assertEqual(publisher.metrics()['preview_frames'], 1)
                camera.pipeline.feed(8)
                until(lambda: publisher.metrics()['preview_frames'] == 2)
                self.assertEqual((Path(tmp)/'preview.jpg').read_bytes(), b'8')
                metadata = json.loads((Path(tmp)/'preview_meta.json').read_text())
                self.assertEqual(metadata['id'], Path(tmp).name)
            finally:
                publisher.close()
            self.assertFalse(publisher.thread.is_alive())

    def test_slow_encoder_skips_old_frames_without_holding_camera_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera = Camera(); entered = threading.Event(); release = threading.Event(); seen = []
            def encode(image):
                seen.append(image)
                if image == 0:
                    entered.set(); release.wait(2)
                return str(image).encode()
            publisher = PreviewPublisher(camera, tmp, encoder=encode)
            publisher.start()
            try:
                camera.pipeline.feed(0)
                self.assertTrue(entered.wait(1))
                # This would deadlock if encoding held the SDK cache lock.
                for value in range(1, 50):camera.pipeline.feed(value)
                release.set()
                until(lambda: publisher.metrics()['preview_frames'] == 2)
                self.assertEqual(seen, [0, 49])
            finally:
                release.set(); publisher.close()

    def test_session_reads_camera_metrics_during_a_blocked_control_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'preview_meta.json').write_text(json.dumps({
                'id': folder.name, 'image_at': 123, 'preview_fps': 29.9,
                'state': 'executing', 'active': False,
            }))
            state = {'id':folder.name, 'mode':'inference', 'active':True,
                     'state':'preview', 'preview_stream':True, 'image_at':1}
            with patch.object(inference.recording,'status',return_value=state), \
                 patch.object(inference.recording,'current_dir',return_value=folder):
                result = inference.session()
            self.assertEqual(result['image_at'],123)
            self.assertEqual(result['preview_fps'],29.9)
            self.assertEqual(result['state'],'preview')
            self.assertTrue(result['active'])


class StreamTests(unittest.TestCase):
    def test_frozen_frame_closes_stream_instead_of_repeating_old_images(self):
        class Connection:
            def gettimeout(self):return None
            def settimeout(self, value):pass
            def setsockopt(self, *args):pass
        class Handler:
            connection=Connection()
            wfile=io.BytesIO()
            def send_response(self, code):pass
            def send_header(self, *args):pass
            def end_headers(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'preview.jpg').write_bytes(b'only-frame')
            handler=Handler();start=time.monotonic()
            stream_jpegs(handler,tmp,lambda:True,stale_s=.05)
            self.assertLess(time.monotonic()-start,.5)
            self.assertEqual(handler.wfile.getvalue().count(b'Content-Type: image/jpeg'),1)

    def test_http_stream_session_binding_frames_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/('a'*32);folder.mkdir()
            state = {'id':folder.name,'active':True,'mode':'inference'}
            def write(data):
                temp=folder/'preview.tmp';temp.write_bytes(data);temp.replace(folder/'preview.jpg')
            def part(response):
                while response.readline().strip()!=b'--b601frame':pass
                headers={}
                while True:
                    line=response.readline().strip()
                    if not line:break
                    key,value=line.decode().split(':',1);headers[key]=value.strip()
                return headers,response.read(int(headers['Content-Length']))
            with patch.object(app.inference,'session',side_effect=lambda:dict(state)), \
                 patch.object(app.recording,'current_dir',return_value=folder):
                server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                try:
                    conn.request('GET','/api/inference/stream?id='+('b'*32))
                    response=conn.getresponse();self.assertEqual(response.status,404);response.read()
                    write(b'frame-one')
                    conn.request('GET','/api/inference/stream?id='+folder.name)
                    response=conn.getresponse();self.assertEqual(response.status,200)
                    self.assertIn('multipart/x-mixed-replace',response.getheader('Content-Type'))
                    self.assertIn('no-store',response.getheader('Cache-Control'))
                    first,data=part(response);self.assertEqual(data,b'frame-one')
                    write(b'frame-two')
                    second,data=part(response);self.assertEqual(data,b'frame-two')
                    self.assertNotEqual(first['X-Frame-Id'],second['X-Frame-Id'])
                    state['active']=False
                    self.assertEqual(response.read(),b'\r\n')
                finally:
                    state['active']=False;conn.close();server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
