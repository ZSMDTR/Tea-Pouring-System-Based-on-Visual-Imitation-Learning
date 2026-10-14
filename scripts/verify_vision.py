"""Run actual pretrained inference and an optional synthetic training step."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from rebot_tea.vision import Detector, WEIGHT_NAME

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
parser.add_argument("--train-step", action="store_true")
args = parser.parse_args()
detector = Detector(root / "models" / WEIGHT_NAME, args.device)
predictions = detector.predict(np.zeros((320, 320, 3), dtype=np.uint8), .99, "cup")
result = {"torch": torch.__version__, "device": str(detector.device),
          "inference": "passed", "blank_image_cup_count": len(predictions),
          "training_step": "not requested", "real_accuracy_verified": False}
if args.device == "cuda":
    a = torch.randn(64, 64, device="cuda")
    assert torch.isfinite(a @ a).all()
    torch.cuda.synchronize()
    result["gpu"] = torch.cuda.get_device_name(0)
    result["capability"] = list(torch.cuda.get_device_capability())
if args.train_step:
    detector.model.train()
    optimizer = torch.optim.SGD(detector.model.parameters(), lr=1e-4)
    images = [torch.full((3, 320, 320), .5, device=detector.device) for _ in range(2)]
    targets = [{"boxes": torch.tensor([[100., 100., 200., 200.]], device=detector.device),
                "labels": torch.tensor([detector.categories.index("cup")],
                                       device=detector.device)} for _ in images]
    optimizer.zero_grad()
    losses = detector.model(images, targets)
    loss = sum(losses.values())
    if not torch.isfinite(loss):
        raise RuntimeError("Synthetic training loss is not finite")
    loss.backward()
    optimizer.step()
    result["training_step"] = "passed on synthetic input only"
    result["loss"] = float(loss.detach())
directory = root / "artifacts"
directory.mkdir(exist_ok=True)
(directory / f"vision-{args.device}.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
