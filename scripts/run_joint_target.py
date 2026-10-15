"""Validate or execute one commissioned B601-RS joint target at 50 Hz."""
import argparse
import json
import math
import time
from pathlib import Path

from rebot_tea.hardware import connect_lerobot_backend, load_verified_hardware_config
from rebot_tea.joints import JOINT_NAMES, LimitedJointController, joint_vector


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--target-deg",
        type=float,
        nargs=7,
        metavar=tuple(name.upper() for name in JOINT_NAMES),
        required=True,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="connect, enable and move; without this flag only validate the config and target",
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/joint-target.json"))
    args = parser.parse_args()

    report = {
        "robot": "B601-RS",
        "driver": "lerobot.rebot_b601_follower",
        "execute_requested": args.execute,
        "hardware_connected": False,
        "commands_sent": 0,
        "physical_stop_confirmed": False,
        "passed": False,
    }
    backend = None
    controller = None
    try:
        config = load_verified_hardware_config(args.config)
        target_rad = joint_vector(tuple(math.radians(value) for value in args.target_deg), "target")
        config.limits.check_position(target_rad)
        report.update(
            {
                "config": str(config.source),
                "channel": config.channel,
                "joint_order": list(JOINT_NAMES),
                "target_deg": list(args.target_deg),
                "stop_mode": config.stop_mode,
                "configuration_verified": True,
            }
        )
        if not args.execute:
            report.update(
                {
                    "passed": True,
                    "result": "validation_only",
                    "note": "No LeRobot import, CAN connection, motor enable or command occurred.",
                }
            )
            return 0

        backend = connect_lerobot_backend(config)
        report["hardware_connected"] = True
        controller = LimitedJointController(backend, config.limits, time.monotonic)
        decision = controller.start(target_rad)
        period_s = 1.0 / 50.0
        next_tick = time.monotonic()
        while decision.state == "running":
            next_tick += period_s
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            decision = controller.tick()
        report.update(
            {
                "passed": decision.state == "completed",
                "result": decision.state,
                "reason": decision.reason,
                "elapsed_s": decision.elapsed_s,
                "commands_sent": backend.command_count,
                "stop_requested_by_controller": decision.stop_requested,
                "physical_stop_confirmed": False,
            }
        )
    except KeyboardInterrupt:
        if controller is not None:
            decision = controller.stop("keyboard_interrupt")
            report.update({"result": decision.state, "reason": decision.reason})
        else:
            report.update({"result": "interrupted_before_controller"})
    except Exception as error:
        report.update({"result": "error", "error": f"{type(error).__name__}: {error}"})
    finally:
        if backend is not None:
            try:
                if backend.stop_calls == 0:
                    backend.request_stop()
                report["stop_requested"] = True
            except Exception as error:
                report["stop_error"] = f"{type(error).__name__}: {error}"
            try:
                backend.close()
            except Exception as error:
                report["disconnect_error"] = f"{type(error).__name__}: {error}"
            report["hardware_connected"] = False
        write_report(args.output, report)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
