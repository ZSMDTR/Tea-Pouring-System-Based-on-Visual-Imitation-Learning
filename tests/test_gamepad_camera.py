import importlib.util
import threading
import unittest
from dataclasses import replace

import numpy as np

from tests.test_orbbec_integration import QueueSource


@unittest.skipUnless(importlib.util.find_spec("lerobot"), "Run in .venv-training")
class GamepadCameraTests(unittest.TestCase):
    def setUp(self):
        from rebot_tea.lerobot_orbbec import Gemini2Camera, Gemini2CameraConfig
        self.camera = Gemini2Camera(Gemini2CameraConfig(width=4, height=2),
                                   source_factory=QueueSource, clock=lambda: 20.)
        self.camera.connect()
        with self.camera._condition:
            self.camera._packet = replace(self.camera._packet, host_monotonic_s=20.)

    def tearDown(self):
        if self.camera.is_connected:
            self.camera._source.queue.put(TimeoutError("test teardown"))
            self.camera.disconnect()

    def test_latest_metadata_matches_rgb_copy_and_paired_depth(self):
        # Consuming the frame must not make this latest-frame read wait for new input.
        self.camera.async_read()
        image, metadata = self.camera.read_latest_with_metadata()
        self.assertEqual(metadata, {"camera_host_s": 20., "camera_frame_index": 1,
                                    "camera_timestamp_us": 1, "depth_timestamp_us": 1})
        image[:] = 0
        self.assertTrue(np.all(self.camera._packet.rgb == 10))
        with self.camera._condition:
            self.camera._packet = replace(self.camera._packet,
                                          depth=np.full((2, 4), 200, dtype=np.uint16))
        self.assertEqual(self.camera.read_latest_depth()[0, 0, 0], 100)

    def test_read_and_metadata_obey_the_same_packet_lock(self):
        entered, finished = threading.Event(), threading.Event()
        results = []

        def read():
            entered.set()
            results.append(self.camera.read_latest_with_metadata())
            finished.set()

        with self.camera._condition:
            thread = threading.Thread(target=read)
            thread.start()
            self.assertTrue(entered.wait(1.))
            self.assertFalse(finished.wait(.02))
            self.camera._packet = replace(
                self.camera._packet, rgb=np.full((2, 4, 3), 30, dtype=np.uint8),
                color_frame_index=3, color_timestamp_us=3000, depth_timestamp_us=3001,
            )
        thread.join(1.)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        image, metadata = results[0]
        self.assertTrue(np.all(image == 30))
        self.assertEqual(metadata["camera_frame_index"], 3)
        self.assertEqual(metadata["camera_timestamp_us"], 3000)
        self.assertEqual(metadata["depth_timestamp_us"], 3001)

    def test_age_validation_preserves_existing_latest_default(self):
        with self.camera._condition:
            self.camera._packet = replace(self.camera._packet, host_monotonic_s=19.75)
        self.camera.read_latest()  # Existing API still allows 500 ms by default.
        with self.assertRaises(TimeoutError):
            self.camera.read_latest_with_metadata()
        for limit in (0, -1, True, float("nan")):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.camera.read_latest_with_metadata(max_age_ms=limit)
        with self.camera._condition:
            self.camera._packet = replace(self.camera._packet, host_monotonic_s=20.1)
        with self.assertRaises(TimeoutError):
            self.camera.read_latest_with_metadata()

    def test_acquisition_failure_and_disconnect_do_not_return_old_metadata(self):
        with self.camera._condition:
            self.camera._error = OSError("USB disconnected")
        with self.assertRaisesRegex(RuntimeError, "USB disconnected"):
            self.camera.read_latest_with_metadata()
        self.camera._source.queue.put(TimeoutError("stop"))
        self.camera.disconnect()
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            self.camera.read_latest_with_metadata()
