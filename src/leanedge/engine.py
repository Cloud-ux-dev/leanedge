"""Strategy proposes; independent risk, safety and paper executor control records."""
from datetime import timedelta
import asyncio
import uuid
from .ai import AdviceError
from .config import Asset
from .core import D,ZERO,digest,instant,stamp,utcnow
from .costs import accrue_hosting,total_costs,spending_mode
from .data import SOL
from .execution import PaperExecutor
from .halt import apply_halt,is_halted,watch_halt
from .risk import equity,entry_reasons,latest_observation,positions,update_periods
from .safety import passed,record_checks
from .strategy import completed_bars,momentum_after_pullback,random_entry


class Engine:
    def __init__(self,store,config,safety,adviser=None):
        self.store,self.config,self.safety,self.adviser=store,config,safety,adviser
        self.executor=PaperExecutor(store,config)
        self.last_step=None
        self.assets={a.token:a for a in config.assets}
        if config.sol_pool:
            self.assets[SOL]=Asset(SOL,config.sol_pool,store.experiment()['time'],'native-sol-benchmark')

    def reject(self,key,account,token,reasons,now):
        with self.store.transaction('proposal_rejected',now):
            self.store.put('decision',key,{'id':key,'account':account,'token':token,'time':stamp(now),
                           'accepted':False,'reasons':sorted(set(reasons))},once=True)

    async def propose(self,key,account,asset,budget,features,now):
        s=self.store;c=self.config
        apply_halt(s,now)
        if s.get('signal',key):
            return
        with s.transaction('strategy_proposal',now):
            s.put('signal',key,{'id':key,'account':account,'token':asset.token,'time':stamp(now),
                      'budget':str(budget),'features':features},once=True)
            update_periods(s,account,c,now)
        observation=latest_observation(s,asset.token,now)
        reasons=entry_reasons(s,account,asset.token,budget,c,now)
        health=s.get('health',asset.token)
        if not observation or not health or not health['ok'] or (now-instant(observation['time'])).total_seconds()>c.data.fresh_seconds:
            reasons.append('no fresh reliable market observation')
        if reasons:
            self.reject(key,account,asset.token,reasons,now);return
        if c.ai.enabled and account=='strategy':
            try:
                factor=await self.adviser.advise(key,dict(features,budget_usd=str(budget),price_usd=observation['price'],
                    liquidity_usd=observation['liquidity'],equity_usd=str(equity(s,account,c,now)[0])),account,now)
            except AdviceError as exc:
                self.reject(key,account,asset.token,[str(exc)],now);return
            except AttributeError:
                self.reject(key,account,asset.token,['required AI adviser unavailable'],now);return
            budget*=factor
            if budget<=0:
                self.reject(key,account,asset.token,['AI veto'],now);return
        quantity=self.executor.estimate_quantity(budget,D(observation['price']))
        checks=await self.safety.checks(asset,quantity,observation,now)
        # The synthetic evidence provider is never allowed on a real experiment.
        record_checks(s,key,account,asset.token,checks,now)
        if not passed(checks,c,now):
            self.reject(key,account,asset.token,[f'{x.name}: {x.result.value}: {x.reason}' for x in checks if x.result.value!='PASS'],now)
            return
        self.executor.submit(key,account,asset,'buy',budget,quantity,now)

    async def step(self,now):
        s=self.store;c=self.config
        apply_halt(s,now)
        if self.last_step is not None and (now-self.last_step).total_seconds()<c.data.poll_seconds:
            await self.executor.process_due(self.assets,self.safety,now)
            return
        self.last_step=now
        accrue_hosting(s,c,now)
        with s.transaction('risk_monitor',now):
            for account in s.all('account'):
                update_periods(s,account['id'],c,now)
            for p in s.all('position'):
                if D(p['quantity'])<=0:
                    continue
                o=latest_observation(s,p['token'],now)
                if o and (now-instant(o['time'])).total_seconds()<=c.data.fresh_seconds:
                    p['last_price']=o['price'];p['high']=str(max(D(p['high']),D(o['price'])))
                    s.put('position',p['id'],p)
        # Exits continue under local, period, spending, and experiment-entry halts.
        for p in s.all('position'):
            if D(p['quantity'])<=0 or p['passive']:
                continue
            o=latest_observation(s,p['token'],now)
            if not o or (now-instant(o['time'])).total_seconds()>c.data.fresh_seconds:
                continue
            price=D(o['price']);reason=None
            if price<=D(p['entry_price'])*(1-c.risk.stop_loss):reason='initial stop trigger'
            elif price<=D(p['high'])*(1-c.risk.trailing_stop):reason='trailing stop trigger'
            elif (now-instant(p['opened'])).total_seconds()>=c.risk.holding_hours*3600:reason='maximum hold time'
            if reason:
                key=digest([p['id'],p['opened'],now.date().isoformat(),reason,len([o for o in s.all('order') if o['account']==p['account'] and o['token']==p['token'] and o['side']=='sell'])])
                self.executor.submit(key,p['account'],self.assets[p['token']],'sell',ZERO,D(p['quantity']),now,reason=reason)
        await self.executor.process_due(self.assets,self.safety,now)
        if not is_halted(s.path) and not s.experiment()['ended']:
            for asset in c.assets:
                if instant(asset.selected_at)>now:
                    continue
                bars=completed_bars(s,asset.token,c,now)
                if not bars:
                    continue
                features=momentum_after_pullback(bars,c.strategy)
                for a in s.all('account'):
                    account=a['id']
                    if account=='hold_sol':continue
                    fire=bool(features) if account=='strategy' else random_entry(int(account.split('_')[1]),asset.token,bars[-1]['end'],c.strategy.random_probability)
                    if fire:
                        eq,_=equity(s,account,c,now)
                        key=digest([s.experiment()['id'],account,asset.token,bars[-1]['end']])
                        await self.propose(key,account,asset,eq*c.risk.position_fraction,features if account=='strategy' else {},now)
            if c.sol_pool and not s.get('order','hold_sol:initial'):
                o=latest_observation(s,SOL,now);h=s.get('health',SOL)
                if o and h and h['ok'] and (now-instant(o['time'])).total_seconds()<=c.data.fresh_seconds:
                    budget=c.starting_balance*D('.98');q=self.executor.estimate_quantity(budget,D(o['price']))
                    self.executor.submit('hold_sol:initial','hold_sol',self.assets[SOL],'buy',budget,q,now,
                                         reason='predeclared passive SOL allocation',passive=True)
        await self.snapshot(now)

    async def snapshot(self,now):
        s=self.store;c=self.config
        with s.transaction('account_snapshots',now):
            for a in s.all('account'):
                eq,healthy=equity(s,a['id'],c,now)
                op=total_costs(s,a['id']);survival=eq-op
                prior=[x for x in s.all('snapshot') if x['account']==a['id']]
                peak=max([c.starting_balance,*[D(x['equity']) for x in prior]])
                drawdown=max(ZERO,(peak-eq)/peak)
                key=a['id']+':'+stamp(now)
                s.put('snapshot',key,{'id':key,'account':a['id'],'time':stamp(now),'cash':a['cash'],
                          'equity':str(eq),'healthy':healthy,'realized':a['realized'],'trading_costs':a['trading_costs'],
                          'operating_costs':str(op),'survival':str(survival),'drawdown':str(drawdown),
                          'mode':spending_mode(s,c,now)},once=True)
                if survival<=0 and not a['ended']:
                    a['ended']=True;s.put('account',a['id'],a)
                    if a['id']=='strategy':
                        exp=s.experiment();exp['ended']=True;exp['end_time']=stamp(now)
                        s.put('experiment','current',exp)
                        for o in s.all('order'):
                            if o['side']=='buy' and o['status']=='pending':
                                o['status']='cancelled';s.put('order',o['id'],o)


async def operate(engine,recorder,*,duration=None):
    """Polling can wait on HTTP without stopping the independent 250ms halt watcher."""
    stopped=asyncio.Event();start=asyncio.get_running_loop().time()
    async def polling():
        while not stopped.is_set():
            for asset in engine.assets.values():
                await recorder.poll(asset)
            try:await asyncio.wait_for(stopped.wait(),timeout=engine.config.data.poll_seconds)
            except asyncio.TimeoutError:pass
    async def monitor():
        while not stopped.is_set():
            await engine.step(utcnow())
            if duration is not None and asyncio.get_running_loop().time()-start>=duration:
                stopped.set();break
            try:await asyncio.wait_for(stopped.wait(),timeout=.5)
            except asyncio.TimeoutError:pass
    tasks=[asyncio.create_task(polling()),asyncio.create_task(monitor()),asyncio.create_task(watch_halt(engine.store,stopped))]
    try:
        done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
    finally:
        stopped.set()
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
