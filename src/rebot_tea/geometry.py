"""Aligned, rectified RGB-D projection. All 3D coordinates are metres."""
from dataclasses import dataclass

import numpy as np

from .perception import Detection


@dataclass(frozen=True)
class ImageDetection:
    label: str
    confidence: float
    bbox_xyxy: tuple[float, float, float, float]


def localize(detection: ImageDetection, depth: np.ndarray, calibration: dict) -> Detection:
    """Use a centre depth patch as a localization baseline, not a final TCP pose."""
    if calibration.get("depth_aligned_to_color") is not True:
        raise ValueError("Depth must be aligned to the color image")
    if calibration.get("rectified") is not True:
        raise ValueError("Inputs must be rectified before pinhole projection")
    width, height = calibration["width"], calibration["height"]
    if depth.shape != (height, width):
        raise ValueError("Depth shape does not match calibrated image resolution")
    fx, fy, cx, cy = (calibration[k] for k in ("fx", "fy", "cx", "cy"))
    scale = calibration["depth_scale_m"]
    if not np.isfinite([fx, fy, cx, cy, scale]).all() or min(fx, fy, scale) <= 0:
        raise ValueError("Invalid intrinsics or depth scale")
    box = np.asarray(detection.bbox_xyxy, dtype=float)
    if box.shape != (4,) or not np.isfinite(box).all():
        raise ValueError("Expected four finite bounding-box coordinates")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("Bounding box outside calibrated image")
    u, v = (x1 + x2) / 2, (y1 + y2) / 2
    col, row = min(int(round(u)), width-1), min(int(round(v)), height-1)
    patch = depth[max(0, row-2):row+3, max(0, col-2):col+3].astype(float) * scale
    valid = patch[np.isfinite(patch) & (patch > 0)]
    if valid.size < 5:
        raise ValueError("Insufficient valid depth at detection centre")
    z = float(np.median(valid))
    transform = np.asarray(calibration["T_base_camera"], dtype=float)
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        raise ValueError("Expected finite 4x4 T_base_camera")
    rotation = transform[:3, :3]
    if not (np.allclose(transform[3], [0, 0, 0, 1]) and
            np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) and
            np.isclose(np.linalg.det(rotation), 1, atol=1e-6)):
        raise ValueError("T_base_camera must be a rigid right-handed transform")
    point = transform @ np.array([(u-cx)*z/fx, (v-cy)*z/fy, z, 1])
    return Detection(detection.label, detection.confidence, "base",
                     tuple(float(value) for value in point[:3]))
