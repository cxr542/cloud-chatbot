from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(chat_router)
app.include_router(search_router)
app.include_router(quiz_router)
app.include_router(glossary_router)
app.include_router(progress_router)
