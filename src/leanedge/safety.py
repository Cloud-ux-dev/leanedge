"""Every safety assertion needs dated evidence; unknown always rejects an entry."""
from collections import defaultdict
from dataclasses import dataclass,asdict
from enum import Enum
from .core import D,digest,instant,number,stamp
from .data import DataError,SPL,USDC


class Result(str,Enum):
    PASS='PASS'
    FAIL='FAIL'
    UNKNOWN='UNKNOWN'


@dataclass(frozen=True)
class Check:
    name:str
    result:Result
    source:str
    time:str
    reason:str


REQUIRED={'liquidity','age','mint_authority','freeze_authority','holders','liquidity_protection','token_program','sell_quote'}


def passed(checks,config,now):
    return {c.name for c in checks}==REQUIRED and len(checks)==len(REQUIRED) and all(
        c.result==Result.PASS and 0<=(now-instant(c.time)).total_seconds()<=config.data.safety_fresh_seconds
        for c in checks)


def evaluate_evidence(asset,observation,evidence,quantity,config,now):
    checks=[]
    def check(name,value,source,when,reason):
        status=Result.UNKNOWN if value is None else Result.PASS if value else Result.FAIL
        try:
            fresh=0<=(now-instant(when)).total_seconds()<=config.data.safety_fresh_seconds
        except (ValueError,TypeError):
            fresh=False
        if not fresh:
            status=Result.UNKNOWN;reason='missing or stale evidence'
        checks.append(Check(name,status,source,when or stamp(now),reason))
    market=observation or {}
    t=market.get('time')
    check('liquidity',D(market['liquidity'])>=config.risk.min_liquidity if market.get('valid') else None,
          'independent market consensus',t,'minimum USD liquidity')
    # A verified creation date of this pool is a lower bound on the token's age.
    created=market.get('pool_created')
    check('age',(now-instant(created)).total_seconds()>=config.risk.min_age_hours*3600 if created else None,
          'pool existence lower bound on token age',t,'token existed when this pool was created')
    mint=evidence.get('mint',{})
    when=mint.get('time');bound=mint.get('token')==asset.token
    for name,key in [('mint_authority','mintAuthority'),('freeze_authority','freezeAuthority')]:
        check(name,(mint[key] is None) if bound and key in mint else None,'Helius getAccountInfo',when,'authority must be explicitly revoked')
    check('token_program',mint.get('owner')==SPL and mint.get('extensions')==[] if bound and 'owner' in mint else None,
          'Helius parsed mint program',when,'only ordinary SPL token behavior is supported; Token-2022 rejected')
    holders=evidence.get('holders',{})
    ratio=holders.get('top10')
    check('holders',D(ratio)<=config.risk.max_top10 if holders.get('complete') and
          holders.get('token')==asset.token and ratio is not None else None,
          'Helius full token-account enumeration grouped by owner',holders.get('time'),
          'all pages, supply reconciliation, fresh indexing, no frozen/delegated behavior required')
    proof=evidence.get('liquidity_protection',{})
    # No user boolean or config value can manufacture a live pool-specific verification.
    check('liquidity_protection',proof.get('verified') if proof.get('pool')==asset.pool and
          proof.get('pool_type')==asset.pool_type and proof.get('verifier')=='synthetic-fixture-only' and
          evidence.get('_demo') else None,
          proof.get('verifier','no verified decoder for this pool type'),proof.get('time'),
          'pool-specific protection is unavailable; generic liquidity and quotes are not proof')
    quote=evidence.get('quote',{})
    valid=None
    if quote and quote.get('token')==asset.token:
        valid=(D(quote.get('quantity','0'))==quantity and D(quote.get('out_usd','0'))>0 and quote.get('route') is True)
    check('sell_quote',valid,'Jupiter read-only exact-size quote',quote.get('time'),
          'a quote does not prove that a real sale would succeed')
    return checks


def record_checks(store,key,account,token,checks,now):
    if not store.experiment()['demo'] and any(c.source=='synthetic-fixture-only' for c in checks):
        raise ValueError('Synthetic safety evidence cannot be used in a real-data experiment')
    with store.transaction('safety_evaluated',now):
        store.put('safety',key,{'id':key,'account':account,'token':token,'time':stamp(now),
                  'checks':[asdict(c) for c in checks]})


class LiveSafety:
    """Read-only authority, owner concentration and sell quotes. LP decoder unavailable."""
    def __init__(self,http,secrets,config,store):
        self.http,self.secrets,self.config,self.store=http,secrets,config,store

    async def rpc(self,method,params):
        if not self.secrets.helius:
            raise DataError('Helius credentials unavailable')
        r=await self.http.request('helius',method,'https://mainnet.helius-rpc.com/',
            params={'api-key':self.secrets.helius},payload={'jsonrpc':'2.0','id':'leanedge','method':method,'params':params},
            cost=self.config.costs.rpc_request_usd)
        if 'result' not in r.body:
            raise DataError('Read-only RPC result unavailable')
        return r.body['result'],r

    async def evidence(self,asset,quantity,observation,now):
        evidence={}
        decimals=None
        try:
            mint,r=await self.rpc('getAccountInfo',[asset.token,{'encoding':'jsonParsed','commitment':'finalized'}])
            v=mint['value'];info=v['data']['parsed']['info']
            if v['data']['parsed']['type']!='mint':
                raise ValueError()
            decimals=info['decimals']
            if type(decimals) is not int or not 0<=decimals<=18:
                raise ValueError()
            out={'token':asset.token,'owner':v['owner'],'time':stamp(r.time)}
            for k in ('mintAuthority','freezeAuthority'):
                if k in info:
                    out[k]=info[k]
            out['extensions']=info.get('extensions',[])
            evidence['mint']=out
            supply=number(info['supply'],positive=True)
            slot=mint['context']['slot']
            totals=defaultdict(lambda:D(0));seen=set();cursor=None;complete=False;bad=False
            for _ in range(self.config.data.max_holder_pages):
                params={'mint':asset.token,'limit':1000,'options':{'showZeroBalance':True}}
                if cursor:
                    params['cursor']=cursor
                page,pr=await self.rpc('getTokenAccounts',params)
                indexed=page.get('last_indexed_slot')
                if type(indexed) is not int or abs(slot-indexed)>150:
                    bad=True
                for a in page['token_accounts']:
                    if a['mint']!=asset.token or a['address'] in seen:
                        bad=True;continue
                    seen.add(a['address'])
                    totals[a['owner']]+=number(a['amount'])
                    if a.get('frozen') is not False or number(a.get('delegated_amount',0))>0:
                        bad=True
                new_cursor=page.get('cursor')
                if not new_cursor:
                    complete=True;break
                if new_cursor==cursor:
                    bad=True;break
                cursor=new_cursor
            total=sum(totals.values(),D(0))
            complete=complete and not bad and total==supply and len(totals)>0
            evidence['holders']={'token':asset.token,'time':stamp(pr.time),'complete':complete,
                                 'top10':str(sum(sorted(totals.values(),reverse=True)[:10],D(0))/supply)}
        except (DataError,KeyError,TypeError,ValueError,UnboundLocalError):
            pass  # Missing evidence is explicitly UNKNOWN, never a pass.
        if decimals is not None and self.secrets.jupiter:
            try:
                atomic=int(quantity*(D(10)**decimals))
                if atomic<=0 or D(atomic)/(D(10)**decimals)!=quantity:
                    raise ValueError()
                r=await self.http.request('jupiter','quote','https://api.jup.ag/swap/v1/quote',
                    params={'inputMint':asset.token,'outputMint':USDC,'amount':str(atomic),'slippageBps':50,'swapMode':'ExactIn'},
                    headers={'x-api-key':self.secrets.jupiter},cost=self.config.costs.other_request_usd)
                q=r.body
                if q['inputMint']!=asset.token or q['outputMint']!=USDC or int(q['inAmount'])!=atomic or q['swapMode']!='ExactIn':
                    raise ValueError()
                evidence['quote']={'token':asset.token,'quantity':str(quantity),'out_usd':str(number(q['otherAmountThreshold'])/D(10**6)),
                                   'route':bool(q['routePlan']),'time':stamp(r.time),'reference':r.reference}
            except (DataError,KeyError,TypeError,ValueError):
                pass
        return evidence

    async def checks(self,asset,quantity,observation,now):
        ev=await self.evidence(asset,quantity,observation,now)
        # Re-evaluate time after calls; slow evidence must not remain apparently fresh.
        from .core import utcnow
        return evaluate_evidence(asset,observation,ev,quantity,self.config,utcnow())
