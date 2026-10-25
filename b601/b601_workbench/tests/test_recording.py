import fcntl
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recording
import app


class RecordingTests(unittest.TestCase):
    def test_validation_rejects_unprepared_invalid_and_extra_fields(self):
        for data in [{}, {'task':'a','prepared':False}, {'task':'a','prepared':True,'seconds':float('nan')}, {'task':'a','prepared':True,'seconds':-1}, {'task':'a','prepared':True,'root':'/tmp/override'}]:
            with self.assertRaises(ValueError): recording.validate_request(data)
        self.assertEqual(recording.validate_request({'task':' a ','prepared':True,'seconds':0}),('a',0))

    def test_restart_preserves_active_job_and_stale_stop_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(recording,'JOBS',Path(temp)):
            folder=Path(temp)/('a'*32); folder.mkdir()
            (Path(temp)/'current').write_text(folder.name)
            recording.write_json(folder/'status.json',{'id':folder.name,'state':'recording'})
            with (Path(temp)/'device.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertTrue(recording.status()['active'])
                with self.assertRaises(ValueError):recording.control({'id':'wrong'},'stop')
                self.assertFalse((folder/'stop').exists())
                recording.control({'id':folder.name},'stop')
                self.assertTrue((folder/'stop').exists())
                with patch('library.collection',return_value={'id':'test'}), patch('can_control.read_state',return_value={'up':True,'bitrate':1000000,'bus_state':'ERROR-ACTIVE'}), patch.object(Path,'exists',return_value=True), patch.object(recording.subprocess,'Popen') as spawn:
                    with self.assertRaisesRegex(ValueError,'已有采集'):recording.start({'task':'test','prepared':True}, {})
                    spawn.assert_not_called()
            self.assertEqual(recording.status()['state'],'error')

    def test_record_http_auth_and_dispatch(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(recording,'start',return_value={'active':True}) as start:
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(origin+'/api/record/start',data=b'{}',method='POST'))
                self.assertEqual(error.exception.code,403);start.assert_not_called()
                data={'task':'example','seconds':30,'prepared':True}
                with urlopen(Request(origin+'/api/record/start',data=json.dumps(data).encode(),headers={'Origin':origin,'X-Workbench-Token':app.CSRF_TOKEN},method='POST')) as response:
                    self.assertEqual(response.status,202)
                self.assertEqual(start.call_args.args[0],data)
        finally:
            server.shutdown();server.server_close();thread.join()
