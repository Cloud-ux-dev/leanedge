"""A conservative human-review gate. It never enables real trading."""
from datetime import datetime,timedelta,timezone
import random
from statistics import mean
from .core import D,ZERO,digest,instant,stamp,utcnow
from .data import SOL
from .reporting import status,exclusive_write


def block_interval(differences,block_days,runs,seed,capital):
    """Circular moving-block bootstrap of aligned daily excess P&L / initial capital."""
    if len(differences)<max(10,block_days*2):
        return None
    rng=random.Random(seed);n=len(differences);totals=[]
    for _ in range(runs):
        drawn=[]
        while len(drawn)<n:
            start=rng.randrange(n)
            drawn.extend(differences[(start+j)%n] for j in range(block_days))
        totals.append(sum(drawn[:n],ZERO)*capital)
    totals.sort()
    return str(totals[int(runs*.025)]),str(totals[min(runs-1,int(runs*.975))])


def daily_evidence(store,config,now):
    exp=store.experiment();start=instant(exp['time'])
    day=datetime.combine(start.date()+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
    snapshots=store.all('snapshot')
    names=[a['id'] for a in store.all('account')]
    tokens=list(dict.fromkeys([a.token for a in config.assets]+([SOL] if config.sol_pool else [])))
    observations=store.all('observation')
    bytoken={t:sorted(set(instant(o['time']) for o in observations if o.get('normalized') and o.get('valid') and o['token']==t)) for t in tokens}
    byaccount={a:sorted([s for s in snapshots if s['account']==a],key=lambda s:s['time']) for a in names}
    days=[]
    while day+timedelta(days=1)<=now:
        end=day+timedelta(days=1);coverage=[];gaps=[];valid=bool(tokens) and bool(config.sol_pool) and bool(config.assets)
        for token in tokens:
            times=[t for t in bytoken[token] if day-timedelta(seconds=config.data.poll_seconds)<=t<end]
            cursor=day;covered=0.;max_gap=0.
            for t in times:
                stop=min(end,t+timedelta(seconds=config.data.poll_seconds))
                max_gap=max(max_gap,max(0,(t-cursor).total_seconds()))
                covered+=max(0,(stop-max(cursor,day,t)).total_seconds())
                cursor=max(cursor,stop)
            max_gap=max(max_gap,max(0,(end-cursor).total_seconds()))
            cov=D(str(covered))/D(86400);coverage.append(cov);gaps.append(max_gap)
            valid=valid and cov>=config.evaluation.coverage_min and max_gap<=config.evaluation.max_gap_seconds
        returns={};closings={}
        for a in names:
            opening=max((s for s in byaccount[a] if instant(s['time'])<=day),key=lambda s:s['time'],default=None)
            closing=max((s for s in byaccount[a] if instant(s['time'])<end),key=lambda s:s['time'],default=None)
            inside=[s for s in byaccount[a] if day<=instant(s['time'])<end]
            good=opening and closing and inside and all(s['healthy'] for s in inside) and opening['healthy'] and closing['healthy'] and (
                day-instant(opening['time'])).total_seconds()<=config.data.poll_seconds*2 and (
                end-instant(closing['time'])).total_seconds()<=config.data.poll_seconds*2
            # Snapshot cadence itself must be monitored, not merely its endpoints.
            if inside:
                ts=[instant(opening['time'])] if opening else [day]
                ts += [instant(s['time']) for s in inside]+[end]
                good=good and max((b-a).total_seconds() for a,b in zip(ts,ts[1:]))<=config.evaluation.max_gap_seconds
            valid=bool(valid and good)
            if good:
                begin=D(opening['equity'])-D(opening['operating_costs'])
                final=D(closing['equity'])-D(closing['operating_costs'])
                returns[a]=(final-begin)/config.starting_balance
                closings[a]=D(closing['equity'])
        # Regimes fixed in configuration: native SOL trailing 7-day return, ±2%.
        regime=None
        sol=[o for o in observations if o.get('normalized') and o.get('valid') and o['token']==SOL]
        latest=max((o for o in sol if instant(o['time'])<end),key=lambda o:o['time'],default=None)
        cutoff=end-timedelta(days=config.evaluation.regime_window_days)
        old=max((o for o in sol if instant(o['time'])<=cutoff),key=lambda o:o['time'],default=None)
        if latest and old and (cutoff-instant(old['time'])).total_seconds()<=config.data.poll_seconds*2:
            change=D(latest['price'])/D(old['price'])-1
            regime='UP' if change>=config.evaluation.regime_trend else 'DOWN' if change<=-config.evaluation.regime_down else 'RANGE'
        days.append({'date':day.date().isoformat(),'valid':valid,'coverage':str(min(coverage,default=ZERO)),
                     'max_gap_seconds':max(gaps,default=86400),'returns':{k:str(v) for k,v in returns.items()},
                     'regime':regime})
        day=end
    return days


def evaluate(store,config,now=None):
    now=now or utcnow();exp=store.experiment();e=config.evaluation
    view=status(store,config,now);strategy=next(a for a in view['accounts'] if a['account']=='strategy')
    days=daily_evidence(store,config,now);valid=[d for d in days if d['valid']]
    checks=[]
    def add(name,threshold,value,passed,reason):
        checks.append({'name':name,'threshold':threshold,'measured':value,'passed':bool(passed),'reason':reason})
    add('real-data provenance','synthetic data never qualifies',exp['label'],not exp['demo'],'demo databases are permanently excluded')
    add('valid full UTC days',e.min_days,len(valid),len(valid)>=e.min_days,'partial days and invalid periods do not count')
    add('closed strategy trades',e.min_trades,strategy['closed_trades'],strategy['closed_trades']>=e.min_trades,'partial exits alone do not count as closed trades')
    net=D(strategy['after_operating_costs'])
    add('result after all costs','> 0 USD',str(net),net>0,'trading costs and attributed operating costs included once')
    dd=D(strategy['max_drawdown'])
    add('maximum drawdown',f'< {e.max_drawdown}',str(dd),dd<e.max_drawdown,'marked equity; stale valuation also fails data health')
    random_names=[a['account'] for a in view['accounts'] if a['account'].startswith('random_')]
    # Never silently discard bad days: any invalid whole day makes comparison incomplete.
    aligned=bool(days) and len(days)==len(valid) and len(random_names)>=3
    differences=[]
    for d in valid:
        if 'strategy' in d['returns'] and all(n in d['returns'] for n in random_names):
            differences.append(D(d['returns']['strategy'])-sum((D(d['returns'][n]) for n in random_names),ZERO)/len(random_names))
    interval=block_interval(differences,e.block_days,e.bootstrap_runs,e.seed,config.starting_balance) if aligned else None
    random_mean=sum((D(a['after_operating_costs']) for a in view['accounts'] if a['account'] in random_names),ZERO)/len(random_names) if random_names else ZERO
    add('beat seeded random ensemble','95% block bootstrap lower bound > 0 USD',interval or 'insufficient aligned evidence',
        interval is not None and D(interval[0])>0 and net>random_mean,'all complete UTC days aligned, multiple seeds; invalid days make this incomplete')
    winners=sorted([D(f.get('closed_net') or f['net']) for f in store.all('fill') if f['account']=='strategy' and f.get('closed') and not f['failed'] and D(f.get('closed_net') or f.get('net','0'))>0],reverse=True)
    trimmed=net-sum(winners[:3],ZERO)
    add('remove three largest winners','> 0 USD with at least 3 winners',str(trimmed),len(winners)>=3 and trimmed>0,'subtract closed-trade net gains, retain every cost')
    regimes=sorted({d['regime'] for d in valid if d['regime']})
    add('predefined market regimes','at least 2 of UP/DOWN/RANGE',regimes,len(regimes)>=2,'SOL trailing-window rules frozen before observations')
    benchmark_ok=any(f['account']=='hold_sol' and f['side']=='buy' and not f['failed'] and D(f['quantity'])>0 for f in store.all('fill'))
    add('SOL benchmark available','successful passive benchmark fill',benchmark_ok,benchmark_ok,'a missing or failed benchmark remains incomplete')
    good_data=aligned and len(valid)>=e.min_days and all(a['healthy'] for a in view['accounts'])
    add('data quality and downtime',f'coverage >= {e.coverage_min}; gaps <= {e.max_gap_seconds}s; no invalid whole days',
        {'whole_days':len(days),'invalid_days':len(days)-len(valid),'current_healthy':all(a['healthy'] for a in view['accounts'])},good_data,'gaps, unavailable assets, and downtime remain visible')
    ledger,reason=store.verify()
    add('audit ledger','verified',ledger,ledger,reason)
    frozen=digest(exp['config'])==exp['config_hash']==config.hash
    add('unchanged frozen configuration','hash match',frozen,frozen,'material changes need a separate experiment ID')
    result={'experiment':exp['id'],'label':exp['label'],'time':stamp(now),'ready':all(c['passed'] for c in checks),
            'checks':checks,'days':days,'bootstrap':{'method':'circular moving blocks','block_days':e.block_days,'runs':e.bootstrap_runs,'seed':e.seed},
            'notice':'Human review only. This gate cannot enable real trading.'}
    return result


def write_evaluation(store,config,directory,now=None):
    result=evaluate(store,config,now)
    from pathlib import Path
    import json
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    exclusive_write(directory/'evaluation.json',json.dumps(result,indent=2))
    name='REVIEW_READY.md' if result['ready'] else 'PROMOTION_DENIED.md'
    lines=['# '+('Ready for human review' if result['ready'] else 'Promotion denied'),'',result['label'],'',result['notice'],'',
           '| Requirement | Threshold | Measured | Pass | Reason |','|---|---|---|---|---|']
    def clean(value):return str(value).replace('|','/').replace('\n',' ')
    for c in result['checks']:
        lines.append('| '+' | '.join(clean(c[k]) for k in ['name','threshold','measured','passed','reason'])+' |')
    lines+=['','Bootstrap limitations: the small sample, chosen block length, dependence across tokens, and regime definitions can affect confidence. A passing paper comparison is not proof of future returns.']
    exclusive_write(directory/name,'\n'.join(lines)+'\n')
    # Replication is a written proposal only, never a subprocess or a new account.
    if result['ready']:
        view=status(store,config,now)
        a=next(a for a in view['accounts'] if a['account']=='strategy')
        good=result['days'][-7:]
        consecutive=len(good)==7 and all(d['valid'] for d in good) and all(
            (datetime.fromisoformat(b['date'])-datetime.fromisoformat(a['date'])).days==1 for a,b in zip(good,good[1:]))
        snaps=[s for s in store.all('snapshot') if s['account']=='strategy' and good and s['time'][:10]>=good[0]['date']]
        if D(a['net_paper_result'])>3*config.costs.monthly_estimate and consecutive and snaps and all(D(s['drawdown'])<D('.20') for s in snaps):
            exclusive_write(directory/'REPLICATE_PROPOSAL.md',
                '# Separate paper experiment proposal\n\n'+f"Lifetime net paper profit: USD {a['net_paper_result']}. Monthly operating estimate: USD {config.costs.monthly_estimate}. Seven consecutive valid days remain below 20% drawdown.\n\nPropose a new independently identified paper experiment with the same frozen policy, a separately attributed monthly budget, and new out-of-sample dates. No process, resource, account, or funds were created. Paper success does not prove another bot will succeed.\n")
    if store.writable:
        with store.transaction('evaluation_report',now):
            key='evaluation:'+result['time']
            store.put('report',key,{'id':key,'time':result['time'],'ready':result['ready'],'report_hash':digest(result)})
    return result
