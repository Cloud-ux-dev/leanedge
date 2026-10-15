"""Windows-friendly commands. No command implements real order submission."""
import argparse
import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import sqlite3
import sys
import uuid
from .config import load_config,secrets
from .core import stamp,utcnow,instant
from .data import MarketSources,ReadOnlyHTTP,Recorder,SOL,data_gaps
from .experiment import initialize,frozen_config
from .halt import request_halt,resume
from .reporting import export_report,exclusive_write,serve,status
from .settings import SettingsError,load_settings,load_telegram_settings
from .storage import Store,StorageError


def arguments():
    parser=argparse.ArgumentParser(description='LeanEdge — paper-only experiments')
    def common(p,sub=False):
        opts={'default':argparse.SUPPRESS} if sub else {}
        p.add_argument('--db',**opts,help='Experiment SQLite path')
        p.add_argument('--config',**opts,help='Ordinary settings TOML')
        p.add_argument('--risk',**opts,help='Human-controlled risk TOML')
    common(parser)
    parser.add_argument('--telegram',action='store_true',help='Legacy private status-only Telegram interface')
    subs=parser.add_subparsers(dest='command')
    for cmd in ['validate','doctor','init','run','demo','status','halt','resume','report','dashboard','gaps','verify','checkpoint','evaluate','backup','cost']:
        p=subs.add_parser(cmd);common(p,True)
        if cmd=='init':p.add_argument('--id',required=True)
        if cmd=='doctor':p.add_argument('--online',action='store_true')
        if cmd=='run':p.add_argument('--seconds',type=float,help='Optional bounded run duration')
        if cmd in {'report','evaluate','demo'}:p.add_argument('--out',help='New report directory')
        if cmd=='demo':p.add_argument('--steps',type=int,default=432)
        if cmd=='dashboard':p.add_argument('--port',type=int,default=8765)
        if cmd=='verify':p.add_argument('--checkpoint',help='Previously exported trusted JSON checkpoint')
        if cmd=='checkpoint':p.add_argument('--out',required=True)
        if cmd=='backup':p.add_argument('--out',required=True)
        if cmd=='cost':
            p.add_argument('--usd',required=True);p.add_argument('--category',choices=['rpc','other_api','hosting'],required=True)
            p.add_argument('--reference',required=True,help='Unique bill reference without secrets')
    return parser.parse_args()


async def _run(store,config,sec):
    from .ai import Adviser
    from .engine import Engine,operate
    from .safety import LiveSafety
    keys=secrets();http=ReadOnlyHTTP(store,config)
    adviser=Adviser(store,config,keys.deepseek)
    sources=MarketSources(http);recorder=Recorder(store,config,sources)
    try:
        await operate(Engine(store,config,LiveSafety(http,keys,config,store),adviser),recorder,duration=sec)
    finally:
        await http.close();await adviser.close()


async def _online_doctor(store,config):
    sources_http=ReadOnlyHTTP(store,config)
    try:
        rec=Recorder(store,config,MarketSources(sources_http))
        assets=list(config.assets)
        if config.sol_pool:
            from .config import Asset
            assets.append(Asset(SOL,config.sol_pool,stamp()))
        if not assets:
            return {'market':'UNAVAILABLE: configure selected token/pool allowlist and SOL pool'}
        result={}
        for asset in assets:
            r=await rec.poll(asset)
            result[asset.token]='PASS: public snapshots recorded' if r else 'UNAVAILABLE: recorded provider or quality failure'
        return result
    finally:
        await sources_http.close()


def main():
    args=arguments()
    try:
        settings=load_settings()  # paper-only boundary before any normal startup or integration
        telegram=load_telegram_settings() if args.telegram else None
        config_path=args.config or ('config/experiment.toml' if Path('config/experiment.toml').is_file() else None)
        risk_path=args.risk or ('config/risk.toml' if Path('config/risk.toml').is_file() else None)
        config=load_config(config_path,risk_path)
        if args.command=='run' and args.seconds is not None:
            from math import isfinite
            if not isfinite(args.seconds) or args.seconds<=0:raise SettingsError('seconds: must be positive and finite')
        if args.command=='demo' and not 1<=args.steps<=10000:raise SettingsError('steps: must be between 1 and 10000')
        if args.command=='dashboard' and not 1<=args.port<=65535:raise SettingsError('port: invalid port')
    except (SettingsError,ValueError) as exc:
        print(f'Settings error: {exc}',file=sys.stderr);return 2
    print('PAPER MODE — pretend money only.',flush=True)
    if not args.command:
        print('LeanEdge')
        print(f'Pretend starting balance: {settings.paper_starting_balance:.2f} USD')
        print('No trades happen yet. Use init and run, or demo.')
        if telegram:
            from .telegram import run_bot,TelegramError
            try:run_bot(settings,telegram)
            except KeyboardInterrupt:print('Telegram stopped.')
            except TelegramError as exc:print(f'Telegram error: {exc}',file=sys.stderr);return 1
        return 0
    command=args.command
    db=Path(args.db or ('work/demo-'+uuid.uuid4().hex[:8]+'.db' if command=='demo' else 'data/leanedge.db'))
    try:
        if command=='validate':
            print('Configuration valid. Frozen hash: '+config.hash);return 0
        if command=='halt':
            record=request_halt(db);print('Persistent halt set at '+record['time']);return 0
        if command=='doctor':
            keys=secrets()
            result={'python':sys.version.split()[0],'httpx':version('httpx'),'sqlite':sqlite3.sqlite_version,
                    'mode':'paper only','Helius credential':bool(keys.helius),'Jupiter credential':bool(keys.jupiter),
                    'DeepSeek credential':bool(keys.deepseek),'liquidity_protection':'UNAVAILABLE: no verified live pool decoder',
                    'AI':'enabled with block fallback' if config.ai.enabled else 'disabled',
                    'network':'not tested; use --online after init'}
            if args.online:
                with Store(db,writable=True) as s:
                    frozen=frozen_config(s)
                    if s.experiment()['demo']:raise StorageError('Online checks cannot write into a demo database')
                    result['network']=asyncio.run(_online_doctor(s,frozen))
            print(json.dumps(result,indent=2));return 0
        if command=='init':
            with Store(db,writable=True) as s:
                exp=initialize(s,config,args.id)
                print(f"Initialized {exp['id']} at {db}")
                if not config.assets:print('Recorder awaits a dated token/pool allowlist. Entries remain blocked.')
            return 0
        if command=='demo':
            from .demo import run_demo
            from .evaluation import write_evaluation
            with Store(db,writable=True) as s:
                c,now=asyncio.run(run_demo(s,args.steps))
                out=Path(args.out or 'reports/demo-'+uuid.uuid4().hex[:8])
                view=export_report(s,c,out,now)
                gate=write_evaluation(s,c,out,now)
                print(json.dumps({'label':'SYNTHETIC DEMO DATA','database':str(db),'reports':str(out),
                    'closed_strategy_trades':next(a['closed_trades'] for a in view['accounts'] if a['account']=='strategy'),
                    'ledger':s.verify(),'review_ready':gate['ready']},indent=2))
            return 0
        if command=='dashboard':
            serve(db,args.port);return 0
        writable=command in {'run','resume','evaluate','cost'}
        with Store(db,writable=writable) as s:
            c=frozen_config(s,config if command=='run' or args.config or args.risk else None)
            if command=='run':
                if s.experiment()['demo']:raise StorageError('Use demo for synthetic data; run accepts real-data experiments only')
                if not c.assets or not c.sol_pool:raise StorageError('Configure a dated token/pool allowlist and a SOL benchmark pool, then initialize a new experiment')
                if not s.verify()[0]:raise StorageError('Ledger verification failed')
                print('Recording real public data; all balances and fills remain paper money.',flush=True)
                asyncio.run(_run(s,c,args.seconds))
            elif command=='status':print(json.dumps(status(s,c),indent=2))
            elif command=='resume':
                now=utcnow();required={a.token for a in c.assets}|({SOL} if c.sol_pool else set())
                from .risk import latest_observation
                health=s.all('health');good=bool(required) and all(any(h.get('token')==t and h['ok'] and
                    0<=(now-instant(h['time'])).total_seconds()<=c.data.fresh_seconds for h in health) for t in required)
                good=good and all((o:=latest_observation(s,t,now)) is not None and
                    0<=(now-instant(o['time'])).total_seconds()<=c.data.fresh_seconds for t in required)
                resume(s,good);print('Human resume recorded. Period loss halts remain in force.')
            elif command=='gaps':print(json.dumps(data_gaps(s,c,utcnow()),indent=2))
            elif command=='verify':
                cp=json.loads(Path(args.checkpoint).read_text()) if args.checkpoint else None
                ok,reason=s.verify(cp);print(reason);return 0 if ok else 1
            elif command=='checkpoint':
                exclusive_write(args.out,json.dumps(s.checkpoint(),indent=2));print('Checkpoint exported. Keep an independent copy.')
            elif command=='backup':print('Backup created: '+str(s.backup(args.out)))
            elif command=='report':
                out=Path(args.out or 'reports/daily-'+uuid.uuid4().hex[:8]);export_report(s,c,out);print('Reports: '+str(out))
            elif command=='evaluate':
                from .evaluation import write_evaluation
                out=Path(args.out or 'reports/evaluation-'+uuid.uuid4().hex[:8]);r=write_evaluation(s,c,out)
                print('REVIEW_READY.md' if r['ready'] else 'PROMOTION_DENIED.md');print('Reports: '+str(out))
                return 0 if r['ready'] else 1
            elif command=='cost':
                from .core import number,digest
                amount=number(args.usd,'usd',positive=True)
                key='bill:'+digest(args.reference)
                with s.transaction('human_cost_recorded'):
                    if s.get('cost',key):raise StorageError('Bill reference already recorded')
                    s.put('cost',key,{'id':key,'time':stamp(),'amount':str(amount),'category':args.category,
                        'account':None,'status':'human-reported actual bill','details':{}})
                print('Operating bill recorded; do not also enter the same expense as an estimate.')
        return 0
    except KeyboardInterrupt:
        print('Stopped. Experiment history is preserved.');return 0
    except (StorageError,sqlite3.Error,OSError,ValueError) as exc:
        # Only known application errors may be shown; library errors may contain paths or values.
        text=str(exc) if isinstance(exc,StorageError) else 'Operation failed; check file access, arguments, or database health.'
        print('LeanEdge error: '+text,file=sys.stderr);return 2
