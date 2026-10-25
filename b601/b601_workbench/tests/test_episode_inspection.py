import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import episode_inspection as inspection
import pyarrow as pa
import pyarrow.parquet as pq

class InspectionTests(unittest.TestCase):
    def fixture(self,root,times=None,action=None,video=True):
        (root/'meta').mkdir();(root/'data').mkdir();(root/'videos').mkdir()
        info={'codebase_version':'v2.1','fps':30,'chunks_size':1000,'data_path':'data/episode_{episode_index:06d}.parquet','video_path':'videos/episode_{episode_index:06d}.mp4','features':{'action':{'shape':[2],'names':['a','b']},'observation.state':{'shape':[2],'names':['a','b']},'observation.images.wrist_rgb':{'dtype':'video','shape':[480,640,3]}}}
        (root/'meta/info.json').write_text(json.dumps(info));(root/'meta/episodes.jsonl').write_text(json.dumps({'episode_index':0,'length':3})+'\n')
        pq.write_table(pa.table({'episode_index':[0]*3,'timestamp':times or [0.,1/30,2/30],'action':action or [[1.,2.],[2.,3.],[3.,4.]],'observation.state':[[1.,2.],[2.,3.],[3.,4.]]}),root/'data/episode_000000.parquet')
        if video:(root/'videos/episode_000000.mp4').write_bytes(b'fixture-only')

    def test_valid_read_preserves_data_and_reports_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root);before=(root/'data/episode_000000.parquet').read_bytes()
            result=inspection.inspect(root,0)
            self.assertEqual(result['frames'],3);self.assertEqual(result['status'],'warning')
            self.assertEqual([c['key'] for c in result['checks'] if c['status']=='warning'],['provenance'])
            self.assertEqual(result['plot']['action']['values'][0],[1.,2.]);self.assertEqual(before,(root/'data/episode_000000.parquet').read_bytes())

    def test_nonfinite_data_and_nonmonotonic_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root,times=[0.,.1,.05],action=[[1.,2.],[float('nan'),2.],[3.,4.]])
            result=inspection.inspect(root,0);json.dumps(result,allow_nan=False)
            self.assertEqual(result['status'],'error');self.assertEqual(result['plot']['action']['values'][1],[None,None])
            self.assertEqual(next(c for c in result['checks'] if c['key']=='timing')['status'],'warning')

    def test_missing_video_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root,video=False);result=inspection.inspect(root,0)
            self.assertEqual(next(c for c in result['checks'] if c['key']=='video')['status'],'error')

    def test_missing_episode_and_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            for index in [-1,1]:
                with self.assertRaises(ValueError):inspection.inspect(root,index)
            with self.assertRaises(ValueError):inspection.safe_file(root,'../outside.parquet','data')
            target=root/'data/link.parquet';target.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):inspection.safe_file(root,'data/link.parquet','data')

if __name__=='__main__':unittest.main()
