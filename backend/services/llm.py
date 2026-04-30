from __future__ import annotations

from backend.db.vector import RetrievalChunk


class LLMService:
    def answer(self, query: str, contexts: list[RetrievalChunk], difficulty: str) -> str:
        if not contexts:
            return "질문 감사합니다. 이 문서 기준으로 핵심 용어부터 함께 정리해볼까요?"
        head = contexts[0]
        if difficulty == "고급":
            return f"{head.title} 중심으로 설명하면, {head.content} 운영 관점에서 정책/가용성/비용 영향을 함께 보시면 좋습니다."
        if difficulty == "중급":
            return f"{head.title} 기준으로 보면 {head.content} 입니다."
        return f"{head.title}: {head.content}"
