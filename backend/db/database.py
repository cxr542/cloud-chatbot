from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from backend.config import settings

# backend.db.database → backend → 프로젝트 루트 (my_prompt.txt 위치)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# llm._STOCK_SYSTEM_PROMPTS_FROZEN 과 동일 — 관리자 UI 기본 문구만 있을 때 시드 대상으로 봅니다.
_STOCK_SYSTEM_PROMPTS = frozenset(
    {
        "친절한 클라우드 학습 도우미입니다.",
        "친절하고 명확하게 답변해주는 클라우드 학습 도우미입니다.",
    }
)

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


def _is_stock_system_prompt(text: str | None) -> bool:
    """DB에 남은 값이 코드 기본 문구이면 True — 배포 시 파일·환경 변수로 덮어쓸 수 있습니다."""
    return (text or "").strip() in _STOCK_SYSTEM_PROMPTS


def seed_system_prompt_from_bootstrap() -> None:
    """재배포·빈 DB일 때 관리자가 넣어 둔 챗봇 성격을 복구합니다.

    우선순위: 환경 변수 ``SYSTEM_PROMPT`` → ``SYSTEM_PROMPT_FILE``(기본 ``my_prompt.txt``).
    DB에 이미 커스텀 프롬프트가 있으면 건드리지 않습니다(Render에서 UI로 수정한 내용 보존).

    Returns:
        없음.
    """
    current = get_settings("system_prompt")
    if current and not _is_stock_system_prompt(current):
        return

    env_text = (os.getenv("SYSTEM_PROMPT") or "").strip()
    if env_text:
        set_settings("system_prompt", env_text)
        return

    rel = (os.getenv("SYSTEM_PROMPT_FILE") or "my_prompt.txt").strip()
    path = Path(rel).expanduser()
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.is_file():
        return

    file_text = path.read_text(encoding="utf-8").strip()
    if file_text:
        set_settings("system_prompt", file_text)


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


# 모듈 로드 시 DB 초기화 후, 기본 문구만 있으면 my_prompt.txt 등으로 채웁니다.
init_db()
seed_system_prompt_from_bootstrap()
