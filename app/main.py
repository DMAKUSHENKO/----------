from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from .config import load_settings
from .handlers import router as media_router


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_settings()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(media_router)
    # Описание и команды бота (для раздела About в Telegram)
    try:
        await bot.set_my_description(
            "Бот-конвертер: превращает ваши видео в «кружки» хорошего качества. "
            "Отправь видео как медиа или файл до 20 МБ — я автоматически обрежу до квадрата и верну кружок."
        )
        await bot.set_my_short_description("Конвертирует видео в «кружки»")
        await bot.set_my_commands([BotCommand(command="start", description="Инструкция и начало работы")])
    except Exception:
        # Игнорируем ошибки установки описания, чтобы не мешать запуску
        pass
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())


