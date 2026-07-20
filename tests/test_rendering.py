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

    def test_blockquote_rendering(self) -> None:
        text = "> This is a quote.\n> Spanning multiple lines."
        chunks = build_message_chunks(
            OutgoingMessage(text, render_mode=RenderMode.MARKDOWN),
            4096,
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "<blockquote>This is a quote.\nSpanning multiple lines.</blockquote>\n")

    def test_large_code_block_is_split_into_html_chunks(self) -> None:
        code = "print('hi')\n" * 600  # ~7800 chars, well over the limit
        md = f"Here is the code:\n\n```python\n{code}```\n\nDone."
        chunks = build_message_chunks(
            OutgoingMessage(md, render_mode=RenderMode.MARKDOWN),
            4096,
        )
        self.assertGreaterEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 4096)
            # Every chunk stays HTML-formatted, no plain-text fallback.
            self.assertEqual(chunk.parse_mode, "HTML")
        # Code chunks carry the python fence.
        code_chunks = [c for c in chunks if "<pre>" in c.text]
        self.assertGreaterEqual(len(code_chunks), 2)

    def test_markdown_split_keeps_blocks_intact(self) -> None:
        para_a = "alpha " * 500  # ~3000 chars
        para_b = "beta " * 500   # ~3000 chars
        md = f"{para_a}\n\n{para_b}"
        chunks = build_message_chunks(
            OutgoingMessage(md, render_mode=RenderMode.MARKDOWN),
            4096,
        )
        self.assertGreaterEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 4096)
            # Each chunk remains valid HTML (no broken split mid-token).
            self.assertEqual(chunk.parse_mode, "HTML")
