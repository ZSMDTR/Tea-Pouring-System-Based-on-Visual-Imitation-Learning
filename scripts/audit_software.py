"""Audit installed software without connecting to the robot or modifying devices."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = {
    "main": (".venv", "requirements/lock-linux-cu128.txt"),
    "training": (".venv-training", "requirements/lock-training-cu128.txt"),
    "kinematics": (".venv-kinematics", "requirements/kinematics.txt"),
}
VERSIONS = {
    "vendor/lerobot": "ae2b57f12223c8c5184fcd814eb63cd9ce7de203",
    "vendor/reBotArm_control_py": "6415d43130d1e143c70dc106096a857ac5556f81",
}
REMOTE_EXTENSIONS = (
    "ms-python.python", "ms-python.vscode-pylance", "ms-python.debugpy",
    "charliermarsh.ruff", "ms-toolsai.jupyter",
)


def run(command, timeout=60, environment=None):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if environment == "training":
        env["LD_LIBRARY_PATH"] = str(ROOT / ".venv-training/lib")
    try:
        process = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                 errors="replace", capture_output=True, timeout=timeout)
        return {"passed": process.returncode == 0, "returncode": process.returncode,
                "stdout": process.stdout.strip(), "stderr": process.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"passed": False, "error": str(error)}


def audit(output):
    checks = {}
    inventory = {}
    for name, (directory, lock) in ENVIRONMENTS.items():
        python = str(ROOT / directory / "bin/python")
        checks[f"{name}_dependencies"] = run([python, "-m", "pip", "check"], environment=name)
        code = '''import importlib.metadata as m, json, platform, re, sys
from pathlib import Path
pins = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if "==" in line and not line.lstrip().startswith("#"):
        key, value = line.split("==", 1)
        pins[re.sub(r"[-_.]+", "-", key).lower()] = value.strip()
packages = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version
            for d in m.distributions()}
# Public == pins permit a local CUDA suffix under PEP 440.
errors = {k: {"expected": v, "actual": packages.get(k)} for k, v in pins.items()
          if (packages.get(k, "").split("+", 1)[0] if "+" not in v
              else packages.get(k)) != v}
print(json.dumps({"python": platform.python_version(), "packages": packages,
                  "lock_mismatches": errors}))
sys.exit(bool(errors))
'''
        result = run([python, "-c", code, lock], environment=name)
        if result.get("stdout"):
            try:
                inventory[name] = json.loads(result.pop("stdout"))
            except ValueError:
                result["passed"] = False
        checks[f"{name}_lock"] = result
    for directory, expected in VERSIONS.items():
        result = run(["git", "-C", directory, "rev-parse", "HEAD"])
        result["expected"] = expected
        result["passed"] = result["passed"] and result.get("stdout") == expected
        checks[directory] = result

    python = str(ROOT / ".venv/bin/python")
    checks["main_gpu"] = run([python, "scripts/check_environment.py", "--profile", "gpu"])
    checks["training_environment"] = run(["bash", "scripts/training.sh", "check"])
    checks["camera_native_sdk"] = run(["bash", "scripts/camera.sh", "check"])
    checks["motorbridge_native"] = run([python, "-c", "import motorbridge; "
        "print(motorbridge.abi_version(), motorbridge.abi_capabilities()); "
        "assert 'robstride' in motorbridge.abi_capabilities()['vendors']"])
    checks["notebook_kernels"] = run([python, "scripts/check_notebooks.py"], timeout=150)
    for module in ("can", "can_raw", "peak_usb", "gs_usb", "vhci_hcd", "uvcvideo"):
        checks[f"kernel_{module}"] = run(["modinfo", "-F", "filename", module])
    checks["udev_service"] = run(["systemctl", "is-active", "systemd-udevd"])
    rule = Path("/etc/udev/rules.d/99-obsensor-libusb.rules")
    source = ROOT / ".venv/lib/python3.10/site-packages/pyorbbecsdk/shared/99-obsensor-libusb.rules"
    checks["orbbec_usb_permissions"] = {
        "passed": rule.is_file() and source.is_file() and rule.read_bytes() == source.read_bytes(),
        "path": str(rule),
        "sha256": hashlib.sha256(rule.read_bytes()).hexdigest() if rule.is_file() else None,
    }
    extensions = run(["code", "--list-extensions", "--show-versions"])
    installed = dict(line.rsplit("@", 1) for line in extensions.get("stdout", "").splitlines()
                     if "@" in line)
    checks["vscode_wsl_extensions"] = {
        "passed": extensions["passed"] and all(key in installed for key in REMOTE_EXTENSIONS),
        "versions": {key: installed.get(key) for key in REMOTE_EXTENSIONS},
    }
    windows = None
    powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if powershell.is_file() and Path("/init").is_file():
        script = (ROOT / "scripts/audit_windows.ps1").read_text()
        encoded = base64.b64encode(script.encode("utf-16le")).decode()
        result = run(["/init", str(powershell), "powershell.exe", "-NoProfile", "-NonInteractive",
                      "-EncodedCommand", encoded], timeout=90)
        if result["passed"]:
            try:
                windows = json.loads(result["stdout"].lstrip("\ufeff"))
                (ROOT / "artifacts/windows-software-audit.json").write_text(
                    json.dumps(windows, ensure_ascii=False, indent=2) + "\n")
            except ValueError as error:
                result = {"passed": False, "error": str(error)}
        checks["windows_inventory"] = {key: value for key, value in result.items()
                                       if key != "stdout"}
        if windows is not None:
            checks["windows_usbipd"] = {
                "passed": windows["usbipd_installed"] and windows["usbipd_service"] == "Running"}
            checks["windows_pcan_runtime"] = {"passed": len(windows["pcan_basic_dll"]) == 2
                                              and windows["pcan_x64_loadable"]}
            checks["windows_orbbec_viewer"] = {"passed": bool(windows["orbbec_viewer"])}
            checks["vscode_windows_wsl_extension"] = {"passed": any(
                ext["id"] == "ms-vscode-remote.remote-wsl"
                for ext in windows["vscode_extensions"])}
    usage = shutil.disk_usage(ROOT)
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "software_checks_passed": all(item["passed"] for item in checks.values()),
        "checks": checks, "environment_inventory": inventory, "windows": windows,
        "wsl_usb": run(["lsusb"]), "network_interfaces": run(["ip", "-brief", "link"]),
        "project_disk_free_bytes": usage.free,
        "optional_tools": {name: shutil.which(name) for name in (
            "git-lfs", "dvc", "tensorboard", "docker", "tmux", "label-studio", "ros2")},
        "hardware_verified": False, "motor_commands_sent": False,
        "physical_checks_pending": ["USB identification and ownership", "RGB-D stream quality",
                                    "CAN feedback", "calibration and measured limits"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for name, check in checks.items():
        print(f"[{'PASS' if check['passed'] else 'FAIL'}] {name}")
    print(f"Report: {output}")
    print("Physical hardware has not been verified; no motor commands sent.")
    return 0 if report["software_checks_passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/software-audit.json")
    raise SystemExit(audit(parser.parse_args().output))
