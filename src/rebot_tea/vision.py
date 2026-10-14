"""Pretrained COCO baseline; inference never opens a robot connection."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

import cv2
import torch
from torchvision.models.detection import (
    SSDLite320_MobileNet_V3_Large_Weights,
    ssdlite320_mobilenet_v3_large,
)

from .geometry import ImageDetection

WEIGHT_NAME = "ssdlite320_mobilenet_v3_large_coco-a79551df.pth"


class Detector:
    def __init__(self, weights: Path, device: str = "auto"):
        if not weights.is_file():
            raise FileNotFoundError("Run python scripts/prepare_model.py first")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        self.device = torch.device(device)
        self.categories = SSDLite320_MobileNet_V3_Large_Weights.COCO_V1.meta["categories"]
        self.model = ssdlite320_mobilenet_v3_large(
            weights=None, weights_backbone=None, num_classes=len(self.categories))
        self.model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
        self.model.to(self.device).eval()

    def predict(self, image_bgr, min_confidence=.5, label=None) -> list[ImageDetection]:
        if image_bgr is None or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
            raise ValueError("Expected a BGR color image")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be in [0, 1]")
        if label is not None and label not in self.categories:
            raise ValueError(f"Unknown COCO label: {label}")
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).float().div(255).to(self.device)
        with torch.inference_mode():
            result = self.model([tensor])[0]
        detections = []
        for box, score, category in zip(result["boxes"].cpu(), result["scores"].cpu(),
                                        result["labels"].cpu()):
            name = self.categories[int(category)]
            if float(score) >= min_confidence and (label is None or name == label):
                detections.append(ImageDetection(name, float(score), tuple(box.tolist())))
        return detections


def main():
    parser = argparse.ArgumentParser(description="Image detection only; no physical motion")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--weights", type=Path, default=Path("models") / WEIGHT_NAME)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--label", default="cup")
    parser.add_argument("--threshold", type=float, default=.5)
    parser.add_argument("--output", type=Path, default=Path("artifacts/detections.json"))
    args = parser.parse_args()
    image = cv2.imread(str(args.image))
    detections = Detector(args.weights, args.device).predict(image, args.threshold, args.label)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([asdict(d) for d in detections], indent=2) + "\n")
    annotated = image.copy()
    for item in detections:
        x1, y1, x2, y2 = map(int, item.bbox_xyxy)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 200, 0), 2)
        cv2.putText(annotated, f"{item.label} {item.confidence:.2f}", (x1, max(y1-8, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 200, 0), 1)
    cv2.imwrite(str(args.output.with_suffix(".png")), annotated)
    print(f"{len(detections)} detections -> {args.output}; no grasp executed")


if __name__ == "__main__":
    main()
