from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from llm_tg_bot.providers import AgyAdapter
from tests.base import BaseSessionTestCase
from tests.utils import FakeProcess, option_value


class AgySessionIsolationTests(BaseSessionTestCase):
    adapter_class = AgyAdapter
    provider_name = "agy"

    def mock_exec_agy(self, outputs: list[str], session_ids: list[str]):
        async def fake_exec(*command, **kwargs):
            del kwargs
            self.commands.append(tuple(command))

            log_file_path = None
            try:
                idx = command.index("--log-file")
                if idx + 1 < len(command):
                    log_file_path = command[idx + 1]
            except ValueError:
                pass

            if log_file_path:
                session_id = session_ids[len(self.commands) - 1]
                Path(log_file_path).write_text(
                    f"I0520 15:40:22.788609 732204 server.go:747] Created conversation {session_id}\n",
                    encoding="utf-8",
                )

            stdout = outputs[len(self.commands) - 1].encode("utf-8")
            return FakeProcess(stdout=stdout, returncode=0)

        return patch(
            "llm_tg_bot.request_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=fake_exec),
        )

    async def test_same_chat_consecutive_resumes_conversation_id(self) -> None:
        outputs = ["first response", "second response"]
        session_ids = ["session-one", "session-one"]

        with self.mock_exec_agy(outputs, session_ids):
            first = await self.manager.send_text(1, "first", "agy")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "agy")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)

        first_conversation = option_value(self.commands[0], "--conversation")
        first_log_file = option_value(self.commands[0], "--log-file")
        self.assertIsNone(first_conversation)
        self.assertIsNotNone(first_log_file)

        second_conversation = option_value(self.commands[1], "--conversation")
        self.assertEqual(second_conversation, "session-one")

        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertIn("Command: agy", self.manager.status_text(1))

    async def test_different_chats_get_distinct_agy_conversation_ids(self) -> None:
        outputs = ["first response", "second response"]
        session_ids = ["session-one", "session-two"]

        with self.mock_exec_agy(outputs, session_ids):
            first = await self.manager.send_text(1, "first", "agy")
            await first.record.active_task

            second = await self.manager.send_text(2, "second", "agy")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_conversation = option_value(self.commands[0], "--conversation")
        second_conversation = option_value(self.commands[1], "--conversation")

        self.assertIsNone(first_conversation)
        self.assertIsNone(second_conversation)
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertEqual(second.record.provider_session_id, "session-two")

    async def test_followup_only_emits_new_agy_reply(self) -> None:
        outputs = ["apple", "apple\nbanana"]
        session_ids = ["session-one", "session-one"]

        with self.mock_exec_agy(outputs, session_ids):
            first = await self.manager.send_text(1, "first", "agy")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "agy")
            await second.record.active_task

        self.assertEqual(self.outputs, [(1, "apple"), (1, "banana")])
        # last_response_text now stores the raw transcript for agy,
        # so the prefix-stripping in _extract_agy_latest_reply works correctly.
        self.assertEqual(self.manager._records[1].last_response_text, "apple\nbanana")
