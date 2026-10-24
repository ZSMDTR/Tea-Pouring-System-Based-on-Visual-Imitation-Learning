import argparse
import importlib.util
import json
from pathlib import Path
import socket
import threading
import time
import unittest

from rebot_tea.gamepad_bridge import AXES, GamepadFrame, InputBridge, _response


def response(request_id=1, **updates):
    data = {"version": 1, "request_id": request_id, "connected": True,
            "axes": [-1., 1., -.5, .5, 0., 1.], "buttons": ["LB", "A"],
            "packet_number": 0xFFFFFFFF, "synthetic": True}
    data.update(updates)
    return (json.dumps(data) + "\n").encode("ascii")


class FrameTests(unittest.TestCase):
    def test_frame_api_and_normalized_boundaries(self):
        frame = _response(response(), 1, 123.)
        self.assertIsInstance(frame, GamepadFrame)
        self.assertEqual(AXES, ("LX", "LY", "RX", "RY", "LT", "RT"))
        self.assertEqual(frame.sequence, 1)
        self.assertEqual(frame.received_s, 123.)
        self.assertEqual(frame.buttons, frozenset({"LB", "A"}))
        self.assertTrue(frame.synthetic)
        self.assertFalse(_response(response(connected=False), 1, 123.).connected)

    def test_malformed_protocol_types_and_ranges_are_rejected(self):
        invalid = [
            {"version": True}, {"version": 2}, {"request_id": True}, {"request_id": 2},
            {"connected": 1}, {"synthetic": "false"}, {"packet_number": True},
            {"packet_number": -1}, {"packet_number": 0x100000000}, {"axes": [0.] * 5},
            {"axes": [True, 0, 0, 0, 0, 0]}, {"axes": [1.001, 0, 0, 0, 0, 0]},
            {"axes": [0, 0, 0, 0, -.01, 0]}, {"axes": [0, 0, 0, 0, 0, 1.001]},
            {"axes": [float("nan"), 0, 0, 0, 0, 0]},
            {"axes": [float("inf"), 0, 0, 0, 0, 0]},
            {"axes": [10 ** 1000, 0, 0, 0, 0, 0]},
            {"buttons": ["enable"]}, {"buttons": ["LB", "LB"]}, {"buttons": [1]},
            {"buttons": "LB"}, {"unexpected": 1},
        ]
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(ValueError):
                _response(response(**update), 1, 0.)
        for line in (b"[]", b"{}", b"garbage", b"\xff", b"[" * 1500):
            with self.subTest(line=line[:10]), self.assertRaises(ValueError):
                _response(line, 1, 0.)

    def test_receiver_is_loopback_only_and_validates_options(self):
        for options in ({"host": "0.0.0.0"}, {"host": "192.168.1.2"}, {"port": True},
                        {"port": 65536}, {"timeout_s": 0}, {"poll_hz": float("nan")}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                InputBridge(**options)


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.bridge = InputBridge(port=0, timeout_s=.1, poll_hz=100)
        self.bridge.start()
        self.connections = []

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        self.bridge.close()

    def connect(self):
        connection = socket.create_connection(("127.0.0.1", self.bridge.port), timeout=.5)
        connection.settimeout(.5)
        self.connections.append(connection)
        return connection

    def request(self, connection):
        data = bytearray()
        while b"\n" not in data:
            chunk = connection.recv(4096)
            self.assertTrue(chunk, "bridge closed before request")
            data.extend(chunk)
        return json.loads(data)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.002)
        self.fail(f"timed out; last_error={self.bridge.last_error}")

    def answer(self, connection, **updates):
        request = self.request(connection)
        self.assertEqual(request["version"], 1)
        connection.sendall(response(request["request_id"], **updates))
        return request

    def test_real_socket_roundtrip_and_local_freshness(self):
        connection = self.connect()
        request = self.answer(connection)
        frame = self.wait_for(self.bridge.snapshot)
        self.assertEqual(frame.sequence, request["request_id"])
        self.assertEqual(frame.axes, (-1., 1., -.5, .5, 0., 1.))
        self.assertGreater(frame.received_s, 0)
        self.assertIsNone(self.bridge.snapshot(frame.received_s + .101))
        self.assertIsNone(self.bridge.snapshot())

    def test_disconnect_clears_frame_and_new_client_gets_new_request_id(self):
        first = self.connect()
        old_request = self.answer(first)
        self.wait_for(self.bridge.snapshot)
        first.close()
        self.wait_for(lambda: self.bridge.last_error)
        self.assertIsNone(self.bridge.snapshot())
        second = self.connect()
        new_request = self.answer(second)
        self.assertGreater(new_request["request_id"], old_request["request_id"])
        self.assertEqual(self.wait_for(self.bridge.snapshot).sequence, new_request["request_id"])

    def test_connected_false_clears_previous_input(self):
        connection = self.connect()
        self.answer(connection)
        self.wait_for(self.bridge.snapshot)
        self.answer(connection, connected=False)
        self.wait_for(lambda: self.bridge.last_error == "gamepad disconnected")
        self.assertIsNone(self.bridge.snapshot())

    def test_missing_reply_expires_input_without_blocking_snapshot(self):
        connection = self.connect()
        self.answer(connection)
        self.wait_for(self.bridge.snapshot)
        self.request(connection)
        started = time.monotonic()
        self.bridge.snapshot()
        self.assertLess(time.monotonic() - started, .03)
        self.wait_for(lambda: self.bridge.last_error)
        self.assertIsNone(self.bridge.snapshot())

    def test_wrong_request_echo_is_rejected(self):
        connection = self.connect()
        request = self.request(connection)
        connection.sendall(response(request["request_id"] + 1))
        self.wait_for(lambda: self.bridge.last_error)
        self.assertIn("request_id", self.bridge.last_error)
        self.assertIsNone(self.bridge.snapshot())

    def test_oversized_and_queued_responses_are_rejected(self):
        for payload in (b"x" * 4097, response() + response()):
            with self.subTest(payload=payload[:15]):
                connection = self.connect()
                self.request(connection)
                connection.sendall(payload)
                self.wait_for(lambda: self.bridge.last_error)
                self.assertIsNone(self.bridge.snapshot())
                connection.close()
                self.bridge.close()
                self.bridge = InputBridge(port=0, timeout_s=.1, poll_hz=100)
                self.bridge.start()

    def test_slow_partial_reply_is_rejected_even_if_valid_when_completed(self):
        connection = self.connect()
        request = self.request(connection)
        line = response(request["request_id"])
        connection.sendall(line[:10])
        time.sleep(.12)
        self.wait_for(lambda: self.bridge.last_error)
        self.assertIsNone(self.bridge.snapshot())

    def test_close_clears_input_and_finishes_worker(self):
        connection = self.connect()
        self.answer(connection)
        self.wait_for(self.bridge.snapshot)
        self.bridge.close()
        self.assertIsNone(self.bridge.snapshot())
        self.assertFalse(self.bridge._thread.is_alive())


class WindowsClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "scripts" / "gamepad_windows.py"
        spec = importlib.util.spec_from_file_location("gamepad_windows_test", path)
        cls.client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.client)

    def test_synthetic_episode_is_labelled_and_starts_neutral(self):
        expected = [(0., [], 0., 0.), (.6, ["A"], 0., 0.),
                    (1.5, ["LB"], .35, 0.), (2.2, [], 0., 0.),
                    (2.5, ["RB"], 0., 0.), (3.2, ["LB"], 0., .25),
                    (4.3, ["B"], 0., 0.), (5., [], 0., 0.)]
        for elapsed, buttons, lx, ry in expected:
            with self.subTest(elapsed=elapsed):
                frame = self.client.synthetic_input(elapsed)
                self.assertTrue(frame["synthetic"])
                self.assertEqual(frame["buttons"], buttons)
                self.assertEqual(frame["axes"][0], lx)
                self.assertEqual(frame["axes"][3], ry)

    def test_only_loopback_targets_are_accepted(self):
        self.assertEqual(self.client.loopback_target("localhost:8765"), ("127.0.0.1", 8765))
        for target in ("0.0.0.0:8765", "example.org:8765", "127.0.0.1:0"):
            with self.subTest(target=target), self.assertRaises(argparse.ArgumentTypeError):
                self.client.loopback_target(target)

    def test_xinput_normalization_and_disconnected_state_without_hardware(self):
        reader = self.client.XInputReader.__new__(self.client.XInputReader)
        reader.slot = 0

        def fake_state(slot, pointer):
            state = pointer._obj
            state.packet = 17
            state.gamepad.lx, state.gamepad.ly = -32768, 32767
            state.gamepad.rx, state.gamepad.ry = 0, 0
            state.gamepad.lt, state.gamepad.rt = 0, 255
            state.gamepad.buttons = 0x0100
            return 0

        reader.get_state = fake_state
        frame = reader.read()
        self.assertEqual(frame["axes"], [-1., 1., 0., 0., 0., 1.])
        self.assertEqual(frame["buttons"], ["LB"])
        self.assertEqual(frame["packet_number"], 17)
        self.assertFalse(frame["synthetic"])
        reader.get_state = lambda slot, pointer: 1167
        self.assertFalse(reader.read()["connected"])
        self.assertEqual(reader.read()["axes"], [0.] * 6)

    def test_client_reads_input_only_after_receiving_a_request_and_echoes_id(self):
        server, client = socket.socketpair()
        calls, errors = [], []

        def read_input():
            calls.append(time.monotonic())
            return self.client.synthetic_input(0.)

        def run_client():
            try:
                self.client.serve_polls(client, read_input, time.monotonic() + .2)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=run_client)
        thread.start()
        try:
            time.sleep(.02)
            self.assertEqual(calls, [])
            server.sendall(b'{"version":1,"request_id":7}\n')
            server.settimeout(.5)
            data = json.loads(server.recv(4096))
            self.assertEqual(data["request_id"], 7)
            self.assertTrue(data["synthetic"])
            self.assertEqual(len(calls), 1)
            thread.join(.5)
            self.assertEqual(errors, [])
        finally:
            server.close()
            client.close()
            thread.join(.5)

    def test_clean_server_eof_exits_normally_but_partial_request_is_rejected(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                server, client = socket.socketpair()
                try:
                    if partial:
                        server.sendall(b'{"version":1')
                    server.shutdown(socket.SHUT_WR)
                    if partial:
                        with self.assertRaisesRegex(ValueError, "partial request"):
                            self.client.serve_polls(client, lambda: None, None)
                    else:
                        self.client.serve_polls(client, lambda: None, None)
                finally:
                    server.close()
                    client.close()


if __name__ == "__main__":
    unittest.main()
