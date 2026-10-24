"""Local LeRobot episodes with measured timing, without device control.

Call ``add_frame`` only after sending the supplied action target successfully.
LeRobot timestamps are frame_index/fps; measured host monotonic timestamps live
in recording/frames.jsonl. This association is not hard real-time synchronization.
"""
import json
import math
from pathlib import Path

import numpy as np

from .dataset_contract import FEATURE_NAMES, IMAGE_KEY, public_radians_to_degrees
from .joints import JOINT_NAMES, finite_number

TIMING_KEYS = ("input_received_s", "observation_read_s", "camera_host_s", "command_sent_s")


class EpisodeRecorder:
    def __init__(self, root, fps=20, image_shape=(224, 224, 3), synthetic=True,
                 task="Empty teapot pick and place demonstration", dataset_factory=None,
                 image_shapes=None, max_camera_skew_ms=50):
        self.root = Path(root)
        if self.root.exists() or self.root.is_symlink():
            raise FileExistsError(f"Preserve existing dataset: {self.root}")
        if type(fps) is not int or fps <= 0:
            raise ValueError("fps must be a positive integer")
        self.dual_camera = image_shapes is not None
        shapes = image_shapes if self.dual_camera else {"scene": image_shape}
        if not isinstance(shapes, dict) or set(shapes) != (
            {"scene", "wrist"} if self.dual_camera else {"scene"}
        ):
            raise ValueError("image_shapes must contain exactly scene and wrist")
        for shape in shapes.values():
            if not isinstance(shape, (tuple, list)) or len(shape) != 3 or any(
                type(size) is not int or size <= 0 for size in shape
            ) or shape[2] != 3:
                raise ValueError("image_shape must be positive (height, width, 3)")
        if isinstance(max_camera_skew_ms, bool) or not isinstance(
            max_camera_skew_ms, (int, float)
        ) or not math.isfinite(max_camera_skew_ms) or max_camera_skew_ms <= 0:
            raise ValueError("max_camera_skew_ms must be finite and positive")
        if type(synthetic) is not bool or not isinstance(task, str) or not task.strip():
            raise ValueError("synthetic must be boolean and task must be nonempty")
        if dataset_factory is None:
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
            dataset_factory = LeRobotDataset.create
        self.fps, self.image_shape = fps, tuple(shapes["scene"])
        self.image_shapes = {name: tuple(shape) for name, shape in shapes.items()}
        self.max_camera_skew_ms = float(max_camera_skew_ms)
        self.synthetic, self.task = synthetic, task
        self._active, self._closed = False, False
        self._timings, self._episode_index = [], 0
        self.dataset = dataset_factory(
            repo_id="local/gamepad-demonstrations", root=self.root, fps=fps,
            robot_type="synthetic_rebot_b601" if synthetic else "rebot_b601_follower",
            features={
                "observation.state": {"dtype": "float32", "shape": (7,),
                                      "names": list(FEATURE_NAMES)},
                "action": {"dtype": "float32", "shape": (7,), "names": list(FEATURE_NAMES)},
                **{f"observation.images.{name}": {
                    "dtype": "video", "shape": shape,
                    "names": ["height", "width", "channels"],
                } for name, shape in self.image_shapes.items()},
            },
            use_videos=True, video_backend="pyav",
        )
        self._sidecar = self.root / "recording"
        self._sidecar.mkdir()
        self._manifest = {
            "schema_version": 1, "synthetic": synthetic, "physical_task_verified": False,
            "repo_id": "local/gamepad-demonstrations", "fps": fps, "task": task,
            "image_keys": [f"observation.images.{name}" for name in self.image_shapes],
            "image_shapes": {name: list(shape) for name, shape in self.image_shapes.items()},
            "max_camera_skew_ms": self.max_camera_skew_ms if self.dual_camera else None,
            "joint_names": list(JOINT_NAMES), "position_units": "public_joint_degrees",
            "action_source": "caller-supplied target after successful send; no recorder limiter",
            "timing_clock": "host time.monotonic seconds",
            "dataset_timestamp": "frame_index / fps; nominal sampling grid",
            "measured_timing": "recording/frames.jsonl", "hard_realtime_synchronized": False,
            "episodes_saved": 0, "status": "empty",
        }
        if not self.dual_camera:
            self._manifest.update(image_key=IMAGE_KEY, image_shape=list(self.image_shape))
        (self._sidecar / "manifest.json").write_text(json.dumps(self._manifest, indent=2) + "\n")

    @property
    def is_recording(self):
        return self._active

    @property
    def frames_in_episode(self):
        return len(self._timings)

    @property
    def episodes_saved(self):
        return self._episode_index

    def start(self):
        if self._closed or self._active:
            raise RuntimeError("Recorder must be open and have no active episode")
        self._active = True

    def add_frame(self, image_rgb, state_rad, action_rad, timing):
        """Associate observations with the target actually sent, without clipping it."""
        if self._closed or not self._active:
            raise RuntimeError("Start an episode before adding frames")
        if self.dual_camera:
            if not isinstance(image_rgb, dict) or set(image_rgb) != {"scene", "wrist"}:
                raise ValueError("image_rgb must contain scene and wrist")
            images = image_rgb
        else:
            images = {"scene": image_rgb}
        for name, image in images.items():
            if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or \
                    image.shape != self.image_shapes[name]:
                raise ValueError(f"{name} image must match its shape and have uint8 RGB pixels")
        vectors = {}
        for name, values in (("observation.state", state_rad), ("action", action_rad)):
            if isinstance(values, np.ndarray):
                values = values.tolist()
            degrees = public_radians_to_degrees(values)
            if any(abs(value) > np.finfo(np.float32).max for value in degrees):
                raise ValueError(f"{name} must fit finite float32 degrees")
            vectors[name] = np.asarray(degrees, dtype=np.float32)
        if not isinstance(timing, dict) or any(
            not finite_number(timing.get(key)) or timing[key] < 0 for key in TIMING_KEYS
        ):
            raise ValueError("timing requires four finite nonnegative monotonic timestamps")
        if not timing["input_received_s"] <= timing["observation_read_s"] <= timing["command_sent_s"] \
                or timing["camera_host_s"] > timing["command_sent_s"]:
            raise ValueError("Input and observations must precede the command send")
        if self.dual_camera:
            cameras = timing.get("cameras")
            if not isinstance(cameras, dict) or set(cameras) != {"scene", "wrist"}:
                raise ValueError("timing must contain scene and wrist camera metadata")
            for name, metadata in cameras.items():
                if not isinstance(metadata, dict) or not finite_number(
                    metadata.get("camera_host_s")
                ) or metadata["camera_host_s"] < 0 or \
                        type(metadata.get("camera_frame_index")) is not int or \
                        metadata["camera_frame_index"] < 0:
                    raise ValueError(f"{name} needs finite host time and frame index")
                if metadata["camera_host_s"] > timing["command_sent_s"] or \
                        timing["command_sent_s"] - metadata["camera_host_s"] > .2:
                    raise ValueError(f"{name} frame is stale or from the future")
            scene_s, wrist_s = (cameras[name]["camera_host_s"] for name in ("scene", "wrist"))
            skew_ms = abs(scene_s - wrist_s) * 1000
            if skew_ms > self.max_camera_skew_ms:
                raise ValueError("dual camera host timestamp skew exceeds limit")
            if not finite_number(timing.get("camera_pair_skew_ms")) or not math.isclose(
                timing["camera_pair_skew_ms"], skew_ms, abs_tol=.001
            ):
                raise ValueError("camera_pair_skew_ms must match measured host timestamps")
            if timing["camera_host_s"] != max(scene_s, wrist_s):
                raise ValueError("camera_host_s must be the newer camera host timestamp")
        if self._timings:
            previous = self._timings[-1]
            if any(timing[key] < previous[key] for key in TIMING_KEYS) or any(
                timing[key] == previous[key] for key in ("camera_host_s", "command_sent_s")
            ):
                raise ValueError("Frame timing reversed or camera/command timestamp repeated")
            if self.dual_camera and any(
                cameras[name]["camera_host_s"] <= previous["cameras"][name]["camera_host_s"] or
                cameras[name]["camera_frame_index"] <=
                previous["cameras"][name]["camera_frame_index"]
                for name in ("scene", "wrist")
            ):
                raise ValueError("Both cameras need newer frames before recording")
        # Validate and copy sidecar fields before mutating the native episode buffer.
        timing = json.loads(json.dumps(timing, allow_nan=False))
        self.dataset.add_frame({**vectors, **{
            f"observation.images.{name}": image for name, image in images.items()
        }, "task": self.task})
        self._timings.append(timing)

    def finish(self, outcome):
        """Save a nonempty episode labelled 'success' or 'failure'; return its index."""
        if self._closed:
            raise RuntimeError("Recorder is closed")
        if outcome not in ("success", "failure"):
            raise ValueError("outcome must be 'success' or 'failure'")
        if not self._active or not self._timings:
            self.discard()
            return None
        episode = self._episode_index
        self.dataset.save_episode(parallel_encoding=False)
        with (self._sidecar / "frames.jsonl").open("a", encoding="utf-8") as stream:
            for frame, timing in enumerate(self._timings):
                stream.write(json.dumps({
                    **timing,
                    "episode_index": episode, "frame_index": frame,
                    "timestamp": frame / self.fps,
                    "synthetic": self.synthetic, "physical_task_verified": False,
                }) + "\n")
        with (self._sidecar / "episodes.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "episode_index": episode, "outcome": outcome, "frames": len(self._timings),
                "synthetic": self.synthetic, "physical_task_verified": False,
            }) + "\n")
        self._episode_index += 1
        self._timings.clear()
        self._active = False
        return episode

    def discard(self):
        if self._active:
            self.dataset.clear_episode_buffer(delete_images=True)
            self._timings.clear()
            self._active = False

    def close(self):
        """Discard unfinished work and finalize saved episodes; never upload."""
        if not self._closed:
            self.discard()
            self.dataset.finalize()
            self._manifest.update(episodes_saved=self._episode_index,
                                  status="saved_episodes" if self._episode_index else "empty")
            (self._sidecar / "manifest.json").write_text(json.dumps(self._manifest, indent=2) + "\n")
            self._closed = True
