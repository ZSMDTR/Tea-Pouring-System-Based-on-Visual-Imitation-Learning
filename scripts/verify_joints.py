"""Run bounded joint trajectories against five mock feedback scenarios."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from rebot_tea.joints import JOINT_NAMES, JointLimits, LimitedJointController, SimulatedJointBackend

ROOT = Path(__file__).resolve().parents[1]


def run_scenario(config, scenario):
    if config.get("synthetic") is not True or config.get("backend") != "mock":
        raise ValueError("Joint verification requires an explicitly synthetic mock configuration")
    hz = config["control_hz"]
    if type(hz) is not int or hz != 50:
        raise ValueError("This verification uses a 50 Hz virtual clock")
    now = [0.0]
    def clock():
        return now[0]
    limits = JointLimits(**config["limits"])
    backend = SimulatedJointBackend(clock, config["initial_rad"], scenario)
    controller = LimitedJointController(backend, limits, clock)
    controller.start(config["target_rad"])
    samples = []
    for index in range(int(limits.timeout_s * hz) + 2):
        now[0] = index / hz
        decision = controller.tick()
        if decision.target_rad is not None:
            samples.append({"timestamp_s": now[0], "state_rad": list(backend.position),
                            "action_rad": list(decision.target_rad)})
        if decision.state != "running":
            break
    expected = "completed" if scenario == "normal" else "aborted"
    passed = decision.state == expected and (
        scenario == "normal" or decision.stop_requested and decision.mock_stop_confirmed)
    return {"scenario": scenario, "passed": passed, "decision": asdict(decision),
            "command_count": len(backend.history), "stop_calls": backend.stop_calls,
            "trajectory_duration_s": controller.trajectory.duration_s, "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/joints.synthetic.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/joints-check.json")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    results = [run_scenario(config, name) for name in
               ("normal", "lag", "missing", "stalled", "communication")]
    report = {"synthetic": True, "backend": "mock", "joint_names": JOINT_NAMES,
              "units": "public joint radians", "physical_motion_verified": False,
              "passed": all(row["passed"] for row in results), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({**report, "results": [{k: v for k, v in row.items() if k != "samples"}
                                           for row in results]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
