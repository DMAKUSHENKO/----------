from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Tuple

from aiogram import F, Router
from aiogram.types import FSInputFile, Message
from aiogram.utils.chat_action import ChatActionSender

from .ffmpeg_utils import convert_to_square_video_note

router = Router(name="media_handlers")


@router.message(F.text == "/start")
async def cmd_start(message: Message) -> None:
    """Приветственное сообщение и краткая инструкция."""
    await message.answer(
        "Привет! Пришлите мне видео, видео-заметку или документ с видео — "
        "я конвертирую его в «кружок» (video note) и отправлю обратно."
    )


def _extract_file_id(message: Message) -> Tuple[Optional[str], str]:
    """Определяет file_id из сообщения и человеческий тип объекта.
    
    Возвращает:
        (file_id или None, тип: 'video' | 'video_note' | 'document')
    """
    if message.video:
        return message.video.file_id, "video"
    if message.video_note:
        return message.video_note.file_id, "video_note"
    if message.document:
        # Документ может быть видео: проверим mime_type
        mt = (message.document.mime_type or "").lower()
        if mt.startswith("video/"):
            return message.document.file_id, "document"
    return None, "unknown"


async def _download_file_to(
    message: Message,
    file_id: str,
    dst_path: Path,
) -> Path:
    """Скачивает файл Telegram по file_id в указанный путь.
    
    Комментарии:
    - Сначала получаем объект файла у Telegram (чтобы узнать оригинальный путь/расширение)
    - Затем скачиваем его содержимое на диск
    """
    bot = message.bot
    file = await bot.get_file(file_id)
    # Если у Telegram есть расширение, используем его для исходного имени
    ext = Path(file.file_path or "").suffix or ".mp4"
    src_path = dst_path.with_suffix(ext)
    await bot.download(file, destination=src_path)
    return src_path


async def _process_and_reply_with_video_note(
    message: Message,
    size: int = 640,
) -> None:
    """Скачивает медиа, конвертирует его в квадратный формат и отвечает video_note.
    
    Основные шаги:
    1) Скачивание файла
    2) Конвертация через FFmpeg в 640x640 (H.264 + AAC)
    3) Отправка как answer_video_note
    """
    file_id, kind = _extract_file_id(message)
    if not file_id:
        await message.answer("Не удалось распознать видео. Пришлите видео, видео-заметку или видео-документ.")
        return
    async with ChatActionSender.upload_video_note(chat_id=message.chat.id, bot=message.bot):
        try:
            with TemporaryDirectory(prefix="videonote_") as td:
                tmp_dir = Path(td)
                # 1) Скачивание
                source_path_hint = tmp_dir / "input"
                src_path = await _download_file_to(message, file_id, source_path_hint)
                # 2) Конвертация (в отдельном потоке, чтобы не блокировать event loop)
                out_path = tmp_dir / "output.mp4"
                await asyncio.to_thread(
                    convert_to_square_video_note,
                    src_path,          # исходное видео
                    out_path,          # путь результата
                    size,              # длина стороны квадрата (по умолчанию 640)
                    False,             # аппаратное ускорение (можно включить при желании)
                    14,                # CRF ниже — выше качество (увеличит размер файла)
                    "slow",            # preset для лучшего качества/компрессии
                )
                # 3) Отправка как video_note
                video_note = FSInputFile(out_path)
                await message.answer_video_note(video_note=video_note, length=size)
        except Exception as e:
            await message.answer(f"Ошибка при обработке видео: {e}")


@router.message(F.video)
async def handle_video(message: Message) -> None:
    """Обработчик обычных видео."""
    await _process_and_reply_with_video_note(message)


@router.message(F.video_note)
async def handle_video_note(message: Message) -> None:
    """Обработчик видео-заметок (можно перекодировать для единообразия/качества)."""
    await _process_and_reply_with_video_note(message)


@router.message(F.document)
async def handle_document(message: Message) -> None:
    """Обработчик документов, если это видео (video/*)."""
    mt = (message.document.mime_type or "").lower()
    if mt.startswith("video/"):
        await _process_and_reply_with_video_note(message)
    else:
        await message.answer("Этот документ не является видео. Пришлите видео или видео-документ (video/*).")


