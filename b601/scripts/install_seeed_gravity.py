#!/usr/bin/env python3
"""Locally obtain the pinned Seeed gravity controller; do not redistribute it."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'b601_workbench/vendor/seeed_gravity'
REPO = 'https://github.com/Seeed-Projects/reBotArm_control_py.git'
REVISION = '6415d43130d1'
FILES = {
    'example/10_gravity_compensation_lock.py': '10_gravity_compensation_lock.py',
    'reBotArm_control_py/controllers/gravity_compensation.py': 'gravity_compensation.py',
    'config/rebotarm_rs.yaml': 'rebotarm_rs.yaml',
}


def method(path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        help='existing checkout of the exact upstream revision')
    args = parser.parse_args()
    source = args.source
    if source is None:
        source = ROOT / '.cache/seeed-gravity-src'
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout',
                            REPO, str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'checkout', '--detach',
                            REVISION], check=True)
    if (source / '.git').exists():
        sha = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                                      text=True).strip()
        if not sha.startswith(REVISION):
            raise SystemExit(f'Wrong Seeed source revision: {sha}')
    manifest = json.loads((DEST / 'SOURCE.json').read_text())
    if manifest['revision'] != REVISION or manifest['repository'].rstrip('/') != REPO.removesuffix('.git'):
        raise SystemExit('Unexpected Seeed source manifest')
    for relative, expected in manifest['sha256'].items():
        actual = hashlib.sha256((source / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f'Seeed source hash mismatch: {relative}')

    lock = source / 'example/10_gravity_compensation_lock.py'
    gravity = source / 'reBotArm_control_py/controllers/gravity_compensation.py'
    methods = [
        method(lock, 'VelocityLockGravityCompensation', '_estimate_joint_velocity'),
        method(lock, 'VelocityLockGravityCompensation', '_update_lock_target'),
        method(lock, 'VelocityLockGravityCompensation', '_loop_cb'),
        method(gravity, 'GravityCompensation', '_transition_blend'),
    ]
    generated = ('# Generated locally from Seeed-Projects/reBotArm_control_py @ '
                 + REVISION + '. See SOURCE.json.\n'
                 + 'from __future__ import annotations\n'
                 + 'import math\nimport time\nimport numpy as np\n\n'
                 + 'class OfficialVelocityLock:\n'
                 + '\n\n'.join(textwrap.indent(ast.unparse(node), '    ')
                                for node in methods) + '\n')
    DEST.mkdir(parents=True, exist_ok=True)
    for relative, filename in FILES.items():
        target = DEST / filename
        content = (source / relative).read_bytes()
        if target.exists() and target.read_bytes() != content:
            raise SystemExit(f'Existing vendor file differs: {target}')
        target.write_bytes(content)
    target = DEST / 'control_law.py'
    if target.exists() and target.read_text() != generated:
        raise SystemExit(f'Existing vendor file differs: {target}')
    target.write_text(generated)
    print('Seeed gravity source obtained, hashes checked, and local adapter generated.')


if __name__ == '__main__':
    main()
