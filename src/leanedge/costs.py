"""Real expenses or explicit estimates, separate from paper trading frictions."""
from .core import D, ZERO, digest, stamp


def total_costs(store, account=None, month=None):
    records=store.all('cost')
    names=[a['id'] for a in store.all('account')]
    total=ZERO
    for c in records:
        if month and c['time'][:7]!=month:
            continue
        amount=D(c['amount'])
        if account:
            if c.get('account'):
                if c['account']==account:
                    total+=amount
            else:
                total+=amount / D(len(names))
        else:
            total+=amount
    return total


def spending_mode(store,config,now):
    spent=total_costs(store,month=now.strftime('%Y-%m'))
    if spent>=config.costs.monthly_budget:
        return 'HALTED'
    strategy=store.get('account','strategy')
    if spent>=config.costs.monthly_budget*D('.6') or spent>0 and strategy and D(strategy['realized'])<=total_costs(store,'strategy'):
        return 'FRUGAL'
    return 'NORMAL'


def reserve(store,key,amount,category,now,config,*,account=None,details=None):
    if store.get('cost',key):
        return False
    if amount<0 or total_costs(store,month=now.strftime('%Y-%m'))+amount>config.costs.monthly_budget:
        return False
    with store.transaction('operating_cost_reserved',now):
        return store.put('cost',key,{'id':key,'time':stamp(now),'amount':str(amount),
                         'category':category,'account':account,'status':'reserved estimate',
                         'details':details or {}},once=True)


def settle(store,key,amount,now,details=None):
    row=store.get('cost',key)
    if not row:
        raise ValueError('Cost reservation missing')
    if amount>D(row['amount']):
        # Keep the actual bill even if a provider exceeds a declared bound. Block further work.
        row['overrun']=True
    row.update(amount=str(amount),status='usage-derived estimate',details=details or {})
    with store.transaction('operating_cost_settled',now):
        store.put('cost',key,row)


def accrue_hosting(store,config,now):
    """Accrue elapsed time, including downtime, without silently omitting days."""
    exp=store.experiment()
    from .core import instant
    previous=store.get('usage','hosting',{'until':exp['time']})
    seconds=D(str(max(0,(now-instant(previous['until'])).total_seconds())))
    amount=config.costs.hosting_monthly*seconds/D(30*86400)
    if seconds<=0:
        return
    with store.transaction('hosting_accrued',now):
        if amount:
            key='hosting:'+stamp(now)
            store.put('cost',key,{'id':key,'time':stamp(now),'amount':str(amount),
                      'category':'hosting','account':None,'status':'elapsed-time estimate','details':{'month_days':30}})
        store.put('usage','hosting',{'until':stamp(now)})
