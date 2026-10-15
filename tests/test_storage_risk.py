from datetime import timedelta
from decimal import Decimal as D
import asyncio
import sqlite3
import time
import pytest
from leanedge.config import Config
from leanedge.core import stamp, utcnow
from leanedge.experiment import initialize, frozen_config
from leanedge.halt import request_halt, apply_halt, watch_halt, resume
from leanedge.risk import entry_reasons, update_periods
from leanedge.storage import Store, StorageError


@pytest.fixture
def book(tmp_path):
    with Store(tmp_path/'test.db',writable=True) as s:
        initialize(s,Config(),'test',demo=True)
        yield s


def test_atomic_rollback_ledger_and_tamper(book):
    with pytest.raises(RuntimeError):
        with book.transaction('crash'):
            book.put('health','x',{'ok':False})
            raise RuntimeError()
    assert book.get('health','x') is None
    assert book.verify()[0]
    book.conn.execute("UPDATE records SET data='{}' WHERE kind='account'")
    assert not book.verify()[0]


def test_lock_restart_backup_freezing(book,tmp_path):
    with pytest.raises(StorageError,match='Another'):
        Store(book.path,writable=True)
    with pytest.raises(StorageError,match='history'):
        initialize(book,Config(),'new')
    from dataclasses import replace
    with pytest.raises(StorageError,match='Policy changed'):
        frozen_config(book,replace(Config(),starting_balance=D(2000)))
    backup=book.backup(tmp_path/'backup.db')
    with pytest.raises(StorageError):
        book.backup(backup)
    with Store(backup) as copy:
        assert copy.verify()[0] and copy.get('account','strategy')['cash']=='1000'


def test_limits_halts_and_restart(book):
    c=Config(); now=utcnow()
    with book.transaction('anchor'):
        update_periods(book,'strategy',c,now)
    assert not entry_reasons(book,'strategy','a',D(20),c,now)
    assert 'position limit including costs' in entry_reasons(book,'strategy','a',D('20.01'),c,now)
    with book.transaction('loss'):
        a=book.get('account','strategy');a['cash']='890';book.put('account','strategy',a)
        update_periods(book,'strategy',c,now)
    assert 'period loss halt' in entry_reasons(book,'strategy','a',D(10),c,now)
    assert all(p['halted'] for p in book.get('account','strategy')['periods'].values())
    path=book.path;book.close()
    with Store(path,writable=True) as s:
        assert 'period loss halt' in entry_reasons(s,'strategy','a',D(10),c,now)


def test_kill_latency_independent_of_blocked_poll(book):
    async def measure():
        stop=asyncio.Event()
        task=asyncio.create_task(watch_halt(book,stop))
        await asyncio.sleep(.02)
        start=time.monotonic();request_halt(book.path)
        while not book.all('halt'):
            await asyncio.sleep(.01)
        elapsed=time.monotonic()-start
        stop.set();await task
        assert elapsed<2
        return elapsed
    measured=asyncio.run(measure())
    print(f'Kill switch observed in {measured:.3f} seconds')
    assert 'local halt' in entry_reasons(book,'strategy','a',D(10),Config(),utcnow())
    with pytest.raises(StorageError):
        resume(book,False)
    resume(book,True)
    assert 'local halt' not in entry_reasons(book,'strategy','a',D(10),Config(),utcnow())
