"""Read-only checks inside the intended Linux / WSL / SSH session."""
import argparse
import importlib
import importlib.metadata
import platform
import shutil
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--profile", choices=["core", "vision", "gpu"], default="vision")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
checks = {
    "Linux": sys.platform == "linux",
    "Python >= 3.10": sys.version_info >= (3, 10),
    "Virtual environment": sys.prefix != sys.base_prefix,
    "Git": shutil.which("git") is not None,
    "Simulation config": (root / "config/simulation.json").is_file(),
}
print(f"System: {platform.platform()}")
print(f"Python: {sys.executable}")
if args.profile in ("vision", "gpu"):
    for name in ("numpy", "cv2", "torch", "torchvision", "motorbridge", "debugpy"):
        try:
            module = importlib.import_module(name)
            checks[f"Import {name} ({getattr(module, '__version__', 'installed')})"] = True
        except (ImportError, OSError) as error:
            checks[f"Import {name}: {error}"] = False
    checks["COCO model cached"] = (
        root / "models/ssdlite320_mobilenet_v3_large_coco-a79551df.pth").is_file()
    try:
        print(f"MotorBridge SDK installed: {importlib.metadata.version('motorbridge')}")
    except importlib.metadata.PackageNotFoundError:
        print("MotorBridge SDK not installed")
if args.profile == "gpu":
    import torch
    checks["CUDA available"] = torch.cuda.is_available()
    if checks["CUDA available"]:
        try:
            a = torch.randn(32, 32, device="cuda")
            checks["CUDA computation"] = bool(torch.isfinite(a @ a).all())
            torch.cuda.synchronize()
            print(f"GPU: {torch.cuda.get_device_name(0)}")
        except RuntimeError as error:
            checks[f"CUDA computation: {error}"] = False
for label, ok in checks.items():
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
net = Path("/sys/class/net")
print(f"CAN candidates: {[p.name for p in net.glob('*can*')] if net.exists() else []}")
print("Hardware, camera calibration and physical grasp success: NOT VERIFIED")
sys.exit(0 if all(checks.values()) else 1)
