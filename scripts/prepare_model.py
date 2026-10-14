"""Download hash-checked official TorchVision COCO weights; no robot access."""
from pathlib import Path

import torch
from torchvision.models.detection import SSDLite320_MobileNet_V3_Large_Weights

root = Path(__file__).resolve().parents[1]
weights = SSDLite320_MobileNet_V3_Large_Weights.COCO_V1
torch.hub.load_state_dict_from_url(weights.url, model_dir=str(root / "models"),
                                 check_hash=True, map_location="cpu", weights_only=True)
print(f"COCO weights cached in {root / 'models'}")
