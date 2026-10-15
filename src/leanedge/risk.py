"""Independent limits. Strategy and AI have no path to changing these rules."""
from datetime import timedelta
from .core import D, ZERO, instant, stamp
from .halt import is_halted


def positions(store, account):
    return [p for p in store.all('position') if p['account']==account and D(p['quantity'])>0]


def latest_observation(store, token, now):
    eligible = [o for o in store.all('observation') if o.get('token')==token and
                o.get('normalized') and o.get('valid') and instant(o['time'])<=now]
    return max(eligible,key=lambda x:x['time'],default=None)


def equity(store, account, config, now):
    a = store.get('account',account)
    value = D(a['cash'])
    healthy = True
    for p in positions(store,account):
        o = latest_observation(store,p['token'],now)
        fresh = o is not None and 0 <= (now-instant(o['time'])).total_seconds() <= config.data.fresh_seconds
        price = D(o['price']) if fresh else D(p['last_price'])
        if not fresh:
            healthy = False
            price = min(price,D(p['entry_price']))
        # A known bad latest update must not be hidden by an older good observation.
        bad = [h for h in store.all('health') if h.get('token')==p['token'] and instant(h['time'])<=now]
        if bad and not max(bad,key=lambda h:h['time']).get('ok'):
            healthy = False
        value += D(p['quantity'])*price
    return value, healthy


def update_periods(store, account, config, now):
    a = store.get('account',account)
    eq, healthy = equity(store,account,config,now)
    periods = a['periods']
    for kind, key, loss in [('day',now.date().isoformat(),config.risk.daily_loss),
                            ('week',(now-timedelta(days=now.weekday())).date().isoformat(),config.risk.weekly_loss)]:
        old = periods.get(kind)
        if old is None or old['key']!=key and healthy:
            # Use the last observed equity at the boundary, not an intraday low after a gap.
            anchor = D(a['last_equity'])
            old = {'key':key,'anchor':str(anchor),'halted':False}
            periods[kind] = old
        if old and eq <= D(old['anchor'])*(1-loss):
            old['halted'] = True
    if healthy:
        a['last_equity']=str(eq)
    store.put('account',account,a)
    return eq, healthy


def entry_reasons(store, account, token, total_cost, config, now, *, excluding=None):
    reasons = []
    if is_halted(store.path):
        reasons.append('local halt')
    exp = store.experiment()
    a = store.get('account',account)
    eq, healthy = equity(store,account,config,now)
    if exp['ended'] or a['ended']:
        reasons.append('experiment ended')
    if not healthy:
        reasons.append('unreliable valuation')
    if any(p['halted'] for p in a['periods'].values()):
        reasons.append('period loss halt')
    pending = [o for o in store.all('order') if o['account']==account and o['status']=='pending' and
               o['side']=='buy' and o['id']!=excluding]
    openp = positions(store,account)
    if token in {p['token'] for p in openp} or token in {o['token'] for o in pending}:
        reasons.append('duplicate token exposure')
    if len(openp)+len(pending)>=config.risk.max_positions:
        reasons.append('open position limit')
    entries = [o for o in store.all('order') if o['account']==account and o['side']=='buy' and
               o['id']!=excluding and o['time'][:10]==now.date().isoformat()]
    # Count attempts too: failures/retries must not create unlimited daily entries.
    if len(entries)>=config.risk.entries_per_day:
        reasons.append('daily entry limit')
    if any(o['token']==token and (now-instant(o['time'])).total_seconds()<config.risk.cooldown_minutes*60
           for o in store.all('order') if o['account']==account and o['side']=='buy' and o['id']!=excluding):
        reasons.append('token cooldown')
    reserved = sum((D(o['budget']) for o in pending),ZERO)
    if total_cost<=0 or total_cost>D(a['cash'])-reserved:
        reasons.append('cash limit including costs')
    if total_cost>eq*config.risk.position_fraction:
        reasons.append('position limit including costs')
    return reasons
