# Frozen experiment rules

All fractions below are decimal fractions, not percentages written as whole
numbers. Changing strategy, risk, fill, data, AI, budget, or evaluation settings
requires a new ID **and a new database**. Existing results remain untouched.

## Entry and account risk

Starting paper capital is USD 1,000 per account. Each strategy/random entry's total
modeled purchase cost, including fees and adverse fill costs, must fit 2% of the
account's current marked equity and its unreserved cash. At most 3 tokens may be
open or reserved. Count at most 10 buy attempts per UTC day, including failed and
cancelled attempts. Count retries through the original order ID, never as a new
fill. Do not reopen a token within 60 minutes of an entry attempt.

Daily boundaries are UTC midnight. Weekly boundaries are Monday UTC midnight.
Anchors use the last observed equity before a boundary. Fees, realized losses and
unrealized price changes affect equity. At 5% daily or 10% weekly loss, persist a
halt for that period. A rebound does not clear it. New periods clear their own
period halt only with healthy valuations. A local human halt is separate and
requires a human resume. All entry halts still allow modeled exits.

Valuation uses the latest valid consensus price. When it becomes stale or a
provider fails, keep the lower of the last price and entry price, keep known
losses, and block risk additions. That conservative estimate is **not** a reliable
liquidation value. No missing observation becomes zero loss or an excuse to trade.

A signal cannot bypass independent controls. Recheck risk, current halt state,
and all safety checks at the simulated fill. Single-process decision ownership
uses an OS lock; SQLite transactions atomically update cash, positions, orders,
fills, and the audit record. A running process may be halted by another command;
stop it before explicit resume, which needs the decision lock and fresh health.

## Safety

Require all eight PASS results, each dated and with a source: USD 50,000 minimum
liquidity; token age at least 6 hours (verified pool existence is a lower bound);
revoked mint authority; revoked freeze authority; top-ten owner concentration at
most 20%; pool-specific verifiable liquidity protection; ordinary supported SPL
transfer behavior; exact-size read-only sell quote. UNKNOWN/FAIL blocks entry.
There is currently no live liquidity-protection decoder: see API_NOTES.md.

Market acquisition freshness and safety freshness are each 120 seconds. A future
timestamp is invalid. Prices differing by more than 5%, or liquidity differing by
more than 25%, are unexplained disagreement and block normalized data. Both market
providers must match the configured base mint and pool. Cached data retains its
original acquisition time. Neither provider promises a complete trade feed.

## Momentum after pullback

Defaults: 5-minute sampled bars; 24 contiguous completed bars of warm-up; at least
80% of expected samples in each bar, boundary samples within twice the polling
interval, no excessive internal gap. No incomplete or future bar can signal.

Let `C[-1]` be the newest completed close. Measure momentum as
`C[-4] / C[-16] - 1`, which must be at least 3%. Let the recent peak be the maximum
of `C[-4], C[-3], C[-2]`. Pullback is `(peak - C[-2]) / peak` and must be between
1% and 4%, inclusive. Trigger only if `C[-1] > C[-2]` and `C[-1] <= peak`. Propose
at most one entry per account/token/bar. These parameters are experiment choices,
not evidence of a profitable strategy.

Exit when a fresh sampled price reaches 5% below the filled entry price, 5% below
the highest observed price since entry, or after 6 hours. Stops trigger an order;
they do not promise a price. An exit must wait for an executable observation and
can fail, gap, or fill partially. Remaining exposure stays open and monitored.

## Paper fill model (pessimistic-v1)

There is no real executor. Seed 42 drives independent ID-based random outcomes,
so retries and restarts cannot reroll a failure. Base delay 2 seconds × 1.5 latency
multiplier × uniform [0.75, 1.25]. Failure assumption 10%. A failed attempt costs
USD 0.01, limited by paper cash. These are assumptions, not measured Solana rates.

At or after the due time, use the latest recorded valid snapshot available by
processing time, with an acquisition time at or after due. Sparse snapshots and
processing add execution delay. Never backdate a fill, interpolate future prices,
or use a later price for an earlier entry decision. Stale processing data rejects
the order; bad current market health blocks buys and leaves exits waiting. A
restart resumes pending orders without duplicating fills and rejects stale prices.

Buy price = midpoint × (1 + penalty); sell = midpoint × (1 - penalty).
Penalty = half spread (default full spread 0.2%) + 0.3% slippage + 0.2% adverse
penalty + impact. Impact = min(5%, midpoint notional / (liquidity × 2%)). The 2%
depth assumption approximates executable liquidity; total pool TVL is not an order
book. If a recorded spread is larger, use the larger spread. Add 0.3% fee on filled
notional. Reject buys exceeding the depth participation cap; sell up to that cap
and leave the rest open. Quantities round down to six decimals. Tokens requiring
other exact quote sizes may be conservatively rejected. No quote is treated as
a guaranteed fill. Jupiter sell quotes are a safety check; the simulated price
continues to use this fixed fallback model.

## Baselines and cost accounting

Five random accounts use seeds 17, 29, 43, 71, 101. At each eligible completed bar,
choose an entry with probability 5%, using an ID-derived seed. They share the
allowlist, safety checks, risk limits, exit rules, fill model and start time.
Strategy AI advice, if enabled, applies only to strategy proposals; this difference
is explicit and its direct cost is attributed only to the strategy.

Hold-SOL uses the same starting balance and sampled consensus valuations. It
proposes a single passive purchase with 98% of capital (the remaining 2% is a cash
buffer), with the same fee, failure, delay and depth model. Its exposure is a
predeclared benchmark exception to active position/stop/safety rules, and it does
not rebalance or retry a failed first purchase. A local halt still blocks its
initial purchase. Record failure and show it; never silently invent the benchmark.
An unusable SOL benchmark makes evaluation evidence incomplete.

Trading costs include entry/exit fees, adverse price differences from recorded
midpoints, and modeled failed fees. Net result = marked equity minus starting
capital. Gross result = net result plus those trading costs. Entry friction is
recognized immediately; realized result = closed midpoint gains minus all
recognized trading friction. Unrealized result is the remaining marked change.
The account report reconciles realized + unrealized = net result.

Operating expenses are separate. Reserve uncertain API expense conservatively;
usage-derived AI estimates distinguish input, cached input, and output tokens.
Hosting estimate accrues elapsed seconds at monthly amount / (30 × 86,400),
including downtime. Direct AI costs belong to the advised account; shared RPC,
market API and hosting costs are divided equally across all seven accounts.
This is a research cost allocation, not seven independently billed servers.
A human may record an actual invoice using `cost`; do not also enter the same
expense as a request estimate. Provider bills must be reconciled by a human.
Zero request costs mean an assumed existing free allowance, not verified billing.

Normal spending becomes FRUGAL at 60% of monthly USD 50 budget, or when accrued
costs are not covered by realized strategy results. Cache public observations;
pause optional paid RPC/quotes and AI analysis in FRUGAL. HALTED spending at the
monthly limit blocks optional paid work. Required free monitoring continues;
unavailable safety/monitoring blocks new entries. No spending mode changes risk.
Hypothetical survival = starting capital + net paper result - attributed operating
costs. At zero, end that account; a strategy-account depletion ends the experiment
and cancels pending entries. It never restarts itself. Paper gains cannot pay bills.
