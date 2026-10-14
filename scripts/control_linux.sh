#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
action="${1:-doctor}"
channel="${2:-}"
case "$action" in
  doctor)
    args=(scripts/doctor_control.py --output artifacts/control-linux.json)
    if [[ -n "$channel" ]]; then args+=(--channel "$channel"); fi
    .venv/bin/python "${args[@]}"
    ;;
  probe)
    [[ -n "$channel" ]] || { echo "Specify the confirmed SocketCAN interface"; exit 2; }
    .venv/bin/python -m rebot_tea.connection --channel "$channel" --output artifacts/motors-linux.json
    ;;
  gateway)
    [[ -n "$channel" ]] || { echo "Specify the confirmed SocketCAN interface"; exit 2; }
    .venv/bin/python -m motorbridge.gateway --bind 127.0.0.1:9002 --vendor robstride --channel "$channel"
    ;;
  can-up)
    [[ "$channel" =~ ^can[0-9]+$ ]] || { echo "Expected an existing can0/can1 interface"; exit 2; }
    .venv/bin/python -c 'import json,subprocess,sys; links=json.loads(subprocess.check_output(["ip","-j","link","show","dev",sys.argv[1]])); sys.exit(0 if links[0].get("link_type")=="can" else 2)' "$channel"
    sudo ip link set "$channel" down
    sudo ip link set "$channel" type can bitrate 1000000
    sudo ip link set "$channel" up
    ip -details link show "$channel"
    ;;
  *) echo "Usage: bash scripts/control_linux.sh doctor|probe|gateway|can-up [can0]"; exit 2 ;;
esac
