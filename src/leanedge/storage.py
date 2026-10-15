"""SQLite JSON records with transactional accounts and a replay-verifiable ledger."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from .core import canonical, digest, stamp

KINDS = {'experiment', 'account', 'observation', 'bar', 'safety', 'signal', 'decision',
         'order', 'fill', 'position', 'snapshot', 'ai', 'cost', 'halt', 'health', 'report', 'usage'}


class StorageError(RuntimeError):
    pass


class WriterLock:
    """OS lock released on crash. Never delete the lock inode on release."""
    def __init__(self, database):
        self.path = Path(str(Path(database).resolve()) + '.writer.lock')
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0)
        if self.file.read(1) == b'':
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise StorageError('Another decision writer owns this experiment database') from None
        return self

    def __exit__(self, *args):
        if self.file:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


class Store:
    def __init__(self, path, *, writable=False):
        self.path = Path(path).resolve()
        self.writable = writable
        self.lock = None
        self.conn = None
        self.changes = None
        if writable:
            self.lock = WriterLock(path).__enter__()
        try:
            if writable:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=5)
                self.conn.execute('PRAGMA journal_mode=WAL')
                self.conn.execute('PRAGMA synchronous=FULL')
                self.conn.executescript('''
                    CREATE TABLE IF NOT EXISTS records (
                        kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)),
                        PRIMARY KEY(kind,id));
                    CREATE INDEX IF NOT EXISTS record_time ON records(kind,json_extract(data,'$.time'));
                    CREATE TABLE IF NOT EXISTS ledger (
                        seq INTEGER PRIMARY KEY, time TEXT NOT NULL, body TEXT NOT NULL,
                        previous TEXT NOT NULL, hash TEXT NOT NULL);
                    CREATE TRIGGER IF NOT EXISTS no_ledger_update BEFORE UPDATE ON ledger
                    BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
                    CREATE TRIGGER IF NOT EXISTS no_ledger_delete BEFORE DELETE ON ledger
                    BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
                ''')
            else:
                self.conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                            isolation_level=None, timeout=5)
                self.conn.execute('PRAGMA query_only=ON')
                self.conn.execute('BEGIN')  # one consistent read snapshot for reports and verification
        except Exception:
            self.close()
            raise

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
        if self.lock:
            self.lock.__exit__()
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self, event, time=None):
        if not self.writable or self.changes is not None:
            raise StorageError('Writes require a single-writer transaction')
        self.conn.execute('BEGIN IMMEDIATE')
        self.changes = []
        try:
            yield self
            previous = self.conn.execute('SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1').fetchone()
            previous = previous[0] if previous else '0' * 64
            t = stamp(time)
            body = canonical({'event': event, 'changes': self.changes})
            h = digest({'time': t, 'body': body, 'previous': previous})
            self.conn.execute('INSERT INTO ledger(time,body,previous,hash) VALUES(?,?,?,?)', (t,body,previous,h))
            self.conn.execute('COMMIT')
        except BaseException:
            self.conn.execute('ROLLBACK')
            raise
        finally:
            self.changes = None

    def get(self, kind, key, default=None):
        row = self.conn.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind,str(key))).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, kind):
        return [json.loads(r[0]) for r in self.conn.execute('SELECT data FROM records WHERE kind=? ORDER BY id', (kind,))]

    def put(self, kind, key, data, *, once=False):
        if self.changes is None or kind not in KINDS:
            raise StorageError('Invalid record mutation')
        key = str(key)
        if once and self.get(kind, key) is not None:
            return False
        text = canonical(data)
        self.conn.execute('INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data',
                          (kind,key,text))
        # Hashes rather than copies keep the ledger small. Replay tracks each final row.
        self.changes.append({'kind': kind, 'id': key, 'hash': digest(json.loads(text))})
        return True

    def experiment(self):
        row = self.get('experiment', 'current')
        if not row:
            raise StorageError('Initialize an experiment first')
        return row

    def verify(self, checkpoint=None):
        previous = '0' * 64
        state = {}
        n = 0
        checkpoint_found = checkpoint is None
        try:
            for seq,t,body,prev,h in self.conn.execute('SELECT * FROM ledger ORDER BY seq'):
                n += 1
                if seq != n or prev != previous or digest({'time':t,'body':body,'previous':prev}) != h:
                    return False, f'ledger mismatch at event {seq}'
                for change in json.loads(body)['changes']:
                    state[(change['kind'],change['id'])] = change['hash']
                previous = h
                if checkpoint and seq == checkpoint['sequence']:
                    checkpoint_found = h == checkpoint['hash']
            actual = {(k,i):digest(json.loads(d)) for k,i,d in self.conn.execute('SELECT * FROM records')}
            if actual != state:
                return False, 'stored records differ from audited records'
            if not checkpoint_found:
                return False, 'trusted checkpoint does not match'
            return True, f'{n} audit events verified'
        except (ValueError, KeyError, TypeError):
            return False, 'unreadable audit event'

    def checkpoint(self):
        r = self.conn.execute('SELECT seq,hash FROM ledger ORDER BY seq DESC LIMIT 1').fetchone()
        return {'sequence': r[0] if r else 0, 'hash': r[1] if r else '0'*64,
                'experiment': self.experiment()['id'], 'exported_at':stamp()}

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists() or destination.resolve() == self.path:
            raise StorageError('Backup destination must be a new file')
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create prevents accidentally replacing an existing experiment.
        with destination.open('xb'):
            pass
        with sqlite3.connect(destination) as target:
            self.conn.backup(target)
        return destination
