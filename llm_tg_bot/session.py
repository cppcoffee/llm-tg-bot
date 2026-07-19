from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from llm_tg_bot.providers import ProviderSpec, RequestContext
from llm_tg_bot.rendering import OutgoingMessage
from llm_tg_bot.request_runner import run_provider_request, terminate_process
from llm_tg_bot.workdirs import format_workdir

logger = logging.getLogger(__name__)
_WHITESPACE_RE = re.compile(r"\s+")

OutputHandler = Callable[[int, OutgoingMessage], Awaitable[None]]
RequestStartedHandler = Callable[[int, asyncio.Task[None]], None]


@dataclass(slots=True)
class SessionRecord:
    chat_id: int
    provider: ProviderSpec
    last_activity: float = field(default_factory=time.monotonic)
    request_count: int = 0
    provider_session_id: str | None = None
    last_response_text: str | None = None
    active_task: asyncio.Task[None] | None = None
    active_process: asyncio.subprocess.Process | None = None
    # ponytail: queue carries message_id so edits can locate the entry.
    # None id = internally queued, not editable.
    pending_prompts: deque[tuple[int | None, str]] = field(default_factory=deque)
    active_message_id: int | None = None

    @property
    def is_busy(self) -> bool:
        return (
            self.active_task is not None and not self.active_task.done()
        ) or self.active_process is not None

    @property
    def queued_count(self) -> int:
        return len(self.pending_prompts)


@dataclass(frozen=True, slots=True)
class SendResult:
    record: SessionRecord
    queued_ahead: int


class EditOutcome:
    UPDATED_QUEUED = "updated_queued"
    RESTARTED_ACTIVE = "restarted_active"
    NOT_FOUND = "not_found"
    NO_SESSION = "no_session"


class SessionManager:
    def __init__(
        self,
        providers: dict[str, ProviderSpec],
        idle_timeout_seconds: int,
        output_callback: OutputHandler,
        request_started_callback: RequestStartedHandler | None = None,
        cleanup_callback: Callable[[int], None] | None = None,
        max_queue_size: int = 10,
        busy_timeout_seconds: int = 3600,
    ) -> None:
        self._providers = providers
        self._idle_timeout_seconds = idle_timeout_seconds
        self._busy_timeout_seconds = busy_timeout_seconds
        self._output_callback = output_callback
        self._request_started_callback = request_started_callback
        self._cleanup_callback = cleanup_callback
        self._max_queue_size = max_queue_size
        self._records: dict[int, SessionRecord] = {}
        self._chat_last_activity: dict[int, float] = {}

    def register_activity(self, chat_id: int) -> None:
        self._chat_last_activity[chat_id] = time.monotonic()

    async def start_session(
        self,
        chat_id: int,
        provider_name: str,
        *,
        cwd: Path | None = None,
    ) -> SessionRecord:
        await self.stop_session(chat_id, announce=False)
        provider = self._provider_for_session(provider_name, cwd)
        record = SessionRecord(chat_id=chat_id, provider=provider)
        self._records[chat_id] = record
        self.register_activity(chat_id)
        return record

    async def get_or_start_session(
        self,
        chat_id: int,
        provider_name: str,
    ) -> SessionRecord:
        record = self._records.get(chat_id)
        if record and record.provider.name == provider_name:
            return record
        return await self.start_session(chat_id, provider_name)

    async def send_text(
        self,
        chat_id: int,
        text: str,
        provider_name: str,
        *,
        message_id: int | None = None,
    ) -> SendResult:
        record = await self.get_or_start_session(chat_id, provider_name)
        if record.queued_count >= self._max_queue_size:
            raise RuntimeError(f"Queue full ({self._max_queue_size} prompts max)")

        queued_ahead = record.queued_count + (1 if record.is_busy else 0)
        record.pending_prompts.append((message_id, text))
        record.last_activity = time.monotonic()
        self.register_activity(chat_id)
        self._ensure_active_request(record)
        return SendResult(record=record, queued_ahead=queued_ahead)

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> str:
        """Edit a queued or in-flight prompt by its originating message_id.

        Returns one of EditOutcome.*.
        """
        self.register_activity(chat_id)
        record = self._records.get(chat_id)
        if record is None:
            return EditOutcome.NO_SESSION

        for index, (existing_id, _) in enumerate(record.pending_prompts):
            if existing_id == message_id:
                record.pending_prompts[index] = (message_id, text)
                return EditOutcome.UPDATED_QUEUED

        if record.active_message_id == message_id and record.is_busy:
            record.pending_prompts.appendleft((message_id, text))
            await self._cancel_active_request(record)
            # _cancel_active_request clears active_task; _ensure_active_request
            # runs in the cancelled task's finally block and will pick up the
            # newly prepended prompt.
            return EditOutcome.RESTARTED_ACTIVE

        return EditOutcome.NOT_FOUND

    def has_session(self, chat_id: int) -> bool:
        return chat_id in self._records

    def active_provider_name(self, chat_id: int) -> str | None:
        record = self._records.get(chat_id)
        if record is None:
            return None
        return record.provider.name

    async def interrupt(self, chat_id: int) -> bool:
        self.register_activity(chat_id)
        record = self._records.get(chat_id)
        if record is None:
            return False
        return await self._cancel_active_request(record)

    async def stop_session(self, chat_id: int, announce: bool = True) -> bool:
        self._chat_last_activity.pop(chat_id, None)
        record = self._records.pop(chat_id, None)

        if self._cleanup_callback:
            self._cleanup_callback(chat_id)

        if record is None:
            return False

        await self._cancel_active_request(record)
        record.pending_prompts.clear()

        if announce:
            await self._output_callback(chat_id, OutgoingMessage("[session stopped]\n"))

        return True

    def status_text(self, chat_id: int) -> str:
        self.register_activity(chat_id)
        record = self._records.get(chat_id)
        if record is None:
            return "No active session."

        self._ensure_active_request(record)
        idle_seconds = int(time.monotonic() - record.last_activity)
        return (
            f"Active session: {record.provider.name}\n"
            f"Command: {record.provider.display_command}\n"
            f"Workdir: {format_workdir(record.provider.cwd)}\n"
            f"Mode: headless request/response\n"
            f"Requests: {record.request_count}\n"
            f"Busy: {'yes' if record.is_busy else 'no'}\n"
            f"Queued: {record.queued_count}\n"
            f"Idle: {idle_seconds}s"
        )

    def queue_text(self, chat_id: int) -> str:
        self.register_activity(chat_id)
        record = self._records.get(chat_id)
        if record is None:
            return "No active session."

        if not record.pending_prompts:
            return "Queue is empty."

        queue_list = list(record.pending_prompts)
        lines = [f"Queue ({len(queue_list)} item(s)):"]
        for index, (_, prompt) in enumerate(queue_list, 1):
            single_line_prompt = _WHITESPACE_RE.sub(" ", prompt).strip()
            display_prompt = (
                single_line_prompt[:100] + "..."
                if len(single_line_prompt) > 100
                else single_line_prompt
            )
            lines.append(f"{index}. {display_prompt}")

        return "\n".join(lines)

    async def stop_idle_sessions(self) -> None:
        if self._idle_timeout_seconds <= 0:
            return

        now = time.monotonic()

        idle_chats = [
            chat_id
            for chat_id, last_act in self._chat_last_activity.items()
            if chat_id not in self._records
            and now - last_act >= self._idle_timeout_seconds
        ]
        for chat_id in idle_chats:
            await self.stop_session(chat_id, announce=False)

        stale_chat_ids: list[tuple[int, bool]] = []
        for chat_id, record in self._records.items():
            self._ensure_active_request(record)

            is_stuck = record.is_busy and (
                now - record.last_activity >= self._busy_timeout_seconds
            )
            is_idle = not record.is_busy and (
                now - record.last_activity >= self._idle_timeout_seconds
            )

            if is_stuck or is_idle:
                stale_chat_ids.append((chat_id, is_stuck))

        for chat_id, was_stuck in stale_chat_ids:
            await self.stop_session(chat_id, announce=False)
            msg = (
                "[session closed due to timeout]\n"
                if was_stuck
                else "[session closed due to idle timeout]\n"
            )
            await self._output_callback(chat_id, OutgoingMessage(msg))

    def _ensure_active_request(self, record: SessionRecord) -> bool:
        if record.is_busy or not record.pending_prompts:
            return False

        message_id, prompt = record.pending_prompts.popleft()
        record.active_message_id = message_id
        record.active_task = asyncio.create_task(
            self._run_request(record, prompt)
        )
        if self._request_started_callback is not None:
            self._request_started_callback(record.chat_id, record.active_task)
        return True

    async def _cancel_active_request(self, record: SessionRecord) -> bool:
        had_active_request = record.is_busy

        if record.active_process is not None:
            await terminate_process(record.active_process)

        if record.active_task is not None:
            record.active_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await record.active_task

        return had_active_request

    def _provider_for_session(
        self,
        provider_name: str,
        cwd: Path | None,
    ) -> ProviderSpec:
        provider = self._providers[provider_name]
        if cwd is None or cwd == provider.cwd:
            return provider
        return ProviderSpec(
            adapter=provider.adapter,
            cwd=cwd,
            skip_git_repo_check=provider.skip_git_repo_check,
        )

    def _track_active_process(
        self,
        record: SessionRecord,
        process: asyncio.subprocess.Process | None,
    ) -> None:
        record.active_process = process

    async def _run_request(self, record: SessionRecord, prompt: str) -> None:
        try:
            result = await run_provider_request(
                record.provider,
                prompt,
                request_context=RequestContext(
                    is_followup=record.request_count > 0,
                    session_id=record.provider_session_id,
                ),
                process_tracker=lambda process: self._track_active_process(
                    record, process
                ),
                previous_response_text=record.last_response_text,
            )
            record.last_activity = result.completed_at
            if result.succeeded:
                if result.session_id is not None:
                    record.provider_session_id = result.session_id
                record.request_count += 1
            if result.message is not None:
                record.last_response_text = result.raw_text or result.message.text
                await self._output_callback(record.chat_id, result.message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Provider request failed for chat_id=%s", record.chat_id)
            await self._output_callback(
                record.chat_id,
                OutgoingMessage(f"[request failed: {exc}]\n"),
            )
        finally:
            record.active_process = None
            record.active_task = None
            record.active_message_id = None
            if self._records.get(record.chat_id) is record:
                self._ensure_active_request(record)
