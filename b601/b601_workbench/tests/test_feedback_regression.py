import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mode_worker,record_worker

class FeedbackRegression(unittest.TestCase):
    def robot(self):
        motor=Mock();motor.get_state.return_value=SimpleNamespace(pos=.5,vel=.01,t_mos=30.,t_rotor=31.)
        return SimpleNamespace(motors={'joint':motor},bus=Mock(),config=SimpleNamespace(joint_limits={'joint':(-90,90)}),cameras={},_emergency_disable_requested=False,safe_zero=Mock())
    def test_official_feedback_path_does_not_read_sdo(self):
        r=self.robot();values,_=mode_worker.measured(r,live=True)
        self.assertAlmostEqual(values['joint'],28.6478897565)
        r.motors['joint'].request_feedback.assert_called_once();r.bus.poll_feedback_once.assert_called_once()
        r.motors['joint'].robstride_get_param_f32.assert_not_called()
    def test_missing_feedback_is_not_replaced_by_zero(self):
        r=self.robot();r.motors['joint'].get_state.return_value=None
        with self.assertRaises(RuntimeError):mode_worker.measured(r,live=True)
        r.motors['joint'].robstride_get_param_f32.assert_not_called()
    def test_can_read_error_is_not_hidden(self):
        r=self.robot();r.bus.poll_feedback_once.side_effect=RuntimeError('CAN disconnected')
        with self.assertRaisesRegex(RuntimeError,'CAN disconnected'):mode_worker.measured(r,live=True)
    def test_cleanup_keeps_failures_and_does_not_retry_closed_handles(self):
        r=self.robot();bus=r.bus;motor=r.motors['joint']
        r.safe_zero.side_effect=RuntimeError('no CAN');motor.disable.side_effect=RuntimeError('no CAN')
        errors=record_worker.cleanup(r,None,True)
        self.assertEqual(len(errors),2);self.assertIsNone(r.bus);self.assertTrue(r._skip_safe_zero_on_disconnect)
        record_worker.cleanup(r,None,True)
        r.safe_zero.assert_called_once();motor.disable.assert_called_once();motor.close.assert_called_once();bus.close.assert_called_once()

if __name__=='__main__':unittest.main()
