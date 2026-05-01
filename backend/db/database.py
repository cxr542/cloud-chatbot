from __future__ import annotations

import sqlite3
from typing import Any

from backend.config import settings

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    # 채팅 로그 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            query TEXT,
            difficulty TEXT,
            is_fallback BOOLEAN,
            response TEXT
        )
    """)
    # 시스템 설정 테이블 (프롬프트 튜닝 등)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # 기본 프롬프트 설정 추가
    cursor.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('system_prompt', '친절하고 명확하게 답변해주는 클라우드 학습 도우미입니다.')"
    )
    conn.commit()
    conn.close()

# 모듈 로드 시 DB 초기화
init_db()

def log_chat(query: str, difficulty: str, is_fallback: bool, response: str) -> None:
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO chat_logs (query, difficulty, is_fallback, response) VALUES (?, ?, ?, ?)",
        (query, difficulty, is_fallback, response)
    )
    conn.commit()
    conn.close()

def get_settings(key: str) -> str | None:
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None

def set_settings(key: str, value: str) -> None:
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_recent_chat_logs(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM chat_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
