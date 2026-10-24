import math
import random
import unittest
from dataclasses import replace

from rebot_tea.gamepad_bridge import GamepadFrame
from rebot_tea.gamepad_control import JointJogger
from rebot_tea.joints import JointLimits, JointState


class GamepadControlTests(unittest.TestCase):
    def setUp(self):
        self.limits = JointLimits((-1.0,) * 7, (1.0,) * 7, (0.5,) * 7, (1.0,) * 7)
        self.jogger = JointJogger(self.limits)
        self.now = 0.0
        self.sequence = 0
        self.position = (0.25,) * 7

    def frame(self, buttons=(), axes=(0.0,) * 6, **kwargs):
        self.sequence += 1
        values = dict(received_s=self.now, sequence=self.sequence, axes=axes,
                      buttons=frozenset(buttons), connected=True, synthetic=True)
        values.update(kwargs)
        return GamepadFrame(**values)

    def step(self, buttons=(), axes=(0.0,) * 6, dt=0.02, position=None, **kwargs):
        self.now += dt
        if position is not None:
            self.position = position
        frame = self.frame(buttons, axes, **kwargs)
        return self.jogger.step(frame, JointState(self.position, self.now), self.now)

    def test_starts_at_feedback_and_requires_released_neutral(self):
        result = self.step(("LB",), (1, 0, 0, 0, 0, 0))
        self.assertFalse(result.active)
        self.assertEqual(result.target_rad, self.position)
        self.assertFalse(self.step(axes=(1, 0, 0, 0, 0, 0)).active)
        self.assertFalse(self.step(("LB",)).active)
        self.assertEqual(self.step().reason, "ready")
        self.assertTrue(self.step(("LB",)).active)

    def test_deadzone_normalization_and_group_axis_mapping(self):
        self.jogger = JointJogger(replace(
            self.limits, lower_rad=(-100.0,) * 7, upper_rad=(100.0,) * 7,
            max_acceleration_rad_s2=(100.0,) * 7,
        ))
        self.step()
        result = self.step(("LB",), (0.56, -1, 1, 0.12, 0, 1))
        expected = list(self.position)
        expected[0] += 0.25 * 0.02
        expected[1] -= 0.5 * 0.02
        expected[6] += 0.5 * 0.02
        for actual, wanted in zip(result.target_rad, expected):
            self.assertAlmostEqual(actual, wanted)
        self.step()
        self.assertEqual(self.step(("RB",)).group, 1)
        result = self.step(("LB",), (1, -1, 0, 1, 1, 0))
        self.assertEqual(result.target_rad[:3], self.position[:3])
        self.assertGreater(result.target_rad[3], self.position[3])
        self.assertLess(result.target_rad[4], self.position[4])
        self.assertGreater(result.target_rad[5], self.position[5])
        self.assertLess(result.target_rad[6], self.position[6])

    def test_rb_switch_is_an_edge_and_only_when_released_and_neutral(self):
        self.step()
        self.assertEqual(self.step(("RB",)).group, 1)
        self.assertEqual(self.step(("RB",)).group, 1)
        self.step()
        self.assertEqual(self.step(("LB", "RB")).group, 1)
        self.step()
        self.assertEqual(self.step(("RB",), (0, 0, 1, 0, 0, 0)).group, 1)
        self.step()
        self.assertEqual(self.step(("RB",), (0, 0, 0, 0, 1, 1)).group, 1)
        self.step()
        self.assertEqual(self.step(("RB",)).group, 0)

    def test_release_locks_feedback_once_instead_of_following_drift(self):
        self.step()
        self.step(("LB",), (1, 0, 0, 0, 0, 0))
        stopped = self.step(position=(0.3,) * 7)
        self.assertFalse(stopped.active)
        self.assertEqual(stopped.target_rad, (0.3,) * 7)
        self.assertEqual(self.step(position=(0.31,) * 7).target_rad, (0.3,) * 7)
        result = self.step(("LB",), position=(0.32,) * 7)
        self.assertTrue(result.active)
        self.assertEqual(result.target_rad, (0.32,) * 7)

    def test_actual_dt_velocity_and_acceleration_limits_including_soft_limits(self):
        rng = random.Random(42)
        self.step(position=(0.99, -0.99, 0.0, 0.0, 0.0, 0.0, 0.99))
        previous = self.position
        old_velocity = (0.0,) * 7
        for tick in range(1500):
            dt = rng.uniform(0.003, 0.1)
            sign = 1 if tick % 180 < 90 else -1
            axes = (sign, -sign, 0, sign, float(sign < 0), float(sign > 0))
            result = self.step(("LB",), axes, dt=dt, position=previous)
            velocity = tuple((b - a) / dt for a, b in zip(previous, result.target_rad))
            self.assertTrue(result.active)
            for index, q in enumerate(result.target_rad):
                self.assertTrue(math.isfinite(q) and -1 <= q <= 1)
                self.assertLessEqual(abs(velocity[index]), 0.5 + 1e-10)
                self.assertLessEqual(abs(velocity[index] - old_velocity[index]), dt + 1e-9)
            previous, old_velocity = result.target_rad, velocity

    def test_outward_jog_at_soft_limit_stays_bounded(self):
        self.step(position=(1.0,) * 7)
        for _ in range(20):
            result = self.step(("LB",), (1, 1, 0, 1, 0, 1), position=(1.0,) * 7)
            self.assertEqual(result.target_rad, (1.0,) * 7)
        result = self.step(("LB",), (-1, -1, 0, -1, 1, 0))
        self.assertLess(result.target_rad[0], 1)
        self.assertLess(result.target_rad[6], 1)

    def test_loss_modes_require_a_new_neutral_release(self):
        for mode in ("missing", "disconnected", "expired", "sequence_reset"):
            with self.subTest(mode=mode):
                self.setUp()
                self.step()
                self.step(("LB",), (1, 0, 0, 0, 0, 0))
                self.now += 0.02
                frame = self.frame(("LB",))
                if mode == "missing":
                    frame = None
                elif mode == "disconnected":
                    frame = replace(frame, connected=False)
                elif mode == "expired":
                    frame = replace(frame, received_s=self.now - 0.21)
                else:
                    frame = replace(frame, sequence=0)
                result = self.jogger.step(frame, JointState(self.position, self.now), self.now)
                self.assertFalse(result.active)
                self.assertFalse(self.step(("LB",)).active)
                self.assertFalse(self.step(axes=(1, 0, 0, 0, 0, 0)).active)
                self.assertFalse(self.step(("LB",)).active)
                self.step()
                self.assertTrue(self.step(("LB",)).active)

    def test_long_tick_and_pause_do_not_accumulate_movement(self):
        self.step()
        self.step(("LB",), (1, 0, 0, 0, 0, 0))
        result = self.step(("LB",), (1, 0, 0, 0, 0, 0), dt=0.5)
        self.assertEqual(result.reason, "tick_timeout")
        self.assertEqual(result.target_rad, self.position)
        self.assertFalse(self.step(("LB",)).active)
        self.step()
        self.assertTrue(self.step(("LB",)).active)
        self.jogger.pause()
        self.assertFalse(self.step(("LB",), dt=3).active)
        self.step()
        self.assertTrue(self.step(("LB",)).active)

    def test_event_edges_ignore_initial_holds_and_duplicate_sequences(self):
        self.assertEqual(self.step(("A", "B", "X", "Y")).events, ())
        self.step()
        self.assertEqual(self.step(("A", "B", "X", "Y")).events,
                         ("record_start", "record_success", "record_discard", "record_failure"))
        duplicate = self.frame(("A",), sequence=self.sequence)
        for _ in range(3):
            self.now += 0.02
            result = self.jogger.step(duplicate, JointState(self.position, self.now), self.now)
            self.assertEqual(result.events, ())
        self.assertEqual(self.step(("A", "B", "X", "Y")).events, ())

    def test_duplicate_sequence_cannot_refresh_input_timeout_or_change_axes(self):
        self.step()
        result = self.step(("LB",))
        sequence = self.sequence
        for tick in range(1, 13):
            self.now += 0.02
            frame = self.frame(("LB",), (1, 0, 0, 0, 0, 0), sequence=sequence)
            result = self.jogger.step(frame, JointState(self.position, self.now), self.now)
            self.assertEqual(result.target_rad, self.position)
            if result.reason == "input_timeout":
                break
        self.assertEqual(result.reason, "input_timeout")

    def test_feedback_faults_and_bad_time_raise_and_clear_permission(self):
        for bad_state, bad_now, reason in (
            (None, 0.04, "missing_feedback"),
            (JointState((0.25,) * 7, 0.04, "overheat"), 0.04, "motor_fault"),
            (JointState((0.25,) * 7, -1), 0.04, "stale_feedback"),
            (JointState((0.25,) * 7, 0.05), 0.04, "future_feedback"),
            (JointState((0.25,) * 7, math.nan), 0.04, "invalid_feedback_time"),
            (JointState((2.0,) * 7, 0.04), 0.04, "joint_limit_exceeded"),
            (JointState((0.25,) * 6 + (math.inf,), 0.04), 0.04, "finite"),
            (JointState((0.25,) * 7, 0.02), 0.02, "clock_not_increasing"),
            (JointState((0.25,) * 7, 0.04), math.nan, "invalid_clock"),
        ):
            with self.subTest(reason=reason):
                self.setUp()
                self.step()
                with self.assertRaisesRegex(ValueError, reason):
                    self.jogger.step(self.frame(), bad_state, bad_now)
                self.now = 0.06
                self.assertFalse(self.step(("LB",)).active)

    def test_reversed_feedback_or_input_time_raises(self):
        self.step()
        with self.assertRaisesRegex(ValueError, "feedback_time_reversed"):
            self.jogger.step(self.frame(), JointState(self.position, 0.01), 0.04)
        self.setUp()
        self.step()
        self.now = 0.04
        with self.assertRaisesRegex(ValueError, "input_time_reversed"):
            self.jogger.step(self.frame(received_s=0.01), JointState(self.position, self.now), self.now)

    def test_malformed_input_and_future_input_raise(self):
        for kwargs in ({"axes": (0,) * 5}, {"axes": (math.nan,) * 6},
                       {"axes": (0, 0, 0, 0, -1, 0)}, {"sequence": True},
                       {"received_s": math.nan}, {"received_s": 1.0},
                       {"buttons": {"LB"}}, {"connected": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.setUp()
                self.now = 0.02
                frame = replace(self.frame(), **kwargs)
                self.jogger.step(frame, JointState(self.position, self.now), self.now)

    def test_bad_configuration_is_rejected(self):
        for kwargs in ({"deadzone": 1}, {"deadzone": -0.1}, {"deadzone": True},
                       {"input_timeout_s": 0}, {"input_timeout_s": math.inf}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                JointJogger(self.limits, **kwargs)


if __name__ == "__main__":
    unittest.main()
