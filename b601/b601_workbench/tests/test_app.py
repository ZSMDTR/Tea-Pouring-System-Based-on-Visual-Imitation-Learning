import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

class WorkbenchTests(unittest.TestCase):
    def test_range_bounds(self):
        self.assertEqual(app.byte_range('bytes=3-999', 10), (3, 9, True))
        self.assertEqual(app.byte_range('bytes=-3', 10), (7, 9, True))
        for value in ['bytes=10-', 'bytes=-0', 'bytes=4-2', 'bytes=0-1,4-5']:
            with self.assertRaises(ValueError): app.byte_range(value,10)

    def test_paths_cannot_escape_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'demo/meta').mkdir(parents=True)
            (root/'demo/meta/info.json').write_text('{}')
            (root/'demo/videos').mkdir()
            (root/'outside.mp4').write_bytes(b'x')
            (root/'demo/videos/escape.mp4').symlink_to(root/'outside.mp4')
            with patch.dict(app.CONFIG, dataset_root=tmp):
                self.assertEqual(app.dataset_path('demo'),root/'demo')
                for name in ['..','demo/..','/etc']:
                    with self.assertRaises(ValueError): app.dataset_path(name)
                for name in ['../../outside.mp4','videos/escape.mp4']:
                    with self.assertRaises(ValueError): app.video_path('demo',name)

    def test_raw_and_converted_episode_boundaries(self):
        stem='b601_rs_red_screwdriver_orange_container_v2crop_20260901'
        raw=app.episodes(stem); converted=app.episodes(stem+'_openpi')
        self.assertEqual(len(raw),15)
        self.assertEqual([(e['index'],e['frames']) for e in raw],[(e['index'],e['frames']) for e in converted])
        self.assertGreater(raw[1]['videos'][0]['start'],0)
        self.assertEqual(converted[1]['videos'][0]['start'],0)
        self.assertEqual(sum(e['frames'] for e in raw),13459)

    def test_existing_data_is_not_automatically_accepted(self):
        self.assertTrue(all(d['status'] in ['空记录','未登记回放验收','待实机 Replay 验收','采集异常 · 需检查'] for d in app.datasets() if 'error' not in d))

if __name__=='__main__':unittest.main()
