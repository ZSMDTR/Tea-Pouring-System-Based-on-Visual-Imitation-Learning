"""Pure joint-jog calculations; this module never sends a robot command."""
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .joints import JointLimits, JointState, finite_number

if TYPE_CHECKING:
    from .gamepad_bridge import GamepadFrame


@dataclass(frozen=True)
class JogDecision:
    target_rad: tuple[float, ...]
    active: bool
    reason: str
    group: int
    events: tuple[str, ...] = ()


class JointJogger:
    """Require neutral/released LB before arming, then hold LB to jog.

    Group 0 maps LX/LY/RY to shoulder_pan/shoulder_lift/elbow_flex;
    group 1 maps them to wrist_flex/wrist_yaw/wrist_roll. RT-LT is a
    signed gripper rate, with no assumption about physical open direction.
    Input loss and tick_timeout require the caller's validated stop procedure;
    their returned target is informational and must not be sent as a command.
    """

    max_tick_s = 0.1

    def __init__(self, limits: JointLimits, deadzone=0.12, input_timeout_s=0.2):
        if not isinstance(limits, JointLimits):
            raise ValueError("limits must be verified JointLimits")
        if not finite_number(deadzone) or not 0 <= deadzone < 1:
            raise ValueError("deadzone must be finite and in [0, 1)")
        if not finite_number(input_timeout_s) or input_timeout_s <= 0:
            raise ValueError("input_timeout_s must be positive and finite")
        self.limits = limits
        self.deadzone = deadzone
        self.input_timeout_s = input_timeout_s
        self.group = 0
        self._last_feedback_s = None
        self.pause()

    def pause(self):
        """Forget permission and timing; a new neutral release is required."""
        self._ready = False
        self._active = False
        self._target = None
        self._velocity = (0.0,) * 7
        self._last_now = None
        self._frame = None
        self._buttons = None

    def _feedback(self, state, now):
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
        if self._last_feedback_s is not None and state.timestamp_s < self._last_feedback_s:
            raise ValueError("feedback_time_reversed")
        position = self.limits.check_position(state.positions_rad)
        self._last_feedback_s = state.timestamp_s
        return position

    def _validate_frame(self, frame, now):
        try:
            valid = (type(frame.sequence) is int and frame.sequence >= 0
                     and type(frame.connected) is bool
                     and finite_number(frame.received_s)
                     and isinstance(frame.axes, (list, tuple)) and len(frame.axes) == 6
                     and all(finite_number(x) and -1 <= x <= 1 for x in frame.axes[:4])
                     and all(finite_number(x) and 0 <= x <= 1 for x in frame.axes[4:])
                     and isinstance(frame.buttons, frozenset)
                     and all(isinstance(x, str) for x in frame.buttons))
        except AttributeError:
            valid = False
        if not valid:
            raise ValueError("invalid_gamepad_frame")
        if frame.received_s > now:
            raise ValueError("future_input")

    def _inactive(self, position, reason):
        self.pause()
        self._target = position
        return JogDecision(position, False, reason, self.group)

    def _axis(self, value):
        magnitude = max(0.0, (abs(value) - self.deadzone) / (1 - self.deadzone))
        return math.copysign(magnitude, value)

    def _integrate(self, rates, dt):
        if not dt:
            return
        targets, velocities = [], []
        for q, previous, rate, lo, hi, vmax, accel in zip(
            self._target, self._velocity, rates, self.limits.lower_rad,
            self.limits.upper_rad, self.limits.max_velocity_rad_s,
            self.limits.max_acceleration_rad_s2,
        ):
            # Keep a braking margin for the largest permitted next tick.
            # This avoids an abrupt velocity reset when reaching a soft limit.
            margin = 2 * accel * self.max_tick_s
            forward = math.sqrt(margin * margin + 2 * accel * (hi - q)) - margin
            reverse = math.sqrt(margin * margin + 2 * accel * (q - lo)) - margin
            desired = min(forward, max(-reverse, rate * vmax))
            velocity = min(previous + accel * dt, max(previous - accel * dt, desired))
            target = min(hi, max(lo, q + velocity * dt))
            velocities.append((target - q) / dt)
            targets.append(target)
        self._target = self.limits.check_position(tuple(targets))
        self._velocity = tuple(velocities)

    def step(self, frame: "GamepadFrame | None", state: JointState, now: float) -> JogDecision:
        try:
            if not finite_number(now):
                raise ValueError("invalid_clock")
            if self._last_now is not None and now <= self._last_now:
                raise ValueError("clock_not_increasing")
            position = self._feedback(state, now)
            dt = 0.0 if self._last_now is None else now - self._last_now
            self._last_now = now
            if self._target is None:
                self._target = position
            if dt > self.max_tick_s + 1e-12:
                return self._inactive(position, "tick_timeout")
            if frame is None:
                return self._inactive(position, "input_missing")
            self._validate_frame(frame, now)
            if not frame.connected:
                return self._inactive(position, "input_disconnected")
            if now - frame.received_s > self.input_timeout_s:
                return self._inactive(position, "input_timeout")
            if self._frame is not None and frame.sequence < self._frame.sequence:
                return self._inactive(position, "input_reset")
            fresh = self._frame is None or frame.sequence > self._frame.sequence
            if fresh:
                if self._frame is not None and frame.received_s < self._frame.received_s:
                    raise ValueError("input_time_reversed")
                self._frame = frame
            else:
                # Replaying a sequence cannot renew its age or change controls.
                frame = self._frame
            if now - frame.received_s > self.input_timeout_s:
                return self._inactive(position, "input_timeout")
            buttons = frame.buttons
            rising = buttons - self._buttons if fresh and self._buttons is not None else frozenset()
            if fresh:
                self._buttons = buttons
            events = tuple(event for button, event in (
                ("A", "record_start"), ("B", "record_success"),
                ("X", "record_discard"), ("Y", "record_failure"),
            ) if button in rising)
            neutral = all(abs(x) <= self.deadzone for x in frame.axes)
            if "RB" in rising and "LB" not in buttons and neutral:
                self.group = 1 - self.group
            if "LB" not in buttons:
                if self._active:
                    self._target = position
                self._active = False
                self._velocity = (0.0,) * 7
                self._ready = self._ready or neutral
                reason = "ready" if neutral and self._ready else "holding"
                if not self._ready:
                    reason = "awaiting_neutral"
                return JogDecision(self._target, False, reason, self.group, events)
            if not self._ready:
                return JogDecision(self._target, False, "awaiting_neutral", self.group, events)
            if not self._active:
                self._target, self._velocity = position, (0.0,) * 7
            self._active = True
            rates = [0.0] * 7
            rates[self.group * 3:self.group * 3 + 3] = (
                self._axis(frame.axes[0]), self._axis(frame.axes[1]), self._axis(frame.axes[3]),
            )
            rates[6] = self._axis(frame.axes[5] - frame.axes[4])
            self._integrate(rates, dt)
            return JogDecision(self._target, True, "active", self.group, events)
        except ValueError:
            self.pause()
            raise
