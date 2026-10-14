import copy
import json
import unittest
from pathlib import Path

from rebot_tea.__main__ import run
from rebot_tea.perception import Detection
from rebot_tea.planning import plan_grasp

ROOT = Path(__file__).resolve().parents[1]


class GraspTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/simulation.json").read_text())["planning"]

    def test_mock_pipeline(self):
        steps = run(ROOT / "config/simulation.json")
        self.assertEqual([s.action for s in steps],
                         ["open_gripper", "approach", "descend", "close_gripper", "lift"])
        self.assertAlmostEqual(steps[-1].position_m[2], 0.20)

    def test_rejects_untrusted_detection(self):
        for confidence, frame, position in [
            (0.1, "base", (0.3, 0., 0.08)),
            (float("nan"), "base", (0.3, 0., 0.08)),
            (0.95, "camera", (0.3, 0., 0.08)),
            (0.95, "base", (float("nan"), 0., 0.08)),
            (0.95, "base", (0.3, 0.)),
        ]:
            with self.subTest(confidence=confidence, frame=frame, position=position):
                with self.assertRaises(ValueError):
                    plan_grasp(Detection("cup", confidence, frame, position), self.config)

    def test_checks_lift_not_only_grasp_point(self):
        with self.assertRaisesRegex(ValueError, "lift"):
            plan_grasp(Detection("cup", .95, "base", (.3, 0., .25)), self.config)

    def test_refuses_hardware_backend(self):
        import tempfile
        config = json.loads((ROOT / "config/simulation.json").read_text())
        config["robot"]["backend"] = "hardware"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "mock"):
                run(path)

    def test_rejects_invalid_planning_configuration(self):
        for key, value in [("approach_m", -1), ("lift_m", float("inf")),
                           ("min_confidence", -1)]:
            config = copy.deepcopy(self.config)
            config[key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    plan_grasp(Detection("cup", .95, "base", (.3, 0., .08)), config)


if __name__ == "__main__":
    unittest.main()
