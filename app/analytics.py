from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Tuple, Optional
import time
import os

DB_PATH = Path(os.getenv("ANALYTICS_DB_PATH", "data/bot.db"))


def _get_conn() -> sqlite3.Connection:
    """Возвращает соединение с БД SQLite, создаёт структуру при первом обращении."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            ts INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS counters (
            user_id INTEGER PRIMARY KEY,
            processed_count INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            ts INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            ts INTEGER NOT NULL
        );
        """
    )
    return conn


def record_event(user_id: int, event: str) -> None:
    """Сохраняет событие в таблицу events (например, 'start', 'conversion')."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO events(user_id, event, ts) VALUES (?, ?, ?);",
            (user_id, event, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def record_start(user_id: int) -> None:
    """Учитывает нажатие /start."""
    record_event(user_id, "start")


def record_conversion(user_id: int) -> None:
    """Учитывает успешную обработку видео (кружка)."""
    try:
        conn = _get_conn()
        with conn:
            conn.execute(
                "INSERT INTO events(user_id, event, ts) VALUES (?, ?, ?);",
                (user_id, "conversion", int(time.time())),
            )
            conn.execute(
                """
                INSERT INTO counters(user_id, processed_count) VALUES(?, 1)
                ON CONFLICT(user_id) DO UPDATE SET processed_count = processed_count + 1;
                """,
                (user_id,),
            )
    finally:
        conn.close()

def record_error(user_id: int, code: str) -> None:
    """Учитывает ошибку обработки с коротким кодом."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO errors(user_id, code, ts) VALUES (?, ?, ?);",
            (user_id, code, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()

def record_metric(user_id: int, metric: str, value: float) -> None:
    """Сохраняет числовую метрику (например, processing_ms, output_size_bytes)."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO metrics(user_id, metric, value, ts) VALUES (?, ?, ?, ?);",
            (user_id, metric, value, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()

def record_kind(user_id: int, kind: str) -> None:
    """Фиксирует тип входного медиа (video | video_note | document)."""
    record_event(user_id, f"kind:{kind}")


def get_stats() -> dict:
    """Возвращает словарь с агрегированной статистикой."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # Всего уникальных пользователей (по любому событию)
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM events;")
        total_users = cur.fetchone()[0] or 0
        # Всего обработок (сумма по counters)
        cur.execute("SELECT COALESCE(SUM(processed_count), 0) FROM counters;")
        total_conversions = cur.fetchone()[0] or 0
        # Топ-5 пользователей по количеству обработок
        cur.execute(
            """
            SELECT user_id, processed_count
            FROM counters
            ORDER BY processed_count DESC, user_id ASC
            LIMIT 5;
            """
        )
        top = cur.fetchall()
        return {
            "total_users": total_users,
            "total_conversions": total_conversions,
            "top_users": top,  # List[Tuple[user_id, count]]
        }
    finally:
        conn.close()

def get_detailed_stats() -> dict:
    """Расширенная статистика: ошибки, средняя длительность обработки, размеры и разбивка по типам медиа."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # Ошибки
        cur.execute("SELECT COUNT(*) FROM errors;")
        total_errors = cur.fetchone()[0] or 0
        cur.execute("SELECT code, COUNT(*) AS c FROM errors GROUP BY code ORDER BY c DESC LIMIT 5;")
        top_errors = cur.fetchall()
        # Время обработки
        cur.execute("SELECT AVG(value) FROM metrics WHERE metric='processing_ms';")
        avg_ms = cur.fetchone()[0]
        # Размеры результата
        cur.execute("SELECT SUM(value), AVG(value) FROM metrics WHERE metric='output_size_bytes';")
        row = cur.fetchone()
        sum_bytes = row[0] or 0
        avg_bytes = row[1]
        # Разбивка по типам медиа
        cur.execute(
            """
            SELECT substr(event, 6) AS kind, COUNT(*)
            FROM events
            WHERE event LIKE 'kind:%'
            GROUP BY kind
            ORDER BY COUNT(*) DESC;
            """
        )
        kinds = cur.fetchall()
        return {
            "total_errors": total_errors,
            "top_errors": top_errors,  # List[Tuple[code, count]]
            "avg_processing_ms": avg_ms,
            "sum_output_bytes": sum_bytes,
            "avg_output_bytes": avg_bytes,
            "kinds": kinds,  # List[Tuple[kind, count]]
        }
    finally:
        conn.close()


