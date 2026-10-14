"""Task waypoints only: these are NOT IK solutions or collision-free trajectories."""
import math
from dataclasses import dataclass

from .perception import Detection


@dataclass(frozen=True)
class Step:
    action: str
    position_m: tuple[float, float, float] | None = None


def plan_grasp(target: Detection, config: dict) -> list[Step]:
    threshold = config["min_confidence"]
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("min_confidence must be in [0, 1]")
    if not math.isfinite(target.confidence) or not threshold <= target.confidence <= 1:
        raise ValueError("Detection confidence rejected")
    if target.frame != config["frame"] or target.frame != "base":
        raise ValueError("Expected a calibrated detection in base frame")
    if len(target.position_m) != 3 or not all(map(math.isfinite, target.position_m)):
        raise ValueError("Expected three finite coordinates in metres")
    approach, lift = config["approach_m"], config["lift_m"]
    if not all(math.isfinite(v) and v > 0 for v in (approach, lift)):
        raise ValueError("Approach and lift must be positive finite distances")
    x, y, z = target.position_m
    steps = [
        Step("open_gripper"),
        Step("approach", (x, y, z + approach)),
        Step("descend", (x, y, z)),
        Step("close_gripper"),
        Step("lift", (x, y, z + lift)),
    ]
    # Validate the whole plan before the first simulated action.
    for step in steps:
        if step.position_m is None:
            continue
        for axis, value in zip("xyz", step.position_m):
            lower, upper = config["workspace_m"][axis]
            if not all(map(math.isfinite, (lower, upper))) or lower >= upper:
                raise ValueError(f"Invalid workspace interval for {axis}")
            if not lower <= value <= upper:
                raise ValueError(f"{step.action}: {axis} outside demo workspace")
    return steps
