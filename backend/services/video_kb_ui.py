"""동영상 지식 출처(UI)용: 파일명 안전 처리·구간 문자열 파싱·파일 경로 확인.

브라우저에서 썸네일·플레이어를 붙일 때 재생 시각(poster)·seek 위치 계산에 씁니다.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import unicodedata
import zlib
from pathlib import Path

logger = logging.getLogger(__name__)

_KB_VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".mpeg", ".mpg", ".m4v"})


def kb_video_upload_basename(chunk_title: str) -> str | None:
    """청크 ``title``(예: ``강의.mp4 (동영상)``)에서 uploads 안 실제 파일명만 뽑습니다.

    Args:
        chunk_title: ``RetrievalChunk.title``.

    Returns:
        ``foo.mp4`` 형태 문자열 또는 형식에 맞지 않으면 None.
    """
    t = unicodedata.normalize("NFC", (chunk_title or "").strip())
    if not t.endswith("(동영상)") or "(동영상)" not in t:
        return None
    raw = re.sub(r"\s*\(동영상\)\s*$", "", t, flags=re.IGNORECASE).strip()
    return raw or None


def safe_uploads_video_path(project_root: Path, filename: str) -> Path | None:
    """요청 문자열에서 디렉터리 탐색을 막고 ``uploads`` 아래 허용된 동영상만 경로를 돌려줍니다.

    Args:
        project_root: 프로젝트 루트(``backend`` 상위).
        filename: 클라이언트가 넘긴 파일명(URL 디코드 뒤).

    Returns:
        존재·형식 조건을 만족하면 ``Path``, 아니면 None.
    """
    name = unicodedata.normalize("NFC", Path(str(filename).strip()).name)
    if not name:
        return None
    if ".." in name or "/" in name or "\\" in name:
        return None
    suf = Path(name).suffix.lower()
    if suf not in _KB_VIDEO_SUFFIXES:
        return None
    cand = (project_root / "uploads" / name).resolve()
    root = (project_root / "uploads").resolve()
    try:
        cand.relative_to(root)
    except ValueError:
        return None
    except OSError:
        return None
    try:
        if not cand.is_file():
            return None
    except OSError:
        return None
    return cand


def parse_video_time_band_seconds(band: str) -> tuple[float | None, float | None]:
    """저장된 ``ref_time_band`` 문자열에서 재생 시작·끝(초)을 추출합니다.

    모델이 ``약 01:05–06:40 (추정)``, ``영상 전체`` 등으로 남긴 값을 허술하게 허용합니다.

    Args:
        band: ``RetrievalChunk.ref_time_band`` 등.

    Returns:
        ``(시작초 또는 None, 끝초 또는 None)``. 알 수 없으면 둘 다 None.
    """
    b0 = unicodedata.normalize("NFC", (band or "").strip())
    if not b0:
        return None, None
    if re.fullmatch(r"영상\s*전체\s*", b0, flags=re.IGNORECASE):
        return None, None

    norm = (
        b0.replace("–", "-")
        .replace("—", "-")
        .replace("〜", "~")
        .replace("∼", "~")
    )

    def _clock_to_seconds(tok: str) -> float | None:
        t = (tok or "").strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
        if not m:
            return None
        if m.group(3) is not None:
            h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return float(h * 3600 + mi * 60 + sec)
        mi, sec = int(m.group(1)), int(m.group(2))
        return float(mi * 60 + sec)

    m_rng = re.search(
        r"(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-|~)\s*(\d{1,2}:\d{2}(?::\d{2})?)",
        norm,
    )
    if m_rng:
        a = _clock_to_seconds(m_rng.group(1))
        z = _clock_to_seconds(m_rng.group(2))
        if a is not None and z is not None and z >= a:
            return a, z
        if a is not None:
            return a, None
    m_one = re.search(r"(?:약|부터)?\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:경|쯤|부근)", norm)
    if m_one:
        a = _clock_to_seconds(m_one.group(1))
        return (a, None) if a is not None else (None, None)
    return None, None


def default_thumb_seek_sec(start_sec: float | None) -> float:
    """포스터(썸네일) 한 장 뽑을 때 ``-ss`` 위치입니다.

    Args:
        start_sec: 구간 시작으로 알려진 초(없음이면 브라우저 검은 화면을 줄이기 위해 기본 오프셋).

    Returns:
        0 이상 초 단위 값.
    """
    if start_sec is not None and start_sec >= 0:
        return float(start_sec)
    return 35.0


def poster_seek_seconds(
    *,
    media_filename: str | None,
    start_sec: float | None,
    band_label: str | None = None,
) -> float:
    """UI용 대표 장면 시각(초). 구간 시작이 없으면 **파일명·구간 문자열** 해시로 분산합니다.

    서로 다른 동영상 파일이 모두 동일한 초(예: 35s)를 쓰면 썸네일·플레이어 시작이 똑같이 보이므로,
    지식 베이스에 편이 여러 개 있을 때 구분이 되게 합니다.

    Args:
        media_filename: uploads 기준 파일명(예: ``강의.mp4``).
        start_sec: 파싱된 구간 시작 초(있으면 우선).
        band_label: ``ref_time_band`` 원문(있으면 같은 파일도 약간 다른 시각 허용).

    Returns:
        0 이상의 ``ffmpeg -ss`` 또는 ``<video>`` 현재 시각에 쓸 초.
    """
    if start_sec is not None and start_sec >= 0:
        return float(start_sec)
    name = unicodedata.normalize("NFC", (media_filename or "").strip())
    if not name:
        return 35.0
    h1 = zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF
    b = unicodedata.normalize("NFC", (band_label or "").strip())
    h2 = zlib.crc32(b.encode("utf-8")) & 0xFFFFFFFF if b else 0
    spread = int((h1 ^ (h2 * 33)) % 191)
    return 9.0 + float(spread)


def extract_video_poster_jpeg(video_path: Path, seek_sec: float) -> bytes | None:
    """ffmpeg 으로 지정 시각 근처 1프레임을 JPEG 바이트로 뽑습니다.

    교육·데모 서버에서는 ffmpeg 미설치일 수 있어 None 으로 실패 표시 후 프론트가 비디오만 씁니다.

    Args:
        video_path: 실제 uploads 내 동영상 경로.
        seek_sec: 키프레임 전 스킵 시간(초).

    Returns:
        JPEG 원시 바이트 또는 ffmpeg 없음/실패 시 None.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        logger.debug("ffmpeg 없음: 포스터 생략")
        return None
    seek = max(0.0, float(seek_sec))
    cmd = [
        ff,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(seek),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-an",
        "-vf",
        "scale=min(854\\,iw):-2",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg 포스터 타임아웃: %s", video_path.name)
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or b"")[:300]
        logger.debug("ffmpeg 포스터 실패(%s): %s", video_path.name, tail)
        return None
    out = proc.stdout or b""
    return out if len(out) >= 400 else None
