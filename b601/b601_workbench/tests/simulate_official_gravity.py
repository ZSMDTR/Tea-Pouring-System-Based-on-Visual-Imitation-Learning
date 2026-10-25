"""Run with lerobot Python; real URDF math and fake time, no device connection."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mode_worker,official_gravity

class OfficialGravityTests(unittest.TestCase):
    def setUp(self):
        p=json.loads((mode_worker.ROOT/'capture_profile.json').read_text())
        self.robot,_=mode_worker.build(p,'gravity');self.c=official_gravity.Controller(self.robot);self.now=100.;self.positions={n:0. for n in self.robot.motor_names}
    def step(self,dt=.02):
        self.now+=dt
        with patch('time.perf_counter',return_value=self.now):return self.c.step(self.positions)
    def lock(self):
        for _ in range(30):self.step()
    def test_startup_then_lock_and_restore_force(self):
        out=self.step();self.assertEqual(self.c._lock_status,'STARTUP');np.testing.assert_allclose(out['kp'],self.c._hold_kp)
        self.lock();self.assertEqual(self.c._lock_status,'LOCKED');np.testing.assert_allclose(self.c.command['kp'],8.)
        self.positions['shoulder_lift']=-.001
        self.step();self.assertEqual(self.c._lock_status,'LOCKED');self.assertGreater(self.c._integral[1],0)
        self.assertEqual(self.c.command['pos'][1],0)
        self.assertIsNone(self.robot.bus)
    def test_push_follow_then_relock(self):
        self.lock()
        for i in range(5):self.positions['shoulder_lift']+=1;self.step()
        self.assertEqual(self.c._lock_status,'FOLLOW');np.testing.assert_allclose(self.c.command['pos'],self.c.q);np.testing.assert_allclose(self.c._integral,0)
        for _ in range(30):self.step()
        self.assertEqual(self.c._lock_status,'LOCKED')
    def test_timeout_and_nan_stop_before_command(self):
        self.step()
        with self.assertRaises(RuntimeError):self.step(.2)
        self.positions['shoulder_pan']=float('nan')
        with self.assertRaises(ValueError):self.step()
    def test_integral_is_bounded(self):
        self.lock();self.c._q_target=np.ones(6)
        for _ in range(100):self.step()
        self.assertLessEqual(float(np.max(np.abs(self.c._integral))),.3)
    def test_motor_commands_use_motor_frame_and_keep_gripper_hold(self):
        self.positions.update(dict(zip(self.robot.motor_names,[15,40,70,-10,10,20,30])))
        out=self.step()
        np.testing.assert_allclose(out['pos'],np.radians([15,40,70,-10,10,20]))
        self.robot.motors={n:Mock() for n in self.robot.motor_names}
        with patch.object(self.robot,'_check_motor_temperatures'),patch.object(self.robot,'mit_output_torque_limit',return_value=.2):
            self.c.send(out,30)
        self.assertAlmostEqual(self.robot.motors['elbow_flex'].send_mit.call_args.args[0],np.radians(70))
        self.robot.motors['gripper'].send_mit.assert_called_once_with(0,0,0,1.5,.2)

    def test_math_matches_pinocchio(self):
        import subprocess
        urdf=self.robot.config.gravity_urdf_path
        samples=np.random.default_rng(6).uniform(-.4,.4,(12,6)).tolist()
        script="""
import json,sys,numpy as np,pinocchio as pin
urdf,samples=json.loads(sys.stdin.read());model=pin.buildModelFromUrdf(urdf);data=model.createData()
# The six arm joints in this model precede the fixed gripper links.
names=[name for i,name in enumerate(model.names) if i and model.joints[i].nq==1]
end=model.getFrameId(names[5]);out=[]
for sample in samples:
 q=np.zeros(model.nq);q[:6]=sample
 tau=pin.computeGeneralizedGravity(model,data,q).copy()[:6]
 pin.computeJointJacobians(model,data,q);pin.updateFramePlacements(model,data)
 J=pin.getFrameJacobian(model,data,end,pin.ReferenceFrame.WORLD)[:,:6]
 out.append({'tau':tau.tolist(),'J':J.tolist()})
print(json.dumps(out))
"""
        python=mode_worker.ROOT.parent/'.venvs/rebotarm/bin/python'
        result=subprocess.run([str(python),'-c',script],input=json.dumps([urdf,samples]),capture_output=True,text=True,check=True)
        for q,expected in zip(samples,json.loads(result.stdout)):
            np.testing.assert_allclose(self.c._gravity_comp_torque(np.array(q)),np.array(expected['tau'])*self.c._tau_scale,atol=1e-9)
            J=np.array(expected['J'])
            for i in range(6):
                qd=np.eye(6)[i];linear,angular=self.c._end_effector_speed(np.array(q),qd)
                np.testing.assert_allclose([linear,angular],[np.linalg.norm(J[:3,i]),np.linalg.norm(J[3:,i])],atol=1e-9)

    def test_official_method_bodies_are_unchanged(self):
        root=mode_worker.ROOT/'vendor/seeed_gravity'
        def methods(path,cls):
            tree=ast.parse(path.read_text());node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
            return {n.name:ast.dump(n,include_attributes=False) for n in node.body if isinstance(n,ast.FunctionDef)}
        extracted=methods(root/'control_law.py','OfficialVelocityLock')
        originals={**methods(root/'10_gravity_compensation_lock.py','VelocityLockGravityCompensation'),**methods(root/'gravity_compensation.py','GravityCompensation')}
        # Both upstream classes define _loop_cb; select the lock variant.
        originals['_loop_cb']=methods(root/'10_gravity_compensation_lock.py','VelocityLockGravityCompensation')['_loop_cb']
        for name,body in extracted.items():self.assertEqual(body,originals[name],name)

if __name__=='__main__':unittest.main()
