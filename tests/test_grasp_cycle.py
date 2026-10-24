import copy
import json
import unittest
from pathlib import Path

from rebot_tea.grasp_cycle import SyntheticGraspCycle, plan_synthetic_cycle
from scripts.run_grasp_cycle_demo import replay

ROOT = Path(__file__).resolve().parents[1]


class SyntheticGraspCycleTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/grasp_cycle.synthetic.json").read_text())

    def cycle_after_close(self):
        cycle = SyntheticGraspCycle(self.config)
        self.assertEqual(cycle.start(0.0).action, "open_gripper")
        for action, when in (("open_gripper", .05), ("approach_grasp", .10),
                             ("descend_grasp", .15), ("close_gripper", .20)):
            cycle.report_completion(action, True, when, when)
        self.assertEqual(cycle.state, "await_grasp")
        return cycle

    def test_complete_sequence_requires_two_separate_confirmations(self):
        cycle = self.cycle_after_close()
        self.assertEqual([step.action for step in cycle.issued][-1], "close_gripper")
        self.assertIsNone(cycle.current)
        self.assertEqual(cycle.confirm_grasp(True, .25, .25).action, "lift")
        for action, when in (("lift", .30), ("transfer", .35),
                             ("descend_place", .40), ("release", .45)):
            cycle.report_completion(action, True, when, when)
        self.assertEqual(cycle.state, "await_release")
        self.assertEqual(cycle.issued[-1].action, "release")
        self.assertEqual(cycle.confirm_release(True, .50, .50).action, "retreat")
        cycle.report_completion("retreat", True, .55, .55)
        self.assertEqual(cycle.state, "completed")
        self.assertEqual([step.action for step in cycle.issued],
                         ["open_gripper", "approach_grasp", "descend_grasp",
                          "close_gripper", "lift", "transfer", "descend_place",
                          "release", "retreat"])
        report = cycle.report()
        self.assertTrue(report["synthetic"])
        for field in ("physical_grasp_verified", "physical_release_verified",
                      "ik_verified", "collision_checked"):
            self.assertFalse(report[field])

    def test_missing_grasp_evidence_aborts_before_lift_and_latches(self):
        cycle = self.cycle_after_close()
        cycle.tick(1.2)
        self.assertEqual(cycle.state, "aborted")
        self.assertEqual(cycle.reason, "await_grasp_timeout")
        self.assertIsNone(cycle.confirm_grasp(True, 1.21, 1.21))
        self.assertIsNone(cycle.start(2.0))
        self.assertEqual(len(cycle.issued), 4)

    def test_step_completion_cannot_impersonate_grasp_or_release(self):
        cycle = self.cycle_after_close()
        cycle.report_completion("lift", True, .25, .25)
        self.assertEqual(cycle.reason, "unexpected_step_feedback")
        self.assertEqual(len(cycle.issued), 4)

        cycle = self.cycle_after_close()
        cycle.confirm_grasp(True, .25, .25)
        for action, when in (("lift", .30), ("transfer", .35),
                             ("descend_place", .40), ("release", .45)):
            cycle.report_completion(action, True, when, when)
        cycle.tick(1.45)
        self.assertEqual(cycle.reason, "await_release_timeout")
        self.assertEqual(cycle.issued[-1].action, "release")

    def test_false_or_truthy_grasp_evidence_aborts(self):
        for value, reason in ((False, "await_grasp_rejected"),
                              (1, "invalid_confirmation_evidence"),
                              ("yes", "invalid_confirmation_evidence")):
            with self.subTest(value=value):
                cycle = self.cycle_after_close()
                cycle.confirm_grasp(value, .25, .25)
                self.assertEqual(cycle.reason, reason)
                self.assertEqual(len(cycle.issued), 4)

    def test_rejected_release_evidence_prevents_retreat(self):
        cycle = self.cycle_after_close()
        cycle.confirm_grasp(True, .25, .25)
        for action, when in (("lift", .30), ("transfer", .35),
                             ("descend_place", .40), ("release", .45)):
            cycle.report_completion(action, True, when, when)
        cycle.confirm_release(False, .50, .50)
        self.assertEqual(cycle.reason, "await_release_rejected")
        self.assertEqual(cycle.issued[-1].action, "release")

    def test_stale_wrong_and_out_of_order_feedback_abort(self):
        cases = (("approach_grasp", True, .05, .05, "unexpected_step_feedback"),
                 ("open_gripper", True, -.01, .05, "stale_or_out_of_order_feedback"),
                 ("open_gripper", True, .05, .30, "stale_or_out_of_order_feedback"),
                 ("open_gripper", 1, .05, .05, "invalid_completion_evidence"))
        for action, completed, observed, now, reason in cases:
            with self.subTest(reason=reason, observed=observed):
                cycle = SyntheticGraspCycle(self.config)
                cycle.start(0.0)
                cycle.report_completion(action, completed, observed, now)
                self.assertEqual(cycle.reason, reason)
                self.assertEqual(len(cycle.issued), 1)
                self.assertIsNone(cycle.report_completion("open_gripper", True, .4, .4))
                self.assertEqual(len(cycle.issued), 1)

        cycle = SyntheticGraspCycle(self.config)
        cycle.start(0.0)
        cycle.report_completion("open_gripper", True, .05, .05)
        cycle.report_completion("approach_grasp", True, .05, .10)
        self.assertEqual(cycle.reason, "stale_or_out_of_order_feedback")
        self.assertEqual(len(cycle.issued), 2)

    def test_timeout_and_clock_reversal_never_issue_next_command(self):
        cycle = SyntheticGraspCycle(self.config)
        cycle.start(0.0)
        cycle.tick(1.0)
        self.assertEqual(cycle.reason, "step_timeout:open_gripper")
        self.assertIsNone(cycle.report_completion("open_gripper", True, 1.01, 1.01))
        self.assertEqual(len(cycle.issued), 1)

        cycle = SyntheticGraspCycle(self.config)
        cycle.start(0.0)
        self.assertIsNone(cycle.tick(.2))
        self.assertEqual(len(cycle.issued), 1)
        cycle.tick(.1)
        self.assertEqual(cycle.reason, "clock_reversal")
        self.assertEqual(len(cycle.issued), 1)

        cycle = SyntheticGraspCycle(self.config)
        cycle.start(0.0)
        cycle.tick(float("nan"))
        self.assertEqual(cycle.reason, "invalid_time")
        self.assertEqual(len(cycle.issued), 1)

    def test_entire_plan_is_validated_before_any_command(self):
        for change in ({"robot": {"model": "B601-RS", "backend": "hardware"}},
                       {"synthetic": False}, {"frame": "camera"},
                       {"place_m": [.5, 0, .08]}, {"grasp_m": [.3, 0, .30]},
                       {"max_feedback_age_s": float("nan")},
                       {"step_timeout_s": True}):
            with self.subTest(change=change):
                config = copy.deepcopy(self.config)
                config.update(change)
                with self.assertRaises(ValueError):
                    plan_synthetic_cycle(config)

    def test_demo_consumes_external_synthetic_events(self):
        result = replay(ROOT / "config/grasp_cycle.synthetic.json",
                        ROOT / "fixtures/grasp_cycle_feedback.synthetic.json")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(result["issued"]), 9)
        self.assertFalse(result["physical_grasp_verified"])


if __name__ == "__main__":
    unittest.main()
