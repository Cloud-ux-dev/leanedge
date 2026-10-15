"""AI may only veto or shrink an existing proposal; no tools or raw token text."""
import asyncio
import json
import uuid
import httpx
from .core import D,canonical,digest,instant,number,stamp,utcnow
from .costs import reserve,settle,spending_mode


class AdviceError(RuntimeError):
    pass


def parse_advice(raw):
    try:
        def unique(pairs):
            result={}
            for k,v in pairs:
                if k in result:
                    raise ValueError()
                result[k]=v
            return result
        data=json.loads(raw,parse_float=str,object_pairs_hook=unique)
        if not isinstance(data,dict) or set(data)!={'action','size_fraction'}:
            raise ValueError()
        action=data['action'];factor=number(data['size_fraction'])
        if action not in {'accept','reject','reduce'} or factor>1 or (
            action=='accept' and factor!=1 or action=='reject' and factor!=0 or action=='reduce' and not 0<factor<1):
            raise ValueError()
        return action,factor
    except (ValueError,TypeError):
        raise AdviceError('AI advice failed strict schema validation') from None


class Adviser:
    def __init__(self,store,config,secret,client=None):
        self.store,self.config,self.secret=store,config,secret
        self.client=client or httpx.AsyncClient(timeout=8,follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def advise(self,proposal_id,numeric,account,now):
        c=self.config.ai
        if not c.enabled:
            return D(1)
        # An allowlist of numeric features prevents metadata-based prompt injection.
        allowed={'budget_usd','price_usd','liquidity_usd','momentum','pullback','equity_usd'}
        if set(numeric)-allowed:
            raise AdviceError('AI input contains unsupported fields')
        try:
            safe={k:str(number(v)) for k,v in numeric.items()}
        except ValueError:
            raise AdviceError('AI input is invalid') from None
        prompt=canonical(safe)
        pricing_age=(now-instant(c.pricing_verified)).total_seconds()
        if not self.secret or not 0<=pricing_age<=c.pricing_max_days*86400:
            raise AdviceError('AI credentials or verified current pricing unavailable')
        if spending_mode(self.store,self.config,now)!='NORMAL':
            raise AdviceError('AI optional requests paused by spending policy')
        system=('Return JSON only with exactly action and size_fraction. action is accept, reject, or reduce. '
                'accept requires size_fraction 1; reject requires 0; reduce requires a number between 0 and 1. '
                'You advise on an existing paper proposal. You cannot create orders or increase risk. '
                'Use only the provided numeric fields. No tools, code, text, or extra fields.')
        # UTF-8 byte count plus overhead conservatively bounds the tiny ASCII prompt's input tokens.
        if len((system+prompt).encode())+512>c.input_token_bound:
            raise AdviceError('AI input token bound exceeded')
        bound=(D(c.input_token_bound)*c.input_per_million+D(c.max_output_tokens)*c.output_per_million)/D(1_000_000)
        for attempt in range(c.retries+1):
            daily=sum((D(x['amount']) for x in self.store.all('cost') if x['category']=='ai' and x['time'][:10]==now.date().isoformat()),D(0))
            if daily+bound>c.daily_cap:
                raise AdviceError('AI daily spending cap reached')
            key='ai:'+proposal_id+':'+str(attempt)
            if not reserve(self.store,key,bound,'ai',now,self.config,account=account,
                           details={'input_bound':c.input_token_bound,'output_bound':c.max_output_tokens}):
                raise AdviceError('AI cost reservation refused or proposal already attempted')
            valid=False;reason='AI request unavailable';reply=None;malformed=False
            try:
                r=await self.client.post('https://api.deepseek.com/chat/completions',headers={'Authorization':'Bearer '+self.secret},
                    json={'model':c.model,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
                          'response_format':{'type':'json_object'},'thinking':{'type':'disabled'},
                          'max_tokens':c.max_output_tokens,'stream':False},timeout=8)
                if r.status_code!=200 or len(r.content)>100_000:
                    raise AdviceError('AI request refused')
                body=r.json();usage=body['usage'];completion=body['choices'][0]
                if completion.get('finish_reason')!='stop':
                    raise AdviceError('AI response incomplete')
                total=usage['prompt_tokens'];out=usage['completion_tokens'];cached=usage.get('prompt_cache_hit_tokens',0)
                if any(type(n) is not int or n<0 for n in [total,out,cached]) or cached>total or total>c.input_token_bound or out>c.max_output_tokens:
                    raise AdviceError('AI usage is uncertain or exceeded reserved bounds')
                cost=(D(total-cached)*c.input_per_million+D(cached)*c.cached_per_million+D(out)*c.output_per_million)/D(1_000_000)
                settle(self.store,key,cost,now,{'input_tokens':total,'cached_tokens':cached,'output_tokens':out})
                malformed=True
                action,factor=parse_advice(completion['message']['content'])
                malformed=False
                reply={'action':action,'size_fraction':str(factor)};valid=True;reason='validated advice'
            except (httpx.HTTPError,ValueError,KeyError,TypeError,AdviceError):
                factor=D(0)
            with self.store.transaction('ai_advice',now):
                self.store.put('ai',key,{'id':key,'proposal':proposal_id,'time':stamp(now),'valid':valid,
                              'reply':reply,'reason':reason,'prompt_hash':digest(safe),'cost_reference':key})
            if valid:
                return factor
            # Unknown or malformed responses block this proposal. Retry only transport failures;
            # reservations remain charged at their maximum when usage cannot be known.
            if malformed:
                raise AdviceError('Malformed AI advice blocks this proposal')
            if attempt<c.retries:
                await asyncio.sleep(2**attempt)
        raise AdviceError('AI advice unavailable; frozen policy blocks proposal')
