"""SYNTHETIC DEMO DATA. Offline only; permanently ineligible for evaluation."""
from dataclasses import replace
from datetime import timedelta
from .config import Config,Asset
from .core import D,stamp,utcnow
from .data import Recorder,SPL,SOL
from .engine import Engine
from .experiment import initialize
from .safety import evaluate_evidence

DEMO_TOKEN='D'*32
DEMO_POOL='P'*32
DEMO_SOL_POOL='Q'*32


class DemoSafety:
    def __init__(self,store,config):
        if not store.experiment()['demo']:
            raise ValueError('Synthetic safety is forbidden in real-data experiments')
        self.config=config

    async def checks(self,asset,quantity,observation,now):
        evidence={'_demo':True,'mint':{'token':asset.token,'time':stamp(now),'owner':SPL,'mintAuthority':None,'freezeAuthority':None,'extensions':[]},
          'holders':{'token':asset.token,'time':stamp(now),'complete':True,'top10':'.10'},
          'liquidity_protection':{'pool':asset.pool,'pool_type':asset.pool_type,'time':stamp(now),'verified':True,'verifier':'synthetic-fixture-only'},
          'quote':{'token':asset.token,'quantity':str(quantity),'out_usd':str(quantity*D(observation['price'])*D('.98')),'route':True,'time':stamp(now)}}
        return evaluate_evidence(asset,observation,evidence,quantity,self.config,now)


def demo_config(start):
    c=Config()
    return replace(c,assets=(Asset(DEMO_TOKEN,DEMO_POOL,stamp(start),'synthetic-fixture'),),sol_pool=DEMO_SOL_POOL,
                   strategy=replace(c.strategy,bar_seconds=60),data=replace(c.data,poll_seconds=10))


def observations(asset,price,now,index):
    return [{'provider':p,'token':asset.token,'pool':asset.pool,'time':stamp(now),'source_time':None,
             'price':str(price),'liquidity':'5000000','volume24h':'1000000',
             'pool_created':stamp(now-timedelta(days=30)),'reference':'SYNTHETIC:'+p+':'+asset.token+':'+str(index)}
            for p in ['dexscreener','geckoterminal']]


async def run_demo(store,steps=432):
    start=utcnow()-timedelta(seconds=steps*10)
    c=demo_config(start)
    initialize(store,c,'synthetic-demo',demo=True,now=start)
    recorder=Recorder(store,c,None);engine=Engine(store,c,DemoSafety(store,c))
    for i in range(steps+1):
        now=start+timedelta(seconds=i*10);bar=(i//6)%36
        if bar<=21:price=D(1)+D(bar)*D('.005')
        else:price={22:D('1.10'),23:D('1.08'),24:D('1.09'),25:D('1.10'),26:D('1.05'),27:D('.99')}.get(bar,D('1.00')+D(bar-28)*D('.002'))
        for asset in engine.assets.values():
            p=price if asset.token==DEMO_TOKEN else D(150)*(1+D(i)/D(10000))
            recorder.record(asset,observations(asset,p,now,i),now)
        await engine.step(now)
        if i and i%120==0:
            print(f'SYNTHETIC DEMO DATA: {i}/{steps} samples',flush=True)
    return c,now
