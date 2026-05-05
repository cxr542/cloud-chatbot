from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트(backend/ 상위)의 .env를 항상 읽습니다. (uvicorn CWD와 무관)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

class Settings:
    # 관리자 시스템(포트 8001) 접속용 인증 정보
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "cloud1234!")

    # 향후 실제 연동을 위한 LLM (OpenAI 등) API 키
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

    # SQLite DB 경로
    DB_PATH: str = os.getenv("DB_PATH", "chatbot_data.db")

settings = Settings()
