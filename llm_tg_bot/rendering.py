from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from html import escape


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


@dataclass(frozen=True, slots=True)
class _MarkdownBlock:
    kind: str
    text: str


_FENCED_CODE_RE = re.compile(r"^```")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_LIST_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
_HORIZONTAL_RULE_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_TASK_LIST_RE = re.compile(r"^\[( |x|X)\]\s+(.*)$")


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
    blocks = _parse_markdown_blocks(text)
    if not blocks:
        return [
            RenderedChunk(text=chunk, plain_text=chunk)
            for chunk in split_plain_text(text, limit)
        ]

    chunks: list[RenderedChunk] = []
    current_html = ""
    current_plain = ""

    for block in blocks:
        for piece in _split_block_to_fit(block, limit):
            html = _render_block(piece)
            plain = piece.text
            if current_html and len(current_html) + 2 + len(html) > limit:
                chunks.append(
                    RenderedChunk(
                        text=current_html,
                        plain_text=current_plain,
                        parse_mode="HTML",
                    )
                )
                current_html = html
                current_plain = plain
                continue

            if current_html:
                current_html = f"{current_html}\n\n{html}"
                current_plain = f"{current_plain}\n\n{plain}"
            else:
                current_html = html
                current_plain = plain

    if current_html:
        chunks.append(
            RenderedChunk(
                text=current_html,
                plain_text=current_plain,
                parse_mode="HTML",
            )
        )

    return chunks


def _parse_markdown_blocks(text: str) -> list[_MarkdownBlock]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    blocks: list[_MarkdownBlock] = []
    text_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCED_CODE_RE.match(line.strip()):
            _flush_text_blocks(text_lines, blocks)
            text_lines.clear()
            index += 1

            code_lines: list[str] = []
            while index < len(lines):
                if _FENCED_CODE_RE.match(lines[index].strip()):
                    index += 1
                    break
                code_lines.append(lines[index])
                index += 1

            blocks.append(_MarkdownBlock(kind="code", text="\n".join(code_lines)))
            continue

        text_lines.append(line)
        index += 1

    _flush_text_blocks(text_lines, blocks)
    return blocks


def _flush_text_blocks(lines: list[str], blocks: list[_MarkdownBlock]) -> None:
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
            continue

        if current:
            blocks.append(_MarkdownBlock(kind="text", text="\n".join(current)))
            current.clear()

    if current:
        blocks.append(_MarkdownBlock(kind="text", text="\n".join(current)))


def _split_block_to_fit(block: _MarkdownBlock, limit: int) -> list[_MarkdownBlock]:
    if len(_render_block(block)) <= limit:
        return [block]

    if block.kind == "code":
        return _split_code_block(block, limit)
    return _split_text_block(block, limit)


def _split_code_block(block: _MarkdownBlock, limit: int) -> list[_MarkdownBlock]:
    lines = block.text.splitlines()
    if not lines:
        return [block]

    pieces: list[_MarkdownBlock] = []
    current: list[str] = []
    for line in lines:
        candidate = current + [line]
        candidate_block = _MarkdownBlock(kind="code", text="\n".join(candidate))
        if current and len(_render_block(candidate_block)) > limit:
            pieces.append(_MarkdownBlock(kind="code", text="\n".join(current)))
            current = [line]
            continue
        current = candidate

    if current:
        pieces.append(_MarkdownBlock(kind="code", text="\n".join(current)))

    expanded: list[_MarkdownBlock] = []
    for piece in pieces:
        if len(_render_block(piece)) <= limit:
            expanded.append(piece)
            continue
        expanded.extend(_split_long_code_text(piece.text, limit))
    return expanded


def _split_long_code_text(text: str, limit: int) -> list[_MarkdownBlock]:
    pieces: list[_MarkdownBlock] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max(1, limit - len("<pre></pre>")))
        piece = text[start:end]
        while len(_render_code_block(piece)) > limit and len(piece) > 1:
            end -= 1
            piece = text[start:end]
        pieces.append(_MarkdownBlock(kind="code", text=piece))
        start = end
    return pieces


def _split_text_block(block: _MarkdownBlock, limit: int) -> list[_MarkdownBlock]:
    lines = block.text.splitlines()
    if len(lines) <= 1:
        return _split_long_text_line(block.text, limit)

    pieces: list[_MarkdownBlock] = []
    current: list[str] = []
    for line in lines:
        candidate = current + [line]
        candidate_block = _MarkdownBlock(kind="text", text="\n".join(candidate))
        if current and len(_render_block(candidate_block)) > limit:
            pieces.append(_MarkdownBlock(kind="text", text="\n".join(current)))
            current = [line]
            continue
        current = candidate

    if current:
        pieces.append(_MarkdownBlock(kind="text", text="\n".join(current)))

    expanded: list[_MarkdownBlock] = []
    for piece in pieces:
        if len(_render_block(piece)) <= limit:
            expanded.append(piece)
            continue
        expanded.extend(_split_long_text_line(piece.text, limit))
    return expanded


def _split_long_text_line(text: str, limit: int) -> list[_MarkdownBlock]:
    stripped = text.strip()
    if not stripped:
        return [_MarkdownBlock(kind="text", text=text)]

    words = stripped.split()
    pieces: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if current and len(_render_text_block(candidate)) > limit:
            pieces.append(current)
            current = word
            continue
        current = candidate

    if current:
        pieces.append(current)

    if all(len(_render_text_block(piece)) <= limit for piece in pieces):
        return [_MarkdownBlock(kind="text", text=piece) for piece in pieces]

    return _split_long_text_by_characters(stripped, limit)


def _split_long_text_by_characters(text: str, limit: int) -> list[_MarkdownBlock]:
    pieces: list[_MarkdownBlock] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        piece = text[start:end]
        while len(_render_text_block(piece)) > limit and len(piece) > 1:
            end -= 1
            piece = text[start:end]
        pieces.append(_MarkdownBlock(kind="text", text=piece.strip()))
        start = end
    return pieces


def _render_block(block: _MarkdownBlock) -> str:
    if block.kind == "code":
        return _render_code_block(block.text)
    return _render_text_block(block.text)


def _render_code_block(text: str) -> str:
    return f"<pre>{escape(text)}</pre>"


def _render_text_block(text: str) -> str:
    rendered_lines = [_render_text_line(line) for line in text.splitlines()]
    return "\n".join(rendered_lines).strip()


def _render_text_line(line: str) -> str:
    if not line.strip():
        return ""

    if _HORIZONTAL_RULE_RE.match(line):
        return "────────"

    heading_match = _HEADING_RE.match(line)
    if heading_match:
        content = re.sub(r"\s+#+\s*$", "", heading_match.group(2)).strip()
        return f"<b>{_render_inline(content)}</b>"

    blockquote_match = _BLOCKQUOTE_RE.match(line)
    if blockquote_match:
        return f"&gt; {_render_inline(blockquote_match.group(1).strip())}"

    bullet_match = _BULLET_RE.match(line)
    if bullet_match:
        return _render_list_line("• ", bullet_match.group(1), bullet_match.group(2))

    numbered_match = _NUMBERED_LIST_RE.match(line)
    if numbered_match:
        prefix = f"{numbered_match.group(2)}. "
        return _render_list_line(prefix, numbered_match.group(1), numbered_match.group(3))

    return _render_inline(line.strip())


def _render_list_line(prefix: str, indent: str, content: str) -> str:
    nested_prefix = "  " * min(4, len(indent) // 2)
    task_match = _TASK_LIST_RE.match(content)
    if task_match:
        checkbox = "☑ " if task_match.group(1).lower() == "x" else "☐ "
        content = f"{checkbox}{task_match.group(2)}"
    return f"{nested_prefix}{prefix}{_render_inline(content.strip())}"


def _render_inline(text: str) -> str:
    parts: list[str] = []
    index = 0

    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            parts.append(escape(text[index + 1]))
            index += 2
            continue

        if text[index] == "`":
            closing = text.find("`", index + 1)
            if closing != -1:
                parts.append(f"<code>{escape(text[index + 1:closing])}</code>")
                index = closing + 1
                continue

        if text[index] == "[":
            link = _match_link(text, index)
            if link is not None:
                label, url, next_index = link
                parts.append(
                    f'<a href="{escape(url, quote=True)}">{_render_inline(label)}</a>'
                )
                index = next_index
                continue

        for marker, tag in (("**", "b"), ("__", "b"), ("~~", "s"), ("*", "i")):
            if not text.startswith(marker, index):
                continue
            closing = _find_closing_marker(text, marker, index + len(marker))
            if closing == -1:
                continue
            inner = text[index + len(marker) : closing]
            if not inner or "\n" in inner:
                continue
            parts.append(f"<{tag}>{_render_inline(inner)}</{tag}>")
            index = closing + len(marker)
            break
        else:
            next_index = _next_special_index(text, index)
            if next_index == index:
                next_index += 1
            parts.append(escape(text[index:next_index]))
            index = next_index

    return "".join(parts)


def _match_link(text: str, start: int) -> tuple[str, str, int] | None:
    divider = text.find("](", start + 1)
    if divider == -1:
        return None

    label = text[start + 1 : divider]
    if not label:
        return None

    index = divider + 2
    depth = 1
    while index < len(text):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                url = text[divider + 2 : index].strip()
                if not url:
                    return None
                return label, url, index + 1
        index += 1
    return None


def _find_closing_marker(text: str, marker: str, start: int) -> int:
    index = start
    while True:
        index = text.find(marker, index)
        if index == -1:
            return -1
        if marker != "*" or index == start or text[index - 1].isspace() is False:
            return index
        index += len(marker)


def _next_special_index(text: str, start: int) -> int:
    specials = [
        text.find(char, start)
        for char in ("\\", "`", "[", "*", "_", "~")
        if text.find(char, start) != -1
    ]
    if not specials:
        return len(text)
    return min(specials)
