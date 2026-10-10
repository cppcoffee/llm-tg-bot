from __future__ import annotations

from tests.base import BaseSessionTestCase
from tests.utils import option_value


class PiSessionTests(BaseSessionTestCase):
    async def test_same_chat_reuses_generated_session_id(self) -> None:
        with self.mock_exec(["first response", "second response"]):
            first = await self.manager.send_text(1, "first")
            await first.record.active_task

            second = await self.manager.send_text(1, "second")
            await second.record.active_task

        self.assertEqual(len(self.commands), 2)
        session_id = option_value(self.commands[0], "--session-id")
        self.assertTrue(session_id)
        self.assertEqual(option_value(self.commands[1], "--session-id"), session_id)
        self.assertNotIn("--continue", self.commands[1])

    async def test_new_session_gets_a_fresh_id(self) -> None:
        with self.mock_exec(["one", "two"]):
            first = await self.manager.send_text(1, "first")
            await first.record.active_task
            old_session_id = first.record.session_id

            await self.manager.start_session(1)
            second = await self.manager.send_text(1, "second")
            await second.record.active_task

        self.assertNotEqual(old_session_id, second.record.session_id)
        self.assertNotEqual(
            option_value(self.commands[0], "--session-id"),
            option_value(self.commands[1], "--session-id"),
        )

    async def test_headless_print_flags_and_final_text(self) -> None:
        with self.mock_exec(["final answer"]):
            first = await self.manager.send_text(1, "hi")
            await first.record.active_task

        command = self.commands[0]
        self.assertEqual(command[0], "pi")
        self.assertIn("--print", command)
        self.assertIn("--approve", command)
        self.assertEqual(self.outputs, [(1, "final answer")])
