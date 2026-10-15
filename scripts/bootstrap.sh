#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r requirements/lock-linux-cu128.txt
.venv/bin/python scripts/prepare_model.py
.venv/bin/python scripts/generate_demo.py
.venv/bin/python scripts/check_environment.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m rebot_tea --config config/simulation.json
.venv/bin/python -m rebot_tea.replay
