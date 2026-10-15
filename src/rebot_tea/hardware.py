"""Commissioning-gated adapter for LeRobot's official reBot B601-RS driver.

The module has no LeRobot import at module load time.  Configuration can be
checked in the small project environment; the actual connection factory must
run in the pinned training environment where LeRobot is installed.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .joints import JOINT_NAMES, JointLimits, JointState, joint_vector

_EXPECTED_MOTOR_IDS = tuple(range(1, 8))
_LEROBOT_RS_SIGNS = (-1.0,) * 7
_STOP_MODES = {"hold_position", "disable_torque"}
_PINNED_RS_LIMITS_DEG = (
    (-145.0, 145.0),
    (-170.0, 0.0),
    (-200.0, 0.0),
    (-90.0, 80.0),
    (-90.0, 90.0),
    (-90.0, 90.0),
    (-270.0, 0.0),
)
_MAX_FIRST_COMMISSIONING_VELOCITY_RAD_S = 0.4
_MAX_FIRST_COMMISSIONING_ACCELERATION_RAD_S2 = 1.0


class LeRobotArm(Protocol):
    """The small part of the LeRobot Robot API used by this project."""

    is_connected: bool

    def get_observation(self) -> Mapping[str, Any]: ...
    def send_action(self, action: Mapping[str, float]) -> Mapping[str, float]: ...
    def disable_torque(self) -> None: ...
    def disconnect(self) -> None: ...


def _finite_sequence(value: Any, name: str) -> tuple[float, ...]:
    try:
        return joint_vector(value, name)
    except ValueError as error:
        raise ValueError(f"hardware config: {error}") from error


def _required_text(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"hardware config: {name} must be a non-empty string")
    return value.strip()


def _required_bool(data: Mapping[str, Any], name: str) -> bool:
    value = data.get(name)
    if type(value) is not bool:
        raise ValueError(f"hardware config: {name} must be true or false")
    return value


def _record_path(config_path: Path, data: Mapping[str, Any], name: str) -> Path:
    value = Path(_required_text(data, name))
    path = value if value.is_absolute() else config_path.parent / value
    if not path.is_file():
        raise ValueError(f"hardware config: {name} does not exist: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"hardware config: {name} is empty: {path}")
    return path.resolve()


@dataclass(frozen=True)
class VerifiedHardwareConfig:
    """Values that must come from the physical commissioning record."""

    source: Path
    channel: str
    usb_can_model: str
    firmware: str
    motor_ids: tuple[int, ...]
    limits: JointLimits
    stop_mode: str
    calibration_record: Path
    parameter_readback_record: Path

    @property
    def joint_limits_deg(self) -> dict[str, tuple[float, float]]:
        return {
            name: (math.degrees(lower), math.degrees(upper))
            for name, lower, upper in zip(
                JOINT_NAMES, self.limits.lower_rad, self.limits.upper_rad
            )
        }

    @property
    def motor_can_ids(self) -> dict[str, tuple[int, int]]:
        return {name: (motor_id, 0xFD) for name, motor_id in zip(JOINT_NAMES, self.motor_ids)}

    @property
    def max_relative_target_deg(self) -> dict[str, float]:
        # The outer controller runs at 50 Hz.  This is a second per-command
        # guard in LeRobot, not the primary trajectory velocity limit.
        return {
            name: math.degrees(velocity / 50.0) * 1.05
            for name, velocity in zip(JOINT_NAMES, self.limits.max_velocity_rad_s)
        }


def load_verified_hardware_config(path: str | Path) -> VerifiedHardwareConfig:
    """Load a config only when every physical commissioning gate is satisfied."""

    source = Path(path).resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"hardware config cannot be read: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("hardware config: root must be an object")
    if data.get("robot") != "B601-RS":
        raise ValueError("hardware config: robot must be B601-RS")
    if not _required_bool(data, "hardware_verified"):
        raise ValueError("hardware config: hardware_verified must be true")
    if not _required_bool(data, "control_enabled"):
        raise ValueError("hardware config: control_enabled must be true")
    if _required_text(data, "owner_os").lower() != "linux":
        raise ValueError("hardware config: this LeRobot adapter requires owner_os=linux")

    channel = _required_text(data, "channel")
    if re.fullmatch(r"can[0-9]+", channel) is None:
        raise ValueError("hardware config: channel must be a Linux SocketCAN name such as can0")
    usb_can_model = _required_text(data, "usb_can_model")
    _required_text(data, "usb_vid_pid")
    firmware = _required_text(data, "firmware")

    if tuple(data.get("joint_names") or ()) != JOINT_NAMES:
        raise ValueError("hardware config: joint_names must match JOINT_NAMES exactly")
    motor_ids_data = data.get("motor_ids")
    if not isinstance(motor_ids_data, dict) or set(motor_ids_data) != set(JOINT_NAMES):
        raise ValueError("hardware config: motor_ids must be an object keyed by every JOINT_NAMES item")
    motor_ids = tuple(motor_ids_data[name] for name in JOINT_NAMES)
    if any(type(value) is not int for value in motor_ids) or motor_ids != _EXPECTED_MOTOR_IDS:
        raise ValueError("hardware config: this adapter requires verified RS motor IDs 1 through 7")

    signs = _finite_sequence(data.get("motor_to_public_signs"), "motor_to_public_signs")
    if signs != _LEROBOT_RS_SIGNS:
        raise ValueError("hardware config: measured signs do not match the pinned LeRobot RS profile")
    offsets = _finite_sequence(data.get("zero_offsets_rad"), "zero_offsets_rad")
    if any(abs(value) > 1e-12 for value in offsets):
        raise ValueError("hardware config: non-zero software offsets are unsupported; set motor zero first")

    limits = JointLimits(
        _finite_sequence(data.get("lower_rad"), "lower_rad"),
        _finite_sequence(data.get("upper_rad"), "upper_rad"),
        _finite_sequence(data.get("max_velocity_rad_s"), "max_velocity_rad_s"),
        _finite_sequence(data.get("max_acceleration_rad_s2"), "max_acceleration_rad_s2"),
        max_feedback_age_s=data.get("feedback_age_limit_s"),
        arrival_tolerance_rad=data.get("arrival_tolerance_rad"),
        arrival_samples=data.get("arrival_samples"),
        timeout_s=data.get("motion_timeout_s"),
    )
    for name, lower, upper, (profile_lower_deg, profile_upper_deg) in zip(
        JOINT_NAMES, limits.lower_rad, limits.upper_rad, _PINNED_RS_LIMITS_DEG
    ):
        if lower < math.radians(profile_lower_deg) or upper > math.radians(profile_upper_deg):
            raise ValueError(
                f"hardware config: {name} limits exceed the pinned LeRobot RS profile"
            )
    if any(
        value > _MAX_FIRST_COMMISSIONING_VELOCITY_RAD_S
        for value in limits.max_velocity_rad_s
    ):
        raise ValueError("hardware config: first-commissioning velocity must not exceed 0.4 rad/s")
    if any(
        value > _MAX_FIRST_COMMISSIONING_ACCELERATION_RAD_S2
        for value in limits.max_acceleration_rad_s2
    ):
        raise ValueError("hardware config: first-commissioning acceleration must not exceed 1.0 rad/s^2")
    if limits.max_feedback_age_s > 0.5:
        raise ValueError("hardware config: feedback age limit must not exceed 0.5 s")
    if limits.arrival_tolerance_rad > 0.05:
        raise ValueError("hardware config: arrival tolerance must not exceed 0.05 rad")
    if limits.arrival_samples < 3:
        raise ValueError("hardware config: arrival_samples must be at least 3")
    if limits.timeout_s > 30.0:
        raise ValueError("hardware config: motion timeout must not exceed 30 s")

    stop = data.get("stop_behavior")
    if not isinstance(stop, dict) or stop.get("mode") not in _STOP_MODES:
        raise ValueError(
            "hardware config: stop_behavior.mode must be hold_position or disable_torque"
        )
    if stop.get("physically_verified") is not True:
        raise ValueError("hardware config: stop behavior must be physically verified")
    _required_text(stop, "notes")

    return VerifiedHardwareConfig(
        source=source,
        channel=channel,
        usb_can_model=usb_can_model,
        firmware=firmware,
        motor_ids=motor_ids,
        limits=limits,
        stop_mode=stop["mode"],
        calibration_record=_record_path(source, data, "calibration_record"),
        parameter_readback_record=_record_path(source, data, "parameter_readback_record"),
    )


class LeRobotJointBackend:
    """Convert the project's radian vectors to LeRobot's degree dictionaries."""

    mode = "hardware"
    execution_authorized = True

    def __init__(
        self,
        robot: LeRobotArm,
        config: VerifiedHardwareConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not robot.is_connected:
            raise ValueError("LeRobot robot must already be connected")
        if not isinstance(config, VerifiedHardwareConfig):
            raise TypeError("config must come from load_verified_hardware_config")
        self.robot = robot
        self.config = config
        self.clock = clock
        self.last_positions_rad: tuple[float, ...] | None = None
        self.stop_calls = 0
        self.command_count = 0

    @staticmethod
    def _action(positions_rad: tuple[float, ...]) -> dict[str, float]:
        values = joint_vector(positions_rad)
        return {f"{name}.pos": math.degrees(value) for name, value in zip(JOINT_NAMES, values)}

    def read_state(self) -> JointState:
        # LeRobot returns no individual acquisition timestamps. Conservatively
        # date the group from the start of the read, so slow sequential reads
        # cannot make the oldest joint appear fresh at completion.
        read_started_s = self.clock()
        observation = self.robot.get_observation()
        values = []
        for name in JOINT_NAMES:
            key = f"{name}.pos"
            value = observation.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"invalid or missing LeRobot feedback: {key}")
            values.append(math.radians(float(value)))
        positions = self.config.limits.check_position(values)
        self.last_positions_rad = positions
        return JointState(positions, read_started_s)

    def send_target(self, positions_rad: tuple[float, ...]) -> None:
        target = self.config.limits.check_position(positions_rad)
        requested = self._action(target)
        sent = self.robot.send_action(requested)
        if set(sent) != set(requested):
            raise RuntimeError("LeRobot did not report all seven commands")
        for key, value in sent.items():
            if not math.isfinite(float(value)) or abs(float(value) - requested[key]) > 1e-6:
                raise RuntimeError(f"LeRobot altered target {key}; check limits and mapping")
        self.command_count += 1

    def request_stop(self) -> bool:
        self.stop_calls += 1
        if self.config.stop_mode == "hold_position":
            if self.last_positions_rad is None:
                raise RuntimeError("no valid feedback is available for a hold-position stop")
            self.robot.send_action(self._action(self.last_positions_rad))
        else:
            self.robot.disable_torque()
        # Command acceptance cannot prove that the physical arm has stopped.
        return False

    def close(self) -> None:
        if self.robot.is_connected:
            self.robot.disconnect()


def connect_lerobot_backend(
    config: VerifiedHardwareConfig,
    clock: Callable[[], float] = time.monotonic,
) -> LeRobotJointBackend:
    """Connect the pinned LeRobot B601-RS driver without running calibration."""

    try:
        from lerobot.robots.rebot_b601_follower import (
            RebotB601Follower,
            RebotB601FollowerRobotConfig,
        )
    except ImportError as error:
        raise RuntimeError("Run hardware control with .venv-training; LeRobot is unavailable") from error

    robot_config = RebotB601FollowerRobotConfig(
        id="rebot-tea-b601-rs",
        port=config.channel,
        motor_family="rs",
        can_adapter="socketcan",
        disable_torque_on_disconnect=config.stop_mode == "disable_torque",
        max_relative_target=config.max_relative_target_deg,
        motor_can_ids=config.motor_can_ids,
        joint_limits=config.joint_limits_deg,
    )
    robot = RebotB601Follower(robot_config)
    try:
        robot.connect(calibrate=False)
        backend = LeRobotJointBackend(robot, config, clock)
        backend.read_state()
        return backend
    except Exception:
        if robot.is_connected:
            robot.disconnect()
        raise
