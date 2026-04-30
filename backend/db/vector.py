from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalChunk:
    page: int
    title: str
    content: str


class FaissVectorStore:
    """
    FAISS 자리.
    현재는 간단한 키워드 검색으로 동작하며, 이후 임베딩+FAISS로 교체.
    """

    def __init__(self, chunks: list[RetrievalChunk]) -> None:
        self._chunks = chunks

    def search(self, query: str, top_k: int = 3) -> list[RetrievalChunk]:
        q = query.lower()
        scored: list[tuple[int, RetrievalChunk]] = []
        for c in self._chunks:
            text = f"{c.title} {c.content}".lower()
            score = sum(1 for token in q.split() if token in text)
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:top_k]]
