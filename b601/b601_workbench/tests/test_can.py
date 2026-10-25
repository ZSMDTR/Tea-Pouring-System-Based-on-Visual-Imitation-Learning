"""All privileged operations mocked; never modifies the host CAN interface."""
from pathlib import Path
import json
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import can_control as can


def state(up=False, bitrate=None, bus='STOPPED', kind='can'):
    return SimpleNamespace(returncode=0, stdout=json.dumps([{'flags':['UP'] if up else [], 'linkinfo':{'info_kind':kind,'info_data':{'bittiming':{'bitrate':bitrate},'state':bus}}}]), stderr='')

OK = SimpleNamespace(returncode=0, stdout='', stderr='')

class CanTests(unittest.TestCase):
    def test_start_down_interface_only_fixed_commands(self):
        run=Mock(side_effect=[state(),OK,OK,state(True,1000000,'ERROR-ACTIVE')])
        self.assertTrue(can.start_can(run)['state']['up'])
        self.assertEqual(run.call_args_list[1].args[0], ['/usr/sbin/ip','link','set','dev','can0','type','can','bitrate','1000000'])
        self.assertEqual(run.call_args_list[2].args[0], ['/usr/sbin/ip','link','set','dev','can0','up'])

    def test_existing_correct_channel_is_not_restarted(self):
        run=Mock(return_value=state(True,1000000,'ERROR-ACTIVE'))
        can.start_can(run); self.assertEqual(run.call_count,1)

    def test_active_wrong_rate_busoff_or_noncan_not_reconfigured(self):
        for reply in [state(True,500000,'ERROR-ACTIVE'),state(True,1000000,'BUS-OFF'),state(kind='veth')]:
            run=Mock(return_value=reply)
            with self.assertRaises(RuntimeError):can.start_can(run)
            self.assertEqual(run.call_count,1)

    def test_missing_adapter_and_failed_configuration(self):
        run=Mock(return_value=SimpleNamespace(returncode=1,stdout='',stderr='No device'))
        with self.assertRaisesRegex(RuntimeError,'未找到'):can.start_can(run)
        run=Mock(side_effect=[state(),SimpleNamespace(returncode=1,stderr='permission denied')])
        with self.assertRaisesRegex(RuntimeError,'permission denied'):can.start_can(run)
        self.assertEqual(run.call_count,2)

    def test_cancelled_auth_is_reported(self):
        with patch.object(can,'read_state',return_value={'up':False,'bitrate':None,'bus_state':'STOPPED'}), patch.object(app.subprocess,'run',return_value=SimpleNamespace(returncode=126,stdout='',stderr='cancelled')):
            app.run_can_start()
        self.assertFalse(app.can_job()['ok']); self.assertIn('取消',app.can_job()['message'])

    def test_duplicate_click_does_not_spawn_second_worker(self):
        with patch.object(app.threading,'Thread') as thread:
            app.CAN_JOB.update(running=False)
            app.begin_can_start();app.begin_can_start()
            self.assertEqual(thread.call_count,1)
        app.CAN_JOB.update(running=False)

    def test_http_requires_origin_and_token_and_no_body(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(app,'begin_can_start',return_value={'running':True}) as start:
                for headers,body,status in [({},None,403),({'Origin':'https://foreign.example','X-Workbench-Token':app.CSRF_TOKEN},None,403),({'Origin':origin,'X-Workbench-Token':app.CSRF_TOKEN},b'{}',400)]:
                    with self.assertRaises(HTTPError) as exc:urlopen(Request(origin+'/api/can/start',data=body,headers=headers,method='POST'))
                    self.assertEqual(exc.exception.code,status)
                start.assert_not_called()
                r=urlopen(Request(origin+'/api/can/start',headers={'Origin':origin,'X-Workbench-Token':app.CSRF_TOKEN},method='POST'))
                self.assertEqual(r.status,202);start.assert_called_once()
        finally:
            server.shutdown();server.server_close();t.join()

if __name__=='__main__': unittest.main()
