import unittest
from dataclasses import replace

from rebot_tea.pouring import CupObservation, PourController, PourLimits


class PouringTests(unittest.TestCase):
    def setUp(self):
        # Synthetic values for this test, not a real cup or hot-liquid policy.
        self.limits = PourLimits(0.9, 0.9, 0.25, 0.015, 1.0)
        self.observation = CupObservation("cup-a", 0.0, (0.32, -0.05, 0.1), 0.2, 0.99,
                                          grasp_verified=True, spout_aligned=True)

    def controller(self):
        return PourController("cup-a", self.limits)

    def assert_stopped(self, result, reason):
        self.assertEqual(result.state, "aborted")
        self.assertEqual(result.reason, reason)
        self.assertEqual(result.actions, ("stop_pour", "upright"))

    def test_target_volume_stops_pouring_and_completion_cannot_restart(self):
        control = self.controller()
        start = control.step(self.observation, 0.0)
        self.assertEqual(start.actions, ("tilt_increment",))
        self.assertEqual(control.step(replace(self.observation, timestamp_s=0.1,
                                            volume_fraction=0.65), 0.1).actions,
                         ("tilt_increment",))
        done = control.step(replace(self.observation, timestamp_s=0.2, volume_fraction=0.9), 0.2)
        self.assertEqual(done.state, "complete")
        self.assertEqual(done.actions, ("stop_pour", "upright"))
        self.assertFalse(done.physical_stop_confirmed)
        self.assertFalse(done.residual_flow_settled)
        later = control.step(replace(self.observation, timestamp_s=0.3), 0.3)
        self.assertEqual(later.state, "complete")
        self.assertEqual(later.actions, ())

    def test_unverified_grasp_cannot_start(self):
        self.assert_stopped(self.controller().step(
            replace(self.observation, grasp_verified=False), 0), "grasp_unverified")

    def test_lost_grasp_stops_an_active_pour(self):
        control = self.controller()
        control.step(self.observation, 0)
        self.assert_stopped(control.step(replace(self.observation, timestamp_s=0.1,
                                                grasp_verified=False), 0.1), "grasp_lost")

    def test_unverified_spout_alignment_cannot_start(self):
        self.assert_stopped(self.controller().step(
            replace(self.observation, spout_aligned=False), 0), "spout_unverified")

    def test_lost_spout_alignment_stops_an_active_pour(self):
        control = self.controller()
        control.step(self.observation, 0)
        self.assert_stopped(control.step(replace(self.observation, timestamp_s=0.1,
                                                spout_aligned=False), 0.1), "spout_alignment_lost")

    def test_initial_cup_positions_are_taken_from_observation(self):
        for position in ((0.2, 0.1, 0.1), (0.4, -0.1, 0.12)):
            with self.subTest(position=position):
                result = self.controller().step(replace(self.observation, position_m=position), 0)
                self.assertEqual(result.cup_position_m, position)
                self.assertEqual(result.state, "pouring")

    def test_already_at_target_volume_never_requests_tilt(self):
        result = self.controller().step(replace(self.observation, volume_fraction=0.95), 0)
        self.assertEqual(result.state, "complete")
        self.assertEqual(result.actions, ("stop_pour", "upright"))

    def test_missing_feedback_stops_and_failure_cannot_restart(self):
        control = self.controller()
        control.step(self.observation, 0)
        self.assert_stopped(control.step(None, 0.1), "missing_observation")
        self.assertEqual(control.step(replace(self.observation, timestamp_s=0.2), 0.2).actions, ())
        self.assertEqual(control.state, "aborted")

    def test_stale_and_future_feedback(self):
        for timestamp, now, reason in ((0.0, 0.3, "stale_observation"),
                                       (0.1, 0.0, "future_observation")):
            with self.subTest(reason=reason):
                self.assert_stopped(self.controller().step(
                    replace(self.observation, timestamp_s=timestamp), now), reason)

    def test_identity_and_position_are_locked_for_a_pour(self):
        for change, reason in (({"cup_id": "cup-b"}, "cup_identity_changed"),
                               ({"position_m": (0.4, -0.05, 0.1)}, "cup_moved")):
            with self.subTest(reason=reason):
                control = self.controller()
                control.step(self.observation, 0)
                changed = replace(self.observation, timestamp_s=0.1, **change)
                self.assert_stopped(control.step(changed, 0.1), reason)

    def test_nonfinite_feedback_and_missing_volume_are_rejected(self):
        for field, value in (("timestamp_s", float("nan")), ("volume_fraction", float("inf")),
                             ("volume_fraction", float("nan")), ("confidence", float("nan")),
                             ("volume_fraction", None),
                             ("position_m", (0.3, float("nan"), 0.1))):
            with self.subTest(field=field, value=value):
                self.assert_stopped(self.controller().step(
                    replace(self.observation, **{field: value}), 0), "invalid_observation")

    def test_nonfinite_clock_is_rejected(self):
        self.assert_stopped(self.controller().step(self.observation, float("inf")), "invalid_clock")

    def test_low_confidence_is_distinct_from_filled_cup(self):
        result = self.controller().step(replace(self.observation, volume_fraction=0.95,
                                               confidence=0.5), 0)
        self.assert_stopped(result, "low_confidence")

    def test_repeated_observation_cannot_command_more_tilt(self):
        control = self.controller()
        control.step(self.observation, 0)
        self.assert_stopped(control.step(self.observation, 0.1), "observation_time_not_increasing")

    def test_clock_reversal_stops(self):
        control = self.controller()
        control.step(replace(self.observation, timestamp_s=0.1), 0.1)
        self.assert_stopped(control.step(self.observation, 0), "clock_reversed")

    def test_timeout_stops_despite_fresh_feedback(self):
        control = self.controller()
        control.step(self.observation, 0)
        self.assert_stopped(control.step(replace(self.observation, timestamp_s=1.0), 1.0),
                            "pour_timeout")

    def test_invalid_volume_confidence_frame_and_position_stop(self):
        for change in ({"volume_fraction": -0.1}, {"volume_fraction": 1.1},
                       {"volume_fraction": True},
                       {"confidence": 1.1}, {"frame": "camera"},
                       {"position_m": (0.3, 0.1)}):
            with self.subTest(change=change):
                self.assert_stopped(self.controller().step(replace(self.observation, **change), 0),
                                    "invalid_observation")

    def test_invalid_limits_rejected_before_pouring(self):
        for change in ({"target_volume_fraction": 0}, {"target_volume_fraction": 1.1},
                       {"min_confidence": 1.1},
                       {"max_pour_duration_s": float("nan")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.limits, **change)


if __name__ == "__main__":
    unittest.main()
