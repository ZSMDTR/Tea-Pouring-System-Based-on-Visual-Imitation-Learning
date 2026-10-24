import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rebot_tea.dataset_contract import IMAGE_KEY
from rebot_tea.gamepad_recording import EpisodeRecorder


class MemoryDataset:
    def __init__(self, **kwargs):
        self.root = kwargs["root"]
        self.root.mkdir(parents=True)
        self.frames, self.episodes = [], []
        self.finalized = False

    def add_frame(self, frame):
        self.frames.append(frame)

    def save_episode(self, parallel_encoding=False):
        self.episodes.append(self.frames)
        self.frames = []

    def clear_episode_buffer(self, delete_images=True):
        self.frames = []

    def finalize(self):
        self.finalized = True


def timing(at=10.0):
    return {"input_received_s": at, "observation_read_s": at + .01,
            "camera_host_s": at - .02, "command_sent_s": at + .03}


class EpisodeRecorderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "demonstrations"
        self.image = np.full((16, 16, 3), 100, dtype=np.uint8)
        self.recorder = EpisodeRecorder(self.root, image_shape=self.image.shape,
                                        dataset_factory=MemoryDataset)
        self.addCleanup(self.recorder.close)

    def add_frame(self, at=10.0, **kwargs):
        self.recorder.add_frame(self.image, [0.] * 7, [0.] * 7, kwargs.get("timing", timing(at)))

    def sidecar(self, name):
        return [json.loads(line) for line in (self.root / "recording" / name).read_text().splitlines()]

    def test_units_actual_target_and_measured_timing_are_preserved(self):
        self.recorder.start()
        self.recorder.add_frame(self.image, [math.pi / 2] * 7, [4.] * 7, timing())
        self.assertTrue(self.recorder.is_recording)
        self.assertEqual(self.recorder.frames_in_episode, 1)
        frame = self.recorder.dataset.frames[0]
        np.testing.assert_allclose(frame["observation.state"], [90.] * 7)
        np.testing.assert_allclose(frame["action"], [math.degrees(4.)] * 7)
        self.add_frame(10.2)
        self.assertEqual(self.recorder.finish("success"), 0)
        self.assertFalse(self.recorder.is_recording)
        self.assertEqual(self.recorder.frames_in_episode, 0)
        self.assertEqual(self.recorder.episodes_saved, 1)
        rows = self.sidecar("frames.jsonl")
        self.assertEqual(rows[1]["timestamp"], .05)
        self.assertAlmostEqual(rows[1]["command_sent_s"] - rows[0]["command_sent_s"], .2)
        self.assertEqual(rows[0]["camera_host_s"], timing()["camera_host_s"])
        self.assertFalse(rows[0]["physical_task_verified"])
        manifest = json.loads((self.root / "recording/manifest.json").read_text())
        self.assertEqual(manifest["position_units"], "public_joint_degrees")
        self.assertFalse(manifest["hard_realtime_synchronized"])

    def test_discard_keeps_saved_episodes_and_does_not_consume_an_index(self):
        self.recorder.start()
        self.add_frame()
        self.assertEqual(self.recorder.finish("success"), 0)
        saved = (self.root / "recording/frames.jsonl").read_bytes()
        self.recorder.start()
        self.add_frame(20.)
        self.recorder.discard()
        self.assertEqual((self.root / "recording/frames.jsonl").read_bytes(), saved)
        self.recorder.start()
        self.add_frame(30.)
        self.assertEqual(self.recorder.finish("failure"), 1)
        self.assertEqual([row["outcome"] for row in self.sidecar("episodes.jsonl")],
                         ["success", "failure"])
        self.assertEqual(len(self.recorder.dataset.episodes), 2)

    def test_close_discards_unfinished_work_and_is_idempotent(self):
        self.recorder.start()
        self.add_frame()
        self.recorder.close()
        self.recorder.close()
        self.assertEqual(self.recorder.dataset.frames, [])
        self.assertEqual(self.recorder.dataset.episodes, [])
        self.assertTrue(self.recorder.dataset.finalized)
        self.assertFalse((self.root / "recording/episodes.jsonl").exists())
        manifest = json.loads((self.root / "recording/manifest.json").read_text())
        self.assertEqual(manifest["status"], "empty")
        self.assertEqual(manifest["episodes_saved"], 0)
        with self.assertRaises(RuntimeError):
            self.recorder.start()

    def test_existing_root_is_preserved(self):
        sentinel = self.root / "keep.txt"
        sentinel.write_text("unchanged")
        with self.assertRaises(FileExistsError):
            EpisodeRecorder(self.root, dataset_factory=MemoryDataset)
        self.assertEqual(sentinel.read_text(), "unchanged")

    def test_state_transitions_and_invalid_outcome(self):
        with self.assertRaises(RuntimeError):
            self.add_frame()
        self.recorder.start()
        with self.assertRaises(RuntimeError):
            self.recorder.start()
        self.assertIsNone(self.recorder.finish("success"))
        self.recorder.start()
        self.add_frame()
        with self.assertRaises(ValueError):
            self.recorder.finish("unknown")
        self.assertEqual(self.recorder.finish("failure"), 0)

    def test_bad_vectors_and_images_do_not_enter_native_buffer(self):
        self.recorder.start()
        for bad in (None, [0.] * 6, [math.nan] * 7, [True] * 7, [1e308] * 7):
            for argument in ("state", "action"):
                with self.subTest(bad=bad, argument=argument), self.assertRaises(ValueError):
                    self.recorder.add_frame(self.image, bad if argument == "state" else [0.] * 7,
                                            bad if argument == "action" else [0.] * 7, timing())
        for image in (np.zeros((8, 8, 3), dtype=np.uint8), self.image.astype(np.float32)):
            with self.assertRaises(ValueError):
                self.recorder.add_frame(image, [0.] * 7, [0.] * 7, timing())
        self.assertEqual(self.recorder.dataset.frames, [])

    def test_invalid_missing_and_reversed_timing_is_rejected(self):
        self.recorder.start()
        invalid = [None, {}, {**timing(), "command_sent_s": 9.},
                   {**timing(), "observation_read_s": 11.}, {**timing(), "camera_host_s": 11.}]
        for key in timing():
            invalid.extend({**timing(), key: value} for value in (math.inf, math.nan, True, -1.))
        for bad in invalid:
            with self.subTest(timing=bad), self.assertRaises(ValueError):
                self.add_frame(timing=bad)
        self.add_frame()
        for bad in (timing(), timing(9.), {**timing(11.), "camera_host_s": 9.98}):
            with self.subTest(timing=bad), self.assertRaises(ValueError):
                self.add_frame(timing=bad)
        self.assertEqual(len(self.recorder.dataset.frames), 1)


@unittest.skipUnless(importlib.util.find_spec("lerobot"), "Run in .venv-training")
class NativeEpisodeRecorderTests(unittest.TestCase):
    def test_empty_recording_closes_with_explicit_empty_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "empty"
            recorder = EpisodeRecorder(root)
            recorder.close()
            manifest = json.loads((root / "recording/manifest.json").read_text())
            self.assertEqual(manifest["status"], "empty")
            self.assertEqual(manifest["episodes_saved"], 0)
            self.assertTrue(root.is_dir())

    def test_video_roundtrip_discard_and_failure_label(self):
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recorded"
            image = np.full((32, 32, 3), (60, 120, 180), dtype=np.uint8)
            recorder = EpisodeRecorder(root, image_shape=image.shape)
            try:
                recorder.start()
                for index in range(4):
                    recorder.add_frame(image, [.1] * 7, [.2] * 7, timing(10. + index * .05))
                self.assertEqual(recorder.finish("success"), 0)
                recorder.start()
                recorder.add_frame(image, [9.] * 7, [9.] * 7, timing(20.))
                recorder.discard()
                recorder.start()
                for index in range(4):
                    recorder.add_frame(image, [.3] * 7, [.4] * 7, timing(30. + index * .08))
                self.assertEqual(recorder.finish("failure"), 1)
                recorder.start()
                recorder.add_frame(image, [8.] * 7, [8.] * 7, timing(40.))
            finally:
                recorder.close()
            dataset = LeRobotDataset("local/gamepad-demonstrations", root=root, video_backend="pyav")
            self.assertEqual(dataset.num_episodes, 2)
            self.assertEqual(len(dataset), 8)
            for index in (0, 3, 4, 7):
                frame = dataset[index]
                self.assertEqual(tuple(frame[IMAGE_KEY].shape), (3, 32, 32))
                np.testing.assert_allclose(frame[IMAGE_KEY].mean(dim=(1, 2)).numpy(),
                                           np.asarray((60, 120, 180)) / 255., atol=.03)
                self.assertAlmostEqual(float(frame["timestamp"]), (index % 4) / 20.)
                np.testing.assert_allclose(frame["observation.state"].numpy(),
                                           [math.degrees(.1 if index < 4 else .3)] * 7, rtol=1e-6)
                np.testing.assert_allclose(frame["action"].numpy(),
                                           [math.degrees(.2 if index < 4 else .4)] * 7, rtol=1e-6)
            self.assertTrue(list(root.glob("videos/**/*.mp4")))
            rows = [json.loads(line) for line in (root / "recording/frames.jsonl").read_text().splitlines()]
            self.assertEqual([(row["episode_index"], row["frame_index"]) for row in rows],
                             [(episode, index) for episode in range(2) for index in range(4)])
            episodes = [json.loads(line) for line in (root / "recording/episodes.jsonl").read_text().splitlines()]
            self.assertEqual([row["outcome"] for row in episodes], ["success", "failure"])
            self.assertTrue(all(row["physical_task_verified"] is False for row in episodes))
            manifest = json.loads((root / "recording/manifest.json").read_text())
            self.assertEqual(manifest["episodes_saved"], 2)
            self.assertEqual(manifest["status"], "saved_episodes")
