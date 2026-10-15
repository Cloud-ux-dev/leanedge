import asyncio
from dataclasses import replace
from datetime import timedelta
from leanedge.config import Config,Asset
from leanedge.core import D,stamp,utcnow
from leanedge.data import Recorder,SOL
from leanedge.engine import Engine
from leanedge.experiment import initialize
from leanedge.execution import PaperExecutor
from leanedge.halt import request_halt
from leanedge.risk import entry_reasons,equity,update_periods
from leanedge.safety import evaluate_evidence
from leanedge.storage import Store
from test_data import samples
from test_safety import good_evidence


class FakeSafety:
    def __init__(self,c):self.c=c
    async def checks(self,a,q,o,now):
        e=good_evidence(now);e['quote']['quantity']=str(q)
        return evaluate_evidence(a,o,e,q,self.c,now)


def config(frictionless=False,fail='0'):
    c=Config();a=Asset('a','p','2020-01-01T00:00:00+00:00','fixture')
    f=replace(c.fill,failure_probability=D(fail),delay_seconds=D(2))
    if frictionless:
        f=replace(f,fee_fraction=D(0),spread_fraction=D(0),slippage_fraction=D(0),adverse_fraction=D(0),max_impact=D(0))
    return replace(c,assets=(a,),fill=f)


def run_roundtrip(path,frictionless=False,fail='0'):
    c=config(frictionless,fail);now=utcnow();a=c.assets[0]
    async def run():
        with Store(path,writable=True) as s:
            initialize(s,c,'test',demo=True,now=now)
            rec=Recorder(s,c,None);rec.record(a,samples(now),now)
            e=PaperExecutor(s,c);q=D(10)
            assert e.submit('buy','strategy',a,'buy',D(20),q,now)
            assert not e.submit('buy','strategy',a,'buy',D(20),q,now)
            await e.process_due({'a':a},FakeSafety(c),now+timedelta(seconds=1))
            assert not s.all('fill')
            later=now+timedelta(seconds=10);rec.record(a,samples(later),later)
            await e.process_due({'a':a},FakeSafety(c),later)
            await e.process_due({'a':a},FakeSafety(c),later)
            assert len(s.all('fill'))==1
            if fail=='1':
                assert D(s.get('account','strategy')['cash'])==D('999.99')
                assert not s.all('position')
                return D(s.get('account','strategy')['cash'])
            assert e.submit('sell','strategy',a,'sell',D(0),q,later)
            end=now+timedelta(seconds=20);rec.record(a,samples(end,price='1.1'),end)
            await e.process_due({'a':a},FakeSafety(c),end)
            assert len(s.all('fill'))==2 and s.get('position','strategy:a')['quantity']=='0'
            account=s.get('account','strategy');cash=D(account['cash'])
            assert abs(cash-D(1000)-(D(account['gross'])-D(account['trading_costs'])))<D('.0000001')
            assert abs(D(account['realized'])-(cash-D(1000)))<D('.0000001')
            assert s.verify()[0]
            return cash
    return asyncio.run(run())


def test_pessimistic_no_better_and_fail_cost(tmp_path):
    assert run_roundtrip(tmp_path/'bad.db')<=run_roundtrip(tmp_path/'free.db',True)
    run_roundtrip(tmp_path/'failed.db',fail='1')


def test_halt_cancels_pending_but_permits_exits(tmp_path):
    c=config();now=utcnow();a=c.assets[0]
    with Store(tmp_path/'h.db',writable=True) as s:
        initialize(s,c,'test',demo=True)
        e=PaperExecutor(s,c)
        assert e.submit('pending','strategy',a,'buy',D(20),D(10),now)
        request_halt(s.path)
        asyncio.run(e.process_due({'a':a},FakeSafety(c),now+timedelta(seconds=10)))
        assert s.get('order','pending')['status']=='cancelled'
        assert not s.all('fill')
        with s.transaction('fixture position'):
            s.put('position','strategy:a',{'account':'strategy','token':'a','quantity':'1','entry_price':'1','last_price':'1'})
        assert e.submit('exit','strategy',a,'sell',D(0),D(1),now)


def test_pending_cash_limits_entries_and_cooldown(tmp_path):
    c=config();now=utcnow();a=c.assets[0]
    with Store(tmp_path/'limits.db',writable=True) as s:
        initialize(s,c,'test',demo=True)
        e=PaperExecutor(s,c)
        assert e.submit('one','strategy',a,'buy',D(20),D(10),now)
        reasons=entry_reasons(s,'strategy','a',D(20),c,now)
        assert 'duplicate token exposure' in reasons and 'token cooldown' in reasons
        for i in range(2):
            asset=Asset(str(i),'pool',stamp(now),'fixture')
            assert e.submit('more'+str(i),'strategy',asset,'buy',D(20),D(10),now)
        assert 'open position limit' in entry_reasons(s,'strategy','four',D(20),c,now)
        with s.transaction('cash fixture'):
            row=s.get('account','strategy');row['cash']='50';s.put('account','strategy',row)
        assert 'cash limit including costs' in entry_reasons(s,'strategy','four',D(20),c,now)


def test_delayed_fill_uses_current_available_price_not_backdated_price(tmp_path):
    c=config();now=utcnow();a=c.assets[0]
    async def scenario():
        with Store(tmp_path/'late.db',writable=True) as s:
            initialize(s,c,'test',demo=True)
            e=PaperExecutor(s,c);rec=Recorder(s,c,None)
            assert e.submit('buy','strategy',a,'buy',D(20),D(10),now)
            rec.record(a,samples(now+timedelta(seconds=10),price='1'),now+timedelta(seconds=10))
            rec.record(a,samples(now+timedelta(seconds=20),price='1.5'),now+timedelta(seconds=20))
            await e.process_due({'a':a},FakeSafety(c),now+timedelta(seconds=20))
            f=s.get('fill','buy')
            assert f['mid']=='1.5' and f['execution_data_time']==stamp(now+timedelta(seconds=20))
    asyncio.run(scenario())
