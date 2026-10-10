from __future__ import annotations

import re
from pathlib import Path


class PiProvider:
    """One headless `pi --print` request; the caller owns the session id."""

    name = "pi"
    executable = "pi"

    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = cwd

    def prepare_request(self, prompt: str, session_id: str) -> tuple[str, ...]:
        return (
            self.executable,
            "--print",
            "--approve",
            "--session-id",
            session_id,
            prompt,
        )

    def build_response(
        self,
        stdout_text: str,
        stderr_text: str,
        return_code: int,
    ) -> str:
        return _build_response(_clean_output_text(stdout_text), stderr_text, return_code)


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _build_response(primary_text: str, stderr_text: str, return_code: int) -> str:
    parts: list[str] = []
    if primary_text:
        parts.append(primary_text)

    stderr_clean = _clean_output_text(stderr_text)
    if return_code != 0 and stderr_clean:
        parts.append(f"[stderr]\n{stderr_clean}")

    if return_code != 0 and not parts:
        parts.append(f"[request failed: exit code {return_code}]")

    return "\n\n".join(parts).strip()


def _clean_output_text(text: str) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(lines).strip()
