import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import capture_pipeline
import library
import recording
import record_worker
from types import SimpleNamespace
from unittest.mock import Mock

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.patch=patch.object(library,'DB',self.root/'library.sqlite3');self.patch.start();self.addCleanup(self.patch.stop)
        self.group=library.create('series','task')
        self.folder=self.root/'job';self.folder.mkdir();(self.folder/'heartbeat').touch()
        self.worker=self.root/'fake_capture.py'
        self.worker.write_text("import json,sys\nfrom pathlib import Path\nf=Path(sys.argv[1]); r=json.loads((f/'request.json').read_text()); (f/'preview.jpg').write_bytes(b'fake'); (f/'status.json').write_text(json.dumps({**r,'state':'completed','message':'saved','saved':True,'frames':1}))\n")
        self.request={'id':'a'*32,'dataset':'raw_demo','root':str(self.root/'raw_demo'),'collection':self.group['id'],'task':'task','seconds':1,'series_count':2,'rest_seconds':0,'auto_convert':False,'pipeline_config':{'dataset_root':str(self.root),'lerobot_python':sys.executable}}
        previous=[signal.getsignal(signal.SIGINT),signal.getsignal(signal.SIGTERM)]
        self.addCleanup(signal.signal,signal.SIGTERM,previous[1]);self.addCleanup(signal.signal,signal.SIGINT,previous[0])

    def run_pipeline(self,request):
        with (self.folder/'lock').open('w') as lock:capture_pipeline.run(self.folder,lock.fileno(),request,self.worker)
        return json.loads((self.folder/'status.json').read_text())

    def test_each_episode_has_separate_source_and_only_one_current_job(self):
        result=self.run_pipeline(self.request)
        self.assertEqual(result['state'],'completed');self.assertEqual(result['completed_datasets'],['raw_demo','raw_demo_s002'])
        a=json.loads((self.folder/'episodes/001/request.json').read_text());b=json.loads((self.folder/'episodes/002/request.json').read_text())
        self.assertNotEqual(a['root'],b['root']);self.assertNotIn('pipeline_config',a)
        self.assertEqual((self.folder/'episodes/002/heartbeat').resolve(),self.folder/'heartbeat')

    def test_stop_before_connect_never_spawns_worker(self):
        (self.folder/'stop').touch()
        with patch.object(capture_pipeline.subprocess,'Popen') as proc:result=self.run_pipeline(self.request)
        proc.assert_not_called();self.assertFalse(result['saved']);self.assertEqual(result['completed_datasets'],[])

    def test_request_validation_rejects_unsafe_batch_options(self):
        base={'task':'task','prepared':True,'seconds':1}
        for extra in ({'series_count':True},{'series_count':2,'seconds':0},{'rest_seconds':-1},{'auto_convert':'yes'}):
            with self.assertRaises(ValueError):recording.validate_request({**base,**extra})
        self.assertEqual(recording.validate_request({**base,'series_count':2,'auto_convert':True}),('task',1))

    def test_conversion_begins_only_after_recording_child_completed(self):
        request={**self.request,'series_count':1,'auto_convert':True}
        def start(*args):
            self.assertEqual(json.loads((self.folder/'episodes/001/status.json').read_text())['state'],'completed')
            return {'id':'copy'}
        with patch.object(capture_pipeline.experiments,'start',side_effect=start),patch.object(capture_pipeline.experiments,'status',return_value={'active':False,'state':'completed','dataset_output':'converted'}):
            result=self.run_pipeline(request)
        self.assertEqual(result['converted_datasets'],['converted'])

    def test_automatic_start_reads_alignment_without_sending(self):
        robot=Mock();leader=Mock();robot.config=SimpleNamespace(joint_directions={'arm':-1,'gripper':6},joint_limits={'arm':[-90,90],'gripper':[0,300]})
        robot.motors={'arm':Mock(),'gripper':Mock()}
        for motor in robot.motors.values():motor.get_state.return_value=SimpleNamespace(pos=0.)
        leader.get_action.return_value={'arm.pos':2.,'gripper.pos':10.}
        record_worker.check_batch_alignment(robot,leader)
        robot.send_action.assert_not_called()
        leader.get_action.return_value={'arm.pos':20.,'gripper.pos':10.}
        with self.assertRaisesRegex(RuntimeError,'姿态不一致'):record_worker.check_batch_alignment(robot,leader)
        robot.send_action.assert_not_called()

if __name__=='__main__':unittest.main()
