#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
[[ -x .venv/bin/python ]] || { echo 'Missing .venv; run scripts/bootstrap.sh' >&2; exit 2; }
exec .venv/bin/python scripts/audit_software.py "$@"
