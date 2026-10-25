#!/usr/bin/env bash
B601_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HF_LEROBOT_CALIBRATION="$B601_ROOT/calibration"
export B601_LEROBOT_PYTHON="$B601_ROOT/.venvs/lerobot/bin/python"
export B601_OPENPI_PYTHON="$B601_ROOT/openpi/.venv/bin/python"
export B601_FULLSAVE_PYTHON="$B601_ROOT/openpi_fullsave/.venv/bin/python"
