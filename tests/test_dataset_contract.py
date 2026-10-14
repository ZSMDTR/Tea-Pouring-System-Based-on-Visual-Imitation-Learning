import copy
import math
import unittest

from rebot_tea.dataset_contract import (
    public_degrees_to_radians, public_radians_to_degrees, validate_manifest, validate_rows,
)
from rebot_tea.joints import JOINT_NAMES


class DatasetContractTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"schema_version": 1, "synthetic": True,
                         "position_units": "public_joint_degrees", "joint_names": list(JOINT_NAMES),
                         "fps": 20, "num_episodes": 2, "frames_per_episode": 3,
                         "train_episodes": [0], "validation_episodes": [1], "max_step_deg": 10.}
        self.rows = [{"episode_index": ep, "frame_index": idx, "timestamp": idx / 20,
                      "observation.state": [idx] * 7, "action": [idx] * 7}
                     for ep in range(2) for idx in range(3)]

    def test_valid_episode_split_and_frames(self):
        report = validate_rows(self.rows, self.manifest)
        self.assertTrue(report["passed"])
        self.assertEqual(report["frames_checked"], 6)

    def test_public_unit_conversion_roundtrip(self):
        values = (.1, -.2, .3, -.4, .5, -.6, -.7)
        for a, b in zip(values, public_degrees_to_radians(public_radians_to_degrees(values))):
            self.assertAlmostEqual(a, b)

    def test_missing_nonfinite_and_wrong_dimension_fields(self):
        for value in (None, [0.] * 6, [math.nan] * 7, [True] * 7):
            rows = copy.deepcopy(self.rows)
            rows[1]["action"] = value
            self.assertFalse(validate_rows(rows, self.manifest)["passed"])

    def test_time_reversal_missing_frames_and_jumps(self):
        for change in ("reversed", "missing", "jump", "bad_rate"):
            rows = copy.deepcopy(self.rows)
            if change == "reversed":
                rows[1], rows[2] = rows[2], rows[1]
            elif change == "missing":
                rows.pop()
            elif change == "jump":
                rows[1]["action"] = [80.] * 7
            else:
                rows[1]["timestamp"] = .051
            with self.subTest(change=change):
                self.assertFalse(validate_rows(rows, self.manifest)["passed"])

    def test_bad_splits_units_and_identity_are_rejected(self):
        for change in ({"validation_episodes": [0]}, {"train_episodes": [0, 0]},
                       {"validation_episodes": []}, {"position_units": "motor_radians"},
                       {"joint_names": list(reversed(JOINT_NAMES))}, {"synthetic": False},
                       {"fps": True}):
            self.assertTrue(validate_manifest({**self.manifest, **change}))
