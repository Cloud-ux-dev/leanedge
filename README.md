# LeanEdge — Day 1

LeanEdge is an Automaton-inspired AI trading project. The plan is 14 days of
building, then 30 days of paper testing, with a video every day. The long-term
goal is to compare trading results with running costs and see whether a bot
could support itself. That work comes later.

Day 1 is just the project foundation. The app reads and checks settings, shows
its paper mode and pretend starting balance, then exits. The default balance is
1,000 USD of **pretend money**. No trades happen yet. There is no market data,
AI adviser, trading simulator, database, dashboard, or cost tracker.

Only `paper` mode is supported. `live` and every other mode are refused before
startup. There is no switch or approval file that enables real trading. No API
keys, exchange accounts, wallets, or private keys are needed.

## Setup

Use Python 3.11 or newer. From the repository folder, run:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows, activate with `.venv\Scripts\activate` instead.

Start the app:

```sh
python -m leanedge
```

Run the tests:

```sh
python -m unittest discover -s tests -v
```

The tests cover startup, balance checks, mode refusal, `.env` settings,
environment priority, and keeping secrets out of output.

## Settings

All settings are optional:

| Environment variable | Default | Allowed values |
| --- | --- | --- |
| `LEANEDGE_TRADING_MODE` | `paper` | Only `paper` (lowercase) |
| `LEANEDGE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; case does not matter |
| `LEANEDGE_PAPER_STARTING_BALANCE` | `1000.00` | Positive USD amount, with at most two decimal places; for example `2500.50` |

Optional local settings can go in `.env` in the folder where you run the app.
To start a new local settings file, copy `.env.example` to `.env` only if you
do not already have one. Actual environment variables take priority, even when
their values are invalid. `.env` values do not expand other variables.

An invalid setting prints a clear error and exits with status `2`. Successful
startup exits with status `0`. The paper notice and balance stay visible at
every supported log level. Logs include only the mode, log level, and pretend
balance. Input values from invalid settings are never echoed.

`.env`, local secret files, virtual environments, caches, and generated logs
are ignored by Git. `.env.example` stays trackable. Do not put real credentials
in tracked files or show local settings files in a video.

## Day 1 video demo

After activating the virtual environment, use these commands in Bash or zsh:

```sh
LEANEDGE_TRADING_MODE=paper LEANEDGE_LOG_LEVEL=INFO LEANEDGE_PAPER_STARTING_BALANCE=1000.00 python -m leanedge
LEANEDGE_TRADING_MODE=live python -m leanedge
echo "Exit status: $?"
```

The first command shows `PAPER MODE — pretend money only.` and `1000.00 USD`.
The second refuses to start, and the last command shows exit status `2`.
Do not display `.env` or dump your environment on screen.

Suggested narration: “Day 1 is the foundation. It starts with pretend money
and makes no trades. If I ask for live mode, it refuses to start.”
