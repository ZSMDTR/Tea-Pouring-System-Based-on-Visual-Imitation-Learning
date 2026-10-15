import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from rebot_tea.orbbec import (
    Gemini2Settings, Gemini2Source, color_to_rgb, list_devices, save_packet,
)
from tests.orbbec_fakes import FakeSDK, Frame, Frames


class OrbbecTests(unittest.TestCase):
    def setUp(self):
        self.sdk = FakeSDK()
        self.settings = Gemini2Settings(width=4, height=2)

    def open(self):
        source = Gemini2Source(self.settings, self.sdk, clock=lambda: 5.0)
        source.connect()
        self.addCleanup(source.disconnect)
        return source

    def test_invalid_config_fails_without_sdk(self):
        for kwargs in ({"width": 0}, {"fps": True}, {"max_pair_skew_ms": math.nan},
                       {"serial_number": ""}, {"rectify": "true"}, {"color_format": "H264"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                replace(self.settings, **kwargs)

    def test_enumeration_reports_actual_profiles_and_does_not_stream(self):
        result = list_devices(self.sdk)
        self.assertEqual(result[0]["serial_number"], "SN0")
        self.assertEqual(result[0]["profiles"]["color"][0]["width"], 4)
        self.assertFalse(self.sdk.started)

    def test_single_device_required_and_wrong_model_rejected(self):
        for names in ((), ("Gemini 2 XL",), ("Gemini 2", "Gemini 2")):
            with self.subTest(names=names), self.assertRaisesRegex(RuntimeError, "Expected one"):
                Gemini2Source(self.settings, FakeSDK(names)).connect()
        source = Gemini2Source(replace(self.settings, serial_number="SN1"), FakeSDK(("Gemini 2",)*2))
        source.connect()
        self.assertEqual(source.info["serial_number"], "SN1")
        source.disconnect()

    def test_old_firmware_rejected_before_streaming(self):
        sdk = FakeSDK(firmware="1.4.76")
        with self.assertRaisesRegex(ValueError, "1.4.92"):
            Gemini2Source(self.settings, sdk).connect()
        self.assertFalse(sdk.started)

    def test_rgb_depth_scale_and_intrinsics_are_actual(self):
        packet = self.open().read_packet()
        self.assertTrue(self.sdk.sync)
        self.assertEqual(len(self.sdk.enabled), 2)
        np.testing.assert_array_equal(packet.rgb[0, 0], [10, 20, 30])
        self.assertEqual(packet.depth.dtype, np.uint16)
        self.assertAlmostEqual(packet.calibration["depth_scale_m"], 0.0001)
        self.assertEqual(packet.calibration["fx"], 10.)
        self.assertEqual(packet.host_monotonic_s, 5.)
        self.assertTrue(packet.calibration["rectified"])
        self.assertIsNone(packet.calibration["T_base_camera"])

    def test_missing_frames_skew_and_clock_reversal_fail(self):
        source = self.open()
        self.sdk.frames = None
        with self.assertRaises(TimeoutError):
            source.read_packet()
        self.sdk.frames = Frames(skew=50000)
        with self.assertRaisesRegex(ValueError, "skew"):
            source.read_packet()
        self.sdk.frames = Frames()
        source.read_packet()
        with self.assertRaisesRegex(ValueError, "Repeated or reversed"):
            source.read_packet()

    def test_invalid_depth_and_mismatched_alignment_fail(self):
        for problem in ("empty", "scale", "shape", "missing"):
            with self.subTest(problem=problem):
                sdk = FakeSDK()
                source = Gemini2Source(self.settings, sdk)
                source.connect()
                if problem == "empty":
                    sdk.frames.depth.data[:] = 0
                if problem == "scale":
                    sdk.frames.depth.scale = math.nan
                if problem == "shape":
                    sdk.frames.depth.data = np.ones((1, 2), dtype=np.uint16)
                if problem == "missing":
                    sdk.frames.color = None
                with self.assertRaises(ValueError):
                    source.read_packet()
                source.disconnect()

    def test_bgr_and_mjpg_convert_to_rgb(self):
        bgr = np.zeros((2, 4, 3), dtype=np.uint8)
        bgr[:] = (0, 0, 255)
        frame = Frame(bgr, 1, "BGR")
        np.testing.assert_array_equal(color_to_rgb(frame, self.sdk)[0, 0], [255, 0, 0])
        success, encoded = cv2.imencode(".jpg", bgr)
        self.assertTrue(success)
        frame.fmt = "MJPG"
        frame.get_data = lambda: encoded.tobytes()
        rgb = color_to_rgb(frame, self.sdk)
        self.assertGreater(int(rgb[0, 0, 0]), 240)
        self.assertLess(int(rgb[0, 0, 2]), 10)

    def test_export_preserves_raw_depth_and_unknown_base_transform(self):
        packet = self.open().read_packet()
        with tempfile.TemporaryDirectory() as tmp:
            scene = save_packet(packet, tmp)
            root = Path(tmp)
            np.testing.assert_array_equal(np.load(root/scene["depth"]), packet.depth)
            calibration = json.loads((root/scene["calibration"]).read_text())
            self.assertIsNone(calibration["T_base_camera"])
            self.assertFalse(scene["synthetic"])
            with self.assertRaises(FileExistsError):
                save_packet(packet, tmp)

    def test_disconnect_is_idempotent(self):
        source = self.open()
        source.disconnect()
        source.disconnect()
        self.assertEqual(self.sdk.stops, 1)
