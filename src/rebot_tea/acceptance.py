"""Score operator-entered trials without claiming independent physical verification."""
from .joints import finite_number


def _identity(rows):
    ids = [row.get("trial_id") for row in rows]
    if any(not isinstance(value, str) or not value.strip() for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Trial IDs must be nonempty and unique")


def summarize_grasp(rows):
    _identity(rows)
    good = ("grasp_stable", "lift_complete", "placed_in_region")
    bad = ("dropped", "collided", "intervention")
    results = []
    for row in rows:
        if any(type(row.get(key)) is not bool for key in good + bad):
            raise ValueError("All grasp observations must be explicitly true or false")
        results.append(all(row[key] for key in good) and not any(row[key] for key in bad))
    successes = sum(results)
    return {"trial_count": len(rows), "successes": successes,
            "required_trials": 20, "required_successes": 18,
            "passed": len(rows) == 20 and successes >= 18,
            "independently_verified": False, "source": "operator_entered"}


def summarize_pour(rows):
    _identity(rows)
    results = []
    for row in rows:
        capacity, final = row.get("capacity_ml"), row.get("final_volume_ml")
        if not finite_number(capacity) or capacity <= 0 or not finite_number(final) or final < 0:
            raise ValueError("Independent capacity/final-volume measurements must be finite and valid")
        if type(row.get("spill")) is not bool:
            raise ValueError("spill must be explicitly true or false")
        requested, stopped = row.get("stop_requested_time_s"), row.get("flow_stopped_time_s")
        if not finite_number(requested) or not finite_number(stopped) or stopped < requested:
            raise ValueError("Stop and settled-flow times must be valid on the same clock")
        observed = row.get("measured_volume_at_stop_ml")
        if observed is not None and (not finite_number(observed) or not 0 <= observed <= final):
            raise ValueError("Measured stop-time volume cannot exceed the settled final volume")
        fraction = final / capacity
        results.append({"trial_id": row["trial_id"], "volume_fraction": fraction,
                        "target_error_percentage_points": (fraction - .9) * 100,
                        "stop_delay_s": stopped - requested,
                        "residual_volume_ml": None if observed is None else final - observed,
                        "passed": .85 - 1e-12 <= fraction <= .95 + 1e-12 and not row["spill"]})
    return {"trial_count": len(rows), "target_volume_fraction": .9,
            "allowed_fraction": [.85, .95], "passed": bool(rows) and all(r["passed"] for r in results),
            "trials": results, "independently_verified": False, "source": "operator_entered"}
