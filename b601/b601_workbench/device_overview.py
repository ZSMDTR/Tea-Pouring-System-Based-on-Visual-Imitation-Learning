"""Read-only device inventory. Never open a serial port, camera or motor driver."""
import json
import os
import time
from pathlib import Path


def read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def overview(profile_path, can, job, *, dev=Path('/dev'), usb=Path('/sys/bus/usb/devices'),
             calibration=None):
    calibration = Path(calibration or os.environ.get('HF_LEROBOT_CALIBRATION') or
                       Path.home() / '.cache/huggingface/lerobot/calibration')
    profile = json.loads(Path(profile_path).read_text())
    ports = sorted(str(p) for pattern in ('ttyUSB*', 'ttyACM*', 'serial/by-id/*') for p in dev.glob(pattern))
    cameras = []
    for path in sorted(usb.glob('*/product')):
        name = read_text(path)
        if 'orbbec' in name.lower() or 'gemini' in name.lower():
            cameras.append({'name': name, 'serial': read_text(path.parent / 'serial'),
                            'usb_path': path.parent.name})
    camera = profile.get('camera', {})
    selected = str(camera.get('serial_number_or_name', ''))
    wrist = next((c for c in cameras if selected and selected in (c['serial'], c['name'])), None)
    files = []
    for role, folder, identity in [
        ('引导臂', 'teleoperators/rebot_arm_102_leader', profile.get('leader_id', '')),
        ('从臂', 'robots/seeed_b601_rs_follower', profile.get('robot_id', '')),
    ]:
        # IDs come from the local profile; don't follow arbitrary paths.
        path = calibration / folder / (identity + '.json')
        inside = path.resolve().is_relative_to(calibration.resolve())
        exists = inside and path.is_file()
        files.append({'role': role, 'id': identity, 'exists': exists,
                      'path': str(path) if inside else '', 'modified_at': path.stat().st_mtime if exists else None})
    leader = profile.get('leader_port', '')
    return {'checked_at': time.time(), 'profile': profile, 'ports': ports, 'cameras': cameras,
            'leader_present': bool(leader and Path(leader).exists()),
            'wrist': wrist, 'calibrations': files, 'can': can,
            'session': {k: job.get(k) for k in ('id', 'active', 'mode', 'state', 'message', 'cleanup_ok')}}
