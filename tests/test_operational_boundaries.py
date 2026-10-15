import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import pytest
from leanedge.config import Config,Asset
from leanedge.core import D,stamp,utcnow
from leanedge.data import Recorder,ReadOnlyHTTP,DataError
from leanedge.demo import DemoSafety
from leanedge.engine import Engine
from leanedge.experiment import initialize
from leanedge.execution import PaperExecutor
from leanedge.halt import request_halt,watch_halt
from leanedge.risk import equity,entry_reasons,update_periods
from leanedge.safety import evaluate_evidence
from leanedge.storage import Store,StorageError
from test_execution import FakeSafety,config
from test_data import samples
import httpx


def test_single_writer_in_a_second_process_and_recovery(tmp_path):
    db=tmp_path/'one.db'
    with Store(db,writable=True) as s:
        initialize(s,Config(),'test',demo=True)
        code='from leanedge.storage import Store; Store(__import__("sys").argv[1],writable=True)'
        p=subprocess.run([sys.executable,'-c',code,str(db)],capture_output=True,text=True)
        assert p.returncode!=0 and 'Another decision writer' in p.stderr
    with Store(db,writable=True) as reopened:
        assert reopened.verify()[0]


def test_pending_fill_survives_restart_once(tmp_path):
    c=config();now=utcnow();db=tmp_path/'restart.db';a=c.assets[0]
    with Store(db,writable=True) as s:
        initialize(s,c,'test',demo=True,now=now)
        e=PaperExecutor(s,c)
        assert e.submit('pending','strategy',a,'buy',D(20),D(10),now)
    with Store(db,writable=True) as s:
        e=PaperExecutor(s,c);later=now+timedelta(seconds=10)
        Recorder(s,c,None).record(a,samples(later),later)
        asyncio.run(e.process_due({'a':a},FakeSafety(c),later))
        asyncio.run(e.process_due({'a':a},FakeSafety(c),later))
        assert len(s.all('fill'))==1
        assert s.get('order','pending')['status']=='filled'
        assert s.verify()[0]


def test_daily_attempt_limit_and_stale_unrealized_loss(tmp_path):
    c=config();now=utcnow()
    with Store(tmp_path/'r.db',writable=True) as s:
        initialize(s,c,'test',demo=True,now=now)
        with s.transaction('risk fixture',now):
            update_periods(s,'strategy',c,now)
            for i in range(10):
                s.put('order',str(i),{'id':str(i),'account':'strategy','side':'buy','time':stamp(now),
                           'status':'failed','token':str(i),'budget':'1'})
            a=s.get('account','strategy');a['cash']='800';s.put('account','strategy',a)
            s.put('position','strategy:a',{'id':'strategy:a','account':'strategy','token':'a','quantity':'100',
                      'entry_price':'2','last_price':'1','entry_cost':'200','high':'2','opened':stamp(now),'passive':False})
            update_periods(s,'strategy',c,now)
        eq,healthy=equity(s,'strategy',c,now)
        assert eq==900 and not healthy
        reasons=entry_reasons(s,'strategy','new',D(10),c,now)
        assert 'daily entry limit' in reasons and 'unreliable valuation' in reasons and 'period loss halt' in reasons
        with s.transaction('next day unhealthy',now+timedelta(days=1)):
            update_periods(s,'strategy',c,now+timedelta(days=1))
        assert s.get('account','strategy')['periods']['day']['halted']


def test_halt_during_blocked_market_task(tmp_path):
    async def scenario():
        with Store(tmp_path/'kill.db',writable=True) as s:
            initialize(s,Config(),'test',demo=True)
            stop=asyncio.Event();network_block=asyncio.Event()
            poll=asyncio.create_task(network_block.wait());watch=asyncio.create_task(watch_halt(s,stop))
            request_halt(s.path)
            for _ in range(150):
                if s.all('halt'):break
                await asyncio.sleep(.01)
            assert s.all('halt') and not poll.done()
            stop.set();poll.cancel();await watch
    asyncio.run(scenario())


def test_synthetic_safety_refused_for_real_database(tmp_path):
    with Store(tmp_path/'real.db',writable=True) as s:
        initialize(s,Config(),'real')
        with pytest.raises(ValueError,match='Synthetic'):
            DemoSafety(s,Config())


def test_retry_rate_budget_and_timeout_are_bounded(tmp_path):
    async def scenario():
        c=Config();c=replace(c,data=replace(c.data,retries=0))
        with Store(tmp_path/'network.db',writable=True) as s:
            initialize(s,c,'test',demo=True)
            count=0
            def fail(req):
                nonlocal count;count+=1
                raise httpx.ReadTimeout('test-secret-must-not-leak',request=req)
            h=ReadOnlyHTTP(s,c,httpx.AsyncClient(transport=httpx.MockTransport(fail)))
            with pytest.raises(DataError) as e:
                await h.request('dexscreener','pair','https://api.dexscreener.com/latest/dex/pairs/solana/p')
            assert count==1 and 'test-secret' not in str(e.value)
            assert s.all('observation')[0]['status']==0
            await h.close()
    asyncio.run(scenario())


def test_survival_ends_without_restart(tmp_path):
    c=Config();now=utcnow()
    with Store(tmp_path/'s.db',writable=True) as s:
        initialize(s,c,'test',demo=True)
        with s.transaction('actual costs fixture'):
            s.put('cost','cost',{'id':'cost','time':stamp(now),'amount':'8000','category':'hosting','account':None})
        engine=Engine(s,c,DemoSafety(s,c))
        asyncio.run(engine.snapshot(now))
        assert s.experiment()['ended'] and s.get('account','strategy')['ended']
        assert 'experiment ended' in entry_reasons(s,'strategy','a',D(1),c,now)


def test_disagreement_blocks_pending_fill_even_with_recent_good_price(tmp_path):
    c=config();now=utcnow();a=c.assets[0]
    with Store(tmp_path/'disagreement.db',writable=True) as s:
        initialize(s,c,'test',demo=True)
        e=PaperExecutor(s,c)
        e.submit('pending','strategy',a,'buy',D(20),D(10),now)
        recorder=Recorder(s,c,None);due=now+timedelta(seconds=10)
        recorder.record(a,samples(due),due)
        bad=samples(due+timedelta(seconds=1));bad[1]['price']='2'
        recorder.record(a,bad,due+timedelta(seconds=1))
        asyncio.run(e.process_due({'a':a},FakeSafety(c),due+timedelta(seconds=1)))
        assert s.get('order','pending')['status']=='rejected'
        assert not s.all('fill')


def test_process_crash_rolls_back_uncommitted_financial_state(tmp_path):
    db=tmp_path/'crash.db'
    with Store(db,writable=True) as s:
        initialize(s,Config(),'crash-fixture',demo=True)
    code='''
import os,sys
from leanedge.storage import Store
with Store(sys.argv[1],writable=True) as s:
    with s.transaction('interrupted fixture transaction'):
        a=s.get('account','strategy');a['cash']='1';s.put('account','strategy',a)
        os._exit(7)
'''
    r=subprocess.run([sys.executable,'-c',code,str(db)],capture_output=True,text=True,timeout=10)
    assert r.returncode==7
    with Store(db,writable=True) as s:
        assert s.get('account','strategy')['cash']=='1000' and s.verify()[0]


def test_continuous_engine_stops_while_async_transport_is_blocked(tmp_path):
    from leanedge.engine import operate
    from leanedge.demo import DemoSafety
    async def scenario():
        c=config();now=utcnow()
        with Store(tmp_path/'async-stop.db',writable=True) as s:
            initialize(s,c,'blocked-transport-fixture',demo=True,now=now)
            forever=asyncio.Event()
            class BlockedRecorder:
                async def poll(self,asset):
                    await forever.wait()
            await asyncio.wait_for(operate(Engine(s,c,DemoSafety(s,c)),BlockedRecorder(),duration=.05),timeout=2)
            assert s.verify()[0]
    asyncio.run(scenario())
