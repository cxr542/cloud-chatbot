from __future__ import annotations

from fastapi import APIRouter, Query

from backend.dependencies import get_services
from backend.models.schemas import SourceItem, SummaryRequest, SummaryResponse
from backend.services.kb_refs import chunk_to_source_item, stagger_duplicate_video_thumbs_source_items
from backend.services.llm import source_search_card_snippet
from backend.services.summary import build_summary

router = APIRouter()

# Q&A(RagService.ask)와 동일한 상한으로 맞추면 같은 데이터에서 같은 우선순위로 페이지를 고릅니다.
_SOURCE_SEARCH_TOP_K = 10


@router.get("/api/search-source", response_model=list[SourceItem])
def search_source(
    keyword: str = Query(..., min_length=1, description="검색어·짧은 질문(임베딩+BM25 검색 입력)"),
) -> list[SourceItem]:
    """
    교육생 **출처 찾기** 탭용 페이지 검색입니다.

    ``FileVectorStore.search`` 와 동일하게, 로컬 임베딩이 있으면 의미 유사도를 우선하고
    부족한 슬롯은 BM25·부분일치 폴백으로 채웁니다. 별도의 단순 LIKE/키워드 전용 테이블은 없습니다.
    """
    services = get_services()
    chunks = services.rag.vector_store.search(keyword, top_k=_SOURCE_SEARCH_TOP_K)
    if not chunks:
        return []
    return stagger_duplicate_video_thumbs_source_items(
        [
            chunk_to_source_item(
                c,
                source_search_card_snippet(c.content, keyword, max_len=400),
            )
            for c in chunks
        ],
    )


@router.post("/api/summary", response_model=SummaryResponse)
def summary(body: SummaryRequest) -> SummaryResponse:
    services = get_services()
    return build_summary(services.rag.vector_store, services.rag.llm_service, body.mode, body.topic, body.start, body.end)
