"""Local B601 workbench. CAN setup is explicit; no robot imports or motor commands."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import fcntl
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
import secrets
import can_control
import recording
import library
import hashlib
import recycle
import inference
import checkpoint_catalog
import experiments
import experiment_data
import dataset_export
import modes
import replay
import device_overview
import device_settings
import workspace_state
import maintenance
import monitoring
from live_preview import stream_jpegs, view_folder

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('HF_LEROBOT_CALIBRATION', str(ROOT.parent / 'calibration'))
CONFIG = json.loads((ROOT / 'config.json').read_text())
RUNTIME = ROOT / '.runtime'
APP_ID = 'b601-workbench-v1'
CSRF_TOKEN = secrets.token_urlsafe(32)
CAN_LOCK = threading.Lock()
CAN_JOB = {'running': False, 'ok': None, 'message': ''}
EPISODE_INSPECTION_SLOTS = threading.BoundedSemaphore(2)
ACTION_REGISTER_LOCK = threading.Lock()


def setup_overview():
    try:
        state = can_control.read_state()
        can = {**state, 'ready': can_control.ready(state)}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        can = {'ready': False, 'error': str(exc)}
    return device_overview.overview(ROOT / 'capture_profile.json', can, recording.status())


def inspect_episode(name, index):
    root = dataset_path(name)
    if index < 0 or any(m['dataset'] == name and m['episode'] == index and m['trashed']
                        for m in library.snapshot()['members']):
        raise ValueError('示范不存在或已移入回收站。')
    job = recording.status()
    if job.get('active') and job.get('dataset') == name:
        raise ValueError('请等待这一条采集与保存完成后再检查。')
    if not EPISODE_INSPECTION_SLOTS.acquire(blocking=False):
        raise ValueError('正在检查其他示范，请稍后重试。')
    try:
        result = subprocess.run([CONFIG['lerobot_python'], '-B', str(ROOT / 'episode_inspection.py'),
                                 str(root), str(index)], capture_output=True, text=True, timeout=25)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise ValueError('示范检查未完成，请刷新后重试。') from None
        if result.returncode or data.get('error'):
            raise ValueError(data.get('error') or '无法读取这条示范。')
        return data
    finally:
        EPISODE_INSPECTION_SLOTS.release()

def can_job():
    with CAN_LOCK:
        return dict(CAN_JOB)

def run_can_start():
    ok = False
    message = '启动未完成，请刷新状态后重试。'
    try:
        state = can_control.read_state()
        if can_control.ready(state):
            message = 'CAN 已启用，无需重复启动。'
            ok = True
        elif state['up']:
            message = 'CAN 已启用但参数或总线状态异常，未重启通道。请先结束使用它的程序并检查配置。'
        else:
            result = subprocess.run(['/usr/bin/pkexec', '--disable-internal-agent', '/usr/bin/python3', '-I', str(ROOT / 'can_control.py')], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90)
            if result.returncode in (126, 127):
                message = '系统授权未完成或被取消。请在桌面授权弹窗中确认后重试；工作台不接收密码。'
            else:
                try:
                    data = json.loads(result.stdout)
                except ValueError:
                    data = {'error': result.stderr.strip()[-500:] or '系统未返回启动结果'}
                ok = result.returncode == 0 and 'error' not in data and can_control.ready(can_control.read_state())
                message = data.get('message' if ok else 'error', '启动后检查未通过，请刷新状态。')
    except subprocess.TimeoutExpired:
        message = '等待系统授权超时，请刷新状态后重试。'
    except (RuntimeError, OSError, ValueError, IndexError) as exc:
        message = str(exc)
    finally:
        with CAN_LOCK:
            CAN_JOB.update(running=False, ok=ok, message=message)

def begin_can_start():
    if CONFIG['can_interface'] != can_control.INTERFACE:
        raise ValueError('当前启动按钮仅支持已确认的 can0 / 1 Mbps 配置。')
    with CAN_LOCK:
        if CAN_JOB['running']:
            return dict(CAN_JOB)
        CAN_JOB.update(running=True, ok=None, message='正在启动 CAN；如出现系统授权弹窗，请在弹窗中确认。')
        result = dict(CAN_JOB)
    threading.Thread(target=run_can_start, daemon=True).start()
    return result

def dataset_path(name):
    if not isinstance(name,str) or not name:
        raise ValueError('请选择有效的数据集名称。')
    if name in recycle.deleted_names():
        raise ValueError('数据集已移入回收站或已彻底删除。')
    base = Path(CONFIG['dataset_root']).resolve()
    p = (base / name).resolve()
    if p.parent != base or not (p / 'meta/info.json').is_file():
        raise ValueError('数据集不存在或路径无效')
    return p

def datasets():
    result = []
    deleted = recycle.deleted_names()
    removed = {}
    for member in library.snapshot()['members']:
        if member['trashed']:
            removed[member['dataset']] = removed.get(member['dataset'],0)+1
    for p in sorted(Path(CONFIG['dataset_root']).glob('*/meta/info.json')):
        if p.parent.parent.name in deleted or p.parent.parent.name.startswith('.'):
            continue
        try:
            d = json.loads(p.read_text())
            if d['total_episodes'] > 0 and removed.get(p.parent.parent.name,0) >= d['total_episodes']:
                continue
            capture_status = '空记录' if not d['total_frames'] else '未登记回放验收'
            manifest = p.parent.parent / 'workbench_capture.json'
            if manifest.is_file():
                capture = json.loads(manifest.read_text())
                capture_status = '采集异常 · 需检查' if capture.get('state') == 'error' else '待实机 Replay 验收'
            export_meta=p.parent.parent/'workbench_export.json'
            exported=json.loads(export_meta.read_text()) if export_meta.is_file() else {}
            result.append({'name': p.parent.parent.name, 'path': str(p.parent.parent),
                           'simulation':(p.parent.parent/'workbench_simulation.json').is_file(),
                           'export_label':exported.get('collection_name'), 'export_test':exported.get('source_plan',{}).get('test_only',False),
                           'episodes': d['total_episodes'], 'frames': d['total_frames'],
                           'fps': d['fps'], 'tasks': d['total_tasks'], 'version': d['codebase_version'],
                           'removed_episodes': removed.get(p.parent.parent.name,0),
                           'status': capture_status})
        except (ValueError, KeyError, OSError) as exc:
            result.append({'name': p.parent.parent.name, 'error': str(exc)})
    return result

def readiness():
    checks = []
    for key, title in [('lerobot_python', '采集与机器人环境'), ('openpi_python', 'OpenPI 模型环境')]:
        ok = Path(CONFIG[key]).is_file()
        checks.append({'title': title, 'status': '已找到' if ok else '未找到', 'ok': ok,
                       'detail': '解释器路径存在，尚未执行设备或模型测试', 'path': CONFIG[key]})
    products = []
    for p in Path('/sys/bus/usb/devices').glob('*/product'):
        try:
            products.append(p.read_text().strip())
        except OSError:
            pass
    cameras = [s for s in products if 'orbbec' in s.lower() or 'gemini' in s.lower()]
    checks.append({'title': 'Gemini 2 相机', 'ok': bool(cameras), 'status': 'USB 已枚举' if cameras else '未识别到',
                   'detail': '、'.join(cameras) if cameras else 'USB 产品信息中未识别到相机；尚未打开视频流'})
    ports = sorted(str(p) for p in Path('/dev').glob('ttyUSB*'))
    checks.append({'title': '引导臂串口', 'ok': bool(ports), 'status': '发现候选串口' if ports else '未发现候选串口',
                   'detail': '、'.join(ports) + '（尚未确认设备身份）' if ports else '型号仍待核实；此检查不打开串口'})
    try:
        state = can_control.read_state()
        up, rate = state['up'], state['bitrate']
        checks.append({'id': 'can', 'title': '从臂 CAN 通道', 'ok': can_control.ready(state),
                       'status': '通道已启用' if up else '通道未启用',
                       'detail': f"can0 · 波特率 {rate or '未配置'} · {state['bus_state']}；尚未验证电机通信"})
    except (RuntimeError, OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        checks.append({'id': 'can', 'title': '从臂 CAN 通道', 'ok': False, 'status': '无法读取', 'detail': str(exc)})
    return checks

def models():
    roots = [Path(CONFIG['base_model'])]
    roots += [p.parent for p in Path(CONFIG['checkpoint_root']).glob('*/[0-9]*/FINAL_PARAMS_ONLY.txt')]
    full = {item['path'] for item in checkpoint_catalog.listing(CONFIG['checkpoint_root'])}
    roots += [Path(path) for path in full]
    rows = []
    for p in sorted(set(roots)):
        rows.append({'name': '原始 π0.5' if p == Path(CONFIG['base_model']) else p.parent.name,
                     'step': '' if p == Path(CONFIG['base_model']) else p.name,
                     'path': str(p), 'params_present': (p / 'params').is_dir(),
                     'assets': [str(a.relative_to(p / 'assets')) for a in (p / 'assets').rglob('norm_stats.json')],
                     'resume': '完整存档，可恢复训练' if str(p) in full else '仅推理参数，不能精确断点续训' if (p / 'FINAL_PARAMS_ONLY.txt').exists() else '基础权重'})
    return rows

def episodes(name, include_deleted=False):
    root = dataset_path(name)
    result = subprocess.run([CONFIG['lerobot_python'], '-B', str(ROOT / 'metadata_reader.py'), str(root)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('无法读取示范元信息：' + result.stderr[-600:])
    records = json.loads(result.stdout)
    removed = {m['episode'] for m in library.snapshot()['members'] if m['dataset']==name and m['trashed']}
    return records if include_deleted else [r for r in records if r['index'] not in removed]

def library_snapshot():
    result = library.snapshot()
    replays=replay.summaries();identities={}
    deleted = {r['dataset']:r['state'] for r in recycle.rows()}
    for member in result['members']:
        member['source_deleted'] = deleted.get(member['dataset'])
        if member['source_deleted']:
            member.update(trashed=1,capture_state='deleted')
            continue
        try:
            root = dataset_path(member['dataset'])
            manifest = root / 'workbench_capture.json'
            if manifest.is_file():
                capture = json.loads(manifest.read_text())
                member['frames'] = capture.get('frames',member['frames'])
                member['capture_state'] = capture.get('state')
            else:
                member['capture_state'] = 'legacy' if member['frames'] else 'pending'
        except (OSError,ValueError):
            member['capture_state'] = 'unavailable'
        accepted=replays.get((member['dataset'],member['episode']))
        if accepted:
            try:
                if member['dataset'] not in identities:identities[member['dataset']]=replay.source_identity(dataset_path(member['dataset']))
                if identities[member['dataset']]!=accepted['source_identity']:accepted={**accepted,'status':'stale','message':'源文件已变化，请重新 Replay。'}
            except (OSError,ValueError):accepted={**accepted,'status':'stale'}
            member['replay']=accepted
    removed = {(m['dataset'],m['episode']) for m in result['members'] if m['trashed']}
    for mixture in result['mixtures']:
        affected = [f"{m['dataset']} / 示范 {m['episode']+1}" for s in mixture['sources'] for m in s['episodes'] if m['dataset'] in deleted or (m['dataset'],m['episode']) in removed]
        mixture['unavailable_sources'] = affected
    projects=workspace_state.listing(result['collections'])
    ids={c['id'] for c in result['collections'] if projects['bindings'].get(c['id'])==projects['active']}
    result['collections']=[c for c in result['collections'] if c['id'] in ids]
    result['members']=[m for m in result['members'] if m['collection'] in ids]
    result['mixtures']=[m for m in result['mixtures'] if all(s['id'] in ids for s in m['sources'])]
    result['project']=projects
    return result

def visible_record_status():
    job = recording.status()
    if job.get('action_name') and job.get('saved') and job.get('state')=='completed' and not job.get('active'):
        with ACTION_REGISTER_LOCK:
            folder=recording.current_dir()
            if folder and not (folder/'action_registered.json').exists():
                try:
                    root=dataset_path(job['dataset'])
                    item=workspace_state.save_action({'name':job['action_name'],'dataset':job['dataset'],'episode':0},replay.source_identity(root),job.get('action_project'))
                    recording.write_json(folder/'action_registered.json',item)
                except (ValueError,OSError) as exc:
                    job['action_catalog_error']=str(exc)
    if job.get('dataset') in recycle.deleted_names() or any(m['dataset']==job.get('dataset') and m['episode']==job.get('episode',0) and m['trashed'] for m in library.snapshot()['members']):
        job.update(saved=False,frames=0,message='这条数据已删除。可在统一回收站查看。')
    return job

def recycle_action(action,data):
    if not isinstance(data,dict) or not isinstance(data.get('dataset'),str):
        raise ValueError('请指定原始数据集。')
    with experiments.LOCK, recording.LOCK, recycle.LOCK:
        if experiments.status().get('active'):
            raise ValueError('请等数据准备或训练结束后再整理、删除或恢复。')
        if recording.status().get('active'):
            raise ValueError('请等当前采集与保存结束后再删除或恢复。')
        name=data['dataset'];root=CONFIG['dataset_root']
        if action=='trash':return recycle.trash(root,name)
        if action=='restore':
            if 'episode' in data:
                return library.update({'dataset':name,'episode':data['episode'],'trashed':False})
            return recycle.restore(root,name)
        if action=='plan':
            if name not in recycle.deleted_names():
                path = recycle.source(root,name)
                info = json.loads((path/'meta/info.json').read_text())
                count = sum(1 for m in library.snapshot()['members'] if m['dataset']==name and m['trashed'])
                if info['total_episodes'] <= 0 or count < info['total_episodes']:
                    raise ValueError('仍有在库示范，请先明确将整个原始数据集移入回收站。')
                recycle.trash(root,name)
            return recycle.plan(root,name)
        if action=='purge':return recycle.purge(root,name,data.get('token'),data.get('confirmation'))
        raise ValueError('未知回收站操作。')

def describe_source(name, index, cache=None):
    if cache is not None and name in cache:
        base = cache[name]
        ep = next((e for e in base['records'] if e['index']==index),None)
        if not ep or not ep['frames']:
            raise ValueError('示范不存在或尚未保存完成。')
        return {**base['description'],'episode':ep}
    root = dataset_path(name)
    info = json.loads((root / 'meta/info.json').read_text())
    records = episodes(name)
    ep = next((e for e in records if e['index'] == index), None)
    if not ep or not ep['frames']:
        raise ValueError('示范不存在或尚未保存完成。')
    manifest = root / 'workbench_capture.json'
    capture = json.loads(manifest.read_text()) if manifest.is_file() else {}
    if manifest.is_file() and (not capture.get('saved') or capture.get('state') != 'completed'):
        raise ValueError('采集异常的示范不能加入训练组合，请先检查。')
    profile = capture.get('profile',{})
    signature = {'fps':info['fps'], 'robot':info.get('robot_type'), 'features':info['features'],
                 'camera':profile.get('camera'), 'robot_config':profile.get('robot_config'),
                 'secondary_camera':profile.get('secondary_camera') if profile.get('secondary_enabled') else None,
                 'leader_config':profile.get('leader_config')}
    fingerprint = hashlib.sha256()
    metadata = sorted((root/'meta').rglob('*')) + ([manifest] if manifest.is_file() else [])
    for path in metadata:
        if path.is_file():
            fingerprint.update(str(path.relative_to(root)).encode())
            fingerprint.update(path.read_bytes())
    description = {'root':str(root),'episode':ep, 'metadata_sha256':fingerprint.hexdigest(),
            'signature':signature, 'replay':'unverified', 'provenance_known':bool(profile)}
    if cache is not None:
        cache[name] = {'records':records,'description':description}
    return description

def library_action(action, data):
    if not isinstance(data,dict):
        raise ValueError('请求格式无效。')
    if action == 'create':
        group=library.create(data.get('name'),data.get('task'))
        workspace_state.bind_collection(group['id'])
        return group
    if action in ('batch','collection'):
        with experiments.LOCK, recording.LOCK, recycle.LOCK:
            require_idle()
            return library.batch_update(data) if action=='batch' else library.update_collection(data)
    if action == 'import':
        group = library.collection(data.get('collection'))
        name = data.get('dataset')
        if not isinstance(name,str):
            raise ValueError('请选择原始数据集。')
        current = recording.status()
        if current.get('active') and current.get('dataset') == name:
            raise ValueError('请等当前采集保存完成。')
        return library.add_members(group['id'],name,episodes(name))
    if action == 'update':
        current = recording.status()
        with experiments.LOCK, recording.LOCK, recycle.LOCK:
            if experiments.status().get('active'):
                raise ValueError('请等数据准备或训练结束后再整理。')
            if recording.status().get('active'):
                raise ValueError('请等当前采集与保存结束后再整理。')
            result = library.update(data)
            if data.get('trashed') is True and (Path(CONFIG['dataset_root'])/data['dataset']/'meta/info.json').is_file():
                try:
                    root = dataset_path(data['dataset'])
                    info = json.loads((root/'meta/info.json').read_text())
                    removed = sum(1 for m in library.snapshot()['members'] if m['dataset']==data['dataset'] and m['trashed'])
                    if removed >= info['total_episodes']:
                        recycle.trash(CONFIG['dataset_root'],data['dataset'])
                except FileNotFoundError:
                    pass
            return result
    if action == 'mixture':
        current = recording.status()
        if current.get('active'):
            raise ValueError('请等采集和保存完成后再保存训练组合。')
        cache = {}
        return library.mixture(data,lambda name,index:describe_source(name,index,cache))
    raise ValueError('未知整理操作。')

def replay_action(action,data):
    allowed={'dataset','episode'}|({'digest','prepared','legacy_confirmed'} if action=='start' else {'id','verdict','note'} if action=='review' else set())
    if not isinstance(data,dict) or set(data)-allowed or not isinstance(data.get('dataset'),str) or type(data.get('episode')) is not int:
        raise ValueError('Replay 参数无效，请选择一条已保存示范。')
    with experiments.LOCK, recording.LOCK, recycle.LOCK:
        if any(m['dataset']==data['dataset'] and m['episode']==data['episode'] and m['trashed'] for m in library.snapshot()['members']):
            raise ValueError('示范已删除，请先从回收站恢复。')
        root=dataset_path(data['dataset'])
        if (root/'workbench_simulation.json').is_file():raise ValueError('软件设备测试数据不能实机回放。')
        if action=='plan':return replay.plan(root,data['episode'],CONFIG)
        if experiments.status().get('active'):raise ValueError('请等数据准备或训练结束。')
        if recording.status().get('active'):raise ValueError('请先结束采集、机械臂模式或推理相机预览。')
        if action=='start':return replay.start(root,data,CONFIG)
        if action=='review':return replay.review(root,data)
    raise ValueError('未知 Replay 操作。')


def export_action(action,data):
    if not isinstance(data,dict) or not isinstance(data.get('collection'),str):raise ValueError('请选择整理后的数据集。')
    with experiments.LOCK, recording.LOCK, recycle.LOCK:
        if recording.status().get('active'):raise ValueError('请等采集或机械臂模式结束后导出。')
        cache={}
        plan=dataset_export.plan(library.snapshot(),data['collection'],lambda name,index:describe_source(name,index,cache))
        if action=='plan':return plan
        if not plan['items']:raise ValueError('没有标记为保留的示范，请先整理数据。')
        if data.get('digest')!=plan['digest']:raise ValueError('数据清单已变化，请重新预览后导出。')
        return experiments.start('export',{'export_plan':plan},CONFIG,Path(data['collection']))


def experiment_action(action,data):
    if not isinstance(data,dict) or not isinstance(data.get('dataset'),str):
        raise ValueError('请选择原始数据目录。')
    with experiments.LOCK, recording.LOCK, recycle.LOCK:
        source=dataset_path(data['dataset'])
        active=recording.status()
        if active.get('active') and active.get('dataset')==source.name:
            raise ValueError('请等待采集保存完成。')
        if any(m['dataset']==source.name and (m['trashed'] or m.get('review')=='reject') for m in library.snapshot()['members']):
            raise ValueError('该原始目录含已删除或需重录示范，不能整包处理。请先完成整理；请使用整理后数据集的保留清单导出。')
        if action=='inspect':return experiment_data.inspect(source)
        return experiments.start(action,data,CONFIG,source)


def require_idle():
    if recording.status().get('active'):
        raise ValueError('请先正常结束设备或相机任务。')
    if experiments.status().get('active'):
        raise ValueError('请等待数据准备或训练结束。')
    if maintenance.STATE.get('active') and maintenance.STATE.get('kind')=='backup':
        raise ValueError('正在备份，请等待文件校验完成。')


def named_actions():
    result=[]
    members={(m['dataset'],m['episode']):m for m in library_snapshot()['members']}
    for item in workspace_state.actions():
        row={**item,'available':False,'reason':'源示范已删除、移动到其他项目或不可用。'}
        member=members.get((item['dataset'],item['episode']))
        if member and not member['trashed'] and not member.get('source_deleted'):
            try:
                unchanged=replay.source_identity(dataset_path(item['dataset']))==item['identity']
                row.update(available=unchanged,reason='' if unchanged else '源文件已变化，请重新检查后保存动作。',member=member)
            except (ValueError,OSError):pass
        result.append(row)
    return result


def manage_action(action,data):
    if not isinstance(data,dict):raise ValueError('请求格式无效。')
    if action=='cancel':return experiments.cancel(data)
    if action=='preview':
        with experiments.LOCK:
            if experiments.status().get('active'):raise ValueError('请等待数据准备或训练结束。')
            return device_settings.start_preview(data,CONFIG)
    if action in ('scan','support'):return maintenance.start(action,data,CONFIG)
    if action=='record_action':
        name=library.text(data.get('name'),80)
        if any(a['name']==name for a in workspace_state.actions()):raise ValueError('已有同名动作，请换一个名称。')
        with experiments.LOCK:
            if experiments.status().get('active'):raise ValueError('请等待数据准备或训练结束。')
            group=data.get('collection')
            if not group:
                recording.validate_request({'task':data.get('task') or name,'seconds':data.get('seconds',0),'prepared':data.get('prepared')})
                with recording.LOCK:
                    if recording.status().get('active'):raise ValueError('请先正常结束设备任务。')
                    project=workspace_state.current();title='动作示教 · '+project[:12]
                    existing=next((c for c in library.snapshot()['collections'] if c['name']==title),None)
                    item=existing or library.create(title,'Named action teaching')
                    workspace_state.bind_collection(item['id'],project);group=item['id']
            return recording.start({'task':data.get('task') or name,'seconds':data.get('seconds',0),'prepared':data.get('prepared'),
                                    'collection':group,'action_name':name},CONFIG)
    with experiments.LOCK,recording.LOCK,recycle.LOCK:
        require_idle()
        if action in ('project_create','project_select','project_update','project_bind'):
            return workspace_state.project_action(action.removeprefix('project_'),data)
        if action=='setup_save':return device_settings.save(data,setup_overview())
        if action=='backup':return maintenance.start(action,data,CONFIG)
        if action=='reserve':return {'reserve_gib':workspace_state.reserve_gib(data.get('reserve_gib'))}
        if action=='action_save':
            description=describe_source(data.get('dataset'),data.get('episode'))
            return workspace_state.save_action(data,replay.source_identity(Path(description['root'])))
        if action in ('action_update','action_delete'):
            return workspace_state.edit_action(action.removeprefix('action_'),data)
        if action=='audit':
            group=library.collection(data.get('collection'))
            members=[m for m in library_snapshot()['members'] if m['collection']==group['id'] and not m['trashed'] and not m.get('source_deleted')]
            if not 1<=len(members)<=500:raise ValueError('每次可检查 1–500 条完整示范。')
            items=[{'root':str(dataset_path(m['dataset'])),'episode':m['episode']} for m in members if m['frames']]
            if not items:raise ValueError('尚无完整示范。')
            return experiments.start('audit',{'audit_items':items,'audit_name':group['name']},CONFIG,Path(group['id']))
        if action=='simulation':return experiments.start('simulation',{},CONFIG,Path('software_device'))
        if action in ('merge_plan','merge_start'):
            cache={};plan=dataset_export.mixture_plan(library.snapshot(),data.get('mixture'),lambda name,index:describe_source(name,index,cache))
            if action=='merge_plan':return plan
            if not plan['items'] or plan['digest']!=data.get('digest'):raise ValueError('清单已改变，请重新预览。')
            return experiments.start('export',{'export_plan':plan},CONFIG,Path(plan['collection']))
        if action in ('import_hf','upload_hf'):
            source=dataset_path(data.get('dataset','')) if action=='upload_hf' else Path('hf_import')
            return experiments.start(action,data,CONFIG,source)
        if action=='retry':
            folder=monitoring.folder(data.get('id'));request=json.loads((folder/'request.json').read_text())
            kind=request['kind']
            if kind=='export':raise ValueError('请从数据集重新预览最新清单后导出。')
            if kind=='audit':raise ValueError('请从数据集重新启动检查。')
            source=dataset_path(request['dataset']) if kind!='import_hf' else Path('hf_import')
            retry={k:request[k] for k in ('steps','batch','save_mode','save_interval','repo','private','revision') if k in request}
            if request.get('resume_from'):
                root=Path(CONFIG['checkpoint_root']).resolve();resume=Path(request['resume_from']).resolve()
                if not resume.is_relative_to(root):raise ValueError('原续训存档不在当前模型目录中。')
                retry['resume_checkpoint']=str(resume.relative_to(root))
            retry['acknowledged']=data.get('acknowledged')
            if kind=='train' and retry['acknowledged'] is not True:raise ValueError('请确认按原参数在新目录中重试训练。')
            return experiments.start(kind,retry,CONFIG,source)
    raise ValueError('未知管理操作。')


def video_path(name, relative):
    root = dataset_path(name)
    p = (root / relative).resolve()
    if not p.is_relative_to((root / 'videos').resolve()) or p.suffix.lower() != '.mp4' or not p.is_file():
        raise ValueError('视频不存在或路径无效')
    return p

def byte_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()):
        raise ValueError('无效范围')
    a, b = match.groups()
    if not a:
        start, end = max(0, size - int(b)), size - 1
    else:
        start, end = int(a), min(int(b), size - 1) if b else size - 1
    if start > end or start >= size:
        raise ValueError('范围超出文件')
    return start, end, True

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data, code=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        # Bind to loopback and reject foreign Host headers.
        host = self.headers.get('Host', '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost'):
            return self.reply({'error': '仅允许本机访问'}, 403)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == '/api/status':
                return self.reply({'app': APP_ID, 'checks': readiness(), 'datasets': datasets(), 'models': models(), 'csrf_token': CSRF_TOKEN, 'can_job': can_job()})
            if url.path == '/api/setup/status':
                return self.reply(setup_overview())
            if url.path == '/api/manage/projects':
                return self.reply(workspace_state.listing(library.snapshot()['collections']))
            if url.path == '/api/manage/actions':
                return self.reply(named_actions())
            if url.path == '/api/manage/storage':
                return self.reply(maintenance.status(CONFIG))
            if url.path == '/api/manage/history':
                return self.reply(monitoring.history(workspace_state.current()))
            if url.path == '/api/manage/job':
                return self.reply(monitoring.detail(query.get('id',[''])[0]))
            if url.path == '/api/manage/quality':
                folder=monitoring.folder(query.get('id',[''])[0]);path=folder/'quality_report.json'
                if not path.is_file():raise ValueError('质量报告尚未生成。')
                return self.reply(json.loads(path.read_text()))
            if url.path == '/api/manage/resources':
                return self.reply(monitoring.resources())
            if url.path == '/api/manage/download':
                name=query.get('name',[''])[0]
                if not re.fullmatch(r'support_[0-9a-f]{32}\.json',name):raise ValueError('诊断文件编号无效。')
                return self.send_file(maintenance.RUNTIME/name)
            if url.path == '/api/setup/stream':
                job=recording.status();folder=recording.current_dir();ident=query.get('id',[''])[0]
                if not job.get('active') or job.get('mode')!='camera_preview' or ident!=job.get('id') or not folder or folder.name!=ident:
                    return self.reply({'error':'相机预览未启动。'},404)
                role=query.get('role',['wrist'])[0]
                if role=='head' and job.get('role')!='both':return self.reply({'error':'第三视角未启动。'},404)
                return stream_jpegs(self,view_folder(folder,role),lambda:recording.status().get('active') and recording.current_dir()==folder)
            if url.path == '/api/episode/inspect':
                return self.reply(inspect_episode(query.get('dataset', [''])[0], int(query.get('episode', ['-1'])[0])))
            if url.path == '/api/can/status':
                return self.reply(can_job())
            if url.path == '/api/mode/pose':
                return self.reply(modes.pose())
            if url.path == '/api/inference/status':
                return self.reply(inference.status(CONFIG))
            if url.path == '/api/inference/preview':
                state=inference.session();folder=recording.current_dir()
                path=view_folder(folder,query.get('role',['wrist'])[0])/'preview.jpg' if folder else None
                if not state.get('active') or path is None or not path.is_file():return self.reply({'error':'预览尚未就绪'},404)
                return self.send_file(path)
            if url.path == '/api/inference/stream':
                state=inference.session();folder=recording.current_dir()
                ident=query.get('id',[''])[0]
                if (not state.get('active') or not folder or ident!=state.get('id')
                        or folder.name!=ident or not re.fullmatch(r'[0-9a-f]{32}',ident)):
                    return self.reply({'error':'预览会话已结束或已变化。'},404)
                def alive():
                    current=inference.session()
                    return current.get('active') and current.get('id')==ident
                role=query.get('role',['wrist'])[0]
                if role=='head' and 'head_rgb' not in state.get('camera_views',[]):return self.reply({'error':'第三视角未启动。'},404)
                return stream_jpegs(self,view_folder(folder,role),alive)
            if url.path == '/api/experiment/checkpoints':
                return self.reply(checkpoint_catalog.listing(CONFIG['checkpoint_root']))
            if url.path == '/api/experiment/status':
                return self.reply(experiments.status())
            if url.path == '/api/record/status':
                return self.reply(visible_record_status())
            if url.path == '/api/recycle':
                return self.reply(recycle.listing(CONFIG['dataset_root']))
            if url.path == '/api/library':
                return self.reply(library_snapshot())
            if url.path == '/api/record/preview':
                if not visible_record_status().get('saved') and not recording.status().get('active'):
                    return self.reply({'error':'无可用预览'},404)
                folder = recording.current_dir()
                path=view_folder(folder,query.get('role',['wrist'])[0])/'preview.jpg' if folder else None
                if path and path.is_file():
                    return self.send_file(path)
                return self.reply({'error': '尚无相机画面'}, 404)
            if url.path == '/api/episodes':
                return self.reply(episodes(query.get('dataset', [''])[0]))
            if url.path == '/video':
                return self.send_file(video_path(query.get('dataset', [''])[0], query.get('path', [''])[0]), ranges=True)
            if url.path == '/':
                return self.send_file(ROOT / 'web/index.html')
            if url.path in ['/style.css', '/ui.js', '/library_ui.js', '/recycle_ui.js', '/workspace_ui.js', '/experiment_ui.js', '/inference_ui.js', '/replay_ui.js', '/data_workspace_ui.js', '/device_setup_ui.js', '/management_ui.js', '/icon.svg']:
                return self.send_file(ROOT / 'web' / url.path[1:])
            return self.reply({'error': '页面不存在'}, 404)
        except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as exc:
            return self.reply({'error': str(exc)}, 400)

    def do_POST(self):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if (host not in allowed or self.headers.get('Origin') != 'http://' + host
                or not secrets.compare_digest(self.headers.get('X-Workbench-Token', '').encode(), CSRF_TOKEN.encode())):
            return self.reply({'error': '请求来源无效，请刷新工作台后重试。'}, 403)
        if maintenance.STATE.get('active') and maintenance.STATE.get('kind')=='backup' and self.path not in ('/api/record/stop','/api/record/heartbeat','/api/inference/stop','/api/inference/finish','/api/inference/heartbeat','/api/manage/cancel'):
            return self.reply({'error':'正在备份和校验文件，请稍后再修改。'},400)
        if self.path.startswith('/api/manage/'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=65536 or self.headers.get('Transfer-Encoding'):raise ValueError('请求大小无效。')
                return self.reply(manage_action(self.path.rsplit('/',1)[-1],json.loads(self.rfile.read(size))))
            except (ValueError,RuntimeError,OSError,KeyError,subprocess.SubprocessError) as exc:return self.reply({'error':str(exc)},400)
        if self.path in ('/api/replay/plan','/api/replay/start','/api/replay/review'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192 or self.headers.get('Transfer-Encoding'):raise ValueError('请求大小无效。')
                return self.reply(replay_action(self.path.rsplit('/',1)[-1],json.loads(self.rfile.read(size))))
            except (ValueError,RuntimeError,OSError,KeyError,subprocess.SubprocessError) as exc:return self.reply({'error':str(exc)},400)
        if self.path.startswith('/api/inference/'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192 or self.headers.get('Transfer-Encoding'):raise ValueError('请求大小无效。')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('请求格式无效。')
                return self.reply(inference.action(self.path.rsplit('/',1)[-1],data,CONFIG))
            except (ValueError,RuntimeError,OSError,KeyError,subprocess.SubprocessError) as exc:return self.reply({'error':str(exc)},400)
        if self.path in ('/api/mode/start','/api/mode/save'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192 or self.headers.get('Transfer-Encoding'):raise ValueError('请求大小无效。')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('请求格式无效。')
                return self.reply(modes.start(data,CONFIG) if self.path.endswith('/start') else modes.save(data))
            except (ValueError,RuntimeError,OSError) as exc:return self.reply({'error':str(exc)},400)
        if self.path in ('/api/export/plan','/api/export/start'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192 or self.headers.get('Transfer-Encoding'):raise ValueError('请求大小无效。')
                return self.reply(export_action(self.path.rsplit('/',1)[-1],json.loads(self.rfile.read(size))))
            except (ValueError,RuntimeError,OSError,KeyError,subprocess.TimeoutExpired) as exc:return self.reply({'error':str(exc)},400)
        if self.path in ('/api/experiment/inspect','/api/experiment/convert','/api/experiment/train'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192 or self.headers.get('Transfer-Encoding'):raise ValueError('请求大小无效。')
                return self.reply(experiment_action(self.path.rsplit('/',1)[-1],json.loads(self.rfile.read(size))))
            except (ValueError,RuntimeError,OSError,KeyError) as exc:
                return self.reply({'error':str(exc)},400)
        if self.path in ('/api/recycle/trash','/api/recycle/restore','/api/recycle/plan','/api/recycle/purge'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('请求大小无效。')
                return self.reply(recycle_action(self.path.rsplit('/',1)[-1],json.loads(self.rfile.read(size))))
            except (ValueError,RuntimeError,OSError) as exc:
                return self.reply({'error':str(exc)},400)
        if self.path in ('/api/library/create','/api/library/import','/api/library/update','/api/library/mixture','/api/library/batch','/api/library/collection'):
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 65536 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('请求大小无效。')
                data = json.loads(self.rfile.read(size))
                return self.reply(library_action(self.path.rsplit('/',1)[-1],data))
            except (ValueError,OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
                return self.reply({'error':str(exc)},400)
        if self.path in ('/api/record/start', '/api/record/stop', '/api/record/heartbeat','/api/record/next'):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('请求大小无效')
                data = json.loads(self.rfile.read(size))
                action = self.path.rsplit('/', 1)[-1]
                if action=='start':
                    with experiments.LOCK:
                        if experiments.status().get('active'):raise ValueError('请先结束数据准备或训练任务。')
                        result=recording.start(data,CONFIG)
                else:result=recording.control(data,action)
                return self.reply(result, 202 if action == 'start' else 200)
            except (ValueError, RuntimeError, OSError) as exc:
                return self.reply({'error': str(exc)}, 400)
        if self.path != '/api/can/start':
            return self.reply({'error': '操作不存在'}, 404)
        if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
            return self.reply({'error': '此操作不接受参数。'}, 400)
        try:
            return self.reply(begin_can_start(), 202)
        except ValueError as exc:
            return self.reply({'error': str(exc)}, 400)

    def send_file(self, p, ranges=False):
        size = p.stat().st_size
        try:
            start, end, partial = byte_range(self.headers.get('Range') if ranges else None, size)
        except ValueError:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', mimetypes.guess_type(p)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('X-Content-Type-Options', 'nosniff')
        if ranges:
            self.send_header('Accept-Ranges', 'bytes')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with p.open('rb') as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

def serve():
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    lock = (RUNTIME / 'server.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    port = 0
    try:
        previous = json.loads((RUNTIME / 'server.json').read_text())
        port = urlparse(previous['url']).port or 0
    except (OSError, ValueError, KeyError):
        pass
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state = {'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid()}
    tmp = RUNTIME / 'server.tmp'
    tmp.write_text(json.dumps(state))
    tmp.replace(RUNTIME / 'server.json')
    recycle.recover(CONFIG['dataset_root'])
    server.serve_forever()

if __name__ == '__main__':
    serve()
