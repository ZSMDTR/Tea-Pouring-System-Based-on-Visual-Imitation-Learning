"""Fail-closed, mock-only empty-teapot grasp/place sequence.

Commands are semantic requests, not joint targets, IK solutions or collision-checked
paths. Completion and grasp/release evidence must be supplied by the caller.
"""

import math
from dataclasses import asdict

from .planning import Step


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def _positive(config, name):
    value = config.get(name)
    if not _finite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


def _point(config, name):
    value = config.get(name)
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(map(_finite, value)):
        raise ValueError(f"{name} must be three finite metre coordinates")
    return tuple(map(float, value))


def _workspace(config):
    source = config.get("workspace_m")
    if not isinstance(source, dict):
        raise ValueError("workspace_m must specify x, y and z bounds")
    bounds = {}
    for axis in "xyz":
        pair = source.get(axis)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 \
                or not all(map(_finite, pair)) or pair[0] >= pair[1]:
            raise ValueError(f"Invalid workspace_m interval for {axis}")
        bounds[axis] = tuple(map(float, pair))
    return bounds


def plan_synthetic_cycle(config: dict) -> tuple[Step, ...]:
    """Check the entire semantic path before any mock command can be issued."""
    if not isinstance(config, dict) or config.get("synthetic") is not True \
            or config.get("robot") != {"model": "B601-RS", "backend": "mock"}:
        raise ValueError("An explicitly synthetic B601-RS mock configuration is required")
    if config.get("frame") != "base":
        raise ValueError("Synthetic targets must use the base frame")
    bounds = _workspace(config)
    grasp, place = _point(config, "grasp_m"), _point(config, "place_m")
    approach = _positive(config, "approach_m")
    lift = _positive(config, "lift_m")
    retreat = _positive(config, "retreat_m")
    _positive(config, "step_timeout_s")
    _positive(config, "max_feedback_age_s")

    def above(point, height):
        return point[0], point[1], point[2] + height

    steps = (
        Step("open_gripper"),
        Step("approach_grasp", above(grasp, approach)),
        Step("descend_grasp", grasp),
        Step("close_gripper"),
        Step("lift", above(grasp, lift)),
        Step("transfer", above(place, lift)),
        Step("descend_place", place),
        Step("release"),
        Step("retreat", above(place, retreat)),
    )
    for step in steps:
        if step.position_m is None:
            continue
        for axis, position in zip("xyz", step.position_m):
            lower, upper = bounds[axis]
            if not lower <= position <= upper:
                raise ValueError(f"{step.action}: {axis} outside synthetic workspace")
    return steps


class SyntheticGraspCycle:
    """Issue the next mock request only after fresh evidence for the current phase."""

    def __init__(self, config: dict):
        self.plan = plan_synthetic_cycle(config)
        self.step_timeout_s = float(config["step_timeout_s"])
        self.max_feedback_age_s = float(config["max_feedback_age_s"])
        self.state = "idle"
        self.reason = "not_started"
        self.index = 0
        self.phase_started_s = None
        self.last_now_s = None
        self.last_observed_s = None
        self.issued: list[Step] = []

    @property
    def current(self):
        return self.plan[self.index] if self.state == "running" else None

    def _abort(self, reason):
        self.state = "aborted"
        self.reason = reason
        return None

    def abort(self, reason):
        """Latch an external interruption without producing another command."""
        if self.state not in ("completed", "aborted"):
            self._abort(reason)
        return None

    def _clock(self, now_s):
        if not _finite(now_s):
            return self._abort("invalid_time")
        if self.last_now_s is not None and now_s < self.last_now_s:
            return self._abort("clock_reversal")
        self.last_now_s = float(now_s)
        return True

    def _issue(self, index, now_s):
        self.index = index
        self.phase_started_s = float(now_s)
        self.state = "running"
        command = self.plan[index]
        self.issued.append(command)
        return command

    def start(self, now_s):
        if self.state != "idle":
            return None
        if not self._clock(now_s):
            return None
        return self._issue(0, now_s)

    def tick(self, now_s):
        if self.state in ("completed", "aborted"):
            return None
        if not self._clock(now_s):
            return None
        if self.state == "idle":
            return None
        if now_s - self.phase_started_s >= self.step_timeout_s:
            if self.state == "running":
                return self._abort(f"step_timeout:{self.current.action}")
            return self._abort(f"{self.state}_timeout")
        # The current command was already issued; polling cannot reissue it.
        return None

    def _event(self, observed_at_s, now_s):
        if not _finite(observed_at_s) or observed_at_s > now_s \
                or observed_at_s < self.phase_started_s \
                or now_s - observed_at_s > self.max_feedback_age_s \
                or (self.last_observed_s is not None
                    and observed_at_s <= self.last_observed_s):
            return self._abort("stale_or_out_of_order_feedback")
        self.last_observed_s = float(observed_at_s)
        return True

    def report_completion(self, action, completed, observed_at_s, now_s):
        """A step completion never counts as evidence that an object was held/released."""
        if self.state in ("completed", "aborted"):
            return None
        self.tick(now_s)
        if self.state in ("completed", "aborted"):
            return None
        if self.state != "running" or action != self.current.action:
            return self._abort("unexpected_step_feedback")
        if not self._event(observed_at_s, now_s):
            return None
        if type(completed) is not bool:
            return self._abort("invalid_completion_evidence")
        if not completed:
            return self._abort(f"step_failed:{action}")
        if action == "close_gripper":
            self.state = "await_grasp"
            self.phase_started_s = float(now_s)
            return None
        if action == "release":
            self.state = "await_release"
            self.phase_started_s = float(now_s)
            return None
        if self.index == len(self.plan) - 1:
            self.state = "completed"
            self.reason = "synthetic_cycle_completed"
            return None
        return self._issue(self.index + 1, now_s)

    def _confirmation(self, expected_state, confirmed, observed_at_s, now_s):
        if self.state in ("completed", "aborted"):
            return None
        self.tick(now_s)
        if self.state in ("completed", "aborted"):
            return None
        if self.state != expected_state:
            return self._abort("unexpected_confirmation")
        if not self._event(observed_at_s, now_s):
            return None
        if type(confirmed) is not bool:
            return self._abort("invalid_confirmation_evidence")
        if not confirmed:
            return self._abort(f"{expected_state}_rejected")
        return self._issue(self.index + 1, now_s)

    def confirm_grasp(self, confirmed, observed_at_s, now_s):
        return self._confirmation("await_grasp", confirmed, observed_at_s, now_s)

    def confirm_release(self, confirmed, observed_at_s, now_s):
        return self._confirmation("await_release", confirmed, observed_at_s, now_s)

    def report(self):
        return {"synthetic": True, "robot_backend": "mock", "state": self.state,
                "reason": self.reason, "issued": [asdict(step) for step in self.issued],
                "physical_grasp_verified": False, "physical_release_verified": False,
                "ik_verified": False, "collision_checked": False}
