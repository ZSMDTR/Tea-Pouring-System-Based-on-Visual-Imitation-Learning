import unittest

from rebot_tea.acceptance import summarize_grasp, summarize_pour


class AcceptanceTests(unittest.TestCase):
    def grasp(self, trial_id):
        return {"trial_id": trial_id, "grasp_stable": True, "lift_complete": True,
                "placed_in_region": True, "dropped": False, "collided": False, "intervention": False}

    def pour(self, fraction):
        return {"trial_id": "p1", "capacity_ml": 200., "final_volume_ml": fraction * 200,
                "stop_requested_time_s": 1., "flow_stopped_time_s": 1.3,
                "measured_volume_at_stop_ml": None, "spill": False}

    def test_empty_or_incomplete_trials_never_pass(self):
        self.assertFalse(summarize_grasp([])["passed"])
        self.assertFalse(summarize_grasp([self.grasp("g1")])["passed"])
        self.assertFalse(summarize_pour([])["passed"])

    def test_eighteen_of_twenty_and_failure_observations(self):
        rows = [self.grasp(str(index)) for index in range(20)]
        rows[0]["dropped"], rows[1]["intervention"] = True, True
        self.assertTrue(summarize_grasp(rows)["passed"])
        rows[2]["collided"] = True
        self.assertFalse(summarize_grasp(rows)["passed"])

    def test_capacity_fraction_bounds_and_spill(self):
        for value in (.85, .9, .95):
            self.assertTrue(summarize_pour([self.pour(value)])["passed"])
        for value in (.84, .96):
            self.assertFalse(summarize_pour([self.pour(value)])["passed"])
        row = self.pour(.9)
        row["spill"] = True
        self.assertFalse(summarize_pour([row])["passed"])

    def test_unknown_residual_is_not_fabricated_as_zero(self):
        report = summarize_pour([self.pour(.9)])
        self.assertIsNone(report["trials"][0]["residual_volume_ml"])
        self.assertFalse(report["independently_verified"])

    def test_missing_observations_and_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            summarize_grasp([self.grasp("g1"), self.grasp("g1")])
        row = self.grasp("g1")
        del row["dropped"]
        with self.assertRaises(ValueError):
            summarize_grasp([row])

    def test_invalid_measurement_and_stop_timing_rejected(self):
        for change in ({"capacity_ml": 0}, {"final_volume_ml": float("nan")},
                       {"flow_stopped_time_s": .5}, {"measured_volume_at_stop_ml": 190}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                summarize_pour([{**self.pour(.9), **change}])
