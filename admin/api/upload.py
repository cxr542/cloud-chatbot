import logging
import unicodedata

import fitz  # PyMuPDF
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from starlette.datastructures import UploadFile

from backend.config import settings
from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.services.local_embeddings import chunk_to_embed_text, embed_texts_safe
from backend.services.media_extract import (
    extract_text_from_image_bytes_for_kb,
    extract_text_from_video_path_for_kb,
    is_video_kb_summarized_content,
    strip_kb_ref_time_footer,
    video_mime_type_for_suffix,
)

router = APIRouter(prefix="/api/admin/docs", tags=["docs"])
BASE_DIR = Path(__file__).resolve().parent.parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def _multipart_max_part_bytes() -> int:
    """동영상 설정(MB)보다 여유 있게 두어 multipart 한 파트가 잘리지 않게 합니다."""
    return settings.max_video_upload_bytes() + 16 * 1024 * 1024


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mpeg", ".mpg", ".m4v"}

logger = logging.getLogger(__name__)


def _configure_docs_logger() -> None:
    """관리자 터미널에서 목록 조회·동영상 후처리 로그를 볼 수 있게 스트림 핸들러를 붙입니다.

    Uvicorn만 켜 두면 ``logger.info`` 가 안 보일 수 있어, 이 모듈 전용으로 한 번만 설정합니다.
    """
    if logger.handlers:
        return
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[Admin-Docs] %(levelname)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    logger.propagate = False


_configure_docs_logger()


def _find_usable_video_kb_chunk(chunks: list[RetrievalChunk], chunk_title: str) -> RetrievalChunk | None:
    """같은 제목의 동영상 청크 중 Gemini 요약이 이미 성공한 항목을 찾습니다.

    Args:
        chunks: ``vector.json`` 에서 읽은 전체 청크.
        chunk_title: 예: ``강의.mp4 (동영상)``.

    Returns:
        재사용 가능한 청크 또는 None.
    """
    for c in chunks:
        if c.title != chunk_title:
            continue
        if is_video_kb_summarized_content(c.content or ""):
            return c
    return None


def _form_flag_true(form, key: str) -> bool:
    """multipart 폼의 ``force`` 등 불리언 플래그를 해석합니다."""
    raw = form.get(key)
    if raw is None:
        return False
    val = raw if isinstance(raw, str) else getattr(raw, "filename", None) or str(raw)
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _index_video_kb_after_upload(save_path: Path, filename: str, mime_type: str | None) -> None:
    """저장된 동영상에 대해 **LLM(클라우드) 요약**·로컬 임베딩을 실행하고 청크를 ``vector.json`` 에 넣습니다.

    업로드 HTTP 응답을 기다리지 않게 하기 위해 BackgroundTasks 에서 실행합니다.

    같은 파일명 동영상을 여러 번 올린 경우 제목이 같아지므로, 저장 시 **동일 제목 청크는
    모두 지운 뒤 최신 결과 하나만** 남겨 검색 목록 중복을 막습니다.

    Args:
        save_path: ``uploads`` 아래 저장 경로입니다.
        filename: 업로드 시의 파일명(청크 제목에 씁니다).
        mime_type: 멀티모달 업로드용 MIME(선택).
    """
    title = f"{filename} (동영상)"
    try:
        raw = extract_text_from_video_path_for_kb(save_path, filename, mime_type=mime_type) or ""
        body, band_parsed = strip_kb_ref_time_footer(raw)
        ref_band = (band_parsed or "").strip() or "영상 전체"
        body = body[:6000]
        c0 = RetrievalChunk(
            page=1,
            title=title,
            content=body,
            embedding=None,
            ref_time_band=ref_band,
        )
        vecs = embed_texts_safe(
            [chunk_to_embed_text(c0.title, c0.content, max_content_chars=4500)],
        )
        v0 = vecs[0] if vecs else None
        merged = RetrievalChunk(
            c0.page,
            c0.title,
            c0.content,
            v0,
            c0.ref_time_band,
        )

        store = FileVectorStore()
        chunks = store.load_chunks()
        chunks = [c for c in chunks if c.title != title]
        chunks.append(merged)
        store.save_chunks(chunks)
        if merged.content.startswith("[동영상 자료]"):
            logger.error(
                "동영상 플레이스홀더만 vector.json 에 반영됨(실제 요약 없음): %s — 관리자 터미널의 Gemini 관련 WARN/ERROR 를 확인하세요.",
                filename,
            )
            print(
                f"[Admin-Docs] ⚠ 동영상 요약 미완료(플레이스홀더만 저장): {filename} — 터미널 WARN 로그 참고 후 재업로드",
                flush=True,
            )
        else:
            logger.info("동영상 지식베이스 반영 완료(동일 제목 기존 청크 정리 후 1건): %s", filename)
    except Exception:
        logger.exception("동영상 지식베이스 후처리 실패: %s", filename)


def _safe_upload_name(raw: str) -> str:
    """업로드 파일명에서 디렉터리 탐색 등을 제거하고 기본 이름만 반환합니다."""
    return Path(raw or "unnamed").name


def _kb_filename_for_disk_and_titles(raw_name: str) -> str:
    """
    업로드 파일명을 지식 베이스 제목·저장 경로에 쓸 문자열로 고릅니다.

    macOS(APFS) 등에서 NFD(자모 분해)로 들어온 한글 파일명은 검색 토큰이 어긋날 수 있어
    표시·저장 전에 NFC 로 맞춥니다(이미 NFC면 그대로 둡니다).

    Args:
        raw_name: 브라우저가 넘긴 원본 파일 이름.

    Returns:
        경로 탐색 공격을 막은 파일명의 NFC 정규형.
    """
    return unicodedata.normalize("NFC", _safe_upload_name(raw_name))


def _admin_chunk_issue_hint(c: RetrievalChunk) -> str | None:
    """
    플레이스홀더만 있는 동영상·이미지 청크인지 판별해 관리자 UI에 짧은 안내 문구를 제공합니다.

    정상적인 요약본은 동영상이 ``[주제]`` 로, 이미지는 모델 응답 문자열로 시작하는 경우가 많아
    접두사 ``[동영상 자료]`` · ``[이미지 자료]`` 로 시작하면 media_extract 단계에서
    채우지 못한 상태로 봅니다.

    Args:
        c: ``vector.json`` 에서 읽은 청크 하나.

    Returns:
        출처가 불완전하면 한글 안내, 정상 추정이면 None.
    """
    body = (c.content or "").strip()
    title = c.title or ""
    if "(동영상)" in title and body.startswith("[동영상 자료]"):
        return (
            "동영상 요약이 만들어지지 않은 상태입니다. 504가 반복되면 Google 쪽 처리 한계일 수 있어요. "
            "GEMINI_VIDEO_RPC_TIMEOUT_SEC·GEMINI_VIDEO_UNARY_ATTEMPTS·GEMINI_VIDEO_RETRY_WAIT_SEC·"
            "VIDEO_GEMINI_WAIT_SEC 을 확인하고, 짧은 클립 또는 대본(txt) 업로드를 고려해 주세요."
        )
    if "(이미지)" in title and body.startswith("[이미지 자료]"):
        return "이미지 설명이 만들어지지 않았습니다. API 키·이미지 형식·Pillow 설치 여부를 확인하세요."
    return None


@router.get("/")
def list_docs():
    """
    로컬 ``vector.json`` 기준 청크 목록을 돌려줍니다.

    브라우저 부하를 줄이기 위해 **임베딩 벡터는 제외**하고, ``has_embedding`` 만 표시합니다.
    동영상·PDF 전체 텍스트는 길어질 수 있어 목록에서는 **앞부분만** 넣습니다(실제 검색/Q&A는 ``vector.json`` 전체 텍스트).
    """
    # 관리자 표는 미리보기 100자만 쓰지만, 브라우저·네트워크 한도를 위해 응답당 본문은 상한을 둡니다.
    max_list_body = 4000

    store = FileVectorStore()
    chunks = store.load_chunks()
    slim = []
    for c in chunks:
        raw_body = c.content or ""
        if len(raw_body) > max_list_body:
            body = raw_body[:max_list_body] + "\n…"
        else:
            body = raw_body
        row: dict = {
            "page": c.page,
            "title": c.title,
            "content": body,
            "has_embedding": bool(c.embedding),
            "ref_time_band": getattr(c, "ref_time_band", None),
        }
        if hint := _admin_chunk_issue_hint(c):
            row["issue_hint"] = hint
        slim.append(row)
    logger.info("지식 목록 조회 — 청크 %d건 (GET /api/admin/docs/)", len(slim))
    # 로깅 설정과 무관하게 ``run.py`` 파이프·단독 터미널 모두에서 한 줄은 보이게 합니다.
    print(f"[Admin-Docs] 지식 목록 조회 — 청크 {len(slim)}건 (GET /api/admin/docs/)", flush=True)
    return {"chunks": slim}


@router.delete("/{title}")
def delete_doc(title: str):
    store = FileVectorStore()
    store.delete_chunk(title)
    return {"status": "deleted", "title": title}


@router.post("/upload")
async def upload_doc(request: Request, background_tasks: BackgroundTasks):
    """
    PDF · TXT · 이미지 · 동영상을 저장하고, 지식 베이스용 텍스트 청크를 ``vector.json`` 에 추가합니다.

    - PDF: 페이지별 텍스트를 묶어 **한 번의 배치 임베딩** 후 저장합니다. TXT 는 전체를 한 청크로 넣습니다.
    - 이미지: **LLM 모델**(``.env`` 의 ``LLM_API_KEY`` 등)로 설명 문구를 만든 뒤 동일하게 임베딩합니다.
    - 동영상: 파일만 먼저 저장하고 즉시 응답한 뒤, **LLM 기반 요약·분석은 백그라운드**에서 끝냅니다.
      브라우저 ``Failed to fetch``(연결 타임아웃)를 피하기 위함입니다.
    - 임베딩 모델 오류 시에도 **텍스트 청크는 저장**합니다(BM25 폴백 검색).
    """
    form = await request.form(max_part_size=_multipart_max_part_bytes())
    uf_raw = form.get("file")
    if not isinstance(uf_raw, UploadFile):
        raise HTTPException(
            status_code=400,
            detail='multipart 폼에 업로드 파일 필드 "file"이 없거나 형식이 잘못되었습니다.',
        )
    uf: UploadFile = uf_raw
    content = await uf.read()
    filename = _kb_filename_for_disk_and_titles(uf.filename or "")
    suffix = Path(filename).suffix.lower()
    store = FileVectorStore()

    if suffix == ".pdf":
        # 파일 저장
        save_path = UPLOAD_DIR / filename
        with open(save_path, "wb") as f:
            f.write(content)

        # PDF 처리
        doc = fitz.open(stream=content, filetype="pdf")
        to_add: list[RetrievalChunk] = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text()
            if text.strip():
                body = text[:2000]
                title = f"{filename} (p.{page_num + 1})"
                to_add.append(RetrievalChunk(page=page_num + 1, title=title, content=body))
        texts = [chunk_to_embed_text(c.title, c.content) for c in to_add]
        vecs = embed_texts_safe(texts)
        with_emb = [
            RetrievalChunk(c.page, c.title, c.content, vecs[i] if i < len(vecs) else None)
            for i, c in enumerate(to_add)
        ]
        store.add_chunks(with_emb)
        return {"status": "uploaded", "filename": filename, "pages": len(doc)}

    elif suffix == ".txt":
        # TXT 처리
        text = content.decode("utf-8")
        body = text[:2000]
        c0 = RetrievalChunk(page=1, title=filename, content=body)
        vecs = embed_texts_safe([chunk_to_embed_text(c0.title, c0.content)])
        v0 = vecs[0] if vecs else None
        store.add_chunks([RetrievalChunk(c0.page, c0.title, c0.content, v0)])
        return {"status": "uploaded", "filename": filename}

    elif suffix in _IMAGE_SUFFIXES:
        save_path = UPLOAD_DIR / filename
        with open(save_path, "wb") as f:
            f.write(content)
        body = extract_text_from_image_bytes_for_kb(content, filename)[:2000]
        chunk_title = f"{filename} (이미지)"
        c0 = RetrievalChunk(page=1, title=chunk_title, content=body)
        vecs = embed_texts_safe([chunk_to_embed_text(c0.title, c0.content)])
        v0 = vecs[0] if vecs else None
        merged = RetrievalChunk(c0.page, c0.title, c0.content, v0, ref_time_band=None)
        chunks_im = store.load_chunks()
        chunks_im = [c for c in chunks_im if c.title != chunk_title]
        chunks_im.append(merged)
        store.save_chunks(chunks_im)
        return {"status": "uploaded", "filename": filename, "kind": "image"}

    elif suffix in _VIDEO_SUFFIXES:
        max_bytes = settings.max_video_upload_bytes()
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"동영상은 {settings.max_video_upload_mb}MB 이하만 업로드할 수 있습니다. "
                    '(늘리려면 .env 에 밑줄 포함 키 이름으로 MAX_VIDEO_UPLOAD_MB=숫자 를 두세요. 최대 512)'
                ),
            )
        save_path = UPLOAD_DIR / filename
        with open(save_path, "wb") as f:
            f.write(content)
        mime = video_mime_type_for_suffix(suffix)
        chunk_title = f"{filename} (동영상)"
        force_reindex = _form_flag_true(form, "force")
        skip_gemini = settings.GEMINI_VIDEO_SKIP_IF_INDEXED and not force_reindex
        if skip_gemini:
            existing = _find_usable_video_kb_chunk(store.load_chunks(), chunk_title)
            if existing is not None:
                logger.info(
                    "동영상 Gemini 요약 생략(기존 청크 재사용): %s — 재요약은 force=1",
                    filename,
                )
                return {
                    "status": "skipped",
                    "filename": filename,
                    "kind": "video",
                    "chunk_title": chunk_title,
                    "message": (
                        "파일은 저장되었고, 지식 베이스에 이미 요약된 동영상이 있어 Gemini 호출을 건너뛰었습니다. "
                        "다시 요약하려면 업로드 시 force=1 을 함께 보내세요."
                    ),
                }
        background_tasks.add_task(_index_video_kb_after_upload, save_path, filename, mime)
        logger.info("동영상 업로드 접수 — 저장 완료, 백그라운드 요약 시작: %s", filename)
        return {
            "status": "accepted",
            "filename": filename,
            "kind": "video",
            "chunk_title": chunk_title,
            "message": (
                "동영상은 저장되었습니다. 지금부터 서버에서 LLM 모델이 요약을 만들고 있어요(약 1~수 분)."
                " 목록의 「목록 새로고침」을 눌러 청크가 생겼는지 확인하면 됩니다."
            ),
        }

    else:
        raise HTTPException(
            status_code=400,
            detail="지원 형식이 아닙니다. pdf, txt, png, jpg, jpeg, gif, webp, mp4, webm, mov, mpeg, mpg, m4v 등을 사용하세요.",
        )
