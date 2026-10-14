"""Read-only PC/CAN diagnostics: never create a motor controller or send CAN frames."""
import argparse
import ctypes
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def can_candidates(devices):
    """Only USB CAN candidates; Bluetooth COM ports and speakers are not adapters."""
    pattern = re.compile(r"\b(?:PCAN|PEAK|CANable|CANalyst|RobStride)\b|USB[- ]?CAN", re.I)
    return [device for device in devices
            if str(device.get("vid_pid", "")).upper().startswith("VID_0C72")
            or (device.get("vid_pid") and pattern.search(device.get("name") or ""))]


def linux_can_links(links):
    return [link for link in links if link.get("link_type") == "can"]


def linux_can_ready(link):
    data = link.get("linkinfo", {}).get("info_data", {})
    bitrate = data.get("bittiming", {}).get("bitrate", data.get("bitrate"))
    return "UP" in link.get("flags", []) and bitrate == 1000000


def diagnose(channel=None):
    report = {"platform": sys.platform, "python": sys.executable,
              "channel_requested": channel, "software_ready": False,
              "bus_prerequisites_ready": False, "motors_online": "NOT TESTED",
              "calibration": "NOT VERIFIED",
              "parameters": "NOT VERIFIED", "motor_commands_sent": False}
    try:
        import motorbridge
        report["motorbridge"] = motorbridge.__version__
        report["abi"] = motorbridge.abi_version()
        report["software_ready"] = "robstride" in motorbridge.abi_capabilities()["vendors"]
    except Exception as error:
        report["sdk_error"] = f"{type(error).__name__}: {error}"
    if sys.platform == "win32":
        command = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
            "$d=@(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
            "Where-Object {$_.InstanceId -match 'USB\\\\VID_'} | ForEach-Object { "
            "$v=''; if ($_.InstanceId -match 'VID_[0-9A-F]{4}&PID_[0-9A-F]{4}') "
            "{$v=$matches[0]}; [pscustomobject]@{name=$_.FriendlyName;vid_pid=$v;"
            "status=$_.Status}}); ConvertTo-Json -InputObject $d -Compress"
        )
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                                 "-Command", command], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30)
        if result.returncode:
            report["device_query_error"] = result.stderr.strip()
            devices = []
        else:
            devices = json.loads(result.stdout.strip() or "[]")
        report["usb_can_candidates"] = can_candidates(devices)
        report["usb_devices"] = devices
        try:
            ctypes.CDLL("PCANBasic.dll")
            report["pcan_basic_runtime"] = True
        except OSError:
            report["pcan_basic_runtime"] = False
        pcan_devices = [device for device in report["usb_can_candidates"]
                        if device.get("status") == "OK" and (
                            device["vid_pid"].upper().startswith("VID_0C72")
                            or re.search(r"\b(?:PCAN|PEAK)\b", device.get("name") or "", re.I))]
        report["bus_prerequisites_ready"] = bool(
            report["software_ready"] and report["pcan_basic_runtime"]
            and pcan_devices)
        report["channel_hint"] = "PCAN only: can0@1000000 or can1@1000000; confirm adapter order"
        report["usbipd_installed"] = shutil.which("usbipd") is not None
    elif sys.platform.startswith("linux"):
        result = subprocess.run(["ip", "-json", "-details", "link", "show"],
                                capture_output=True, text=True, timeout=10, check=True)
        links = linux_can_links(json.loads(result.stdout))
        report["can_interfaces"] = links
        selected = [item for item in links if item["ifname"] == channel] if channel else []
        report["bus_prerequisites_ready"] = bool(
            report["software_ready"] and any(map(linux_can_ready, selected)))
        report["channel_hint"] = "Linux SocketCAN: plain can0/can1, configured UP at 1000000 bps"
    else:
        report["platform_error"] = "This project currently documents Windows and Linux"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = diagnose(args.channel)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
    print(json.dumps(data, indent=2, ensure_ascii=True))
    sys.exit(0 if data["bus_prerequisites_ready"] else 2)
