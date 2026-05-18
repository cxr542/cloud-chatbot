from __future__ import annotations

import os
import re
from pathlib import Path

from backend.env_file import load_env_file

# 프로젝트 루트(backend/ 상위)의 .env를 항상 읽습니다. (uvicorn CWD와 무관)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_env_file(_PROJECT_ROOT / ".env", override=True)


def _resolve_db_path() -> str:
    """
    SQLite 경로를 프로젝트 루트 기준으로 고정합니다.

    uvicorn 실행 시 작업 디렉터리(cwd)가 달라지면 상대 경로 ``chatbot_data.db`` 가
    서로 다른 파일을 가리킬 수 있어, 관리자(9998)에서 저장한 프롬프트가 챗봇(9999)에 안 보이는 현상이 납니다.
    """
    raw = os.getenv("DB_PATH", "chatbot_data.db")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return str(p.resolve())


def _int_env_range(key: str, default: int, *, lo: int, hi: int) -> int:
    """환경 변수를 정수로 읽되 범위로 잘라 ``Settings`` 기본값을 안정적으로 만듭니다."""
    try:
        raw = (os.getenv(key) or "").strip()
        # 셸·수동 설정에 `300 #설명` 같이 붙은 경우 보정(주로 숫자 변수용).
        m = re.search(r"\s+#", raw)
        if m:
            raw = raw[: m.start()].rstrip()
        v = int(raw or str(default))
    except ValueError:
        return default
    return max(lo, min(hi, v))


class Settings:
    # 관리자 시스템(포트 8001) 접속용 인증 정보
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "cloud1234!")

    # Gemini(Google) 연동용 키. 우선순위: LLM_API_KEY → GOOGLE_API_KEY → GEMINI_API_KEY
    # (Google AI Studio에서 발급받은 키를 .env에 넣을 때 이름을 헷갈려도 되도록 별칭을 둡니다.)
    LLM_API_KEY: str = (
        (os.getenv("LLM_API_KEY") or "").strip()
        or (os.getenv("GOOGLE_API_KEY") or "").strip()
        or (os.getenv("GEMINI_API_KEY") or "").strip()
    )

    # SQLite DB 경로 (상대 경로는 프로젝트 루트 기준)
    DB_PATH: str = _resolve_db_path()

    # 로컬 RAG 임베딩: HuggingFace 모델 이름(sentence-transformers).
    # 최초 로딩·인코딩 시 다운로드될 수 있어 교육망 환경에서는 사전 캐시를 권장합니다.
    LOCAL_EMBEDDING_MODEL: str = (os.getenv("LOCAL_EMBEDDING_MODEL") or "").strip() or (
        "paraphrase-multilingual-MiniLM-L12-v2"
    )

    # 동영상 업로드 후 Gemini File API가 ACTIVE가 될 때까지 기다리는 최대 시간(초).
    # 짧게 두면 인덱스는 생기지만 「처리 미완」 폴백 문구만 들어가 검색에 거의 안 잡히는 현상이 납니다.
    VIDEO_GEMINI_WAIT_SEC: int = _int_env_range("VIDEO_GEMINI_WAIT_SEC", default=420, lo=120, hi=900)

    # 동영상 요약 전용 모델(멀티모달 File API). 기본은 채팅과 동일 계열 이름이며 .env 로 바꿀 수 있습니다.
    GEMINI_VIDEO_MODEL: str = (os.getenv("GEMINI_VIDEO_MODEL") or "").strip() or "gemini-2.5-flash"

    # 동영상 요약 generate_content RPC 타임아웃(초). peer 504 도 자주 나서 기본값을 크게 두었습니다(클라이언트 최대 대기).
    GEMINI_VIDEO_RPC_TIMEOUT_SEC: int = _int_env_range(
        "GEMINI_VIDEO_RPC_TIMEOUT_SEC", default=1800, lo=180, hi=7200
    )

    # unary generate_content 가 DeadlineExceeded(peers 504)일 때 같은 파일 요약을 몇 번 더 시도할지(중간 대기 포함).
    GEMINI_VIDEO_UNARY_ATTEMPTS: int = _int_env_range(
        "GEMINI_VIDEO_UNARY_ATTEMPTS", default=4, lo=1, hi=10
    )

    # DeadlineExceeded 발생 후 재시도 전 대기(초). 피크 시간대 일시 과부하를 피하기 위함입니다.
    GEMINI_VIDEO_RETRY_WAIT_SEC: int = _int_env_range(
        "GEMINI_VIDEO_RETRY_WAIT_SEC", default=60, lo=15, hi=180
    )

    # 관리자 업로드 동영상 크기 상한(MB). RAM에 전부 읽히므로 너무 크게 두지 마세요(최대 512).
    # 값은 ``max_video_upload_mb`` 프로퍼티로 읽어 ``MAX_VIDEO_UPLOAD_MB`` 를 매번 확인합니다.
    @property
    def max_video_upload_mb(self) -> int:
        """동영상 업로드 허용 크기(MB). 키 이름은 밑줄로 ``MAX_VIDEO_UPLOAD_MB`` (공백 없음)."""
        return _int_env_range("MAX_VIDEO_UPLOAD_MB", default=256, lo=1, hi=512)

    def max_video_upload_bytes(self) -> int:
        """`.env` 의 MB 설정을 바이트로 바꿉니다(동영상 본문·multipart 파트 상한 계산에 씁니다)."""
        return self.max_video_upload_mb * 1024 * 1024

    # 나만의 이모티콘 스티커 팩 생성(Nano Banana / Gemini 네이티브 이미지).
    GEMINI_STICKER_MODEL: str = (os.getenv("GEMINI_STICKER_MODEL") or "").strip() or "gemini-2.5-flash-image"

    # 참조 사진 업로드 상한(MB). 한 장만 받습니다.
    MAX_STICKER_UPLOAD_MB: int = _int_env_range("MAX_STICKER_UPLOAD_MB", default=8, lo=1, hi=20)

    def max_sticker_upload_bytes(self) -> int:
        """스티커 참조 이미지 한 장의 최대 업로드 바이트입니다."""
        return self.MAX_STICKER_UPLOAD_MB * 1024 * 1024

    # Gemini 입력 직전, 가장 긴 변 픽셀(큰 업로드를 줄입니다).
    GEMINI_STICKER_REF_MAX_EDGE: int = _int_env_range("GEMINI_STICKER_REF_MAX_EDGE", default=1024, lo=384, hi=2048)

    # 12종 패널 병렬 생성 시 동시 요청 개수 상한입니다.
    GEMINI_STICKER_CONCURRENCY: int = _int_env_range("GEMINI_STICKER_CONCURRENCY", default=2, lo=1, hi=8)

    # 패널(표정) 한 장당 최대 대기 시간(초).
    GEMINI_STICKER_PER_IMAGE_TIMEOUT_SEC: int = _int_env_range(
        "GEMINI_STICKER_PER_IMAGE_TIMEOUT_SEC",
        default=180,
        lo=45,
        hi=900,
    )

    # 이미지만 오지 않았을 때(텍스트로 STOP)·일시 과부하 대비 같은 패널을 몇 번까지 다시 호출할지입니다.
    GEMINI_STICKER_PANEL_ATTEMPTS: int = _int_env_range(
        "GEMINI_STICKER_PANEL_ATTEMPTS",
        default=3,
        lo=1,
        hi=8,
    )


settings = Settings()
