"""Check SDK, enumerate Gemini 2, or capture RGB-D samples without robot control."""
import argparse
import json
import time
from pathlib import Path

from rebot_tea.orbbec import (
    Gemini2Settings, Gemini2Source, list_devices, save_packet, software_info,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "list", "capture"))
    parser.add_argument("--config", type=Path, default=Path("config/camera.gemini2.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--frames", type=int, default=30)
    args = parser.parse_args()
    report = {"passed": False, "action": args.action, "motor_commands_sent": False,
              "physical_camera_verified": False}
    source = None
    capture_output = None
    try:
        if not 1 <= args.frames <= 100000:
            raise ValueError("frames must be in [1, 100000]")
        report["software"] = software_info()
        if args.action == "check":
            from rebot_tea.orbbec import load_sdk
            sdk = load_sdk()
            config = sdk.Config()
            config.set_frame_aggregate_output_mode(sdk.OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
            sdk.AlignFilter(align_to_stream=sdk.OBStreamType.COLOR_STREAM)
            report["native_config_and_alignment_api"] = "passed"
            report["passed"] = True
        elif args.action == "list":
            report["devices"] = list_devices()
            report["passed"] = any(item["is_gemini2"] for item in report["devices"])
            if not report["passed"]:
                report["error"] = "No Gemini 2 visible; check USB ownership, permissions and firmware"
        else:
            output = args.output or Path("data/raw") / f"gemini2-{time.time_ns()}"
            output.mkdir(parents=True, exist_ok=False)
            capture_output = output
            source = Gemini2Source(Gemini2Settings.load(args.config))
            source.connect()
            samples = []
            with (output/"frames.jsonl").open("x", encoding="utf-8") as stream:
                for index in range(args.frames):
                    packet = source.read_packet()
                    sample = save_packet(packet, output, index)
                    stream.write(json.dumps(sample) + "\n")
                    stream.flush()
                    samples.append(sample)
            (output/"scene.json").write_text(json.dumps(samples[-1], indent=2) + "\n")
            elapsed = samples[-1]["host_monotonic_s"] - samples[0]["host_monotonic_s"]
            report.update({
                "passed": True, "device": source.info, "captured_frames": len(samples),
                "output": str(output.resolve()), "camera_key": "scene",
                "host_observed_fps": (len(samples)-1)/elapsed if elapsed > 0 else None,
                "max_pair_skew_ms": max(abs(row["color_timestamp_us"] - row["depth_timestamp_us"])
                                        / 1000.0 for row in samples),
                "acquisition_test_passed": True, "physical_camera_verified": False,
                "note": "Actual frames captured; extrinsics and physical accuracy still require measurement",
            })
    except Exception as error:
        report["passed"] = False
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if source is not None:
            try:
                source.disconnect()
            except Exception as error:
                report["passed"] = False
                report["close_error"] = str(error)
    if capture_output is not None:
        (capture_output/"capture-report.json").write_text(json.dumps(report, indent=2) + "\n")
    report_path = Path("artifacts") / f"camera-{args.action}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
