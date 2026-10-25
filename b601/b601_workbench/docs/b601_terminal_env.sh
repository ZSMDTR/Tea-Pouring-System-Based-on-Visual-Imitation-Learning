# Bash helpers for the current local B601 workbench API.
# Loading this file defines functions only; it opens no devices or models.
export B601_WORKBENCH_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export HF_LEROBOT_CALIBRATION="${HF_LEROBOT_CALIBRATION:-$(dirname "$B601_WORKBENCH_ROOT")/calibration}"

b601_url() {
  /usr/bin/python3 - <<'PY'
import json, os
from pathlib import Path
from urllib.parse import urlparse
try:
    root = Path(os.environ['B601_WORKBENCH_ROOT'])
    url = json.loads((root/'.runtime/server.json').read_text())['url']
    parsed = urlparse(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or parsed.username or parsed.password or not parsed.port
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('工作台地址不是有效的本机地址。')
    print(url.rstrip('/'))
except (OSError, ValueError, KeyError) as exc:
    raise SystemExit('无法读取工作台地址，请先启动服务：'+str(exc))
PY
}

b601_api() {
  /usr/bin/python3 - "$@" <<'PY'
import json, os, sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
try:
    if len(sys.argv) < 3:
        raise ValueError('用法：b601_api GET|POST /api/接口 [字段=值 ...]')
    method, path = sys.argv[1].upper(), sys.argv[2]
    if method not in ('GET','POST') or not path.startswith('/api/') or '#' in path:
        raise ValueError('仅支持本机工作台 /api/ 下的 GET / POST。')
    root = Path(os.environ['B601_WORKBENCH_ROOT'])
    base = json.loads((root/'.runtime/server.json').read_text())['url'].rstrip('/')
    parsed = urlparse(base)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or parsed.username or parsed.password or not parsed.port
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('工作台地址不是有效的本机地址。')
    values = {}
    for arg in sys.argv[3:]:
        if '=' not in arg:
            raise ValueError('参数需要写成 字段=值：'+arg)
        key, value = arg.split('=',1)
        if not key or key in values:
            raise ValueError('字段为空或重复：'+key)
        try: values[key] = json.loads(value)
        except ValueError: values[key] = value
    # Confirm the service and fetch the current token; never print the token.
    with urlopen(base+'/api/status',timeout=15) as response:
        status = json.load(response)
    if status.get('app') != 'b601-workbench-v1':
        raise ValueError('此端口不是 B601 工作台。')
    headers = {'Origin':base}
    body = None
    if method == 'POST':
        headers.update({'Content-Type':'application/json','X-Workbench-Token':status['csrf_token']})
        if path == '/api/can/start':
            if values: raise ValueError('CAN 启动接口不接受参数。')
            body = b''
        else:
            body = json.dumps(values,ensure_ascii=False,allow_nan=False).encode()
    elif values:
        path += ('&' if '?' in path else '?')+urlencode(values)
    with urlopen(Request(base+path,data=body,headers=headers,method=method),timeout=90) as response:
        result = json.load(response)
    if isinstance(result,dict): result.pop('csrf_token',None)
    print(json.dumps(result,ensure_ascii=False,indent=2))
except HTTPError as exc:
    try: detail = json.loads(exc.read()).get('error',str(exc))
    except (ValueError,AttributeError): detail = str(exc)
    print('工作台拒绝操作：'+str(detail),file=sys.stderr)
    sys.exit(1)
except (OSError,URLError,ValueError,KeyError,TypeError) as exc:
    print('命令未完成：'+str(exc),file=sys.stderr)
    sys.exit(1)
PY
}

b601_value() {
  /usr/bin/python3 -c '
import json,sys
try:
    value=json.load(sys.stdin)
    for key in sys.argv[1].split("."):
        value=value[int(key)] if isinstance(value,list) else value[key]
    print(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))
except (ValueError,KeyError,IndexError,TypeError) as exc:
    print("读取返回字段失败，请先查看上一条命令的完整结果："+str(exc),file=sys.stderr)
    sys.exit(1)
' "$1"
}

b601_heartbeat() {
  local b601_ident="${1:-}" b601_response b601_active
  if [[ ! "$b601_ident" =~ ^[0-9a-f]{32}$ ]]; then
    echo '心跳需要本次设备会话的 32 位 ID；请先确认启动命令成功。' >&2
    return 1
  fi
  while :; do
    b601_response=$(b601_api POST /api/record/heartbeat id="$b601_ident") || return 1
    b601_active=$(printf '%s\n' "$b601_response" | b601_value active) || return 1
    if [[ "$b601_active" != true ]]; then return 0; fi
    sleep 2
  done
}
