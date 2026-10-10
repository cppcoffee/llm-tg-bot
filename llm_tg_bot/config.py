from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from llm_tg_bot.providers import PiProvider


class Settings(BaseModel, frozen=True):
    model_config = {"arbitrary_types_allowed": True}

    bot_tokens: list[str] = Field(min_length=1)
    allow_all_users: bool
    allowed_user_ids: frozenset[int] = frozenset()
    provider: PiProvider
    poll_timeout_seconds: int = Field(gt=0, default=30)
    telegram_connection_pool_size: int = Field(gt=0, default=8)
    telegram_pool_timeout_seconds: float = Field(gt=0, default=5.0)
    message_max_chars: int = Field(gt=0, default=4000)
    session_idle_timeout_seconds: int = Field(gt=0, default=7200)
    session_busy_timeout_seconds: int = Field(gt=0, default=7200)
    max_queue_size: int = Field(gt=0, default=10)
    log_level: str = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def uppercase_log_level(cls, v: str) -> str:
        return str(v).upper()


def load_settings() -> Settings:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    bot_tokens = _load_bot_tokens()
    provider = _load_provider()

    allow_all_users, allowed_user_ids = _load_allowed_users(
        os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    )

    return Settings(
        bot_tokens=bot_tokens,
        allow_all_users=allow_all_users,
        allowed_user_ids=allowed_user_ids,
        provider=provider,
        poll_timeout_seconds=_int_env("POLL_TIMEOUT_SECONDS", 30),
        telegram_connection_pool_size=_int_env("TELEGRAM_CONNECTION_POOL_SIZE", 8),
        telegram_pool_timeout_seconds=_float_env("TELEGRAM_POOL_TIMEOUT_SECONDS", 5.0),
        message_max_chars=_int_env("MESSAGE_MAX_CHARS", _int_env("MESSAGE_MAX_BYTES", 4000)),
        session_idle_timeout_seconds=_int_env("SESSION_IDLE_TIMEOUT_SECONDS", 7200),
        session_busy_timeout_seconds=_int_env("SESSION_BUSY_TIMEOUT_SECONDS", 7200),
        max_queue_size=_int_env("MAX_QUEUE_SIZE", 10),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def _load_bot_tokens() -> list[str]:
    raw = os.getenv("TELEGRAM_BOT_TOKENS") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not raw:
        raise ValueError(
            "Missing required environment variable: TELEGRAM_BOT_TOKENS. "
            "Please set it in your .env file."
        )
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        raise ValueError("TELEGRAM_BOT_TOKENS must contain at least one token")
    # Deduplicate while preserving order
    return list(dict.fromkeys(tokens))


def _load_provider() -> PiProvider:
    if not _command_exists(PiProvider.executable):
        raise ValueError(
            "pi executable not found in PATH. "
            "Install pi and ensure it is available."
        )

    return PiProvider(cwd=_optional_path_env("WORKDIR") or Path.cwd())


def _load_allowed_users(raw_user_ids: str) -> tuple[bool, frozenset[int]]:
    if raw_user_ids == "*":
        return True, frozenset()

    allowed_user_ids = _parse_allowed_user_ids(raw_user_ids)
    if not allowed_user_ids:
        raise ValueError(
            "Configure TELEGRAM_ALLOWED_USER_IDS. "
            "Use '*' only for development."
        )

    return False, allowed_user_ids


def _parse_allowed_user_ids(raw_value: str) -> frozenset[int]:
    if not raw_value:
        return frozenset()

    user_ids: set[int] = set()
    for item in raw_value.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            user_id = int(value)
        except ValueError as exc:
            raise ValueError(
                f"TELEGRAM_ALLOWED_USER_IDS contains a non-integer value: {value!r}"
            ) from exc
        if user_id <= 0:
            raise ValueError(
                f"TELEGRAM_ALLOWED_USER_IDS must contain positive integers: {value!r}"
            )
        user_ids.add(user_id)

    return frozenset(user_ids)


def _optional_path_env(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"{name} does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"{name} is not a directory: {path}")
    return path


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float_env(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _command_exists(executable: str) -> bool:
    if "/" in executable:
        candidate = Path(executable).expanduser()
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(executable) is not None
