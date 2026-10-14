"""Fixture input; replace with camera + model + calibrated 3D localization."""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    frame: str
    position_m: tuple[float, float, float]


def load_fixture(path: Path) -> Detection:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Detection(data["label"], data["confidence"], data["frame"],
                     tuple(data["position_m"]))
