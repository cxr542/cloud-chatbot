from __future__ import annotations

from backend.db.database import get_settings
from backend.db.vector import RetrievalChunk


class LLMService:
    def answer(self, query: str, contexts: list[RetrievalChunk], difficulty: str) -> str:
        prompt = get_settings("system_prompt") or "친절한 챗봇입니다."
        
        if not contexts:
            return f"[{prompt}] 질문에 맞는 문서를 찾지 못했습니다. 핵심 용어부터 다시 검색해 볼까요?"
            
        head = contexts[0]
        if difficulty == "고급":
            return f"[{prompt}] {head.title} 중심으로 설명하면, {head.content} 운영 관점에서 정책/가용성/비용 영향을 함께 보시면 좋습니다."
        if difficulty == "중급":
            return f"[{prompt}] {head.title} 기준으로 보면 {head.content} 입니다."
        return f"[{prompt}] {head.title}: {head.content}"
