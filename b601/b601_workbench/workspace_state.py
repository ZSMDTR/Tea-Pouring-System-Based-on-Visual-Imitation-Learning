"""Local project organization and named actions. Does not open hardware."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data/workspace.sqlite3'
DEFAULT = 'default'


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, note TEXT NOT NULL, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS project_collections(collection TEXT PRIMARY KEY, project TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, project TEXT NOT NULL, name TEXT NOT NULL, note TEXT NOT NULL, dataset TEXT NOT NULL, episode INTEGER NOT NULL, identity TEXT NOT NULL, created REAL NOT NULL, UNIQUE(project,name));
    ''')
    db.execute('INSERT OR IGNORE INTO projects VALUES(?,?,?,?)', (DEFAULT, 'B601 · 当前项目', '', time.time()))
    db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('active_project', DEFAULT))
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def text(value, maximum=80, empty=False):
    if not isinstance(value, str) or len(value.strip()) > maximum or (not empty and not value.strip()):
        raise ValueError('名称或备注长度无效。')
    return value.strip()


def current():
    with connect() as db:
        return db.execute("SELECT value FROM settings WHERE key='active_project'").fetchone()[0]


def listing(collections=()):
    with connect() as db:
        for c in collections:
            db.execute('INSERT OR IGNORE INTO project_collections VALUES(?,?)', (c['id'], DEFAULT))
        return {'active': db.execute("SELECT value FROM settings WHERE key='active_project'").fetchone()[0],
                'projects': [dict(r) for r in db.execute('SELECT * FROM projects ORDER BY created')],
                'bindings': {r['collection']: r['project'] for r in db.execute('SELECT * FROM project_collections')}}


def project_action(action, data):
    with connect() as db:
        if action == 'create':
            item = {'id': uuid.uuid4().hex, 'name': text(data.get('name')), 'note': text(data.get('note', ''), 2000, True), 'created': time.time()}
            try:
                db.execute('INSERT INTO projects VALUES(:id,:name,:note,:created)', item)
            except sqlite3.IntegrityError:
                raise ValueError('已有同名项目。') from None
            return item
        ident = data.get('id')
        if not isinstance(ident, str) or not db.execute('SELECT 1 FROM projects WHERE id=?', (ident,)).fetchone():
            raise ValueError('项目不存在。')
        if action == 'select':
            db.execute("UPDATE settings SET value=? WHERE key='active_project'", (ident,))
        elif action == 'update':
            try:
                db.execute('UPDATE projects SET name=?,note=? WHERE id=?', (text(data.get('name')), text(data.get('note', ''), 2000, True), ident))
            except sqlite3.IntegrityError:
                raise ValueError('已有同名项目。') from None
        elif action == 'bind':
            import library
            group = library.collection(data.get('collection'))
            db.execute('INSERT OR REPLACE INTO project_collections VALUES(?,?)', (group['id'], ident))
        else:
            raise ValueError('未知项目操作。')
    return {'ok': True}


def bind_collection(ident, project=None):
    project = project or current()
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO project_collections VALUES(?,?)', (ident, project))


def actions(project=None):
    project = project or current()
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM actions WHERE project=? ORDER BY created DESC', (project,))]


def save_action(data, identity, project=None):
    project = project or current()
    name = text(data.get('name'))
    note = text(data.get('note', ''), 2000, True)
    dataset, episode = data.get('dataset'), data.get('episode')
    if not isinstance(dataset, str) or type(episode) is not int or episode < 0:
        raise ValueError('请选择完整示范。')
    with connect() as db:
        try:
            ident = uuid.uuid4().hex
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)', (ident, project, name, note, dataset, episode, identity, time.time()))
        except sqlite3.IntegrityError:
            raise ValueError('动作名称已存在，请更换名称。') from None
    return {'id': ident, 'name': name}


def edit_action(action, data):
    project = current()
    with connect() as db:
        ident = data.get('id')
        if not db.execute('SELECT 1 FROM actions WHERE id=? AND project=?', (ident, project)).fetchone():
            raise ValueError('动作不存在。')
        if action == 'delete':
            db.execute('DELETE FROM actions WHERE id=?', (ident,))
        elif action == 'update':
            try:
                db.execute('UPDATE actions SET name=?,note=? WHERE id=?', (text(data.get('name')), text(data.get('note', ''), 2000, True), ident))
            except sqlite3.IntegrityError:
                raise ValueError('动作名称已存在。') from None
        else:
            raise ValueError('未知动作操作。')
    return {'ok': True}


def reserve_gib(value=None):
    with connect() as db:
        if value is not None:
            if type(value) is not int or not 2<=value<=500:raise ValueError('保留空间应为 2–500 GiB 的整数。')
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('reserve_gib',str(value)))
        row=db.execute("SELECT value FROM settings WHERE key='reserve_gib'").fetchone()
        return int(row[0]) if row else 20


def check_space(path):
    import shutil
    path=Path(path)
    while True:
        try:free=shutil.disk_usage(path).free;break
        except FileNotFoundError:
            if path==path.parent:raise
            path=path.parent
    reserve=reserve_gib()
    if free < reserve*2**30:raise ValueError(f'剩余空间低于设置的 {reserve} GiB 保留线，未启动新任务。请先查看空间占用并整理文件。')
