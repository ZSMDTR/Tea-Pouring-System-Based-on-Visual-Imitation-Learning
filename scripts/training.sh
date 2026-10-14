#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
[[ -x .venv-training/bin/python ]] || { echo "Run bash scripts/bootstrap_training.sh first." >&2; exit 2; }
export LD_LIBRARY_PATH="$PWD/.venv-training/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 WANDB_MODE=disabled
action="${1:-check}"
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
    check|generate|validate|smoke|verify)
        exec .venv-training/bin/python scripts/training_tools.py "$action" "$@"
        ;;
    test)
        exec .venv-training/bin/python scripts/check_tests.py --pattern test_training_integration.py \
            --require-no-skips --output artifacts/training-tests.json
        ;;
    *) echo 'Usage: bash scripts/training.sh check|generate|validate|smoke|verify|test [options]' >&2; exit 2 ;;
esac
