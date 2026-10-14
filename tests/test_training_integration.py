"""Real video/data corruption checks; run in the isolated training environment."""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

AVAILABLE = importlib.util.find_spec("lerobot") is not None


@unittest.skipUnless(AVAILABLE, "Run bash scripts/training.sh test in the training environment")
class TrainingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "training_tools", Path(__file__).resolve().parents[1] / "scripts/training_tools.py")
        cls.tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tools)
        cls.directory = tempfile.TemporaryDirectory()
        cls.base = Path(cls.directory.name) / "base"
        cls.tools.create_dataset(cls.base, frames=8)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.case = Path(self.directory.name) / self.id().rsplit(".", 1)[-1]
        shutil.copytree(self.base, self.case)

    def test_native_video_dataset_roundtrip(self):
        report = self.tools.validate_dataset(self.case)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["frames_checked"], 40)

    def test_corrupted_video_is_reported(self):
        video = next((self.case / "videos").rglob("*.mp4"))
        video.write_bytes(b"invalid synthetic video")
        report = self.tools.validate_dataset(self.case)
        self.assertFalse(report["passed"])
        self.assertTrue(report["errors"])

    def test_missing_feature_is_reported(self):
        info = self.case / "meta/info.json"
        payload = json.loads(info.read_text())
        del payload["features"]["action"]
        info.write_text(json.dumps(payload))
        self.assertFalse(self.tools.validate_dataset(self.case)["passed"])

    def test_overlap_split_is_reported_before_training(self):
        path = self.case / "prehardware.json"
        manifest = json.loads(path.read_text())
        manifest["validation_episodes"] = [0]
        path.write_text(json.dumps(manifest))
        self.assertFalse(self.tools.validate_dataset(self.case)["passed"])
