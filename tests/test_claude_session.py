from __future__ import annotations

import json
from llm_tg_bot.providers import ClaudeAdapter
from tests.base import BaseSessionTestCase
from tests.utils import option_value

class ClaudeSessionIsolationTests(BaseSessionTestCase):
    adapter_class = ClaudeAdapter
    provider_name = "claude"

    async def test_same_chat_reuses_explicit_claude_session_id(self) -> None:
        outputs = [
            json.dumps({"result": "first response", "session_id": "session-one"}),
            json.dumps({"result": "second response", "session_id": "session-one"}),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "claude")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "claude")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_resume = option_value(self.commands[0], "--resume")
        second_resume = option_value(self.commands[1], "--resume")

        self.assertIsNone(first_resume)
        self.assertEqual(second_resume, "session-one")
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertIn("Command: claude", self.manager.status_text(1))

    async def test_different_chats_get_distinct_claude_session_ids(self) -> None:
        outputs = [
            json.dumps({"result": "first response", "session_id": "session-one"}),
            json.dumps({"result": "second response", "session_id": "session-two"}),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "claude")
            await first.record.active_task

            second = await self.manager.send_text(2, "second", "claude")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_resume = option_value(self.commands[0], "--resume")
        second_resume = option_value(self.commands[1], "--resume")

        self.assertIsNone(first_resume)
        self.assertIsNone(second_resume)
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertEqual(second.record.provider_session_id, "session-two")
