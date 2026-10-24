#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
# Avoid OpenCV thread oversubscription during simultaneous WSL camera capture.
export OPENCV_FOR_THREADS_NUM=1
action="${1:-check}"
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
  check|list|capture)
    exec .venv/bin/python scripts/camera_orbbec.py "$action" "$@"
    ;;
  lerobot|test)
    [[ -x .venv-training/bin/python ]] || { echo "Training environment missing" >&2; exit 2; }
    export LD_LIBRARY_PATH="$PWD/.venv-training/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    if [[ "$action" == test ]]; then
      exec .venv-training/bin/python scripts/check_tests.py --pattern test_orbbec_integration.py \
        --require-no-skips --output artifacts/camera-plugin-tests.json
    fi
    exec .venv-training/bin/python scripts/lerobot_orbbec.py "$@"
    ;;
  *) echo "Usage: bash scripts/camera.sh check|list|capture|test|lerobot [options]" >&2; exit 2 ;;
esac
