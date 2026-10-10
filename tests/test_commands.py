from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from llm_tg_bot.commands import CommandHandler
from llm_tg_bot.config import Settings
from llm_tg_bot.providers import PiProvider
from llm_tg_bot.rendering import OutgoingMessage
from llm_tg_bot.session import SessionManager


class NewSessionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="llm-tg-bot-test-"))
        self.addCleanup(shutil.rmtree, self.workdir)
        settings = Settings(
            bot_tokens=["token"],
            allow_all_users=True,
            provider=PiProvider(cwd=self.workdir),
        )
        self.sent: list[str] = []

        async def send_message(chat_id, text, reply_markup=None):
            del chat_id, reply_markup
            self.sent.append(text)

        async def output_callback(chat_id: int, message: OutgoingMessage) -> None:
            del chat_id, message

        self.manager = SessionManager(
            provider=settings.provider,
            idle_timeout_seconds=60,
            output_callback=output_callback,
        )
        self.handler = CommandHandler(
            settings=settings,
            session_manager=self.manager,
            send_message=send_message,
            keyboard_factory=lambda: None,  # type: ignore[arg-type,return-value]
        )

    async def test_new_goes_straight_to_directory_prompt(self) -> None:
        await self.handler.handle(1, "/new")
        self.assertTrue(self.handler.has_pending_new_session(1))
        self.assertIn("Select workdir for pi", self.sent[-1])
        self.assertNotIn("Select provider", "\n".join(self.sent))

        self.assertTrue(await self.handler.handle_pending_input(1, "."))
        self.assertIn("[session started: pi", self.sent[-1])
        self.assertTrue(self.manager.has_session(1))

    async def test_new_with_directory_argument_skips_prompt(self) -> None:
        await self.handler.handle(1, "/new .")
        self.assertIn("[session started: pi", self.sent[-1])
        self.assertFalse(self.handler.has_pending_new_session(1))

    async def test_use_is_removed_and_status_drops_preferred_provider(self) -> None:
        await self.handler.handle(1, "/use pi")
        self.assertEqual(self.sent[-1], "Unknown command. Use /help.")
        await self.handler.handle(1, "/status")
        self.assertNotIn("Preferred provider", self.sent[-1])


if __name__ == "__main__":
    unittest.main()
