"""Fixed-scope CAN setup helper. No robot SDK, sockets or motor commands.

Called through pkexec with system Python -I; never accepts web-supplied arguments.
"""
import json
import subprocess

IP = '/usr/sbin/ip'
INTERFACE = 'can0'
BITRATE = 1_000_000


def read_state(run=subprocess.run):
    result = run([IP, '-details', '-json', 'link', 'show', INTERFACE],
                 capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError('未找到 can0，请检查 USB-CAN 适配器是否连接。')
    data = json.loads(result.stdout)[0]
    if data.get('linkinfo', {}).get('info_kind') != 'can':
        raise RuntimeError('can0 不是 CAN 接口，已停止操作。')
    can = data['linkinfo'].get('info_data', {})
    return {'up': 'UP' in data.get('flags', []),
            'bitrate': can.get('bittiming', {}).get('bitrate'),
            'bus_state': can.get('state', 'UNKNOWN')}


def ready(state):
    return state['up'] and state['bitrate'] == BITRATE and state['bus_state'] in ('ERROR-ACTIVE', 'ERROR-WARNING', 'ERROR-PASSIVE')


def start_can(run=subprocess.run):
    before = read_state(run)
    if before['up']:
        if ready(before):
            return {'message': 'CAN 已启用，无需重复启动。', 'state': before}
        raise RuntimeError('CAN 已被启用，但参数或总线状态异常；未重启通道。请先结束使用它的程序并检查配置。')
    for args in [[IP, 'link', 'set', 'dev', INTERFACE, 'type', 'can', 'bitrate', str(BITRATE)],
                 [IP, 'link', 'set', 'dev', INTERFACE, 'up']]:
        result = run(args, capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError('启动 CAN 失败：' + (result.stderr.strip() or '系统拒绝操作'))
    after = read_state(run)
    if not ready(after):
        raise RuntimeError('启动后检查未通过，请刷新检查通道状态。')
    return {'message': 'CAN 已启动：can0 · 1 Mbps。尚未连接或使能电机。', 'state': after}


if __name__ == '__main__':
    try:
        print(json.dumps(start_can(), ensure_ascii=False))
    except (RuntimeError, OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
