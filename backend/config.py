from __future__ import annotations

import os

from dotenv import load_dotenv

# 로컬 .env 파일 로드
load_dotenv()

class Settings:
    # 관리자 시스템(포트 8001) 접속용 인증 정보
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "cloud1234!")

    # 향후 실제 연동을 위한 LLM (OpenAI 등) API 키
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

    # SQLite DB 경로
    DB_PATH: str = os.getenv("DB_PATH", "chatbot_data.db")

settings = Settings()
