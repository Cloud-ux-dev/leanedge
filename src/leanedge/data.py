"""Public read-only requests; recorded snapshots are never called a trade feed."""
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import time
import uuid
import httpx
from .core import D, digest, instant, number, redact, stamp, utcnow
from .costs import reserve, spending_mode

SOL='So11111111111111111111111111111111111111112'
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
SPL='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'


class DataError(RuntimeError):
    """Only fixed messages, never response bodies or transport exceptions."""


@dataclass
class Response:
    body: dict
    time: datetime
    reference: str


class ReadOnlyHTTP:
    def __init__(self,store,config,client=None):
        self.store,self.config=store,config
        self.client=client or httpx.AsyncClient(timeout=config.data.timeout_seconds,follow_redirects=False)
        self.cache={}
        self.next_request={}
        self.locks=defaultdict(asyncio.Lock)
        self.retry_tokens=20
        self.retry_window=time.monotonic()

    async def close(self):
        await self.client.aclose()

    async def request(self,provider,endpoint,url,*,params=None,payload=None,headers=None,cost=D(0)):
        allowed={'dexscreener':('https://api.dexscreener.com/',{'pair'}),
                 'geckoterminal':('https://api.geckoterminal.com/',{'pool'}),
                 'helius':('https://mainnet.helius-rpc.com/',{'getAccountInfo','getTokenAccounts','getSlot'}),
                 'jupiter':('https://api.jup.ag/swap/v1/quote',{'quote'})}
        if provider not in allowed or not url.startswith(allowed[provider][0]) or endpoint not in allowed[provider][1]:
            raise DataError('Unsupported read-only request')
        key=digest({'provider':provider,'endpoint':endpoint,'params':redact(params),'payload':payload})
        cached=self.cache.get(key)
        now=utcnow()
        if cached and (now-cached.time).total_seconds()<=self.config.data.cache_seconds:
            return cached
        if cost and spending_mode(self.store,self.config,now)!='NORMAL':
            raise DataError('Optional paid data paused by spending policy; entries must wait')
        async with self.locks[provider]:
            for attempt in range(self.config.data.retries+1):
                wait=max(0,self.next_request.get(provider,0)-time.monotonic())
                if wait:
                    await asyncio.sleep(wait)
                # Deliberately below public advertised limits; configurable poll size still matters.
                self.next_request[provider]=time.monotonic()+{'dexscreener':.25,'geckoterminal':6,'helius':.1,'jupiter':1}[provider]
                rid=uuid.uuid4().hex
                now=utcnow()
                if cost and not reserve(self.store,'http:'+rid,cost,provider,now,self.config):
                    raise DataError('Operating budget blocks request')
                status=0; body={}; finding='request failed';retry=False;retry_after=0
                try:
                    response=await self.client.request('POST' if payload is not None else 'GET',url,
                                                      params=params,json=payload,headers=headers,
                                                      timeout=self.config.data.timeout_seconds)
                    status=response.status_code
                    if len(response.content)>8_000_000:
                        raise DataError('Provider response exceeds size bound')
                    body=json.loads(response.content,parse_float=str)
                    if not isinstance(body,dict):
                        raise ValueError()
                    retry=status==429 or status>=500
                    try:
                        retry_after=min(30,max(0,float(response.headers.get('retry-after','0'))))
                    except ValueError:
                        retry_after=0
                    finding='ok' if status==200 and 'error' not in body else 'provider refused request'
                except (httpx.HTTPError,ValueError,UnicodeError,DataError):
                    retry=True
                observed=utcnow()
                clean=redact(body)
                with self.store.transaction('raw_observation',observed):
                    self.store.put('observation','raw:'+rid,{'id':'raw:'+rid,'provider':provider,'endpoint':endpoint,
                        'time':stamp(observed),'source_time':None,'status':status,'normalized':False,
                        'request_hash':key,'content_hash':digest(clean),'body':clean,'findings':[finding]})
                if finding=='ok':
                    value=Response(body,observed,'raw:'+rid)
                    self.cache[key]=value
                    return value
                if not retry or attempt==self.config.data.retries:
                    break
                if time.monotonic()-self.retry_window>=60:
                    self.retry_window=time.monotonic();self.retry_tokens=20
                if self.retry_tokens<=0:
                    break
                self.retry_tokens-=1
                await asyncio.sleep(max(retry_after,min(8,2**attempt)))
        raise DataError('Market provider unavailable; see recorded status')


class MarketSources:
    def __init__(self,http):
        self.http=http

    async def dex(self,asset):
        r=await self.http.request('dexscreener','pair',f'https://api.dexscreener.com/latest/dex/pairs/solana/{asset.pool}')
        try:
            pairs=r.body['pairs']
            p=next(p for p in pairs if p['pairAddress']==asset.pool)
            if p['chainId']!='solana' or p['baseToken']['address']!=asset.token:
                raise ValueError()
            created=None
            if p.get('pairCreatedAt') is not None:
                created=datetime.fromtimestamp(float(number(p['pairCreatedAt']))/1000,timezone.utc)
            return {'provider':'dexscreener','token':asset.token,'pool':asset.pool,'time':stamp(r.time),
                    'source_time':None,'price':str(number(p['priceUsd'],positive=True)),
                    'liquidity':str(number(p['liquidity']['usd'],positive=True)),
                    'volume24h':str(number(p['volume']['h24'])),
                    'pool_created':stamp(created) if created else None,'reference':r.reference}
        except (KeyError,TypeError,ValueError,StopIteration,OverflowError):
            raise DataError('DexScreener returned missing, impossible, or wrong-asset data') from None

    async def gecko(self,asset):
        r=await self.http.request('geckoterminal','pool',f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{asset.pool}')
        try:
            p=r.body['data'];a=p['attributes']
            if a['address']!=asset.pool or p['relationships']['base_token']['data']['id']!='solana_'+asset.token:
                raise ValueError()
            created=instant(a['pool_created_at']) if a.get('pool_created_at') else None
            return {'provider':'geckoterminal','token':asset.token,'pool':asset.pool,'time':stamp(r.time),
                    'source_time':None,'price':str(number(a['base_token_price_usd'],positive=True)),
                    'liquidity':str(number(a['reserve_in_usd'],positive=True)),
                    'volume24h':str(number(a['volume_usd']['h24'])),
                    'pool_created':stamp(created) if created else None,'reference':r.reference}
        except (KeyError,TypeError,ValueError):
            raise DataError('GeckoTerminal returned missing, impossible, or wrong-asset data') from None


def validate_observations(observations,asset,config,now):
    findings=[]
    if len(observations)!=2 or {o['provider'] for o in observations}!={'dexscreener','geckoterminal'}:
        findings.append('both independent providers required')
    for o in observations:
        try:
            if o['token']!=asset.token or o['pool']!=asset.pool:
                findings.append('wrong asset')
            for field in ('price','liquidity'):
                number(o[field],field,positive=True)
            number(o['volume24h'],'volume')
            for t in [o['time'], *([o['source_time']] if o.get('source_time') else [])]:
                age=(now-instant(t)).total_seconds()
                if age<0 or age>config.data.fresh_seconds:
                    findings.append('stale or future observation')
            if o.get('pool_created') and instant(o['pool_created'])>now:
                findings.append('future pool creation')
        except (ValueError,KeyError,TypeError):
            findings.append('invalid fields')
    if not findings and observations:
        prices=[D(o['price']) for o in observations]
        if max(prices)/min(prices)-1>config.data.disagreement_fraction:
            findings.append('unexplained provider price disagreement')
        liquidity=[D(o['liquidity']) for o in observations]
        if max(liquidity)/min(liquidity)-1>D('.25'):
            findings.append('unexplained provider liquidity disagreement')
    return sorted(set(findings))


class Recorder:
    def __init__(self,store,config,sources):
        self.store,self.config,self.sources=store,config,sources

    def record(self,asset,observations,now,extra_findings=None):
        findings=validate_observations(observations,asset,self.config,now)+list(extra_findings or [])
        key=digest({'asset':asset.token,'time':stamp(now),'refs':[o['reference'] for o in observations]})
        with self.store.transaction('normalized_sample',now):
            self.store.put('health',asset.token,{'token':asset.token,'time':stamp(now),'ok':not findings,'findings':findings})
            for o in observations:
                self.store.put('observation','source:'+o['reference'],dict(o,id='source:'+o['reference'],
                               normalized=False,status=200,findings=findings),once=True)
            if findings:
                return None
            # Conservative low price and liquidity; oldest acquisition time survives cache reuse.
            old=min(o['time'] for o in observations)
            out={'id':key,'token':asset.token,'pool':asset.pool,'time':old,'received_time':stamp(now),
                 'source_time':None,'normalized':True,'valid':True,'provider':'consensus',
                 'price':str(min(D(o['price']) for o in observations)),
                 'liquidity':str(min(D(o['liquidity']) for o in observations)),
                 'pool_created':max((o['pool_created'] for o in observations if o.get('pool_created')),default=None),
                 'volume24h':str(min(D(o['volume24h']) for o in observations)),
                 'references':[o['reference'] for o in observations], 'findings':[]}
            self.store.put('observation',key,out,once=True)
            self._bar(out)
            return out

    def _bar(self,o):
        t=instant(o['time']);width=self.config.strategy.bar_seconds
        bucket=int(t.timestamp())//width*width
        key=o['token']+':'+str(bucket)
        start=datetime.fromtimestamp(bucket,timezone.utc)
        b=self.store.get('bar',key)
        if b is None:
            b={'id':key,'token':o['token'],'time':stamp(start),'end':stamp(start+timedelta(seconds=width)),
               'open':o['price'],'high':o['price'],'low':o['price'],'close':o['price'],
               'first':o['time'],'last':o['time'],'samples':0,'max_gap':0,'kind':'sampled snapshots'}
        if o['time']<=b['last'] and b['samples']:
            return
        b['max_gap']=max(b['max_gap'],(t-instant(b['last'])).total_seconds())
        b['high']=str(max(D(b['high']),D(o['price'])))
        b['low']=str(min(D(b['low']),D(o['price'])))
        b['close']=o['price'];b['last']=o['time'];b['samples']+=1
        self.store.put('bar',key,b)

    async def poll(self,asset):
        results=await asyncio.gather(self.sources.dex(asset),self.sources.gecko(asset),return_exceptions=True)
        observations=[r for r in results if isinstance(r,dict)]
        return self.record(asset,observations,utcnow(),['provider request failed'] if len(observations)!=2 else [])


def data_gaps(store,config,now):
    exp=store.experiment()
    start=instant(exp['time'])
    assets=[a.token for a in config.assets]+([SOL] if config.sol_pool else [])
    rows=[]
    for token in dict.fromkeys(assets):
        obs=sorted([o for o in store.all('observation') if o.get('normalized') and o.get('valid') and
                    o['token']==token and start<=instant(o['time'])<=now],key=lambda x:x['time'])
        times=sorted(set(instant(o['time']) for o in obs))
        intervals=[];covered=D(0)
        cursor=start
        for t in times:
            # A sample supports at most one polling interval; future gaps are not backfilled.
            if t>cursor:
                intervals.append({'from':stamp(cursor),'to':stamp(t),'seconds':(t-cursor).total_seconds()})
            covered+=D(str(max(0,(min(now,t+timedelta(seconds=config.data.poll_seconds))-max(t,cursor)).total_seconds())))
            cursor=max(cursor,t+timedelta(seconds=config.data.poll_seconds))
        if cursor<now:
            intervals.append({'from':stamp(cursor),'to':stamp(now),'seconds':(now-cursor).total_seconds()})
        elapsed=D(str(max(0,(now-start).total_seconds())))
        rows.append({'token':token,'observations':len(times),'coverage':str(min(D(1),covered/elapsed) if elapsed else D(0)),
                     'max_gap_seconds':max((g['seconds'] for g in intervals),default=0),'gaps':intervals})
    return {'time':stamp(now),'label':exp['label'],'assets':rows}
