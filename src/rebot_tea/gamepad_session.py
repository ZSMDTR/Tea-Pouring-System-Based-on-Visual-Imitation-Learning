"""Run joint jogging and local recording; all device factories stay in the CLI."""
import math
import time

import numpy as np

from .gamepad_control import JointJogger


class SyntheticCamera:
    """A labelled software fixture, without a rendered robot or physical scene."""

    image_shape = (224, 224, 3)

    def __init__(self, clock=time.monotonic):
        self.clock, self.index = clock, 0
        self.image = np.zeros(self.image_shape, dtype=np.uint8)
        self.image[:, :, 0] = np.arange(224, dtype=np.uint8)[None, :]
        self.image[:, :, 1] = np.arange(224, dtype=np.uint8)[:, None]

    def read_latest_with_metadata(self, max_age_ms=200):
        self.index += 1
        image = self.image.copy()
        image[:, :, 2] = self.index % 256
        return image, {"camera_host_s": self.clock(), "camera_frame_index": self.index,
                       "camera_timestamp_us": None, "depth_timestamp_us": None}


def require_neutral_input(frame, hardware=False):
    if frame is None or not frame.connected:
        raise ValueError("A fresh connected gamepad is required")
    if hardware and frame.synthetic:
        raise ValueError("Synthetic input cannot enable physical hardware")
    if frame.buttons or any(abs(value) > .12 for value in frame.axes):
        raise ValueError("Release all buttons, sticks and triggers before starting")


def run_session(backend, bridge, camera, recorder, limits, duration=0,
                clock=time.monotonic, sleep=time.sleep):
    """Stop on input/feedback/camera failure, without reconnecting the arm.

    The caller has already acquired fresh neutral input before connecting a
    hardware backend. Native video encoding runs only after a feedback hold;
    it requires a new neutral/released-LB observation before the next jog.
    """
    jogger = JointJogger(limits)
    report = {"backend": backend.mode, "synthetic": backend.mode == "mock",
              "physical_task_verified": False, "physical_stop_confirmed": False,
              "commands_sent": 0, "active_ticks": 0, "recorded_frames": 0,
              "synthetic_input_seen": False, "events": [], "status": "running"}
    started = clock()
    next_tick, next_record, last_camera_s = started, started, None
    next_display = started
    try:
        while not duration or clock() - started < duration:
            frame = bridge.snapshot()
            if frame is None:
                raise ValueError("input_missing_or_expired")
            if backend.mode == "hardware" and frame.synthetic:
                raise ValueError("synthetic_input_rejected_for_hardware")
            report["synthetic_input_seen"] |= frame.synthetic
            state = backend.read_state()
            image, camera_timing = camera.read_latest_with_metadata(max_age_ms=200)
            now = clock()
            if not 0 <= now - camera_timing["camera_host_s"] <= .2:
                raise ValueError("camera_stale_or_future")
            decision = jogger.step(frame, state, now)
            if decision.reason in {"input_missing", "input_disconnected", "input_timeout",
                                   "input_reset", "tick_timeout"}:
                raise ValueError(decision.reason)
            if now >= next_display:
                degrees = [round(math.degrees(value), 2) for value in state.positions_rad]
                print(f"group={decision.group + 1} {decision.reason} feedback_deg={degrees}", flush=True)
                next_display = now + 1
            # Episode operations can encode videos and must not leave a jog active.
            if decision.events:
                backend.send_target(state.positions_rad)
                report["commands_sent"] += 1
                jogger.pause()
                for event in decision.events:
                    report["events"].append({"event": event, "elapsed_s": clock() - started})
                    print(f"{event}: frames={recorder.frames_in_episode}", flush=True)
                    if event == "record_start" and not recorder.is_recording:
                        recorder.start()
                    elif event == "record_discard":
                        recorder.discard()
                    elif event in {"record_success", "record_failure"}:
                        recorder.finish("success" if event == "record_success" else "failure")
                jogger.pause()
                next_tick = clock() + .02
                next_record, last_camera_s = clock(), None
            else:
                backend.send_target(decision.target_rad)
                sent_s = clock()
                report["commands_sent"] += 1
                report["active_ticks"] += int(decision.active)
                report["last_target_rad"] = list(decision.target_rad)
                if recorder.is_recording and sent_s >= next_record and \
                        camera_timing["camera_host_s"] != last_camera_s:
                    recorder.add_frame(image, state.positions_rad, decision.target_rad, {
                        **camera_timing, "input_received_s": frame.received_s,
                        "input_sequence": frame.sequence,
                        "observation_read_s": state.timestamp_s, "command_sent_s": sent_s,
                        "control_group": decision.group, "deadman_held": decision.active,
                    })
                    report["recorded_frames"] += 1
                    interval = 1 / recorder.fps
                    next_record += interval
                    if next_record <= sent_s:
                        # Skip missed slots; never duplicate an observation to catch up.
                        next_record += (int((sent_s - next_record) / interval) + 1) * interval
                    last_camera_s = camera_timing["camera_host_s"]
                next_tick += .02
            sleep(max(0, next_tick - clock()))
        report["status"] = "duration_completed"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as error:
        report.update(status="stopped_on_error", error=f"{type(error).__name__}: {error}")
    finally:
        report["stop_requested"] = True
        try:
            confirmed = backend.request_stop()
            report["mock_stop_confirmed"] = backend.mode == "mock" and confirmed is True
        except Exception as error:
            report["stop_error"] = f"{type(error).__name__}: {error}"
        report["elapsed_s"] = clock() - started
        report["episodes_saved"] = recorder.episodes_saved
    return report
