import sys
import unittest

from rebot_tea.connection import probe, validate_channel

CHANNEL = "can0@1000000" if sys.platform == "win32" else "can0"


class FakeMotor:
    def __init__(self, motor_id, calls, failures):
        self.motor_id, self.calls, self.failures = motor_id, calls, failures

    def robstride_ping_host_id(self, host, timeout):
        self.calls.append(("ping", self.motor_id, host, timeout))
        if self.failures.get((self.motor_id, "ping")):
            raise TimeoutError("no ping")
        return self.motor_id, host

    def robstride_get_param_f32_host_id(self, param, host, timeout):
        self.calls.append(("read", self.motor_id, param, host, timeout))
        value = self.failures.get((self.motor_id, "position"), 0.0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.calls.append(("motor_close", self.motor_id))


class FakeController:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}

    def add_robstride_motor(self, motor_id, host, model):
        self.calls.append(("add", motor_id, host, model))
        if self.failures.get((motor_id, "registration")):
            raise RuntimeError("registration failure")
        return FakeMotor(motor_id, self.calls, self.failures)

    def close_bus(self):
        self.calls.append(("close_bus",))

    def close(self):
        self.calls.append(("controller_close",))


class ConnectionTests(unittest.TestCase):
    def run_probe(self, failures=None):
        controller = FakeController(failures)
        report = probe(CHANNEL, controller_factory=lambda channel: controller, clock=lambda: 12.5)
        return report, controller.calls

    def test_reads_seven_fresh_positions_with_correct_model_hints(self):
        report, calls = self.run_probe()
        self.assertTrue(report["passed"])
        self.assertEqual([m["position_rad"] for m in report["motors"]], [0.] * 7)
        self.assertTrue(all(m["position_sample_monotonic_s"] == 12.5 for m in report["motors"]))
        added = [call for call in calls if call[0] == "add"]
        self.assertEqual([item[3] for item in added], ["rs-06"] * 3 + ["rs-00"] * 4)
        self.assertEqual(calls[-2:], [("close_bus",), ("controller_close",)])

    def test_never_calls_enable_disable_zero_or_motion(self):
        _, calls = self.run_probe()
        # Fake API deliberately exposes no actuation methods; any such call would fail.
        allowed = {"add", "ping", "read", "motor_close", "close_bus", "controller_close"}
        self.assertTrue(all(call[0] in allowed for call in calls))
        self.assertEqual(len([call for call in calls if call[0] == "motor_close"]), 7)

    def test_parameter_read_can_confirm_online_after_ping_timeout(self):
        report, _ = self.run_probe({(2, "ping"): True})
        self.assertTrue(report["passed"])
        self.assertIn("ping", report["motors"][1]["errors"])

    def test_ping_without_fresh_position_is_not_ready(self):
        report, _ = self.run_probe({(4, "position"): TimeoutError("no position")})
        self.assertFalse(report["passed"])
        self.assertTrue(report["motors"][3]["online"])
        self.assertIsNone(report["motors"][3]["position_rad"])
        self.assertIsNone(report["motors"][3]["position_sample_monotonic_s"])

    def test_nonfinite_missing_and_boolean_positions_are_rejected(self):
        for value in [float("nan"), float("inf"), None, True]:
            with self.subTest(value=value):
                report, _ = self.run_probe({(1, "ping"): True, (1, "position"): value})
                self.assertFalse(report["passed"])
                self.assertFalse(report["motors"][0]["online"])
                self.assertIsNone(report["motors"][0]["position_rad"])

    def test_missing_motor_and_registration_failure_do_not_skip_cleanup(self):
        report, calls = self.run_probe({(3, "registration"): True})
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["motors"]), 7)
        self.assertEqual(calls[-2:], [("close_bus",), ("controller_close",)])

    def test_rejects_invalid_input_before_bus_creation(self):
        def factory(channel):
            self.fail("must not open a bus")
        for timeout in [0, -1, True, 1.5, 5001]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                probe(CHANNEL, timeout, controller_factory=factory)
        with self.assertRaises(ValueError):
            probe("can0; echo unsafe", controller_factory=factory)

    def test_platform_specific_channel_names(self):
        validate_channel("can0", "linux")
        validate_channel("can0@1000000", "win32")
        for name, platform in [("can0@1000000", "linux"), ("COM9", "win32"),
                               ("can0@500000", "win32")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_channel(name, platform)
