"""Exercise the real workbench driver with a fake motor; no CAN connection."""
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import inference_worker  # Load the existing environment's native runtime first.
from lerobot_robot_seeed_b601 import SeeedB601RSFollower
from b601_driver_adapter import WorkbenchB601RSFollower


class GripperTimingTests(unittest.TestCase):
    def make(self,hz):
        robot=object.__new__(WorkbenchB601RSFollower)
        robot.config=SimpleNamespace(gripper_mit_kp=1.,gripper_mit_kd=1.,
                                     gripper_mit_torque_limit=10.,gripper_mit_hold_torque_limit=.1)
        robot.bus=Mock()
        robot.configure_command_timing(hz)
        state=SimpleNamespace(pos=0.,vel=0.)
        motor=Mock();motor.get_state.return_value=state
        return robot,motor,state

    def test_50hz_and_unconfigured_driver_delegate_to_previously_tested_method(self):
        robot,motor,_=self.make(50)
        with patch.object(SeeedB601RSFollower,'mit_output_torque_limit',return_value=.123) as upstream:
            self.assertEqual(robot.mit_output_torque_limit(motor,1),.123)
            del robot._gripper_command_hz
            self.assertEqual(robot.mit_output_torque_limit(motor,1),.123)
            self.assertEqual(upstream.call_count,2)

    def test_velocity_and_filter_have_same_physical_time_scale_across_rates(self):
        values=[]
        for hz in (100,200):
            robot,motor,state=self.make(hz)
            with patch('b601_driver_adapter.time.monotonic',side_effect=[i/hz for i in range(int(.1*hz)+1)]):
                for i in range(int(.1*hz)+1):
                    t=i/hz
                    state.pos=t;state.vel=1.
                    torque=robot.mit_output_torque_limit(motor,2*t)
            expected_vel=2*(1-.7**5)
            self.assertAlmostEqual(robot._gripper_prev_filtered_target_vel,expected_vel)
            self.assertAlmostEqual(torque,.1+expected_vel-1)
            values.append(torque)
        self.assertAlmostEqual(*values)

    def test_measured_intervals_handle_jitter_and_20ms_return_steps(self):
        robot,motor,state=self.make(100)
        with patch('b601_driver_adapter.time.monotonic',side_effect=[0,.01,.04,.06]):
            for t in (0,.01,.04,.06):
                state.pos=t;state.vel=1.
                robot.mit_output_torque_limit(motor,2*t)
        self.assertAlmostEqual(robot._gripper_prev_filtered_target_vel,2*(1-.7**3))

    def test_original_torque_caps_and_target_velocity_cap_remain_effective(self):
        robot,motor,state=self.make(200)
        with patch('b601_driver_adapter.time.monotonic',side_effect=[0,.005,.01]):
            self.assertEqual(robot.mit_output_torque_limit(motor,100),.1)
            self.assertEqual(robot.mit_output_torque_limit(motor,200),.1)
            self.assertEqual(robot._gripper_prev_filtered_target_vel,3.)
            state.pos=.01
            self.assertEqual(robot.mit_output_torque_limit(motor,300),10.)

    def test_missing_feedback_does_not_create_timing_or_target_history(self):
        robot,motor,_=self.make(100)
        motor.get_state.return_value=None
        self.assertIsNone(robot.mit_output_torque_limit(motor,1))
        self.assertIsNone(robot._gripper_last_torque_at)
        self.assertIsNone(robot._gripper_prev_target_pos)
        motor.request_feedback.assert_not_called()

    def test_invalid_frequencies_rejected(self):
        for hz in (0,-1,True,float('nan'),float('inf')):
            with self.subTest(hz=hz),self.assertRaises(ValueError):self.make(hz)


if __name__=='__main__':unittest.main()
