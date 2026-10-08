from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from llm_tg_bot.bot import BridgeBot
from llm_tg_bot.config import Settings, load_settings

_RESTART_DELAY_SECONDS = 5

logger = logging.getLogger(__name__)


@contextmanager
def _bot_token_lock(token: str) -> Iterator[None]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # ponytail: per-user local lock; other hosts still require a single deployment.
    path = Path(tempfile.gettempdir()) / f"llm-tg-bot-{os.getuid()}-{token_hash}.lock"
    with path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise SystemExit(
                "Another llm-tg-bot instance is already using a configured bot token. "
                "Stop the existing instance before starting this one."
            ) from None
        # Closing the file releases the lock. Do not unlink it: waiting processes
        # must continue to lock the same inode.
        yield


async def _run_bot_forever(token: str, settings: Settings, index: int) -> None:
    while True:
        try:
            bot = BridgeBot(token, settings)
            await bot.run()
        except Exception:
            logger.exception("Bot %d crashed, restarting in %ds", index, _RESTART_DELAY_SECONDS)
            await asyncio.sleep(_RESTART_DELAY_SECONDS)


async def async_main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    with ExitStack() as locks:
        for token in settings.bot_tokens:
            locks.enter_context(_bot_token_lock(token))

        tasks = []
        for i, token in enumerate(settings.bot_tokens):
            logger.info("Starting Bot %d", i)
            tasks.append(_run_bot_forever(token, settings, i))

        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
