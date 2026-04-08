from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from telegram import ReplyKeyboardMarkup

from llm_tg_bot.config import Settings
from llm_tg_bot.session import SessionManager

SendMessage = Callable[[int, str, ReplyKeyboardMarkup | None], Awaitable[None]]
KeyboardFactory = Callable[[], ReplyKeyboardMarkup]

BOT_COMMANDS = frozenset(
    {
        "/help",
        "/list",
        "/use",
        "/new",
        "/status",
        "/stop",
        "/cancel",
    }
)

_DIRECTORY_BUTTON_LIMIT = 24
_KEYBOARD_COLUMNS = 2


def command_name(text: str) -> str:
    return text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()


def is_bot_command(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and command_name(stripped) in BOT_COMMANDS


@dataclass(slots=True)
class PendingNewSession:
    provider_name: str | None = None


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
        self._preferred_provider_by_chat: dict[int, str] = {}
        self._pending_new_session_by_chat: dict[int, PendingNewSession] = {}

    async def handle(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        command = command_name(parts[0])
        raw_arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            await self._send_message(
                chat_id,
                self._help_text(chat_id),
                reply_markup=self._keyboard_factory(),
            )
            return

        if command == "/list":
            await self._send_message(chat_id, self._providers_text())
            return

        if command == "/use":
            if not raw_arg:
                await self._send_message(chat_id, "Usage: /use <provider>")
                return
            await self._set_preferred_provider(chat_id, raw_arg.lower())
            return

        if command == "/new":
            if not raw_arg:
                await self._begin_new_session(chat_id)
                return

            provider_name, directory_choice = self._parse_new_arguments(
                chat_id, raw_arg
            )
            if directory_choice is None:
                await self._begin_new_session(chat_id, provider_name=provider_name)
                return

            workdir = self._resolve_workdir_choice(provider_name, directory_choice)
            await self._start_session(chat_id, provider_name, workdir)
            return

        if command == "/status":
            preferred = self.preferred_provider(chat_id)
            status = self._session_manager.status_text(chat_id)
            await self._send_message(
                chat_id, f"Preferred provider: {preferred}\n{status}"
            )
            return

        if command == "/stop":
            self._pending_new_session_by_chat.pop(chat_id, None)
            stopped = await self._session_manager.stop_session(chat_id, announce=False)
            await self._send_message(
                chat_id,
                "[session stopped]" if stopped else "No active session.",
                reply_markup=self._keyboard_factory(),
            )
            return

        if command == "/cancel":
            selection_cancelled = (
                self._pending_new_session_by_chat.pop(chat_id, None) is not None
            )
            interrupted = await self._session_manager.interrupt(chat_id)
            if selection_cancelled and interrupted:
                message = "[request cancelled]\n[new session setup cancelled]"
            elif selection_cancelled:
                message = "[new session setup cancelled]"
            else:
                message = "[request cancelled]" if interrupted else "No active request."
            await self._send_message(
                chat_id,
                message,
                reply_markup=self._keyboard_factory(),
            )
            return

        await self._send_message(chat_id, "Unknown command. Use /help.")

    def has_pending_new_session(self, chat_id: int) -> bool:
        return chat_id in self._pending_new_session_by_chat

    async def handle_pending_input(self, chat_id: int, text: str) -> bool:
        pending = self._pending_new_session_by_chat.get(chat_id)
        if pending is None:
            return False

        choice = text.strip()
        if pending.provider_name is None:
            provider_name = choice.lower()
            if provider_name not in self._settings.providers:
                await self._send_message(
                    chat_id,
                    (
                        f"Unknown provider {choice!r}. "
                        "Choose one of the configured providers or send /cancel."
                    ),
                    reply_markup=self._provider_keyboard(),
                )
                return True

            pending.provider_name = provider_name
            await self._send_message(
                chat_id,
                self._directory_prompt(provider_name),
                reply_markup=self._directory_keyboard(provider_name),
            )
            return True

        provider_name = pending.provider_name
        try:
            workdir = self._resolve_workdir_choice(provider_name, choice)
            await self._start_session(chat_id, provider_name, workdir)
        except ValueError as exc:
            await self._send_message(
                chat_id,
                f"Error: {exc}\n\n{self._directory_prompt(provider_name)}",
                reply_markup=self._directory_keyboard(provider_name),
            )
        return True

    def preferred_provider(self, chat_id: int) -> str:
        return self._preferred_provider_by_chat.get(
            chat_id, self._settings.default_provider
        )

    async def _set_preferred_provider(self, chat_id: int, provider_name: str) -> None:
        self._ensure_provider_exists(provider_name)
        self._preferred_provider_by_chat[chat_id] = provider_name
        await self._send_message(
            chat_id,
            f"Preferred provider set to {provider_name}. "
            "Use /new to restart the session with this provider.",
            reply_markup=self._keyboard_factory(),
        )

    def _ensure_provider_exists(self, provider_name: str) -> None:
        if provider_name not in self._settings.providers:
            available = ", ".join(sorted(self._settings.providers))
            raise ValueError(
                f"Unknown provider {provider_name!r}. Available: {available}"
            )

    def _providers_text(self) -> str:
        provider_items = sorted(self._settings.providers.items())
        workdirs = {self._format_workdir(provider.cwd) for _, provider in provider_items}

        if len(workdirs) == 1:
            shared_workdir = next(iter(workdirs))
            lines = [
                f"Workdir root: {shared_workdir}",
                "Available providers:",
            ]
            for name, provider in provider_items:
                lines.append(f"- {name}: {provider.display_command}")
            lines.append("")
            lines.append("Use /new to choose a provider and a direct child directory.")
            return "\n".join(lines)

        lines = ["Available providers:"]
        for name, provider in provider_items:
            workdir = self._format_workdir(provider.cwd)
            lines.append(f"- {name}: {provider.display_command} | workdir={workdir}")
        lines.append("")
        lines.append("Use /new to choose a provider and a direct child directory.")
        return "\n".join(lines)

    @staticmethod
    def _format_workdir(workdir: object) -> str:
        return str(workdir) if workdir else "(current working directory)"

    async def _begin_new_session(
        self,
        chat_id: int,
        provider_name: str | None = None,
    ) -> None:
        if provider_name is not None:
            self._ensure_provider_exists(provider_name)
            self._pending_new_session_by_chat[chat_id] = PendingNewSession(
                provider_name=provider_name
            )
            await self._send_message(
                chat_id,
                self._directory_prompt(provider_name),
                reply_markup=self._directory_keyboard(provider_name),
            )
            return

        preferred = self.preferred_provider(chat_id)
        self._pending_new_session_by_chat[chat_id] = PendingNewSession()
        await self._send_message(
            chat_id,
            (
                "Select provider for the new session.\n"
                f"Current preferred provider: {preferred}\n"
                "Send /cancel to abort."
            ),
            reply_markup=self._provider_keyboard(),
        )

    async def _start_session(
        self,
        chat_id: int,
        provider_name: str,
        workdir: Path,
    ) -> None:
        try:
            await self._session_manager.start_session(
                chat_id,
                provider_name,
                cwd=workdir,
            )
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise ValueError(
                f"Failed to start provider {provider_name}: {exc}"
            ) from exc
        self._pending_new_session_by_chat.pop(chat_id, None)
        await self._send_message(
            chat_id,
            f"[session started: {provider_name} | workdir={workdir}]",
            reply_markup=self._keyboard_factory(),
        )

    def _parse_new_arguments(
        self,
        chat_id: int,
        raw_arg: str,
    ) -> tuple[str, str | None]:
        try:
            tokens = shlex.split(raw_arg)
        except ValueError as exc:
            raise ValueError(f"Invalid /new arguments: {exc}") from exc

        if not tokens:
            return self.preferred_provider(chat_id), None

        provider_candidate = tokens[0].lower()
        if provider_candidate in self._settings.providers:
            directory_choice = " ".join(tokens[1:]) or None
            return provider_candidate, directory_choice

        if len(tokens) > 1:
            raise ValueError("Usage: /new [provider] [directory]")

        return self.preferred_provider(chat_id), tokens[0]

    def _provider_keyboard(self) -> ReplyKeyboardMarkup:
        return self._choices_keyboard(sorted(self._settings.providers))

    def _directory_keyboard(self, provider_name: str) -> ReplyKeyboardMarkup:
        choices = [".", *self._visible_child_directory_names(provider_name)]
        return self._choices_keyboard(choices[: _DIRECTORY_BUTTON_LIMIT + 1])

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

    def _directory_prompt(self, provider_name: str) -> str:
        root = self._session_root(provider_name)
        visible_directories = self._visible_child_directory_names(provider_name)
        lines = [
            f"Select workdir for {provider_name} under {root}",
            "Use . for the root directory.",
        ]
        if visible_directories:
            preview = visible_directories[:_DIRECTORY_BUTTON_LIMIT]
            lines.append("Direct child directories:")
            lines.extend(f"- {name}" for name in preview)
            if len(visible_directories) > len(preview):
                remaining = len(visible_directories) - len(preview)
                lines.append(
                    f"- ... ({remaining} more; you can type a direct child directory name manually)"
                )
        else:
            lines.append(
                "No visible child directories were found. "
                "You can still type a direct child directory name manually."
            )
        lines.append("")
        lines.append("Send /cancel to abort.")
        return "\n".join(lines)

    def _resolve_workdir_choice(self, provider_name: str, value: str) -> Path:
        choice = value.strip()
        if not choice:
            raise ValueError("Directory selection cannot be empty.")

        root = self._session_root(provider_name)
        if choice == ".":
            return root

        raw_path = Path(choice).expanduser()
        candidate = raw_path if raw_path.is_absolute() else root / raw_path
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"Directory does not exist: {choice}") from exc
        except OSError as exc:
            raise ValueError(f"Failed to resolve directory {choice!r}: {exc}") from exc

        if not resolved.is_dir():
            raise ValueError(f"Not a directory: {choice}")
        if resolved == root:
            return root
        if resolved.parent != root:
            raise ValueError(
                "Choose the root directory (.) or a direct child directory "
                "of the configured workdir."
            )
        return resolved

    def _visible_child_directory_names(self, provider_name: str) -> list[str]:
        root = self._session_root(provider_name)
        try:
            directories = [
                child.name
                for child in root.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            ]
        except OSError as exc:
            raise ValueError(f"Failed to inspect workdir {root}: {exc}") from exc
        return sorted(directories, key=str.lower)

    def _session_root(self, provider_name: str) -> Path:
        self._ensure_provider_exists(provider_name)
        root = self._settings.providers[provider_name].cwd or Path.cwd()
        try:
            resolved = root.expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"Configured workdir does not exist: {root}") from exc
        except OSError as exc:
            raise ValueError(f"Failed to resolve workdir {root}: {exc}") from exc

        if not resolved.is_dir():
            raise ValueError(f"Configured workdir is not a directory: {resolved}")
        return resolved

    def _help_text(self, chat_id: int) -> str:
        return (
            "Commands:\n"
            "/help - show this message\n"
            "/list - list configured providers\n"
            "/use <provider> - set preferred provider for this chat\n"
            "/new [provider] [directory] - choose or start a session\n"
            "/status - show current session status\n"
            "/stop - stop the current session\n"
            "/cancel - cancel the in-flight request or /new setup\n\n"
            f"Current preferred provider: {self.preferred_provider(chat_id)}\n"
            "Use /new with no arguments to choose a provider and a direct child "
            "directory under the configured workdir.\n"
            "Plain text messages are forwarded as standalone CLI requests and "
            "queued while the provider is busy."
        )
