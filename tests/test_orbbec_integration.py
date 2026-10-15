"""Run with scripts/camera.sh test; all device traffic is replaced by a queue."""
import importlib.util
import queue
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rebot_tea.orbbec import RGBDPacket


class QueueSource:
    def __init__(self, settings):
        self.queue = queue.Queue()
        self.closed = False
        self.queue.put(RGBDPacket(np.full((2, 4, 3), 10, dtype=np.uint8),
                                 np.full((2, 4), 100, dtype=np.uint16),
                                 1, 1, 1, 1, time.monotonic(), {}))

    def connect(self):
        pass

    def read_packet(self):
        item = self.queue.get(timeout=1.0)
        if isinstance(item, Exception):
            raise item
        return item

    def disconnect(self):
        self.closed = True


@unittest.skipUnless(importlib.util.find_spec("lerobot"), "Run bash scripts/camera.sh test")
class OrbbecIntegrationTests(unittest.TestCase):
    def setUp(self):
        from rebot_tea.lerobot_orbbec import Gemini2Camera, Gemini2CameraConfig
        self.config = Gemini2CameraConfig(width=4, height=2)
        self.camera = Gemini2Camera(self.config, source_factory=QueueSource)

    def tearDown(self):
        if self.camera.is_connected:
            self.camera._source.queue.put(TimeoutError("test teardown"))
            self.camera.disconnect()

    def test_registry_and_official_factory_work_without_connecting(self):
        import draccus
        from lerobot.cameras import make_cameras_from_configs
        from lerobot.cameras.configs import CameraConfig
        config = draccus.decode(CameraConfig, {"type": "orbbec_gemini2", "width": 4, "height": 2})
        with patch("rebot_tea.orbbec.load_sdk", side_effect=AssertionError("SDK must remain closed")):
            camera = make_cameras_from_configs({"scene": config})["scene"]
            self.assertFalse(camera.is_connected)

    def test_async_returns_new_frames_and_times_out_when_consumed(self):
        self.camera.connect()
        image = self.camera.async_read(timeout_ms=100)
        self.assertEqual(image.shape, (2, 4, 3))
        with self.assertRaises(TimeoutError):
            self.camera.async_read(timeout_ms=5)

    def test_latest_rejects_stale_frames(self):
        self.camera.connect()
        self.camera._packet = replace(self.camera._packet, host_monotonic_s=time.monotonic()-2)
        with self.assertRaises(TimeoutError):
            self.camera.read_latest(max_age_ms=100)

    def test_rgb_and_depth_remain_paired_across_background_updates(self):
        self.camera.connect()
        self.camera.read_latest()
        packet = replace(self.camera._packet, depth=np.full((2, 4), 200, dtype=np.uint16))
        with self.camera._condition:
            self.camera._packet = packet
        self.assertEqual(self.camera.read_latest_depth()[0, 0, 0], 100)
        self.camera.read_latest()
        self.assertEqual(self.camera.read_latest_depth()[0, 0, 0], 200)

    def test_worker_error_latches_and_never_returns_old_frames(self):
        self.camera.connect()
        self.camera._source.queue.put(OSError("disconnected USB"))
        with self.camera._condition:
            self.assertTrue(self.camera._condition.wait_for(lambda: self.camera._error is not None, 1))
        with self.assertRaisesRegex(RuntimeError, "disconnected USB"):
            self.camera.read_latest()

    def test_disconnect_releases_device_and_prevents_reads(self):
        self.camera.connect()
        source = self.camera._source
        source.queue.put(TimeoutError("stop"))
        self.camera.disconnect()
        self.assertTrue(source.closed)
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            self.camera.read_latest()

    def test_plugin_rgb_can_be_saved_and_reloaded_by_native_lerobot_dataset(self):
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        self.camera.connect()
        with tempfile.TemporaryDirectory() as directory:
            dataset = LeRobotDataset.create(
                repo_id="local/gemini2-plugin-test", root=Path(directory)/"dataset",
                robot_type="rebot_b601_follower", fps=30, use_videos=False,
                features={
                    "observation.images.scene": {"dtype": "image", "shape": (2, 4, 3),
                                                 "names": ["height", "width", "channels"]},
                    "observation.state": {"dtype": "float32", "shape": (7,), "names": None},
                    "action": {"dtype": "float32", "shape": (7,), "names": None},
                },
            )
            dataset.add_frame({"observation.images.scene": self.camera.read_latest(),
                               "observation.state": np.zeros(7, dtype=np.float32),
                               "action": np.zeros(7, dtype=np.float32),
                               "task": "Synthetic camera plugin test; no physical demonstration"})
            dataset.save_episode()
            dataset.finalize()
            reloaded = LeRobotDataset("local/gemini2-plugin-test", root=Path(directory)/"dataset")
            self.assertEqual(tuple(reloaded[0]["observation.images.scene"].shape), (3, 2, 4))
            self.assertEqual(tuple(reloaded[0]["action"].shape), (7,))
