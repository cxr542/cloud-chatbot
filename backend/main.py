from pathlib import Path
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api.chat import router as chat_router
from backend.api.glossary import router as glossary_router
from backend.api.quiz import router as quiz_router
from backend.api.search import router as search_router
from backend.api.mbti import router as mbti_router
from backend.api.sticker_pack import router as sticker_router
from backend.services.video_kb_ui import extract_video_poster_jpeg, safe_uploads_video_path

app = FastAPI(title="Cloud Learning Chatbot API", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from fastapi.staticfiles import StaticFiles

# 프로젝트 루트 경로 확보
BASE_DIR = Path(__file__).parent.parent

# 지식 베이스 서랍에 노출할 uploads 내 파일 종류(관리자 업로드와 맞춤).
_KB_VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".mpeg", ".mpg", ".m4v"})
_KB_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_KB_MEDIA_MIME_BY_SUFFIX: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".m4v": "video/x-m4v",
}

@app.get("/")
def read_index():
    return FileResponse(str(BASE_DIR / "index.html"))


@app.get("/api/knowledge-documents")
def list_knowledge_documents() -> dict[str, Any]:
    """``uploads`` 안의 PDF·동영상·이미지 파일을 **최근 수정 순**으로 돌려줍니다.

    교육생 화면 지식 베이스 서랍에 그대로 씁니다. PDF만 페이지 수를 세고, 동영상·이미지는
    ``pages`` 를 0으로 두며 ``kind`` 로 구분합니다.

    Returns:
        ``documents``: ``file``, ``pages``, ``mtime``, ``kind`` 키를 가진 객체의 배열입니다.
    """
    upload_dir = BASE_DIR / "uploads"
    items: list[dict[str, Any]] = []
    if not upload_dir.is_dir():
        return {"documents": items}

    rows: list[tuple[Path, str]] = []
    for p in upload_dir.iterdir():
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf == ".pdf":
            rows.append((p, "pdf"))
        elif suf in _KB_VIDEO_SUFFIXES:
            rows.append((p, "video"))
        elif suf in _KB_IMAGE_SUFFIXES:
            rows.append((p, "image"))

    rows.sort(key=lambda pr: pr[0].stat().st_mtime, reverse=True)

    for p, kind in rows:
        try:
            st = p.stat()
            mtime = float(st.st_mtime)
        except OSError:
            continue

        if kind == "pdf":
            try:
                pdf_doc = fitz.open(p)
                page_count = len(pdf_doc)
                pdf_doc.close()
            except (RuntimeError, OSError, ValueError):
                page_count = 0
            items.append(
                {
                    "file": p.name,
                    "pages": int(page_count),
                    "mtime": mtime,
                    "kind": "pdf",
                }
            )
        else:
            items.append(
                {
                    "file": p.name,
                    "pages": 0,
                    "mtime": mtime,
                    "kind": kind,
                }
            )

    return {"documents": items}


@app.get("/api/pdf-page/{filename}/{page_num}")
async def get_pdf_page(filename: str, page_num: int):
    upload_dir = BASE_DIR / "uploads"
    
    # 1. 정확한 파일명 확인
    pdf_path = upload_dir / filename
    
    # 2. .pdf 확장자가 빠진 경우 보완
    if not pdf_path.exists() and not filename.endswith(".pdf"):
        pdf_path = upload_dir / f"{filename}.pdf"
        
    # 3. 그래도 없으면 uploads 폴더의 첫 번째 PDF 파일로 폴백 (데모용 유연성)
    if not pdf_path.exists():
        pdf_files = list(upload_dir.glob("*.pdf"))
        if pdf_files:
            pdf_path = pdf_files[0]
            
    if not pdf_path.exists() or not pdf_path.is_file():
        return Response(status_code=404)
    
    try:
        doc = fitz.open(pdf_path)
        # 페이지 범위 체크
        if page_num < 1 or page_num > len(doc):
            return Response(status_code=400, content="Page out of range")
            
        page = doc.load_page(page_num - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x 고해상도
        img_data = pix.tobytes("png")
        return Response(content=img_data, media_type="image/png")
    except Exception as e:
        return Response(status_code=500, content=str(e))


@app.get("/api/kb-media/{filename}")
async def get_kb_media(filename: str) -> FileResponse:
    """``uploads`` 의 동영상을 브라우저 ``<video src>`` 에서 재생할 수 있게 내려줍니다."""
    video_path = safe_uploads_video_path(BASE_DIR, filename)
    if not video_path:
        raise HTTPException(status_code=404, detail="동영상 파일을 찾을 수 없습니다.")
    mime = _KB_MEDIA_MIME_BY_SUFFIX.get(video_path.suffix.lower(), "application/octet-stream")
    return FileResponse(path=str(video_path), filename=video_path.name, media_type=mime)


@app.get("/api/video-poster/{filename}")
async def kb_video_poster_endpoint(
    filename: str,
    t: float = Query(35.0, ge=0, le=86400, description="포스터로 쓸 장면의 초 단위 시각"),
):
    """ffmpeg 로 한 프레임 JPEG 를 만들어 PDF 미리보기와 비슷한 썸네일로 씁니다(미설치 시 503)."""
    video_path = safe_uploads_video_path(BASE_DIR, filename)
    if not video_path:
        raise HTTPException(status_code=404, detail="동영상 파일을 찾을 수 없습니다.")
    jpeg_bytes = extract_video_poster_jpeg(video_path, t)
    if not jpeg_bytes:
        raise HTTPException(
            status_code=503,
            detail="포스터 생성 실패(ffmpeg·코덱). 동영상 직접 재생은 가능할 수 있습니다.",
        )
    return Response(content=jpeg_bytes, media_type="image/jpeg")


# 정적 파일 마운트 (index.html 및 기타 CSS/JS/이미지 서빙용)
app.mount("/static", StaticFiles(directory=str(BASE_DIR)), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(chat_router)
app.include_router(search_router)
app.include_router(quiz_router)
app.include_router(glossary_router)
app.include_router(sticker_router)
app.include_router(mbti_router)

@app.get("/home")
def read_home():
    return FileResponse(str(BASE_DIR / "home.html"))

# 서버 리로드 강제 트리거용 주석 (수정됨)
