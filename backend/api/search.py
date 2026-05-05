from __future__ import annotations

from fastapi import APIRouter, Query

from backend.dependencies import get_services
from backend.models.schemas import SourceItem, SummaryRequest, SummaryResponse
from backend.services.summary import build_summary

router = APIRouter()


@router.get("/api/search-source", response_model=list[SourceItem])
def search_source(keyword: str = Query(..., min_length=1)) -> list[SourceItem]:
    services = get_services()
    chunks = services.rag.vector_store.search(keyword, top_k=5)
    if not chunks:
        return [SourceItem(p=1, t="클라우드 개요", d="클라우드 핵심 개념 소개")]
    return [SourceItem(p=c.page, t=c.title, d=c.content) for c in chunks]


@router.post("/api/summary", response_model=SummaryResponse)
def summary(body: SummaryRequest) -> SummaryResponse:
    services = get_services()
    return build_summary(services.rag.vector_store, services.rag.llm_service, body.mode, body.topic, body.start, body.end)
