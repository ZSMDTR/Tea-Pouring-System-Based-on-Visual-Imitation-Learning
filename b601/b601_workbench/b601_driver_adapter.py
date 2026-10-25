"""Workbench-only compatibility for per-motor caps and partial safe_zero actions.

The installed Seeed driver adds wrist_yaw when absent and requires cap keys to
exactly match the current goal keys. safe_zero sends six arm joints first and
only sometimes includes the gripper later. Preserve its trajectory and caps.
All calls occur on the single hardware-owning worker thread.
"""
import math
import time

from lerobot_robot_seeed_b601 import SeeedB601RSFollower

class WorkbenchB601RSFollower(SeeedB601RSFollower):
    def configure_command_timing(self,hz):
        """Opt in above 50Hz; keep the previously tested <=50Hz driver path.

        Adapted from the installed SeeedB601RSFollower.mit_output_torque_limit.
        Its 20ms derivatives and alpha=.3 filter assume a 50Hz loop. Preserve
        the same gains, torque limits and physical filter time constant here.
        """
        if type(hz) not in (int,float) or not math.isfinite(hz) or hz<=0:
            raise ValueError('指令下发频率必须是正数。')
        self._gripper_command_hz=float(hz)
        self._gripper_last_torque_at=None
        self._gripper_prev_target_pos=None
        self._gripper_prev_filtered_target_vel=None
        self._gripper_prev_state_pos=None

    def mit_output_torque_limit(self,motor,pos_target):
        hz=getattr(self,'_gripper_command_hz',50)
        if hz<=50:return super().mit_output_torque_limit(motor,pos_target)
        if motor is None:return None
        self.bus.poll_feedback_once()
        state=motor.get_state()
        if state is None:return None
        now=time.monotonic()
        previous=self._gripper_last_torque_at
        dt=(now-previous) if previous is not None and now>previous else 1/hz
        self._gripper_last_torque_at=now
        prev_target=self._gripper_prev_target_pos
        target_vel=0.0 if prev_target is None else (pos_target-prev_target)/dt
        self._gripper_prev_target_pos=pos_target
        prev_filtered=self._gripper_prev_filtered_target_vel
        # At dt=.02, alpha is exactly the original .3. At higher rates,
        # several small updates have the same decay as one 20ms update.
        alpha=-math.expm1(math.log1p(-.3)*dt/.02)
        filtered=target_vel if prev_filtered is None else alpha*target_vel+(1-alpha)*prev_filtered
        target_vel=max(-3.0,min(3.0,filtered))
        self._gripper_prev_filtered_target_vel=target_vel
        prev_state=self._gripper_prev_state_pos
        estimated_vel=0.0 if prev_state is None else (state.pos-prev_state)/dt
        self._gripper_prev_state_pos=state.pos
        torque=(float(self.config.gripper_mit_kp)*(pos_target-state.pos)
                +float(self.config.gripper_mit_kd)*(target_vel-state.vel))
        cap=(self.config.gripper_mit_torque_limit if abs(estimated_vel)>.25
             else self.config.gripper_mit_hold_torque_limit)
        motor.request_feedback()
        return max(-cap,min(cap,torque))

    def send_action(self,action):
        limits=self.config.max_relative_target
        if not isinstance(limits,dict):return super().send_action(action)
        names={key.removesuffix('.pos') for key in action if key.endswith('.pos')}
        names.add('wrist_yaw')  # Same implicit goal inserted by the installed driver.
        missing=names-limits.keys()
        if missing:raise ValueError('缺少电机限幅配置：'+', '.join(sorted(missing)))
        try:
            self.config.max_relative_target={name:limits[name] for name in names}
            return super().send_action(action)
        finally:
            self.config.max_relative_target=limits
