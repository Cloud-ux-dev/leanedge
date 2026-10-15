import asyncio
from datetime import timedelta
from leanedge.config import Config,Asset
from leanedge.core import stamp,utcnow,D
from leanedge.data import ReadOnlyHTTP,Recorder,validate_observations,DataError
from leanedge.experiment import initialize
from leanedge.storage import Store
import httpx
import pytest


def samples(now,token='a',pool='p',price='1'):
    return [dict(provider=p,token=token,pool=pool,time=stamp(now),source_time=None,
                 price=price,liquidity='100000',volume24h='1000',pool_created=stamp(now-timedelta(days=7)),reference=p+stamp(now))
            for p in ['dexscreener','geckoterminal']]


def test_quality_cases(tmp_path):
    c=Config();now=utcnow();a=Asset('a','p',stamp(now))
    obs=samples(now)
    assert not validate_observations(obs,a,c,now)
    obs[0]['price']='1.20'
    assert 'unexplained provider price disagreement' in validate_observations(obs,a,c,now)
    assert validate_observations(samples(now-timedelta(minutes=5)),a,c,now)
    assert validate_observations(samples(now,token='wrong'),a,c,now)
    assert validate_observations(samples(now,price='NaN'),a,c,now)
    assert validate_observations([],a,c,now)


def test_record_cache_and_safe_raw(tmp_path):
    async def scenario():
        with Store(tmp_path/'d.db',writable=True) as s:
            initialize(s,Config(),'test',demo=True)
            calls=[]
            def fake(req):
                calls.append(req)
                return httpx.Response(200,json={'price':'1','url':'https://bad.test/?api-key=private-marker','api_key':'private-marker'})
            client=httpx.AsyncClient(transport=httpx.MockTransport(fake))
            h=ReadOnlyHTTP(s,Config(),client)
            r=await h.request('dexscreener','pair','https://api.dexscreener.com/latest/dex/pairs/solana/p')
            r2=await h.request('dexscreener','pair','https://api.dexscreener.com/latest/dex/pairs/solana/p')
            assert r.time==r2.time and len(calls)==1
            assert 'private-marker' not in str(s.all('observation'))
            now=utcnow();a=Asset('a','p',stamp(now));rec=Recorder(s,Config(),None)
            assert rec.record(a,samples(now),now)
            assert s.all('bar')[0]['kind']=='sampled snapshots'
            assert s.verify()[0]
            await h.close()
    asyncio.run(scenario())
