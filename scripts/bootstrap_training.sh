#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
training_commit=ae2b57f12223c8c5184fcd814eb63cd9ce7de203
if [[ ! -d vendor/lerobot/.git ]]; then
    mkdir -p vendor
    git init vendor/lerobot
    git -C vendor/lerobot remote add origin https://github.com/huggingface/lerobot.git
    git -C vendor/lerobot fetch --depth 1 origin "$training_commit"
    git -C vendor/lerobot checkout --detach FETCH_HEAD
fi
[[ "$(git -C vendor/lerobot rev-parse HEAD)" == "$training_commit" ]] || {
    echo "Existing LeRobot checkout differs from the required commit; preserve and inspect it." >&2
    exit 2
}
if [[ ! -x .venv-training/bin/python ]]; then
    conda_bin="$(command -v conda || true)"
    if [[ -z "$conda_bin" && -x "$HOME/miniconda3/bin/conda" ]]; then
        conda_bin="$HOME/miniconda3/bin/conda"
    fi
    [[ -n "$conda_bin" ]] || { echo "Existing Miniconda is required." >&2; exit 2; }
    if [[ -f requirements/lock-training-conda.txt ]]; then
        "$conda_bin" create --prefix "$PWD/.venv-training" --yes \
            --file requirements/lock-training-conda.txt
    else
        "$conda_bin" create --prefix "$PWD/.venv-training" --override-channels \
            -c conda-forge --yes python=3.12 pip ffmpeg=7
    fi
fi
.venv-training/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Expected Python 3.12"'
.venv-training/bin/python -m pip install --disable-pip-version-check \
    --index-url https://download.pytorch.org/whl/cpu --no-deps 'torchcodec==0.10.0'
lock_args=(-c requirements/training-constraints.txt)
if [[ -f requirements/lock-training-cu128.txt ]]; then
    lock_args+=(-r requirements/lock-training-cu128.txt -c requirements/lock-training-cu128.txt)
fi
.venv-training/bin/python -m pip install --disable-pip-version-check \
    "${lock_args[@]}" -e './vendor/lerobot[rebot,training,core_scripts]' -e .
bash scripts/training.sh check
echo 'Run bash scripts/training.sh verify to validate data, training, and checkpoint reload.'
