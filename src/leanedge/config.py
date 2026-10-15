"""Human settings in TOML; secrets only in environment or private .env."""
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
import os
import re
import tomllib
from .core import D, canonical, digest, instant, number
from .settings import SettingsError, _local_values, load_settings


@dataclass(frozen=True)
class Risk:
    position_fraction: D = D("0.02")
    max_positions: int = 3
    entries_per_day: int = 10
    daily_loss: D = D("0.05")
    weekly_loss: D = D("0.10")
    min_liquidity: D = D("50000")
    min_age_hours: D = D("6")
    max_top10: D = D("0.20")
    cooldown_minutes: int = 60
    stop_loss: D = D("0.05")
    trailing_stop: D = D("0.05")
    holding_hours: D = D("6")


@dataclass(frozen=True)
class Strategy:
    bar_seconds: int = 300
    warmup_bars: int = 24
    momentum_bars: int = 12
    momentum_min: D = D("0.03")
    pullback_bars: int = 3
    pullback_min: D = D("0.01")
    pullback_max: D = D("0.04")
    random_seeds: tuple = (17, 29, 43, 71, 101)
    random_probability: D = D("0.05")


@dataclass(frozen=True)
class FillModel:
    version: str = "pessimistic-v1"
    failure_probability: D = D("0.10")
    latency_multiplier: D = D("1.5")
    delay_seconds: D = D("2")
    fee_fraction: D = D("0.003")
    spread_fraction: D = D("0.002")
    slippage_fraction: D = D("0.003")
    adverse_fraction: D = D("0.002")
    failed_fee_usd: D = D("0.01")
    depth_fraction: D = D("0.02")
    max_impact: D = D("0.05")
    seed: int = 42


@dataclass(frozen=True)
class AI:
    enabled: bool = False
    fallback: str = "block"
    model: str = "deepseek-flash"
    daily_cap: D = D("0.50")
    input_per_million: D = D("0.30")
    cached_per_million: D = D("0.006")
    output_per_million: D = D("1.20")
    pricing_verified: str = "2026-10-15T00:00:00+00:00"
    pricing_max_days: int = 7
    max_output_tokens: int = 128
    input_token_bound: int = 4096
    retries: int = 1


@dataclass(frozen=True)
class Costs:
    monthly_budget: D = D("50")
    hosting_monthly: D = D("0")
    rpc_request_usd: D = D("0")
    monthly_estimate: D = D("50")
    other_request_usd: D = D("0")


@dataclass(frozen=True)
class Data:
    poll_seconds: int = 30
    fresh_seconds: int = 120
    safety_fresh_seconds: int = 120
    disagreement_fraction: D = D("0.05")
    timeout_seconds: int = 8
    retries: int = 2
    cache_seconds: int = 10
    max_holder_pages: int = 5


@dataclass(frozen=True)
class Evaluation:
    min_days: int = 30
    min_trades: int = 100
    max_drawdown: D = D("0.20")
    coverage_min: D = D("0.95")
    max_gap_seconds: int = 120
    block_days: int = 5
    bootstrap_runs: int = 2000
    seed: int = 977
    regime_trend: D = D("0.02")
    regime_down: D = D("0.02")
    regime_window_days: int = 7


@dataclass(frozen=True)
class Asset:
    token: str
    pool: str
    selected_at: str
    pool_type: str = "unverified"


@dataclass(frozen=True)
class Config:
    mode: str = "paper"
    starting_balance: D = D("1000")
    risk: Risk = field(default_factory=Risk)
    strategy: Strategy = field(default_factory=Strategy)
    fill: FillModel = field(default_factory=FillModel)
    ai: AI = field(default_factory=AI)
    costs: Costs = field(default_factory=Costs)
    data: Data = field(default_factory=Data)
    evaluation: Evaluation = field(default_factory=Evaluation)
    assets: tuple = ()
    sol_pool: str = ""

    def snapshot(self):
        import json
        return json.loads(canonical(asdict(self)))

    @property
    def hash(self):
        return digest(self.snapshot())


@dataclass(frozen=True)
class Secrets:
    helius: str = field(default="", repr=False)
    jupiter: str = field(default="", repr=False)
    deepseek: str = field(default="", repr=False)


def secrets(env_file=None, environ=None):
    local = _local_values(env_file)
    env = os.environ if environ is None else environ
    return Secrets(*(env.get(n, local.get(n, "")) or "" for n in
                     ("LEANEDGE_HELIUS_API_KEY", "LEANEDGE_JUPITER_API_KEY", "LEANEDGE_DEEPSEEK_API_KEY")))


def _section(cls, raw, prefix):
    if not isinstance(raw, dict) or set(raw) - {f.name for f in fields(cls)}:
        raise SettingsError(f"{prefix}: unknown or invalid setting")
    out = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        v, default = raw[f.name], f.default
        key = f"{prefix}.{f.name}"
        try:
            if isinstance(default, D):
                v = number(v, key)
            elif type(default) is int:
                if type(v) is not int or v < 0:
                    raise ValueError()
            elif type(default) is bool:
                if type(v) is not bool:
                    raise ValueError()
            elif isinstance(default, tuple):
                if not isinstance(v, list) or not v or any(type(n) is not int or n < 0 for n in v) or len(set(v)) != len(v):
                    raise ValueError()
                v = tuple(v)
            elif not isinstance(v, str):
                raise ValueError()
        except (ValueError, TypeError):
            raise SettingsError(f"{key}: invalid setting") from None
        out[f.name] = v
    return cls(**out)


def from_snapshot(raw):
    raw = dict(raw)
    types = {"risk": Risk, "strategy": Strategy, "fill": FillModel, "ai": AI,
             "costs": Costs, "data": Data, "evaluation": Evaluation}
    for k, cls in types.items():
        raw[k] = _section(cls, raw.get(k, {}), k)
    raw["starting_balance"] = number(raw.get("starting_balance", "1000"), "starting_balance", positive=True)
    raw["assets"] = tuple(Asset(**x) for x in raw.get("assets", []))
    c = Config(**raw)
    validate(c)
    return c


def validate(c):
    def require(ok, name):
        if not ok:
            raise SettingsError(f"{name}: invalid or inconsistent setting")
    require(c.mode == "paper", "mode (only paper is supported)")
    require(c.starting_balance > 0, "starting_balance")
    for section in (c.risk, c.strategy, c.fill, c.ai, c.costs, c.data, c.evaluation):
        for f in fields(section):
            v = getattr(section, f.name)
            require(not isinstance(v, D) or v.is_finite() and v >= 0, f.name)
    for k in ("position_fraction", "daily_loss", "weekly_loss", "max_top10", "stop_loss", "trailing_stop"):
        require(0 < getattr(c.risk, k) < 1, f"risk.{k}")
    require(c.risk.max_positions > 0 and c.risk.entries_per_day > 0 and c.risk.holding_hours > 0, "risk.limits")
    require(c.strategy.bar_seconds > 0 and c.strategy.pullback_bars > 1 and
            c.strategy.momentum_bars > c.strategy.pullback_bars and
            c.strategy.warmup_bars >= c.strategy.momentum_bars + c.strategy.pullback_bars + 2,
            "strategy.windows")
    require(0 <= c.strategy.pullback_min <= c.strategy.pullback_max < 1, "strategy.pullback_range")
    require(c.strategy.random_probability <= 1 and len(c.strategy.random_seeds) >= 3, "strategy.random_seeds/probability")
    require(c.fill.failure_probability <= 1 and c.fill.latency_multiplier >= 1 and c.fill.depth_fraction > 0,
            "fill.assumptions")
    require(all(getattr(c.fill, k) < 1 for k in ("fee_fraction", "spread_fraction", "slippage_fraction", "adverse_fraction", "max_impact")), "fill.fractions")
    require(c.fill.version == "pessimistic-v1", "fill.version")
    require(0 < c.data.poll_seconds <= c.data.fresh_seconds and c.data.timeout_seconds > 0 and
            c.data.safety_fresh_seconds > 0 and c.data.max_holder_pages > 0 and c.data.retries <= 5,
            "data.timing")
    require(c.data.cache_seconds <= c.data.fresh_seconds and c.data.disagreement_fraction < 1, "data.quality")
    require(c.costs.monthly_budget > 0 and c.costs.monthly_estimate > 0, "costs.monthly_budget/estimate")
    require(c.ai.fallback == "block" and c.ai.retries <= 2 and c.ai.max_output_tokens > 0 and
            c.ai.input_token_bound >= 1024 and c.ai.pricing_max_days > 0, "ai.policy")
    require(c.ai.cached_per_million<=c.ai.input_per_million, "ai.cached_per_million")
    require(c.ai.model in {"deepseek-flash", "deepseek-v4-pro"}, "ai.model")
    try:
        instant(c.ai.pricing_verified)
    except (ValueError, TypeError):
        raise SettingsError("ai.pricing_verified: UTC-aware timestamp required") from None
    if c.ai.enabled:
        require(c.ai.daily_cap > 0 and c.ai.input_per_million > 0 and c.ai.output_per_million > 0,
                "ai.pricing/cap")
    e = c.evaluation
    require(e.min_days >= 30 and e.min_trades >= 100 and 0 < e.max_drawdown <= D("0.2") and
            D("0.95") <= e.coverage_min <= 1 and e.bootstrap_runs >= 1000 and e.block_days > 0 and
            e.max_gap_seconds > 0 and e.regime_window_days >= 2, "evaluation.thresholds")
    require(len({a.token for a in c.assets}) == len(c.assets) and len({a.pool for a in c.assets}) == len(c.assets), "assets.duplicates")
    for a in c.assets:
        require(bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", a.token)) and
                bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", a.pool)), "assets.identifiers")
        try:
            instant(a.selected_at)
        except (ValueError, TypeError):
            raise SettingsError("assets.selected_at: UTC-aware timestamp required") from None
    require(isinstance(c.sol_pool,str) and (not c.sol_pool or bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", c.sol_pool))), "sol_pool")


def load_config(path=None, risk_path=None, *, env_file=None, environ=None):
    legacy = load_settings(env_file, environ)  # validates paper boundary before any IO or network
    def read(p):
        try:
            return tomllib.loads(Path(p).read_text(encoding="utf-8")) if p else {}
        except (OSError, ValueError):
            raise SettingsError("configuration file: cannot read valid TOML") from None
    raw = read(path)
    allowed = {f.name for f in fields(Config)} - {"risk"}
    if set(raw) - allowed:
        raise SettingsError("configuration: unknown setting; risk belongs in the human risk file")
    raw["risk"] = read(risk_path)
    # The old balance override remains supported, and its final value is frozen.
    env = os.environ if environ is None else environ
    local = _local_values(env_file)
    if "LEANEDGE_PAPER_STARTING_BALANCE" in env or "LEANEDGE_PAPER_STARTING_BALANCE" in local:
        raw["starting_balance"] = str(legacy.paper_starting_balance)
    try:
        return from_snapshot(raw)
    except (TypeError, KeyError, ValueError) as exc:
        if isinstance(exc, SettingsError):
            raise
        raise SettingsError("configuration: invalid fields or setting") from None
