"""Replay explicit synthetic feedback through the mock-only grasp/place cycle."""
import argparse
import json
from pathlib import Path

from rebot_tea.grasp_cycle import SyntheticGraspCycle

ROOT = Path(__file__).resolve().parents[1]


def replay(config_path: Path, feedback_path: Path) -> dict:
    cycle = SyntheticGraspCycle(json.loads(config_path.read_text(encoding="utf-8")))
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    if feedback.get("synthetic") is not True or not isinstance(feedback.get("events"), list):
        raise ValueError("An explicitly synthetic feedback event list is required")
    cycle.start(0.0)
    for event in feedback["events"]:
        try:
            kind = event.get("type")
            if kind == "step":
                cycle.report_completion(event["action"], event["completed"],
                                        event["observed_at_s"], event["now_s"])
            elif kind == "grasp_evidence":
                cycle.confirm_grasp(event["confirmed"], event["observed_at_s"], event["now_s"])
            elif kind == "release_evidence":
                cycle.confirm_release(event["confirmed"], event["observed_at_s"], event["now_s"])
            else:
                cycle.abort("unknown_event_type")
        except (AttributeError, KeyError, TypeError):
            cycle.abort("invalid_event")
        if cycle.state in ("completed", "aborted"):
            break
    if cycle.state not in ("completed", "aborted"):
        cycle.abort("feedback_ended_before_completion")
    return cycle.report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/grasp_cycle.synthetic.json")
    parser.add_argument("--feedback", type=Path,
                        default=ROOT / "fixtures/grasp_cycle_feedback.synthetic.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/grasp-cycle.json")
    args = parser.parse_args()
    result = replay(args.config, args.feedback)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic mock grasp/place: {result['state']} ({result['reason']}) -> {args.output}")
    return 0 if result["state"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
