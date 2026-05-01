from __future__ import annotations

from fastapi import APIRouter

from backend.db.database import log_chat
from backend.dependencies import get_services
from backend.models.schemas import AskQuestionRequest, AskQuestionResponse, ChatRequest, ChatResponse, PageRef

router = APIRouter()


@router.post("/api/ask-question", response_model=AskQuestionResponse)
def ask_question(body: AskQuestionRequest) -> AskQuestionResponse:
    services = get_services()
    reply, chunk = services.rag.ask(body.text, body.difficulty)
    page = PageRef(no=chunk.page, title=chunk.title) if chunk else None
    
    # SQLite에 로그 저장
    is_fallback = chunk is None
    log_chat(body.text, body.difficulty, is_fallback, reply)
    
    return AskQuestionResponse(reply=reply, page=page)


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    services = get_services()
    reply, chunk = services.rag.ask(body.message, "초급")
    
    # SQLite에 로그 저장
    is_fallback = chunk is None
    log_chat(body.message, "초급", is_fallback, reply)
    
    return ChatResponse(reply=reply)
