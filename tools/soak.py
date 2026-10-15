"""Run for a real elapsed 12/24/48 hours; never count accelerated demo as a soak."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--hours',type=int,choices=[12,24,48],required=True)
    p.add_argument('--db',required=True)
    p.add_argument('--config',default='config/experiment.toml')
    p.add_argument('--risk',default='config/risk.toml')
    p.add_argument('--out',required=True)
    p.add_argument('--synthetic',action='store_true',help='Offline resilience soak, permanently synthetic evidence')
    args=p.parse_args()
    destination=Path(args.out)
    destination.mkdir(parents=True,exist_ok=False)
    started=datetime.now(timezone.utc).isoformat();clock=time.monotonic();target=args.hours*3600
    env=dict(os.environ);env['PYTHONUTF8']='1'
    if args.synthetic:
        # In-process synthetic loop paced by the REAL wall clock. Not a real-data test.
        result=synthetic(args.db,target,destination)
    else:
        cmd=[sys.executable,'-m','leanedge','--db',args.db,'--config',args.config,'--risk',args.risk,
             'run','--seconds',str(target)]
        with (destination/'run.log').open('x',encoding='utf-8') as log:
            try:result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=target+120).returncode
            except subprocess.TimeoutExpired:result=124
    elapsed=time.monotonic()-clock
    check=subprocess.run([sys.executable,'-m','leanedge','--db',args.db,'verify'],capture_output=True,text=True,env=env)
    status=subprocess.run([sys.executable,'-m','leanedge','--db',args.db,'status'],capture_output=True,text=True,env=env)
    (destination/'final-status.txt').write_text(status.stdout+status.stderr,encoding='utf-8')
    record={'started':started,'ended':datetime.now(timezone.utc).isoformat(),'requested_hours':args.hours,
            'elapsed_seconds':elapsed,'returncode':result,'ledger_returncode':check.returncode,
            'label':'SYNTHETIC DEMO DATA' if args.synthetic else 'REAL PUBLIC DATA — PAPER MONEY',
            'duration_and_ledger_passed':result==0 and elapsed>=target and check.returncode==0,
            'note':'Duration and ledger alone do not establish data quality, complete coverage, or profitability.'}
    (destination/'result.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record,indent=2))
    return 0 if record['duration_and_ledger_passed'] else 1


def synthetic(database,seconds,destination):
    import asyncio
    from datetime import timedelta
    from leanedge.core import D,utcnow
    from leanedge.data import Recorder
    from leanedge.demo import DemoSafety,demo_config,observations
    from leanedge.engine import Engine
    from leanedge.experiment import initialize,frozen_config
    from leanedge.halt import watch_halt
    from leanedge.storage import Store
    async def run():
        with Store(database,writable=True) as store:
            if store.get('experiment','current'):
                if not store.experiment()['demo']:raise ValueError('Cannot write synthetic data into a real-data experiment')
                config=frozen_config(store)
            else:
                config=demo_config(utcnow());initialize(store,config,'synthetic-soak',demo=True)
            engine=Engine(store,config,DemoSafety(store,config));rec=Recorder(store,config,None)
            stop=asyncio.Event();watcher=asyncio.create_task(watch_halt(store,stop));start=time.monotonic();i=0
            try:
                while time.monotonic()-start<seconds:
                    now=utcnow()
                    for asset in engine.assets.values():
                        rec.record(asset,observations(asset,D(1)+D(i%60)/1000,now,i),now)
                    await engine.step(now);i+=1
                    await asyncio.sleep(min(config.data.poll_seconds,max(0,seconds-(time.monotonic()-start))))
            finally:
                stop.set();await watcher
        return 0
    return asyncio.run(run())


if __name__=='__main__':raise SystemExit(main())
