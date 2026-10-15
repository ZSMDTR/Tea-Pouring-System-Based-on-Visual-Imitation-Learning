#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset PYTHONPATH PYTHONHOME
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
  joint-plan)
    [[ $# -eq 9 ]] || {
      echo "Usage: bash scripts/control_linux.sh joint-plan CONFIG DEG1 DEG2 DEG3 DEG4 DEG5 DEG6 DEG7"
      exit 2
    }
    .venv/bin/python scripts/run_joint_target.py --config "$2" --target-deg "${@:3:7}"
    ;;
  joint-run)
    [[ $# -eq 9 ]] || {
      echo "Usage: bash scripts/control_linux.sh joint-run CONFIG DEG1 DEG2 DEG3 DEG4 DEG5 DEG6 DEG7"
      exit 2
    }
    [[ -x .venv-training/bin/python ]] || {
      echo "Training/LeRobot environment missing: run bash scripts/bootstrap_training.sh"
      exit 2
    }
    env -u PYTHONPATH -u PYTHONHOME .venv-training/bin/python scripts/run_joint_target.py \
      --config "$2" --target-deg "${@:3:7}" --execute
    ;;
  can-up)
    [[ "$channel" =~ ^can[0-9]+$ ]] || { echo "Expected an existing can0/can1 interface"; exit 2; }
    .venv/bin/python -c 'import json,subprocess,sys; links=json.loads(subprocess.check_output(["ip","-j","link","show","dev",sys.argv[1]])); sys.exit(0 if links[0].get("link_type")=="can" else 2)' "$channel"
    sudo ip link set "$channel" down
    sudo ip link set "$channel" type can bitrate 1000000
    sudo ip link set "$channel" up
    ip -details link show "$channel"
    ;;
  *) echo "Usage: bash scripts/control_linux.sh doctor|probe|gateway|can-up|joint-plan|joint-run"; exit 2 ;;
esac
