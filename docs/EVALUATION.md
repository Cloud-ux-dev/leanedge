# Evidence gate

The gate can produce a human-review report only. No result enables real trading.
Synthetic databases are permanently excluded. It requires all of:

- At least 30 **full, valid UTC days**, excluding the partial start/end days.
- At least 100 fully closed strategy positions. Partial sells alone do not count.
- Positive marked strategy result after all trading and attributed operating costs.
- Maximum marked-equity drawdown strictly below 20%.
- Better total result than the mean of five seeded random accounts, and a 95%
  circular moving-block bootstrap confidence interval for the total aligned daily
  excess result whose lower bound is strictly above zero.
- Positive result after subtracting the three biggest closed-position net wins,
  and at least three winning closed positions.
- At least two predefined regimes on valid days.
- Acceptable market coverage, current valuation health and snapshot cadence.
- Verified audit records and the same frozen configuration hash.

Regimes are fixed before the run: the trailing 7-day SOL price return is UP at
+2% or above, DOWN at -2% or below, otherwise RANGE. Do not fit these thresholds
to observed results. Days before the required lookback have no regime label.

A full day requires every selected asset and SOL to have at least 95% coverage,
no missing-data gap longer than 120 seconds, and valid marked account snapshots
on matching times. Each acquired sample supports at most one polling interval;
a large future gap cannot retroactively create valid observation time. Missing
assets and downtime remain in the report. Any invalid complete day makes the
baseline comparison incomplete rather than being silently dropped. First and
last partial days are explicitly not successful days. This gate is intentionally
strict: continue the same frozen run or begin a separately identified experiment;
do not delete inconvenient history.

Daily changes use equity minus attributed operating costs, normalized to the
same initial capital for all accounts. Random comparisons use the daily ensemble
mean. Default bootstrap: 2,000 circular resamples, block length 5 days, seed 977.
A block preserves short-range daily dependence. The method does not eliminate
selection bias, long dependence, model error, changing market conditions, or
uncertainty from few trades. Baseline differences and failed benchmark fills
must be inspected by the human reviewer. A passing statistic is not proof of a
profitable real execution strategy.

`PROMOTION_DENIED.md` lists every threshold and measured value when incomplete or
failed. `REVIEW_READY.md` means human review only. If it also has lifetime net
paper profit above three times the configured monthly cost estimate (default
USD 50), plus seven consecutive valid days below 20% drawdown, a separate
`REPLICATE_PROPOSAL.md` describes another paper experiment. It creates nothing.

## Audit limits and checkpoints

Every database mutation records its row hash in an append-only SHA-256 chain.
Verification recomputes chain links and checks the final hash for every stored
record, detecting edits to account/observation rows as well as ledger changes.
Export a checkpoint and keep it outside the experiment folder. Verify against
that trusted checkpoint later. A person with full database access can rebuild
the whole chain; a chain alone does not prevent that. An independently retained
checkpoint is needed to detect rebuilding before that checkpoint.

SQLite online backups include a consistent committed view, even with WAL. Backup
destinations must be new files. Never copy only the main database while a process
is writing. Keep a backup's associated halt state separately; after restoring,
set a local halt immediately and inspect health before resuming. The next writer
recovers committed pending orders, preserves original due times, and rejects
unreliable old fill evidence. SQLite rolls back uncommitted changes after a crash.
