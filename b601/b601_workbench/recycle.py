"""Dataset quarantine with a durable journal. Only direct dataset-root children."""
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import threading
import time
import uuid
import library

LOCK = threading.RLock()
PLANS = {}


def rows():
    with library.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM raw_trash')]


def deleted_names():
    return {r['dataset'] for r in rows()}


def source(root, name):
    if not isinstance(name,str) or not name or name.startswith('.') or Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('原始数据集名称无效。')
    root = Path(root).resolve()
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError('不能删除符号链接或数据目录以外的路径。')
    return path


def bin_root(root):
    path = Path(root).resolve() / '.b601-trash'
    if path.is_symlink():
        raise ValueError('回收站目录不能是符号链接。')
    path.mkdir(mode=0o700,exist_ok=True)
    return path


def quarantined(root,row):
    token = row['token']
    if len(token)!=32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('回收站记录异常。')
    return bin_root(root) / token


def identity(path,row):
    st=path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_ino!=row['inode'] or st.st_dev!=row['device']:
        raise ValueError('目录身份发生变化，已停止操作。')


def state(name,value):
    with library.connect() as db:
        db.execute('UPDATE raw_trash SET state=? WHERE dataset=?',(value,name))


def recover(root):
    """Recover interrupted renames. Never automatically resume permanent erasure."""
    with LOCK:
        for row in rows():
            if row['state'] not in ('moving','restoring'):
                continue
            src=source(root,row['dataset']); dst=quarantined(root,row)
            if row['state']=='moving':
                if dst.exists() and not src.exists():
                    identity(dst,row);state(row['dataset'],'trashed')
                elif src.exists() and not dst.exists():
                    identity(src,row)
                    with library.connect() as db:db.execute('DELETE FROM raw_trash WHERE dataset=?',(row['dataset'],))
            elif src.exists() and not dst.exists():
                identity(src,row)
                finish_restore(row)
            elif dst.exists() and not src.exists():
                identity(dst,row);state(row['dataset'],'trashed')


def trash(root,name):
    with LOCK:
        recover(root)
        if name in deleted_names():
            raise ValueError('这个原始数据集已经在回收站中或已彻底删除。')
        src=source(root,name)
        if not src.is_dir() or not (src/'meta/info.json').is_file():
            raise ValueError('原始数据集不存在。')
        info=json.loads((src/'meta/info.json').read_text())
        st=src.lstat()
        row={'dataset':name,'token':uuid.uuid4().hex,'state':'moving','created':time.time(),
             'inode':st.st_ino,'device':st.st_dev,'info':json.dumps(info,ensure_ascii=False)}
        dst=quarantined(root,row)
        with library.connect() as db:
            db.execute('INSERT INTO raw_trash VALUES(:dataset,:token,:state,:created,:inode,:device,:info)',row)
        try:
            src.rename(dst)
            state(name,'trashed')
        except BaseException:
            recover(root)
            raise
        return {'ok':True,'dataset':name}


def finish_restore(row):
    with library.connect() as db:
        # If all episodes were individually removed, restoring the dataset
        # restores them all; otherwise retain earlier individual removals.
        count=db.execute('SELECT count(*) FROM members WHERE dataset=? AND trashed=1',(row['dataset'],)).fetchone()[0]
        if count >= json.loads(row['info']).get('total_episodes',1):
            db.execute('UPDATE members SET trashed=0 WHERE dataset=?',(row['dataset'],))
        db.execute('DELETE FROM raw_trash WHERE dataset=?',(row['dataset'],))


def restore(root,name):
    with LOCK:
        recover(root)
        row=next((r for r in rows() if r['dataset']==name),None)
        if not row or row['state']!='trashed':
            raise ValueError('此项不能恢复；彻底删除中断的项只能重试删除。')
        src=quarantined(root,row);dst=source(root,name)
        if dst.exists() or dst.is_symlink():
            raise ValueError('原位置已有同名数据，未覆盖。')
        identity(src,row)
        state(name,'restoring');src.rename(dst);finish_restore(row)
        return {'ok':True}


def sizes(path):
    logical=allocated=0
    for base,dirs,files in os.walk(path,followlinks=False):
        for name in files:
            st=(Path(base)/name).lstat()
            if stat.S_ISREG(st.st_mode):
                logical+=st.st_size
                allocated+=st.st_blocks*512
    return logical,allocated


def references(name,episode=None):
    result=[]
    for mixture in library.snapshot()['mixtures']:
        count=sum(1 for s in mixture['sources'] for e in s['episodes'] if e['dataset']==name and (episode is None or e['episode']==episode))
        if count:result.append({'name':mixture['name'],'episodes':count})
    return result


def listing(root):
    with LOCK:
        recover(root)
        raw=[]
        all_rows=rows(); names={r['dataset'] for r in all_rows}
        for row in all_rows:
            if row['state']=='purged':continue
            path=quarantined(root,row)
            logical,allocated=sizes(path) if path.exists() else (0,0)
            raw.append({'dataset':row['dataset'],'kind':'dataset','state':row['state'],'episodes':json.loads(row['info']).get('total_episodes',0),
                        'bytes':logical,'allocated':allocated,'created':row['created'],'references':references(row['dataset'])})
        for member in library.snapshot()['members']:
            if member['trashed'] and member['dataset'] not in names:
                path=source(root,member['dataset'])
                try: info=json.loads((path/'meta/info.json').read_text())
                except (OSError,ValueError):info={}
                raw.append({'dataset':member['dataset'],'episode':member['episode'],'kind':'episode','state':'trashed',
                            'episodes':1,'source_episodes':info.get('total_episodes',0),'created':member['created'],
                            'whole_source_removed':info.get('total_episodes',0)>0 and sum(1 for m in library.snapshot()['members'] if m['dataset']==member['dataset'] and m['trashed'])>=info['total_episodes'],
                            'references':references(member['dataset'],member['episode'])})
        return sorted(raw,key=lambda r:r['created'],reverse=True)


def plan(root,name):
    with LOCK:
        recover(root)
        row=next((r for r in rows() if r['dataset']==name),None)
        if not row or row['state'] not in ('trashed','purging'):
            raise ValueError('请先将整个原始数据集移入回收站。')
        path=quarantined(root,row)
        if path.exists() or path.is_symlink():identity(path,row)
        elif row['state']!='purging':raise ValueError('回收站文件缺失，请检查。')
        logical,allocated=sizes(path)
        token=secrets.token_urlsafe(32)
        PLANS[token]={'dataset':name,'root':str(Path(root).resolve()),'trash_token':row['token'],'expires':time.time()+300}
        for key in list(PLANS):
            if PLANS[key]['expires']<time.time():PLANS.pop(key,None)
        return {'token':token,'dataset':name,'episodes':json.loads(row['info']).get('total_episodes',0),
                'bytes':logical,'allocated':allocated,'references':references(name),
                'message':'将永久删除此原始数据集的全部视频、数据和元信息，无法恢复。相关训练组合将标记为源数据缺失。独立转换副本不在删除范围内。'}


def purge(root,name,token,confirmation):
    with LOCK:
        approval=PLANS.pop(token,None) if isinstance(token,str) else None
        if not approval or approval['expires']<time.time() or approval['dataset']!=name or approval['root']!=str(Path(root).resolve()) or confirmation!=name:
            raise ValueError('删除确认已失效，请重新查看影响范围并输入完整数据集名称。')
        row=next((r for r in rows() if r['dataset']==name),None)
        if not row or row['token']!=approval['trash_token'] or row['state'] not in ('trashed','purging'):
            raise ValueError('回收站状态已变化，请重新确认。')
        path=quarantined(root,row)
        if path.exists() or path.is_symlink():identity(path,row)
        elif row['state']!='purging':raise ValueError('回收站目录不存在。')
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RuntimeError('当前系统不支持安全的目录删除。')
        state(name,'purging')
        if path.exists():shutil.rmtree(path)
        state(name,'purged')
        return {'ok':True,'dataset':name}
