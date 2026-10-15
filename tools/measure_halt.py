"""Measure the independent local watcher with an isolated synthetic database."""
import asyncio
from pathlib import Path
import tempfile
import time
from leanedge.config import Config
from leanedge.experiment import initialize
from leanedge.halt import request_halt,watch_halt
from leanedge.storage import Store


async def run():
    with tempfile.TemporaryDirectory() as folder:
        with Store(Path(folder)/'measurement.db',writable=True) as s:
            initialize(s,Config(),'halt-measurement',demo=True)
            stop=asyncio.Event();task=asyncio.create_task(watch_halt(s,stop))
            await asyncio.sleep(.02);start=time.monotonic();request_halt(s.path)
            while not s.all('halt'):
                await asyncio.sleep(.005)
            elapsed=time.monotonic()-start;stop.set();await task
            print(f'SYNTHETIC DEMO DATA — local halt observed in {elapsed:.3f} seconds; target < 2 seconds')
            return 0 if elapsed<2 else 1

if __name__=='__main__':raise SystemExit(asyncio.run(run()))
