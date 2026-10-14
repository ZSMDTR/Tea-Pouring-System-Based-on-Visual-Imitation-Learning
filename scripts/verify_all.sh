#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
.venv/bin/python -m pip check
.venv/bin/python scripts/check_environment.py --profile gpu
.venv/bin/ruff check src scripts tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/generate_demo.py
.venv/bin/python -m rebot_tea
.venv/bin/python -m rebot_tea.replay
.venv/bin/python scripts/replay_pouring.py
.venv/bin/python scripts/plot_replay.py
.venv/bin/python scripts/verify_vision.py --device cpu
.venv/bin/python scripts/verify_vision.py --device cuda --train-step
if test -x .venv-kinematics/bin/python; then
    .venv-kinematics/bin/python scripts/check_kinematics.py --output artifacts/kinematics-check.json
fi
