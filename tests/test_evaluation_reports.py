from datetime import timedelta
from leanedge.config import Config
from leanedge.core import D,utcnow
from leanedge.evaluation import evaluate,write_evaluation,block_interval
from leanedge.experiment import initialize
from leanedge.reporting import status,dashboard_html,export_report
from leanedge.storage import Store


def test_incomplete_gate_demo_never_qualifies_and_escape(tmp_path):
    c=Config();now=utcnow()
    with Store(tmp_path/'e.db',writable=True) as s:
        initialize(s,c,'test',demo=True,now=now)
        r=write_evaluation(s,c,tmp_path/'report',now)
        assert not r['ready'] and (tmp_path/'report'/'PROMOTION_DENIED.md').exists()
        assert not (tmp_path/'report'/'REPLICATE_PROPOSAL.md').exists()
        assert all('threshold' in x and 'measured' in x and 'reason' in x for x in r['checks'])
        v=status(s,c,now);v['experiment']='<script>alert(1)</script>'
        out=dashboard_html(v,[],r)
        assert '<script>' not in out and '&lt;script&gt;' in out
        assert 'SYNTHETIC DEMO DATA' in out
        export_report(s,c,tmp_path/'export',now)
        assert (tmp_path/'export'/'accounts.csv').exists()
        assert s.verify()[0]


def test_time_dependent_bootstrap_repeatable():
    positive=[D('.001')]*30
    interval=block_interval(positive,5,1000,97,D(1000))
    assert D(interval[0])>0
    assert interval==block_interval(positive,5,1000,97,D(1000))
    assert block_interval(positive[:5],5,1000,97,D(1000)) is None
