from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import json
import os
from typing import List, Optional


def ensure_ffmpeg_available() -> None:
    """Проверяет наличие утилиты ffmpeg в системе.
    
    Бросает исключение, если ffmpeg не найден в PATH.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFmpeg не найден. Установите FFmpeg и убедитесь, что он доступен в PATH. "
            "Например, на macOS: brew install ffmpeg"
        )


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    size: int = 640,
    use_hwaccel: bool = False,
    crf: int = 14,
    preset: str = "slow",
    audio_codec: str = "copy",
    source_colors: dict | None = None,
    auto_colorspace: bool = False,
    tune: str | None = None,
    apply_color_tags: bool = False,
    scale_flags: str = "lanczos+accurate_rnd+full_chroma_int",
    compat_video_note: bool = True,
    enhance_saturation: bool = False,
    saturation: float = 1.0,
    contrast: float = 1.0,
    brightness: float = 0.0,
    gamma: float = 1.0,
    force_limited_range: bool = False,
) -> List[str]:
    """Строит команду ffmpeg для конвертации видео в квадратный формат.
    
    - Кроп до квадрата (по меньшей стороне), затем масштаб до size x size
    - Масштабирование: высококачественное (lanczos) с точной обработкой хромы
    - Видео: H.264 (libx264 по умолчанию) с CRF, yuv420p для совместимости
    - Аудио: по умолчанию копируем исходный поток (макс. качество); при несовместимости используем AAC
    - Добавлен -movflags +faststart для более быстрой отправки
    - Цвет: проставляем теги BT.709 и ограниченный диапазон (TV) для корректной интерпретации плеерами
    
    Параметры:
        input_path: путь к исходному видео
        output_path: путь для выходного файла (mp4)
        size: целевой размер стороны квадрата (по умолчанию 640)
        use_hwaccel: использовать ли аппаратное кодирование (опционально)
        crf: целевой CRF для libx264 (меньше — лучше качество/больше размер)
        preset: пресет скорости для libx264
        audio_codec: 'copy' для копирования звука или 'aac' для перекодирования
        source_colors: словарь цветовых метаданных входа (ffprobe)
        auto_colorspace: автоматически конвертировать в bt709 при отличии исходного пространства
        tune: подсказка кодеку (например, 'film' или 'grain') для визуального качества
        apply_color_tags: проставлять ли цветовые теги BT.709 в выходном видео
        scale_flags: флаги для фильтра scale (качество ресемплинга)
    
    Примечание:
        Для macOS можно попробовать аппаратное кодирование:
        заменить ('-c:v', 'libx264', '-crf', str(crf), '-preset', preset)
        на ('-c:v', 'h264_videotoolbox', '-b:v', '2.5M') — будет быстрее, но CRF не применяется.
    """
    # Кроп → (опционально HDR→SDR) → масштаб → setsar → диапазон → формат пикселей
    vf_chain = [f"crop='min(in_w,in_h)':'min(in_w,in_h)'"]
    prim = (source_colors.get("color_primaries") or "").lower() if source_colors else ""
    trc = (source_colors.get("color_transfer") or "").lower() if source_colors else ""
    mtx = (source_colors.get("color_space") or "").lower() if source_colors else ""
    is_hdr = trc in ("arib-std-b67", "smpte2084") or "2020" in prim or "2020" in mtx
    # Для HDR (HLG/PQ, BT.2020) используем тонемаппинг в SDR BT.709 перед масштабированием
    if is_hdr:
        prim_in = "bt2020" if "2020" in prim else (prim or "bt709")
        mtx_in = "bt2020nc" if "2020" in mtx else (mtx or "bt709")
        trc_in = trc or "arib-std-b67"
        vf_chain += [
            f"zscale=transferin={trc_in}:primariesin={prim_in}:matrixin={mtx_in}",
            "tonemap=hable:param=0.5:desat=0",
            "zscale=transfer=bt709:primaries=bt709:matrix=bt709:range=tv",
        ]
    vf_chain += [
        f"scale={size}:{size}:flags={scale_flags}",
        "setsar=1",
    ]
    # Приводим к ограниченному диапазону (TV), который ожидают мобильные клиенты (для не-HDR пути)
    if force_limited_range and not is_hdr:
        # colorspace фактически конвертирует значения в TV range и bt709
        vf_chain.append("colorspace=all=bt709:range=tv:fast=1")
    # Лёгкое «подкручивание» насыщенности/контраста для более сочной картинки на мобильных
    # Используем eq, чтобы не менять оттенок (hue) и не «пережигать» светлые области
    if enhance_saturation:
        eq_parts = [
            f"saturation={max(0.0, saturation):.3f}",
            f"contrast={max(0.0, contrast):.3f}",
            f"brightness={brightness:.3f}",
            f"gamma={max(0.1, gamma):.3f}",
        ]
        vf_chain.append("eq=" + ":".join(eq_parts))
    # Итоговый формат пикселей для совместимости с Telegram
    vf_chain.append("format=yuv420p")
    # Для максимальной совместимости video note на мобильных не проставляем цветовые теги
    if not compat_video_note and apply_color_tags:
        vf_chain.append("setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709")
    scale_crop = ",".join(vf_chain)
    cmd = [
        "ffmpeg",
        "-y",  # перезаписывать выходной файл без запроса
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(input_path),
        "-vf", scale_crop,
        "-movflags", "+faststart",
    ]
    if use_hwaccel:
        # Аппаратное кодирование (пример для macOS). Обычно быстрее, но контроль качества иной.
        cmd += [
            "-c:v", "h264_videotoolbox",
            "-b:v", "2.5M",  # примерный видеобитрейт
        ]
    else:
        # Программное кодирование libx264 с CRF
        if compat_video_note:
            # Максимальная совместимость с мобильными клиентами Telegram
            cmd += [
                "-c:v", "libx264",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-preset", preset,
                "-crf", str(crf),
            ]
            if tune:
                cmd += ["-tune", tune]
            # Явные цветовые метаданные BT.709 + ограниченный диапазон
            if force_limited_range:
                cmd += [
                    "-color_primaries", "bt709",
                    "-color_trc", "bt709",
                    "-colorspace", "bt709",
                    "-color_range", "tv",
                ]
        else:
            cmd += [
                "-c:v", "libx264",
                "-profile:v", "high",
                "-preset", preset,
                "-crf", str(crf),
            ]
            if tune:
                cmd += ["-tune", tune]
            if force_limited_range:
                cmd += [
                    "-color_primaries", "bt709",
                    "-color_trc", "bt709",
                    "-colorspace", "bt709",
                    "-color_range", "tv",
                ]
    # Аудио: либо копируем как есть, либо перекодируем в AAC без даунмикса
    if audio_codec == "copy":
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [str(output_path)]
    return cmd


def probe_source_colorspace(input_path: Path) -> dict:
    """Опрашивает ffprobe, чтобы получить color_space/transfer/primaries у входного видео.
    
    Возвращает словарь с ключами color_space, color_transfer, color_primaries (если доступны).
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=color_space,color_transfer,color_primaries",
        "-of", "json",
        str(input_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        if streams:
            s = streams[0]
            return {
                "color_space": s.get("color_space"),
                "color_transfer": s.get("color_transfer"),
                "color_primaries": s.get("color_primaries"),
            }
    except Exception:
        pass
    return {}


def convert_to_square_video_note(
    input_path: Path,
    output_path: Path,
    size: int = 640,
    use_hwaccel: bool = False,
    crf: int = 18,
    preset: str = "slow",
) -> None:
    """Запускает ffmpeg-конвертацию в квадратный формат для видео-заметки.
    
    Бросает RuntimeError при неудаче, включая stderr ffmpeg.
    """
    ensure_ffmpeg_available()
    source_colors = probe_source_colorspace(input_path)
    # Опциональная «подкрутка» цвета из .env (по умолчанию выключена)
    enhance = os.getenv("ENHANCE_SAT", "0").lower() in ("1", "true", "yes", "on")
    sat = float(os.getenv("VIDEO_NOTE_SAT", "1.12"))
    con = float(os.getenv("VIDEO_NOTE_CONTRAST", "1.02"))
    bri = float(os.getenv("VIDEO_NOTE_BRIGHTNESS", "0.0"))
    gam = float(os.getenv("VIDEO_NOTE_GAMMA", "1.0"))
    # 1-я попытка: копировать аудио для максимального качества/скорости
    cmd = build_ffmpeg_command(
        input_path=input_path,
        output_path=output_path,
        size=size,
        use_hwaccel=use_hwaccel,
        crf=crf,
        preset=preset,
        source_colors=source_colors,
        auto_colorspace=False,  # отключаем автоматическую конверсию цветов для совместимости
        audio_codec="copy",
        compat_video_note=True,
        enhance_saturation=enhance,
        saturation=sat,
        contrast=con,
        brightness=bri,
        gamma=gam,
    )
    # Таймаут FFmpeg из .env (по умолчанию 600 сек)
    try:
        ffmpeg_timeout_s = int(os.getenv("FFMPEG_TIMEOUT_SECONDS", "600"))
    except Exception:
        ffmpeg_timeout_s = 600
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=ffmpeg_timeout_s)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Обработка видео превысила лимит времени {ffmpeg_timeout_s} сек и была прервана.")
    if proc.returncode != 0:
        # Если копирование звука несовместимо с mp4 (например, opus), то повторим с AAC
        cmd_fallback = build_ffmpeg_command(
            input_path=input_path,
            output_path=output_path,
            size=size,
            use_hwaccel=use_hwaccel,
            crf=crf,
            preset=preset,
            source_colors=source_colors,
            auto_colorspace=False,
            audio_codec="aac",
            compat_video_note=True,
            enhance_saturation=enhance,
            saturation=sat,
            contrast=con,
            brightness=bri,
            gamma=gam,
        )
        try:
            proc2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=ffmpeg_timeout_s)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Обработка видео (fallback) превысила лимит времени {ffmpeg_timeout_s} сек и была прервана.")
        if proc2.returncode != 0:
            raise RuntimeError(f"FFmpeg ошибка:\n{proc2.stderr.strip()}")

    # Гарантируем укладывание в лимит размера для video note
    limit_mb_env = os.getenv("TELEGRAM_VIDEONOTE_LIMIT_MB", "").strip()
    try:
        # 0 или отрицательное значение — отключает контроль размера
        size_limit_mb = float(limit_mb_env) if limit_mb_env else 12.0
    except Exception:
        size_limit_mb = 12.0
    if size_limit_mb <= 0:
        # Без ограничения: не трогаем результат, отправляем как есть
        return
    size_limit_bytes = int(size_limit_mb * 1024 * 1024)
    try:
        out_size = output_path.stat().st_size
    except FileNotFoundError:
        out_size = 0
    if out_size > size_limit_bytes:
        # Перекодируем с расчётом целевого битрейта
        duration = _probe_duration_seconds(output_path) or _probe_duration_seconds(input_path) or 0.0
        # Резерв 95% лимита под полезные данные
        target_bits_total = int(size_limit_bytes * 8 * 0.95)
        audio_k = 96
        if duration > 0:
            v_k = max(300, int(target_bits_total / duration / 1000) - audio_k)
        else:
            v_k = 1800
        tmp_path = output_path.with_suffix(".sizefix.mp4")
        cmd_reduce = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(output_path),
            "-r", "24",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-preset", "medium",
            "-crf", str(max(crf, 22)),
            "-maxrate", f"{v_k}k",
            "-bufsize", f"{v_k * 2}k",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", f"{audio_k}k",
            str(tmp_path),
        ]
        try:
            proc3 = subprocess.run(cmd_reduce, capture_output=True, text=True, timeout=ffmpeg_timeout_s)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Обработка видео (size-fix) превысила лимит времени {ffmpeg_timeout_s} сек и была прервана.")
        if proc3.returncode != 0:
            raise RuntimeError(f"FFmpeg ошибка (size-fix):\n{proc3.stderr.strip()}")
        # Заменяем файл результатом перекодирования
        output_path.unlink(missing_ok=True)
        tmp_path.rename(output_path)

def _probe_duration_seconds(path: Path) -> Optional[float]:
    """Возвращает длительность файла в секундах через ffprobe, либо None при ошибке.
    
    Используется для документов (video как файл), где Telegram не указывает duration.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        s = (out.stdout or "").strip()
        return float(s) if s else None
    except Exception:
        return None

