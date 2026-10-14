"""Summarize a completed operator CSV; empty templates cannot pass acceptance."""
import argparse
import csv
import json
from pathlib import Path

from rebot_tea.acceptance import summarize_grasp, summarize_pour


def read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    bool_keys = ("grasp_stable", "lift_complete", "placed_in_region", "dropped", "collided",
                 "intervention", "spill")
    number_keys = ("capacity_ml", "final_volume_ml", "stop_requested_time_s", "flow_stopped_time_s",
                   "measured_volume_at_stop_ml")
    for row in rows:
        for key in bool_keys:
            if key in row:
                value = row[key].strip().lower()
                if value not in ("true", "false"):
                    raise ValueError(f"{key} requires true/false, not an empty cell")
                row[key] = value == "true"
        for key in number_keys:
            if key in row:
                row[key] = float(row[key]) if row[key].strip() else None
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("grasp", "pour"))
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        rows = read_csv(args.csv)
        report = summarize_grasp(rows) if args.kind == "grasp" else summarize_pour(rows)
    except Exception as error:
        report = {"passed": False, "error": f"{type(error).__name__}: {error}"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
