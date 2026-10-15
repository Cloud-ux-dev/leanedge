"""Read-only views and exports. External strings are always HTML-escaped."""
from collections import Counter
import csv
from datetime import timedelta
import html
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import sqlite3
from pathlib import Path
from .core import D,ZERO,canonical,instant,stamp,utcnow
from .costs import spending_mode,total_costs
from .data import data_gaps
from .experiment import frozen_config
from .halt import is_halted
from .risk import equity,positions
from .storage import Store,StorageError


def status(store,config,now=None):
    now=now or utcnow();exp=store.experiment();accounts=[]
    snapshots=store.all('snapshot');fills=store.all('fill')
    for a in store.all('account'):
        eq,healthy=equity(store,a['id'],config,now)
        net=eq-config.starting_balance
        operating=total_costs(store,a['id'])
        closed=[f for f in fills if f['account']==a['id'] and f.get('closed') and not f['failed']]
        wins=len([f for f in closed if D(f.get('closed_net') or f['net'])>0])
        history=[x for x in snapshots if x['account']==a['id']]
        peak=max([config.starting_balance,*[D(x['equity']) for x in history]])
        drawdown=max([ZERO,(peak-eq)/peak,*[D(x['drawdown']) for x in history]])
        accounts.append({'account':a['id'],'cash':a['cash'],'equity':str(eq),'healthy':healthy,
            'gross_paper_result':str(net+D(a['trading_costs'])),'trading_costs':a['trading_costs'],
            'net_paper_result':str(net),'realized':a['realized'],'unrealized':str(net-D(a['realized'])),
            'operating_costs':str(operating),'after_operating_costs':str(net-operating),
            'survival_balance':str(eq-operating),'max_drawdown':str(drawdown),
            'closed_trades':len(closed),'win_rate':str(D(wins)/len(closed) if closed else ZERO),
            'ended':a['ended'],'open_positions':positions(store,a['id'])})
    reasons=Counter(r for d in store.all('decision') if not d['accepted'] for r in d['reasons'])
    health=store.all('health')
    return {'experiment':exp['id'],'label':exp['label'],'paper_only':True,'time':stamp(now),
            'ai_policy':'AI-advised; required advice or block' if config.ai.enabled else 'deterministic',
            'mode':spending_mode(store,config,now),'halted':is_halted(store.path),'ended':exp['ended'],
            'data_health':health,'last_success':max((h['time'] for h in health if h.get('ok')),default=None),
            'accounts':accounts,'rejections':dict(reasons),'ledger_verified':store.verify()[0],
            'notice':'Paper gains cannot pay real bills. Survival balance is hypothetical.'}


def exclusive_write(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='') as f:f.write(text)
    return path


def export_report(store,config,directory,now=None):
    now=now or utcnow();directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    view=status(store,config,now)
    exclusive_write(directory/'summary.json',json.dumps(view,indent=2))
    exclusive_write(directory/'data-gaps.json',json.dumps(data_gaps(store,config,now),indent=2))
    with (directory/'accounts.csv').open('x',encoding='utf-8',newline='') as f:
        rows=[{k:v for k,v in row.items() if k!='open_positions'} for row in view['accounts']]
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    # Daily exports preserve missing and partial days rather than silently filling them.
    daily={}
    for snap in sorted(store.all('snapshot'),key=lambda r:r['time']):
        daily[(snap['time'][:10],snap['account'])]=snap
    exclusive_write(directory/'daily.json',json.dumps(list(daily.values()),indent=2))
    from .evaluation import evaluate
    exclusive_write(directory/'dashboard.html',dashboard_html(view,store.all('snapshot'),evaluate(store,config,now)))
    return view


def table(headers,rows):
    e=lambda v:html.escape(str(v),quote=True)
    return '<table><thead><tr>'+''.join('<th>'+e(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+e(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>'


def dashboard_html(view,snapshots,gate=None):
    e=lambda v:html.escape(str(v),quote=True)
    series={}
    for a in view['accounts']:
        series[a['account']]=sorted([s for s in snapshots if s['account']==a['account']],key=lambda x:x['time'])[-1000:]
    values=[float(s['equity']) for samples in series.values() for s in samples]
    chart=''
    if values:
        lo=min(values);span=max(1,max(values)-lo)
        palette=['#46c9ab','#eab36a','#8aaaff','#e98aa0','#b593ef','#93b596','#b5bac7']
        for i,(name,samples) in enumerate(series.items()):
            points=' '.join(f'{20+j*860/max(1,len(samples)-1):.1f},{200-(float(s["equity"])-lo)*170/span:.1f}' for j,s in enumerate(samples))
            chart+=f'<polyline fill="none" stroke="{palette[i%len(palette)]}" stroke-width="2" points="{points}"><title>{e(name)}</title></polyline>'
        chart='<svg role="img" aria-label="Paper equity by sample; gaps are listed separately" viewBox="0 0 900 230">'+chart+'</svg><p>Paper equity by sample, last 1,000 samples per account. Dates and gaps remain in exports.</p>'
    rows=[]
    for a in view['accounts']:
        rows.append([a[k] for k in ['account','cash','equity','realized','unrealized','trading_costs','operating_costs','after_operating_costs','max_drawdown','closed_trades','win_rate','survival_balance']])
    body=f'<h1>LeanEdge</h1><p class="label">PAPER MODE — pretend money only.</p><h2>{e(view["label"])}</h2>'
    body+=f'<p>Experiment: {e(view["experiment"])} · {e(view["ai_policy"])} · Spending: {e(view["mode"])} · Halt flag: {e(view["halted"])} · Ended: {e(view["ended"])}</p>'
    body+=f'<p>Updated UTC: {e(view["time"])} · Last good data: {e(view["last_success"])} · Ledger: {e(view["ledger_verified"])}</p>'
    body+=chart+table(['Account','Cash USD','Equity USD','Realized USD','Unrealized USD','Trading costs USD','Operating costs USD','After operating USD','Max drawdown fraction','Closed trades','Win fraction','Hypothetical survival USD'],rows)
    body+='<h2>Data health</h2>'+table(['Token','Observation UTC','Healthy','Findings'],[[h.get('token'),h['time'],h['ok'],'; '.join(h['findings'])] for h in view['data_health']])
    body+='<h2>Open paper positions</h2>'+table(['Account','Token','Quantity','Entry price','Last price'],[[a['account'],p['token'],p['quantity'],p['entry_price'],p['last_price']] for a in view['accounts'] for p in a['open_positions']])
    body+='<h2>Entry rejections</h2>'+table(['Reason','Count'],view['rejections'].items())
    body+='<h2>Human-review gate</h2>'
    if gate:
        body+=table(['Requirement','Measured','Pass','Reason'],[[g['name'],g['measured'],g['passed'],g['reason']] for g in gate['checks']])
    else:
        body+='<p>Not evaluated in this view. Run evaluate for thresholds and unmet requirements. Demo evidence always fails.</p>'
    body+='<p>'+e(view['notice'])+'</p><p>Read-only dashboard. All trades and balances are simulated.</p>'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>LeanEdge — paper only</title><style>body{font:15px system-ui;background:#101820;color:#e1e8ec;margin:28px}h1{color:#46c9ab}h2{margin-top:30px}.label{font-weight:bold;color:#eab36a}table{border-collapse:collapse;display:block;overflow:auto;margin:16px 0;width:100%}th,td{border-bottom:1px solid #32424e;padding:10px;text-align:left;max-width:440px;overflow-wrap:anywhere}th{color:#8edbc8}svg{width:100%;max-height:260px;background:#17242c}p{line-height:1.6}</style>'+body+'</html>'


def serve(database,port=8765):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {'/','/status.json'}:
                self.send_error(404);return
            try:
                with Store(database) as store:
                    config=frozen_config(store)
                    view=status(store,config)
                    if self.path=='/status.json':
                        body=json.dumps(view).encode();kind='application/json'
                    else:
                        from .evaluation import evaluate
                        gate=evaluate(store,config)
                        body=dashboard_html(view,store.all('snapshot'),gate).encode();kind='text/html; charset=utf-8'
                self.send_response(200)
                self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
                self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
                self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
            except (OSError,ValueError,StorageError,sqlite3.Error):
                self.send_error(503,'Experiment view unavailable')
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'Read-only paper dashboard: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
