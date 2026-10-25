"""Real Parquet replay reader, synthetic recordings. No device imports."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import pyarrow as pa
import pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import replay_data as data


class ReplayStorage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for name in ['meta/episodes/chunk-000','data/chunk-000']:(self.root/name).mkdir(parents=True)
        self.config={'joint_limits':{n:[0,270] if n=='gripper' else [-145,200] for n in data.NAMES},'joint_directions':data.DIRECTIONS,'id':'follower1','cameras':{}}
        self.calibration={'id':'unchanged'}
        self.info={'codebase_version':'v3.0','fps':30,'robot_type':'seeed_b601_rs_follower','total_episodes':2,
                   'features':{k:{'shape':[7],'names':data.KEYS} for k in ('action','observation.state')}}
        self.write_info()
        pq.write_table(pa.Table.from_pylist([{'episode_index':i,'length':3} for i in range(2)]),self.root/'meta/episodes/chunk-000/file-000.parquet')
        self.rows=[{'episode_index':i,'frame_index':j,'timestamp':j/30,'action':[float(i)]*7,'observation.state':[0.]*7} for i in range(2) for j in [2,0,1]]
        self.write_rows()
    def tearDown(self):self.temp.cleanup()
    def write_info(self):(self.root/'meta/info.json').write_text(json.dumps(self.info))
    def write_rows(self):pq.write_table(pa.Table.from_pylist(self.rows),self.root/'data/chunk-000/file-000.parquet')
    def capture(self):
        self.info['total_episodes']=1;self.write_info()
        value={'saved':True,'state':'completed','frames':3,'action_semantics':'leader_command_before_follower_mapping',
               'profile':{'robot_config':self.config,'robot_calibration':self.calibration},
               'frame_timing':[{'observation_start_s':j*.04} for j in range(3)]}
        (self.root/'workbench_capture.json').write_text(json.dumps(value));return value
    def test_legacy_reads_exact_one_episode_sorted_without_changing_files(self):
        before=data.source_identity(self.root)
        plan,actions,intervals=data.prepare(self.root,1,self.config,self.calibration)
        self.assertTrue(plan['legacy']);self.assertEqual(plan['frames'],3)
        self.assertTrue(all(a['raw']['gripper.pos']==1 for a in actions));self.assertEqual(actions[0]['motor']['gripper'],6)
        self.assertEqual(data.source_identity(self.root),before);self.assertAlmostEqual(sum(intervals),.1)
    def test_provenance_and_real_timing(self):
        self.capture();plan,_,intervals=data.prepare(self.root,0,self.config,self.calibration)
        self.assertFalse(plan['legacy']);self.assertAlmostEqual(intervals[0],.04)
        with self.assertRaisesRegex(ValueError,'标定'):data.prepare(self.root,0,self.config,{'changed':True})
        cfg=copy.deepcopy(self.config);cfg['id']='other'
        with self.assertRaisesRegex(ValueError,'控制配置'):data.prepare(self.root,0,cfg,self.calibration)
    def test_metadata_missing_frames_corruption_and_unknown_semantics_blocked(self):
        self.rows.pop();self.write_rows()
        with self.assertRaisesRegex(ValueError,'帧数'):data.prepare(self.root,1,self.config,self.calibration)
        self.capture();value=json.loads((self.root/'workbench_capture.json').read_text());value['action_semantics']='mapped_motor_degrees';(self.root/'workbench_capture.json').write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'动作语义'):data.prepare(self.root,0,self.config,self.calibration)
    def test_converted_legacy_and_symlinks_blocked(self):
        self.info['codebase_version']='v2.1';self.write_info()
        with self.assertRaisesRegex(ValueError,'原始 v3'):data.prepare(self.root,0,self.config,self.calibration)
        (self.root/'escape').symlink_to('/etc/hosts')
        with self.assertRaises(ValueError):data.prepare(self.root,0,self.config,self.calibration)

    def test_verified_converted_copy_only_when_original_values_match(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            original=Path(temp)/'raw';converted=Path(temp)/'raw_openpi'
            shutil.copytree(self.root,original);shutil.copytree(self.root,converted)
            info={**self.info,'codebase_version':'v2.1'};(converted/'meta/info.json').write_text(json.dumps(info))
            (converted/'meta/episodes.jsonl').write_text('\n'.join(json.dumps({'episode_index':i,'length':3}) for i in range(2)))
            plan,_,_=data.prepare(converted,1,self.config,self.calibration)
            self.assertEqual(plan['verified_original']['dataset'],'raw');self.assertTrue(plan['legacy'])
            rows=copy.deepcopy(self.rows);rows[-1]['action'][0]=50.
            pq.write_table(pa.Table.from_pylist(rows),converted/'data/chunk-000/file-000.parquet')
            with self.assertRaisesRegex(ValueError,'关节数值'):data.prepare(converted,1,self.config,self.calibration)


if __name__=='__main__':unittest.main()
