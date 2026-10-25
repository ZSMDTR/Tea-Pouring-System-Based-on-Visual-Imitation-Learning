"""Explicit inference start pose, independent of model timing and horizontal parking.

Angles are in measured motor coordinates. send_action applies the installed
direction/scale map once, so commands below divide by that map. No SDK device
loop, calibration writes, changes to gains/caps, or hardware imports live here.
"""
import hashlib
import json
import math
from pathlib import Path
import time
from collections import deque

ARM_JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex',
              'wrist_flex', 'wrist_yaw', 'wrist_roll')
JOINTS = ARM_JOINTS + ('gripper',)
DIRECTIONS = dict(zip(JOINTS, (1., 1., -1., -1., -1., 1., 6.)))
PRESET = Path(__file__).resolve().parent / 'data/official_center_ready.json'
VERSION = 2


class ArrivalTimeout(RuntimeError):
    """Only an arrival-check timeout; transport/feedback faults use other errors."""
    def __init__(self, diagnostics, near_target):
        self.diagnostics = diagnostics
        self.near_target = near_target
        super().__init__(
            f"官方待命位确认超时：{diagnostics['settle_status']}；"
            f"最大关节误差 {diagnostics['max_error_deg']:.1f}°；模型未启动。")


def selection(value='horizontal'):
    if value not in ('horizontal', 'official_center_ready'):
        raise ValueError('请选择水平启动或官方居中待命位。')
    return value


def preset():
    value = json.loads(PRESET.read_text())
    if (value['id'] != 'official_center_ready'
            or value['coordinate_system'] != 'motor_degrees'
            or set(value['motor_degrees']) != set(ARM_JOINTS)):
        raise ValueError('官方待命姿态配置无效。')
    if not all(math.isfinite(float(v)) for v in value['motor_degrees'].values()):
        raise ValueError('官方待命姿态角度无效。')
    # Bound the local trajectory settings too; invalid configuration cannot move.
    for key, low, high in [('move_hz', 10, 50), ('max_joint_speed_deg_s', 1, 20),
                           ('min_duration_s', 3, 30), ('settle_timeout_s', 1, 15),
                           ('tolerance_deg', .1, 3), ('stable_motion_deg', .1, 1),
                           ('stable_duration_s', .4, 3)]:
        v = value[key]
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('官方待命轨迹配置无效：' + key)
    return value


def validate_robot(robot, value):
    """Read-only validation before connect/enable, repeated by the mover."""
    cfg = robot.config
    if cfg.id != value['robot_id']:
        raise ValueError('待命姿态与从臂标识不匹配。')
    if hashlib.sha256(Path(robot.calibration_fpath).read_bytes()).hexdigest() != value['calibration_sha256']:
        raise ValueError('从臂标定已变化，请先重新核对待命姿态。')
    if hashlib.sha256(Path(cfg.gravity_urdf_path).read_bytes()).hexdigest() != value['urdf_sha256']:
        raise ValueError('机械臂模型已变化，请先重新核对待命姿态。')
    for index, name in enumerate(JOINTS, 1):
        if cfg.motor_can_ids[name][0] != index or cfg.joint_directions[name] != DIRECTIONS[name]:
            raise ValueError('待命姿态与当前关节映射不匹配：' + name)
    for name, target in value['motor_degrees'].items():
        lo, hi = cfg.joint_limits[name]
        if not lo <= target <= hi:
            raise ValueError('待命姿态超出当前关节范围：' + name)


def measured(robot):
    raw = robot.get_observation()
    positions, velocities = {}, {}
    for name in JOINTS:
        # The installed get_observation substitutes zeros for missing feedback.
        # Startup must fail on that case instead of treating it as a zero pose.
        if robot.motors[name].get_state() is None:
            raise RuntimeError('进入待命位时缺少关节反馈：' + name)
        position = float(raw[name + '.pos'])
        velocity = float(raw[name + '.vel'])
        if not math.isfinite(position) or not math.isfinite(velocity):
            raise RuntimeError('进入待命位时关节反馈无效：' + name)
        lo, hi = robot.config.joint_limits[name]
        if not lo - 2 <= position <= hi + 2:
            raise RuntimeError('进入待命位时关节位置越界：' + name)
        positions[name] = position
        velocities[name] = velocity
    return positions, velocities


def move_to_ready(robot, value, check, update, clock=None, sleep=None):
    """Slow quintic joint motion, hold measured gripper, verify actual settling.

    check() is supplied by the hardware-owning worker. It checks cancellation
    and camera freshness and may raise; it never starts another device owner.
    Stability is measured from a full window of actual joint positions. The
    instantaneous reported velocities are recorded, not used as a consecutive
    below-threshold gate. A failed arrival never starts model execution.
    """
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    validate_robot(robot, value)
    check()
    starts, _ = measured(robot)
    targets = {**value['motor_degrees'], 'gripper': starts['gripper']}
    delta = max(abs(targets[n] - starts[n]) for n in ARM_JOINTS)
    # Maximum derivative of 10u^3-15u^4+6u^5 is 1.875.
    duration = max(value['min_duration_s'], 1.875 * delta / value['max_joint_speed_deg_s'])
    frames = math.ceil(duration * value['move_hz'])
    interval = 1 / value['move_hz']
    begin = clock()
    history = deque()
    stable_duration = value['stable_duration_s']
    minimum_samples = max(3, math.ceil(stable_duration * value['move_hz'] / 2))
    last_update = -math.inf
    error = math.inf
    tick_index = 0
    while True:
        tick = clock()
        check()
        if getattr(robot, '_emergency_disable_requested', False) is True:
            raise RuntimeError('进入待命位时驱动已触发保护性失能。')
        positions, velocities = measured(robot)
        u = min(1., tick_index / frames)
        blend = u ** 3 * (10 + u * (-15 + 6 * u))
        goals = {n: starts[n] + blend * (targets[n] - starts[n]) for n in JOINTS}
        check()
        robot.send_action({n + '.pos': goals[n] / DIRECTIONS[n] for n in JOINTS})
        if getattr(robot, '_emergency_disable_requested', False) is True:
            raise RuntimeError('进入待命位时驱动已触发保护性失能。')
        errors = {n:abs(positions[n] - targets[n]) for n in ARM_JOINTS}
        error = max(errors.values())
        observed_at = clock()
        if u >= 1 and error <= value['tolerance_deg']:
            history.append((observed_at, positions))
            # Keep one sample at/before the window boundary, plus all newer ones.
            while len(history) > 1 and history[1][0] <= observed_at - stable_duration:
                history.popleft()
        else:
            history.clear()
        span = observed_at - history[0][0] if history else 0.
        motion = max(max(p[n] for _, p in history) - min(p[n] for _, p in history)
                     for n in ARM_JOINTS) if len(history) >= 2 else None
        enough_samples = span >= stable_duration and len(history) >= minimum_samples
        settled = enough_samples and motion <= value['stable_motion_deg']
        status = ('正在进入待命位' if u < 1 else '位置误差仍超出允许范围' if error > value['tolerance_deg']
                  else '等待完整的位置稳定窗口' if not enough_samples
                  else '到位附近的位置仍在变化' if not settled else '位置已到位并稳定')
        diagnostics = {'progress':round(min(100., u * 100), 1),
                       'max_error_deg':round(error, 3), 'elapsed':round(clock() - begin, 3),
                       'measured_motor_degrees':positions,
                       'target_motor_degrees':targets, 'joint_error_deg':errors,
                       'reported_velocity_deg_s':velocities,
                       'reported_speed_deg_s':max(abs(velocities[n]) for n in ARM_JOINTS),
                       'window_duration_s':round(span, 3), 'window_samples':len(history),
                       'window_motion_deg':None if motion is None else round(motion, 3),
                       'settle_status':status, 'arrival_check_version':VERSION}
        if settled:
            update(**diagnostics)
            return {**diagnostics, 'id':value['id'], 'label':value['label'], 'verified':True}
        if clock() - begin > duration + value['settle_timeout_s']:
            update(**diagnostics)
            raise ArrivalTimeout(diagnostics, near_target=error <= value['tolerance_deg'])
        if tick - last_update >= .2:
            update(**diagnostics)
            last_update = tick
        tick_index += 1
        # Delays slow this startup trajectory; never jump forward or catch up.
        sleep(max(0., interval - (clock() - tick)))
