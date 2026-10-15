import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rebot_tea.hardware import LeRobotJointBackend, load_verified_hardware_config
from rebot_tea.joints import JOINT_NAMES, LimitedJointController


class FakeLeRobot:
    def __init__(self):
        self.is_connected = True
        self.observation = {f"{name}.pos": 0.0 for name in JOINT_NAMES}
        self.actions = []
        self.disabled = 0

    def get_observation(self):
        return dict(self.observation)

    def send_action(self, action):
        self.actions.append(dict(action))
        return dict(action)

    def disable_torque(self):
        self.disabled += 1

    def disconnect(self):
        self.is_connected = False


class HardwareAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "calibration.json").write_text("{}\n")
        (root / "parameters.json").write_text("{}\n")
        self.path = root / "hardware.json"
        self.data = {
            "robot": "B601-RS",
            "control_enabled": True,
            "hardware_verified": True,
            "usb_can_model": "commissioned adapter",
            "usb_vid_pid": "VID_0000&PID_0000",
            "owner_os": "linux",
            "channel": "can0",
            "firmware": "recorded-version",
            "calibration_record": "calibration.json",
            "parameter_readback_record": "parameters.json",
            "joint_names": list(JOINT_NAMES),
            "motor_ids": {name: index for index, name in enumerate(JOINT_NAMES, 1)},
            "motor_to_public_signs": [-1.0] * 7,
            "zero_offsets_rad": [0.0] * 7,
            "lower_rad": [-1.0] * 7,
            "upper_rad": [1.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0],
            "max_velocity_rad_s": [0.25] * 7,
            "max_acceleration_rad_s2": [0.5] * 7,
            "feedback_age_limit_s": 0.2,
            "arrival_tolerance_rad": 0.01,
            "arrival_samples": 3,
            "motion_timeout_s": 8.0,
            "stop_behavior": {
                "mode": "hold_position",
                "physically_verified": True,
                "notes": "Synthetic unit-test fixture; not a commissioning record",
            },
        }
        self.write_config()

    def tearDown(self):
        self.directory.cleanup()

    def write_config(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def test_complete_commissioned_config_loads(self):
        config = load_verified_hardware_config(self.path)
        self.assertEqual(config.motor_ids, tuple(range(1, 8)))
        self.assertEqual(config.channel, "can0")
        self.assertEqual(tuple(config.motor_can_ids), JOINT_NAMES)
        self.assertTrue(all(value > 0 for value in config.max_relative_target_deg.values()))

    def test_every_execution_gate_is_required(self):
        mutations = (
            ("control_enabled", False, "control_enabled"),
            ("hardware_verified", False, "hardware_verified"),
            ("owner_os", "windows", "owner_os"),
            ("channel", "can0@1000000", "channel"),
            ("calibration_record", "missing.json", "calibration_record"),
        )
        for key, value, reason in mutations:
            with self.subTest(key=key):
                original = self.data[key]
                self.data[key] = value
                self.write_config()
                with self.assertRaisesRegex(ValueError, reason):
                    load_verified_hardware_config(self.path)
                self.data[key] = original

    def test_profile_mismatch_is_rejected(self):
        self.data["motor_to_public_signs"][0] = 1.0
        self.write_config()
        with self.assertRaisesRegex(ValueError, "pinned LeRobot RS profile"):
            load_verified_hardware_config(self.path)

    def test_broad_or_fast_limits_are_rejected(self):
        self.data["upper_rad"][1] = 0.1
        self.write_config()
        with self.assertRaisesRegex(ValueError, "limits exceed"):
            load_verified_hardware_config(self.path)
        self.data["upper_rad"][1] = 0.0
        self.data["max_velocity_rad_s"][0] = 0.5
        self.write_config()
        with self.assertRaisesRegex(ValueError, "0.4 rad/s"):
            load_verified_hardware_config(self.path)

    def test_degree_radian_mapping_and_feedback(self):
        robot = FakeLeRobot()
        robot.observation["shoulder_pan.pos"] = 45.0
        backend = LeRobotJointBackend(robot, load_verified_hardware_config(self.path), lambda: 3.0)
        state = backend.read_state()
        self.assertAlmostEqual(state.positions_rad[0], math.pi / 4)
        self.assertEqual(state.timestamp_s, 3.0)
        target = (0.1, -0.1, -0.1, 0.1, 0.1, 0.1, -0.1)
        backend.send_target(target)
        self.assertAlmostEqual(robot.actions[-1]["shoulder_pan.pos"], math.degrees(0.1))
        self.assertEqual(tuple(key.removesuffix(".pos") for key in robot.actions[-1]), JOINT_NAMES)

    def test_missing_feedback_and_driver_clipping_are_faults(self):
        robot = FakeLeRobot()
        backend = LeRobotJointBackend(robot, load_verified_hardware_config(self.path))
        robot.observation.pop("gripper.pos")
        with self.assertRaisesRegex(ValueError, "gripper.pos"):
            backend.read_state()
        robot.observation["gripper.pos"] = 0.0
        robot.send_action = lambda action: {**action, "gripper.pos": action["gripper.pos"] + 1.0}
        with self.assertRaisesRegex(RuntimeError, "altered target"):
            backend.send_target((0.1, -0.1, -0.1, 0.1, 0.1, 0.1, -0.1))

    def test_stop_is_requested_but_never_claimed_as_physically_confirmed(self):
        robot = FakeLeRobot()
        config = load_verified_hardware_config(self.path)
        backend = LeRobotJointBackend(robot, config)
        backend.read_state()
        self.assertFalse(backend.request_stop())
        self.assertEqual(len(robot.actions), 1)

        robot = FakeLeRobot()
        backend = LeRobotJointBackend(robot, replace(config, stop_mode="disable_torque"))
        self.assertFalse(backend.request_stop())
        self.assertEqual(robot.disabled, 1)

    def test_authorized_adapter_can_enter_bounded_controller(self):
        robot = FakeLeRobot()
        config = load_verified_hardware_config(self.path)
        backend = LeRobotJointBackend(robot, config, lambda: 0.0)
        controller = LimitedJointController(backend, config.limits, lambda: 0.0)
        self.assertEqual(controller.state, "idle")

    def test_host_feedback_timestamp_covers_the_whole_observation_read(self):
        now = [0.0]
        robot = FakeLeRobot()
        original_read = robot.get_observation

        def delayed_observation():
            observation = original_read()
            now[0] += .25
            return observation

        robot.get_observation = delayed_observation
        config = load_verified_hardware_config(self.path)
        backend = LeRobotJointBackend(robot, config, lambda: now[0])
        state = backend.read_state()
        self.assertEqual(state.timestamp_s, 0.0)
        controller = LimitedJointController(backend, config.limits, lambda: now[0])
        with self.assertRaisesRegex(ValueError, "stale_feedback"):
            controller.start((0.,) * 7)
        self.assertEqual(robot.actions, [])


if __name__ == "__main__":
    unittest.main()
