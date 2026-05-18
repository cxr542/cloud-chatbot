from __future__ import annotations

from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.services.llm import LLMService, is_greeting_query


class RagService:
    def __init__(self, vector_store: FileVectorStore, llm_service: LLMService) -> None:
        self.vector_store = vector_store
        self.llm_service = llm_service

    def ask(self, query: str, difficulty: str = "초급") -> tuple[str, list[RetrievalChunk]]:
        # 인사말 예외 처리: 검색 생략 및 출처 카드(버튼) 숨김 (판별 로직은 llm과 동일)
        if is_greeting_query(query):
            answer = self.llm_service.answer(query, [], difficulty)
            return answer, []
            
        # 검색 후보는 넉넉히 두고, LLM 입력은 상위만 넣습니다. PDF가 많으면 동영상·이미지 청크가
        # 뒤로 밀릴 수 있어 후보 수·전달 개수를 다소 여유 있게 잡습니다.
        contexts = self.vector_store.search(query, top_k=18)

        # 검색된 결과가 없을 경우 예외 처리
        if not contexts:
            return "죄송합니다. 질문과 관련된 내용을 문서에서 찾을 수 없습니다.", []

        answer = self.llm_service.answer(query, contexts[:12], difficulty)

        # 중복된 페이지 제거 및 상위 출처만 프론트엔드에 전달
        unique_contexts = []
        seen = set()
        for c in contexts:
            key = f"{c.title}-{c.page}"
            if key not in seen:
                unique_contexts.append(c)
                seen.add(key)

        return answer, unique_contexts[:6]
