#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
for camera_python in .venv/bin/python .venv-training/bin/python; do
  [[ -x "$camera_python" ]] || { echo "Missing environment: $camera_python" >&2; exit 2; }
  "$camera_python" -m pip install --only-binary=:all: -r requirements/camera-orbbec.txt
  "$camera_python" -m pip check
done
bash scripts/camera.sh check
echo 'SDK ready. USB attachment and physical camera checks are separate; no firmware was changed.'
