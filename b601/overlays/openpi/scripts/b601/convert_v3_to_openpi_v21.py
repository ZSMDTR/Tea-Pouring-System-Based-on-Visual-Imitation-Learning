#!/usr/bin/env python3

import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


SRC_NAME = "b601_rs_rgbd_motion_smoke4_20260824"
DST_NAME = SRC_NAME + "_openpi"

LEROBOT_HOME = Path.home() / ".cache/huggingface/lerobot"

SRC = LEROBOT_HOME / "local" / SRC_NAME
DST = LEROBOT_HOME / "local" / DST_NAME


def jsonable(x):
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    return x


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(jsonable(record), ensure_ascii=False) + "\n")


def sha256(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


print("===== B601 v3 -> OpenPI LeRobot v2.1 converter =====")
print("SOURCE:", SRC)
print("DEST  :", DST)

if not SRC.exists():
    raise FileNotFoundError(SRC)

# Never overwrite anything.
if DST.exists():
    raise FileExistsError(
        f"\nDestination already exists:\n{DST}\n"
        "Refusing to overwrite it."
    )


# ---------------------------------------------------------------------
# Read v3 metadata
# ---------------------------------------------------------------------

with (SRC / "meta/info.json").open(encoding="utf-8") as f:
    src_info = json.load(f)

episode_files = sorted((SRC / "meta/episodes").rglob("*.parquet"))

if len(episode_files) != 1:
    raise RuntimeError(
        "This first smoke-test converter intentionally supports exactly "
        f"one episode metadata file. Found {len(episode_files)}."
    )

episodes_df = pd.read_parquet(episode_files[0])

if len(episodes_df) != 1:
    raise RuntimeError(
        "This first smoke-test converter intentionally supports exactly "
        f"one episode. Found {len(episodes_df)}."
    )

ep = episodes_df.iloc[0]

episode_index = int(ep["episode_index"])
length = int(ep["length"])
dataset_from = int(ep["dataset_from_index"])
dataset_to = int(ep["dataset_to_index"])

if episode_index != 0:
    raise RuntimeError(f"Expected episode_index=0, got {episode_index}")

if dataset_from != 0 or dataset_to != length:
    raise RuntimeError(
        "Episode does not occupy the whole parquet file. "
        "Refusing to blindly copy it."
    )

if int(src_info["total_episodes"]) != 1:
    raise RuntimeError(
        f"Expected total_episodes=1, got {src_info['total_episodes']}"
    )

if int(src_info["total_frames"]) != length:
    raise RuntimeError(
        f"info total_frames={src_info['total_frames']} but episode length={length}"
    )


# ---------------------------------------------------------------------
# Create OpenPI/v2.1 directory structure
# ---------------------------------------------------------------------

(DST / "meta").mkdir(parents=True)
(DST / "data/chunk-000").mkdir(parents=True)


# ---------------------------------------------------------------------
# info.json
# ---------------------------------------------------------------------

video_keys = [
    key
    for key, feature in src_info["features"].items()
    if feature["dtype"] == "video"
]

dst_info = {
    "codebase_version": "v2.1",
    "robot_type": src_info["robot_type"],
    "total_episodes": 1,
    "total_frames": length,
    "total_tasks": int(src_info["total_tasks"]),
    "total_videos": len(video_keys),
    "total_chunks": 1,
    "chunks_size": int(src_info.get("chunks_size", 1000)),
    "fps": int(src_info["fps"]),
    "splits": {"train": "0:1"},
    "data_path": (
        "data/chunk-{episode_chunk:03d}/"
        "episode_{episode_index:06d}.parquet"
    ),
    "video_path": (
        "videos/chunk-{episode_chunk:03d}/"
        "{video_key}/episode_{episode_index:06d}.mp4"
    ),
    "features": src_info["features"],
}

with (DST / "meta/info.json").open("w", encoding="utf-8") as f:
    json.dump(dst_info, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------
# tasks.parquet -> tasks.jsonl
# ---------------------------------------------------------------------

tasks_df = pd.read_parquet(SRC / "meta/tasks.parquet")

task_records = []

if "task" in tasks_df.columns:
    for _, row in tasks_df.iterrows():
        task_records.append(
            {
                "task_index": int(row["task_index"]),
                "task": str(row["task"]),
            }
        )
else:
    # In this v3 dataset the task string is the DataFrame index.
    for task, row in tasks_df.iterrows():
        task_records.append(
            {
                "task_index": int(row["task_index"]),
                "task": str(task),
            }
        )

task_records.sort(key=lambda x: x["task_index"])

write_jsonl(DST / "meta/tasks.jsonl", task_records)


# ---------------------------------------------------------------------
# v3 episode metadata -> episodes.jsonl
# ---------------------------------------------------------------------

tasks = jsonable(ep["tasks"])

if isinstance(tasks, str):
    tasks = [tasks]

episode_record = {
    "episode_index": episode_index,
    "tasks": tasks,
    "length": length,
}

write_jsonl(
    DST / "meta/episodes.jsonl",
    [episode_record],
)


# ---------------------------------------------------------------------
# v3 flattened episode stats -> episodes_stats.jsonl
# ---------------------------------------------------------------------

episode_stats = {}

for column in episodes_df.columns:
    if not column.startswith("stats/"):
        continue

    # Example:
    # stats/observation.state/mean
    # -> feature = observation.state
    # -> stat    = mean
    rest = column[len("stats/") :]
    feature, stat = rest.rsplit("/", 1)

    episode_stats.setdefault(feature, {})[stat] = jsonable(ep[column])

write_jsonl(
    DST / "meta/episodes_stats.jsonl",
    [
        {
            "episode_index": episode_index,
            "stats": episode_stats,
        }
    ],
)


# Keep the global stats file as additional metadata.
src_stats = SRC / "meta/stats.json"
if src_stats.exists():
    shutil.copy2(src_stats, DST / "meta/stats.json")


# ---------------------------------------------------------------------
# Copy parquet byte-for-byte.
# ---------------------------------------------------------------------

src_data_rel = src_info["data_path"].format(
    chunk_index=int(ep["data/chunk_index"]),
    file_index=int(ep["data/file_index"]),
)

src_data = SRC / src_data_rel
dst_data = DST / "data/chunk-000/episode_000000.parquet"

shutil.copy2(src_data, dst_data)


# ---------------------------------------------------------------------
# Copy videos byte-for-byte.
#
# This is safe for THIS smoke dataset because each video file contains
# exactly the single episode starting at timestamp zero.
# ---------------------------------------------------------------------

video_pairs = []

for video_key in video_keys:
    prefix = f"videos/{video_key}"

    chunk_index = int(ep[f"{prefix}/chunk_index"])
    file_index = int(ep[f"{prefix}/file_index"])
    from_ts = float(ep[f"{prefix}/from_timestamp"])

    if abs(from_ts) > 1e-6:
        raise RuntimeError(
            f"{video_key}: from_timestamp={from_ts}, so the episode does "
            "not start at the beginning of the video. Refusing to copy."
        )

    src_video_rel = src_info["video_path"].format(
        video_key=video_key,
        chunk_index=chunk_index,
        file_index=file_index,
    )

    src_video = SRC / src_video_rel

    dst_video = (
        DST
        / "videos"
        / "chunk-000"
        / video_key
        / "episode_000000.mp4"
    )

    dst_video.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_video, dst_video)

    video_pairs.append((video_key, src_video, dst_video))


# ---------------------------------------------------------------------
# Byte-level verification
# ---------------------------------------------------------------------

print("\n===== BYTE-LEVEL VERIFICATION =====")

src_hash = sha256(src_data)
dst_hash = sha256(dst_data)

print("parquet:")
print("  src:", src_hash)
print("  dst:", dst_hash)

if src_hash != dst_hash:
    raise RuntimeError("Parquet SHA256 mismatch")

for key, src_video, dst_video in video_pairs:
    src_hash = sha256(src_video)
    dst_hash = sha256(dst_video)

    print(f"{key}:")
    print("  src:", src_hash)
    print("  dst:", dst_hash)

    if src_hash != dst_hash:
        raise RuntimeError(f"Video SHA256 mismatch: {key}")


# ---------------------------------------------------------------------
# Numeric verification
# ---------------------------------------------------------------------

print("\n===== NUMERIC VERIFICATION =====")

src_df = pd.read_parquet(src_data)
dst_df = pd.read_parquet(dst_data)

if len(src_df) != len(dst_df):
    raise RuntimeError("Frame count mismatch")

for key in [
    "action",
    "observation.state",
    "timestamp",
    "frame_index",
    "episode_index",
    "index",
    "task_index",
]:
    a = src_df[key].to_numpy()
    b = dst_df[key].to_numpy()

    if key in ("action", "observation.state"):
        a = np.stack(a)
        b = np.stack(b)

    equal = np.array_equal(a, b)

    print(f"{key:24s}: {'PASS' if equal else 'FAIL'}")

    if not equal:
        raise RuntimeError(f"Data mismatch: {key}")


print("\n===== OUTPUT TREE =====")

for path in sorted(DST.rglob("*")):
    if path.is_file():
        print(path.relative_to(DST))


print("\nCONVERSION RESULT: PASS")
print("Converted repo_id:")
print(f"local/{DST_NAME}")
