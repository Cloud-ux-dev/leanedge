# LeanEdge

LeanEdge records public Solana market snapshots and runs a **paper-only** trading
experiment. It uses pretend money, fixed rules, SQLite records, and pessimistic
fills. It compares one momentum strategy with five seeded random accounts and a
passive SOL account. Optional AI can reject or shrink a proposal. It cannot place
real orders, connect a wallet, sign a transaction, or unlock real trading.

**Current limitation:** real-data entries remain blocked because no verified
pool-specific liquidity-protection decoder is included. Public recording, local
controls, the paper engine, reports, evaluation, and the offline demo work locally.
Provider credentials and live access have not been verified here. See
[BLOCKERS.md](BLOCKERS.md). A public quote or liquidity number is not safety proof.

Paper gains cannot pay real bills. No profitability, 30-day result, completed
12/24/48-hour soak, or live-trading readiness is claimed.

## Windows PowerShell setup

Files were built in `/workspace/leanedge` on a cloud Linux machine, **not** on your
laptop. Copy/extract the source into your intended folder while preserving your
private `.env`, unrelated files, and `.git` folder. The initial build did not
commit or push; publication happens only on an explicit user request.
The existing origin remains `https://github.com/Cloud-ux-dev/leanedge.git`.

Use Python 3.11 or newer. These commands do not activate a script and do not change
PowerShell execution policy:

```powershell
Set-Location 'C:\Users\Anan\Desktop\leanedge'
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m leanedge validate
.\.venv\Scripts\python.exe -m leanedge doctor
.\.venv\Scripts\python.exe -m pytest -q
```

On Linux/macOS, use `python3 -m venv .venv` and `.venv/bin/python` instead. The
installable `leanedge` command and `python -m leanedge` share the same CLI.

## Offline demo

No network or credentials are needed. Use a **new** database and output directory:

```powershell
.\.venv\Scripts\python.exe -m leanedge demo --db work\demo-01.db --out reports\demo-01
.\.venv\Scripts\python.exe -m leanedge status --db work\demo-01.db
.\.venv\Scripts\python.exe -m leanedge dashboard --db work\demo-01.db
```

Open `http://127.0.0.1:8765`. Ctrl+C stops the dashboard. It is read-only and binds
only to loopback. You can also open `reports\demo-01\dashboard.html` directly.
Every demo output/report/dashboard says **SYNTHETIC DEMO DATA**. Demo bars use a
separate 60-second setup to show the system quickly. Demo results never count as
live-data evidence. Reusing an existing experiment database is refused.

## Real-data paper operation

First choose tokens and pools, record the selection time, and verify the configured
mint is the **base** token of each pool on both market providers. In
`config\experiment.toml`, set `sol_pool` and add dated `[[assets]]` entries using
the commented example. Choose before observations, not from future results.
The default empty allowlist makes no speculative asset choices for you.

`config\risk.toml` contains human-controlled limits. Ordinary settings are in
`config\experiment.toml`. No bot component writes either file. Risk rules and exact
strategy/fill definitions are in [docs/RULES.md](docs/RULES.md).

Optional secrets go in environment variables or an untracked `.env`. To create
one without overwriting an existing private file:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

- Public market snapshots need no key.
- `LEANEDGE_HELIUS_API_KEY` enables read-only authority/holder checks.
- `LEANEDGE_JUPITER_API_KEY` enables read-only sell-back quotes.
- `LEANEDGE_DEEPSEEK_API_KEY` is optional; AI also requires `ai.enabled = true`,
  current verified model pricing, and budget settings **before** initialization.

Actual environment variables override `.env`. `.env` interpolation is disabled.
Never add wallet keys or tokens to TOML, source files, screenshots, or reports.
Do not turn on HTTP debug logging: third-party HTTP logs may include request URLs.
Official links, verification dates, endpoint details, and limitations are in
[docs/API_NOTES.md](docs/API_NOTES.md).

After configuration, initialize and run:

```powershell
.\.venv\Scripts\python.exe -m leanedge validate
.\.venv\Scripts\python.exe -m leanedge init --id paper-001 --db data\paper-001.db
.\.venv\Scripts\python.exe -m leanedge doctor --online --db data\paper-001.db
.\.venv\Scripts\python.exe -m leanedge run --db data\paper-001.db
```

This starts the complete local runtime against public data **in paper mode**.
It does not establish that live integrations are healthy. With the current
liquidity-verification blocker, active entries are rejected and reasons are
recorded; it is useful as a recorder but cannot complete an eligible trading
trial. Missing credentials, bad data, stale evidence, and unknown safety also
block entries. No live response is replaced with a fake one.

To stop after a bounded period, add `--seconds 60` to `run`. Ctrl+C preserves
history. A restart recovers pending orders with their original modeled due time.
A running database has one decision writer; a second writer is refused. Dashboard,
status, verification, and backups use read-only consistent views.

A run compares current config with the frozen experiment. Changes require a new
ID and new database. Read-only commands normally read the saved policy; passing
`--config` or `--risk` also checks it. Any non-paper mode is rejected before normal
startup. A gate pass cannot change that boundary.

## Halt, resume, reports and recovery

Use another PowerShell window for a persistent local halt:

```powershell
.\.venv\Scripts\python.exe -m leanedge halt --db data\paper-001.db
.\.venv\Scripts\python.exe -m leanedge status --db data\paper-001.db
```

The independent 250ms watcher and fill-boundary checks block new entries, cancel
pending buys, record the halt, and continue data/health work. Controlled paper
exits remain allowed. A halt does not liquidate positions or stop the recorder.
The isolated measured response was **0.231 seconds** on this cloud host; this is
not a Windows timing guarantee. Run your own measurement:

```powershell
.\.venv\Scripts\python.exe tools\measure_halt.py
```

To resume, stop the runner with Ctrl+C, ensure its final market updates are fresh,
and run the explicit human command below before restarting. If freshness fails,
run the recorder while halted to refresh data, stop it, then retry. Daily/weekly
loss halts remain in force until their healthy period rollover.

```powershell
.\.venv\Scripts\python.exe -m leanedge resume --db data\paper-001.db
.\.venv\Scripts\python.exe -m leanedge run --db data\paper-001.db
```

Reports and backups refuse to overwrite existing files:

```powershell
.\.venv\Scripts\python.exe -m leanedge report --db data\paper-001.db --out reports\day-01
.\.venv\Scripts\python.exe -m leanedge gaps --db data\paper-001.db
.\.venv\Scripts\python.exe -m leanedge verify --db data\paper-001.db
.\.venv\Scripts\python.exe -m leanedge checkpoint --db data\paper-001.db --out backups\checkpoint-01.json
.\.venv\Scripts\python.exe -m leanedge verify --db data\paper-001.db --checkpoint backups\checkpoint-01.json
.\.venv\Scripts\python.exe -m leanedge backup --db data\paper-001.db --out backups\paper-001-copy.db
.\.venv\Scripts\python.exe -m leanedge evaluate --db data\paper-001.db --out reports\review-01
```

Reports include current account tables, a dated daily snapshot summary, CSV/JSON,
data gaps and an escaped HTML dashboard with gate progress. Daily summaries retain
partial/missing days; the evidence gate uses only complete valid UTC days and never
silently discards invalid whole days. Evaluation returns exit code 1 when evidence
is incomplete or fails, and writes `PROMOTION_DENIED.md`; exit code 2 means an
operation/configuration error. Success means human review only.

Keep checkpoint copies somewhere independent. The hash chain detects edits
relative to a trusted checkpoint; someone with full access can rebuild a chain.
Use SQLite online backups, not a copy of only the main database while it is running.
Restore a backup to a new path, set `halt` on that path, inspect ledger/config and
health, then explicitly resume. Do not delete or reset experiment history.

To record a real invoice, use a unique nonsecret bill reference. Do not count the
same expense again if already included in per-request or hosting estimates:

```powershell
.\.venv\Scripts\python.exe -m leanedge cost --db data\paper-001.db --usd 2.50 --category hosting --reference invoice-001
```

This records a human-reported expense, not a payment. AI request costs use verified
prices and token counts or a conservative reservation when uncertain. Other API
costs and hosting are explicitly estimates until reconciled with actual bills.
Zero request estimates assume an existing free allowance. Monthly budget USD 50
is a spending limit, not an instruction to buy anything. See RULES.md for shared
cost allocation and NORMAL/FRUGAL/HALTED spending behavior.

## Evaluation and long runs

[docs/EVALUATION.md](docs/EVALUATION.md) defines all thresholds, aligned comparisons,
regimes, dependence-aware bootstrap, and the replication **report-only** condition.
No long test has been completed during this build. Do not count accelerated demo
time as real elapsed observation or soak time.

After `init`, run any of these with distinct output directories:

```powershell
.\.venv\Scripts\python.exe tools\soak.py --hours 12 --db data\paper-001.db --out soak-results\12h
.\.venv\Scripts\python.exe tools\soak.py --hours 24 --db data\paper-001.db --out soak-results\24h
.\.venv\Scripts\python.exe tools\soak.py --hours 48 --db data\paper-001.db --out soak-results\48h
```

For a paced offline resilience run, add `--synthetic` and use a separate demo
path such as `work\soak-demo.db`. This is still synthetic, even after 48 hours.
The tooling records elapsed wall time, process result, final status and ledger
check. Duration/ledger success alone does not prove market-data quality.

## Checks and secret scanning

The actual check results and remaining work are in [PROGRESS.md](PROGRESS.md).
Tests use fake transports and labeled fixtures; they contact no real recipient.
Run `python -m pytest -q` in the environment described above.

A practical Gitleaks configuration is included. With Gitleaks installed, scan the
working directory **and** repository history with redacted output:

```powershell
gitleaks dir . --redact --config .gitleaks.toml
gitleaks git . --redact --config .gitleaks.toml
```

Gitleaks was not available and was **not run** during this build. `.gitignore`
excludes `.env` variants, databases, reports, logs, caches, keys and virtualenvs;
`.env.example` remains trackable. An ignore rule does not remove a previously
committed secret. If you discover one, revoke it and review history separately.

## Architecture and existing Telegram interface

`config` and `settings` validate startup. `storage` and `experiment` own persistence,
freezing and audit. `data` records public snapshots and sampled bars. `safety` and
`risk` independently reject unsafe proposals. `strategy` proposes entries;
`execution.PaperExecutor` alone changes paper financial state. `engine` runs these
async tasks. `ai` is bounded optional advice. `costs`, `reporting`, `evaluation`,
and `cli` provide costs, dashboard, evidence reports and commands. Runtime depends
only on `httpx` and `python-dotenv`; pytest is a development dependency. SQLite,
TOML, web serving and audit hashing use Python's standard library.

The repository's existing `--telegram` private owner-only status interface is
preserved and tested with fake transport. It describes the starting balance,
not the current experiment account. It is separate from the local dashboard and
is not used for halt, AI, automatic summaries or risk monitoring. Start it only
explicitly with privately configured credentials. It was not contacted here.

See [LIVE_CHECKLIST.md](LIVE_CHECKLIST.md) for what a separate future live system
would require. This repository contains no real executor or approval unlock.

The real-socket bounded-stop check timed out during executor shutdown in this
restricted cloud host. A network-enabled diagnostic recheck was blocked by the
automatic review service's usage limit. Mocked blocked-transport cancellation
passed; confirm bounded stop and Ctrl+C locally before an unattended run.
