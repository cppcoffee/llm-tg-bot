from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from telegram import Update
from telegram.error import Conflict

from llm_tg_bot.bot import BridgeBot
from llm_tg_bot.config import Settings
from llm_tg_bot.main import _bot_token_lock, async_main


def settings_for(*tokens: str) -> Settings:
    return Settings(
        bot_tokens=list(tokens),
        allow_all_users=True,
        default_provider="mock",
    )


class PollingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bridge = BridgeBot("123:test", settings_for("123:test"))
        self.api = Mock(
            id=123,
            initialize=AsyncMock(),
            delete_webhook=AsyncMock(),
            shutdown=AsyncMock(),
            get_updates=AsyncMock(),
        )
        self.bridge._bot = self.api
        self.bridge._idle_cleanup_loop = AsyncMock()
        self.bridge._handle_update = AsyncMock()

    async def test_repeated_conflicts_recover_without_resetting_bot_or_offset(self) -> None:
        first, second = Update(100), Update(101)
        self.api.get_updates.side_effect = [
            [first],
            *[Conflict("terminated by other getUpdates request") for _ in range(12)],
            [second],
            asyncio.CancelledError(),
        ]
        with patch("llm_tg_bot.bot.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertLogs("llm_tg_bot.bot", level="WARNING"):
                with self.assertRaises(asyncio.CancelledError):
                    await self.bridge.run()

        self.assertEqual(sleep.await_args_list, [call(30)] * 12)
        self.api.initialize.assert_awaited_once()
        self.api.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        self.api.shutdown.assert_awaited_once()
        self.assertEqual(self.bridge._handle_update.await_args_list, [call(first), call(second)])
        self.assertEqual(self.bridge._offset, 102)
        self.assertEqual(
            self.api.get_updates.await_args_list,
            [call(timeout=30)] + [call(timeout=30, offset=101)] * 13
            + [call(timeout=30, offset=102)],
        )

    async def test_shutdown_during_conflict_backoff(self) -> None:
        self.api.get_updates.side_effect = Conflict("another poller")
        with patch("llm_tg_bot.bot.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertLogs("llm_tg_bot.bot", level="WARNING"):
                with self.assertRaises(asyncio.CancelledError):
                    await self.bridge.run()
        self.api.get_updates.assert_awaited_once()
        self.api.shutdown.assert_awaited_once()

    async def test_duplicate_start_does_not_start_any_bot_or_leak_other_locks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("llm_tg_bot.main.tempfile.gettempdir", return_value=directory):
                with _bot_token_lock("456:test"):
                    with (
                        patch("llm_tg_bot.main.load_settings", return_value=settings_for("123:test", "456:test")),
                        patch("llm_tg_bot.main.BridgeBot") as bot,
                    ):
                        with self.assertRaises(SystemExit):
                            await async_main()
                        bot.assert_not_called()
                    with _bot_token_lock("123:test"):
                        pass


class TokenLockTests(unittest.TestCase):
    def test_competing_process_is_rejected_and_lock_releases_after_exit(self) -> None:
        script = (
            "from llm_tg_bot.main import _bot_token_lock\n"
            "with _bot_token_lock('123:test'):\n"
            "    pass\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, TMPDIR=directory)
            with patch("llm_tg_bot.main.tempfile.gettempdir", return_value=directory):
                with _bot_token_lock("123:test"):
                    result = subprocess.run(
                        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=10,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("already using", result.stderr)
                    self.assertNotIn("123:test", result.stderr)
                result = subprocess.run(
                    [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_different_tokens_can_run_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("llm_tg_bot.main.tempfile.gettempdir", return_value=directory):
                with _bot_token_lock("123:test"), _bot_token_lock("456:test"):
                    pass
