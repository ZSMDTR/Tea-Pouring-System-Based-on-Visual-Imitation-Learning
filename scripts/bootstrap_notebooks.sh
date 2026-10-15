#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
for notebook_environment in main training; do
    if [[ "$notebook_environment" == main ]]; then
        notebook_python=.venv/bin/python
        notebook_lock=requirements/lock-linux-cu128.txt
        notebook_kernel=rebot-tea
        notebook_label='reBot Tea - Python 3.10'
    else
        notebook_python=.venv-training/bin/python
        notebook_lock=requirements/lock-training-cu128.txt
        notebook_kernel=rebot-training
        notebook_label='reBot ACT - Python 3.12 CUDA'
    fi
    [[ -x "$notebook_python" ]] || { echo "Missing $notebook_python" >&2; exit 2; }
    "$notebook_python" -m pip install -r requirements/notebook.txt -c "$notebook_lock"
    "$notebook_python" -m pip check
    "$notebook_python" -m ipykernel install --user --name "$notebook_kernel" \
        --display-name "$notebook_label"
    "$notebook_python" - "$notebook_kernel" "$notebook_environment" <<'PY'
import json
import sys
from pathlib import Path
from jupyter_core.paths import jupyter_data_dir
path = Path(jupyter_data_dir()) / "kernels" / sys.argv[1] / "kernel.json"
data = json.loads(path.read_text())
data["env"] = {"PYTHONPATH": "", "PYTHONHOME": ""}
if sys.argv[2] == "training":
    data["env"]["LD_LIBRARY_PATH"] = str(Path.cwd() / ".venv-training/lib")
path.write_text(json.dumps(data, indent=2) + "\n")
print(f"Registered isolated kernel: {path}")
PY
done
