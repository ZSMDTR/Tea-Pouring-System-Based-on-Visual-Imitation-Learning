#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


LEROBOT_HOME = Path(
    os.environ.get(
        "LEROBOT_HOME",
        Path.home() / ".cache/huggingface/lerobot",
    )
)

DEFAULT_V3_DATA_PATH = (
    "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
)

DEFAULT_V3_VIDEO_PATH = (
    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
)

V21_DATA_PATH = (
    "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet"
)

V21_VIDEO_PATH = (
    "videos/chunk-{episode_chunk:03d}/{video_key}/"
    "episode_{episode_index:06d}.mp4"
)


# ============================================================
# Utilities
# ============================================================


def jsonable(x):
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())

    if isinstance(x, np.generic):
        return jsonable(x.item())

    if isinstance(x, pd.Series):
        return jsonable(x.tolist())

    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]

    if isinstance(x, dict):
        return {
            str(k): jsonable(v)
            for k, v in x.items()
        }

    if x is pd.NA:
        return None

    try:
        if pd.isna(x):
            return None
    except Exception:
        pass

    return x


def resolve_repo(spec: str) -> Path:
    p = Path(spec).expanduser()

    # Explicit filesystem path.
    if p.is_absolute():
        return p.resolve()

    # Existing relative filesystem path.
    if p.exists():
        return p.resolve()

    # LeRobot repo id such as local/foo.
    return (LEROBOT_HOME / p).resolve()


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            block = f.read(1024 * 1024)

            if not block:
                break

            h.update(block)

    return h.hexdigest()


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(
            jsonable(obj),
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w") as f:
        for row in rows:
            f.write(
                json.dumps(
                    jsonable(row),
                    ensure_ascii=False,
                )
                + "\n"
            )


def run(cmd):
    print("  $", " ".join(str(x) for x in cmd))

    subprocess.run(
        [str(x) for x in cmd],
        check=True,
    )


def require_program(name: str):
    if shutil.which(name) is None:
        raise RuntimeError(
            f"Required program not found: {name}"
        )


def remove_huggingface_schema_metadata(
    table: pa.Table,
) -> pa.Table:
    metadata = dict(table.schema.metadata or {})

    metadata.pop(b"huggingface", None)

    return table.replace_schema_metadata(
        metadata or None
    )


# ============================================================
# Source metadata
# ============================================================


def load_episode_metadata(src: Path) -> pd.DataFrame:
    files = sorted(
        (src / "meta/episodes").glob(
            "chunk-*/file-*.parquet"
        )
    )

    if not files:
        files = sorted(
            (src / "meta/episodes").rglob("*.parquet")
        )

    if not files:
        raise RuntimeError(
            "No v3 episode metadata parquet files found."
        )

    frames = [
        pd.read_parquet(path)
        for path in files
    ]

    df = pd.concat(
        frames,
        ignore_index=True,
    )

    if "episode_index" not in df.columns:
        raise RuntimeError(
            "Episode metadata has no episode_index column."
        )

    df = df.sort_values(
        "episode_index"
    ).reset_index(drop=True)

    return df


def load_tasks(src: Path):
    path = src / "meta/tasks.parquet"

    if not path.exists():
        raise RuntimeError(
            f"Missing {path}"
        )

    df = pd.read_parquet(path)

    rows = []

    # Normal representation:
    # task / task_index are both columns.
    if (
        "task" in df.columns
        and "task_index" in df.columns
    ):
        for _, row in df.iterrows():
            rows.append(
                {
                    "task_index": int(
                        row["task_index"]
                    ),
                    "task": str(row["task"]),
                }
            )

    # Seeed v3 representation observed in B601:
    # task text is DataFrame index,
    # task_index is a column.
    elif "task_index" in df.columns:
        for task, row in df.iterrows():
            rows.append(
                {
                    "task_index": int(
                        row["task_index"]
                    ),
                    "task": str(task),
                }
            )

    else:
        raise RuntimeError(
            "Unsupported tasks.parquet layout."
        )

    rows.sort(
        key=lambda x: x["task_index"]
    )

    return rows


# ============================================================
# Episode stats
# ============================================================


def episode_stats_from_row(
    row: pd.Series,
    allowed_features: set[str],
):
    out = {}

    for column, value in row.items():
        if not column.startswith("stats/"):
            continue

        rest = column[len("stats/"):]

        if "/" not in rest:
            continue

        feature, stat_name = rest.rsplit("/", 1)

        if feature not in allowed_features:
            continue

        out.setdefault(
            feature,
            {},
        )[stat_name] = jsonable(value)

    return out


# ============================================================
# Video handling
# ============================================================


def ffprobe_frame_count(path: Path) -> int:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
    )

    text = result.stdout.strip()

    if text in ("", "N/A"):
        raise RuntimeError(
            f"Could not count video frames: {path}"
        )

    return int(text)


def stream_copy_episode(
    src_video: Path,
    dst_video: Path,
    start_s: float,
    end_s: float,
):
    """
    Extract an episode from a v3 concatenated MP4 without
    re-encoding.

    LeRobot v3 creates consolidated MP4s by concatenating
    independently encoded episode videos, so episode
    boundaries should normally be keyframe-safe.
    """

    duration_s = end_s - start_s

    if duration_s <= 0:
        raise RuntimeError(
            f"Invalid video duration: "
            f"{start_s} -> {end_s}"
        )

    dst_video.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_s:.9f}",
            "-i",
            str(src_video),
            "-t",
            f"{duration_s:.9f}",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-y",
            str(dst_video),
        ]
    )


def reencode_episode_lossless_h264(
    src_video: Path,
    dst_video: Path,
    start_frame: int,
    num_frames: int,
    fps: float,
):
    """
    Frame-accurate fallback.

    This decodes only the requested episode frames and
    re-encodes them using H.264 lossless mode (CRF 0).

    We do NOT use this unless explicitly requested because
    packet-copy preserves the original encoded video better.
    """

    end_frame = start_frame + num_frames - 1

    dst_video.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    vf = (
        f"select=between(n\\,{start_frame}\\,{end_frame}),"
        f"setpts=N/({fps}*TB)"
    )

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src_video),
            "-vf",
            vf,
            "-frames:v",
            str(num_frames),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-y",
            str(dst_video),
        ]
    )


# ============================================================
# Conversion
# ============================================================


def convert(args):
    src = resolve_repo(args.src)

    if args.dst is None:
        dst = Path(
            str(src) + "_openpi"
        )
    else:
        dst = resolve_repo(args.dst)

    print(
        "===== B601 LeRobot v3 -> "
        "OpenPI LeRobot v2.1 ====="
    )
    print("SOURCE:", src)
    print("DEST  :", dst)

    if not src.exists():
        raise RuntimeError(
            f"Source dataset does not exist: {src}"
        )

    if src == dst:
        raise RuntimeError(
            "Source and destination must be different."
        )

    if dst.exists():
        if not args.overwrite:
            raise RuntimeError(
                f"Destination already exists:\n"
                f"{dst}\n\n"
                f"Use --overwrite only if you really "
                f"want to replace it."
            )

        print(
            "Removing existing destination:",
            dst,
        )
        shutil.rmtree(dst)

    require_program("ffmpeg")
    require_program("ffprobe")

    src_info_path = src / "meta/info.json"

    if not src_info_path.exists():
        raise RuntimeError(
            f"Missing {src_info_path}"
        )

    src_info = json.loads(
        src_info_path.read_text()
    )

    print("\n===== SOURCE =====")
    print(
        "codebase_version:",
        src_info.get("codebase_version"),
    )
    print(
        "robot_type:",
        src_info.get("robot_type"),
    )
    print(
        "fps:",
        src_info.get("fps"),
    )

    if not str(
        src_info.get("codebase_version", "")
    ).startswith("v3"):
        raise RuntimeError(
            "This converter expects a LeRobot v3 dataset."
        )

    fps = float(src_info["fps"])

    episodes = load_episode_metadata(src)

    episode_indices = [
        int(x)
        for x in episodes[
            "episode_index"
        ].tolist()
    ]

    expected_indices = list(
        range(len(episode_indices))
    )

    if episode_indices != expected_indices:
        raise RuntimeError(
            "This first production converter requires "
            "contiguous episode indices starting at 0.\n"
            f"Got: {episode_indices[:20]}..."
        )

    num_episodes = len(episodes)

    print("episodes:", num_episodes)

    lengths = [
        int(x)
        for x in episodes["length"].tolist()
    ]

    total_frames = int(sum(lengths))

    print("frames:", total_frames)


    # --------------------------------------------------------
    # Select video features
    # --------------------------------------------------------

    all_video_keys = [
        key
        for key, feature
        in src_info["features"].items()
        if feature.get("dtype") == "video"
    ]

    if args.video_key:
        selected_video_keys = list(
            args.video_key
        )

        unknown = (
            set(selected_video_keys)
            - set(all_video_keys)
        )

        if unknown:
            raise RuntimeError(
                f"Unknown video keys: {sorted(unknown)}"
            )

    elif args.include_depth:
        selected_video_keys = all_video_keys

    else:
        # Default B601 π0.5 export:
        # keep RGB cameras, exclude depth streams.
        selected_video_keys = [
            key
            for key in all_video_keys
            if "depth" not in key.lower()
        ]

    if not selected_video_keys:
        raise RuntimeError(
            "No video feature selected."
        )

    print(
        "selected video keys:",
        selected_video_keys,
    )

    dropped_video_keys = [
        key
        for key in all_video_keys
        if key not in selected_video_keys
    ]

    if dropped_video_keys:
        print(
            "dropped video keys:",
            dropped_video_keys,
        )


    # --------------------------------------------------------
    # Output features
    # --------------------------------------------------------

    out_features = {}

    for key, value in src_info[
        "features"
    ].items():

        if value.get("dtype") == "video":
            if key not in selected_video_keys:
                continue

        out_features[key] = copy.deepcopy(
            value
        )

    if args.video_mode == "reencode-h264":
        for key in selected_video_keys:
            info = out_features[
                key
            ].setdefault("info", {})

            info["video.codec"] = "h264"
            info["video.pix_fmt"] = "yuv420p"


    # --------------------------------------------------------
    # v2.1 metadata
    # --------------------------------------------------------

    tasks = load_tasks(src)

    chunks_size = int(
        src_info.get("chunks_size", 1000)
    )

    if chunks_size <= 0:
        chunks_size = 1000

    total_chunks = max(
        1,
        math.ceil(
            num_episodes / chunks_size
        ),
    )

    out_info = {
        "codebase_version": "v2.1",
        "robot_type": src_info.get(
            "robot_type"
        ),
        "total_episodes": num_episodes,
        "total_frames": total_frames,
        "total_tasks": len(tasks),

        # v2.1 has one MP4 per episode per camera.
        "total_videos": (
            num_episodes
            * len(selected_video_keys)
        ),

        "total_chunks": total_chunks,
        "chunks_size": chunks_size,
        "fps": src_info["fps"],
        "splits": {
            "train": f"0:{num_episodes}"
        },
        "data_path": V21_DATA_PATH,
        "video_path": V21_VIDEO_PATH,
        "features": out_features,
    }

    (dst / "meta").mkdir(
        parents=True,
        exist_ok=True,
    )

    write_json(
        dst / "meta/info.json",
        out_info,
    )

    write_jsonl(
        dst / "meta/tasks.jsonl",
        tasks,
    )


    # --------------------------------------------------------
    # episodes.jsonl + episodes_stats.jsonl
    # --------------------------------------------------------

    episode_rows = []
    episode_stats_rows = []

    allowed_features = set(
        out_features.keys()
    )

    for _, row in episodes.iterrows():
        ep_idx = int(
            row["episode_index"]
        )
        length = int(row["length"])

        ep_tasks = jsonable(
            row.get("tasks", [])
        )

        if isinstance(ep_tasks, str):
            ep_tasks = [ep_tasks]

        if ep_tasks is None:
            ep_tasks = []

        episode_rows.append(
            {
                "episode_index": ep_idx,
                "tasks": ep_tasks,
                "length": length,
            }
        )

        stats = episode_stats_from_row(
            row,
            allowed_features,
        )

        episode_stats_rows.append(
            {
                "episode_index": ep_idx,
                "stats": stats,
            }
        )

    write_jsonl(
        dst / "meta/episodes.jsonl",
        episode_rows,
    )

    write_jsonl(
        dst / "meta/episodes_stats.jsonl",
        episode_stats_rows,
    )


    # --------------------------------------------------------
    # Global stats
    # --------------------------------------------------------

    src_stats_path = src / "meta/stats.json"

    if src_stats_path.exists():
        src_stats = json.loads(
            src_stats_path.read_text()
        )

        out_stats = {
            key: value
            for key, value
            in src_stats.items()
            if key in allowed_features
        }

        write_json(
            dst / "meta/stats.json",
            out_stats,
        )


    # --------------------------------------------------------
    # Parquet splitting
    # --------------------------------------------------------

    print(
        "\n===== SPLIT DATA PARQUETS ====="
    )

    data_pattern = src_info.get(
        "data_path",
        DEFAULT_V3_DATA_PATH,
    )

    parquet_pass = 0

    grouped_data = episodes.groupby(
        [
            "data/chunk_index",
            "data/file_index",
        ],
        sort=True,
    )

    for (
        src_chunk,
        src_file,
    ), group in grouped_data:

        src_chunk = int(src_chunk)
        src_file = int(src_file)

        src_data_path = (
            src
            / data_pattern.format(
                chunk_index=src_chunk,
                file_index=src_file,
            )
        )

        if not src_data_path.exists():
            raise RuntimeError(
                f"Missing source parquet:\n"
                f"{src_data_path}"
            )

        print(
            f"\nsource parquet: "
            f"{src_data_path.relative_to(src)}"
        )

        table = pq.read_table(
            src_data_path
        )

        if "episode_index" not in table.column_names:
            raise RuntimeError(
                f"No episode_index in "
                f"{src_data_path}"
            )

        for _, ep_row in group.iterrows():
            ep_idx = int(
                ep_row["episode_index"]
            )
            expected_length = int(
                ep_row["length"]
            )

            mask = pc.equal(
                table["episode_index"],
                ep_idx,
            )

            ep_table = table.filter(mask)

            if ep_table.num_rows != expected_length:
                raise RuntimeError(
                    f"Episode {ep_idx}: "
                    f"expected {expected_length} rows, "
                    f"got {ep_table.num_rows}"
                )

            # Verify frame_index = 0..length-1.
            frame_index = np.asarray(
                ep_table[
                    "frame_index"
                ].combine_chunks()
            )

            expected_frame_index = np.arange(
                expected_length,
                dtype=frame_index.dtype,
            )

            if not np.array_equal(
                frame_index,
                expected_frame_index,
            ):
                raise RuntimeError(
                    f"Episode {ep_idx}: invalid "
                    f"frame_index sequence."
                )

            # Remove new Hugging Face datasets schema
            # metadata (contains Feature type "List" which
            # OpenPI's pinned datasets version cannot parse).
            ep_table = (
                remove_huggingface_schema_metadata(
                    ep_table
                )
            )

            episode_chunk = (
                ep_idx // chunks_size
            )

            dst_data_path = (
                dst
                / V21_DATA_PATH.format(
                    episode_chunk=episode_chunk,
                    episode_index=ep_idx,
                )
            )

            dst_data_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            pq.write_table(
                ep_table,
                dst_data_path,
            )

            # Read it back and verify all Arrow values.
            check_table = pq.read_table(
                dst_data_path
            )

            check_table = (
                remove_huggingface_schema_metadata(
                    check_table
                )
            )

            if not ep_table.equals(
                check_table,
                check_metadata=False,
            ):
                raise RuntimeError(
                    f"Episode {ep_idx}: "
                    f"parquet verification failed."
                )

            parquet_pass += 1

            print(
                f"  episode {ep_idx:06d}: "
                f"{expected_length:5d} frames "
                f"PASS"
            )

    if parquet_pass != num_episodes:
        raise RuntimeError(
            "Not all episodes were written."
        )


    # --------------------------------------------------------
    # Video splitting
    # --------------------------------------------------------

    print(
        "\n===== SPLIT VIDEOS ====="
    )

    video_pattern = src_info.get(
        "video_path",
        DEFAULT_V3_VIDEO_PATH,
    )

    video_pass = 0

    for video_key in selected_video_keys:

        print(
            f"\n===== {video_key} ====="
        )

        chunk_col = (
            f"videos/{video_key}/chunk_index"
        )
        file_col = (
            f"videos/{video_key}/file_index"
        )
        from_col = (
            f"videos/{video_key}/from_timestamp"
        )
        to_col = (
            f"videos/{video_key}/to_timestamp"
        )

        required = [
            chunk_col,
            file_col,
            from_col,
            to_col,
        ]

        missing = [
            col
            for col in required
            if col not in episodes.columns
        ]

        if missing:
            raise RuntimeError(
                f"Missing video metadata columns "
                f"for {video_key}: {missing}"
            )

        groups = episodes.groupby(
            [
                chunk_col,
                file_col,
            ],
            sort=True,
        )

        for (
            src_chunk,
            src_file,
        ), group in groups:

            src_chunk = int(src_chunk)
            src_file = int(src_file)

            src_video_path = (
                src
                / video_pattern.format(
                    video_key=video_key,
                    chunk_index=src_chunk,
                    file_index=src_file,
                )
            )

            if not src_video_path.exists():
                raise RuntimeError(
                    f"Missing source video:\n"
                    f"{src_video_path}"
                )

            group = group.sort_values(
                from_col
            )

            # Frame-based local offsets are robust to
            # timestamp accumulation drift.
            local_start_frame = 0

            for _, ep_row in group.iterrows():

                ep_idx = int(
                    ep_row["episode_index"]
                )
                expected_frames = int(
                    ep_row["length"]
                )

                start_s = float(
                    ep_row[from_col]
                )
                end_s = float(
                    ep_row[to_col]
                )

                episode_chunk = (
                    ep_idx // chunks_size
                )

                dst_video_path = (
                    dst
                    / V21_VIDEO_PATH.format(
                        episode_chunk=episode_chunk,
                        video_key=video_key,
                        episode_index=ep_idx,
                    )
                )

                dst_video_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                if args.video_mode == "stream-copy":

                    # If this MP4 contains only one episode
                    # and starts at zero, copy the original
                    # file byte-for-byte.
                    if (
                        len(group) == 1
                        and abs(start_s) < 1e-6
                    ):
                        shutil.copy2(
                            src_video_path,
                            dst_video_path,
                        )

                        method = "file-copy"

                    else:
                        stream_copy_episode(
                            src_video_path,
                            dst_video_path,
                            start_s,
                            end_s,
                        )

                        method = "stream-copy"

                else:
                    reencode_episode_lossless_h264(
                        src_video_path,
                        dst_video_path,
                        start_frame=local_start_frame,
                        num_frames=expected_frames,
                        fps=fps,
                    )

                    method = "lossless-h264"

                actual_frames = (
                    ffprobe_frame_count(
                        dst_video_path
                    )
                )

                if (
                    actual_frames
                    != expected_frames
                ):
                    raise RuntimeError(
                        "\nVIDEO FRAME COUNT MISMATCH\n"
                        f"episode: {ep_idx}\n"
                        f"camera : {video_key}\n"
                        f"source : {src_video_path}\n"
                        f"method : {method}\n"
                        f"expected frames: "
                        f"{expected_frames}\n"
                        f"actual frames  : "
                        f"{actual_frames}\n\n"
                        "The source v3 video timestamps "
                        "cannot be safely stream-copied.\n"
                        "Re-run with:\n"
                        "  --video-mode reencode-h264\n"
                    )

                # Byte equality only makes sense when the
                # entire original MP4 was copied.
                if method == "file-copy":
                    src_hash = sha256(
                        src_video_path
                    )
                    dst_hash = sha256(
                        dst_video_path
                    )

                    if src_hash != dst_hash:
                        raise RuntimeError(
                            f"Episode {ep_idx}: "
                            f"video SHA256 mismatch."
                        )

                print(
                    f"  episode {ep_idx:06d}: "
                    f"{actual_frames:5d} frames "
                    f"{method} PASS"
                )

                local_start_frame += (
                    expected_frames
                )

                video_pass += 1


    expected_video_count = (
        num_episodes
        * len(selected_video_keys)
    )

    if video_pass != expected_video_count:
        raise RuntimeError(
            f"Expected {expected_video_count} "
            f"episode videos, wrote {video_pass}."
        )


    # --------------------------------------------------------
    # Final checks
    # --------------------------------------------------------

    print(
        "\n===== FINAL VERIFICATION ====="
    )

    out_parquets = list(
        (dst / "data").rglob(
            "episode_*.parquet"
        )
    )

    out_videos = list(
        (dst / "videos").rglob(
            "episode_*.mp4"
        )
    )

    print(
        "episodes:",
        num_episodes,
    )
    print(
        "frames:",
        total_frames,
    )
    print(
        "parquet files:",
        len(out_parquets),
    )
    print(
        "video files:",
        len(out_videos),
    )
    print(
        "video keys:",
        selected_video_keys,
    )

    assert (
        len(out_parquets)
        == num_episodes
    )

    assert (
        len(out_videos)
        == expected_video_count
    )

    print(
        "\nCONVERSION RESULT: PASS"
    )

    try:
        relative = dst.relative_to(
            LEROBOT_HOME
        )

        print(
            "Converted repo_id:",
            relative.as_posix(),
        )

    except ValueError:
        print(
            "Converted path:",
            dst,
        )


# ============================================================
# CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert a LeRobot v3 B601 dataset "
            "to OpenPI-compatible LeRobot v2.1."
        )
    )

    parser.add_argument(
        "--src",
        required=True,
        help=(
            "Source repo id such as local/my_dataset "
            "or an absolute filesystem path."
        ),
    )

    parser.add_argument(
        "--dst",
        default=None,
        help=(
            "Destination repo id/path. "
            "Default: <src>_openpi"
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Delete an existing destination first."
        ),
    )

    parser.add_argument(
        "--include-depth",
        action="store_true",
        help=(
            "Keep depth video features too. "
            "By default depth is excluded for "
            "the first B601 π0.5 pipeline."
        ),
    )

    parser.add_argument(
        "--video-key",
        action="append",
        default=None,
        help=(
            "Explicit video key to retain. "
            "Can be specified multiple times."
        ),
    )

    parser.add_argument(
        "--video-mode",
        choices=[
            "stream-copy",
            "reencode-h264",
        ],
        default="stream-copy",
        help=(
            "stream-copy preserves the original "
            "encoded frames and is preferred. "
            "reencode-h264 is a frame-accurate "
            "lossless fallback."
        ),
    )

    args = parser.parse_args()

    convert(args)


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "\nCONVERSION RESULT: FAIL",
            file=sys.stderr,
        )
        print(
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
