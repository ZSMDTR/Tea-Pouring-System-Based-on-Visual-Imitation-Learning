"""Transactional edits, source preservation, binding checks and job lifecycle."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import library
import workspace_state as workspace
import maintenance
import monitoring
import device_settings
import data_exchange
import experiment_worker

class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.patches=[patch.object(library,'DB',self.root/'library.sqlite3'),patch.object(workspace,'DB',self.root/'workspace.sqlite3')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.group=library.create('A','task')
        library.add_members(self.group['id'],'demo',[{'index':0,'frames':3,'tasks':['task']},{'index':1,'frames':4,'tasks':['task']}])

    def test_batch_is_atomic_and_cannot_forge_replay(self):
        items=[{'dataset':'demo','episode':0},{'dataset':'missing','episode':2}]
        with self.assertRaises(ValueError):library.batch_update({'items':items,'change':{'review':'keep'}})
        self.assertTrue(all(m['review']=='unreviewed' for m in library.snapshot()['members']))
        items[1]={'dataset':'demo','episode':1}
        with self.assertRaises(ValueError):library.batch_update({'items':items,'change':{'review':'passed'}})
        self.assertEqual(library.batch_update({'items':items,'change':{'review':'keep'}})['updated'],2)
        self.assertTrue(all(m['review']=='keep' for m in library.snapshot()['members']))
        self.assertEqual(library.replay_attempts(),[])

    def test_project_switch_preserves_sources_and_action_identity(self):
        workspace.listing(library.snapshot()['collections'])
        p=workspace.project_action('create',{'name':'ForceVLA'})
        workspace.project_action('bind',{'id':p['id'],'collection':self.group['id']})
        workspace.project_action('select',{'id':p['id']})
        self.assertEqual(workspace.current(),p['id'])
        saved=workspace.save_action({'name':'ready','dataset':'demo','episode':0},'immutable')
        self.assertEqual(workspace.actions()[0]['identity'],'immutable')
        workspace.edit_action('update',{'id':saved['id'],'name':'ready2','note':'note'})
        workspace.project_action('select',{'id':'default'})
        self.assertEqual(workspace.actions(),[])
        self.assertEqual(len(library.snapshot()['members']),2)

    def test_camera_binding_rejects_duplicate_and_unseen_devices(self):
        existing={'fps':30,'leader_port':'/dev/ttyUSB0','camera':{'serial_number_or_name':'wrist','width':1280,'height':800,'fps':30}}
        cams=[{'serial':'wrist'},{'serial':'head'}]
        cfg={'serial_number_or_name':'head','width':1280,'height':720,'fps':30}
        valid=device_settings.validate({'secondary_enabled':True,'secondary_camera':cfg},existing,['/dev/ttyUSB0'],cams)
        self.assertEqual(valid['secondary_camera']['output_width'],640)
        self.assertNotIn('secondary_enabled',existing)
        for change in ({'secondary_enabled':True,'secondary_camera':{**cfg,'serial_number_or_name':'wrist'}},{'leader_port':'/etc/passwd'},{'camera':{**cfg,'fps':60}},{'camera':{**cfg,'serial_number_or_name':'unknown'}}):
            with self.assertRaises(ValueError):device_settings.validate(change,existing,['/dev/ttyUSB0'],cams)

    def test_log_curves_are_finite_and_bounded(self):
        job=self.root/'job';job.mkdir()
        (job/'worker.log').write_text('Step 0: loss=0.3450, grad_norm=1.2345\nStep 10: loss=1.2e-3\n')
        result=monitoring.curves(job)
        self.assertEqual(result['points'][1]['loss'],.0012)
        (job/'metrics.jsonl').write_text(''.join(json.dumps({'step':i,'loss':float('nan') if i==2 else i/1000})+'\n' for i in range(1000)))
        result=monitoring.curves(job)
        self.assertEqual(len(result['points']),600);self.assertEqual(result['points'][-1]['step'],999)
        json.dumps(result,allow_nan=False)

    def test_verified_backup_contains_sqlite_snapshot_without_source_edits(self):
        source=self.root/'app';(source/'data').mkdir(parents=True);(source/'web').mkdir()
        (source/'config.json').write_text('{"x":1}');(source/'web/index.html').write_text('page')
        db=source/'data/library.sqlite3'
        with sqlite3.connect(db) as c:c.execute('CREATE TABLE demo(n)');c.execute('INSERT INTO demo VALUES(7)')
        destination=self.root/'backups';destination.mkdir();datasets=self.root/'datasets';datasets.mkdir()
        config={'dataset_root':str(datasets),'checkpoint_root':str(self.root/'models'),'base_model':str(self.root/'base')}
        original=(source/'config.json').read_bytes()
        with patch.object(maintenance,'ROOT',source):
            result=maintenance.backup(destination,config)
            with self.assertRaises(ValueError):maintenance.backup(source/'data',config)
        target=Path(result['path']);manifest=json.loads((target/'manifest.json').read_text())
        self.assertFalse(manifest['contains_models']);self.assertEqual(len(manifest['files']),3)
        self.assertEqual(original,(source/'config.json').read_bytes())
        with sqlite3.connect(target/'workbench/data/library.sqlite3') as c:self.assertEqual(c.execute('SELECT n FROM demo').fetchone()[0],7)
        for row in manifest['files']:self.assertEqual(maintenance.digest(target/row['path']),row['sha256'])

    def test_redaction_and_transfer_require_explicit_object(self):
        value=maintenance.redacted({'token':'do-not-keep','serial_number_or_name':'abc','path':str(Path.home())+'/x abc'},{'camera':{'serial_number_or_name':'abc'}})
        self.assertNotIn('abc',json.dumps(value));self.assertNotIn('do-not-keep',json.dumps(value))
        for data in ({'repo':'user/repo'},{'repo':'../escape','acknowledged':True},{'repo':'user/repo','acknowledged':True,'revision':False}):
            with self.assertRaises(ValueError):data_exchange.options('import_hf',data,{})

    def test_cancellation_targets_only_the_created_child(self):
        job=self.root/'cancel_job';job.mkdir()
        def cancel():(job/'cancel').touch()
        timer=threading.Timer(.3,cancel);timer.start();self.addCleanup(timer.cancel)
        with (job/'lock').open('w') as lock,patch.object(experiment_worker,'OPENPI',self.root):
            with self.assertRaises(experiment_worker.Cancelled):
                experiment_worker.run_child([sys.executable,'-c','import signal,time; signal.signal(signal.SIGINT,lambda *_: exit(0)); time.sleep(5)'],job,lock.fileno())

if __name__=='__main__':unittest.main()
