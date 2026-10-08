from __future__ import annotations

import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    command: tuple[str, ...]
    output_file: Path | None = None


@dataclass(frozen=True, slots=True)
class RequestContext:
    is_followup: bool
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    session_id: str | None = None


class ProviderAdapter(ABC):
    name: str
    executable: str

    @abstractmethod
    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        cwd: Path | None = None,
    ) -> PreparedRequest:
        raise NotImplementedError

    @abstractmethod
    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
    ) -> ProviderResponse:
        raise NotImplementedError


class CodexAdapter(ProviderAdapter):
    name = "codex"
    executable = "codex"

    def __init__(self, *, skip_git_repo_check: bool = True) -> None:
        self._skip_git_repo_check = skip_git_repo_check

    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        cwd: Path | None = None,
    ) -> PreparedRequest:
        fd, temp_path = tempfile.mkstemp(prefix="llm-tg-bot-codex-", suffix=".txt")
        os.close(fd)
        output_file = Path(temp_path)

        command = [
            self.executable,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if context.is_followup:
            command.append("resume")
        if self._skip_git_repo_check:
            command.append("--skip-git-repo-check")
        command.extend(self._request_tail(prompt, output_file, context.is_followup))
        return PreparedRequest(command=tuple(command), output_file=output_file)

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
    ) -> ProviderResponse:
        primary_text = _read_output_file(output_file) or _clean_output_text(stdout_text)
        return ProviderResponse(
            text=_build_response(
                primary_text,
                _add_codex_repo_check_hint(stderr_text),
                return_code,
            )
        )

    @staticmethod
    def _request_tail(prompt: str, output_file: Path, resume: bool) -> list[str]:
        common = ["--output-last-message", str(output_file), prompt]
        if resume:
            return ["--last", *common]
        return ["--color", "never", *common]


class OpencodeAdapter(ProviderAdapter):
    name = "opencode"
    executable = "opencode"

    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        cwd: Path | None = None,
    ) -> PreparedRequest:
        command: list[str] = [
            self.executable,
            "run",
            "--format",
            "json",
            "--dangerously-skip-permissions",
        ]
        if context.session_id:
            command.extend(["--session", context.session_id])
        elif context.is_followup:
            command.append("--continue")
        if cwd:
            command.extend(["--dir", str(cwd)])
        command.append(prompt)
        return PreparedRequest(command=tuple(command))

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
    ) -> ProviderResponse:
        del output_file
        session_id, primary_text = _parse_opencode_json_stream(stdout_text)
        if return_code != 0 and not primary_text:
            primary_text = _clean_output_text(stdout_text)
        return ProviderResponse(
            text=_build_response(primary_text, stderr_text, return_code),
            session_id=session_id,
        )


class PiAdapter(ProviderAdapter):
    name = "pi"
    executable = "pi"

    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        cwd: Path | None = None,
    ) -> PreparedRequest:
        del cwd
        command: list[str] = [
            self.executable,
            "--print",
            "--mode",
            "json",
            "--approve",
        ]
        if context.session_id:
            command.extend(["--session", context.session_id])
        elif context.is_followup:
            command.append("--continue")
        command.append(prompt)
        return PreparedRequest(command=tuple(command))

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
    ) -> ProviderResponse:
        del output_file
        session_id, primary_text = _parse_pi_json_stream(stdout_text)
        if return_code != 0 and not primary_text:
            primary_text = _clean_output_text(stdout_text)
        return ProviderResponse(
            text=_build_response(primary_text, stderr_text, return_code),
            session_id=session_id,
        )


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    adapter: ProviderAdapter
    cwd: Path | None = None

    @property
    def name(self) -> str:
        return self.adapter.name

    @property
    def executable(self) -> str:
        return self.adapter.executable

    def prepare_request(self, prompt: str, context: RequestContext) -> PreparedRequest:
        return self.adapter.prepare_request(prompt, context, cwd=self.cwd)

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
    ) -> ProviderResponse:
        return self.adapter.build_response(
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            return_code=return_code,
            output_file=output_file,
        )


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CODEX_REPO_CHECK_ERROR = (
    "Not inside a trusted directory and --skip-git-repo-check was not specified."
)
_IGNORED_STDERR_PATTERNS = (
    "WARNING: proceeding, even though we could not update PATH",
)


def builtin_adapters(
    *, codex_skip_git_repo_check: bool = True
) -> tuple[ProviderAdapter, ...]:
    return (
        CodexAdapter(skip_git_repo_check=codex_skip_git_repo_check),
        OpencodeAdapter(),
        PiAdapter(),
    )


def _build_response(primary_text: str, stderr_text: str, return_code: int) -> str:
    parts: list[str] = []
    if primary_text:
        parts.append(primary_text)

    stderr_clean = _clean_stderr_text(stderr_text)
    if return_code != 0 and stderr_clean:
        parts.append(f"[stderr]\n{stderr_clean}")

    if return_code != 0 and not parts:
        parts.append(f"[request failed: exit code {return_code}]")

    return "\n\n".join(parts).strip()


def _read_output_file(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return _clean_output_text(path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return ""


def _clean_output_text(text: str) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(lines).strip()


def _clean_stderr_text(text: str) -> str:
    cleaned = _clean_output_text(text)
    if not cleaned:
        return ""

    lines = [
        line
        for line in cleaned.splitlines()
        if not any(pattern in line for pattern in _IGNORED_STDERR_PATTERNS)
    ]
    return "\n".join(lines).strip()


def _add_codex_repo_check_hint(text: str) -> str:
    cleaned = _clean_stderr_text(text)
    if _CODEX_REPO_CHECK_ERROR not in cleaned:
        return text

    return (
        f"{cleaned}\n\n"
        "Hint: set WORKDIR to the project directory Codex should use. If you "
        "disabled the default bypass, set CODEX_SKIP_GIT_REPO_CHECK=1 to allow "
        "running outside a trusted Git worktree."
    )


def _parse_opencode_json_stream(stdout_text: str) -> tuple[str | None, str]:
    """Parse an opencode `run --format json` NDJSON event stream.

    Returns ``(session_id, concatenated_text)``. ``session_id`` is ``None``
    when no event carried one. Text is the concatenation of every
    ``{"type": "text"}`` part's ``text`` field, preserving order.
    """
    session_id: str | None = None
    text_parts: list[str] = []

    for raw_line in stdout_text.splitlines():
        cleaned_line = raw_line.strip()
        if not cleaned_line:
            continue
        try:
            event = json.loads(cleaned_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        event_session = event.get("sessionID")
        if (
            session_id is None
            and isinstance(event_session, str)
            and event_session
        ):
            session_id = event_session

        if event.get("type") != "text":
            continue
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        part_text = part.get("text")
        if isinstance(part_text, str) and part_text:
            text_parts.append(part_text)

    primary_text = "\n".join(text_parts).strip()
    return session_id, primary_text


def _parse_pi_json_stream(stdout_text: str) -> tuple[str | None, str]:
    """Parse a pi `--mode json` NDJSON event stream.

    Returns ``(session_id, last_assistant_text)``. ``session_id`` comes from
    the session header; text is the last non-empty assistant `message_end`.
    """
    session_id: str | None = None
    primary_text = ""

    for raw_line in stdout_text.splitlines():
        cleaned_line = raw_line.strip()
        if not cleaned_line:
            continue
        try:
            event = json.loads(cleaned_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        if session_id is None and event.get("type") == "session":
            event_id = event.get("id")
            if isinstance(event_id, str) and event_id:
                session_id = event_id

        if event.get("type") != "message_end":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        text = "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ).strip()
        if text:
            primary_text = text

    return session_id, primary_text


def get_provider_spec(
    providers: dict[str, ProviderSpec], provider_name: str
) -> ProviderSpec:
    """Get a provider spec by name, raising ValueError if not found."""
    try:
        return providers[provider_name]
    except KeyError as exc:
        available = ", ".join(sorted(providers))
        raise ValueError(
            f"Unknown provider {provider_name!r}. Available: {available}"
        ) from exc
