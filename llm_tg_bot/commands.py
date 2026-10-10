from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from telegram import ReplyKeyboardMarkup

from llm_tg_bot.config import Settings
from llm_tg_bot.session import SessionManager
from llm_tg_bot.workdirs import (
    directory_choices,
    directory_prompt,
    provider_text,
    resolve_workdir_choice,
)

SendMessage = Callable[[int, str, ReplyKeyboardMarkup | None], Awaitable[None]]
KeyboardFactory = Callable[[], ReplyKeyboardMarkup]
CommandAction = Callable[[int, str], Awaitable[None]]

_DIRECTORY_BUTTON_LIMIT = 24
_KEYBOARD_COLUMNS = 2


def command_name(text: str) -> str:
    return text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()


@dataclass(slots=True)
class Command:
    handler: CommandAction
    help: str
    usage: str = ""


class CommandHandler:
    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        send_message: SendMessage,
        keyboard_factory: KeyboardFactory,
    ) -> None:
        self._settings = settings
        self._session_manager = session_manager
        self._send_message = send_message
        self._keyboard_factory = keyboard_factory
        self._pending_new_session_by_chat: set[int] = set()
        self._command_handlers: dict[str, Command] = {
            "/help": Command(self._handle_help, "Show this message"),
            "/list": Command(self._handle_list, "Show provider and workdir root"),
            "/new": Command(
                self._handle_new, "Start a new session", "[directory]"
            ),
            "/status": Command(self._handle_status, "Show current session status"),
            "/queue": Command(self._handle_queue, "Show queued prompts"),
            "/stop": Command(self._handle_stop, "Stop the current session"),
            "/cancel": Command(
                self._handle_cancel, "Cancel in-flight request or /new setup"
            ),
        }

    def cleanup_chat(self, chat_id: int) -> None:
        self._pending_new_session_by_chat.discard(chat_id)

    async def handle(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        command = command_name(parts[0])
        raw_arg = parts[1].strip() if len(parts) > 1 else ""
        cmd = self._command_handlers.get(command)
        if cmd is None:
            await self._send_message(chat_id, "Unknown command. Use /help.")
            return
        await cmd.handler(chat_id, raw_arg)

    def has_pending_new_session(self, chat_id: int) -> bool:
        return chat_id in self._pending_new_session_by_chat

    def is_command(self, text: str) -> bool:
        stripped = text.strip()
        return bool(stripped) and command_name(stripped) in self._command_handlers

    async def handle_pending_input(self, chat_id: int, text: str) -> bool:
        if chat_id not in self._pending_new_session_by_chat:
            return False

        await self._handle_pending_directory_choice(chat_id, text.strip())
        return True

    async def _handle_help(self, chat_id: int, raw_arg: str) -> None:
        del raw_arg
        await self._send_message(
            chat_id,
            self._help_text(),
            reply_markup=self._keyboard_factory(),
        )

    async def _handle_list(self, chat_id: int, raw_arg: str) -> None:
        del raw_arg
        await self._send_message(chat_id, provider_text(self._settings.provider))

    async def _handle_new(self, chat_id: int, raw_arg: str) -> None:
        if not raw_arg.strip():
            await self._begin_new_session(chat_id)
            return

        try:
            tokens = shlex.split(raw_arg)
        except ValueError as exc:
            raise ValueError(f"Invalid /new arguments: {exc}") from exc

        directory_choice = " ".join(tokens)
        if not directory_choice:
            await self._begin_new_session(chat_id)
            return

        await self._start_session_from_choice(chat_id, directory_choice)

    async def _handle_status(self, chat_id: int, raw_arg: str) -> None:
        del raw_arg
        await self._send_message(chat_id, self._session_manager.status_text(chat_id))

    async def _handle_queue(self, chat_id: int, raw_arg: str) -> None:
        del raw_arg
        queue = self._session_manager.queue_text(chat_id)
        await self._send_message(chat_id, queue)

    async def _handle_stop(self, chat_id: int, raw_arg: str) -> None:
        del raw_arg
        self._pending_new_session_by_chat.discard(chat_id)
        stopped = await self._session_manager.stop_session(chat_id, announce=False)
        await self._send_message(
            chat_id,
            "[session stopped]" if stopped else "No active session.",
            reply_markup=self._keyboard_factory(),
        )

    async def _handle_cancel(self, chat_id: int, raw_arg: str) -> None:
        del raw_arg
        selection_cancelled = chat_id in self._pending_new_session_by_chat
        self._pending_new_session_by_chat.discard(chat_id)
        interrupted = await self._session_manager.interrupt(chat_id)
        await self._send_message(
            chat_id,
            self._cancel_message(
                selection_cancelled=selection_cancelled,
                interrupted=interrupted,
            ),
            reply_markup=self._keyboard_factory(),
        )

    async def _handle_pending_directory_choice(self, chat_id: int, choice: str) -> None:
        try:
            await self._start_session_from_choice(
                chat_id, choice, show_keyboard=False
            )
        except ValueError as exc:
            await self._send_message(
                chat_id,
                f"Error: {exc}\n\n{self._directory_prompt()}",
                reply_markup=self._directory_keyboard(),
            )

    async def _begin_new_session(self, chat_id: int) -> None:
        self._pending_new_session_by_chat.add(chat_id)
        await self._send_message(
            chat_id,
            self._directory_prompt(),
            reply_markup=self._directory_keyboard(),
        )

    async def _start_session_from_choice(
        self,
        chat_id: int,
        directory_choice: str,
        *,
        show_keyboard: bool = True,
    ) -> None:
        workdir = self._resolve_workdir_choice(directory_choice)
        await self._start_session(chat_id, workdir, show_keyboard=show_keyboard)

    async def _start_session(
        self,
        chat_id: int,
        workdir: Path,
        *,
        show_keyboard: bool = True,
    ) -> None:
        try:
            await self._session_manager.start_session(chat_id, cwd=workdir)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise ValueError(f"Failed to start pi session: {exc}") from exc
        self._pending_new_session_by_chat.discard(chat_id)
        await self._send_message(
            chat_id,
            f"[session started: {self._settings.provider.name} | workdir={workdir}]",
            reply_markup=self._keyboard_factory() if show_keyboard else None,
        )

    def _directory_keyboard(self) -> ReplyKeyboardMarkup:
        choices = directory_choices(
            self._settings.provider,
            button_limit=_DIRECTORY_BUTTON_LIMIT,
        )
        return self._choices_keyboard(choices)

    def _choices_keyboard(self, choices: list[str]) -> ReplyKeyboardMarkup:
        rows: list[list[str]] = []
        for index in range(0, len(choices), _KEYBOARD_COLUMNS):
            rows.append(choices[index : index + _KEYBOARD_COLUMNS])
        rows.append(["/cancel"])
        return ReplyKeyboardMarkup(
            rows,
            resize_keyboard=True,
            one_time_keyboard=True,
        )

    def _directory_prompt(self) -> str:
        return directory_prompt(
            self._settings.provider,
            preview_limit=_DIRECTORY_BUTTON_LIMIT,
        )

    def _resolve_workdir_choice(self, value: str) -> Path:
        return resolve_workdir_choice(self._settings.provider, value)

    @staticmethod
    def _cancel_message(*, selection_cancelled: bool, interrupted: bool) -> str:
        if selection_cancelled and interrupted:
            return "[request cancelled]\n[new session setup cancelled]"
        if selection_cancelled:
            return "[new session setup cancelled]"
        return "[request cancelled]" if interrupted else "No active request."

    def _help_text(self) -> str:
        lines = ["Commands:"]
        for cmd_name, cmd in sorted(self._command_handlers.items()):
            usage_part = f" {cmd.usage}" if cmd.usage else ""
            lines.append(f"{cmd_name}{usage_part} - {cmd.help}")
        lines.append("")
        lines.append(
            "Use /new with no arguments to choose a direct child directory "
            "under the configured workdir.\n"
            "Plain text messages are forwarded as standalone CLI requests and "
            "queued while pi is busy."
        )
        return "\n".join(lines)
