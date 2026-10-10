from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from llm_tg_bot.providers import PiProvider
from llm_tg_bot.rendering import OutgoingMessage, RenderMode

logger = logging.getLogger(__name__)

ProcessTracker = Callable[[asyncio.subprocess.Process | None], None]


@dataclass(frozen=True, slots=True)
class RequestExecutionResult:
    completed_at: float
    message: OutgoingMessage | None
    succeeded: bool


async def run_provider_request(
    provider: PiProvider,
    prompt: str,
    *,
    session_id: str,
    process_tracker: ProcessTracker | None = None,
) -> RequestExecutionResult:
    process: asyncio.subprocess.Process | None = None
    try:
        command = provider.prepare_request(prompt, session_id)
        logger.info("Running provider=%s command=%s", provider.name, command)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(provider.cwd) if provider.cwd else None,
            env=_child_environment(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process_tracker is not None:
            process_tracker(process)

        stdout_bytes, stderr_bytes = await process.communicate()
        return_code = process.returncode
        assert return_code is not None
        response = provider.build_response(
            stdout_text=stdout_bytes.decode("utf-8", errors="replace"),
            stderr_text=stderr_bytes.decode("utf-8", errors="replace"),
            return_code=return_code,
        )
        return RequestExecutionResult(
            completed_at=time.monotonic(),
            message=_response_message(response, return_code),
            succeeded=return_code == 0,
        )
    except asyncio.CancelledError:
        if process and process.returncode is None:
            await terminate_process(process)
        raise
    finally:
        if process_tracker is not None:
            process_tracker(None)


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def _response_message(response: str, return_code: int) -> OutgoingMessage | None:
    if response:
        render_mode = RenderMode.MARKDOWN if return_code == 0 else RenderMode.PLAIN
        return OutgoingMessage(response, render_mode=render_mode)
    if return_code != 0:
        return OutgoingMessage(f"[request failed: exit code {return_code}]\n")
    return None


def _child_environment() -> dict[str, str]:
    env = dict(os.environ)
    term = env.get("TERM", "").strip().lower()
    if not term or term == "dumb":
        env["TERM"] = "xterm-256color"
    env.setdefault("COLORTERM", "truecolor")
    return env
