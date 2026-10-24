#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
[[ -x .venv-training/bin/python ]] || { echo "Run bash scripts/bootstrap_training.sh first." >&2; exit 2; }
export LD_LIBRARY_PATH="$PWD/.venv-training/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 WANDB_MODE=disabled
exec .venv-training/bin/python -B scripts/gamepad_teleop.py "$@"
