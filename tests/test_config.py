from dataclasses import replace
from decimal import Decimal as D
import pytest
from leanedge.config import Config, load_config, validate
from leanedge.settings import SettingsError


def test_configuration_and_risk_are_separate(tmp_path):
    p = tmp_path / 'config.toml'
    r = tmp_path / 'risk.toml'
    p.write_text('starting_balance="1200"\n[data]\npoll_seconds=15\n')
    r.write_text('max_positions=2\n')
    c = load_config(p, r, env_file=tmp_path/'.env', environ={})
    assert c.starting_balance == D('1200') and c.risk.max_positions == 2
    assert c.hash == load_config(p, r, env_file=tmp_path/'.env', environ={}).hash
    p.write_text('[risk]\nmax_positions=4')
    with pytest.raises(SettingsError, match='human risk'):
        load_config(p, r)


@pytest.mark.parametrize('text', ['mode="live"', 'starting_balance="NaN"', 'starting_balance="-1"',
                                  '[data]\npoll_seconds=0', '[ai]\nfallback="accept"',
                                  '[fill]\nfailure_probability="1.1"', '[strategy]\nwarmup_bars=2'])
def test_bad_config_is_safe(tmp_path, text):
    p = tmp_path/'c.toml'
    p.write_text(text)
    with pytest.raises(SettingsError) as e:
        load_config(p, env_file=tmp_path/'.env', environ={})
    assert 'NaN' not in str(e.value) and '-1' not in str(e.value)


def test_snapshot_roundtrip():
    from leanedge.config import from_snapshot
    assert from_snapshot(Config().snapshot()) == Config()
