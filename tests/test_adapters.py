"""Provider-contract fixtures only; no real credentials or remote calls."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import httpx
import pytest
from leanedge.config import Config,Asset,Secrets
from leanedge.core import D,stamp,utcnow
from leanedge.data import ReadOnlyHTTP,MarketSources,DataError,SPL,USDC
from leanedge.experiment import initialize
from leanedge.safety import LiveSafety,evaluate_evidence,passed,Result
from leanedge.storage import Store


def test_market_adapter_identity_and_fields(tmp_path):
    async def run():
        c=Config();now=utcnow();asset=Asset('a','p',stamp(now))
        def transport(req):
            if req.url.host=='api.dexscreener.com':
                return httpx.Response(200,json={'pairs':[{'chainId':'solana','pairAddress':'p','baseToken':{'address':'a'},
                        'priceUsd':'1.25','liquidity':{'usd':100000},'volume':{'h24':20000},'pairCreatedAt':1700000000000}]})
            return httpx.Response(200,json={'data':{'attributes':{'address':'p','base_token_price_usd':'1.25',
                      'reserve_in_usd':'100000','volume_usd':{'h24':'20000'},'pool_created_at':'2020-01-01T00:00:00Z'},
                       'relationships':{'base_token':{'data':{'id':'solana_a'}}}}})
        with Store(tmp_path/'m.db',writable=True) as s:
            initialize(s,c,'test',demo=True)
            h=ReadOnlyHTTP(s,c,httpx.AsyncClient(transport=httpx.MockTransport(transport)));sources=MarketSources(h)
            dex=await sources.dex(asset);gecko=await sources.gecko(asset)
            assert dex['price']==gecko['price']=='1.25' and dex['pool_created']
            with pytest.raises(DataError,match='wrong-asset'):
                await sources.dex(Asset('wrong','p',stamp(now)))
            with pytest.raises(DataError,match='wrong-asset'):
                await sources.gecko(Asset('wrong','p',stamp(now)))
            assert 'Bearer' not in str(s.all('observation'))
            await h.close()
    asyncio.run(run())


def test_helius_owner_grouping_and_jupiter_exact_size(tmp_path):
    async def run():
        c=Config();now=utcnow();asset=Asset('a','p',stamp(now),'unverified');requests=[]
        def transport(req):
            requests.append(req)
            if req.url.host=='api.jup.ag':
                assert req.headers['x-api-key']=='test-only-jupiter'
                return httpx.Response(200,json={'inputMint':'a','outputMint':USDC,'inAmount':'10000000','swapMode':'ExactIn',
                        'otherAmountThreshold':'9000000','routePlan':[{'percent':100}]})
            import json
            method=json.loads(req.content)['method']
            if method=='getAccountInfo':
                result={'context':{'slot':1000},'value':{'owner':SPL,'data':{'parsed':{'type':'mint','info':{
                        'decimals':6,'supply':'100000000','mintAuthority':None,'freezeAuthority':None}}}}}
            else:
                result={'last_indexed_slot':1000,'cursor':None,'token_accounts':[
                    {'address':str(i),'owner':'owner'+str(i//2),'mint':'a','amount':500000,'frozen':False,'delegated_amount':0}
                    for i in range(200)]}
            return httpx.Response(200,json={'jsonrpc':'2.0','result':result})
        with Store(tmp_path/'s.db',writable=True) as s:
            initialize(s,c,'test',demo=True)
            h=ReadOnlyHTTP(s,c,httpx.AsyncClient(transport=httpx.MockTransport(transport)))
            live=LiveSafety(h,Secrets('test-only-helius','test-only-jupiter',''),c,s)
            evidence=await live.evidence(asset,D(10),None,now)
            assert evidence['holders']['complete'] and D(evidence['holders']['top10'])==D('.1')
            assert evidence['quote']['quantity']=='10' and evidence['quote']['out_usd']=='9'
            o={'valid':True,'time':stamp(utcnow()),'liquidity':'100000','pool_created':'2020-01-01T00:00:00Z'}
            checks=evaluate_evidence(asset,o,evidence,D(10),c,utcnow())
            assert not passed(checks,c,utcnow())
            assert next(x for x in checks if x.name=='liquidity_protection').result==Result.UNKNOWN
            raw=str(s.all('observation'))
            assert 'test-only-helius' not in raw and 'test-only-jupiter' not in raw
            assert len(requests)==3 and s.verify()[0]
            await h.close()
    asyncio.run(run())


def test_pagination_and_wrong_quote_cannot_pass(tmp_path):
    async def run():
        c=Config();c=replace(c,data=replace(c.data,max_holder_pages=1))
        now=utcnow();asset=Asset('a','p',stamp(now));calls=[]
        class FakeRPC:
            async def request(self,provider,endpoint,url,**kwargs):
                from leanedge.data import Response
                calls.append(endpoint)
                if endpoint=='getAccountInfo':
                    result={'context':{'slot':100},'value':{'owner':SPL,'data':{'parsed':{'type':'mint','info':{
                        'decimals':6,'supply':'1000','mintAuthority':None,'freezeAuthority':None}}}}}
                elif endpoint=='getTokenAccounts':
                    result={'last_indexed_slot':100,'cursor':'more-data','token_accounts':[
                        {'address':'x','owner':'owner','mint':'a','amount':1000,'frozen':False,'delegated_amount':0}]}
                else:
                    return Response({'inputMint':'wrong','outputMint':USDC,'inAmount':'10000000','swapMode':'ExactIn'},utcnow(),'fake')
                return Response({'result':result},utcnow(),'fake')
        with Store(tmp_path/'bad.db',writable=True) as s:
            initialize(s,c,'test',demo=True)
            e=await LiveSafety(FakeRPC(),Secrets('test-only','test-only',''),c,s).evidence(asset,D(10),None,now)
            assert not e['holders']['complete'] and 'quote' not in e
            assert calls==['getAccountInfo','getTokenAccounts','quote']
    asyncio.run(run())
