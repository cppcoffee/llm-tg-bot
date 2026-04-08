from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from llm_tg_bot.providers import PreparedRequest, ProviderAdapter, ProviderSpec
from llm_tg_bot.rendering import OutgoingMessage, RenderMode, build_message_chunks
from llm_tg_bot.session import SessionManager


class _FakeAdapter(ProviderAdapter):
    name = "fake"
    executable = "fake"

    def prepare_request(self, prompt: str, resume: bool) -> PreparedRequest:
        del prompt, resume
        return PreparedRequest(command=(self.executable,))

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
    ) -> str:
        del stderr_text, return_code, output_file
        return stdout_text


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


class RenderingTests(unittest.TestCase):
    def test_markdown_chunks_render_common_constructs(self) -> None:
        chunks = build_message_chunks(
            OutgoingMessage(
                "# Title\n\n- **bold** item\n\n`inline`\n\n```python\nprint('hi')\n```",
                render_mode=RenderMode.MARKDOWN,
            ),
            limit=4096,
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].parse_mode, "HTML")
        self.assertIn("<b>Title</b>", chunks[0].text)
        self.assertIn("• <b>bold</b> item", chunks[0].text)
        self.assertIn("<code>inline</code>", chunks[0].text)
        self.assertIn("<pre>print(&#x27;hi&#x27;)</pre>", chunks[0].text)

    def test_markdown_chunks_split_code_blocks_without_breaking_tags(self) -> None:
        chunks = build_message_chunks(
            OutgoingMessage(
                "```\nline 1\nline 2\nline 3\nline 4\n```",
                render_mode=RenderMode.MARKDOWN,
            ),
            limit=24,
        )

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.parse_mode, "HTML")
            self.assertTrue(chunk.text.startswith("<pre>"))
            self.assertTrue(chunk.text.endswith("</pre>"))

    def test_plain_text_chunks_do_not_set_parse_mode(self) -> None:
        chunks = build_message_chunks(
            OutgoingMessage("[session started]"),
            limit=4096,
        )

        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0].parse_mode)
        self.assertEqual(chunks[0].text, "[session started]")


class SessionManagerRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_provider_output_uses_markdown_rendering(self) -> None:
        outputs: list[tuple[int, OutgoingMessage]] = []

        async def output_callback(chat_id: int, message: OutgoingMessage) -> None:
            outputs.append((chat_id, message))

        manager = SessionManager(
            providers={"fake": ProviderSpec(adapter=_FakeAdapter())},
            idle_timeout_seconds=5,
            output_callback=output_callback,
        )
        record = await manager.start_session(9, "fake")
        process = _FakeProcess(stdout=b"**hello**", returncode=0)

        with patch(
            "llm_tg_bot.session.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            await manager._run_request(record, "prompt")

        self.assertEqual(
            outputs,
            [(9, OutgoingMessage("**hello**", render_mode=RenderMode.MARKDOWN))],
        )

    async def test_failed_provider_output_stays_plain_text(self) -> None:
        outputs: list[tuple[int, OutgoingMessage]] = []

        async def output_callback(chat_id: int, message: OutgoingMessage) -> None:
            outputs.append((chat_id, message))

        class _FailingAdapter(_FakeAdapter):
            def build_response(
                self,
                stdout_text: str,
                stderr_text: str,
                return_code: int,
                output_file: Path | None,
            ) -> str:
                del stdout_text, output_file
                return f"[stderr]\n{stderr_text}\ncode={return_code}"

        manager = SessionManager(
            providers={"fake": ProviderSpec(adapter=_FailingAdapter())},
            idle_timeout_seconds=5,
            output_callback=output_callback,
        )
        record = await manager.start_session(9, "fake")
        process = _FakeProcess(stderr=b"boom", returncode=1)

        with patch(
            "llm_tg_bot.session.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            await manager._run_request(record, "prompt")

        self.assertEqual(
            outputs,
            [(9, OutgoingMessage("[stderr]\nboom\ncode=1"))],
        )
