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
    raw_text: str | None = None


class ProviderAdapter(ABC):
    name: str
    executable: str

    @abstractmethod
    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        skip_git_repo_check: bool = False,
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
        *,
        prompt: str | None = None,
        previous_response_text: str | None = None,
    ) -> ProviderResponse:
        raise NotImplementedError


class JsonAdapter(ProviderAdapter):
    text_field: str

    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        skip_git_repo_check: bool = False,
        cwd: Path | None = None,
    ) -> PreparedRequest:
        del skip_git_repo_check
        command = [self.executable]
        self._add_base_args(command)

        if context.session_id:
            command.extend(["--resume", context.session_id])
        elif context.is_followup:
            self._add_followup_args(command)

        command.extend(["-p", prompt] if "-p" not in command else [prompt])
        if "--output-format" not in command:
            command.extend(["--output-format", "json"])

        return PreparedRequest(command=tuple(command))

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
        *,
        prompt: str | None = None,
        previous_response_text: str | None = None,
    ) -> ProviderResponse:
        del output_file, prompt, previous_response_text
        parsed = self._parse_json(stdout_text) if return_code == 0 else None
        primary_text = (
            parsed.text if parsed is not None else _clean_output_text(stdout_text)
        )
        return ProviderResponse(
            text=_build_response(primary_text, stderr_text, return_code),
            session_id=parsed.session_id if parsed is not None else None,
        )

    def _add_base_args(self, command: list[str]) -> None:
        pass

    def _add_followup_args(self, command: list[str]) -> None:
        pass

    def _parse_json(self, stdout_text: str) -> _ProviderJsonResult | None:
        cleaned = _clean_output_text(stdout_text)
        if not cleaned:
            return None

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        result = payload.get(self.text_field)
        text = result if isinstance(result, str) else cleaned
        session_id = payload.get("session_id")
        return _ProviderJsonResult(
            text=_clean_output_text(text),
            session_id=session_id
            if isinstance(session_id, str) and session_id
            else None,
        )


class ClaudeAdapter(JsonAdapter):
    name = "claude"
    executable = "claude"
    text_field = "result"

    def _add_base_args(self, command: list[str]) -> None:
        command.extend(
            ["-p", "--output-format", "json", "--permission-mode", "bypassPermissions"]
        )

    def _add_followup_args(self, command: list[str]) -> None:
        command.append("--continue")


class GeminiAdapter(JsonAdapter):
    name = "gemini"
    executable = "gemini"
    text_field = "response"

    def _add_base_args(self, command: list[str]) -> None:
        command.extend(["--approval-mode", "yolo"])

    def _add_followup_args(self, command: list[str]) -> None:
        command.extend(["--resume", "latest"])


class AgyAdapter(ProviderAdapter):
    name = "agy"
    executable = "agy"

    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        skip_git_repo_check: bool = False,
        cwd: Path | None = None,
    ) -> PreparedRequest:
        del skip_git_repo_check
        fd, temp_path = tempfile.mkstemp(prefix="llm-tg-bot-agy-", suffix=".log")
        os.close(fd)
        log_file = Path(temp_path)

        command = [
            self.executable,
            "-p",
            prompt,
            "--dangerously-skip-permissions",
            "--log-file",
            str(log_file),
            "--print-timeout",
            "1h",
            "--add-dir",
            str(cwd.resolve()) if cwd else str(Path.cwd().resolve()),
        ]

        if context.session_id:
            command.extend(["--conversation", context.session_id])
        elif context.is_followup:
            command.append("--continue")

        return PreparedRequest(command=tuple(command), output_file=log_file)

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
        *,
        prompt: str | None = None,
        previous_response_text: str | None = None,
    ) -> ProviderResponse:
        session_id = None
        if output_file and output_file.exists():
            try:
                log_content = output_file.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"Created conversation ([a-zA-Z0-9\-]+)", log_content)
                if match:
                    session_id = match.group(1)
                else:
                    match = re.search(r"conversation=([a-zA-Z0-9\-]+)", log_content)
                    if match:
                        session_id = match.group(1)
            except Exception:
                pass

        raw_stdout = _clean_output_text(stdout_text)
        primary_text = raw_stdout
        if return_code == 0:
            extracted_text = None
            if session_id:
                try:
                    app_data_dir = Path("~/.gemini/antigravity-cli").expanduser()
                    transcript_path = (
                        app_data_dir
                        / "brain"
                        / session_id
                        / ".system_generated"
                        / "logs"
                        / "transcript.jsonl"
                    )
                    if transcript_path.exists():
                        lines = transcript_path.read_text(encoding="utf-8").splitlines()
                        for line in reversed(lines):
                            try:
                                data = json.loads(line)
                                if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                                    extracted_text = data["content"]
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

            if extracted_text is not None:
                primary_text = _clean_output_text(extracted_text)
            else:
                primary_text = _extract_agy_latest_reply(
                    primary_text,
                    prompt=prompt,
                    previous_response_text=previous_response_text,
                )
        return ProviderResponse(
            text=_build_response(primary_text, stderr_text, return_code),
            session_id=session_id,
            raw_text=raw_stdout,
        )


class CodexAdapter(ProviderAdapter):
    name = "codex"
    executable = "codex"

    def prepare_request(
        self,
        prompt: str,
        context: RequestContext,
        *,
        skip_git_repo_check: bool = False,
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
        if skip_git_repo_check:
            command.append("--skip-git-repo-check")
        command.extend(self._request_tail(prompt, output_file, context.is_followup))
        return PreparedRequest(command=tuple(command), output_file=output_file)

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
        *,
        prompt: str | None = None,
        previous_response_text: str | None = None,
    ) -> ProviderResponse:
        del prompt, previous_response_text
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


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    adapter: ProviderAdapter
    cwd: Path | None = None
    skip_git_repo_check: bool = False

    @property
    def name(self) -> str:
        return self.adapter.name

    @property
    def executable(self) -> str:
        return self.adapter.executable

    @property
    def display_command(self) -> str:
        return self.executable

    def prepare_request(self, prompt: str, context: RequestContext) -> PreparedRequest:
        return self.adapter.prepare_request(
            prompt,
            context,
            skip_git_repo_check=self.skip_git_repo_check,
            cwd=self.cwd,  # Pass the spec cwd
        )

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
        output_file: Path | None,
        *,
        prompt: str | None = None,
        previous_response_text: str | None = None,
    ) -> ProviderResponse:
        return self.adapter.build_response(
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            return_code=return_code,
            output_file=output_file,
            prompt=prompt,
            previous_response_text=previous_response_text,
        )


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CODEX_REPO_CHECK_ERROR = (
    "Not inside a trusted directory and --skip-git-repo-check was not specified."
)
_IGNORED_STDERR_PATTERNS = (
    "WARNING: proceeding, even though we could not update PATH",
)
_BUILTIN_ADAPTERS: tuple[ProviderAdapter, ...] = (
    CodexAdapter(),
    ClaudeAdapter(),
    GeminiAdapter(),
    AgyAdapter(),
)


def builtin_adapters() -> tuple[ProviderAdapter, ...]:
    return _BUILTIN_ADAPTERS


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


def _extract_agy_latest_reply(
    transcript_text: str,
    *,
    prompt: str | None,
    previous_response_text: str | None,
) -> str:
    current = transcript_text.strip()
    if not current:
        return ""

    previous = (previous_response_text or "").strip()
    if previous and current.startswith(previous):
        suffix = current[len(previous) :].lstrip("\n")
        if suffix:
            return suffix.strip()

    if prompt:
        prompt_clean = prompt.strip()
        if prompt_clean:
            prompt_index = current.rfind(prompt_clean)
            if prompt_index != -1:
                suffix = current[prompt_index + len(prompt_clean) :].lstrip("\n")
                if suffix:
                    return suffix.strip()

    return current


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


@dataclass(frozen=True, slots=True)
class _ProviderJsonResult:
    text: str
    session_id: str | None


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
