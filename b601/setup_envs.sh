#!/usr/bin/env bash
# Rebuild isolated environments after bootstrap_sources.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY310="${B601_PYTHON310:-python3.10}"
PY311="${B601_PYTHON311:-python3.11}"
UV="$ROOT/.venvs/tools/bin/uv"
CAPTURE="$ROOT/.venvs/lerobot/bin/python"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"

[[ -f "$ROOT/openpi/uv.lock" && -f "$ROOT/openpi_fullsave/uv.lock" ]] || {
  echo 'Run bootstrap_sources.sh first.' >&2; exit 1;
}
"$PY310" -c 'import sys; assert sys.version_info[:2] == (3, 10)' || {
  echo 'B601_PYTHON310 must be Python 3.10.' >&2; exit 1;
}
"$PY311" -c 'import sys; assert sys.version_info[:2] == (3, 11)' || {
  echo 'B601_PYTHON311 must be Python 3.11.' >&2; exit 1;
}

if [[ ! -x "$UV" ]]; then
  "$PY311" -m venv "$ROOT/.venvs/tools"
  "$ROOT/.venvs/tools/bin/python" -m pip install 'uv==0.9.5'
fi

for name in openpi openpi_fullsave; do
  (cd "$ROOT/$name" && "$UV" sync --locked --no-dev --python "$PY311")
  "$ROOT/$name/.venv/bin/python" -c \
    'import importlib.metadata as m; print("jax", m.version("jax"), "orbax", m.version("orbax-checkpoint"), "torch", m.version("torch"))'
done

if [[ ! -x "$CAPTURE" ]]; then
  "$UV" venv --python "$PY310" "$ROOT/.venvs/lerobot"
fi
"$UV" pip install --python "$CAPTURE" --no-deps \
  --index-url https://download.pytorch.org/whl/cu128 \
  'torch==2.7.1+cu128' 'torchvision==0.22.1+cu128'
"$UV" pip install --python "$CAPTURE" --no-deps \
  -r "$ROOT/requirements/capture-pinned.txt"
"$UV" pip install --python "$CAPTURE" --no-deps \
  -e "$ROOT/lerobot" \
  -e "$ROOT/lerobot-robot-seeed-b601" \
  -e "$ROOT/openpi/packages/openpi-client"

"$PY311" - "$UV" "$CAPTURE" <<'PY'
import re
import subprocess
import sys

check = subprocess.run([sys.argv[1], 'pip', 'check', '--python', sys.argv[2]],
                       capture_output=True, text=True)
report = check.stdout + check.stderr
print(report, end='')
conflicts = re.findall(r'^The package `([^`]+)` requires `([^`]+)`, but `([^`]+)` is installed$',
                       report, re.MULTILINE)
known = {'opencv-python', 'opencv-python-headless', 'rerun-sdk'}
if check.returncode == 0 and not conflicts:
    print('Capture dependencies: no conflicts')
elif (check.returncode == 1 and len(conflicts) == 3
      and {name for name, _, _ in conflicts} == known
      and all('numpy' in requirement and installed == '1.26.4'
              for _, requirement, installed in conflicts)):
    print('Capture dependencies: only the three documented source NumPy conflicts')
else:
    raise SystemExit('Capture dependency check found new or changed conflicts')
PY

"$CAPTURE" - <<'PY'
import importlib.util
import sys
for name in ('lerobot.cameras.orbbec.configuration_orbbec',
             'lerobot_robot_seeed_b601.config_seeed_b601_rs_follower',
             'lerobot_teleoperator_rebot_arm_102.config_rebot_arm_102_leader',
             'pyorbbecsdk'):
    if importlib.util.find_spec(name) is None:
        raise SystemExit(f'Missing module: {name}')
print('Capture environment ready:', sys.executable)
PY
