from __future__ import annotations

import json

from llm_tg_bot.providers import OpencodeAdapter
from tests.base import BaseSessionTestCase
from tests.utils import option_value


def _ndjson(text: str, session_id: str) -> str:
    session_id_field = f'"sessionID":"{session_id}"'
    return "\n".join(
        [
            '{"type":"step_start","timestamp":0,' + session_id_field + ',"part":{"type":"step-start"}}',
            '{"type":"text","timestamp":0,' + session_id_field + ',"part":{"type":"text","text":'
            + json.dumps(text)
            + "}}",
            '{"type":"step_finish","timestamp":0,' + session_id_field + ',"part":{"type":"step-finish"}}',
        ]
    )


class OpencodeSessionIsolationTests(BaseSessionTestCase):
    adapter_class = OpencodeAdapter
    provider_name = "opencode"

    async def test_same_chat_reuses_explicit_opencode_session_id(self) -> None:
        outputs = [
            _ndjson("first response", "session-one"),
            _ndjson("second response", "session-one"),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "opencode")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "opencode")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_session = option_value(self.commands[0], "--session")
        second_session = option_value(self.commands[1], "--session")

        self.assertIsNone(first_session)
        self.assertEqual(second_session, "session-one")
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertIn("Command: opencode", self.manager.status_text(1))

    async def test_different_chats_get_distinct_opencode_session_ids(self) -> None:
        outputs = [
            _ndjson("first response", "session-one"),
            _ndjson("second response", "session-two"),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "opencode")
            await first.record.active_task

            second = await self.manager.send_text(2, "second", "opencode")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        first_session = option_value(self.commands[0], "--session")
        second_session = option_value(self.commands[1], "--session")

        self.assertIsNone(first_session)
        self.assertIsNone(second_session)
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertEqual(second.record.provider_session_id, "session-two")

    async def test_followup_uses_continue_flag_without_session_id(self) -> None:
        outputs = [
            _ndjson("first response", ""),
            _ndjson("second response", ""),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "opencode")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "opencode")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        self.assertNotIn("--session", self.commands[0])
        self.assertIn("--continue", self.commands[1])
        self.assertNotIn("--session", self.commands[1])

    async def test_run_subcommand_and_json_format_are_present(self) -> None:
        with self.mock_exec([_ndjson("hi", "session-one")]):
            first = await self.manager.send_text(1, "hi", "opencode")
            await first.record.active_task

        self.assertEqual(len(self.commands), 1)
        command = self.commands[0]
        self.assertEqual(command[0], "opencode")
        self.assertEqual(command[1], "run")
        self.assertIn("--format", command)
        self.assertEqual(option_value(command, "--format"), "json")
