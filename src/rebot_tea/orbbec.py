"""Gemini 2 RGB-D acquisition; no robot imports or motor commands.

Uses the public API of orbbec/pyorbbecsdk (Apache-2.0), installed as
pyorbbecsdk2. SDK loading is deferred so offline tests can use an injected SDK.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, fields
from importlib.metadata import version
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Gemini2Settings:
    serial_number: str | None = None
    width: int = 640
    height: int = 480
    fps: int = 30
    color_format: str = "RGB"
    timeout_ms: int = 1000
    max_pair_skew_ms: float = 20.0
    rectify: bool = True

    def __post_init__(self):
        for name in ("width", "height", "fps", "timeout_ms"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.timeout_ms > 10000:
            raise ValueError("timeout_ms must not exceed 10000")
        if self.serial_number is not None and (
            not isinstance(self.serial_number, str) or not self.serial_number.strip()
        ):
            raise ValueError("serial_number must be null or a non-empty string")
        if self.color_format not in ("RGB", "BGR", "MJPG", "YUYV", "UYVY"):
            raise ValueError("Unsupported color_format")
        if isinstance(self.max_pair_skew_ms, bool) or not isinstance(
            self.max_pair_skew_ms, (int, float)
        ) or not math.isfinite(self.max_pair_skew_ms) or not 0 < self.max_pair_skew_ms <= 100:
            raise ValueError("max_pair_skew_ms must be finite and in (0, 100]")
        if type(self.rectify) is not bool:
            raise ValueError("rectify must be a boolean")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("model") != "Orbbec Gemini 2":
            raise ValueError("Camera config must identify Orbbec Gemini 2")
        allowed = {item.name for item in fields(cls)} | {"model"}
        if set(data) - allowed:
            raise ValueError(f"Unknown camera settings: {sorted(set(data) - allowed)}")
        return cls(**{key: val for key, val in data.items() if key != "model"})


@dataclass(frozen=True)
class RGBDPacket:
    rgb: np.ndarray
    depth: np.ndarray
    color_timestamp_us: int
    depth_timestamp_us: int
    color_frame_index: int
    depth_frame_index: int
    host_monotonic_s: float
    calibration: dict


def load_sdk():
    try:
        import pyorbbecsdk as sdk
    except ImportError as error:
        raise RuntimeError("Install camera dependencies: bash scripts/bootstrap_camera.sh") from error
    return sdk


def is_gemini2(name):
    return re.sub(r"[^a-z0-9]", "", name.lower()) in ("gemini2", "orbbecgemini2")


def device_info(device):
    info = device.get_device_info()
    return {
        "name": info.get_name(), "serial_number": info.get_serial_number(),
        "firmware": info.get_firmware_version(), "usb_connection": info.get_connection_type(),
        "vid": info.get_vid(), "pid": info.get_pid(),
    }


def list_devices(sdk=None):
    sdk = sdk or load_sdk()
    context = sdk.Context()
    devices = context.query_devices()
    result = []
    for index in range(devices.get_count()):
        device = devices.get_device_by_index(index)
        info = device_info(device)
        info["is_gemini2"] = is_gemini2(info["name"])
        pipeline = sdk.Pipeline(device)
        info["profiles"] = {}
        for label, sensor in (("color", sdk.OBSensorType.COLOR_SENSOR),
                              ("depth", sdk.OBSensorType.DEPTH_SENSOR)):
            profiles = pipeline.get_stream_profile_list(sensor)
            info["profiles"][label] = [
                profile_info(profiles.get_stream_profile_by_index(i).as_video_stream_profile())
                for i in range(profiles.get_count())
            ]
        result.append(info)
    return result


def profile_info(profile):
    return {"width": profile.get_width(), "height": profile.get_height(),
            "fps": profile.get_fps(), "format": str(profile.get_format()).split(".")[-1]}


def color_to_rgb(frame, sdk):
    width, height = frame.get_width(), frame.get_height()
    data = np.frombuffer(frame.get_data(), dtype=np.uint8)
    fmt = frame.get_format()
    if fmt == sdk.OBFormat.MJPG:
        bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if bgr is None or bgr.shape != (height, width, 3):
            raise ValueError("Invalid MJPG color frame")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if fmt in (sdk.OBFormat.RGB, sdk.OBFormat.BGR):
        image = data.reshape(height, width, 3)
        return image.copy() if fmt == sdk.OBFormat.RGB else image[:, :, ::-1].copy()
    conversions = {sdk.OBFormat.YUYV: cv2.COLOR_YUV2RGB_YUY2,
                   sdk.OBFormat.UYVY: cv2.COLOR_YUV2RGB_UYVY}
    if fmt in conversions:
        return cv2.cvtColor(data.reshape(height, width, 2), conversions[fmt])
    raise ValueError(f"Unsupported color frame format: {fmt}")


def color_calibration(profile):
    intrinsic = profile.get_intrinsic()
    result = {key: getattr(intrinsic, key) for key in ("width", "height", "fx", "fy", "cx", "cy")}
    if not np.isfinite(list(result.values())).all() or min(result["fx"], result["fy"]) <= 0:
        raise ValueError("Invalid camera intrinsics")
    distortion = profile.get_distortion()
    # SDK 2.0.18 OBCameraDistortion is the eight-coefficient rational Brown model.
    result["distortion_coefficients"] = [
        float(getattr(distortion, key)) for key in ("k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6")
    ]
    if not np.isfinite(result["distortion_coefficients"]).all():
        raise ValueError("Invalid camera distortion")
    return result


class Gemini2Source:
    def __init__(self, settings=None, sdk=None, clock=time.monotonic):
        self.settings = settings or Gemini2Settings()
        self.sdk = sdk
        self.clock = clock
        self.pipeline = None
        self.context = None
        self.connected = False
        self.info = None
        self.calibration = None
        self.maps = None
        self.last_timestamps = None

    def connect(self):
        if self.connected:
            raise RuntimeError("Gemini 2 is already connected")
        sdk = self.sdk = self.sdk or load_sdk()
        self.context = sdk.Context()
        devices = self.context.query_devices()
        matches = []
        for index in range(devices.get_count()):
            device = devices.get_device_by_index(index)
            info = device_info(device)
            if is_gemini2(info["name"]) and (
                self.settings.serial_number is None
                or info["serial_number"] == self.settings.serial_number
            ):
                matches.append((device, info))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one matching Gemini 2, found {len(matches)}; check USB/SN")
        device, self.info = matches[0]
        firmware = re.search(r"(\d+)\.(\d+)\.(\d+)", self.info["firmware"])
        if firmware is None or tuple(map(int, firmware.groups())) < (1, 4, 92):
            raise ValueError("Gemini 2 firmware must be >= 1.4.92 for the selected SDK v2")
        self.pipeline = sdk.Pipeline(device)
        config = sdk.Config()
        color_list = self.pipeline.get_stream_profile_list(sdk.OBSensorType.COLOR_SENSOR)
        color = color_list.get_video_stream_profile(
            self.settings.width, self.settings.height,
            getattr(sdk.OBFormat, self.settings.color_format), self.settings.fps,
        )
        depth_list = self.pipeline.get_stream_profile_list(sdk.OBSensorType.DEPTH_SENSOR)
        depth = depth_list.get_video_stream_profile(0, 0, sdk.OBFormat.Y16, self.settings.fps)
        config.enable_stream(color)
        config.enable_stream(depth)
        config.set_frame_aggregate_output_mode(sdk.OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
        self.pipeline.enable_frame_sync()
        self.align = sdk.AlignFilter(align_to_stream=sdk.OBStreamType.COLOR_STREAM)
        self.calibration = color_calibration(color)
        if (self.calibration["width"], self.calibration["height"]) != (
            self.settings.width, self.settings.height
        ) or color.get_fps() != self.settings.fps:
            raise ValueError("SDK profile/calibration does not match the requested stream mode")
        self.info["selected_profiles"] = {"color": profile_info(color), "depth": profile_info(depth)}
        self.maps = None
        self.last_timestamps = None
        if self.settings.rectify:
            c = self.calibration
            matrix = np.array([[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1]])
            self.maps = cv2.initUndistortRectifyMap(
                matrix, np.array(c["distortion_coefficients"]), None, matrix,
                (self.settings.width, self.settings.height), cv2.CV_32FC1,
            )
        try:
            self.pipeline.start(config)
            self.connected = True
        except Exception:
            # A failed start may still have partially allocated device resources.
            try:
                self.pipeline.stop()
            except Exception:
                pass
            self.pipeline = None
            raise

    def read_packet(self):
        if not self.connected:
            raise RuntimeError("Gemini 2 is not connected")
        frames = self.pipeline.wait_for_frames(self.settings.timeout_ms)
        if frames is None:
            raise TimeoutError("Gemini 2 did not return a synchronized RGB-D frameset")
        host_s = self.clock()
        if not math.isfinite(host_s):
            raise ValueError("Invalid host monotonic timestamp")
        frames = self.align.process(frames)
        if frames is None:
            raise ValueError("D2C alignment returned no frameset")
        frames = frames.as_frame_set()
        color, depth = frames.get_color_frame(), frames.get_depth_frame()
        if color is None or depth is None:
            raise ValueError("Missing color or depth in synchronized frameset")
        timestamps = (color.get_timestamp_us(), depth.get_timestamp_us())
        if any(type(t) is not int or t < 0 for t in timestamps):
            raise ValueError("Invalid device timestamp")
        if abs(timestamps[0] - timestamps[1]) > self.settings.max_pair_skew_ms * 1000:
            raise ValueError("RGB/depth timestamp skew exceeds configured limit")
        if self.last_timestamps is not None and any(
            current <= previous for current, previous in zip(timestamps, self.last_timestamps)
        ):
            raise ValueError("Repeated or reversed RGB/depth timestamps")
        rgb = color_to_rgb(color, self.sdk)
        if depth.get_format() not in (self.sdk.OBFormat.Y16, self.sdk.OBFormat.Z16):
            raise ValueError("Depth format must be uncompressed uint16")
        depth_data = np.frombuffer(depth.get_data(), dtype=np.uint16).reshape(
            depth.get_height(), depth.get_width()
        ).copy()
        expected = (self.settings.height, self.settings.width)
        if rgb.shape != (*expected, 3) or depth_data.shape != expected:
            raise ValueError("D2C frame sizes do not match the requested color resolution")
        scale_m = float(depth.get_depth_scale()) / 1000.0  # SDK reports millimetres per unit.
        if not math.isfinite(scale_m) or scale_m <= 0:
            raise ValueError("Invalid SDK depth scale")
        if not np.any(depth_data):
            raise ValueError("Depth frame contains no valid samples")
        if self.maps is not None:
            rgb = cv2.remap(rgb, *self.maps, interpolation=cv2.INTER_LINEAR)
            depth_data = cv2.remap(depth_data, *self.maps, interpolation=cv2.INTER_NEAREST)
        if not np.any(depth_data):
            raise ValueError("Rectified depth frame contains no valid samples")
        calibration = {
            **self.calibration, "depth_scale_m": scale_m,
            "original_distortion_coefficients": self.calibration["distortion_coefficients"],
            "distortion_coefficients": [0.0] * 8 if self.settings.rectify
            else self.calibration["distortion_coefficients"],
            "rectified": self.settings.rectify, "depth_aligned_to_color": True,
            "T_base_camera": None, "synthetic": False,
            "frame": "camera_color_optical", "extrinsics_verified": False,
        }
        self.last_timestamps = timestamps
        return RGBDPacket(
            rgb, depth_data, *timestamps, color.get_index(), depth.get_index(), host_s, calibration,
        )

    def disconnect(self):
        try:
            if self.connected:
                self.pipeline.stop()
        finally:
            self.connected = False
            self.pipeline = None
            self.context = None


def save_packet(packet, directory, index=0):
    """Export an RGB-D sample with actual intrinsics; base extrinsics remain unknown."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"frame-{index:06d}"
    color_path, depth_path = directory/f"{stem}.png", directory/f"{stem}.npy"
    calibration_path = directory/f"{stem}.calibration.json"
    if any(path.exists() for path in (color_path, depth_path, calibration_path)):
        raise FileExistsError(f"Preserve existing RGB-D sample: {stem}")
    if not cv2.imwrite(str(color_path), cv2.cvtColor(packet.rgb, cv2.COLOR_RGB2BGR)):
        raise OSError(f"Could not save color image: {color_path}")
    with depth_path.open("xb") as stream:
        np.save(stream, packet.depth, allow_pickle=False)
    calibration_path.write_text(json.dumps(packet.calibration, indent=2) + "\n")
    return {
        "synthetic": False, "color": color_path.name, "depth": depth_path.name,
        "calibration": calibration_path.name, "color_timestamp_us": packet.color_timestamp_us,
        "depth_timestamp_us": packet.depth_timestamp_us,
        "color_frame_index": packet.color_frame_index, "depth_frame_index": packet.depth_frame_index,
        "host_monotonic_s": packet.host_monotonic_s,
    }


def software_info():
    return {name: version(name) for name in ("pyorbbecsdk2", "numpy", "opencv-python-headless")}
