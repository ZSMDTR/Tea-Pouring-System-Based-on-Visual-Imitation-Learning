"""Display XInput locally, or answer a loopback WSL bridge's input polls. No robot APIs."""
import argparse
import ctypes
import ipaddress
import json
import math
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rebot_tea.gamepad_bridge import MAX_LINE_BYTES  # noqa: E402

# Original implementation against Microsoft's public XInput API:
# https://learn.microsoft.com/windows/win32/api/xinput/nf-xinput-xinputgetstate
# API usage reference: https://github.com/Zuzu-Typ/XInput-Python (Zlib).
# This file does not copy that project's implementation or call XInputSetState.
BUTTON_BITS = {
    "DPadUp": 0x0001, "DPadDown": 0x0002, "DPadLeft": 0x0004, "DPadRight": 0x0008,
    "Start": 0x0010, "Back": 0x0020, "LeftStick": 0x0040, "RightStick": 0x0080,
    "LB": 0x0100, "RB": 0x0200, "A": 0x1000, "B": 0x2000,
    "X": 0x4000, "Y": 0x8000,
}


class XInputGamepad(ctypes.Structure):
    _fields_ = [("buttons", ctypes.c_uint16), ("lt", ctypes.c_uint8),
                ("rt", ctypes.c_uint8), ("lx", ctypes.c_int16), ("ly", ctypes.c_int16),
                ("rx", ctypes.c_int16), ("ry", ctypes.c_int16)]


class XInputState(ctypes.Structure):
    _fields_ = [("packet", ctypes.c_uint32), ("gamepad", XInputGamepad)]


class XInputReader:
    def __init__(self, slot):
        if sys.platform != "win32":
            raise RuntimeError("real XInput must run on Windows; --synthetic is test-only")
        self.slot = slot
        for name in ("XInput1_4.dll", "XInput9_1_0.dll"):
            try:
                self.library = ctypes.WinDLL(name)
                break
            except OSError:
                continue
        else:
            raise RuntimeError("Windows XInput DLL is unavailable")
        self.get_state = self.library.XInputGetState
        self.get_state.argtypes = [ctypes.c_uint32, ctypes.POINTER(XInputState)]
        self.get_state.restype = ctypes.c_uint32

    def read(self):
        state = XInputState()
        result = self.get_state(self.slot, ctypes.byref(state))
        if result == 1167:
            return {"connected": False, "axes": [0.0] * 6, "buttons": [],
                    "packet_number": 0, "synthetic": False}
        if result:
            raise OSError(result, "XInputGetState failed")
        pad = state.gamepad
        sticks = (pad.lx, pad.ly, pad.rx, pad.ry)
        axes = [value / (32767.0 if value >= 0 else 32768.0) for value in sticks]
        return {"connected": True, "axes": axes + [pad.lt / 255.0, pad.rt / 255.0],
                "buttons": [name for name, bit in BUTTON_BITS.items() if pad.buttons & bit],
                "packet_number": state.packet, "synthetic": False}


def synthetic_input(elapsed_s):
    """Labelled test episode: neutral, record, translate, rotate, save, then neutral."""
    axes, buttons = [0.0] * 6, []
    if .5 <= elapsed_s < .7:
        buttons = ["A"]
    elif 1.0 <= elapsed_s < 2.0:
        buttons, axes[0] = ["LB"], .35
    elif 2.4 <= elapsed_s < 2.6:
        buttons = ["RB"]
    elif 2.9 <= elapsed_s < 3.8:
        buttons, axes[3], axes[5] = ["LB"], .25, .25
    elif 4.2 <= elapsed_s < 4.4:
        buttons = ["B"]
    return {"connected": True, "axes": axes, "buttons": buttons,
            "packet_number": 0, "synthetic": True}


def loopback_target(value):
    host, separator, port_text = value.rpartition(":")
    host = host.strip("[]")
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
        port = int(port_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected loopback HOST:PORT") from error
    if not separator or not address.is_loopback or not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("target must be a loopback address and valid port")
    return host, port


def serve_polls(connection, read_input, end_s):
    """Read only after each request, so no cached input queue crosses into WSL."""
    pending = bytearray()
    last_request = 0
    connection.settimeout(.1)
    while end_s is None or time.monotonic() < end_s:
        try:
            chunk = connection.recv(MAX_LINE_BYTES + 1 - len(pending))
        except socket.timeout:
            continue
        if not chunk:
            if pending:
                raise ValueError("WSL bridge closed during a partial request")
            return
        pending.extend(chunk)
        if len(pending) > MAX_LINE_BYTES:
            raise ValueError("request exceeds 4096 bytes")
        if b"\n" not in pending:
            continue
        line, tail = pending.split(b"\n", 1)
        if tail:
            raise ValueError("expected one outstanding request")
        pending.clear()
        request = json.loads(line)
        if (not isinstance(request, dict) or set(request) != {"version", "request_id"} or
                type(request["version"]) is not int or request["version"] != 1 or
                type(request["request_id"]) is not int or request["request_id"] <= last_request):
            raise ValueError("invalid or repeated bridge request")
        last_request = request["request_id"]
        response = {"version": 1, "request_id": last_request, **read_input()}
        connection.sendall((json.dumps(response, allow_nan=False) + "\n").encode("ascii"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", type=loopback_target, metavar="HOST:PORT",
                        help="answer WSL input polls; omitted means local display only")
    parser.add_argument("--duration", type=float, default=0,
                        help="stop after this many seconds; 0 runs until Ctrl+C")
    parser.add_argument("--slot", type=int, choices=range(4), default=0)
    parser.add_argument("--synthetic", action="store_true",
                        help="explicitly labelled test input instead of physical XInput")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration < 0:
        parser.error("duration must be finite and nonnegative")
    try:
        started_s = time.monotonic()
        read_input = (lambda: synthetic_input(time.monotonic() - started_s)) if args.synthetic else (
            XInputReader(args.slot).read)
        end_s = started_s + args.duration if args.duration else None
        if args.connect:
            print(json.dumps({"mode": "bridge_client", "synthetic": args.synthetic,
                              "target": f"{args.connect[0]}:{args.connect[1]}"}), flush=True)
            with socket.create_connection(args.connect, timeout=2.0) as connection:
                # Start the scripted neutral phase after connection, even if connecting was slow.
                started_s = time.monotonic()
                end_s = started_s + args.duration if args.duration else None
                serve_polls(connection, read_input, end_s)
        else:
            while end_s is None or time.monotonic() < end_s:
                print(json.dumps({"mode": "local_display", **read_input()}), flush=True)
                time.sleep(.1)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
