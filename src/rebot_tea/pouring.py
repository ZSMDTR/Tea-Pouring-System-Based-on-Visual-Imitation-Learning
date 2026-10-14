"""Offline pouring decisions only; no robot, camera or liquid sensor is connected."""
import math
from dataclasses import dataclass
from numbers import Real


def _finite(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class CupObservation:
    cup_id: str
    timestamp_s: float
    position_m: tuple[float, float, float]
    volume_fraction: float  # Liquid volume / calibrated cup capacity; NOT height or confidence.
    confidence: float
    grasp_verified: bool = False
    spout_aligned: bool = False
    frame: str = "base"


@dataclass(frozen=True)
class PourLimits:
    target_volume_fraction: float
    min_confidence: float
    max_observation_age_s: float
    cup_motion_tolerance_m: float
    max_pour_duration_s: float

    def __post_init__(self):
        positive = (self.max_observation_age_s, self.cup_motion_tolerance_m,
                    self.max_pour_duration_s)
        if not all(_finite(value) and value > 0 for value in positive):
            raise ValueError("Pour distances and durations must be positive and finite")
        if not _finite(self.target_volume_fraction) or not 0 < self.target_volume_fraction <= 1:
            raise ValueError("target_volume_fraction must be in (0, 1]")
        if not _finite(self.min_confidence) or not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be in [0, 1]")


@dataclass(frozen=True)
class PourDecision:
    state: str
    reason: str
    actions: tuple[str, ...]
    cup_position_m: tuple[float, float, float] | None
    physical_stop_confirmed: bool = False
    residual_flow_settled: bool = False


class PourController:
    """Call step regularly, including with None on missing data, to check timeouts.

    Each observation must include current grasp and upstream spout-alignment evidence.
    Actions are semantic mock requests; completion is not a hardware acknowledgement.
    Neither the stop request nor the terminal state confirms settled residual flow.
    A fresh controller must be explicitly created for any new pouring attempt.
    """

    def __init__(self, cup_id: str, limits: PourLimits):
        if not isinstance(cup_id, str) or not cup_id:
            raise ValueError("A fixed nonempty target cup_id is required")
        self.cup_id = cup_id
        self.limits = limits
        self.state = "ready"
        self._reason = "ready"
        self._cup_position = None
        self._started_s = None
        self._last_now_s = None
        self._last_timestamp_s = None

    def _stop(self, state: str, reason: str) -> PourDecision:
        self.state, self._reason = state, reason
        return PourDecision(state, reason, ("stop_pour", "upright"), self._cup_position)

    def step(self, observation: CupObservation | None, now_s: float) -> PourDecision:
        if self.state in ("complete", "aborted"):
            return PourDecision(self.state, self._reason, (), self._cup_position)
        if not _finite(now_s):
            return self._stop("aborted", "invalid_clock")
        if self._last_now_s is not None and now_s < self._last_now_s:
            return self._stop("aborted", "clock_reversed")
        if (self._started_s is not None
                and now_s - self._started_s >= self.limits.max_pour_duration_s):
            return self._stop("aborted", "pour_timeout")
        if observation is None:
            return self._stop("aborted", "missing_observation")
        if not isinstance(observation, CupObservation):
            return self._stop("aborted", "invalid_observation")
        if observation.grasp_verified is not True:
            reason = "grasp_unverified" if self._started_s is None else "grasp_lost"
            return self._stop("aborted", reason)
        if observation.spout_aligned is not True:
            reason = "spout_unverified" if self._started_s is None else "spout_alignment_lost"
            return self._stop("aborted", reason)
        if observation.cup_id != self.cup_id:
            return self._stop("aborted", "cup_identity_changed")
        position = observation.position_m
        values = (observation.timestamp_s, observation.volume_fraction, observation.confidence)
        if (observation.frame != "base" or not all(map(_finite, values))
                or not isinstance(position, (tuple, list)) or len(position) != 3
                or not all(map(_finite, position)) or not 0 <= observation.volume_fraction <= 1
                or not 0 <= observation.confidence <= 1):
            return self._stop("aborted", "invalid_observation")
        age = now_s - observation.timestamp_s
        if age < 0:
            return self._stop("aborted", "future_observation")
        if age > self.limits.max_observation_age_s:
            return self._stop("aborted", "stale_observation")
        if (self._last_timestamp_s is not None
                and observation.timestamp_s <= self._last_timestamp_s):
            return self._stop("aborted", "observation_time_not_increasing")
        if observation.confidence < self.limits.min_confidence:
            return self._stop("aborted", "low_confidence")
        if (self._cup_position is not None
                and math.dist(position, self._cup_position) > self.limits.cup_motion_tolerance_m):
            return self._stop("aborted", "cup_moved")
        first = self._started_s is None
        if first:
            self._started_s = now_s
            self._cup_position = tuple(position)
        self._last_now_s, self._last_timestamp_s = now_s, observation.timestamp_s
        if observation.volume_fraction >= self.limits.target_volume_fraction:
            return self._stop("complete", "target_volume_reached")
        self.state = "pouring"
        return PourDecision(self.state, "below_target_volume", ("tilt_increment",), self._cup_position)
