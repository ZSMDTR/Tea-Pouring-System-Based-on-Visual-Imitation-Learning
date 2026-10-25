import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import checkpoint_catalog as catalog
import experiment_worker


class FullTrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ident='wb_train_test/10';self.path=self.root/self.ident
        for name in ('params','model','adam_mu','adam_nu','training'):
            (self.path/name).mkdir(parents=True);(self.path/name/'_METADATA').write_text('{}')
        norm=self.path/'assets/local/demo/norm_stats.json';norm.parent.mkdir(parents=True);norm.write_text('{}')
        self.identity={'repo':'local/demo','dataset':'demo','source':'/tmp/demo','batch':1,'norm_sha256':catalog.digest(norm)}
        (self.path/'complete.json').write_text(json.dumps({'format':'orbax-four-block-v1','complete':True,'step':10,'workbench':{'format':catalog.FORMAT,'identity':self.identity}}))
        self.config={'checkpoint_root':str(self.root)}
        self.data={'save_mode':'full','save_interval':10,'steps':20,'batch':1}

    def test_catalog_excludes_partial_missing_and_corrupt(self):
        self.assertEqual(len(catalog.listing(self.root)),1)
        (self.root/'wb_train_test/.saving_123').mkdir()
        self.assertEqual(len(catalog.listing(self.root)),1)
        (self.path/'training/_METADATA').unlink()
        self.assertEqual(catalog.listing(self.root),[])

    def test_corrupted_norm_rejected(self):
        (self.path/'assets/local/demo/norm_stats.json').write_text('changed')
        with self.assertRaises(ValueError):catalog.read(self.path)

    def test_paths_and_symlinks_rejected(self):
        (self.root/'wb_train_link').symlink_to(self.path.parent)
        for ident in ('../wb_train_test/10',str(self.path),'wb_train_link/10','wb_train_test/.saving_123'):
            with self.assertRaises(ValueError):catalog.resolve(self.root,ident)

    def test_resume_requires_matching_dataset_batch_and_later_target(self):
        with patch.object(catalog.shutil,'disk_usage',return_value=SimpleNamespace(free=10**15)):
            data={**self.data,'resume_checkpoint':self.ident}
            self.assertEqual(catalog.validate_options(data,self.config,Path('/tmp/demo'))['resume_from'],str(self.path))
            for change,source in [({'steps':10},'/tmp/demo'),({'batch':2},'/tmp/demo'),({},'/tmp/other'),({'save_mode':'final'},'/tmp/demo'),({'save_interval':True},'/tmp/demo')]:
                with self.assertRaises(ValueError):catalog.validate_options({**data,**change},self.config,Path(source))

    def test_disk_budget_counts_final_off_interval_save(self):
        with patch.object(catalog.shutil,'disk_usage',return_value=SimpleNamespace(free=10**15)):
            options=catalog.validate_options({**self.data,'steps':21},self.config,Path('/tmp/demo'))
            self.assertEqual(options['planned_saves'],3)
        with patch.object(catalog.shutil,'disk_usage',return_value=SimpleNamespace(free=60*2**30)):
            with self.assertRaisesRegex(ValueError,'磁盘'):catalog.validate_options(self.data,self.config,Path('/tmp/demo'))

    def test_legacy_default_does_not_require_new_runtime(self):
        self.assertEqual(catalog.validate_options({}, {}, Path('/tmp/demo')),{'save_mode':'final'})

    def test_worker_uses_isolated_entry_and_copies_resume_norm(self):
        folder=self.root/'job';folder.mkdir()
        req={'kind':'train','source':'/tmp/demo','output':str(self.root/'new'),'config':{'openpi_python':'/stable/python'},'steps':20,'save_mode':'full','resume_from':str(self.path)}
        (folder/'request.json').write_text(json.dumps(req))
        with patch.object(experiment_worker,'run_child') as run,patch.object(catalog,'read',return_value={}):
            experiment_worker.main(folder,7)
        self.assertEqual(run.call_count,2)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][0],str(catalog.FULL_PYTHON))
            self.assertIn('full_training/entry.py',call.args[0][2])
        self.assertEqual(run.call_args_list[1].args[3]['XLA_PYTHON_CLIENT_ALLOCATOR'],'platform')
        self.assertEqual(json.loads((folder/'status.json').read_text())['state'],'completed')

if __name__=='__main__':unittest.main()
