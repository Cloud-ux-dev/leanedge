"""An experiment's policy is immutable; changes belong to a new database and ID."""
from .config import from_snapshot
from .core import digest, stamp, utcnow, instant
from .storage import StorageError


def initialize(store, config, name, *, demo=False, now=None):
    now = now or utcnow()
    if store.get('experiment','current'):
        raise StorageError('Experiment history already exists; use a new database')
    if not name or len(name) > 80 or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in name):
        raise StorageError('Experiment ID must use letters, numbers, hyphens, or underscores')
    if not demo and any(instant(a.selected_at) > now for a in config.assets):
        raise StorageError('Asset selection dates must not be in the future')
    with store.transaction('experiment_initialized', now):
        store.put('experiment','current',{'id':name,'demo':demo,'time':stamp(now),
                  'config':config.snapshot(),'config_hash':config.hash,'ended':False,
                  'label':'SYNTHETIC DEMO DATA' if demo else 'REAL PUBLIC DATA — PAPER MONEY'})
        for name in ['strategy', *[f'random_{seed}' for seed in config.strategy.random_seeds], 'hold_sol']:
            store.put('account',name,{'id':name,'cash':str(config.starting_balance),'realized':'0',
                      'trading_costs':'0','gross':'0','periods':{},'ended':False,'last_equity':str(config.starting_balance)})
    return store.experiment()


def frozen_config(store, supplied=None):
    exp = store.experiment()
    if digest(exp['config']) != exp['config_hash']:
        raise StorageError('Frozen configuration hash mismatch')
    config = from_snapshot(exp['config'])
    if supplied is not None and config.hash != supplied.hash:
        raise StorageError('Policy changed; initialize a new experiment ID in a new database')
    return config
