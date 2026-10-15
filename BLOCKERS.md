# Genuine blockers and outstanding evidence

1. **Pool-specific liquidity protection is unavailable.** The first real token/pool
   allowlist has not been chosen. No reviewed on-chain decoder verifies the
   protection model of a selected pool. Real-data strategy/random entries are
   therefore blocked at UNKNOWN. Generic pool liquidity, a pool label, a quote,
   or a manually set boolean cannot bypass it. This is unfinished integration
   engineering, not an optional warning.
2. **Live data access was not verified.** Public DexScreener and GeckoTerminal
   transport probes failed in this cloud environment. Their documentation was
   read via browsing. Fake-transport tests passed; these are not live checks.
   Configure permitted outbound access and run `doctor --online` locally.
3. **No Helius, Jupiter, or DeepSeek credentials are configured here.** Helius and
   Jupiter read-only adapters are implemented and mocked; actual account access,
   quotas, indexing consistency, exact sell sizes and provider bills need checking.
   Jupiter V1 remains documented but is superseded: migration must preserve the
   quote-only boundary and requires new validation.
4. **Optional AI pricing expires.** Official example rates were read 2026-10-15;
   default verification lifetime is 7 days. Expired/uncertain pricing blocks AI
   advice. No authenticated model call, usage bill, or model availability was
   verified. Changes to a frozen model/pricing policy require a new experiment.
5. **No 12/24/48-hour soak or 30-day paper evaluation has finished.** Executable
   wall-clock soak tooling is provided. A short paced synthetic smoke check is
   not a soak. There is no qualifying trading history or profitability claim.
6. **Windows execution is not available in this cloud session.** PowerShell setup
   avoids activation and execution-policy changes. The Windows `msvcrt` lock path
   is implemented but needs a Windows run, including two-process exclusion and
   local halt timing. Python 3.12/Linux is the tested environment.
7. **Gitleaks was not installed/run.** Configuration and redacted commands are
   supplied. No credential scan result is claimed.

The built local workflow, demo, reports, tests, ledger, backup, gate, controls,
and CLI are usable. The complete real-data trading trial is **not ready** until
these dependencies and checks are resolved. Continue collecting honest evidence;
never mark missing safety or failed integrations as passed.

8. **Real-socket bounded shutdown needs rechecking.** A `run --seconds` smoke
   attempt timed out during Python's default-executor shutdown in this restricted
   executor. Mocked blocked-transport cancellation passed. A network-enabled
   diagnostic retry was not executed because automatic approval review hit a
   usage limit; that was a review failure, not a safety determination. Do not
   treat the real-socket shutdown check as passed. Check bounded stop and Ctrl+C
   locally before a long unattended run.
