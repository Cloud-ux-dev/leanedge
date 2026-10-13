"""Optional private Telegram commands; no trading or AI execution."""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from leanedge.settings import Settings, TelegramSettings


class TelegramError(RuntimeError):
    """Safe errors without URLs, tokens, message text, or API response bodies."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A bot token is in Telegram's URL path. Never forward it to another host.
        return None


def _api_error(code: int) -> TelegramError:
    if code == 401:
        return TelegramError("Bot token was refused. Check it privately in your settings.")
    if code == 409:
        return TelegramError(
            "Polling conflict. Stop other copies of this bot and remove any existing webhook."
        )
    if code == 429:
        return TelegramError("Telegram rate limit reached. Wait before restarting the bot.")
    if code >= 500:
        return TelegramError("Telegram is temporarily unavailable.", retryable=True)
    return TelegramError("Telegram refused the request. Check bot access and network settings.")


class TelegramClient:
    def __init__(self, token: str):
        self._token = token
        self._opener = build_opener(_NoRedirects())

    def call(self, method: str, payload: dict):
        if method not in {"getMe", "getUpdates", "sendMessage"}:
            raise ValueError("Unsupported Telegram method")
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self._opener.open(request, timeout=40) as response:
                body = json.load(response)
        except HTTPError as exc:
            exc.close()
            raise _api_error(exc.code) from None
        except (URLError, OSError):
            raise TelegramError(
                "Cannot reach Telegram; check your connection.", retryable=True,
            ) from None
        except (ValueError, UnicodeError):
            raise TelegramError("Telegram returned an unreadable response.") from None
        if not isinstance(body, dict):
            raise TelegramError("Telegram returned an unexpected response.")
        if body.get("ok") is not True:
            code = body.get("error_code", 400)
            raise _api_error(code if isinstance(code, int) else 400)
        if "result" not in body:
            raise TelegramError("Telegram returned an unexpected response.")
        return body["result"]


def reply_for_update(update: dict, settings: Settings, owner_id: int, username: str) -> str | None:
    """Respond only to the owner's direct messages. Never echo message text."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender, chat = message.get("from"), message.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return None
    if sender.get("id") != owner_id or chat.get("id") != owner_id or chat.get("type") != "private":
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    command, _, mention = text.strip().split()[0].partition("@")
    if mention and mention.lower() != username.lower():
        return None
    if command in {"/start", "/status"}:
        return (
            "LeanEdge — Day 1\nPAPER MODE — pretend money only.\n"
            f"Pretend starting balance: {settings.paper_starting_balance:.2f} USD\n"
            "No trades happen yet.\nUse /help for commands."
        )
    if command == "/live":
        return "LeanEdge only supports paper mode. Real trading is unavailable."
    return "Use /start, /status, or /help. I support simple commands; AI chat is not available yet."


def run_bot(settings: Settings, config: TelegramSettings) -> None:
    # Validate again at the service boundary, before any network calls.
    if settings.trading_mode != "paper":
        raise TelegramError("LeanEdge only supports paper mode.")
    client = TelegramClient(config.bot_token)
    identity = client.call("getMe", {})
    if not isinstance(identity, dict) or not isinstance(identity.get("username"), str):
        raise TelegramError("Telegram returned an unexpected bot identity.")
    username = identity["username"]
    print("Telegram connected. Open your bot's private chat and send /start. Ctrl+C stops it.", flush=True)
    offset = 0
    while True:
        try:
            updates = client.call("getUpdates", {
                "offset": offset, "timeout": 30, "allowed_updates": ["message"],
            })
            if not isinstance(updates, list):
                raise TelegramError("Telegram returned unexpected updates.")
            for update in updates:
                if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                    raise TelegramError("Telegram returned an unexpected update.")
                if update["update_id"] < offset:
                    continue
                reply = reply_for_update(update, settings, config.user_id, username)
                if reply is not None:
                    client.call("sendMessage", {"chat_id": config.user_id, "text": reply})
                offset = update["update_id"] + 1
        except TelegramError as exc:
            if not exc.retryable:
                raise
            # Error strings are ours; no raw exceptions, tokens, or messages are logged.
            print(f"{exc} Retrying shortly.", flush=True)
            time.sleep(5)
