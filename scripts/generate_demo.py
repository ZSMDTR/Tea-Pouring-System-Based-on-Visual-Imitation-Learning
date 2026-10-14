"""Generate synthetic RGB-D fixtures, never intended as camera calibration."""
from pathlib import Path

import cv2
import numpy as np

root = Path(__file__).resolve().parents[1]
directory = root / "fixtures/generated"
directory.mkdir(parents=True, exist_ok=True)
image = np.full((480, 640, 3), 220, dtype=np.uint8)
cv2.rectangle(image, (300, 220), (340, 260), (180, 120, 60), -1)
cv2.putText(image, "SYNTHETIC RGB-D DEMO", (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
            .7, (0, 0, 0), 2)
cv2.imwrite(str(directory / "color.png"), image)
np.save(directory / "depth.npy", np.full((480, 640), 500, dtype=np.uint16))
print(f"Synthetic RGB-D files -> {directory}")
