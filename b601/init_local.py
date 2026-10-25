#!/usr/bin/env python3
"""Create local configuration without overwriting existing machine settings."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKBENCH = ROOT / 'b601_workbench'
os.umask(0o077)


def create(template: Path, target: Path) -> None:
    if target.exists():
        print(f'Kept existing {target.relative_to(ROOT)}')
        return
    value = json.loads(template.read_text())

    def expand(item):
        if isinstance(item, str):
            return item.replace('${B601_ROOT}', str(ROOT))
        if isinstance(item, list):
            return [expand(part) for part in item]
        if isinstance(item, dict):
            return {key: expand(part) for key, part in item.items()}
        return item

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(expand(value), ensure_ascii=False, indent=2) + '\n')
    print(f'Created {target.relative_to(ROOT)}')


def main() -> None:
    if not (ROOT / 'openpi' / 'pyproject.toml').exists():
        raise SystemExit('Run bootstrap_sources.sh first.')
    for relative in ('datasets/lerobot/local', 'models', 'calibration',
                     'openpi/checkpoints/pi05_b601'):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)
    create(WORKBENCH / 'config.example.json', WORKBENCH / 'config.json')
    create(WORKBENCH / 'capture_profile.example.json', WORKBENCH / 'capture_profile.json')
    create(WORKBENCH / 'data/official_center_ready.example.json',
           WORKBENCH / 'data/official_center_ready.json')
    print('Select the real serial port and camera serials in capture_profile.json.')
    print('The ready-pose preset stays blocked until its calibration and URDF hashes are verified locally.')


if __name__ == '__main__':
    main()
