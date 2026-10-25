import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import dataset_export,experiment_data,app,experiments

class ExportPlanTests(unittest.TestCase):
    def test_selection_and_stale_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'meta').mkdir();info={'codebase_version':'v2.1','robot_type':'seeed_b601_rs_follower','fps':30,'total_frames':60,'total_episodes':6,'features':{k:{'dtype':'float32','shape':[7],'names':experiment_data.JOINTS} for k in ('action','observation.state')}}
            info['features']['observation.images.wrist_rgb']={'dtype':'video','shape':[480,640,3],'names':['height','width','channels']};(root/'meta/info.json').write_text(json.dumps(info))
            snapshot={'collections':[{'id':'g','name':'demo'}],'members':[{'collection':'g','dataset':'d','episode':i,'review':review,'trashed':trash} for i,(review,trash) in enumerate([('keep',0),('unreviewed',0),('reject',0),('keep',1)])]}
            def describe(name,index):return {'root':str(root),'metadata_sha256':'sha','signature':{'camera':None},'episode':{'frames':10,'tasks':['task'],'videos':[]}}
            a=dataset_export.plan(snapshot,'g',describe);self.assertEqual([x['episode'] for x in a['items']],[0]);self.assertEqual(a['excluded'],3)
            snapshot['members'][1]['review']='keep';b=dataset_export.plan(snapshot,'g',describe);self.assertNotEqual(a['digest'],b['digest'])
            with patch.object(app.recording,'status',return_value={'active':False}),patch.object(app.library,'snapshot',return_value=snapshot),patch.object(app,'describe_source',side_effect=lambda n,i,c:describe(n,i)),patch.object(experiments,'start') as start:
                with self.assertRaisesRegex(ValueError,'清单已变化'):app.export_action('start',{'collection':'g','digest':a['digest']})
                start.assert_not_called()
    def test_symlink_sources_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'escape').symlink_to('/etc/hosts')
            with self.assertRaises(ValueError):dataset_export.inventory(root)
    def test_empty_export_does_not_launch(self):
        with patch.object(app.recording,'status',return_value={'active':False}),patch.object(app.library,'snapshot',return_value={'collections':[{'id':'g','name':'demo'}],'members':[]}),patch.object(experiments,'start') as start:
            with self.assertRaisesRegex(ValueError,'没有标记'):app.export_action('start',{'collection':'g'})
            start.assert_not_called()

if __name__=='__main__':unittest.main()
