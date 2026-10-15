"""Only PaperExecutor exists. Delayed orders do not own a fill price in advance."""
from abc import ABC,abstractmethod
from datetime import timedelta
from decimal import ROUND_DOWN
import random
from .core import D,ZERO,digest,instant,stamp
from .halt import apply_halt,is_halted
from .risk import entry_reasons,positions,update_periods
from .safety import passed,record_checks


class Executor(ABC):
    @abstractmethod
    def submit(self,*args,**kwargs):
        pass


class PaperExecutor(Executor):
    def __init__(self,store,config):
        if config.mode!='paper':
            raise ValueError('Only paper mode is supported')
        self.store,self.config=store,config

    def random(self,key,kind):
        return random.Random(int(digest([self.config.fill.seed,key,kind]),16))

    def estimate_quantity(self,budget,price):
        f=self.config.fill
        worst=f.spread_fraction/2+f.slippage_fraction+f.adverse_fraction+f.max_impact
        return (budget/(price*(1+worst)*(1+f.fee_fraction))).quantize(D('.000001'),rounding=ROUND_DOWN)

    def submit(self,key,account,asset,side,budget,quantity,now,*,reason='proposal',passive=False):
        s=self.store;c=self.config
        if s.get('order',key):
            return False
        if side not in {'buy','sell'} or budget<0 or quantity<=0:
            return False
        apply_halt(s,now)
        with s.transaction('paper_order_submitted',now):
            update_periods(s,account,c,now)
            if side=='buy':
                if passive:
                    reasons=['local halt'] if is_halted(s.path) else []
                    a=s.get('account',account)
                    if a['ended'] or s.experiment()['ended'] or D(a['cash'])<budget or positions(s,account):
                        reasons.append('passive allocation unavailable')
                else:
                    reasons=entry_reasons(s,account,asset.token,budget,c,now)
                if reasons:
                    s.put('decision',key,{'id':key,'account':account,'token':asset.token,'time':stamp(now),'accepted':False,'reasons':reasons},once=True)
                    return False
            elif not any(p['token']==asset.token for p in positions(s,account)):
                return False
            if side=='sell' and any(o['account']==account and o['token']==asset.token and o['side']=='sell' and o['status']=='pending' for o in s.all('order')):
                return False
            delay=c.fill.delay_seconds*c.fill.latency_multiplier*D(str(self.random(key,'latency').uniform(.75,1.25)))
            s.put('order',key,{'id':key,'account':account,'token':asset.token,'pool':asset.pool,'side':side,
                      'time':stamp(now),'due':stamp(now+timedelta(seconds=float(delay))),
                      'budget':str(budget),'quantity':str(quantity),'status':'pending','reason':reason,'passive':passive},once=True)
            s.put('decision',key,{'id':key,'account':account,'token':asset.token,'time':stamp(now),'accepted':True,'reasons':[reason]},once=True)
        return True

    def frictions(self,order,observation):
        f=self.config.fill
        mid=D(observation['price']);q=D(order['quantity']);liquidity=D(observation['liquidity'])
        notional=q*mid
        impact=min(f.max_impact,notional/(liquidity*f.depth_fraction))
        penalty=f.spread_fraction/2+f.slippage_fraction+f.adverse_fraction+impact
        # Flat fallback spread when bid/ask is unavailable; explicit observed spread can be used.
        if observation.get('spread_fraction') is not None:
            penalty+=max(D(0),D(observation['spread_fraction'])-f.spread_fraction)/2
        return mid*(1+penalty if order['side']=='buy' else 1-penalty),impact

    async def process_due(self,assets,safety,now):
        s=self.store;c=self.config
        apply_halt(s,now)
        pending=[o for o in s.all('order') if o['status']=='pending' and instant(o['due'])<=now]
        for order in pending:
            # Execution can happen only after due, using the latest acquired snapshot
            # available now. Polling/processing adds delay; no backdated price is chosen.
            samples=[o for o in s.all('observation') if o.get('normalized') and o.get('valid') and
                     o['token']==order['token'] and instant(order['due'])<=instant(o['time'])<=now]
            observation=max(samples,key=lambda o:o['time'],default=None)
            if not observation:
                continue
            if (now-instant(observation['time'])).total_seconds()>c.data.fresh_seconds:
                with s.transaction('stale_fill_cancelled',now):
                    order['status']='rejected';order['failure']='fill data stale'
                    s.put('order',order['id'],order)
                continue
            asset=assets[order['token']]
            health=s.get('health',asset.token)
            if order['side']=='sell' and (not health or not health['ok']):
                continue  # keep exposure visible until a controlled exit can be valued
            checks=[]
            if order['side']=='buy' and not order['passive']:
                checks=await safety.checks(asset,D(order['quantity']),observation,now)
                record_checks(s,order['id']+':fill',order['account'],asset.token,checks,now)
            apply_halt(s,now)
            if s.get('order',order['id'])['status']!='pending':
                continue
            with s.transaction('paper_fill_attempt',now):
                # Safety and local flag are checked again immediately before financial writes.
                update_periods(s,order['account'],c,now)
                reasons=[]
                if order['side']=='buy':
                    health=s.get('health',asset.token)
                    if not health or not health['ok'] or (now-instant(health['time'])).total_seconds()>c.data.fresh_seconds:
                        reasons.append('current market health unavailable')
                    if is_halted(s.path):reasons.append('local halt')
                    if not order['passive']:
                        reasons+=entry_reasons(s,order['account'],asset.token,D(order['budget']),c,now,excluding=order['id'])
                        if not passed(checks,c,now):reasons.append('entry safety not all PASS')
                a=s.get('account',order['account']);q=D(order['quantity'])
                price,impact=self.frictions(order,observation)
                # Participation constraint: reject a buy that exceeds the depth cap. A sell can
                # partially fill; remaining exposure stays open and gets monitored again.
                capacity=D(observation['liquidity'])*c.fill.depth_fraction*c.fill.max_impact/D(observation['price'])
                if q>capacity and capacity>0:
                    if order['side']=='buy':reasons.append('pool depth limit')
                    else:q=capacity.quantize(D('.000001'),rounding=ROUND_DOWN)
                notional=q*price;fee=notional*c.fill.fee_fraction
                if q<=0:reasons.append('quantity below simulation precision')
                if order['side']=='buy' and (notional+fee>D(order['budget']) or notional+fee>D(a['cash'])):
                    reasons.append('fill exceeds inclusive cash or position budget')
                poskey=order['account']+':'+asset.token
                p=s.get('position',poskey)
                if order['side']=='sell' and (not p or D(p['quantity'])<q):
                    reasons.append('exit quantity unavailable')
                if reasons:
                    order.update(status='rejected',failure='; '.join(sorted(set(reasons))))
                    s.put('order',order['id'],order)
                    continue
                failure=D(str(self.random(order['id'],'failure').random()))<c.fill.failure_probability
                if failure:
                    failed_fee=min(D(a['cash']),c.fill.failed_fee_usd)
                    a['cash']=str(D(a['cash'])-failed_fee)
                    a['trading_costs']=str(D(a['trading_costs'])+failed_fee)
                    a['realized']=str(D(a['realized'])-failed_fee)
                    if order['side']=='sell' and p:
                        p['trade_net']=str(D(p.get('trade_net','0'))-failed_fee)
                        s.put('position',p['id'],p)
                    order.update(status='failed',failure='modeled transaction failure')
                    s.put('account',a['id'],a);s.put('order',order['id'],order)
                    s.put('fill',order['id'],{'id':order['id'],'account':a['id'],'token':asset.token,'time':stamp(now),
                              'side':order['side'],'quantity':'0','fee':str(failed_fee),'friction':str(failed_fee),'failed':True,'closed':False},once=True)
                    update_periods(s,a['id'],c,now)
                    continue
                mid=D(observation['price']);adverse=abs(price-mid)*q;friction=adverse+fee
                if order['side']=='buy':
                    a['cash']=str(D(a['cash'])-notional-fee)
                    p={'id':poskey,'account':a['id'],'token':asset.token,'pool':asset.pool,'quantity':str(q),
                       'entry_price':str(price),'entry_mid':str(mid),'entry_cost':str(notional+fee),
                       'entry_friction':str(friction),'entry_quantity':str(q),'opened':stamp(now),'trade_net':'0',
                       'high':str(mid),'last_price':str(mid),'passive':order['passive']}
                    a['realized']=str(D(a['realized'])-friction)
                    gross=ZERO;net=-friction;closed=False
                else:
                    fraction=q/D(p['quantity']);basis=D(p['entry_cost'])*fraction
                    entry_friction=D(p['entry_friction'])*fraction
                    a['cash']=str(D(a['cash'])+notional-fee)
                    gross=(mid-D(p['entry_mid']))*q
                    net=notional-fee-basis
                    a['gross']=str(D(a['gross'])+gross)
                    # Entry costs were already charged to realized on entry.
                    a['realized']=str(D(a['realized'])+gross-friction)
                    p['quantity']=str(D(p['quantity'])-q);p['entry_cost']=str(D(p['entry_cost'])-basis)
                    p['entry_friction']=str(D(p['entry_friction'])-entry_friction)
                    p['trade_net']=str(D(p.get('trade_net','0'))+net)
                    closed=D(p['quantity'])==0
                a['trading_costs']=str(D(a['trading_costs'])+friction)
                order['status']='filled' if q==D(order['quantity']) else 'partial'
                s.put('account',a['id'],a);s.put('position',poskey,p);s.put('order',order['id'],order)
                s.put('fill',order['id'],{'id':order['id'],'account':a['id'],'token':asset.token,'time':stamp(now),
                    'execution_data_time':observation['time'],'side':order['side'],'quantity':str(q),'price':str(price),
                    'mid':str(mid),'fee':str(fee),'friction':str(friction),'impact':str(impact),
                    'gross':str(gross),'net':str(net),'closed_net':p.get('trade_net','0') if closed else None,'failed':False,'closed':closed,
                    'model':c.fill.version,'observation':observation['id']},once=True)
                update_periods(s,a['id'],c,now)
