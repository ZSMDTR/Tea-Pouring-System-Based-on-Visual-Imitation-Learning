"""LeRobot camera plugin, registered by scripts/lerobot_orbbec.py before CLI parsing."""
from dataclasses import dataclass
import math
import threading
import time

from lerobot.cameras.camera import Camera
from lerobot.cameras.configs import CameraConfig

from .orbbec import Gemini2Settings, Gemini2Source, list_devices


@CameraConfig.register_subclass("orbbec_gemini2")
@dataclass
class Gemini2CameraConfig(CameraConfig):
    width: int = 640
    height: int = 480
    fps: int = 30
    serial_number: str | None = None
    color_format: str = "RGB"
    timeout_ms: int = 1000
    max_pair_skew_ms: float = 20.0
    rectify: bool = True
    use_rgb: bool = True
    use_depth: bool = False

    def __post_init__(self):
        if self.use_rgb is not True or type(self.use_depth) is not bool:
            raise ValueError("Gemini 2 plugin requires RGB; use_depth must be boolean")
        self.settings()  # Share validation with the standalone acquisition path.

    def settings(self):
        return Gemini2Settings(**{
            name: getattr(self, name) for name in (
                "serial_number", "width", "height", "fps", "color_format", "timeout_ms",
                "max_pair_skew_ms", "rectify",
            )
        })


class Gemini2Camera(Camera):
    """Background RGB-D capture with bounded waits and paired RGB/depth reads."""

    def __init__(self, config, source_factory=Gemini2Source, clock=time.monotonic):
        super().__init__(config)
        self.config = config
        self.use_depth = config.use_depth
        self._source_factory = source_factory
        self._clock = clock
        self._source = None
        self._connected = False
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread = None
        self._packet = None
        self._rgb_packet = None
        self._sequence = 0
        self._consumed = 0
        self._error = None

    @property
    def is_connected(self):
        return self._connected

    @staticmethod
    def find_cameras():
        return [item for item in list_devices() if item["is_gemini2"]]

    def connect(self, warmup=True):
        if self.is_connected:
            raise RuntimeError("Gemini 2 camera is already connected")
        self._source = self._source_factory(self.config.settings())
        try:
            self._source.connect()
            packet = self._source.read_packet()
            self._packet, self._rgb_packet = packet, None
            self._sequence, self._consumed = 1, 0
            self._error = None
            self._stop.clear()
            self._connected = True
            self._thread = threading.Thread(target=self._capture, name="gemini2-capture", daemon=True)
            self._thread.start()
        except Exception:
            self._source.disconnect()
            self._source = None
            raise

    def _capture(self):
        while not self._stop.is_set():
            try:
                packet = self._source.read_packet()
                with self._condition:
                    self._packet = packet
                    self._sequence += 1
                    self._condition.notify_all()
            except Exception as error:
                with self._condition:
                    self._error = error
                    self._condition.notify_all()
                return

    def _check(self):
        if not self.is_connected:
            raise RuntimeError("Gemini 2 camera is not connected")
        if self._error is not None:
            raise RuntimeError(f"Gemini 2 acquisition stopped: {self._error}") from self._error

    @staticmethod
    def _timeout_ms(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or value <= 0:
            raise ValueError("Camera timeout/age limit must be finite and positive")
        return value / 1000.0

    def _check_age(self, packet, max_age_ms):
        age = self._clock() - packet.host_monotonic_s
        if age < 0 or age > self._timeout_ms(max_age_ms):
            raise TimeoutError("Latest Gemini 2 frame is stale or its clock is invalid")

    def read(self):
        return self.async_read(self.config.timeout_ms)

    def async_read(self, timeout_ms=200):
        timeout_s = self._timeout_ms(timeout_ms)
        with self._condition:
            self._check()
            ready = self._condition.wait_for(
                lambda: self._sequence > self._consumed or self._error is not None
                or not self.is_connected, timeout_s,
            )
            self._check()
            if not ready:
                raise TimeoutError("No new Gemini 2 frame before timeout")
            self._check_age(self._packet, timeout_ms)
            self._consumed = self._sequence
            self._rgb_packet = self._packet
            return self._packet.rgb.copy()

    def read_latest(self, max_age_ms=500):
        with self._condition:
            self._check()
            self._check_age(self._packet, max_age_ms)
            self._rgb_packet = self._packet
            return self._packet.rgb.copy()

    def read_latest_depth(self, max_age_ms=500):
        with self._condition:
            self._check()
            packet = self._rgb_packet or self._packet
            self._check_age(packet, max_age_ms)
            return packet.depth[:, :, None].copy()

    def disconnect(self):
        if not self._connected:
            return
        self._stop.set()
        self._thread.join(self.config.timeout_ms / 1000.0 + 2.0)
        if self._thread.is_alive():
            raise RuntimeError("Gemini 2 capture thread did not stop; device ownership retained")
        try:
            self._source.disconnect()
        finally:
            with self._condition:
                self._connected = False
                self._condition.notify_all()
            self._source, self._packet, self._rgb_packet = None, None, None
