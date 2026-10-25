import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app, library, recycle

class RecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'datasets';self.root.mkdir()
        for obj,attr,value in [(library,'DB',Path(self.tmp.name)/'library.sqlite3')]:
            p=patch.object(obj,attr,value);p.start();self.addCleanup(p.stop)
        p=patch.dict(app.CONFIG,dataset_root=str(self.root));p.start();self.addCleanup(p.stop)
        p=patch.object(app.recording,'status',return_value={'active':False});p.start();self.addCleanup(p.stop)
        recycle.PLANS.clear()
        self.group=library.create('test','Task')['id']
        self.make('sample')
    def make(self,name,count=1):
        path=self.root/name;(path/'meta').mkdir(parents=True)
        (path/'meta/info.json').write_text(json.dumps({'total_episodes':count,'total_frames':30*count,'fps':30,'total_tasks':1,'codebase_version':'v3','features':{}}))
        (path/'data.bin').write_bytes(b'x'*8192)
        library.add_members(self.group,name,[{'index':i,'tasks':['Task'],'frames':30} for i in range(count)])
        return path
    def test_raw_delete_sync_restore_and_exact_bytes(self):
        original=(self.root/'sample/data.bin').read_bytes()
        app.recycle_action('trash',{'dataset':'sample'})
        self.assertFalse((self.root/'sample').exists());self.assertEqual(app.datasets(),[])
        self.assertTrue(app.library_snapshot()['members'][0]['source_deleted'])
        with self.assertRaises(ValueError):app.dataset_path('sample')
        with self.assertRaises(ValueError):library.add_members(self.group,'sample',[])
        self.assertEqual(len(recycle.listing(self.root)),1)
        app.recycle_action('restore',{'dataset':'sample'})
        self.assertEqual((self.root/'sample/data.bin').read_bytes(),original)
        self.assertFalse(app.library_snapshot()['members'][0]['source_deleted'])
    def test_single_episode_delete_uses_same_bin_and_restores(self):
        app.library_action('update',{'dataset':'sample','episode':0,'trashed':True})
        self.assertEqual(recycle.listing(self.root)[0]['kind'],'dataset')
        app.recycle_action('restore',{'dataset':'sample'})
        self.assertFalse(library.snapshot()['members'][0]['trashed'])
    def test_shared_episode_keeps_other_data_and_requires_whole_dataset(self):
        path=self.make('shared',2)
        app.library_action('update',{'dataset':'shared','episode':0,'trashed':True})
        self.assertTrue(path.exists())
        item=next(i for i in recycle.listing(self.root) if i['dataset']=='shared')
        self.assertEqual(item['kind'],'episode');self.assertEqual(item['source_episodes'],2)
        with self.assertRaises(ValueError):recycle.plan(self.root,'shared')
        app.recycle_action('restore',{'dataset':'shared','episode':0})
        self.assertFalse(next(m for m in library.snapshot()['members'] if m['dataset']=='shared' and m['episode']==0)['trashed'])
    def test_purge_requires_fresh_bound_confirmation_and_releases_files(self):
        recycle.trash(self.root,'sample')
        plan=recycle.plan(self.root,'sample')
        self.assertGreater(plan['allocated'],0)
        with self.assertRaises(ValueError):recycle.purge(self.root,'sample',plan['token'],'wrong')
        self.assertTrue(recycle.quarantined(self.root,recycle.rows()[0]).exists())
        plan=recycle.plan(self.root,'sample')
        target=recycle.quarantined(self.root,recycle.rows()[0])
        recycle.purge(self.root,'sample',plan['token'],'sample')
        self.assertFalse(target.exists());self.assertEqual(recycle.listing(self.root),[])
        with self.assertRaises(ValueError):recycle.restore(self.root,'sample')
        with self.assertRaises(ValueError):recycle.purge(self.root,'sample',plan['token'],'sample')
    def test_referenced_draft_becomes_unavailable_and_recovers(self):
        library.update({'dataset':'sample','episode':0,'review':'keep'})
        draft=library.mixture({'name':'draft','selections':[{'id':self.group,'weight':1}]},lambda *_:{'signature':{}})
        recycle.trash(self.root,'sample')
        self.assertEqual(recycle.plan(self.root,'sample')['references'][0]['name'],'draft')
        self.assertTrue(app.library_snapshot()['mixtures'][0]['unavailable_sources'])
        recycle.restore(self.root,'sample')
        self.assertEqual(app.library_snapshot()['mixtures'][0]['unavailable_sources'],[])
        self.assertEqual(library.snapshot()['mixtures'][0],draft)
    def test_symlinks_and_restore_collision_fail_closed(self):
        outside=Path(self.tmp.name)/'outside';outside.mkdir();(outside/'keep').write_text('keep')
        (self.root/'link').symlink_to(outside,target_is_directory=True)
        for name in ('../outside','/tmp','link','.b601-trash'):
            with self.assertRaises(ValueError):recycle.trash(self.root,name)
        # Nested links are removed as links, never followed on permanent deletion.
        (self.root/'sample/link').symlink_to(outside,target_is_directory=True)
        recycle.trash(self.root,'sample')
        (self.root/'sample').mkdir()
        with self.assertRaises(ValueError):recycle.restore(self.root,'sample')
        plan=recycle.plan(self.root,'sample');recycle.purge(self.root,'sample',plan['token'],'sample')
        self.assertEqual((outside/'keep').read_text(),'keep')
    def test_failed_purge_is_retryable_but_not_restorable(self):
        recycle.trash(self.root,'sample');plan=recycle.plan(self.root,'sample')
        original=recycle.shutil.rmtree
        def fail(_):raise OSError('simulated disk failure')
        fail.avoids_symlink_attacks=original.avoids_symlink_attacks
        with patch.object(recycle.shutil,'rmtree',fail):
            with self.assertRaises(OSError):recycle.purge(self.root,'sample',plan['token'],'sample')
        self.assertEqual(recycle.rows()[0]['state'],'purging')
        with self.assertRaises(ValueError):recycle.restore(self.root,'sample')
        plan=recycle.plan(self.root,'sample');recycle.purge(self.root,'sample',plan['token'],'sample')
        self.assertEqual(recycle.rows()[0]['state'],'purged')
    def test_rename_journal_recovery_and_active_capture_guard(self):
        recycle.trash(self.root,'sample');recycle.state('sample','moving');recycle.recover(self.root)
        self.assertEqual(recycle.rows()[0]['state'],'trashed')
        row=recycle.rows()[0];recycle.state('sample','restoring');recycle.quarantined(self.root,row).rename(self.root/'sample')
        recycle.recover(self.root);self.assertEqual(recycle.rows(),[])
        with patch.object(app.recording,'status',return_value={'active':True,'dataset':'sample'}):
            with self.assertRaises(ValueError):app.recycle_action('trash',{'dataset':'sample'})
        self.assertTrue((self.root/'sample').exists())

    def test_legacy_single_soft_delete_hidden_and_can_be_purged(self):
        library.update({'dataset':'sample','episode':0,'trashed':True})
        self.assertEqual(app.datasets(),[])
        item=recycle.listing(self.root)[0]
        self.assertTrue(item['whole_source_removed'])
        plan=app.recycle_action('plan',{'dataset':'sample'})
        self.assertFalse((self.root/'sample').exists())
        app.recycle_action('purge',{'dataset':'sample','token':plan['token'],'confirmation':'sample'})
        self.assertEqual(recycle.listing(self.root),[])
