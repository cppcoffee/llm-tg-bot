from __future__ import annotations

import json

from llm_tg_bot.providers import PiAdapter
from tests.base import BaseSessionTestCase
from tests.utils import option_value


def _ndjson(text: str, session_id: str) -> str:
    lines = []
    if session_id:
        lines.append(
            '{"type":"session","version":3,"id":'
            + json.dumps(session_id)
            + ',"cwd":"/tmp"}'
        )
    lines.append(
        '{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"prompt"}]}}'
    )
    lines.append(
        '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":'
        + json.dumps(text)
        + "}]}}"
    )
    return "\n".join(lines)


class PiSessionIsolationTests(BaseSessionTestCase):
    adapter_class = PiAdapter
    provider_name = "pi"

    async def test_same_chat_reuses_explicit_session_id(self) -> None:
        outputs = [
            _ndjson("first response", "session-one"),
            _ndjson("second response", "session-one"),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "pi")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "pi")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        self.assertIsNone(option_value(self.commands[0], "--session"))
        self.assertEqual(option_value(self.commands[1], "--session"), "session-one")
        self.assertEqual(first.record.provider_session_id, "session-one")
        self.assertIn("Command: pi", self.manager.status_text(1))

    async def test_followup_uses_continue_without_session_id(self) -> None:
        outputs = [
            _ndjson("first response", ""),
            _ndjson("second response", ""),
        ]

        with self.mock_exec(outputs):
            first = await self.manager.send_text(1, "first", "pi")
            await first.record.active_task

            second = await self.manager.send_text(1, "second", "pi")
            await second.record.active_task

        self.assertNotIn("--session", self.commands[0])
        self.assertIn("--continue", self.commands[1])
        self.assertNotIn("--session", self.commands[1])

    async def test_headless_json_flags_and_last_assistant_text(self) -> None:
        stream = "\n".join(
            [
                '{"type":"session","version":3,"id":"s1","cwd":"/tmp"}',
                '{"type":"message_end","message":{"role":"assistant","content":[{"type":"toolCall","id":"t1","name":"bash","arguments":{}}]}}',
                _ndjson("final answer", ""),
            ]
        )

        with self.mock_exec([stream]):
            first = await self.manager.send_text(1, "hi", "pi")
            await first.record.active_task

        command = self.commands[0]
        self.assertEqual(command[0], "pi")
        self.assertIn("--print", command)
        self.assertEqual(option_value(command, "--mode"), "json")
        self.assertIn("--approve", command)
        self.assertEqual(self.outputs, [(1, "final answer")])
