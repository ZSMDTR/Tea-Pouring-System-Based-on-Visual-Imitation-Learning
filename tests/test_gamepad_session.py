import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rebot_tea import gamepad_session as session
from rebot_tea.gamepad_bridge import GamepadFrame
from rebot_tea.gamepad_control import JointJogger
from rebot_tea.joints import JointLimits, JointState


class FakeClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class FakeBridge:
    def __init__(self, clock, controls=None):
        self.clock, self.controls, self.index = clock, controls, 0

    def snapshot(self):
        index = self.index
        self.index += 1
        if self.controls is not None:
            values = self.controls(index)
            if values is None:
                return None
        else:
            values = {}
        return GamepadFrame(self.clock(), index + 1,
                            values.get("axes", (0.0,) * 6),
                            frozenset(values.get("buttons", ())),
                            values.get("connected", True), values.get("synthetic", True))


class FakeBackend:
    def __init__(self, clock, log, mode="mock", fail_send=None):
        self.clock, self.log, self.mode = clock, log, mode
        self.position = (0.25,) * 7
        self.sent, self.send_attempts, self.stop_calls, self.reads = [], 0, 0, 0
        self.fail_send = fail_send

    def read_state(self):
        self.reads += 1
        return JointState(self.position, self.clock())

    def send_target(self, target):
        self.send_attempts += 1
        if self.send_attempts == self.fail_send:
            raise OSError("injected send failure")
        target = tuple(target)
        self.sent.append((self.clock(), target))
        self.log.append((self.clock(), "send", target))
        self.position = target

    def request_stop(self):
        self.stop_calls += 1
        self.log.append((self.clock(), "stop"))
        return True


class FakeRecorder:
    fps = 20

    def __init__(self, clock, backend, log, recording=True, encode_s=0):
        self.clock, self.backend, self.log = clock, backend, log
        self.is_recording, self.encode_s = recording, encode_s
        self.frames, self.current, self.episodes_saved = [], [], 0

    @property
    def frames_in_episode(self):
        return len(self.current)

    def start(self):
        self.log.append((self.clock(), "start"))
        self.is_recording = True

    def add_frame(self, image, state, target, timing):
        if not self.is_recording or tuple(target) != self.backend.sent[-1][1]:
            raise AssertionError("Recorder received an unsent target")
        row = {"state": tuple(state), "target": tuple(target), "timing": dict(timing),
               "pixel": int(image[0, 0, 2])}
        self.frames.append(row)
        self.current.append(row)
        self.log.append((self.clock(), "add_frame", tuple(target)))

    def finish(self, outcome):
        self.log.append((self.clock(), "finish", outcome))
        self.clock.sleep(self.encode_s)
        self.episodes_saved += bool(self.current)
        self.current = []
        self.is_recording = False

    def discard(self):
        self.log.append((self.clock(), "discard"))
        self.current = []
        self.is_recording = False


class GamepadSessionTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.log = FakeClock(), []
        self.limits = JointLimits((-1.0,) * 7, (1.0,) * 7, (0.5,) * 7, (1.0,) * 7)
        self.backend = FakeBackend(self.clock, self.log)
        self.recorder = FakeRecorder(self.clock, self.backend, self.log)

    def run_session(self, controls=None, duration=0.2, camera=None):
        with redirect_stdout(io.StringIO()):
            return session.run_session(
                self.backend, FakeBridge(self.clock, controls),
                camera or session.SyntheticCamera(self.clock), self.recorder,
                self.limits, duration=duration, clock=self.clock, sleep=self.clock.sleep,
            )

    def test_input_loss_stops_once_without_sending_another_command(self):
        for disconnected in (False, True):
            with self.subTest(disconnected=disconnected):
                self.setUp()

                def controls(index):
                    if index == 3:
                        return {"connected": False} if disconnected else None
                    return {} if index == 0 else {"buttons": ("LB",), "axes": (1, 0, 0, 0, 0, 0)}

                report = self.run_session(controls)
                self.assertEqual(report["status"], "stopped_on_error")
                self.assertIn("input_", report["error"])
                self.assertEqual(len(self.backend.sent), 3)
                self.assertEqual(self.backend.stop_calls, 1)
                self.assertTrue(report["mock_stop_confirmed"])
                self.assertFalse(report["physical_stop_confirmed"])

    def test_synthetic_input_is_rejected_before_any_hardware_read_or_send(self):
        self.backend.mode = "hardware"
        report = self.run_session()
        self.assertEqual(report["status"], "stopped_on_error")
        self.assertIn("synthetic_input_rejected", report["error"])
        self.assertEqual(self.backend.reads, 0)
        self.assertEqual(self.backend.sent, [])
        self.assertEqual(self.recorder.frames, [])
        self.assertEqual(self.backend.stop_calls, 1)
        self.assertFalse(report["mock_stop_confirmed"])
        self.assertFalse(report["physical_task_verified"])

    def test_send_failure_does_not_enter_recorder_or_count_as_sent(self):
        self.backend.fail_send = 2
        report = self.run_session()
        self.assertEqual(report["status"], "stopped_on_error")
        self.assertIn("injected send failure", report["error"])
        self.assertEqual(report["commands_sent"], 1)
        self.assertEqual(self.backend.send_attempts, 2)
        self.assertEqual(len(self.recorder.frames), 1)
        self.assertEqual(self.backend.stop_calls, 1)

    def test_success_event_holds_then_pauses_before_encoding_and_requires_rearm(self):
        decisions, log, clock = [], self.log, self.clock

        class ObservedJogger(JointJogger):
            def pause(self):
                log.append((clock(), "pause"))
                super().pause()

            def step(self, *args):
                result = super().step(*args)
                decisions.append(result)
                return result

        def controls(index):
            if index == 0 or index == 5:
                return {}
            if index == 7:
                return None
            return {"buttons": ("LB", "B") if index in (2, 3) else ("LB",),
                    "axes": (1, 0, 0, 0, 0, 0)}

        self.recorder.encode_s = 0.3
        with patch.object(session, "JointJogger", ObservedJogger):
            report = self.run_session(controls, duration=2)
        self.assertEqual([d.active for d in decisions], [False, True, True, False, False, False, True])
        self.assertEqual(report["episodes_saved"], 1)
        self.assertEqual([event["event"] for event in report["events"]], ["record_success"])
        finish = next(i for i, entry in enumerate(self.log) if entry[1] == "finish")
        self.assertEqual([entry[1] for entry in self.log[finish - 2:finish]], ["send", "pause"])
        sent_before_event = self.backend.sent[1][1]
        hold = self.log[finish - 2][2]
        self.assertEqual(hold, sent_before_event)
        self.assertEqual(decisions[3].target_rad, hold)
        self.assertEqual(decisions[4].target_rad, hold)
        self.assertEqual(decisions[5].target_rad, hold)
        self.assertGreater(decisions[6].target_rad[0], hold[0])
        self.assertNotIn("tick_timeout", report.get("error", ""))

    def test_recorded_target_is_exactly_the_sent_action_with_measured_timing(self):
        def controls(index):
            return {} if index == 0 else {"buttons": ("LB",), "axes": (0.7, -0.6, 0, 0.5, 0, 0.8)}

        report = self.run_session(controls, duration=0.4)
        self.assertEqual(report["status"], "duration_completed")
        self.assertGreater(report["active_ticks"], 0)
        self.assertGreater(len(self.recorder.frames), 3)
        sent = {at: target for at, target in self.backend.sent}
        for row in self.recorder.frames:
            timing = row["timing"]
            self.assertEqual(row["target"], sent[timing["command_sent_s"]])
            self.assertLessEqual(timing["input_received_s"], timing["observation_read_s"])
            self.assertLessEqual(timing["observation_read_s"], timing["command_sent_s"])
        self.assertNotEqual(self.recorder.frames[-1]["target"], self.recorder.frames[-1]["state"])
        self.assertEqual(report["recorded_frames"], len(self.recorder.frames))

    def test_50_hz_control_samples_about_20_unique_camera_frames_per_second(self):
        report = self.run_session(duration=1)
        self.assertEqual(report["status"], "duration_completed")
        self.assertTrue(49 <= report["commands_sent"] <= 51)
        self.assertTrue(19 <= len(self.recorder.frames) <= 21, len(self.recorder.frames))
        camera_times = [row["timing"]["camera_host_s"] for row in self.recorder.frames]
        self.assertEqual(len(set(camera_times)), len(camera_times))
        intervals = [b - a for a, b in zip(camera_times, camera_times[1:])]
        self.assertTrue(all(0.039 <= gap <= 0.061 for gap in intervals))

    def test_camera_staleness_prevents_command_and_recording(self):
        camera = session.SyntheticCamera(lambda: self.clock() - 0.3)
        report = self.run_session(camera=camera)
        self.assertIn("camera_stale_or_future", report["error"])
        self.assertEqual(self.backend.sent, [])
        self.assertEqual(self.recorder.frames, [])
        self.assertEqual(self.backend.stop_calls, 1)

    def test_neutral_check_blocks_held_controls_and_synthetic_hardware(self):
        neutral = GamepadFrame(10, 1, (0.0,) * 6, frozenset(), True, False)
        session.require_neutral_input(neutral, hardware=True)
        for frame in (None, GamepadFrame(10, 1, (0.0,) * 6, frozenset(), False),
                      GamepadFrame(10, 1, (0.0,) * 6, frozenset({"LB"})),
                      GamepadFrame(10, 1, (0, 0, 0, 0, 0, 0.5), frozenset()),
                      GamepadFrame(10, 1, (0.0,) * 6, frozenset(), True, True)):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                session.require_neutral_input(frame, hardware=True)


class GamepadCliGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("gamepad_teleop_test", cls.project / "scripts/gamepad_teleop.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def args(self, **overrides):
        values = dict(backend="mock", execute=False, hardware_config=None,
                      camera="synthetic", duration=0)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_default_mock_limits_do_not_connect_a_device(self):
        with patch.object(self.cli, "connect_lerobot_backend") as connect:
            config, limits = self.cli.session_config(self.args())
            self.assertIsNone(config)
            self.assertIsInstance(limits, JointLimits)
            connect.assert_not_called()

    def test_physical_flags_and_false_template_are_rejected_before_connection(self):
        template = self.project / "config/hardware.template.json"
        for args in (
            self.args(backend="hardware"),
            self.args(backend="hardware", execute=True, hardware_config=template),
            self.args(backend="hardware", execute=True, hardware_config=template, camera="gemini2"),
            self.args(execute=True), self.args(hardware_config=template),
            self.args(duration=-1), self.args(duration=float("nan")),
        ):
            with self.subTest(args=args), patch.object(self.cli, "connect_lerobot_backend") as connect:
                with self.assertRaises(ValueError):
                    self.cli.session_config(args)
                connect.assert_not_called()

    def test_cli_rejects_physical_setup_before_creating_camera_or_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with patch.object(self.cli, "InputBridge") as bridge, \
                    patch.object(self.cli, "SyntheticCamera") as camera, \
                    patch.object(self.cli, "connect_lerobot_backend") as connect, \
                    redirect_stdout(io.StringIO()):
                code = self.cli.main(["--backend", "hardware", "--output", str(output)])
            self.assertEqual(code, 1)
            bridge.assert_not_called()
            camera.assert_not_called()
            connect.assert_not_called()
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "setup_failed")
            self.assertFalse(report["physical_task_verified"])


if __name__ == "__main__":
    unittest.main()
