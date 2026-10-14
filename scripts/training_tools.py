"""Hardware-free LeRobot data creation, checks, ACT training and checkpoint reload."""
import argparse
import importlib.metadata
import json
import logging
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from rebot_tea.dataset_contract import (
    FEATURE_NAMES, IMAGE_KEY, public_radians_to_degrees, validate_manifest, validate_rows,
)
from rebot_tea.joints import JOINT_NAMES, JointLimits, LimitedJointController, SimulatedJointBackend

ROOT = Path(__file__).resolve().parents[1]
LEROBOT_COMMIT = "ae2b57f12223c8c5184fcd814eb63cd9ce7de203"
DEFAULT_DATASET = ROOT / "data/synthetic-empty-teapot"


def write_report(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def check_environment():
    import av
    import motorbridge
    import torch
    import torchcodec
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.robots.rebot_b601_follower.config_rebot_b601_follower import (
        RebotB601FollowerRobotConfig,
    )
    versions = {name: importlib.metadata.version(name) for name in
                ("lerobot", "torch", "torchvision", "torchcodec", "av", "motorbridge", "datasets")}
    commit = subprocess.check_output(
        ["git", "-C", str(ROOT / "vendor/lerobot"), "rev-parse", "HEAD"], text=True).strip()
    dependencies = subprocess.run([sys.executable, "-m", "pip", "check"],
                                  capture_output=True, text=True, timeout=60)
    ready = sys.version_info[:2] == (3, 12) and commit == LEROBOT_COMMIT \
        and dependencies.returncode == 0 and torch.cuda.is_available() and torch.version.cuda == "12.8"
    if torch.cuda.is_available():
        x = torch.ones((8, 8), device="cuda")
        ready = ready and bool(torch.isfinite(x @ x).all())
        torch.cuda.synchronize()
    return {"passed": ready, "python": sys.version, "executable": sys.executable,
            "versions": versions, "lerobot_commit": commit,
            "imports": [LeRobotDataset.__name__, ACTPolicy.__name__,
                        RebotB601FollowerRobotConfig.__name__, torchcodec.__name__],
            "sdk_abi": motorbridge.abi_version(), "av_libraries": av.library_versions,
            "pip_check": dependencies.stdout.strip() + dependencies.stderr.strip(),
            "torch_build": torch.__version__, "cuda_runtime": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "hardware_connections_created": False}


def create_dataset(root, frames=120):
    import numpy as np
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    root = Path(root)
    if root.exists():
        raise FileExistsError(f"Preserve existing dataset: {root}; choose a new --dataset-root")
    if type(frames) is not int or frames < 2:
        raise ValueError("At least two frames per episode are required")
    config = json.loads((ROOT / "config/joints.synthetic.json").read_text())
    limits = JointLimits(**config["limits"])
    features = {
        "observation.state": {"dtype": "float32", "shape": (7,), "names": list(FEATURE_NAMES)},
        "action": {"dtype": "float32", "shape": (7,), "names": list(FEATURE_NAMES)},
        IMAGE_KEY: {"dtype": "video", "shape": (224, 224, 3),
                    "names": ["height", "width", "channels"]},
    }
    dataset = LeRobotDataset.create(repo_id="local/synthetic-empty-teapot", root=root,
                                   fps=20, features=features, robot_type="synthetic_rebot_b601",
                                   video_backend="pyav", image_writer_threads=2)
    for episode in range(5):
        now = [0.0]

        def clock():
            return now[0]

        backend = SimulatedJointBackend(clock)
        controller = LimitedJointController(backend, limits, clock)
        target = tuple(value * (0.7 + 0.1 * episode) for value in config["target_rad"])
        controller.start(target)
        for tick in range(frames * 5):
            # Merge 50 Hz control and 20 Hz recording on a 100 Hz virtual timeline.
            now[0] = tick / 100
            if tick % 2 == 0:
                decision = controller.tick()
                if decision.state == "aborted":
                    raise RuntimeError(f"Synthetic recording aborted: {decision.reason}")
            if tick % 5:
                continue
            state = backend.read_state()
            image = np.full((224, 224, 3), 32, dtype=np.uint8)
            for joint, value in enumerate(state.positions_rad):
                end = int(112 + 80 * value)
                image[12 + joint * 28:30 + joint * 28, 40:max(41, end), joint % 3] = 180 + episode * 10
            image[:8, :, :] = episode * 40
            dataset.add_frame({
                "observation.state": np.asarray(public_radians_to_degrees(state.positions_rad),
                                                dtype=np.float32),
                "action": np.asarray(public_radians_to_degrees(backend.target), dtype=np.float32),
                IMAGE_KEY: image, "task": "Synthetic interface check; not a physical teapot demonstration",
            })
        dataset.save_episode(parallel_encoding=False)
    dataset.finalize()
    manifest = {"schema_version": 1, "synthetic": True, "physical_task_verified": False,
                "repo_id": "local/synthetic-empty-teapot", "fps": 20, "num_episodes": 5,
                "frames_per_episode": frames, "joint_names": list(JOINT_NAMES),
                "position_units": "public_joint_degrees", "image_key": IMAGE_KEY,
                "image_shape": [224, 224, 3], "max_step_deg": 10.0,
                "train_episodes": [0, 1, 2, 3], "validation_episodes": [4],
                "control_hz": 50, "action_source": "last target sent to mock backend",
                "lerobot_commit": LEROBOT_COMMIT}
    write_report(root / "prehardware.json", manifest)
    return manifest


def validate_dataset(root):
    import torch
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    root = Path(root)
    errors, rows = [], []
    try:
        manifest = json.loads((root / "prehardware.json").read_text())
        errors.extend(validate_manifest(manifest))
        if errors:
            return {"passed": False, "errors": errors, "frames_checked": 0}
        dataset = LeRobotDataset(manifest["repo_id"], root=root, video_backend="pyav")
        for key in ("observation.state", "action", IMAGE_KEY):
            feature = dataset.features.get(key)
            if feature is None:
                errors.append(f"Missing feature {key}")
            elif key != IMAGE_KEY and (list(feature["shape"]) != [7]
                                      or feature.get("names") != list(FEATURE_NAMES)):
                errors.append(f"Wrong dimensions or joint order for {key}")
        if errors:
            return {"passed": False, "errors": errors, "frames_checked": 0}
        for index in range(len(dataset)):
            try:
                sample = dataset[index]
                row = {key: sample[key].item() for key in
                       ("episode_index", "frame_index", "timestamp")}
                for key in ("observation.state", "action"):
                    if key in sample:
                        row[key] = sample[key].tolist()
                rows.append(row)
                image = sample[IMAGE_KEY]
                if tuple(image.shape) != (3, 224, 224) or not bool(torch.isfinite(image).all()):
                    errors.append(f"frame {index}: invalid image shape or values")
            except Exception as error:
                errors.append(f"frame {index}: video/data decode failed: {type(error).__name__}: {error}")
        report = validate_rows(rows, manifest)
        report["errors"].extend(errors)
        report["passed"] = not report["errors"]
        report.update({"synthetic": True, "dataset_root": str(root.resolve()),
                       "position_units": manifest["position_units"],
                       "video_backend": "pyav", "physical_task_verified": False})
        return report
    except Exception as error:
        return {"passed": False, "errors": [f"{type(error).__name__}: {error}"],
                "frames_checked": len(rows)}


def smoke_train(root, output_dir, steps=100):
    import torch
    from lerobot.configs.default import DatasetConfig, WandBConfig
    from lerobot.configs.train import TrainPipelineConfig
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.scripts.lerobot_train import train
    from torch.utils.data import DataLoader
    root, output_dir = Path(root), Path(output_dir)
    if type(steps) is not int or steps < 1:
        raise ValueError("steps must be a positive integer")
    quality = validate_dataset(root)
    if not quality["passed"]:
        raise ValueError(f"Dataset quality check failed: {quality['errors'][:5]}")
    if not torch.cuda.is_available():
        raise RuntimeError("GPU smoke verification requires CUDA")
    if output_dir.exists():
        raise FileExistsError(f"Preserve existing training run: {output_dir}")
    started = time.monotonic()
    manifest = json.loads((root / "prehardware.json").read_text())
    attempts = []
    run_dir = output_dir

    class MetricsCapture(logging.Filter):
        def __init__(self):
            super().__init__()
            self.metrics = []

        def filter(self, record):
            if hasattr(record.msg, "to_dict"):
                metrics = record.msg.to_dict()
                if "loss" in metrics:
                    self.metrics.append(metrics)
            return True

    for batch_size in (2, 1):
        cfg = TrainPipelineConfig(
            dataset=DatasetConfig(repo_id=manifest["repo_id"], root=str(root),
                                  episodes=manifest["train_episodes"], video_backend="pyav"),
            policy=ACTConfig(device="cuda", push_to_hub=False, pretrained_backbone_weights=None),
            output_dir=run_dir, steps=steps, batch_size=batch_size, num_workers=0,
            save_freq=steps, log_freq=min(10, steps), env_eval_freq=0,
            wandb=WandBConfig(enable=False), seed=42,
        )
        capture = MetricsCapture()
        try:
            # Upstream's CLI wrapper must not parse this tool's positional arguments.
            old_argv = sys.argv
            sys.argv = ["lerobot-train"]
            logging.getLogger().addFilter(capture)
            try:
                train(cfg)
            finally:
                sys.argv = old_argv
                logging.getLogger().removeFilter(capture)
            attempts.append({"batch_size": batch_size, "passed": True})
            break
        except torch.cuda.OutOfMemoryError as error:
            attempts.append({"batch_size": batch_size, "passed": False, "error": str(error)})
            if batch_size == 1:
                raise
            error.__traceback__ = None
            import gc
            gc.collect()
            torch.cuda.empty_cache()
            run_dir = output_dir.with_name(output_dir.name + "-batch1")
            if run_dir.exists():
                raise FileExistsError(f"Preserve existing fallback run: {run_dir}")
    torch.cuda.synchronize()
    peak = torch.cuda.max_memory_allocated()
    if not capture.metrics or not all(math.isfinite(row["loss"]) for row in capture.metrics):
        raise RuntimeError("Training did not produce finite loss metrics")
    checkpoints = list((run_dir / "checkpoints").glob("*/pretrained_model"))
    checkpoints = [path for path in checkpoints if path.parent.name != "last"]
    if not checkpoints:
        raise RuntimeError("Training did not save a checkpoint")
    checkpoint = sorted(checkpoints)[-1]
    policy = ACTPolicy.from_pretrained(checkpoint).to("cuda").eval()
    pre, post = make_pre_post_processors(
        policy.config, pretrained_path=checkpoint,
        preprocessor_overrides={"device_processor": {"device": "cuda"}},
        postprocessor_overrides={"device_processor": {"device": "cpu"}},
    )
    validation = LeRobotDataset(manifest["repo_id"], root=root,
                               episodes=manifest["validation_episodes"], video_backend="pyav",
                               delta_timestamps={"action": [i / manifest["fps"]
                                                            for i in range(policy.config.chunk_size)]})
    batch = next(iter(DataLoader(validation, batch_size=1, num_workers=0)))
    if batch[IMAGE_KEY].dtype == torch.uint8:
        batch[IMAGE_KEY] = batch[IMAGE_KEY].float() / 255
    processed = pre(batch)
    with torch.no_grad():
        loss, _ = policy(processed)
        policy.reset()
        prediction = post(policy.select_action(processed))
    if tuple(prediction.shape) != (1, 7) or not bool(torch.isfinite(prediction).all()) \
            or not bool(torch.isfinite(loss)):
        raise RuntimeError("Reloaded checkpoint returned invalid loss or seven-joint prediction")
    report = {"passed": True, "synthetic": True, "physical_task_verified": False,
              "steps": steps, "batch_size": batch_size, "attempts": attempts,
              "elapsed_s": time.monotonic() - started,
              "training_loss_last": capture.metrics[-1]["loss"],
              "training_metrics": capture.metrics,
              "validation_loss": float(loss.detach().cpu()),
              "prediction_shape": list(prediction.shape), "prediction_units": "public_joint_degrees",
              "prediction_deg": prediction.detach().cpu().tolist(),
              "gpu_peak_last_training_step_bytes": peak, "gpu": torch.cuda.get_device_name(0),
              "checkpoint": str(checkpoint), "processors_reloaded": True,
              "train_episodes": manifest["train_episodes"],
              "validation_episodes": manifest["validation_episodes"],
              "pretrained_backbone_weights": None, "remote_upload": False}
    write_report(run_dir / "prehardware-smoke.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "generate", "validate", "smoke", "verify"))
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--steps", type=int, default=100)
    args = parser.parse_args()
    result = {"action": args.action, "passed": True, "synthetic": True}
    try:
        if args.action in ("check", "verify"):
            result["environment"] = check_environment()
            if not result["environment"]["passed"]:
                raise RuntimeError("Training environment check failed")
        if args.action == "generate" or args.action == "verify" and not args.dataset_root.exists():
            result["dataset_manifest"] = create_dataset(args.dataset_root)
        if args.action in ("validate", "verify"):
            result["quality"] = validate_dataset(args.dataset_root)
            if not result["quality"]["passed"]:
                raise ValueError("Dataset quality check failed")
        if args.action in ("smoke", "verify"):
            output_dir = args.output_dir or ROOT / "outputs/train" / f"synthetic-act-{time.time_ns()}"
            result["training"] = smoke_train(args.dataset_root, output_dir, args.steps)
    except Exception as error:
        result["passed"] = False
        result["error"] = f"{type(error).__name__}: {error}"
    result["hardware_connections_created"] = False
    report_path = ROOT / "artifacts" / f"training-{args.action}.json"
    write_report(report_path, result)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("WANDB_MODE", "disabled")
    raise SystemExit(main())
