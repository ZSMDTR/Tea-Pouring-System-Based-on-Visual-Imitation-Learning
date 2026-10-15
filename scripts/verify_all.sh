#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Keep system ROS packages inherited from the shell out of the project environments.
unset PYTHONPATH PYTHONHOME
[[ $# -le 1 ]] || { echo "Usage: bash scripts/verify_all.sh [--with-training]" >&2; exit 2; }
with_training=false
case "${1:-}" in
    "") ;;
    --with-training)
        [[ $# -eq 1 ]] || { echo "Usage: bash scripts/verify_all.sh [--with-training]" >&2; exit 2; }
        with_training=true
        [[ -x .venv-training/bin/python ]] || {
            echo "Training environment missing: run bash scripts/bootstrap_training.sh" >&2
            exit 2
        }
        ;;
    *) echo "Usage: bash scripts/verify_all.sh [--with-training]" >&2; exit 2 ;;
esac
.venv/bin/python -m pip check
.venv/bin/python scripts/check_environment.py --profile gpu
.venv/bin/ruff check src scripts tests
.venv/bin/python scripts/check_tests.py
.venv/bin/python scripts/generate_demo.py
.venv/bin/python -m rebot_tea
.venv/bin/python -m rebot_tea.replay
.venv/bin/python scripts/replay_pouring.py
.venv/bin/python scripts/verify_joints.py
.venv/bin/python scripts/plot_replay.py
.venv/bin/python scripts/verify_vision.py --device cpu
.venv/bin/python scripts/verify_vision.py --device cuda --train-step
if test -x .venv-kinematics/bin/python; then
    .venv-kinematics/bin/python -m pip check
    .venv-kinematics/bin/python scripts/check_kinematics.py --output artifacts/kinematics-check.json
fi
if [[ "$with_training" == true ]]; then
    bash scripts/training.sh test
    bash scripts/camera.sh check
    bash scripts/camera.sh test
    bash scripts/training.sh verify
    .venv/bin/python scripts/report_prehardware.py
fi
