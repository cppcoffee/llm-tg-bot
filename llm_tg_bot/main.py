from __future__ import annotations

import asyncio
import logging

from llm_tg_bot.bot import BridgeBot
from llm_tg_bot.config import Settings, load_settings

_RESTART_DELAY_SECONDS = 5

logger = logging.getLogger(__name__)


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
    
    tasks = []
    for i, token in enumerate(settings.bot_tokens):
        masked_token = f"{token[:10]}...{token[-5:]}" if len(token) > 15 else "***"
        logger.info("Starting Bot %d with token %s", i, masked_token)
        tasks.append(_run_bot_forever(token, settings, i))

    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
