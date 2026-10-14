import copy
import json
import unittest
from pathlib import Path

import numpy as np

from rebot_tea.geometry import ImageDetection, localize

ROOT = Path(__file__).resolve().parents[1]


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.calibration = json.loads((ROOT / "fixtures/calibration.demo.json").read_text())
        self.depth = np.full((480, 640), 500, dtype=np.uint16)
        self.detection = ImageDetection("cup", .95, (300, 220, 340, 260))

    def test_known_projection_and_extrinsics(self):
        point = localize(self.detection, self.depth, self.calibration)
        np.testing.assert_allclose(point.position_m, [.3, 0, .1], atol=1e-12)
        self.assertEqual(point.frame, "base")

    def test_pixel_offset_uses_intrinsics(self):
        detection = ImageDetection("cup", .95, (400, 220, 440, 260))
        point = localize(detection, self.depth, self.calibration)
        np.testing.assert_allclose(point.position_m, [.4, 0, .1], atol=1e-12)

    def test_depth_holes_fail_closed(self):
        for fill in (0, np.nan, np.inf, -1):
            with self.subTest(fill=fill), self.assertRaisesRegex(ValueError, "depth"):
                localize(self.detection, np.full((480, 640), fill), self.calibration)

    def test_boxes_touching_image_boundary_are_valid(self):
        for box in [(600, 220, 640, 260), (300, 440, 340, 480),
                    (639.8, 479.8, 640, 480)]:
            with self.subTest(box=box):
                point = localize(ImageDetection("cup", .95, box), self.depth, self.calibration)
                self.assertTrue(np.isfinite(point.position_m).all())

    def test_mismatched_resolution_rejected(self):
        with self.assertRaisesRegex(ValueError, "shape"):
            localize(self.detection, self.depth[:100], self.calibration)

    def test_uncalibrated_inputs_rejected(self):
        for field, value in [("depth_aligned_to_color", False), ("rectified", False),
                             ("fx", 0), ("depth_scale_m", -1)]:
            calibration = copy.deepcopy(self.calibration)
            calibration[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                localize(self.detection, self.depth, calibration)

    def test_non_rigid_transform_rejected(self):
        calibration = copy.deepcopy(self.calibration)
        calibration["T_base_camera"][0][0] = 2
        with self.assertRaisesRegex(ValueError, "rigid"):
            localize(self.detection, self.depth, calibration)

    def test_invalid_boxes_rejected(self):
        for box in [(-1, 220, 340, 260), (300, 220, 900, 260),
                    (340, 220, 300, 260), (float("nan"), 0, 1, 1)]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                localize(ImageDetection("cup", .95, box), self.depth, self.calibration)
