import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app,experiment_data,experiments,mode_worker,recording,modes

class ExperimentTests(unittest.TestCase):
    def test_contract_does_not_accept_wrong_order_or_failed_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'meta').mkdir()
            info={'codebase_version':'v3.0','fps':30,'total_frames':5,'total_episodes':1,'features':{k:{'shape':[7],'names':experiment_data.JOINTS} for k in ('action','observation.state')}}
            info['features']['observation.images.wrist_rgb']={'shape':[480,640,3],'dtype':'video'}
            def save(): (root/'meta/info.json').write_text(json.dumps(info))
            save();self.assertTrue(experiment_data.inspect(root)['compatible'])
            info['features']['action']['names']=list(reversed(experiment_data.JOINTS));save();self.assertFalse(experiment_data.inspect(root)['compatible'])
            info['features']['action']['names']=experiment_data.JOINTS;save();(root/'workbench_capture.json').write_text('{"saved":true,"state":"error"}')
            self.assertFalse(experiment_data.inspect(root)['compatible'])
    def test_deleted_or_rejected_source_cannot_start(self):
        for member in ({'trashed':True,'review':'keep'},{'trashed':False,'review':'reject'}):
            with patch.object(app,'dataset_path',return_value=Path('/tmp/demo')),patch.object(recording,'status',return_value={'active':False}),patch.object(app.library,'snapshot',return_value={'members':[{'dataset':'demo',**member}]}),patch.object(experiments,'start') as start:
                with self.assertRaises(ValueError):app.experiment_action('train',{'dataset':'demo'})
                start.assert_not_called()
    def test_running_experiment_prevents_source_deletion(self):
        with patch.object(experiments,'status',return_value={'active':True}),patch.object(app.recycle,'trash') as trash:
            with self.assertRaises(ValueError):app.recycle_action('trash',{'dataset':'demo'})
            trash.assert_not_called()
    def test_training_requires_ack_and_rejects_boolean_steps(self):
        with patch.object(experiment_data,'inspect',return_value={'compatible':True,'needs_conversion':False,'frames':20}):
            for data in ({'steps':10,'batch':1},{'acknowledged':True,'steps':True,'batch':1}):
                with self.assertRaises(ValueError):experiments.start('train',data,{},Path('/tmp/demo'))
    def test_mode_errors_restore_gains_before_cleanup(self):
        import signal
        handlers=[signal.getsignal(signal.SIGINT),signal.getsignal(signal.SIGTERM)]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp);(folder/'heartbeat').touch();robot=Mock();robot.config.mit_kp={'a':50};robot.send_action.side_effect=RuntimeError('synthetic failure')
                observed=[]
                controller=Mock();controller.send.side_effect=RuntimeError('synthetic failure')
                with patch.object(mode_worker,'create_gravity',return_value=controller),patch.object(mode_worker,'measured',return_value=({'a':0,'gripper':0},0)),patch.object(mode_worker,'cleanup',side_effect=lambda *a:observed.append(dict(robot.config.mit_kp)) or []):
                    mode_worker.run(folder,{'id':'fake','mode':'gravity'},robot,None,{})
                self.assertEqual(observed,[{'a':50}]);self.assertEqual(json.loads((folder/'status.json').read_text())['state'],'error')
        finally:
            signal.signal(signal.SIGINT,handlers[0]);signal.signal(signal.SIGTERM,handlers[1])
    def test_pose_save_requires_live_gravity_session(self):
        with patch.object(recording,'status',return_value={'id':'a','active':True,'mode':'teleop','state':'teleop'}):
            with self.assertRaises(ValueError):modes.save({'id':'a'})

if __name__=='__main__':unittest.main()
