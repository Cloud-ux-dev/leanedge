"""Read only known settings; never display settings input or secrets."""

from dataclasses import dataclass, field
from decimal import Decimal
import os
from pathlib import Path
import re
from typing import Mapping

from dotenv import dotenv_values


class SettingsError(ValueError):
    """A safe, user-facing settings error."""


@dataclass(frozen=True)
class Settings:
    trading_mode: str
    log_level: str
    paper_starting_balance: Decimal


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str = field(repr=False)
    user_id: int


def _local_values(env_file: Path | None) -> Mapping[str, str | None]:
    path = Path.cwd() / ".env" if env_file is None else env_file
    try:
        return dotenv_values(path, interpolate=False) if path.exists() else {}
    except (OSError, UnicodeError) as exc:
        raise SettingsError("Could not read the local .env file.") from exc


def load_telegram_settings(
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> TelegramSettings:
    """Require Telegram credentials only when Telegram is explicitly started."""
    environment = os.environ if environ is None else environ
    local = _local_values(env_file)
    token = environment.get("LEANEDGE_TELEGRAM_BOT_TOKEN", local.get("LEANEDGE_TELEGRAM_BOT_TOKEN"))
    user_id = environment.get("LEANEDGE_TELEGRAM_USER_ID", local.get("LEANEDGE_TELEGRAM_USER_ID"))
    if not token or not token.strip():
        raise SettingsError("Set LEANEDGE_TELEGRAM_BOT_TOKEN privately before starting Telegram.")
    token = token.strip()
    if not re.fullmatch(r"[A-Za-z0-9_:-]+", token):
        raise SettingsError("LEANEDGE_TELEGRAM_BOT_TOKEN contains invalid characters.")
    if not user_id or not re.fullmatch(r"[0-9]{1,20}", user_id.strip()) or int(user_id) <= 0:
        raise SettingsError("LEANEDGE_TELEGRAM_USER_ID must be your positive numeric Telegram user ID.")
    return TelegramSettings(token, int(user_id))


def load_settings(
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Use process settings first, then the optional .env, then defaults.

    Disable interpolation so .env values cannot pull in unrelated secrets.
    """
    environment = os.environ if environ is None else environ
    local = _local_values(env_file)

    def value(name: str, default: str) -> str:
        raw = environment[name] if name in environment else local.get(name, default)
        if raw is None:
            raise SettingsError(f"{name} must have a value.")
        return raw.strip()

    mode = value("LEANEDGE_TRADING_MODE", "paper")
    if mode != "paper":
        raise SettingsError("LEANEDGE_TRADING_MODE must be paper. Real trading is unavailable.")

    level = value("LEANEDGE_LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise SettingsError(
            "LEANEDGE_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL."
        )

    balance_text = value("LEANEDGE_PAPER_STARTING_BALANCE", "1000.00")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", balance_text):
        raise SettingsError(
            "LEANEDGE_PAPER_STARTING_BALANCE must be a positive USD amount "
            "with at most two decimal places (for example, 1000.00)."
        )
    balance = Decimal(balance_text)
    if balance <= 0:
        raise SettingsError("LEANEDGE_PAPER_STARTING_BALANCE must be greater than zero.")

    return Settings(mode, level, balance)
