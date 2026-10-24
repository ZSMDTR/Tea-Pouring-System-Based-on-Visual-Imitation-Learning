"""Loopback-only gamepad input; importing this module never opens hardware or sockets."""
from dataclasses import dataclass
import ipaddress
import json
import math
import socket
import threading
import time


AXES = ("LX", "LY", "RX", "RY", "LT", "RT")
BUTTONS = frozenset({
    "A", "B", "X", "Y", "LB", "RB", "DPadUp", "DPadDown", "DPadLeft", "DPadRight",
    "Start", "Back", "LeftStick", "RightStick",
})
MAX_LINE_BYTES = 4096


@dataclass(frozen=True)
class GamepadFrame:
    received_s: float
    sequence: int
    axes: tuple[float, ...]
    buttons: frozenset[str]
    connected: bool = True
    synthetic: bool = False


def _integer(value, lower=0, upper=None):
    return (type(value) is int and value >= lower and
            (upper is None or value <= upper))


def _response(line: bytes, request_id: int, received_s: float) -> GamepadFrame:
    try:
        data = json.loads(line)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid response JSON") from error
    required = {"version", "request_id", "connected", "axes", "buttons", "packet_number"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise ValueError("missing response fields")
    if data.keys() - required - {"synthetic"}:
        raise ValueError("unknown response fields")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("unsupported protocol version")
    if not _integer(data["request_id"], 1) or data["request_id"] != request_id:
        raise ValueError("response request_id does not match")
    if type(data["connected"]) is not bool or type(data.get("synthetic", False)) is not bool:
        raise ValueError("connected and synthetic must be booleans")
    if not _integer(data["packet_number"], 0, 0xFFFFFFFF):
        raise ValueError("invalid XInput packet number")
    axes = data["axes"]
    if not isinstance(axes, list) or len(axes) != len(AXES):
        raise ValueError("expected six axes")
    for index, value in enumerate(axes):
        lower = -1.0 if index < 4 else 0.0
        if (type(value) not in (int, float) or not lower <= value <= 1.0 or
                not math.isfinite(value)):
            raise ValueError("axis outside its finite normalized range")
    buttons = data["buttons"]
    if (not isinstance(buttons, list) or any(type(button) is not str for button in buttons) or
            len(set(buttons)) != len(buttons) or not set(buttons) <= BUTTONS):
        raise ValueError("invalid buttons")
    return GamepadFrame(received_s, request_id, tuple(float(value) for value in axes),
                        frozenset(buttons), data["connected"], data.get("synthetic", False))


class InputBridge:
    """Poll one local client in a worker thread; expire input using the local clock."""

    def __init__(self, host="127.0.0.1", port=8765, timeout_s=.2, poll_hz=50):
        address = ipaddress.ip_address(host)
        if not address.is_loopback:
            raise ValueError("gamepad bridge must listen on a loopback address")
        if not _integer(port, 0, 65535):
            raise ValueError("invalid port")
        for value in (timeout_s, poll_hz):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("timeout_s and poll_hz must be finite and positive")
        self.host, self.port = host, port
        self.timeout_s, self.poll_hz = float(timeout_s), float(poll_hz)
        self._family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server = self._client = self._thread = None
        self._frame = None
        self._error = None
        self._sequence = 0

    @property
    def last_error(self):
        with self._lock:
            return self._error

    def _clear(self, reason):
        with self._lock:
            self._frame, self._error = None, reason

    def start(self):
        if self._thread is not None:
            raise RuntimeError("bridge has already been started")
        server = socket.socket(self._family, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(1)
            server.settimeout(.05)
        except Exception:
            server.close()
            raise
        self._server = server
        self.port = server.getsockname()[1]
        self._thread = threading.Thread(target=self._run, name="gamepad-input", daemon=True)
        self._thread.start()

    def snapshot(self, now_s=None):
        now = time.monotonic() if now_s is None else now_s
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("snapshot time must be finite")
        with self._lock:
            if self._frame is None:
                return None
            age = now - self._frame.received_s
            if age < 0 or age > self.timeout_s:
                self._frame, self._error = None, "input frame expired or clock reversed"
            return self._frame

    def _line(self, client, deadline):
        data = bytearray()
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("response deadline exceeded")
            client.settimeout(remaining)
            chunk = client.recv(MAX_LINE_BYTES + 1 - len(data))
            if not chunk:
                raise ConnectionError("input client disconnected")
            data.extend(chunk)
            if len(data) > MAX_LINE_BYTES:
                raise ValueError("response exceeds 4096 bytes")
            if b"\n" in data:
                line, tail = data.split(b"\n", 1)
                if tail:
                    raise ValueError("unsolicited response bytes")
                return bytes(line)
        raise ConnectionError("bridge closed")

    def _serve(self, client):
        while not self._stop.is_set():
            started = time.monotonic()
            self._sequence += 1
            request = {"version": 1, "request_id": self._sequence}
            client.settimeout(self.timeout_s)
            client.sendall((json.dumps(request) + "\n").encode("ascii"))
            line = self._line(client, started + self.timeout_s)
            received = time.monotonic()
            if received - started > self.timeout_s:
                raise TimeoutError("response deadline exceeded")
            frame = _response(line, self._sequence, received)
            if frame.connected:
                with self._lock:
                    self._frame, self._error = frame, None
            else:
                self._clear("gamepad disconnected")
            self._stop.wait(max(0.0, 1.0 / self.poll_hz - (time.monotonic() - started)))

    def _run(self):
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._client = client
            try:
                self._serve(client)
            except (OSError, ValueError, ConnectionError) as error:
                self._clear(f"{type(error).__name__}: {error}")
            finally:
                client.close()
                self._client = None
                self._clear(self.last_error or "input client disconnected")

    def close(self):
        self._stop.set()
        for connection in (self._client, self._server):
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        if self._thread is not None:
            self._thread.join(timeout=self.timeout_s + .2)
        self._clear("bridge closed")
