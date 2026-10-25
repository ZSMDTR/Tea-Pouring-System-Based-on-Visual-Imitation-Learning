"""Logical datasets and immutable training drafts. Original data is never moved."""
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager

DB = Path(__file__).resolve().parent / 'data/library.sqlite3'

@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, task TEXT NOT NULL, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS members(dataset TEXT NOT NULL, episode INTEGER NOT NULL, collection TEXT NOT NULL, task TEXT NOT NULL, frames INTEGER NOT NULL, review TEXT NOT NULL DEFAULT 'unreviewed', note TEXT NOT NULL DEFAULT '', trashed INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, PRIMARY KEY(dataset,episode));
    CREATE TABLE IF NOT EXISTS raw_trash(dataset TEXT PRIMARY KEY, token TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, inode INTEGER NOT NULL, device INTEGER NOT NULL, info TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mixtures(id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL, manifest TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS replay_attempts(id TEXT PRIMARY KEY, dataset TEXT NOT NULL, episode INTEGER NOT NULL, source_identity TEXT NOT NULL, created REAL NOT NULL, verdict TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '');
    ''')
    try:
        with db:
            yield db
    finally:
        db.close()


def text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f'请填写 1–{limit} 字的内容。')
    return value.strip()


def collection(ident):
    if not isinstance(ident,str):
        raise ValueError('请先选择或新建采集数据集。')
    with connect() as db:
        row = db.execute('SELECT * FROM collections WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('请先选择或新建采集数据集。')
        return dict(row)


def snapshot():
    with connect() as db:
        return {'collections': [dict(r) for r in db.execute('SELECT * FROM collections ORDER BY created DESC')],
                'members': [dict(r) for r in db.execute('SELECT * FROM members ORDER BY created DESC, dataset, episode DESC')],
                'mixtures': [json.loads(r[0]) for r in db.execute('SELECT manifest FROM mixtures ORDER BY created DESC')]}


def create(name, task):
    item = {'id': uuid.uuid4().hex, 'name': text(name,80), 'task': text(task,500), 'created': time.time()}
    with connect() as db:
        try:
            db.execute('INSERT INTO collections VALUES(:id,:name,:task,:created)', item)
        except sqlite3.IntegrityError:
            raise ValueError('已有同名数据集，请直接选择或换一个名称。')
    return item


def replay_attempts():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM replay_attempts ORDER BY created DESC')]


def begin_replay(ident, dataset, episode, source_identity):
    with connect() as db:
        db.execute('INSERT INTO replay_attempts(id,dataset,episode,source_identity,created) VALUES(?,?,?,?,?)',
                   (ident,dataset,episode,source_identity,time.time()))


def review_replay(ident, verdict, note):
    # Caller verifies the completed, returned attempt and immutable source.
    with connect() as db:
        row=db.execute('SELECT * FROM replay_attempts WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('找不到本次 Replay。')
        db.execute('UPDATE replay_attempts SET verdict=?, note=? WHERE id=?',(verdict,note,ident))
        db.execute('UPDATE members SET review=? WHERE dataset=? AND episode=?',
                   ('keep' if verdict=='passed' else 'reject',row['dataset'],row['episode']))


def add_members(ident, dataset, episodes):
    collection(ident)
    import recycle
    if dataset in recycle.deleted_names():
        raise ValueError('源数据集已删除，请先在统一回收站恢复。')
    count = 0
    with connect() as db:
        for ep in episodes:
            tasks = ep.get('tasks', [])
            task = ' / '.join(tasks) if isinstance(tasks,list) else str(tasks)
            count += db.execute('INSERT OR IGNORE INTO members(dataset,episode,collection,task,frames,created) VALUES(?,?,?,?,?,?)',
                                (dataset, ep['index'], ident, task, ep['frames'], time.time())).rowcount
    return {'added': count}


def update(data, active_dataset=None):
    dataset, episode = data.get('dataset'), data.get('episode')
    if not isinstance(dataset,str) or type(episode) is not int:
        raise ValueError('示范编号无效。')
    if dataset == active_dataset:
        raise ValueError('这条示范仍在采集或保存，请完成后再整理。')
    import recycle
    if dataset in recycle.deleted_names():
        raise ValueError('源数据集已删除，请先在统一回收站恢复。')
    fields = {k:v for k,v in data.items() if k in ('review','note','trashed','collection')}
    if not fields or set(data) - {'dataset','episode',*fields}:
        raise ValueError('整理参数无效。')
    if 'review' in fields and fields['review'] not in ('unreviewed','keep','reject'):
        raise ValueError('结果标记无效；此处不能标记 Replay 验收通过。')
    if 'note' in fields and (not isinstance(fields['note'],str) or len(fields['note']) > 2000):
        raise ValueError('备注最多 2000 字。')
    if 'trashed' in fields and type(fields['trashed']) is not bool:
        raise ValueError('回收站参数无效。')
    if 'collection' in fields:
        collection(fields['collection'])
    with connect() as db:
        cursor = db.execute('UPDATE members SET '+','.join(k+'=?' for k in fields)+' WHERE dataset=? AND episode=?', (*fields.values(),dataset,episode))
        if not cursor.rowcount:
            raise ValueError('未找到示范。')
    return {'ok':True}


def mixture(data, describe):
    name = text(data.get('name'),80)
    choices = data.get('selections')
    if not isinstance(choices,list) or not choices or len(choices)>100:
        raise ValueError('请选择 1–100 个数据集。')
    seen = set()
    sources = []
    signature = None
    with connect() as db:
        # One transaction freezes membership, review and notes together.
        db.execute('BEGIN IMMEDIATE')
        for choice in choices:
            if not isinstance(choice,dict):
                raise ValueError('数据集选择格式无效。')
            ident, weight = choice.get('id'), choice.get('weight')
            if not isinstance(ident,str) or ident in seen:
                raise ValueError('数据集选择无效或重复。')
            seen.add(ident)
            if type(weight) not in (int,float) or not math.isfinite(weight) or weight <= 0:
                raise ValueError('每个数据集的采样权重必须大于 0。')
            row = db.execute('SELECT * FROM collections WHERE id=?',(ident,)).fetchone()
            if not row:
                raise ValueError('选择的数据集不存在。')
            members = [dict(r) for r in db.execute("SELECT * FROM members WHERE collection=? AND trashed=0 AND review='keep' AND dataset NOT IN (SELECT dataset FROM raw_trash) ORDER BY dataset,episode",(ident,))]
            if not members:
                raise ValueError(row['name']+'：没有标记为“保留”的示范，请先检查并整理。')
            for member in members:
                description = describe(member['dataset'],member['episode'])
                current = description['signature']
                if signature is None:
                    signature = current
                if signature != current:
                    raise ValueError('所选示范的相机、动作或采集配置不兼容，不能保存为同一训练组合。')
                member['source'] = description
                member['frames'] = description.get('episode',{}).get('frames',member['frames'])
            sources.append({'id':ident,'name':row['name'],'weight':weight,'episodes':members})
        total = sum(s['weight'] for s in sources)
        if not math.isfinite(total):
            raise ValueError('权重总和过大。')
        for source in sources:
            source['probability'] = source['weight']/total
        item = {'id':uuid.uuid4().hex,'name':name,'created':time.time(),'status':'draft',
                'message':'组合草稿：尚未完成 Replay 验收、数据转换和训练接入，不能直接启动训练。',
                'sources':sources,'episodes':sum(len(s['episodes']) for s in sources)}
        db.execute('INSERT INTO mixtures VALUES(?,?,?,?)',(item['id'],name,item['created'],json.dumps(item,ensure_ascii=False)))
    return item


def update_collection(data):
    group = collection(data.get('id'))
    name, task = text(data.get('name'), 80), text(data.get('task'), 500)
    with connect() as db:
        try:
            db.execute('UPDATE collections SET name=?,task=? WHERE id=?', (name, task, group['id']))
        except sqlite3.IntegrityError:
            raise ValueError('已有同名数据集。') from None
    return {'ok': True}


def batch_update(data):
    """Validate every member before one transaction; never mark Replay passed."""
    items, change = data.get('items'), data.get('change')
    if not isinstance(items, list) or not 1 <= len(items) <= 500:
        raise ValueError('每次请选择 1–500 条示范。')
    if not isinstance(change, dict) or len(change) != 1 or set(change) - {'review', 'collection', 'trashed'}:
        raise ValueError('每次只能批量修改一种属性。')
    key, value = next(iter(change.items()))
    if key == 'review' and value not in ('keep', 'reject', 'unreviewed'):
        raise ValueError('结果标记无效。')
    if key == 'trashed' and type(value) is not bool:
        raise ValueError('删除参数无效。')
    if key == 'collection':
        collection(value)
    keys = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'dataset', 'episode'} or not isinstance(item['dataset'], str) or type(item['episode']) is not int:
            raise ValueError('示范编号无效。')
        pair = (item['dataset'], item['episode'])
        if pair in keys:
            raise ValueError('示范编号重复。')
        keys.append(pair)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        deleted = {r[0] for r in db.execute('SELECT dataset FROM raw_trash')}
        for dataset, episode in keys:
            row = db.execute('SELECT trashed FROM members WHERE dataset=? AND episode=?', (dataset, episode)).fetchone()
            if not row or dataset in deleted or (row['trashed'] and key != 'trashed'):
                raise ValueError('某条示范已删除或不可用，请刷新后重选。')
        db.executemany(f'UPDATE members SET {key}=? WHERE dataset=? AND episode=?', [(value, *pair) for pair in keys])
    return {'updated': len(keys)}
