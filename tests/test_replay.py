import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rebot_tea.replay import replay

ROOT = Path(__file__).resolve().parents[1]


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        cv2.imwrite(str(self.root / "color.png"), np.zeros((480, 640, 3), dtype=np.uint8))
        np.save(self.root / "depth.npy", np.full((480, 640), 500, dtype=np.uint16))
        calibration = (ROOT / "fixtures/calibration.demo.json").read_text()
        (self.root / "calibration.json").write_text(calibration)
        self.scene = {"synthetic": True, "color": "color.png", "depth": "depth.npy",
                      "calibration": "calibration.json", "detections": [
                          {"label": "cup", "confidence": .95,
                           "bbox_xyxy": [300, 220, 340, 260]}]}

    def run_scene(self):
        path = self.root / "scene.json"
        path.write_text(json.dumps(self.scene))
        return replay(path, ROOT / "config/simulation.json")

    def test_rgbd_to_mock_pipeline(self):
        result = self.run_scene()
        self.assertFalse(result["physical_grasp_verified"])
        self.assertEqual(result["robot_backend"], "mock")
        self.assertAlmostEqual(result["target"]["position_m"][2], .1)
        self.assertEqual(len(result["steps"]), 5)

    def test_no_detection_cannot_create_a_plan(self):
        self.scene["detections"] = []
        with self.assertRaisesRegex(ValueError, "No cup"):
            self.run_scene()

    def test_fixture_cannot_impersonate_real_vision(self):
        self.scene["synthetic"] = False
        with self.assertRaisesRegex(ValueError, "synthetic"):
            self.run_scene()
