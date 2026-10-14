"""Replay synthetic volume feedback into semantic mock pouring requests."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from rebot_tea.pouring import CupObservation, PourController, PourLimits


def replay(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("synthetic") is not True:
        raise ValueError("This replay only accepts explicitly synthetic fixtures")
    limits = PourLimits(**payload["limits"])
    controller = PourController(payload["cup_id"], limits)
    history = []
    for entry in payload["observations"]:
        raw = entry["observation"]
        observation = CupObservation(**raw) if raw is not None else None
        decision = controller.step(observation, entry["now_s"])
        history.append({"now_s": entry["now_s"], **asdict(decision)})
    return {"synthetic": True, "backend": "semantic_mock", "limits": asdict(limits),
            "physical_grasp_verified": False, "physical_volume_verified": False,
            "physical_spout_alignment_verified": False,
            "hardware_commands_sent": False, "decisions": history}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=root / "fixtures/pouring.demo.json")
    parser.add_argument("--output", type=Path, default=root / "artifacts/pouring-replay.json")
    args = parser.parse_args()
    result = replay(args.fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic mock pouring replay -> {args.output}")
