"""Public joint coordinates and bounded trajectories, with a mock backend only."""
import math
from dataclasses import dataclass
from typing import Callable, Protocol

JOINT_NAMES = (
    "shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex",
    "wrist_yaw", "wrist_roll", "gripper",
)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def joint_vector(values, name="joint vector"):
    if not isinstance(values, (tuple, list)) or len(values) != len(JOINT_NAMES):
        raise ValueError(f"{name} must contain seven joints in JOINT_NAMES order")
    if not all(finite_number(value) for value in values):
        raise ValueError(f"{name} must contain finite numbers, not booleans")
    return tuple(float(value) for value in values)


@dataclass(frozen=True)
class JointState:
    positions_rad: tuple[float, ...]
    timestamp_s: float
    fault: str | None = None


@dataclass(frozen=True)
class JointLimits:
    lower_rad: tuple[float, ...]
    upper_rad: tuple[float, ...]
    max_velocity_rad_s: tuple[float, ...]
    max_acceleration_rad_s2: tuple[float, ...]
    max_feedback_age_s: float = 0.2
    arrival_tolerance_rad: float = 0.01
    arrival_samples: int = 3
    timeout_s: float = 5.0

    def __post_init__(self):
        for name in ("lower_rad", "upper_rad", "max_velocity_rad_s", "max_acceleration_rad_s2"):
            object.__setattr__(self, name, joint_vector(getattr(self, name), name))
        if any(lo >= hi for lo, hi in zip(self.lower_rad, self.upper_rad)):
            raise ValueError("Each lower limit must be below its upper limit")
        if any(value <= 0 for value in self.max_velocity_rad_s + self.max_acceleration_rad_s2):
            raise ValueError("Velocity and acceleration limits must be positive")
        for name in ("max_feedback_age_s", "arrival_tolerance_rad", "timeout_s"):
            value = getattr(self, name)
            if not finite_number(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if type(self.arrival_samples) is not int or self.arrival_samples < 1:
            raise ValueError("arrival_samples must be a positive integer")

    def check_position(self, position):
        values = joint_vector(position)
        if any(not lo <= q <= hi for q, lo, hi in zip(values, self.lower_rad, self.upper_rad)):
            raise ValueError("joint_limit_exceeded")
        return values


@dataclass(frozen=True)
class QuinticTrajectory:
    start_rad: tuple[float, ...]
    target_rad: tuple[float, ...]
    duration_s: float

    @classmethod
    def create(cls, start, target, limits):
        start, target = limits.check_position(start), limits.check_position(target)
        # Exact extrema of d/ds and d2/ds2 of 10s^3 - 15s^4 + 6s^5.
        duration = max(
            max(1.875 * abs(b - a) / v, math.sqrt((10 / math.sqrt(3)) * abs(b - a) / acc))
            for a, b, v, acc in zip(
                start, target, limits.max_velocity_rad_s, limits.max_acceleration_rad_s2
            )
        )
        return cls(start, target, duration)

    def sample(self, elapsed_s):
        if not finite_number(elapsed_s):
            raise ValueError("Trajectory time must be finite")
        s = min(1.0, max(0.0, elapsed_s / self.duration_s)) if self.duration_s else 1.0
        scale = 10 * s**3 - 15 * s**4 + 6 * s**5
        velocity = (30 * s**2 - 60 * s**3 + 30 * s**4) / self.duration_s if self.duration_s else 0
        acceleration = (60 * s - 180 * s**2 + 120 * s**3) / self.duration_s**2 \
            if self.duration_s else 0
        delta = tuple(b - a for a, b in zip(self.start_rad, self.target_rad))
        return (
            tuple(a + scale * d for a, d in zip(self.start_rad, delta)),
            tuple(velocity * d for d in delta),
            tuple(acceleration * d for d in delta),
        )


class JointBackend(Protocol):
    mode: str

    def read_state(self) -> JointState | None: ...
    def send_target(self, positions_rad: tuple[float, ...]) -> None: ...
    def request_stop(self) -> bool: ...


@dataclass(frozen=True)
class JointDecision:
    state: str
    reason: str
    elapsed_s: float
    stop_requested: bool = False
    mock_stop_confirmed: bool = False
    target_rad: tuple[float, ...] | None = None
    stop_error: str | None = None


class LimitedJointController:
    """Faults latch. No SDK import; only explicitly mock backends are accepted."""

    def __init__(self, backend: JointBackend, limits: JointLimits, clock: Callable[[], float]):
        if getattr(backend, "mode", None) != "mock":
            raise ValueError("Only mock joint execution is available before physical commissioning")
        self.backend, self.limits, self.clock = backend, limits, clock
        self.state = "idle"
        self.trajectory = None
        self.started_s = None
        self.last_tick_s = None
        self.last_sample_s = None
        self.arrived = 0
        self.decision = JointDecision("idle", "not_started", 0)

    def _check_feedback(self, state, now):
        if not isinstance(state, JointState):
            raise ValueError("missing_feedback")
        if state.fault:
            raise ValueError(f"motor_fault: {state.fault}")
        if not finite_number(state.timestamp_s):
            raise ValueError("invalid_feedback_time")
        if state.timestamp_s > now:
            raise ValueError("future_feedback")
        if now - state.timestamp_s > self.limits.max_feedback_age_s:
            raise ValueError("stale_feedback")
        if self.last_sample_s is not None and state.timestamp_s < self.last_sample_s:
            raise ValueError("feedback_time_reversed")
        return self.limits.check_position(state.positions_rad)

    def start(self, target_rad):
        if self.state != "idle":
            raise ValueError("Create a new controller after completion or abort")
        now = self.clock()
        if not finite_number(now):
            raise ValueError("invalid_clock")
        target = self.limits.check_position(target_rad)
        state = self.backend.read_state()
        position = self._check_feedback(state, now)
        trajectory = QuinticTrajectory.create(position, target, self.limits)
        if trajectory.duration_s >= self.limits.timeout_s:
            raise ValueError("Trajectory leaves no time for settling before timeout")
        self.trajectory, self.started_s = trajectory, now
        self.last_sample_s = state.timestamp_s
        self.state = "running"
        self.decision = JointDecision("running", "started_from_feedback", 0)
        return self.decision

    def stop(self, reason="operator_stop", elapsed_s=None):
        if self.state in ("completed", "aborted"):
            return self.decision
        confirmed, error = False, None
        try:
            confirmed = self.backend.request_stop() is True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.state = "aborted"
        elapsed = self.decision.elapsed_s if elapsed_s is None else elapsed_s
        self.decision = JointDecision("aborted", reason, elapsed, True, confirmed, stop_error=error)
        return self.decision

    def tick(self):
        if self.state != "running":
            return self.decision
        now = self.clock()
        if not finite_number(now):
            return self.stop("invalid_clock")
        if now < self.started_s or (self.last_tick_s is not None and now <= self.last_tick_s):
            return self.stop("clock_not_increasing")
        elapsed = now - self.started_s
        self.last_tick_s = now
        if elapsed >= self.limits.timeout_s:
            return self.stop("motion_timeout", elapsed)
        try:
            feedback = self.backend.read_state()
            position = self._check_feedback(feedback, now)
            fresh = feedback.timestamp_s != self.last_sample_s
            self.last_sample_s = feedback.timestamp_s
            at_target = all(abs(a - b) <= self.limits.arrival_tolerance_rad
                            for a, b in zip(position, self.trajectory.target_rad))
            if fresh:
                self.arrived = self.arrived + 1 if at_target and elapsed >= self.trajectory.duration_s \
                    else 0
            if self.arrived >= self.limits.arrival_samples:
                self.state = "completed"
                self.decision = JointDecision("completed", "arrival_confirmed", elapsed)
                return self.decision
            target, _, _ = self.trajectory.sample(elapsed)
            self.backend.send_target(target)
            self.decision = JointDecision("running", "tracking", elapsed, target_rad=target)
        except Exception as error:
            return self.stop(f"{type(error).__name__}: {error}", elapsed)
        return self.decision


class SimulatedJointBackend:
    mode = "mock"

    def __init__(self, clock, initial_rad=(0.0,) * 7, scenario="normal", fault_after_s=0.4):
        if scenario not in ("normal", "lag", "missing", "stalled", "communication"):
            raise ValueError("Unknown mock fault scenario")
        self.clock, self.scenario, self.fault_after_s = clock, scenario, fault_after_s
        self.position = joint_vector(initial_rad)
        self.target = self.position
        self.last_time = clock()
        self.started_s = self.last_time
        self.history = []
        self.stop_calls = 0
        self.stopped = False

    def read_state(self):
        now = self.clock()
        failing = now - self.started_s >= self.fault_after_s
        if failing and self.scenario == "communication":
            raise OSError("synthetic CAN communication failure")
        if failing and self.scenario == "missing":
            return None
        dt = max(0, now - self.last_time)
        if self.scenario != "stalled" and not self.stopped:
            fraction = 1 - math.exp(-dt / 0.03)
            self.position = tuple(a + fraction * (b - a) for a, b in zip(self.position, self.target))
        self.last_time = now
        timestamp = now - 0.5 if failing and self.scenario == "lag" else now
        return JointState(self.position, timestamp)

    def send_target(self, positions_rad):
        if self.stopped:
            raise RuntimeError("Mock backend has stopped")
        self.target = joint_vector(positions_rad)
        self.history.append((self.clock(), self.target))

    def request_stop(self):
        self.stop_calls += 1
        self.stopped = True
        self.target = self.position
        return True
