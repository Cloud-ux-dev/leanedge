"""Validate the foundation, show paper settings, and exit."""

import logging
import sys

from leanedge.settings import SettingsError, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2

    logger = logging.getLogger("leanedge.startup")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(settings.log_level)
    logger.propagate = False

    # The safety message stays visible at every supported log level.
    print("LeanEdge — Day 1")
    print("PAPER MODE — pretend money only.")
    print(f"Pretend starting balance: {settings.paper_starting_balance:.2f} USD")
    # Explicit safe fields only: never log the environment or the settings object.
    logger.info(
        "Settings: trading_mode=%s log_level=%s paper_starting_balance_usd=%s",
        settings.trading_mode,
        settings.log_level,
        f"{settings.paper_starting_balance:.2f}",
    )
    print("No trades happen yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
