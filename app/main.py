from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import load_settings
from .handlers import router as media_router


async def main() -> None:
    """Точка входа: инициализация бота и запуск polling."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_settings()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(media_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())


