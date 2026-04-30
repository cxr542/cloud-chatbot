from __future__ import annotations

from backend.db.vector import FaissVectorStore, RetrievalChunk
from backend.services.llm import LLMService


class RagService:
    def __init__(self, vector_store: FaissVectorStore, llm_service: LLMService) -> None:
        self.vector_store = vector_store
        self.llm_service = llm_service

    def ask(self, query: str, difficulty: str = "초급") -> tuple[str, RetrievalChunk | None]:
        contexts = self.vector_store.search(query, top_k=2)
        answer = self.llm_service.answer(query, contexts, difficulty)
        return answer, contexts[0] if contexts else None
