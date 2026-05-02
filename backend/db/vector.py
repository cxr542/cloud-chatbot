from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RetrievalChunk:
    page: int
    title: str
    content: str


class FileVectorStore:
    """
    디스크 기반(JSON)의 임시 벡터 저장소입니다.
    어드민 서버와 챗봇 서버가 data/vector.json 파일을 공유하여 데이터를 읽고 씁니다.
    """

    def __init__(self, filepath: str = "data/vector.json") -> None:
        self.filepath = filepath
        self._ensure_file()

    def _ensure_file(self) -> None:
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump([], f)

    def load_chunks(self) -> list[RetrievalChunk]:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [RetrievalChunk(**item) for item in data]
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def save_chunks(self, chunks: list[RetrievalChunk]) -> None:
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump([asdict(c) for c in chunks], f, ensure_ascii=False, indent=2)

    def add_chunk(self, chunk: RetrievalChunk) -> None:
        chunks = self.load_chunks()
        chunks.append(chunk)
        self.save_chunks(chunks)

    def delete_chunk(self, title: str) -> None:
        chunks = self.load_chunks()
        filtered = [c for c in chunks if c.title != title]
        self.save_chunks(filtered)

    def search(self, query: str, top_k: int = 3) -> list[RetrievalChunk]:
        import re
        q_clean = re.sub(r'[^\w\s]', '', query).lower()
        tokens = q_clean.split()
        
        scored: list[tuple[int, RetrievalChunk]] = []
        chunks = self.load_chunks()
        for c in chunks:
            text = f"{c.title} {c.content}".lower()
            # 단어가 단순히 포함되었는지가 아니라, 얼마나 많이 등장하는지(빈도수)를 점수로 계산합니다.
            score = sum(text.count(token) for token in tokens)
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:top_k]]
