from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html import escape

import mistune


class RenderMode(str, Enum):
    PLAIN = "plain"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    text: str
    render_mode: RenderMode = RenderMode.PLAIN


@dataclass(frozen=True, slots=True)
class RenderedChunk:
    text: str
    plain_text: str
    parse_mode: str | None = None


def build_message_chunks(message: OutgoingMessage, limit: int) -> list[RenderedChunk]:
    if not message.text:
        return []
    if message.render_mode == RenderMode.MARKDOWN:
        return _render_markdown_chunks(message.text, limit)
    return [
        RenderedChunk(text=chunk, plain_text=chunk)
        for chunk in split_plain_text(message.text, limit)
    ]


def split_plain_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            newline_index = text.rfind("\n", start, end)
            if newline_index > start:
                end = newline_index + 1
        chunks.append(text[start:end])
        start = end

    return chunks


def _render_markdown_chunks(text: str, limit: int) -> list[RenderedChunk]:
    html = _renderer(text)
    plain = _plain_renderer(text)

    if len(html) <= limit:
        return [RenderedChunk(text=html, plain_text=plain, parse_mode="HTML")]

    chunks: list[RenderedChunk] = []
    for raw_chunk in split_plain_text(plain, limit):
        chunk_html = _renderer(raw_chunk)
        if len(chunk_html) <= limit:
            chunks.append(
                RenderedChunk(text=chunk_html, plain_text=raw_chunk, parse_mode="HTML")
            )
        else:
            chunks.append(RenderedChunk(text=raw_chunk, plain_text=raw_chunk))

    return chunks


class _TelegramHTMLRenderer(mistune.HTMLRenderer):
    def paragraph(self, text: str) -> str:
        return f"{text}\n"

    def text(self, text: str) -> str:
        return escape(text, quote=False)

    def heading(self, text: str, level: int, **attrs) -> str:
        return f"<b>{text}</b>\n"

    def block_code(self, code: str, info: str | None = None) -> str:
        return f"<pre>{escape(code, quote=False)}</pre>\n"

    def codespan(self, text: str) -> str:
        return f"<code>{escape(text, quote=False)}</code>"

    def link(self, text: str, url: str, title: str | None = None) -> str:
        href = escape(url, quote=True)
        return f'<a href="{href}">{text}</a>'

    def emphasis(self, text: str) -> str:
        return f"<i>{text}</i>"

    def strong(self, text: str) -> str:
        return f"<b>{text}</b>"

    def strikethrough(self, text: str) -> str:
        return f"<s>{text}</s>"

    def list(self, text: str, ordered: bool, **attrs) -> str:
        return f"{text}\n"

    def list_item(self, text: str) -> str:
        return f"• {text}\n"

    def block_quote(self, text: str) -> str:
        lines = text.strip().split("\n")
        quoted = "\n".join(f"&gt; {line}" for line in lines)
        return f"{quoted}\n"

    def thematic_break(self) -> str:
        return "────────\n"

    def image(self, text: str, url: str, title: str | None = None) -> str:
        return self.link(text, url, title)


_renderer = mistune.create_markdown(
    renderer=_TelegramHTMLRenderer(escape=False),
    plugins=["strikethrough"],
)
_plain_renderer = mistune.create_markdown(plugins=None)
