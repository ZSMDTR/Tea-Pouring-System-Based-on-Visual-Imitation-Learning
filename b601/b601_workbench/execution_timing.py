"""Workbench execution adapters; no motor, camera SDK, or ML imports.

Design references (independent implementation, no upstream code copied):
* LeRobot #2987: peek at a bounded-age camera buffer instead of awaiting a frame.
* LeRobot ActionInterpolator: interpolate policy-space actions before the driver.
* Diffusion Policy RTDEInterpolationController: sample trajectories by monotonic time.

For the interpolated players below, policy steps and command ticks are different
units; their RTC indices/delays use policy steps. The worker's separate native
mode uses RTCQueue directly, consumes one model row per command, and shares only
LatestFrameReader and LoopTiming with these interpolated players.
"""
import math
import time

import numpy as np

from rtc.runtime import RTCQueue


class LatestFrameReader:
    """Nonblocking view of the installed Orbbec producer's existing frame cache.

    SDK frame/sequence/arrival time are snapshotted under its own lock. Conversion
    happens once per new sequence, outside the lock. Never fall back to a blocking
    camera method; unsupported cameras must use the explicit legacy runner.
    """

    def __init__(self, camera, max_age_ms=250, clock=None, wall_clock=None):
        self.camera = camera
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.max_age = max_age_ms / 1000.0
        pipeline = getattr(camera, "pipeline", None)
        required = ("frame_lock", "frames_seq", "latest_frames", "last_frames_ts")
        if pipeline is None or any(not hasattr(pipeline, name) for name in required):
            raise ValueError("当前相机不支持最新帧缓存，请使用旧执行方式。")
        if not callable(getattr(camera, "_read_from_frames", None)):
            raise ValueError("当前相机缺少缓存画面转换接口，请使用旧执行方式。")
        self.pipeline = pipeline
        self.sequence = None
        self.image = None
        self.received_at = None
        self.age_ms = None
        self.max_age_ms = 0.0

    def latest(self):
        with self.pipeline.frame_lock:
            frames = self.pipeline.latest_frames
            sequence = self.pipeline.frames_seq
            arrival = self.pipeline.last_frames_ts
        if frames is None or arrival is None:
            raise RuntimeError("相机尚未提供缓存画面，请等待预览后重试。")
        if sequence != self.sequence:
            # The SDK uses wall time. Translate its arrival to monotonic time once
            # per sequence; later polls cannot make a frozen frame look fresh.
            age = max(0.0, self.wall_clock() - arrival)
            received_at = self.clock() - age
            if age > self.max_age:
                raise TimeoutError("相机缓存画面已过期，停止执行。")
            image = self.camera._read_from_frames(frames)
            if image is None:
                raise RuntimeError("相机缓存中没有有效 RGB 画面。")
            image = np.array(image, copy=True)
            if image.shape != (480, 640, 3) or image.dtype != np.uint8:
                raise ValueError("相机缓存 RGB 画面的尺寸或类型无效。")
            image.flags.writeable = False
            self.sequence, self.image, self.received_at = sequence, image, received_at
        self.age_ms = max(0.0, self.clock() - self.received_at) * 1000.0
        self.max_age_ms = max(self.max_age_ms, self.age_ms)
        if self.age_ms > self.max_age * 1000.0:
            raise TimeoutError("相机画面停止更新或已过期，停止执行。")
        return self.image

    def metrics(self):
        return {"camera_frame_age_ms": None if self.age_ms is None else round(self.age_ms, 1),
                "camera_max_frame_age_ms": round(self.max_age_ms, 1),
                "camera_frame_sequence": self.sequence}


def sample_actions(actions, positions):
    """Linear interpolation in the unchanged 7D policy coordinate convention."""
    positions = np.asarray(positions, dtype=np.float64)
    if not np.isfinite(positions).all() or np.any(positions < -1e-7) or np.any(positions > len(actions) - 1 + 1e-7):
        raise RuntimeError("动作时间轴已超出有效计划范围，停止执行。")
    positions = np.clip(positions, 0, len(actions) - 1)
    lower = np.floor(positions).astype(np.int64)
    upper = np.minimum(lower + 1, len(actions) - 1)
    weight = (positions - lower)[..., None]
    result = (actions[lower] + weight * (actions[upper] - actions[lower])).astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("插值动作包含无效数值，停止执行。")
    return result


class TimedSyncPlayer:
    """Keep baseline synchronous chunk calls, with a separate policy timebase.

    The final target is held for its last policy interval. Model calls still
    block as in the baseline; the new chunk's clock starts after that call.
    """

    def __init__(self, policy, policy_hz, chunk_size, clock=None):
        self.policy, self.hz, self.chunk_size = policy, policy_hz, chunk_size
        self.clock = clock or time.monotonic
        self.chunk = None
        self.origin = None
        self.infer_ms = None

    def action_at(self, obs, reason):
        now = self.clock()
        if self.chunk is None or (now - self.origin) * self.hz >= len(self.chunk) - 1e-7:
            started = self.clock()
            reply = self.policy.infer(obs, reason)
            actions = np.asarray(reply["actions"], dtype=np.float32)
            if actions.ndim != 2 or actions.shape[1] != 7 or len(actions) < self.chunk_size or not np.isfinite(actions).all():
                raise ValueError("策略返回了无效动作，已拒绝发送。")
            self.chunk = actions[:self.chunk_size].copy()
            self.origin = self.clock()
            self.infer_ms = (self.origin - started) * 1000
            now = self.origin
        position = min(max(0.0, (now - self.origin) * self.hz), len(self.chunk) - 1)
        return sample_actions(self.chunk, position)

    def metrics(self):
        return {"infer_ms": None if self.infer_ms is None else round(self.infer_ms, 1)}


class TimedRTCQueue(RTCQueue):
    """PI RTC scheduling on a continuous policy-time trajectory.

    The inherited legacy queue is untouched. Here inference is anchored to its
    observation/query time, not its completion time. Returning chunks are sampled
    at the elapsed policy position, including its fractional part. Command count
    is never used to estimate delay. A late control tick samples the current
    position once instead of bursting obsolete commands.
    """

    def __init__(self, policy, config, policy_hz, executor=None, clock=None):
        super().__init__(policy, config, executor)
        self.hz = policy_hz
        self.clock = clock or time.monotonic
        self.origin = None
        self.query_time = None
        self.position = 0.0

    def action_at(self, obs, reason):
        if self.closed:
            raise RuntimeError("RTC 队列已关闭。")
        if self.chunk is None:
            # Preserve initial inference/cancellation semantics of the existing queue.
            super().next_action(obs, reason)
            self.origin = self.clock()
        now = self.clock()
        position = max(0.0, (now - self.origin) * self.hz)
        if position > len(self.chunk) - 1 + 1e-7:
            raise RuntimeError("RTC 旧动作计划已耗尽，停止执行。")
        if self.future is not None and self.future.done():
            new, ms = self.future.result()
            elapsed_steps = max(0.0, (now - self.query_time) * self.hz)
            if elapsed_steps > len(new) - 1:
                raise RuntimeError("RTC 返回结果已超出有效时间范围，停止执行。")
            self.chunk, self.origin = new, self.query_time
            position = elapsed_steps
            self.last_delay = int(math.ceil(elapsed_steps - 1e-7))
            self.delays.append(self.last_delay)
            self.infer_ms, self.future = ms, None
            self.replans += 1
        self.position = position
        if self.future is None and position >= self.config["s_min"] - 1e-7:
            # Resample the old prefix at the exact query time. This matters when
            # policy_hz / command_hz is not an integer ratio (e.g. 30 / 50).
            remaining = len(self.chunk) - 1 - position
            count = int(math.floor(remaining + 1e-7)) + 1
            delay = max(self.delays)
            if delay >= count:
                raise RuntimeError("RTC 预计延迟超过剩余动作范围，请减小 s_min。")
            prefix = sample_actions(self.chunk, position + np.arange(count))
            snapshot = {key: (value.copy() if isinstance(value, np.ndarray) else value) for key, value in obs.items()}
            self.query_time = now
            self.future = self.executor.submit(self._infer, snapshot, prefix, delay)
        return sample_actions(self.chunk, position)

    def metrics(self):
        result = super().metrics()
        result["rtc_remaining"] = 0 if self.chunk is None else max(0, int(math.floor(len(self.chunk) - 1 - self.position)))
        result["rtc_policy_position"] = round(self.position, 3)
        return result


class LoopTiming:
    """Bounded-memory per-stage timing, for diagnosing actual command cadence."""

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.stages = {}

    def call(self, name, fn, *args):
        start = self.clock()
        try:
            return fn(*args)
        finally:
            ms = max(0.0, self.clock() - start) * 1000
            count, total, worst = self.stages.get(name, (0, 0.0, 0.0))
            self.stages[name] = count + 1, total + ms, max(worst, ms)

    def metrics(self):
        return {"step_timing_ms": {name: {"mean": round(total / count, 3), "max": round(worst, 3), "calls": count}
                                   for name, (count, total, worst) in self.stages.items()}}
