from __future__ import annotations

import re
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


def build_message_chunks(
    message: OutgoingMessage,
    limit: int,
) -> list[RenderedChunk]:
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


def _split_markdown_blocks(md: str) -> list[str]:
    """Split markdown into top-level blocks.

    Blank lines outside fenced code blocks act as block separators.
    Fenced code blocks are always kept intact.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    fence_indent = 0

    for line in md.splitlines(keepends=True):
        stripped = line.lstrip()
        is_fence = stripped.startswith("```") or stripped.startswith("~~~")
        if is_fence:
            indent = len(line) - len(stripped)
            if not in_fence:
                in_fence = True
                fence_indent = indent
            elif indent == fence_indent and stripped[:3] == ("```" if "```" in line else "~~~"):
                in_fence = False
            current.append(line)
            continue

        if not in_fence and line.strip() == "":
            if current:
                blocks.append("".join(current))
                current = []
            continue

        current.append(line)

    if current:
        blocks.append("".join(current))
    return blocks


def _is_code_block(block_md: str) -> tuple[bool, str | None, str]:
    """Return (is_code, language, code) for a fenced code block."""
    match = _FENCE_RE.match(block_md)
    if not match:
        return False, None, ""
    lang = match.group(1) or None
    if lang:
        lang = lang.lower()
    code = match.group(2)
    return True, lang, code


def _render_markdown_chunks(text: str, limit: int) -> list[RenderedChunk]:
    full_html = _renderer(text)
    full_plain = _plain_renderer(text)
    if len(full_html) <= limit:
        return [RenderedChunk(text=full_html, plain_text=full_plain, parse_mode="HTML")]

    # Render block-by-block, packing blocks into chunks that fit within limit.
    chunks: list[RenderedChunk] = []
    buffer_html: list[str] = []
    buffer_plain: list[str] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer_len, buffer_html, buffer_plain
        if not buffer_html:
            return
        html = "".join(buffer_html)
        plain = "".join(buffer_plain)
        chunks.append(RenderedChunk(text=html, plain_text=plain, parse_mode="HTML"))
        buffer_html = []
        buffer_plain = []
        buffer_len = 0

    for block_md in _split_markdown_blocks(text):
        block_html = _renderer(block_md)
        block_plain = _plain_renderer(block_md)

        # Block fits on its own: pack into current chunk or start a new one.
        if len(block_html) <= limit:
            if buffer_html and buffer_len + len(block_html) > limit:
                flush()
            buffer_html.append(block_html)
            buffer_plain.append(block_plain)
            buffer_len += len(block_html)
            continue

        # Single block larger than limit: split by lines, each sub-chunk still
        # valid HTML (paragraphs split into multiple <p>, code fences into
        # multiple <pre>).
        flush()
        is_code, lang, code = _is_code_block(block_md)
        if is_code:
            fence = f"```{lang or ''}\n"
            for sub in _split_code_by_lines(code, fence, limit):
                sub_html = _renderer(sub)
                sub_plain = _plain_renderer(sub)
                chunks.append(
                    RenderedChunk(text=sub_html, plain_text=sub_plain, parse_mode="HTML")
                )
            continue

        # Non-code block: split its markdown by blank-line sub-blocks (paragraphs,
        # list items, etc.) and render each individually.
        sub_split = _split_markdown_blocks(block_md)
        # If the block has no internal sub-blocks (e.g. a single huge paragraph),
        # fall back to plain-text splitting so we never exceed the limit.
        if len(sub_split) <= 1:
            flush()
            for sub in split_plain_text(block_plain, limit):
                chunks.append(RenderedChunk(text=sub, plain_text=sub))
            continue
        for sub_md in sub_split:
            sub_html = _renderer(sub_md)
            if len(sub_html) <= limit:
                chunks.append(
                    RenderedChunk(
                        text=sub_html,
                        plain_text=_plain_renderer(sub_md),
                        parse_mode="HTML",
                    )
                )
            else:
                # Sub-block still too big; render and hard-split at line breaks.
                for piece in _hard_split_html(sub_html, limit):
                    chunks.append(
                        RenderedChunk(text=piece, plain_text=piece, parse_mode="HTML")
                    )

    flush()
    return chunks


def _split_code_by_lines(code: str, fence: str, limit: int) -> list[str]:
    """Yield markdown code fences whose rendered HTML fits within limit."""
    chunks: list[str] = []
    current_lines: list[str] = []
    # ponytail: estimate HTML size by rendering; cheap enough per chunk.
    def render_md() -> str:
        body = "\n".join(current_lines)
        return f"{fence}{body}\n```"

    for line in code.splitlines():
        current_lines.append(line)
        if len(_renderer(render_md())) > limit and len(current_lines) > 1:
            current_lines.pop()
            chunks.append(render_md())
            current_lines = [line]
    if current_lines:
        chunks.append(render_md())
    return chunks


def _hard_split_html(html: str, limit: int) -> list[str]:
    """Split HTML at line boundaries, never exceeding limit."""
    lines = html.splitlines(keepends=True)
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for line in lines:
        if buf and buf_len + len(line) > limit:
            chunks.append("".join(buf))
            buf = []
            buf_len = 0
        buf.append(line)
        buf_len += len(line)
    if buf:
        chunks.append("".join(buf))
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
        return f"<blockquote>{text.strip()}</blockquote>\n"

    def thematic_break(self) -> str:
        return "────────\n"

    def image(self, text: str, url: str, title: str | None = None) -> str:
        return self.link(text, url, title)


_renderer = mistune.create_markdown(
    renderer=_TelegramHTMLRenderer(escape=False),
    plugins=["strikethrough"],
)
_plain_renderer = mistune.create_markdown(plugins=None)

_FENCE_RE = re.compile(
    r"^[^\S\n]*```[^\S\n]*(\S*)\n(.*)^```[^\S\n]*\n?\Z",
    re.DOTALL | re.MULTILINE,
)
