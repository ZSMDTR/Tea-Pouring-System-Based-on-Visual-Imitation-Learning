#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(id -u)" == 0 ]] || {
    echo 'Run with system configuration permission: sudo bash scripts/configure_camera_permissions.sh' >&2
    exit 2
}
camera_rule_source=$(env -u PYTHONPATH -u PYTHONHOME .venv/bin/python - <<'PY'
from importlib.metadata import distribution
print(distribution('pyorbbecsdk2').locate_file('pyorbbecsdk/shared/99-obsensor-libusb.rules'))
PY
)
[[ -f "$camera_rule_source" ]] || { echo 'Bundled Orbbec rules missing' >&2; exit 2; }
camera_rule_target=/etc/udev/rules.d/99-obsensor-libusb.rules
if [[ -f "$camera_rule_target" ]] && ! cmp -s "$camera_rule_source" "$camera_rule_target"; then
    mkdir -p artifacts/system-config-backups
    cp -- "$camera_rule_target" "artifacts/system-config-backups/99-obsensor-libusb.rules.$(date +%s)"
fi
install -m 0644 -- "$camera_rule_source" "$camera_rule_target"
udevadm control --reload-rules
echo 'Installed Orbbec USB access rules. They apply when the camera is connected; no firmware changed.'
