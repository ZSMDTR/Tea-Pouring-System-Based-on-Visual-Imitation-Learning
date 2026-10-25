#!/usr/bin/python3
"""Desktop entrypoint; reuses an existing workbench process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('HF_LEROBOT_CALIBRATION', str(ROOT.parent / 'calibration'))
RUNTIME = ROOT / '.runtime'
RUNTIME.mkdir(mode=0o700, exist_ok=True)

def running_url():
    try:
        d = json.loads((RUNTIME / 'server.json').read_text())
        u = urlparse(d['url'])
        if u.scheme != 'http' or u.hostname != '127.0.0.1':
            return None
        with urlopen(d['url'] + '/api/status', timeout=4) as response:
            if json.load(response).get('app') == 'b601-workbench-v1':
                return d['url']
    except (OSError, ValueError, KeyError):
        pass
    return None

url = running_url()
if not url:
    with (RUNTIME / 'server.log').open('ab') as log:
        subprocess.Popen([sys.executable, '-B', str(ROOT / 'app.py')], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)
    for _ in range(50):
        time.sleep(0.1)
        url = running_url()
        if url:
            break
if not url:
    raise SystemExit('启动失败，请查看 ' + str(RUNTIME / 'server.log'))
print(url)
if '--no-browser' not in sys.argv:
    subprocess.Popen(['/usr/bin/xdg-open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
