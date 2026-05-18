from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from llm_tg_bot.providers import GeminiAdapter, ProviderSpec
from llm_tg_bot.rendering import OutgoingMessage
from llm_tg_bot.session import SessionManager


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _option_value(command: tuple[str, ...], option: str) -> str | None:
    try:
        index = command.index(option)
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


class GeminiSessionIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.provider = ProviderSpec(adapter=GeminiAdapter())
        self.commands: list[tuple[str, ...]] = []
        self.outputs: list[tuple[int, str]] = []

        async def output_callback(chat_id: int, message: OutgoingMessage) -> None:
            self.outputs.append((chat_id, message.text))

        self.manager = SessionManager(
            providers={"gemini": self.provider},
            idle_timeout_seconds=60,
            output_callback=output_callback,
        )

    async def test_different_chats_get_distinct_gemini_session_ids(self) -> None:
        outputs = [
            json.dumps({"response": "first response", "session_id": "session-one"}),
            json.dumps({"response": "second response", "session_id": "session-two"}),
        ]

        async def fake_exec(*command, **kwargs):
            del kwargs
            self.commands.append(tuple(command))
            stdout = outputs[len(self.commands) - 1].encode("utf-8")
            return _FakeProcess(stdout=stdout, returncode=0)

        with patch(
            "llm_tg_bot.request_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=fake_exec),
        ):
            first = await self.manager.send_text(1, "first", "gemini")
            await first.record.active_task

            second = await self.manager.send_text(2, "second", "gemini")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        # Current implementation uses --resume latest for followups, 
        # but here we are checking if it *would* use separate IDs if it could.
        
        first_resume = _option_value(self.commands[0], "--resume")
        second_resume = _option_value(self.commands[1], "--resume")

        self.assertIsNone(first_resume)
        self.assertIsNone(second_resume)
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertEqual(second.record.provider_session_id, "session-two")

    async def test_same_chat_reuses_explicit_gemini_session_id(self) -> None:
        outputs = [
            json.dumps({"response": "first response", "session_id": "session-one"}),
            json.dumps({"response": "second response", "session_id": "session-one"}),
        ]

        async def fake_exec(*command, **kwargs):
            del kwargs
            self.commands.append(tuple(command))
            stdout = outputs[len(self.commands) - 1].encode("utf-8")
            return _FakeProcess(stdout=stdout, returncode=0)

        with patch(
            "llm_tg_bot.request_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=fake_exec),
        ):
            first = await self.manager.send_text(1, "first", "gemini")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "gemini")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_resume = _option_value(self.commands[0], "--resume")
        second_resume = _option_value(self.commands[1], "--resume")

        self.assertIsNone(first_resume)
        self.assertEqual(second_resume, "session-one")
        self.assertEqual(first.record.provider_session_id, "session-one")
