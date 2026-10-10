from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from llm_tg_bot.providers import PiProvider
from llm_tg_bot.rendering import OutgoingMessage
from llm_tg_bot.session import SessionManager
from tests.utils import FakeProcess


class BaseSessionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.provider = PiProvider()
        self.commands: list[tuple[str, ...]] = []
        self.outputs: list[tuple[int, str]] = []

        async def output_callback(chat_id: int, message: OutgoingMessage) -> None:
            self.outputs.append((chat_id, message.text))

        self.manager = SessionManager(
            provider=self.provider,
            idle_timeout_seconds=60,
            output_callback=output_callback,
        )

    def mock_exec(self, outputs: list[str]):
        async def fake_exec(*command, **kwargs):
            del kwargs
            self.commands.append(tuple(command))
            stdout = outputs[len(self.commands) - 1].encode("utf-8")
            return FakeProcess(stdout=stdout, returncode=0)
        
        return patch(
            "llm_tg_bot.request_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=fake_exec),
        )
