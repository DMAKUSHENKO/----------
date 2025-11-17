from __future__ import annotations

import asyncio
from pathlib import Path
import time
from tempfile import TemporaryDirectory
from typing import Optional, Tuple
import os

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message
from aiogram.utils.chat_action import ChatActionSender
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramAPIError

from .ffmpeg_utils import convert_to_square_video_note, _probe_duration_seconds
from .analytics import (
    record_start,
    record_conversion,
    get_stats,
    record_error,
    record_metric,
    record_kind,
    get_detailed_stats,
)
from aiogram.filters import Command

router = Router(name="media_handlers")

# Простой анти-дубль: запоминаем обработанные сообщения на короткое время
_processed_messages: dict[tuple[int, int], float] = {}
_processed_ttl_seconds = 180.0

# Глобальный лимитер параллелизма (Semaphore)
try:
    _max_concurrency = int(os.getenv("MAX_CONCURRENCY", "2"))
except Exception:
    _max_concurrency = 2
_semaphore = asyncio.Semaphore(max(1, _max_concurrency))

# Пер-юзер ограничение частоты запросов
try:
    _per_user_limit_s = float(os.getenv("USER_RATE_LIMIT_SECONDS", "20"))
except Exception:
    _per_user_limit_s = 20.0
_user_last_ts: dict[int, float] = {}
_user_busy_until: dict[int, float] = {}
_user_locks: dict[int, asyncio.Lock] = {}

_processed_groups: dict[str, float] = {}
_groups_ttl_seconds = 300.0


def _get_user_lock(user_id: int) -> asyncio.Lock:
    """Возвращает (и кэширует) per-user Lock для атомарных проверок лимитов."""
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Приветственное сообщение и краткая инструкция."""
    # Анти-дубль: если тот же message_id уже обрабатывался недавно, выходим
    key = (message.chat.id, message.message_id)
    now = time.time()
    for (c_id, m_id), ts in list(_processed_messages.items()):
        if now - ts > _processed_ttl_seconds:
            _processed_messages.pop((c_id, m_id), None)
    if _processed_messages.get(key) and now - _processed_messages[key] <= _processed_ttl_seconds:
        return
    _processed_messages[key] = now

    # Аналитика: /start
    if message.from_user:
        record_start(message.from_user.id)

    await message.answer(
        "Привет! Я могу превратить твои красивые видео в «кружки» хорошего качества.\n\n"
        "Как мной пользоваться:\n"
        "1) Можно загружать видео как медиа или как файл (документ).\n"
        "2) Ограничение размера — до 20 МБ.\n"
        "3) Кадрировать видео до квадрата не нужно — я сделаю это сам автоматически.\n\n"
        "Просто пришли видео — и я верну его красивым кружочком."
    )

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Показывает админскую статистику: всего пользователей и обработок.
    
    Доступ: только ADMIN_ID (Telegram user id) из переменной окружения.
    """
    admin_id = int(os.getenv("ADMIN_ID", "0") or "0")
    if not message.from_user or message.from_user.id != admin_id or admin_id == 0:
        return
    # Базовая статистика
    data = get_stats()
    total_users = data.get("total_users", 0)
    total_conv = data.get("total_conversions", 0)
    top = data.get("top_users", [])
    lines = [
        "Статистика бота:",
        f"- Всего пользователей: {total_users}",
        f"- Всего обработок: {total_conv}",
    ]
    if top:
        lines.append("- Топ по обработкам:")
        for uid, cnt in top:
            lines.append(f"  • user_id={uid}: {cnt}")
    # Расширенная статистика
    d = get_detailed_stats()
    lines.append("")  # пустая строка-разделитель
    lines.append("Детальная статистика:")
    lines.append(f"- Ошибок всего: {d.get('total_errors', 0)}")
    top_errors = d.get("top_errors") or []
    if top_errors:
        lines.append("- Частые ошибки:")
        for code, cnt in top_errors:
            lines.append(f"  • {code}: {cnt}")
    avg_ms = d.get("avg_processing_ms")
    if avg_ms is not None:
        lines.append(f"- Средняя длительность обработки: {avg_ms:.0f} мс")
    sum_b = d.get("sum_output_bytes", 0) or 0
    avg_b = d.get("avg_output_bytes")
    if sum_b:
        lines.append(f"- Всего отправлено данных: {sum_b/1024/1024:.2f} МБ")
    if avg_b is not None:
        lines.append(f"- Средний размер «кружка»: {avg_b/1024/1024:.2f} МБ")
    kinds = d.get("kinds") or []
    if kinds:
        lines.append("- Типы входного медиа:")
        for kind, cnt in kinds:
            lines.append(f"  • {kind}: {cnt}")
    await message.answer("\n".join(lines))

@router.message(Command("stats_detailed"))
async def cmd_stats_detailed(message: Message) -> None:
    """Расширенная статистика: ошибки, средняя длительность, размеры, разбивка по типам."""
    admin_id = int(os.getenv("ADMIN_ID", "0") or "0")
    if not message.from_user or message.from_user.id != admin_id or admin_id == 0:
        return
    d = get_detailed_stats()
    lines = [
        "Детальная статистика:",
        f"- Ошибок всего: {d.get('total_errors', 0)}",
    ]
    top_errors = d.get("top_errors") or []
    if top_errors:
        lines.append("- Частые ошибки:")
        for code, cnt in top_errors:
            lines.append(f"  • {code}: {cnt}")
    avg_ms = d.get("avg_processing_ms")
    if avg_ms is not None:
        lines.append(f"- Средняя длительность обработки: {avg_ms:.0f} мс")
    sum_b = d.get("sum_output_bytes", 0) or 0
    avg_b = d.get("avg_output_bytes")
    if sum_b:
        lines.append(f"- Всего отправлено данных: {sum_b/1024/1024:.2f} МБ")
    if avg_b is not None:
        lines.append(f"- Средний размер «кружка»: {avg_b/1024/1024:.2f} МБ")
    kinds = d.get("kinds") or []
    if kinds:
        lines.append("- Типы входного медиа:")
        for kind, cnt in kinds:
            lines.append(f"  • {kind}: {cnt}")
    await message.answer("\n".join(lines))
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
    # Если пользователь отправил альбом (несколько видео сразу), берём только первое
    if message.media_group_id:
        mgid = str(message.media_group_id)
        now = time.time()
        # очистим устаревшие записи
        for gid, ts in list(_processed_groups.items()):
            if now - ts > _groups_ttl_seconds:
                _processed_groups.pop(gid, None)
        if mgid in _processed_groups:
            await message.answer(
                "В одной отправке обрабатываю только первое видео. "
                "Пожалуйста, отправляйте остальные по очереди или через 20 секунд."
            )
            return
        _processed_groups[mgid] = now

    file_id, kind = _extract_file_id(message)
    if not file_id:
        await message.answer("Не удалось распознать видео. Пришлите видео, видео-заметку или видео-документ.")
        return
    # Аналитика: тип входного медиа
    if message.from_user:
        record_kind(message.from_user.id, kind)
    # Анти-дубль: если тот же message_id уже обрабатывался недавно, выходим
    key = (message.chat.id, message.message_id)
    now = time.time()
    # очистим устаревшие записи
    for (c_id, m_id), ts in list(_processed_messages.items()):
        if now - ts > _processed_ttl_seconds:
            _processed_messages.pop((c_id, m_id), None)
    if _processed_messages.get(key) and now - _processed_messages[key] <= _processed_ttl_seconds:
        return
    _processed_messages[key] = now

    # Пользовательский лимит входного файла (20 МБ по умолчанию, можно переопределить USER_VIDEO_MAX_MB)
    try:
        user_limit_mb = float(os.getenv("USER_VIDEO_MAX_MB", "20"))
    except Exception:
        user_limit_mb = 20.0
    user_limit_bytes = int(user_limit_mb * 1024 * 1024)

    # Проверяем размер до скачивания
    media_size = None
    if message.video and message.video.file_size:
        media_size = int(message.video.file_size)
    elif message.video_note and message.video_note.file_size:
        media_size = int(message.video_note.file_size)
    elif message.document and message.document.file_size:
        media_size = int(message.document.file_size)

    if media_size and media_size > user_limit_bytes:
        if message.from_user:
            record_error(message.from_user.id, "size_limit")
        await message.answer(
            f"Слишком большой файл: ~{media_size // (1024 * 1024)} МБ. "
            f"Максимальный размер — {int(user_limit_mb)} МБ.\n"
            "Пожалуйста, уменьшите размер видео и попробуйте снова."
        )
        return

    # Пер-юзер «ворота»: если одновременно пришло несколько видео,
    # берём в работу только первое, остальные ждут 20 сек
    user_id = message.from_user.id if message.from_user else 0
    user_lock = _get_user_lock(user_id)
    async with user_lock:
        now = time.time()
        busy_until = _user_busy_until.get(user_id, 0.0)
        if busy_until > now:
            wait_left = int(busy_until - now)
            await message.answer(f"Братишка, слишком много видео сразу, я так не умею работать. Отправляй по очереди, пожалуйста. Подожди {max(wait_left, 1)} сек и отправь следующее.")
            return
        # Блокируем пользователя на период rate-limit (по умолчанию 20 сек)
        _user_busy_until[user_id] = now + _per_user_limit_s

    # Лимит длительности (по умолчанию 60 сек, можно переопределить MAX_VIDEO_DURATION_SECONDS)
    try:
        max_duration_s = int(os.getenv("MAX_VIDEO_DURATION_SECONDS", "60"))
    except Exception:
        max_duration_s = 60
    duration = None
    if message.video and message.video.duration:
        duration = int(message.video.duration)
    elif message.video_note and message.video_note.duration:
        duration = int(message.video_note.duration)
    # Для документов длительность неизвестна заранее — проверим после скачивания (ниже)

    # Параллелизм: не более N одновременных конвертаций
    # Используем upload_video (а не upload_video_note), чтобы не падать на чатах, где запрещены кружки
    async with _semaphore, ChatActionSender.upload_video(chat_id=message.chat.id, bot=message.bot):
        try:
            # Сообщение пользователю о начале обработки
            t0 = time.time()
            await message.answer("Я уже работаю над твоим видосиком, скоро всё отправлю.")
            with TemporaryDirectory(prefix="videonote_") as td:
                tmp_dir = Path(td)
                # 1) Скачивание
                source_path_hint = tmp_dir / "input"
                src_path = await _download_file_to(message, file_id, source_path_hint)
                # Если длительность заранее не была известна (документ), проверим через ffprobe
                if duration is None:
                    duration_probe = await asyncio.to_thread(_probe_duration_seconds, src_path)
                    if duration_probe:
                        duration = int(duration_probe)
                if duration is not None and duration > max_duration_s:
                    await message.answer(
                        f"Длительность видео {duration} сек превышает лимит {max_duration_s} сек. "
                        "Сократите ролик и попробуйте снова."
                    )
                    return
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
                # 3) Отправка как video_note (явным методом Bot API) + сообщение с фолбэком
                await message.answer("А вот как и обещал кружочек в хорошем качестве")
                # Проверим разрешения чата на отправку video_note, если доступны
                can_send_vn = True
                try:
                    chat_info = await message.bot.get_chat(message.chat.id)
                    perms = getattr(chat_info, "permissions", None)
                    if perms is not None:
                        allowed = getattr(perms, "can_send_video_notes", None)
                        if allowed is False:
                            can_send_vn = False
                except Exception:
                    # Если не удалось получить инфо о чате, просто попробуем отправить и обработаем ошибку
                    pass
                video_note = FSInputFile(out_path)
                sent_as_note = False
                fallback_reason_forbidden = False
                if can_send_vn:
                    try:
                        await message.bot.send_video_note(
                            chat_id=message.chat.id,
                            video_note=video_note,
                            length=size,
                        )
                        sent_as_note = True
                    except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as send_err:
                        # Некоторые чаты запрещают голосовые/видео-сообщения (video notes)
                        # В таком случае отправим результат как обычное видео/документ
                        err_text = (str(send_err) or "").lower()
                        # Если причина — превышение лимита длительности кружка — объясним, без фолбэка в видео
                        if "too long" in err_text or "longer than" in err_text or "video_note" in err_text and "long" in err_text:
                            await message.answer(
                                f"Кружки в Telegram ограничены {max_duration_s} сек. "
                                "Сократите ролик и отправьте снова, чтобы получить кружок."
                            )
                            sent_as_note = False
                        elif (
                            "forbidden" in err_text and ("voice" in err_text or "video" in err_text)
                        ) or "voice messages forbidden" in err_text or "video messages forbidden" in err_text:
                            sent_as_note = False
                            fallback_reason_forbidden = True
                        else:
                            # Если ошибка иная — попробуем всё равно фолбэк как видео
                            sent_as_note = False
                if not sent_as_note:
                    # Фолбэк: отправляем как обычное видео; если и это запрещено — как документ
                    # Подсказка пользователю: почему пришло квадратное видео вместо «кружка»
                    if (not can_send_vn) or fallback_reason_forbidden:
                        await message.answer(
                            "Похоже, что «кружки» (голосовые/видеосообщения) запрещены "
                            "в этом чате или в ваших настройках приватности. "
                            "Поэтому отправляю квадратное видео.\n\n"
                            "Если хотите получать именно кружок — включите разрешение на "
                            "голосовые/видеосообщения в настройках чата/приватности и отправьте видео снова."
                        )
                    try:
                        await message.bot.send_video(
                            chat_id=message.chat.id,
                            video=FSInputFile(out_path),
                            caption="Готово ✅",
                        )
                    except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as send_video_err:
                        err2 = (str(send_video_err) or "").lower()
                        if "forbidden" in err2 and "video" in err2:
                            await message.answer("В этом чате запрещены видео. Отправляю как файл.")
                            await message.bot.send_document(
                                chat_id=message.chat.id,
                                document=FSInputFile(out_path),
                                caption="Готово ✅",
                            )
                        else:
                            # Если какая-то иная ошибка — пробросим выше
                            raise
                # Аналитика: успешная конвертация
                if message.from_user:
                    record_conversion(message.from_user.id)
                    # Техметрики
                    dt_ms = (time.time() - t0) * 1000.0
                    record_metric(message.from_user.id, "processing_ms", dt_ms)
                    try:
                        out_size = out_path.stat().st_size
                    except Exception:
                        out_size = 0
                    if out_size:
                        record_metric(message.from_user.id, "output_size_bytes", float(out_size))
        except Exception as e:
            if message.from_user:
                msg = (str(e) or "").lower()
                if "timeout" in msg:
                    code = "ffmpeg_timeout"
                elif "ffmpeg ошибка" in msg:
                    code = "ffmpeg_error"
                elif "file is too big" in msg:
                    code = "tele_big"
                elif "длительность" in msg:
                    code = "duration_limit"
                else:
                    code = "other"
                record_error(message.from_user.id, code)
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


@router.message()
async def handle_non_video(message: Message) -> None:
    """Фолбэк-валидация: если присылается не видео — отвечаем подсказкой.
    
    Игнорируем команды (например, /start), чтобы не дублировать ответы.
    """
    # Пропускаем команды
    if message.text and message.text.startswith("/"):
        return
    # Если это видео или документ с video/* — ничего не делаем (обработают профильные хендлеры)
    if message.video or message.video_note:
        return
    if message.document and (message.document.mime_type or "").lower().startswith("video/"):
        return
    await message.answer(
        "Это не видео братик, возможно промахнулся когда жмякал на экран\n"
         "Пришлите видео как медиа или как файл до 20 МБ."
    )

