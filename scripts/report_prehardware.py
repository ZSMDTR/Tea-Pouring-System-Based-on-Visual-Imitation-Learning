"""Aggregate outputs after the fail-fast full offline verification completes."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / "artifacts"
    reports = {name: json.loads((directory / filename).read_text()) for name, filename in {
        "unit_tests": "unit-tests.json", "training_tests": "training-tests.json",
        "joints": "joints-check.json", "training": "training-verify.json",
        "vision_cpu": "vision-cpu.json", "vision_cuda": "vision-cuda.json",
    }.items()}
    if (directory / "kinematics-check.json").exists():
        reports["kinematics"] = json.loads((directory / "kinematics-check.json").read_text())
    passed = all(reports[key]["passed"] for key in ("unit_tests", "training_tests", "joints", "training"))
    passed = passed and reports["training_tests"]["skipped"] == 0 \
        and reports["vision_cpu"]["inference"] == "passed" \
        and reports["vision_cuda"]["inference"] == "passed" \
        and reports["vision_cuda"]["training_step"] == "passed on synthetic input only"
    if "kinematics" in reports:
        passed = passed and reports["kinematics"]["status"] == "PASS"
    # The detailed trajectory samples remain in their own artifact.
    for result in reports["joints"]["results"]:
        result.pop("samples", None)
    report = {"passed": passed, "verification": "prehardware-software-only",
              "physical_task_verified": False, "reports": reports,
              "workspace_head_at_verification": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "working_tree_dirty_at_verification": bool(subprocess.check_output(
                  ["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())}
    (directory / "prehardware-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": passed, "report": str(directory / "prehardware-report.json"),
                      "physical_task_verified": False}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
