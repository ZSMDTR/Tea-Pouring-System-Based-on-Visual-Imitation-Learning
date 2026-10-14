"""Save a diagram of the offline task waypoints; not a motion simulation."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
result = json.loads((root / "artifacts/replay.json").read_text())
steps = [step for step in result["steps"] if step["position_m"] is not None]
figure = plt.figure(figsize=(7, 5))
axes = figure.add_subplot(projection="3d")
points = [step["position_m"] for step in steps]
axes.plot(*zip(*points), marker="o")
for step in steps:
    axes.text(*step["position_m"], step["action"])
axes.set(xlabel="base X (m)", ylabel="base Y (m)", zlabel="base Z (m)",
         title="Synthetic task waypoints - no IK or collision validation")
figure.tight_layout()
figure.savefig(root / "artifacts/waypoints.png", dpi=150)
print("Saved artifacts/waypoints.png")
