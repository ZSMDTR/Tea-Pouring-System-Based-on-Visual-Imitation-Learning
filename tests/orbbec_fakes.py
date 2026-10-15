"""Deterministic SDK substitute; never imports a hardware SDK."""
from types import SimpleNamespace
import numpy as np


class Profile:
    def __init__(self, fmt="RGB", width=4, height=2):
        self.fmt, self.width, self.height = fmt, width, height

    def as_video_stream_profile(self):
        return self

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_fps(self):
        return 30

    def get_format(self):
        return self.fmt

    def get_intrinsic(self):
        return SimpleNamespace(width=self.width, height=self.height, fx=10., fy=10., cx=1.5, cy=.5)

    def get_distortion(self):
        return SimpleNamespace(**dict.fromkeys(("k1", "k2", "k3", "k4", "k5", "k6", "p1", "p2"), 0.))


class ProfileList:
    def __init__(self, profile):
        self.profile = profile

    def get_video_stream_profile(self, width, height, fmt, fps):
        if width not in (0, self.profile.width) or height not in (0, self.profile.height) or fps != 30:
            raise ValueError("Unsupported fake stream request")
        return self.profile

    def get_count(self):
        return 1

    def get_stream_profile_by_index(self, index):
        return self.profile


class Frame:
    def __init__(self, data, timestamp, fmt="RGB", scale=0.1):
        self.data, self.timestamp, self.fmt, self.scale = data, timestamp, fmt, scale

    def get_width(self):
        return self.data.shape[1]

    def get_height(self):
        return self.data.shape[0]

    def get_data(self):
        return self.data.tobytes()

    def get_format(self):
        return self.fmt

    def get_timestamp_us(self):
        return self.timestamp

    def get_depth_scale(self):
        return self.scale

    def get_index(self):
        return self.timestamp // 10000


class Frames:
    def __init__(self, timestamp=1000000, skew=1000):
        rgb = np.zeros((2, 4, 3), dtype=np.uint8)
        rgb[:] = (10, 20, 30)
        self.color = Frame(rgb, timestamp)
        self.depth = Frame(np.full((2, 4), 1000, dtype=np.uint16), timestamp+skew, "Y16")

    def as_frame_set(self):
        return self

    def get_color_frame(self):
        return self.color

    def get_depth_frame(self):
        return self.depth


class FakeSDK:
    OBFormat = SimpleNamespace(**{name: name for name in ("RGB", "BGR", "MJPG", "YUYV", "UYVY", "Y16", "Z16")})
    OBSensorType = SimpleNamespace(COLOR_SENSOR="color", DEPTH_SENSOR="depth")
    OBStreamType = SimpleNamespace(COLOR_STREAM="color")
    OBFrameAggregateOutputMode = SimpleNamespace(FULL_FRAME_REQUIRE="full")

    def __init__(self, names=("Gemini 2",), firmware="1.4.98"):
        self.names, self.firmware = names, firmware
        self.frames = Frames()
        self.started = False
        self.stops = 0
        self.sync = False
        self.enabled = []

    def Context(self):
        return SimpleNamespace(query_devices=lambda: SimpleNamespace(
            get_count=lambda: len(self.names), get_device_by_index=self.device,
        ))

    def device(self, index):
        info = SimpleNamespace(
            get_name=lambda: self.names[index], get_serial_number=lambda: f"SN{index}",
            get_firmware_version=lambda: self.firmware, get_connection_type=lambda: "USB3.0",
            get_vid=lambda: 0x2BC5, get_pid=lambda: 1,
        )
        return SimpleNamespace(get_device_info=lambda: info)

    def Pipeline(self, device):
        return self

    def get_stream_profile_list(self, sensor):
        return ProfileList(Profile("RGB" if sensor == "color" else "Y16"))

    def Config(self):
        return SimpleNamespace(enable_stream=self.enabled.append,
                               set_frame_aggregate_output_mode=lambda mode: None)

    def enable_frame_sync(self):
        self.sync = True

    def AlignFilter(self, align_to_stream):
        return SimpleNamespace(process=lambda frames: frames)

    def start(self, config):
        self.started = True

    def wait_for_frames(self, timeout_ms):
        return self.frames

    def stop(self):
        self.stops += 1
        self.started = False
