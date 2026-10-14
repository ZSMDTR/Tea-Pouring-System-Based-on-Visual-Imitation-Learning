import math
import unittest
from dataclasses import replace

from rebot_tea.joints import (
    JointLimits, JointState, LimitedJointController, QuinticTrajectory, SimulatedJointBackend,
)


class JointTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.limits = JointLimits((-1.,) * 7, (1.,) * 7, (.5,) * 7, (1.,) * 7)

    def clock(self):
        return self.now

    def run_motion(self, scenario="normal"):
        backend = SimulatedJointBackend(self.clock, scenario=scenario)
        controller = LimitedJointController(backend, self.limits, self.clock)
        controller.start((.2,) * 7)
        for tick in range(260):
            self.now = tick / 50
            result = controller.tick()
            if result.state != "running":
                break
        return result, controller, backend

    def test_normal_motion_arrives_from_feedback(self):
        result, controller, backend = self.run_motion()
        self.assertEqual(result.state, "completed")
        self.assertGreaterEqual(controller.arrived, self.limits.arrival_samples)
        self.assertTrue(all(abs(q - .2) < .01 for q in backend.position))
        self.assertEqual(backend.stop_calls, 0)
        self.assertEqual(backend.history[0][1], (0.,) * 7)

    def test_exact_velocity_acceleration_bounds_and_end_conditions(self):
        trajectory = QuinticTrajectory.create((-.8,) * 7, (.9,) * 7, self.limits)
        for step in range(1001):
            q, dq, ddq = trajectory.sample(step / 1000 * trajectory.duration_s)
            self.assertTrue(all(-.8 - 1e-12 <= x <= .9 + 1e-12 for x in q))
            self.assertTrue(all(abs(x) <= .5 + 1e-12 for x in dq))
            self.assertTrue(all(abs(x) <= 1 + 1e-12 for x in ddq))
        for t in (0, trajectory.duration_s):
            _, dq, ddq = trajectory.sample(t)
            self.assertEqual(dq, (0.,) * 7)
            self.assertEqual(ddq, (0.,) * 7)

    def test_invalid_limits_are_rejected(self):
        for kwargs in ({"lower_rad": (0.,) * 7, "upper_rad": (0.,) * 7},
                       {"max_velocity_rad_s": (0.,) * 7},
                       {"max_acceleration_rad_s2": (math.nan,) * 7},
                       {"timeout_s": True}, {"arrival_samples": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                replace(self.limits, **kwargs)

    def test_bad_target_never_sends_a_command(self):
        for target in ((2.,) * 7, (0.,) * 6, (math.nan,) * 7, (True,) * 7):
            backend = SimulatedJointBackend(self.clock)
            controller = LimitedJointController(backend, self.limits, self.clock)
            with self.subTest(target=target), self.assertRaises(ValueError):
                controller.start(target)
            self.assertEqual(backend.history, [])

    def test_start_uses_current_position_and_checks_all_joints(self):
        backend = SimulatedJointBackend(self.clock, initial_rad=(.15,) * 7)
        controller = LimitedJointController(backend, self.limits, self.clock)
        controller.start((.2,) * 7)
        controller.tick()
        self.assertEqual(backend.history[0][1], (.15,) * 7)
        backend = SimulatedJointBackend(self.clock, initial_rad=(0.,) * 6 + (2.,))
        with self.assertRaises(ValueError):
            LimitedJointController(backend, self.limits, self.clock).start((0.,) * 7)
        self.assertEqual(backend.history, [])

    def test_faults_stop_once_and_never_auto_resume(self):
        for scenario, reason in (("lag", "stale_feedback"), ("missing", "missing_feedback"),
                                 ("stalled", "motion_timeout"), ("communication", "communication")):
            self.now = 0
            with self.subTest(scenario=scenario):
                result, controller, backend = self.run_motion(scenario)
                self.assertEqual(result.state, "aborted")
                self.assertIn(reason, result.reason)
                self.assertTrue(result.stop_requested)
                self.assertTrue(result.mock_stop_confirmed)
                count = len(backend.history)
                self.now += 1
                controller.tick()
                self.assertEqual(len(backend.history), count)
                self.assertEqual(backend.stop_calls, 1)
                with self.assertRaises(ValueError):
                    controller.start((0.,) * 7)

    def test_clock_faults_abort_without_another_target(self):
        for invalid in (-1, math.inf, math.nan):
            self.now = 0
            backend = SimulatedJointBackend(self.clock)
            controller = LimitedJointController(backend, self.limits, self.clock)
            controller.start((.2,) * 7)
            controller.tick()
            self.now = invalid
            self.assertEqual(controller.tick().state, "aborted")
            self.assertEqual(len(backend.history), 1)

    def test_runtime_feedback_invalidity_prevents_dispatch(self):
        for feedback in (None, JointState((True,) * 7, .1), JointState((0.,) * 7, .2),
                         JointState((2.,) * 7, .1), JointState((0.,) * 7, .1, "overheat")):
            self.now = 0
            backend = SimulatedJointBackend(self.clock)
            controller = LimitedJointController(backend, self.limits, self.clock)
            controller.start((.2,) * 7)
            self.now = .1
            backend.read_state = lambda: feedback
            self.assertEqual(controller.tick().state, "aborted")
            self.assertEqual(len(backend.history), 0)

    def test_repeated_feedback_cannot_confirm_arrival(self):
        backend = SimulatedJointBackend(self.clock)
        controller = LimitedJointController(backend, self.limits, self.clock)
        controller.start((0.,) * 7)
        backend.read_state = lambda: JointState((0.,) * 7, 0.)
        for tick in range(1, 10):
            self.now = tick / 50
            self.assertEqual(controller.tick().state, "running")
        self.assertEqual(controller.arrived, 0)
        self.now = .3
        self.assertIn("stale", controller.tick().reason)

    def test_stop_request_does_not_imply_confirmation(self):
        backend = SimulatedJointBackend(self.clock)
        controller = LimitedJointController(backend, self.limits, self.clock)
        controller.start((.2,) * 7)
        backend.request_stop = lambda: False
        decision = controller.stop()
        self.assertTrue(decision.stop_requested)
        self.assertFalse(decision.mock_stop_confirmed)

    def test_failed_stop_preserves_original_reason(self):
        backend = SimulatedJointBackend(self.clock)
        controller = LimitedJointController(backend, self.limits, self.clock)
        controller.start((.2,) * 7)

        def fail_stop():
            raise OSError("stop unavailable")

        backend.request_stop = fail_stop
        result = controller.stop("lost_feedback")
        self.assertEqual(result.reason, "lost_feedback")
        self.assertIn("stop unavailable", result.stop_error)
        self.assertFalse(result.mock_stop_confirmed)

    def test_non_mock_backend_is_rejected(self):
        backend = SimulatedJointBackend(self.clock)
        backend.mode = "hardware"
        with self.assertRaises(ValueError):
            LimitedJointController(backend, self.limits, self.clock)

    def test_zero_distance_requires_fresh_arrival_samples(self):
        result, _, _ = self.run_zero_motion()
        self.assertEqual(result.state, "completed")

    def run_zero_motion(self):
        backend = SimulatedJointBackend(self.clock)
        controller = LimitedJointController(backend, self.limits, self.clock)
        controller.start((0.,) * 7)
        for tick in range(4):
            self.now = tick / 50
            result = controller.tick()
        return result, controller, backend
