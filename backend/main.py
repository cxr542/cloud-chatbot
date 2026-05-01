from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI
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
