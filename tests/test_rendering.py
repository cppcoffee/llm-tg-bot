from __future__ import annotations

import unittest

from llm_tg_bot.rendering import OutgoingMessage, RenderMode, build_message_chunks


class RenderingTests(unittest.TestCase):
    def test_plain_text_at_telegram_limit_stays_in_one_chunk(self) -> None:
        text = "a" * 4096

        chunks = build_message_chunks(OutgoingMessage(text), 4096)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, text)
        self.assertEqual(len(chunks[0].text), 4096)

    def test_plain_text_over_telegram_limit_is_split(self) -> None:
        text = "a" * 4097

        chunks = build_message_chunks(OutgoingMessage(text), 4096)

        self.assertEqual([len(chunk.text) for chunk in chunks], [4096, 1])

    def test_markdown_chunks_respect_telegram_limit(self) -> None:
        text = "## Heading\n\n" + ("word " * 1200)

        chunks = build_message_chunks(
            OutgoingMessage(text, render_mode=RenderMode.MARKDOWN),
            4096,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 4096 for chunk in chunks))
