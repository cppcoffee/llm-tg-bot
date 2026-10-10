from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from llm_tg_bot.providers import PiProvider

_PROBE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model: str
    thinking_level: str | None

    def describe(self) -> str:
        lines = [f"Model: {self.model}"]
        if self.thinking_level:
            lines.append(f"Thinking: {self.thinking_level}")
        return "\n".join(lines)


async def probe_model_info(provider: PiProvider) -> ModelInfo | None:
    """Ask a short-lived `pi --mode rpc` process what model/thinking it would use.

    Uses the same working directory and project trust as real requests. Returns
    None when pi is unavailable, slow, or reports no model.
    """
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            provider.executable,
            "--mode",
            "rpc",
            "--no-session",
            "--approve",
            cwd=str(provider.cwd) if provider.cwd else None,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdin, stdout = process.stdin, process.stdout
        if stdin is None or stdout is None:
            return None

        stdin.write(b'{"id":"probe","type":"get_state"}\n')
        await stdin.drain()
        stdin.close()
        return await asyncio.wait_for(
            _read_model_info(stdout), timeout=_PROBE_TIMEOUT_SECONDS
        )
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


async def _read_model_info(stdout: asyncio.StreamReader) -> ModelInfo | None:
    async for raw_line in stdout:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(record, dict)
            and record.get("type") == "response"
            and record.get("command") == "get_state"
        ):
            return _model_info_from_response(record)
    return None


def _model_info_from_response(record: dict) -> ModelInfo | None:
    data = record.get("data")
    if not isinstance(data, dict):
        return None

    model = data.get("model")
    if not isinstance(model, dict):
        return None
    name = model.get("name") or model.get("id")
    if not isinstance(name, str) or not name:
        return None
    provider = model.get("provider")
    label = f"{name} ({provider})" if isinstance(provider, str) and provider else name

    thinking_level = data.get("thinkingLevel")
    return ModelInfo(
        model=label,
        thinking_level=thinking_level if isinstance(thinking_level, str) else None,
    )
