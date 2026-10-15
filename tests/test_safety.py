from datetime import timedelta
from leanedge.config import Config,Asset
from leanedge.core import stamp,utcnow,D
from leanedge.data import SPL
from leanedge.safety import evaluate_evidence,passed,Result


def good_evidence(now):
    return {'_demo':True,'mint':{'token':'a','time':stamp(now),'mintAuthority':None,'freezeAuthority':None,'owner':SPL,'extensions':[]},
        'holders':{'token':'a','time':stamp(now),'complete':True,'top10':'.1'},
        'liquidity_protection':{'pool':'p','pool_type':'fixture','time':stamp(now),'verified':True,'verifier':'synthetic-fixture-only'},
        'quote':{'token':'a','quantity':'10','out_usd':'9','route':True,'time':stamp(now)}}


def test_dangerous_missing_and_stale():
    now=utcnow();c=Config();a=Asset('a','p',stamp(now),'fixture')
    o={'valid':True,'time':stamp(now),'liquidity':'100000','pool_created':stamp(now-timedelta(days=2))}
    evidence=good_evidence(now)
    assert passed(evaluate_evidence(a,o,evidence,D(10),c,now),c,now)
    for key in ['mint','holders','liquidity_protection','quote']:
        bad=dict(evidence);bad.pop(key)
        assert not passed(evaluate_evidence(a,o,bad,D(10),c,now),c,now)
    bad=good_evidence(now);bad['mint']['freezeAuthority']='active'
    checks=evaluate_evidence(a,o,bad,D(10),c,now)
    assert next(x for x in checks if x.name=='freeze_authority').result==Result.FAIL
    evidence.pop('_demo')
    assert not passed(evaluate_evidence(a,o,evidence,D(10),c,now),c,now)
    assert not passed(evaluate_evidence(a,o,good_evidence(now),D(11),c,now),c,now)
    assert not passed(evaluate_evidence(a,o,good_evidence(now),D(10),c,now+timedelta(minutes=5)),c,now+timedelta(minutes=5))
