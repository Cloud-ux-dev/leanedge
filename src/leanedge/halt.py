"""Local control flag is checked outside the market polling task and at fill time."""
import asyncio
import json
import os
from pathlib import Path
import uuid
from .core import canonical, stamp, utcnow
from .storage import StorageError


def flag_path(database):
    return Path(str(Path(database).resolve()) + '.halt')


def request_halt(database, reason='human local halt'):
    path = flag_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use a fixed safe reason rather than recording arbitrary possible credentials.
    record = {'id':uuid.uuid4().hex, 'time':stamp(), 'reason':'human local halt'}
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex)
    with temp.open('x', encoding='utf-8') as f:
        f.write(canonical(record))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    return record


def is_halted(database):
    return flag_path(database).exists()


def apply_halt(store, now=None):
    path = flag_path(store.path)
    if not path.exists():
        return False
    try:
        r = json.loads(path.read_text(encoding='utf-8'))
        key = r['id']
        if not isinstance(key, str) or len(key) != 32:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        key = 'invalid-control-flag'
    if not store.get('halt',key):
        with store.transaction('halt_observed',now):
            store.put('halt',key,{'id':key,'time':stamp(now),'reason':'persistent local halt'})
            for order in store.all('order'):
                if order['side'] == 'buy' and order['status'] == 'pending':
                    order['status'] = 'cancelled'
                    store.put('order',order['id'],order)
    return True


async def watch_halt(store, stopped):
    while not stopped.is_set():
        apply_halt(store)
        try:
            await asyncio.wait_for(stopped.wait(),timeout=0.25)
        except asyncio.TimeoutError:
            pass


def resume(store, health_ok):
    if not health_ok or not store.verify()[0] or store.experiment()['ended']:
        raise StorageError('Resume requires fresh valuations, verified records, and an active experiment')
    path = flag_path(store.path)
    with store.transaction('human_resume'):
        store.put('halt','resume:'+stamp(),{'time':stamp(),'reason':'explicit human resume'})
    path.unlink(missing_ok=True)
