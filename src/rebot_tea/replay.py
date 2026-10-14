"""Offline RGB-D -> localization -> task plan -> mock execution."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from .geometry import ImageDetection, localize
from .planning import plan_grasp
from .robot import MockRobot


def replay(scene_path: Path, config_path: Path, backend="fixture", device="auto") -> dict:
    scene = json.loads(scene_path.read_text())
    config = json.loads(config_path.read_text())
    if config["robot"] != {"model": "B601-RS", "backend": "mock"}:
        raise ValueError("Offline replay only permits the mock robot")
    if backend not in ("fixture", "ssdlite"):
        raise ValueError("Unknown replay detector")
    image = cv2.imread(str(scene_path.parent / scene["color"]))
    if image is None:
        raise ValueError("Color image is missing; generate demo or supply recorded data")
    depth = np.load(scene_path.parent / scene["depth"], allow_pickle=False)
    calibration = json.loads((scene_path.parent / scene["calibration"]).read_text())
    if image.shape[:2] != (calibration["height"], calibration["width"]):
        raise ValueError("Color image shape does not match calibration")
    if backend == "fixture":
        if scene.get("synthetic") is not True:
            raise ValueError("Fixture detections are only permitted for synthetic scenes")
        objects = [ImageDetection(d["label"], d["confidence"], tuple(d["bbox_xyxy"]))
                   for d in scene["detections"]]
    else:
        from .vision import Detector, WEIGHT_NAME
        objects = Detector(config_path.parent.parent / "models" / WEIGHT_NAME, device).predict(
            image, config["planning"]["min_confidence"], "cup")
    if not objects:
        raise ValueError("No cup detected; no grasp steps executed")
    selected = max((d for d in objects if d.label == "cup"), key=lambda d: d.confidence,
                   default=None)
    if selected is None:
        raise ValueError("No cup detected; no grasp steps executed")
    target = localize(selected, depth, calibration)
    steps = plan_grasp(target, config["planning"])
    robot = MockRobot()
    robot.execute(steps)
    return {"synthetic": scene.get("synthetic", False), "detector": backend,
            "robot_backend": "mock", "target": asdict(target),
            "steps": [asdict(step) for step in robot.history],
            "physical_grasp_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=Path("fixtures/rgbd_scene.json"))
    parser.add_argument("--config", type=Path, default=Path("config/simulation.json"))
    parser.add_argument("--detector", choices=["fixture", "ssdlite"], default="fixture")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--output", type=Path, default=Path("artifacts/replay.json"))
    args = parser.parse_args()
    result = replay(args.scene, args.config, args.detector, args.device)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Offline mock replay -> {args.output}")


if __name__ == "__main__":
    main()
