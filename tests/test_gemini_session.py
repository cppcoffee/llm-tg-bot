from __future__ import annotations

import json
from llm_tg_bot.providers import GeminiAdapter
from tests.base import BaseSessionTestCase
from tests.utils import option_value

class GeminiSessionIsolationTests(BaseSessionTestCase):
    adapter_class = GeminiAdapter
    provider_name = "gemini"

    async def test_different_chats_get_distinct_gemini_session_ids(self) -> None:
        outputs = [
            json.dumps({"response": "first response", "session_id": "session-one"}),
            json.dumps({"response": "second response", "session_id": "session-two"}),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "gemini")
            await first.record.active_task

            second = await self.manager.send_text(2, "second", "gemini")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_resume = option_value(self.commands[0], "--resume")
        second_resume = option_value(self.commands[1], "--resume")

        self.assertIsNone(first_resume)
        self.assertIsNone(second_resume)
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertEqual(second.record.provider_session_id, "session-two")

    async def test_same_chat_reuses_explicit_gemini_session_id(self) -> None:
        outputs = [
            json.dumps({"response": "first response", "session_id": "session-one"}),
            json.dumps({"response": "second response", "session_id": "session-one"}),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "gemini")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "gemini")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_resume = option_value(self.commands[0], "--resume")
        second_resume = option_value(self.commands[1], "--resume")

        self.assertIsNone(first_resume)
        self.assertEqual(second_resume, "session-one")
        self.assertEqual(first.record.provider_session_id, "session-one")
