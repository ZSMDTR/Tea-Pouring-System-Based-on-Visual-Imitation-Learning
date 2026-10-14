"""Verify the official B601-RS model in memory; never import actuator code."""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pinocchio as pin


def check_ik_roundtrip(model, frame_id, target, q_target) -> dict:
    """Solve a known reachable pose from a nearby seed; fix the finger joints."""
    q = q_target.copy()
    q[:6] += [0.05, -0.04, 0.03, 0.04, -0.05, 0.06]
    seed = q[:6].copy()
    data = model.createData()
    for iteration in range(201):
        if not np.all((q >= model.lowerPositionLimit) & (q <= model.upperPositionLimit)):
            raise ValueError("IK verification exceeded a URDF joint limit")
        pin.framesForwardKinematics(model, data, q)
        current = data.oMf[frame_id].copy()
        error = pin.log6(current.actInv(target)).vector
        if np.linalg.norm(error) < 1e-8:
            break
        if iteration == 200:
            raise ValueError("IK round-trip did not converge within 200 iterations")
        jacobian = pin.computeFrameJacobian(
            model, data, q, frame_id, pin.ReferenceFrame.LOCAL
        )[:, :6]
        velocity = np.zeros(model.nv)
        velocity[:6] = 0.5 * jacobian.T @ np.linalg.solve(
            jacobian @ jacobian.T + 1e-6 * np.eye(6), error
        )
        q = pin.integrate(model, q, velocity)
    position_error = float(np.linalg.norm(current.translation - target.translation))
    rotation_error = float(np.linalg.norm(pin.log3(current.rotation.T @ target.rotation)))
    if position_error > 1e-7 or rotation_error > 1e-7:
        raise ValueError("IK round-trip pose error exceeds the verification tolerance")
    return {
        "method": "damped least squares, nearby seed, six arm joints only",
        "converged": True,
        "iterations": iteration,
        "seed_arm_q_rad": seed.tolist(),
        "solution_arm_q_rad": q[:6].tolist(),
        "position_error_m": position_error,
        "orientation_error_rad": rotation_error,
        "all_iterates_within_urdf_limits": True,
        "target_source": "FK of sample_arm_q_rad, gripper model joints fixed at zero",
    }


def check(vendor: Path) -> dict:
    urdf = vendor / "urdf/RS/urdf/ReBot_Arm_RS.urdf"
    model = pin.buildModelFromUrdf(str(urdf))
    arm_names = [f"joint{i}" for i in range(1, 7)]
    if list(model.names[1:7]) != arm_names or not model.existFrame("gripper_end"):
        raise ValueError("Expected the official six-axis RS model and gripper_end frame")
    frame_id = model.getFrameId("gripper_end")
    package_dirs = [str(urdf.parent), str(urdf.parent.parent)]
    visual = pin.buildGeomFromUrdf(
        model, str(urdf), pin.GeometryType.VISUAL, package_dirs=package_dirs
    )
    collision = pin.buildGeomFromUrdf(
        model, str(urdf), pin.GeometryType.COLLISION, package_dirs=package_dirs
    )
    if visual.ngeoms == 0 or collision.ngeoms == 0:
        raise ValueError("Visual or collision geometry is missing")

    q = pin.neutral(model)
    data = model.createData()
    pin.framesForwardKinematics(model, data, q)
    neutral_position = data.oMf[frame_id].translation.copy()
    q[:6] = [0.2, 0.7, 1.0, -0.3, 0.2, 0.1]
    if not np.all((q >= model.lowerPositionLimit) & (q <= model.upperPositionLimit)):
        raise ValueError("Verification configuration exceeds URDF joint limits")
    pin.framesForwardKinematics(model, data, q)
    pose = data.oMf[frame_id].copy()
    if not np.isfinite(pose.homogeneous).all():
        raise ValueError("Forward kinematics returned a non-finite pose")
    np.testing.assert_allclose(pose.rotation.T @ pose.rotation, np.eye(3), atol=1e-10)

    # Check the analytical translational Jacobian against actual FK differences.
    jacobian = pin.computeFrameJacobian(
        model, data, q, frame_id, pin.ReferenceFrame.LOCAL_WORLD_ALIGNED
    )[:3, :6]
    epsilon = 1e-7
    finite_difference = np.zeros((3, 6))
    for index in range(6):
        velocity = np.zeros(model.nv)
        velocity[index] = epsilon
        pin.framesForwardKinematics(model, data, pin.integrate(model, q, velocity))
        finite_difference[:, index] = (
            data.oMf[frame_id].translation - pose.translation
        ) / epsilon
    np.testing.assert_allclose(jacobian, finite_difference, atol=1e-6, rtol=1e-5)
    ik_result = check_ik_roundtrip(model, frame_id, pose, q)
    commit = subprocess.run(
        ["git", "-C", str(vendor), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return {
        "status": "PASS",
        "source": "https://github.com/Seeed-Projects/reBotArm_control_py",
        "commit": commit,
        "pinocchio": pin.__version__,
        "urdf": str(urdf),
        "joint_names": list(model.names[1:]),
        "nq": model.nq,
        "nv": model.nv,
        "arm_dof": 6,
        "visual_geometries": visual.ngeoms,
        "collision_geometries": collision.ngeoms,
        "end_effector_frame": "gripper_end",
        "neutral_position_m": neutral_position.tolist(),
        "sample_arm_q_rad": q[:6].tolist(),
        "sample_position_m": pose.translation.tolist(),
        "jacobian_max_error": float(np.max(np.abs(jacobian - finite_difference))),
        "ik_roundtrip": ik_result,
        "hardware_connected": False,
        "self_collision_checked": False,
        "self_collision_reason": "Official source has no SRDF or collision-pair exclusion policy",
        "collision_free_path_verified": False,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", type=Path, default=root / "vendor/reBotArm_control_py")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(check(args.vendor.resolve()), indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
