from pathlib import Path
import fitz
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api.chat import router as chat_router
from backend.api.glossary import router as glossary_router
from backend.api.progress import router as progress_router
from backend.api.quiz import router as quiz_router
from backend.api.search import router as search_router

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

@app.get("/")
def read_index():
    return FileResponse(str(BASE_DIR / "index.html"))

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

# 정적 파일 마운트 (index.html 및 기타 CSS/JS/이미지 서빙용)
app.mount("/static", StaticFiles(directory=str(BASE_DIR)), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(chat_router)
app.include_router(search_router)
app.include_router(quiz_router)
app.include_router(glossary_router)
app.include_router(progress_router)

@app.get("/home")
def read_home():
    return FileResponse(str(BASE_DIR / "home.html"))

# 서버 리로드 강제 트리거용 주석 (수정됨)
