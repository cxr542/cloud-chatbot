from __future__ import annotations

from fastapi import APIRouter, Query

from backend.dependencies import get_services
from backend.models.schemas import SourceItem, SummaryRequest, SummaryResponse

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
    if body.mode == "topic":
        topic = body.topic or "클라우드 보안"
        return SummaryResponse(text=f"[주제: {topic}] 핵심은 인증(SSO/MFA)과 인가(RBAC/ABAC) 구분입니다.", pages=[73, 74, 76, 77])
    if body.mode == "range":
        start = body.start or "1"
        end = body.end or "10"
        return SummaryResponse(text=f"[{start}~{end}p] 범위에서는 HA/DR, 관측성, 비용최적화 흐름이 순차적으로 다뤄집니다.", pages=[69, 70, 71, 79])
    return SummaryResponse(text="전체 요약: 인증/인가, 가용성, 운영 관측성, 비용관리, 컨테이너 운영이 문서의 5대 축입니다.", pages=[52, 69, 70, 73, 76, 79])
