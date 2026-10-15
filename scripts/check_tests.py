"""Run existing unittest suites and save a machine-readable outcome."""
import argparse
import json
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/unit-tests.json")
    parser.add_argument("--require-no-skips", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern=args.pattern, top_level_dir=str(ROOT)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"passed": result.wasSuccessful() and result.testsRun > 0
              and (not args.require_no_skips or not result.skipped),
              "tests_run": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skipped": len(result.skipped),
              "skipped_tests": [{"test": test.id(), "reason": reason}
                                for test, reason in result.skipped],
              "elapsed_s": time.monotonic() - started}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
