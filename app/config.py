from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Настройки приложения.
    
    Attributes:
        bot_token: Токен Telegram-бота из переменной окружения BOT_TOKEN.
    """
    bot_token: str


def load_settings() -> Settings:
    """Загружает настройки из .env и переменных окружения.
    
    Ищет файл .env в корне проекта (рабочей директории). Если файл отсутствует,
    значения берутся из переменных окружения.
    """
    # Загружаем .env из текущей рабочей директории
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не найден BOT_TOKEN. Установите переменную окружения или создайте .env с BOT_TOKEN=... "
            "См. README.md"
        )
    return Settings(bot_token=token)


