"""Adapt pinned Seeed velocity-lock control law to the installed B601 driver.

The upstream methods in vendor/seeed_gravity/control_law.py are unchanged.
We supply the existing robot lifecycle and NumPy URDF math instead of importing
another SDK's device loop or installing Pinocchio into the recording environment.
"""
from pathlib import Path
from types import SimpleNamespace
import time
import numpy as np
from vendor.seeed_gravity.control_law import OfficialVelocityLock

REVISION='6415d43130d1'


class Controller(OfficialVelocityLock):
    def __init__(self, robot):
        import yaml
        from lerobot_robot_seeed_b601 import gravity
        self.robot=robot
        self.math=gravity
        self.model=gravity.load_dynamics_model(robot.config.gravity_urdf_path)
        self.names=[n for n in robot.motor_names if n!='gripper']
        self.pairing=robot._gravity_pairing
        if len(self.names)!=6 or not self.pairing or set(self.pairing)!=set(self.names):
            raise ValueError('官方调姿控制器需要完整的六关节模型映射。')
        self.cfg=yaml.safe_load((Path(__file__).parent/'vendor/seeed_gravity/rebotarm_rs.yaml').read_text())['gravity_compensation']['profiles']['velocity_lock']
        for key in ('ki','integral_limit','linear_release_threshold','angular_release_threshold','linear_lock_threshold','angular_lock_threshold','lock_settle_duration','velocity_filter_time_constant','max_valid_sample_dt','transition_duration','log_every'):
            setattr(self,'_'+key,self.cfg[key])
        self._tau_scale=np.asarray(self.cfg['tau_scale'],dtype=float)
        self._hold_kp=np.array([robot.config.mit_kp[n] for n in self.names])
        self._hold_kd=np.array([robot.config.mit_kd[n] for n in self.names])
        self._compliant_kp=np.full(6,self.cfg['kp']);self._compliant_kd=np.full(6,self.cfg['kd'])
        self._q_target=None;self._q_previous=None;self._sample_time_previous=None;self._qd_filtered=None
        self._integral=np.zeros(6);self._following=False;self._quiet_since=None;self._lock_status='STARTUP'
        self._transition_q_hold=None;self._transition_started_at=None;self._counter=0
        self._last_tau_g=np.zeros(6);self.q=np.zeros(6);self.command=None
        # No hardware methods: official loop computes a complete command first.
        self.facade=SimpleNamespace(arm=SimpleNamespace(num_joints=6,get_positions=lambda:self.q.copy(),send_mit=self.capture_command),has_gripper=False)
        self.last_tick=None;self.raw_speed=0.0;self.linear_speed=0.0;self.angular_speed=0.0
        self.joints=sorted(self.model.joints,key=lambda j:j.q_idx)
        self.q_indices=[self.pairing[n] for n in self.names]
        # A six-axis serial arm is required for the WORLD-frame Jacobian below.
        movable=[j for j in self.joints if j.jtype in ('revolute','continuous')]
        if [j.q_idx for j in movable]!=self.q_indices:raise ValueError('URDF 关节顺序与电机映射不一致。')
        self.chain_end=movable[-1].child
        parent_of={j.child:j for j in self.model.joints};chain=[];link=self.chain_end
        while link!=self.model.root:
            j=parent_of[link];chain.append(j);link=j.parent
        self.chain=list(reversed(chain))
        if {j.q_idx for j in self.chain if j.q_idx>=0}!=set(self.q_indices):raise ValueError('URDF 不是预期的六轴串联结构。')
        # Warm all model calculations before any motor connection.
        self._gravity_comp_torque(self.q);self._end_effector_speed(self.q,self.q)

    def capture_command(self,**command):
        self.command={k:np.asarray(v,dtype=float).copy() for k,v in command.items()}
        if any(a.shape!=(6,) or not np.all(np.isfinite(a)) for a in self.command.values()):raise ValueError('官方控制器输出无效。')

    def model_q(self,q):
        out=np.zeros(self.model.nq)
        for idx,value in zip(self.q_indices,q):out[idx]=value
        return out

    def _gravity_comp_torque(self,q):
        tau=self.math.compute_generalized_gravity(self.model,self.model_q(q))
        return tau[self.q_indices]*self._tau_scale

    def _end_effector_speed(self,q,qd):
        # Equivalent to Pinocchio getFrameJacobian(..., WORLD): spatial
        # velocity expressed at the world origin, as in the official example.
        R=np.eye(3);p=np.zeros(3);linear=np.zeros(3);angular=np.zeros(3)
        by_idx=dict(zip(self.q_indices,zip(q,qd)))
        for j in self.chain:
            p=p+R@j.origin_xyz;R=R@self.math._rpy_to_R(*j.origin_rpy)
            if j.q_idx>=0:
                angle,speed=by_idx[j.q_idx];axis=R@j.axis
                linear+=np.cross(p,axis)*speed;angular+=axis*speed
                R=R@self.math._axis_angle_R(j.axis,angle)
        self.linear_speed=float(np.linalg.norm(linear));self.angular_speed=float(np.linalg.norm(angular))
        return self.linear_speed,self.angular_speed

    def step(self,positions):
        q=np.radians([positions[n] for n in self.names]);now=time.perf_counter()
        if not np.all(np.isfinite(q)):raise ValueError('关节位置无效。')
        if self.last_tick is not None:
            dt=now-self.last_tick
            # Upstream resets velocity on gaps; the workbench stops instead,
            # so a feedback timeout cannot be mistaken for a stationary arm.
            if not 0<dt<=self._max_valid_sample_dt:raise RuntimeError('调姿反馈间隔超过 100 ms，已停止控制并进入退出流程。')
            self.raw_speed=float(np.max(np.abs(q-self.q))/dt)
        else:
            self._transition_q_hold=q.copy();self._transition_started_at=now
        self.q=q;self.last_tick=now;self.command=None
        self._loop_cb(self.facade,1/30)
        if self.command is None:raise RuntimeError('官方控制器未生成动作。')
        return self.command

    def send(self,command,gripper):
        robot=self.robot
        robot._check_motor_temperatures(robot.config.temp_alarm_threshold_c,robot.config.temp_overheat_threshold_c,context='workbench_gravity_lock')
        if robot._emergency_disable_requested:raise RuntimeError('温度保护已要求失能。')
        for i,name in enumerate(self.names):
            lo,hi=robot.config.joint_limits[name]
            if not lo-2<=np.degrees(command['pos'][i])<=hi+2:raise ValueError('调姿目标超出关节范围：'+name)
        # Preserve the installed driver's gripper torque limiting behavior.
        motor=robot.motors['gripper']
        tau=robot.mit_output_torque_limit(motor,float(np.radians(gripper)))
        if tau is None or not np.isfinite(tau):raise RuntimeError('夹爪反馈无效。')
        for i,name in enumerate(self.names):
            robot.motors[name].send_mit(*(float(command[k][i]) for k in ('pos','vel','kp','kd','tau')))
        motor.send_mit(0,0,0,1.5,float(tau))

    def status(self):
        return {'controller':'seeed_velocity_lock','revision':REVISION,'lock_state':self._lock_status,'linear_speed':self.linear_speed,'angular_speed':self.angular_speed,'joint_speed':self.raw_speed}
