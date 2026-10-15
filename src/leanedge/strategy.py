"""One fixed momentum-after-pullback rule and repeatable random proposals."""
import random
from .core import D,digest,instant


def completed_bars(store,token,config,now):
    width=config.strategy.bar_seconds;poll=config.data.poll_seconds
    allbars=sorted([b for b in store.all('bar') if b['token']==token and instant(b['end'])<=now],key=lambda b:b['time'])
    bars=allbars[-config.strategy.warmup_bars:]
    if len(bars)<config.strategy.warmup_bars:
        return []
    for b in bars:
        if b['samples']<max(1,int(width/poll*.8)) or b['max_gap']>config.data.fresh_seconds:
            return []
        if (instant(b['first'])-instant(b['time'])).total_seconds()>poll*2 or (instant(b['end'])-instant(b['last'])).total_seconds()>poll*2:
            return []
    if any(instant(bars[i]['time'])!=instant(bars[i-1]['end']) for i in range(1,len(bars))):
        return []
    if (now-instant(bars[-1]['end'])).total_seconds()>config.data.fresh_seconds:
        return []
    return bars


def momentum_after_pullback(bars,settings):
    if len(bars)<settings.warmup_bars:
        return None
    prices=[D(b['close']) for b in bars]
    pb=settings.pullback_bars;m=settings.momentum_bars
    before=prices[-pb-1]
    momentum=before/prices[-pb-1-m]-1
    peak=max(prices[-pb-1:-1])
    pullback=(peak-prices[-2])/peak
    if momentum>=settings.momentum_min and settings.pullback_min<=pullback<=settings.pullback_max and prices[-1]>prices[-2] and prices[-1]<=peak:
        return {'momentum':str(momentum),'pullback':str(pullback)}
    return None


def random_entry(seed,token,bar_end,probability):
    rng=random.Random(int(digest([seed,token,bar_end]),16))
    return D(str(rng.random()))<probability
