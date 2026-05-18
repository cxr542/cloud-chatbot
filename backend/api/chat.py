from __future__ import annotations

from fastapi import APIRouter

from backend.db.database import log_chat
from backend.dependencies import get_services
from backend.models.schemas import AskQuestionRequest, AskQuestionResponse, ChatRequest, ChatResponse
from backend.services.kb_refs import chunk_to_page_ref, stagger_duplicate_video_thumbs_page_refs

router = APIRouter()


@router.post("/api/ask-question", response_model=AskQuestionResponse)
def ask_question(body: AskQuestionRequest) -> AskQuestionResponse:
    services = get_services()
    reply, chunks = services.rag.ask(body.text, body.difficulty)
    pages = stagger_duplicate_video_thumbs_page_refs(
        [chunk_to_page_ref(c) for c in chunks],
    )
    
    # SQLite에 로그 저장
    is_fallback = not chunks
    log_chat(body.text, body.difficulty, is_fallback, reply)
    
    return AskQuestionResponse(reply=reply, pages=pages, page=pages[0] if pages else None)


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    services = get_services()
    reply, chunks = services.rag.ask(body.message, "초급")

    # SQLite에 로그 저장 (rag.ask는 두 번째 값으로 항상 list를 반환함)
    is_fallback = not chunks
    log_chat(body.message, "초급", is_fallback, reply)
    
    return ChatResponse(reply=reply)
