"""Check settings and the actual installed app's startup boundary."""

from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from leanedge.settings import SettingsError, load_settings


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env_file = self.root / ".env"

    def settings(self, **environment):
        return load_settings(self.env_file, environment)

    def run_app(self, **overrides):
        environment = {
            name: value for name, value in os.environ.items()
            if not name.startswith("LEANEDGE_")
        }
        environment.update(overrides)
        return subprocess.run(
            [sys.executable, "-m", "leanedge"], cwd=self.root,
            env=environment, capture_output=True, text=True, timeout=10,
        )

    def test_default_startup_needs_no_credentials(self):
        settings = self.settings()
        self.assertEqual(settings.trading_mode, "paper")
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.paper_starting_balance, Decimal("1000.00"))
        result = self.run_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LeanEdge", result.stdout)
        self.assertIn("PAPER MODE — pretend money only.", result.stdout)
        self.assertIn("1000.00 USD", result.stdout)
        self.assertIn("No trades happen yet.", result.stdout)

    def test_paper_balance_override(self):
        result = self.run_app(LEANEDGE_PAPER_STARTING_BALANCE="2500.50")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2500.50 USD", result.stdout)

    def test_invalid_balances_fail_before_startup(self):
        for balance in ("0", "-1", "NaN", "Infinity", "1e3", "1.001", "", "abc", "1_000"):
            with self.subTest(balance=balance):
                result = self.run_app(LEANEDGE_PAPER_STARTING_BALANCE=balance)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("LEANEDGE_PAPER_STARTING_BALANCE", result.stderr)

    def test_unsupported_modes_fail_before_startup(self):
        for mode in ("live", "test", "", "PAPER"):
            with self.subTest(mode=mode):
                result = self.run_app(LEANEDGE_TRADING_MODE=mode)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("LEANEDGE_TRADING_MODE must be paper", result.stderr)

    def test_dotenv_settings_are_loaded(self):
        self.env_file.write_text(
            "LEANEDGE_TRADING_MODE=paper\nLEANEDGE_LOG_LEVEL=debug\n"
            "LEANEDGE_PAPER_STARTING_BALANCE='1250.25'\n", encoding="utf-8",
        )
        settings = self.settings()
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.paper_starting_balance, Decimal("1250.25"))
        result = self.run_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1250.25 USD", result.stdout)

    def test_environment_overrides_dotenv(self):
        self.env_file.write_text(
            "LEANEDGE_TRADING_MODE=live\nLEANEDGE_LOG_LEVEL=invalid\n"
            "LEANEDGE_PAPER_STARTING_BALANCE=invalid\n", encoding="utf-8",
        )
        result = self.run_app(
            LEANEDGE_TRADING_MODE="paper", LEANEDGE_LOG_LEVEL="WARNING",
            LEANEDGE_PAPER_STARTING_BALANCE="42.50",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PAPER MODE — pretend money only.", result.stdout)
        self.assertIn("42.50 USD", result.stdout)
        self.assertNotIn("INFO:", result.stdout)

    def test_environment_live_cannot_be_overridden_by_dotenv(self):
        self.env_file.write_text("LEANEDGE_TRADING_MODE=paper\n", encoding="utf-8")
        result = self.run_app(LEANEDGE_TRADING_MODE="live")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_invalid_log_level(self):
        result = self.run_app(LEANEDGE_LOG_LEVEL="verbose")
        self.assertEqual(result.returncode, 2)
        self.assertIn("LEANEDGE_LOG_LEVEL must be", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_all_log_levels_keep_paper_notice_visible(self):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            with self.subTest(level=level):
                result = self.run_app(LEANEDGE_LOG_LEVEL=level)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PAPER MODE — pretend money only.", result.stdout)

    def test_secrets_are_not_printed_or_interpolated(self):
        secret = "test-only-sensitive-marker"
        self.env_file.write_text(f"UNRELATED_LOCAL_SECRET={secret}\n", encoding="utf-8")
        result = self.run_app(UNRELATED_SECRET=secret, LEANEDGE_LOG_LEVEL="DEBUG")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn("UNRELATED", result.stdout + result.stderr)
        self.env_file.write_text(
            "LEANEDGE_PAPER_STARTING_BALANCE=${UNRELATED_SECRET}\n", encoding="utf-8",
        )
        result = self.run_app(UNRELATED_SECRET=secret)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_invalid_setting_values_are_not_echoed(self):
        secret = "test-only-sensitive-marker"
        for name in ("LEANEDGE_TRADING_MODE", "LEANEDGE_LOG_LEVEL", "LEANEDGE_PAPER_STARTING_BALANCE"):
            with self.subTest(name=name):
                result = self.run_app(**{name: secret})
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(secret, result.stdout + result.stderr)

    def test_missing_local_value_has_clear_error(self):
        self.env_file.write_text("LEANEDGE_LOG_LEVEL\n", encoding="utf-8")
        with self.assertRaisesRegex(SettingsError, "LEANEDGE_LOG_LEVEL must have a value"):
            self.settings()


if __name__ == "__main__":
    unittest.main()
