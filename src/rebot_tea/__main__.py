import argparse
import json
from pathlib import Path

from .perception import load_fixture
from .planning import plan_grasp
from .robot import MockRobot


def run(config_path: Path) -> list:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["robot"] != {"model": "B601-RS", "backend": "mock"}:
        raise ValueError("Only B601-RS mock backend is implemented")
    if config["perception"]["backend"] != "fixture":
        raise ValueError("Only synthetic fixture perception is implemented")
    target = load_fixture(config_path.parent / config["perception"]["fixture"])
    steps = plan_grasp(target, config["planning"])
    robot = MockRobot()
    robot.execute(steps)
    return robot.history


def main():
    parser = argparse.ArgumentParser(description="Synthetic grasp flow; no physical motion")
    parser.add_argument("--config", type=Path, default=Path("config/simulation.json"))
    args = parser.parse_args()
    for step in run(args.config):
        print(f"[MOCK] {step.action}: {step.position_m}")
    print("Simulation completed; physical grasp success is not evaluated.")


if __name__ == "__main__":
    main()
