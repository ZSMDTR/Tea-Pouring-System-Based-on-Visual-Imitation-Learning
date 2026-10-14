"""Query B601-RS motors only. Never enable, disable, zero or send motion targets."""
import argparse
import json
import math
import re
import sys
import time
from pathlib import Path


def validate_channel(channel, platform=None):
    platform = platform or sys.platform
    if platform == "win32":
        valid = isinstance(channel, str) and re.fullmatch(r"can[0-9]+@1000000", channel)
    else:
        valid = isinstance(channel, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", channel)
    if not valid:
        raise ValueError("Use Linux can0/can1 or Windows PCAN can0@1000000")


def probe(channel, timeout_ms=150, controller_factory=None, clock=time.monotonic):
    validate_channel(channel)
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 5000:
        raise ValueError("timeout_ms must be an integer in [1, 5000]")
    if controller_factory is None:
        from motorbridge import Controller
        controller_factory = Controller
    controller = controller_factory(channel)
    motors = []
    try:
        for motor_id in range(1, 8):
            model = "rs-06" if motor_id <= 3 else "rs-00"
            row = {"motor_id": motor_id, "model_hint": model, "online": False,
                   "position_rad": None, "position_sample_monotonic_s": None,
                   "errors": {}}
            motor = None
            try:
                motor = controller.add_robstride_motor(motor_id, 0xFD, model)
                try:
                    motor.robstride_ping_host_id(0xFD, timeout_ms)
                    row["online"] = True
                except Exception as error:
                    row["errors"]["ping"] = f"{type(error).__name__}: {error}"
                try:
                    value = motor.robstride_get_param_f32_host_id(0x7019, 0xFD, timeout_ms)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError("Position is not a numeric parameter response")
                    if not math.isfinite(value):
                        raise ValueError("Position is not finite")
                    row["position_rad"] = float(value)
                    row["position_sample_monotonic_s"] = clock()
                    row["online"] = True
                except Exception as error:
                    row["errors"]["position"] = f"{type(error).__name__}: {error}"
            except Exception as error:
                row["errors"]["registration"] = f"{type(error).__name__}: {error}"
            finally:
                if motor is not None:
                    motor.close()
            motors.append(row)
    finally:
        try:
            controller.close_bus()
        finally:
            controller.close()
    all_online = all(row["online"] for row in motors) and len(motors) == 7
    all_positions = all(row["position_rad"] is not None for row in motors) and len(motors) == 7
    return {"robot": "B601-RS", "channel": channel, "host_id": 0xFD,
            "all_online": all_online, "all_positions_valid": all_positions,
            "passed": all_online and all_positions, "motors": motors,
            "actuation_commands_sent": False, "calibration_verified": False,
            "parameters_verified": False, "physical_motion_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--timeout-ms", type=int, default=150)
    parser.add_argument("--output", type=Path, default=Path("artifacts/connection.json"))
    args = parser.parse_args()
    try:
        result = probe(args.channel, args.timeout_ms)
    except Exception as error:
        result = {"passed": False, "channel": args.channel,
                  "error": f"{type(error).__name__}: {error}",
                  "actuation_commands_sent": False}
    content = json.dumps(result, indent=2, ensure_ascii=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content + "\n", encoding="utf-8")
    print(content)
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
