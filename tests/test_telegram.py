"""Telegram behavior is tested offline; no tokens or real messages are needed."""

from dataclasses import replace
from decimal import Decimal
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from leanedge.settings import Settings, SettingsError, TelegramSettings, load_telegram_settings
from leanedge.telegram import TelegramClient, TelegramError, _NoRedirects, reply_for_update, run_bot


SETTINGS = Settings("paper", "INFO", Decimal("1000.00"))
FAKE_TOKEN = "123456:test-only-token-not-a-real-credential"
CONFIG = TelegramSettings(FAKE_TOKEN, 42)


def message(text="/status", *, sender=42, chat=42, kind="private", update_id=1):
    return {"update_id": update_id, "message": {
        "from": {"id": sender}, "chat": {"id": chat, "type": kind}, "text": text,
    }}


class TelegramSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env"

    def test_missing_settings_and_bad_user_ids_are_rejected(self):
        with self.assertRaisesRegex(SettingsError, "BOT_TOKEN"):
            load_telegram_settings(self.path, {})
        for user_id in ("", "0", "-1", "someone", "1.5"):
            with self.subTest(user_id=user_id), self.assertRaisesRegex(SettingsError, "USER_ID"):
                load_telegram_settings(self.path, {
                    "LEANEDGE_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
                    "LEANEDGE_TELEGRAM_USER_ID": user_id,
                })

    def test_environment_priority_and_hidden_token(self):
        self.path.write_text(
            f"LEANEDGE_TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nLEANEDGE_TELEGRAM_USER_ID=42\n",
            encoding="utf-8",
        )
        self.assertEqual(load_telegram_settings(self.path, {}).user_id, 42)
        config = load_telegram_settings(self.path, {
            "LEANEDGE_TELEGRAM_BOT_TOKEN": "987654:another-test-token",
            "LEANEDGE_TELEGRAM_USER_ID": "84",
        })
        self.assertEqual(config.user_id, 84)
        self.assertEqual(config.bot_token, "987654:another-test-token")
        self.assertNotIn(config.bot_token, repr(config))
        with self.assertRaises(SettingsError):
            load_telegram_settings(self.path, {"LEANEDGE_TELEGRAM_BOT_TOKEN": ""})

    def test_cli_rejects_live_before_trying_telegram(self):
        result = subprocess.run(
            [sys.executable, "-m", "leanedge", "--telegram"], cwd=self.directory.name,
            env={"LEANEDGE_TRADING_MODE": "live"}, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be paper", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_requires_credentials_only_in_telegram_mode(self):
        result = subprocess.run(
            [sys.executable, "-m", "leanedge", "--telegram"], cwd=self.directory.name,
            env={}, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BOT_TOKEN", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_token_cannot_inject_url_or_error_output(self):
        token = "secret-test-marker/another-host?query=true"
        with self.assertRaises(SettingsError) as caught:
            load_telegram_settings(self.path, {
                "LEANEDGE_TELEGRAM_BOT_TOKEN": token, "LEANEDGE_TELEGRAM_USER_ID": "42",
            })
        self.assertNotIn(token, str(caught.exception))


class TelegramCommandsTests(unittest.TestCase):
    def reply(self, update):
        return reply_for_update(update, SETTINGS, CONFIG.user_id, "leanedge_test_bot")

    def test_owner_can_use_start_status_and_help(self):
        for command in ("/start", "/status", "/status@leanedge_test_bot"):
            with self.subTest(command=command):
                response = self.reply(message(command))
                self.assertIn("PAPER MODE — pretend money only.", response)
                self.assertIn("1000.00 USD", response)
                self.assertIn("No trades happen yet.", response)
        self.assertIn("/status", self.reply(message("/help")))

    def test_other_people_groups_and_non_text_messages_are_ignored(self):
        for update in (
            message(sender=84), message(chat=84), message(chat=-99, kind="group"),
            message(kind="supergroup"), message("/status@other_bot"),
            message(None), message(" "), {}, {"message": None},
        ):
            with self.subTest(update=update):
                self.assertIsNone(self.reply(update))

    def test_no_ai_claims_echoes_or_live_switch(self):
        secret = "test-only-sensitive-message"
        response = self.reply(message(secret))
        self.assertNotIn(secret, response)
        self.assertIn("AI chat is not available", response)
        self.assertIn("Real trading is unavailable", self.reply(message("/live")))


class TelegramTransportTests(unittest.TestCase):
    def test_post_request_payload_and_response(self):
        client = TelegramClient(FAKE_TOKEN)
        client._opener = Mock()
        client._opener.open.return_value = BytesIO(json.dumps({"ok": True, "result": []}).encode())
        self.assertEqual(client.call("getUpdates", {"timeout": 30}), [])
        args, kwargs = client._opener.open.call_args
        self.assertEqual(args[0].get_method(), "POST")
        self.assertEqual(json.loads(args[0].data), {"timeout": 30})
        self.assertTrue(args[0].full_url.startswith("https://api.telegram.org/bot"))
        self.assertEqual(kwargs["timeout"], 40)

    def test_api_errors_never_expose_tokens_urls_or_response_bodies(self):
        url = f"https://api.telegram.org/bot{FAKE_TOKEN}/getMe"
        errors = [URLError(url), ValueError(FAKE_TOKEN)]
        for code in (401, 403, 409, 429, 500):
            errors.append(HTTPError(url, code, FAKE_TOKEN, {}, BytesIO(FAKE_TOKEN.encode())))
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                client = TelegramClient(FAKE_TOKEN)
                client._opener = Mock()
                client._opener.open.side_effect = error
                with self.assertRaises(TelegramError) as caught:
                    client.call("getMe", {})
                self.assertNotIn(FAKE_TOKEN, str(caught.exception))
                self.assertNotIn("https://", str(caught.exception))

    def test_no_redirects(self):
        self.assertIsNone(_NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.example"))

    def test_malformed_and_unsuccessful_api_responses(self):
        for body in (b"invalid json", b"[]", b'{"ok":true}', b'{"ok":false,"description":"secret"}'):
            with self.subTest(body=body):
                client = TelegramClient(FAKE_TOKEN)
                client._opener = Mock()
                client._opener.open.return_value = BytesIO(body)
                with self.assertRaises(TelegramError) as caught:
                    client.call("getMe", {})
                self.assertNotIn("secret", str(caught.exception))


class TelegramPollingTests(unittest.TestCase):
    @patch("leanedge.telegram.TelegramClient")
    def test_cli_runs_telegram_and_stops_cleanly_without_printing_token(self, client_type):
        from leanedge.__main__ import main

        client_type.return_value.call.side_effect = [
            {"username": "leanedge_test_bot"}, KeyboardInterrupt,
        ]
        output = StringIO()
        with (
            patch("sys.argv", ["leanedge", "--telegram"]),
            patch("sys.stdout", output),
            patch("leanedge.settings._local_values", return_value={}),
            patch.dict("os.environ", {
                "LEANEDGE_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
                "LEANEDGE_TELEGRAM_USER_ID": "42",
            }, clear=True),
        ):
            self.assertEqual(main(), 0)
        self.assertIn("Telegram connected", output.getvalue())
        self.assertIn("Telegram stopped", output.getvalue())
        self.assertNotIn(FAKE_TOKEN, output.getvalue())

    @patch("leanedge.telegram.TelegramClient")
    def test_polling_sends_only_owner_reply_and_advances_offset(self, client_type):
        client = client_type.return_value
        client.call.side_effect = [
            {"username": "leanedge_test_bot"},
            [message(sender=84), message(update_id=2)],
            {"message_id": 5}, KeyboardInterrupt,
        ]
        output = StringIO()
        with patch("sys.stdout", output), self.assertRaises(KeyboardInterrupt):
            run_bot(SETTINGS, CONFIG)
        calls = client.call.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["getMe", "getUpdates", "sendMessage", "getUpdates"])
        self.assertEqual(calls[2].args[1]["chat_id"], 42)
        self.assertIn("1000.00 USD", calls[2].args[1]["text"])
        self.assertEqual(calls[3].args[1]["offset"], 3)
        self.assertNotIn(FAKE_TOKEN, output.getvalue())

    @patch("leanedge.telegram.time.sleep")
    @patch("leanedge.telegram.TelegramClient")
    def test_transient_polling_failure_retries(self, client_type, sleep):
        client_type.return_value.call.side_effect = [
            {"username": "leanedge_test_bot"},
            TelegramError("Connection unavailable.", retryable=True), KeyboardInterrupt,
        ]
        with patch("sys.stdout", StringIO()), self.assertRaises(KeyboardInterrupt):
            run_bot(SETTINGS, CONFIG)
        sleep.assert_called_once_with(5)

    @patch("leanedge.telegram.TelegramClient")
    def test_live_service_refused_before_network(self, client_type):
        with self.assertRaisesRegex(TelegramError, "only supports paper"):
            run_bot(replace(SETTINGS, trading_mode="live"), CONFIG)
        client_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()
