import sqlite3, json, os
from pathlib import Path
from app.errors import DomainError

# Columns added after the first release.  Existing databases (the named volume)
# are migrated in place so historical batches stay readable.
_EXTRA_COLUMNS = [
    ('parent', 'TEXT'),
    ('source', 'TEXT'),
    ('derivation_key', 'TEXT'),
    ('progress', 'INTEGER NOT NULL DEFAULT 7'),
    ('status', "TEXT NOT NULL DEFAULT 'complete'"),
    ('created_at', 'TEXT'),
]

def connection():
    path=Path(os.environ.get('APP_DB','data/runs.sqlite3'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    columns={row['name'] for row in db.execute('PRAGMA table_info(runs)')}
    for name, declaration in _EXTRA_COLUMNS:
        if name not in columns:
            db.execute('ALTER TABLE runs ADD COLUMN %s %s' % (name, declaration))
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS runs_derivation_key ON runs(derivation_key) WHERE derivation_key IS NOT NULL')
    db.execute('CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, ctx TEXT NOT NULL, progress INTEGER NOT NULL)')
    return db

def _payload(row, source=False):
    result=json.loads(row['payload'])
    result['revision']=row['revision']
    result['decision']=row['decision']
    result['parent']=row['parent']
    result['base_run_id']=row['parent']
    result['derived']=row['parent'] is not None
    result['progress']=row['progress']
    result['status']=row['status'] or 'complete'
    result['created_at']=row['created_at'] or result.get('manifest',{}).get('created_at')
    if source: result['source']=row['source']
    return result

def _select(db, ident):
    return db.execute('SELECT * FROM runs WHERE id=?',(ident,)).fetchone()

def save(result, source=None):
    with connection() as db:
        ident=result['manifest']['run_id'];row=_select(db,ident)
        if row:
            if source and not row['source']:
                db.execute('UPDATE runs SET source=? WHERE id=?',(source,ident))
            reused=_payload(row);reused['reused']=True
            return reused
        db.execute('INSERT INTO runs(id,payload,progress,status,source,created_at) VALUES (?,?,?,?,?,?)',
                   (ident,json.dumps(result,ensure_ascii=False),7,'complete',source,result.get('manifest',{}).get('created_at')))
    return {**result,'revision':1,'decision':'待复核','parent':None,'progress':7,'status':'complete','reused':False}

def get_run(ident, with_source=False):
    with connection() as db: row=_select(db,ident)
    if row is None: raise DomainError('NOT_FOUND','批次不存在',404)
    return _payload(row, source=with_source)

def find(ident):
    """Return a batch payload or None without raising."""
    with connection() as db: row=_select(db,ident)
    return _payload(row) if row else None

def source_of(ident):
    with connection() as db: row=_select(db,ident)
    return row['source'] if row else None

def find_derivation(key):
    with connection() as db: row=db.execute('SELECT * FROM runs WHERE derivation_key=?',(key,)).fetchone()
    return _payload(row) if row else None

def save_derived(ident, payload, base, key, source, progress, status):
    """Create or advance a derived batch.

    A completed derivation is never rewritten: re-submitting the same revision
    has to reuse the original derived result.  A partial derivation only moves
    forward, so a stale resume request cannot erase confirmed stages.
    """
    with connection() as db:
        row=_select(db,ident)
        if row:
            current=_payload(row)
            if current['status']=='complete':
                current['reused']=True
                return current
            if row['progress'] is not None and row['progress']>progress:
                progress=row['progress']
            if progress>=7: status='complete'
            db.execute('UPDATE runs SET payload=?,parent=?,derivation_key=?,source=?,progress=?,status=? WHERE id=?',
                       (json.dumps(payload,ensure_ascii=False),base,key,source,progress,status,ident))
            return {**payload,'revision':row['revision'] or 1,'decision':row['decision'] or '待复核',
                    'parent':base,'progress':progress,'status':status,'reused':True}
        try:
            db.execute('INSERT INTO runs(id,payload,revision,decision,parent,derivation_key,source,progress,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (ident,json.dumps(payload,ensure_ascii=False),1,'待复核',base,key,source,progress,status,payload.get('manifest',{}).get('created_at')))
        except sqlite3.IntegrityError:
            # A concurrent submission of the same revision created the batch
            # first; reuse it rather than writing a second derived result.
            row=_select(db,ident)
            if row is None:
                row=db.execute('SELECT * FROM runs WHERE derivation_key=?',(key,)).fetchone()
            if row is None: raise
            current=_payload(row);current['reused']=True
            return current
    return {**payload,'revision':1,'decision':'待复核','parent':base,'progress':progress,'status':status,'reused':False}

def save_checkpoint(ident, ctx, progress):
    with connection() as db:
        db.execute('INSERT INTO checkpoints(id,ctx,progress) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET ctx=excluded.ctx,progress=excluded.progress',
                   (ident,json.dumps(ctx,ensure_ascii=False),progress))

def load_checkpoint(ident):
    with connection() as db:
        row=db.execute('SELECT ctx,progress FROM checkpoints WHERE id=?',(ident,)).fetchone()
    return json.loads(row['ctx']) if row else None

def delete_checkpoint(ident):
    with connection() as db: db.execute('DELETE FROM checkpoints WHERE id=?',(ident,))

def latest():
    with connection() as db:
        # An interrupted revision is kept in history but the last complete
        # batch (normally the original batch) stays on screen.
        row=db.execute("SELECT * FROM runs WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None: row=db.execute('SELECT * FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
    return _payload(row) if row else None

def history():
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT id,revision,decision,parent,status,progress,created_at FROM runs ORDER BY rowid DESC')]

def derivations(base=None):
    with connection() as db:
        sql='SELECT id,parent,status,progress,revision,decision,created_at FROM runs WHERE parent IS NOT NULL'
        args=()
        if base: sql+=' AND parent=?';args=(base,)
        return [dict(r) for r in db.execute(sql+' ORDER BY rowid DESC',args)]

def review(ident, revision, decision):
    if decision not in ('通过','退回'): raise DomainError('DECISION','复核动作无效')
    with connection() as db:
        row=_select(db,ident)
        if row is None: raise DomainError('NOT_FOUND','批次不存在',404)
        if (row['status'] or 'complete')!='complete':
            raise DomainError('INCOMPLETE','修订派生尚未走完全部阶段，不能复核',409)
        changed=db.execute('UPDATE runs SET revision=revision+1,decision=? WHERE id=? AND revision=?',(decision,ident,revision)).rowcount
        if not changed: raise DomainError('VERSION','版本已变化，请重新载入',409)
    return {'revision':revision+1,'decision':decision}
