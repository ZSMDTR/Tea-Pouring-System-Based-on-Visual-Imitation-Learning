"""Dependency-free checks for the explicitly described prehardware dataset contract."""
import math

from .joints import JOINT_NAMES, finite_number, joint_vector

FEATURE_NAMES = tuple(f"{name}.pos" for name in JOINT_NAMES)
IMAGE_KEY = "observation.images.scene"


def public_radians_to_degrees(values):
    """Public coordinates only; raw motor positions require measured direction/zero mapping."""
    return tuple(math.degrees(value) for value in joint_vector(values))


def public_degrees_to_radians(values):
    return tuple(math.radians(value) for value in joint_vector(values))


def validate_manifest(manifest):
    errors = []
    if manifest.get("schema_version") != 1 or manifest.get("synthetic") is not True:
        errors.append("This smoke dataset requires schema_version=1 and synthetic=true")
    if manifest.get("position_units") != "public_joint_degrees":
        errors.append("position_units must be public_joint_degrees")
    if manifest.get("joint_names") != list(JOINT_NAMES):
        errors.append("joint_names are missing or in the wrong order")
    for key in ("fps", "num_episodes", "frames_per_episode"):
        if type(manifest.get(key)) is not int or manifest[key] <= 0:
            errors.append(f"{key} must be a positive integer")
    train, val = manifest.get("train_episodes"), manifest.get("validation_episodes")
    if not isinstance(train, list) or not isinstance(val, list):
        errors.append("Episode splits must be lists")
    elif any(type(index) is not int for index in train + val):
        errors.append("Episode split identifiers must be integers")
    else:
        if not train or not val or set(train) & set(val):
            errors.append("Training and validation episodes must be nonempty and disjoint")
        if len(set(train + val)) != len(train + val):
            errors.append("Episode splits contain duplicates")
        count = manifest.get("num_episodes")
        if type(count) is int and set(train + val) != set(range(count)):
            errors.append("Episode splits must cover the entire dataset")
    if not finite_number(manifest.get("max_step_deg")) or manifest["max_step_deg"] <= 0:
        errors.append("max_step_deg must be a positive finite synthetic jump threshold")
    return errors


def validate_rows(rows, manifest):
    errors = validate_manifest(manifest)
    if errors:
        return {"passed": False, "errors": errors, "frames_checked": 0}
    fps = manifest["fps"]
    count = manifest["num_episodes"]
    seen = {index: [] for index in range(count)}
    previous = {}
    total = 0
    for global_index, row in enumerate(rows):
        total += 1
        prefix = f"frame {global_index}"
        episode, frame, timestamp = (row.get(key) for key in
                                     ("episode_index", "frame_index", "timestamp"))
        if type(episode) is not int or episode not in seen:
            errors.append(f"{prefix}: invalid episode_index")
            continue
        if type(frame) is not int or frame < 0 or not finite_number(timestamp):
            errors.append(f"{prefix}: invalid frame index/time")
            continue
        if abs(timestamp - frame / fps) > 1e-4:
            errors.append(f"{prefix}: timestamp does not match the recording frame rate")
        seen[episode].append(frame)
        last = previous.get(episode)
        if last and (frame != last[0] + 1 or timestamp <= last[1]):
            errors.append(f"{prefix}: frame/time order is not increasing")
        vectors = []
        for key in ("observation.state", "action"):
            try:
                vectors.append(joint_vector(row.get(key), key))
            except ValueError as error:
                errors.append(f"{prefix}: {error}")
        if len(vectors) == 2:
            if last and last[2] is not None:
                for key, current, old in zip(("state", "action"), vectors, last[2]):
                    if any(abs(a - b) > manifest["max_step_deg"] for a, b in zip(current, old)):
                        errors.append(f"{prefix}: abnormal {key} jump")
            previous[episode] = (frame, timestamp, vectors)
        else:
            previous[episode] = (frame, timestamp, None)
    for episode, indexes in seen.items():
        if indexes != list(range(manifest["frames_per_episode"])):
            errors.append(f"episode {episode}: missing, repeated or incorrectly ordered frames")
    return {"passed": not errors, "errors": errors, "frames_checked": total,
            "episodes_checked": count, "train_episodes": manifest["train_episodes"],
            "validation_episodes": manifest["validation_episodes"]}
