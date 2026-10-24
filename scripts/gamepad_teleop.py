"""Explicitly gated gamepad session. The default uses a synthetic camera and arm."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import time

from rebot_tea.gamepad_bridge import InputBridge
from rebot_tea.gamepad_recording import EpisodeRecorder
from rebot_tea.gamepad_session import SyntheticCamera, require_neutral_input, run_session
from rebot_tea.hardware import connect_lerobot_backend, load_verified_hardware_config
from rebot_tea.joints import JointLimits, SimulatedJointBackend

PROJECT = Path(__file__).resolve().parents[1]


def session_config(args):
    """Check every physical gate before constructing a device or opening a socket."""
    if not math.isfinite(args.duration) or args.duration < 0:
        raise ValueError("duration must be finite and nonnegative")
    if args.backend == "hardware":
        if not args.execute or not args.hardware_config or args.camera != "gemini2":
            raise ValueError("Hardware requires --execute, --hardware-config and --camera gemini2")
        config = load_verified_hardware_config(args.hardware_config)
        if config.stop_mode != "hold_position":
            raise ValueError("Streaming requires a physically verified hold_position stop")
        return config, config.limits
    if args.execute or args.hardware_config:
        raise ValueError("--execute and --hardware-config are only for hardware")
    data = json.loads((PROJECT / "config/joints.synthetic.json").read_text())
    return None, JointLimits(**data["limits"])


def wait_neutral(bridge, hardware, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = bridge.snapshot()
        if frame is not None:
            if hardware and frame.synthetic:
                raise ValueError("Synthetic input cannot enable physical hardware")
            try:
                require_neutral_input(frame, hardware)
                return
            except ValueError:
                pass
        time.sleep(.02)
    raise TimeoutError("No neutral gamepad within 30s; release all controls and retry")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("mock", "hardware"), default="mock")
    parser.add_argument("--camera", choices=("synthetic", "gemini2"), default="synthetic")
    parser.add_argument("--camera-config", type=Path, default=PROJECT / "config/camera.gemini2.json")
    parser.add_argument("--hardware-config", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--duration", type=float, default=0, help="Session seconds after input; 0=until Ctrl+C")
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--output", type=Path, default=PROJECT / "artifacts/gamepad-session.json")
    args = parser.parse_args(argv)
    bridge = camera = recorder = backend = None
    report = {"status": "setup_failed", "physical_task_verified": False,
              "physical_stop_confirmed": False, "backend": args.backend}
    try:
        config, limits = session_config(args)
        if args.camera == "gemini2":
            from rebot_tea.lerobot_orbbec import Gemini2Camera, Gemini2CameraConfig
            from rebot_tea.orbbec import Gemini2Settings
            settings = Gemini2Settings.load(args.camera_config)
            camera = Gemini2Camera(Gemini2CameraConfig(**asdict(settings)))
            camera.connect()
            shape = (settings.height, settings.width, 3)
        else:
            camera = SyntheticCamera()
            shape = camera.image_shape
        root = args.dataset_root or PROJECT / "data/gamepad" / f"{args.backend}-{time.time_ns()}"
        recorder = EpisodeRecorder(root, image_shape=shape, synthetic=args.backend == "mock")
        bridge = InputBridge(port=args.port)
        bridge.start()
        print(f"LISTENING 127.0.0.1:{bridge.port}; backend={args.backend}; dataset={root}", flush=True)
        print("Release all controls. LB=hold to jog; RB=group; A=start B=success X=discard Y=failure", flush=True)
        wait_neutral(bridge, args.backend == "hardware")
        # Recheck freshness after initialization and immediately before motor connection.
        require_neutral_input(bridge.snapshot(), args.backend == "hardware")
        backend = connect_lerobot_backend(config) if config else SimulatedJointBackend(time.monotonic)
        report = run_session(backend, bridge, camera, recorder, limits, args.duration)
        report["dataset_root"] = str(root)
        report["camera"] = args.camera
    except KeyboardInterrupt:
        report["status"] = "interrupted_during_setup"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        # run_session stops first. A setup error after connecting must also stop.
        if backend is not None and not report.get("stop_requested"):
            try:
                backend.request_stop()
                report["stop_requested"] = True
            except Exception as error:
                report["stop_error"] = str(error)
        for resource, method in ((backend, "close"), (camera, "disconnect"),
                                 (bridge, "close"), (recorder, "close")):
            if resource is not None and hasattr(resource, method):
                try:
                    getattr(resource, method)()
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append(f"{method}: {error}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] in {"duration_completed", "interrupted"} and \
        not report.get("cleanup_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
