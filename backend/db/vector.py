from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


def _project_root() -> Path:
    """이 파일(`backend/db/vector.py`) 위치를 기준으로 프로젝트 루트 디렉터리를 반환합니다."""
    return Path(__file__).resolve().parent.parent.parent


def _default_vector_path() -> str:
    """실행 시 현재 작업 디렉터리와 무관하게 사용할 `data/vector.json`의 절대 경로를 반환합니다."""
    return str(_project_root() / "data" / "vector.json")


def _normalize_tokens(text: str) -> list[str]:
    """
    질문·문서 텍스트를 BM25 검색용 토큰 리스트로 바꿉니다.

    유니코드에서 `\\w`는 한글·영문·숫자 등 단어 문자로 인식되므로,
    한글과 라틴·숫자 사이에 공백을 넣어 분리한 뒤 구두점을 공백으로 바꾸고 나눕니다.

    자연어 질문에서 ``PaaS에``처럼 붙은 영문 약어가 하나의 토큰이 되어
    문서의 ``paas``와 매칭되지 않던 문제를 줄입니다.

    Args:
        text: 원본 문자열.

    Returns:
        소문자화·토큰화된 문자열 리스트(빈 토큰은 제외).
    """
    t = text.lower()
    t = re.sub(r"([a-z0-9])([가-힣])", r"\1 \2", t)
    t = re.sub(r"([가-힣])([a-z0-9])", r"\1 \2", t)
    cleaned = re.sub(r"[^\w\s]", " ", t)
    return [x for x in cleaned.split() if x]


def _bm25_scores(
    query_tokens: list[str],
    doc_tokens_list: list[list[str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """
    Okapi BM25 점수를 문서(청크)마다 계산합니다.

    단순 토큰 등장 횟수 합보다 문서 길이 편향이 적고,
    여러 문서에 흔히 나오는 단어는 IDF로 가중치가 줄어듭니다.

    Args:
        query_tokens: 사용자 질문에서 뽑은 토큰.
        doc_tokens_list: 각 청크의 토큰 리스트.
        k1: 용어 빈도 포화 정도(보통 1.2~2.0).
        b: 문서 길이 정규화 강도.

    Returns:
        청크 개수와 같은 길이의 점수 배열.
    """
    n_docs = len(doc_tokens_list)
    if n_docs == 0:
        return []

    dl = [len(d) for d in doc_tokens_list]
    avgdl = sum(dl) / n_docs
    if avgdl <= 0:
        avgdl = 1.0

    df: dict[str, int] = {}
    doc_counters: list[Counter[str]] = []
    for d in doc_tokens_list:
        ct = Counter(d)
        doc_counters.append(ct)
        for term in ct:
            df[term] = df.get(term, 0) + 1

    scores = [0.0] * n_docs
    seen_query_terms: set[str] = set()
    for qi in query_tokens:
        if qi in seen_query_terms:
            continue
        seen_query_terms.add(qi)
        n_qi = df.get(qi, 0)
        if n_qi == 0:
            continue
        idf = math.log((n_docs - n_qi + 0.5) / (n_qi + 0.5) + 1.0)
        for i, ct in enumerate(doc_counters):
            f = ct.get(qi, 0)
            if f == 0:
                continue
            denom = f + k1 * (1.0 - b + b * dl[i] / avgdl)
            scores[i] += idf * (f * (k1 + 1.0)) / denom

    return scores


@dataclass(frozen=True)
class RetrievalChunk:
    """RAG 검색 결과로 넘기는 PDF 페이지 단위 텍스트 조각입니다."""

    page: int
    title: str
    content: str


class FileVectorStore:
    """
    디스크(JSON)에 저장된 청크를 **BM25(Okapi)** 로 검색하는 저장소입니다.

    실제 임베딩 벡터는 없으며, 어드민과 챗봇이 같은 `data/vector.json`을 공유합니다.
    기본 경로는 **프로젝트 루트 기준**으로 고정되어 cwd에 덜 의존합니다.
    """

    def __init__(self, filepath: str | None = None) -> None:
        """
        Args:
            filepath: vector JSON 경로. None이면 프로젝트 루트의 data/vector.json을 사용합니다.
        """
        self.filepath = filepath or _default_vector_path()
        self._ensure_file()

    def _ensure_file(self) -> None:
        """저장 파일과 상위 디렉터리가 없으면 빈 배열로 생성합니다."""
        parent = os.path.dirname(self.filepath)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump([], f)

    def load_chunks(self) -> list[RetrievalChunk]:
        """JSON에서 모든 청크를 읽어 `RetrievalChunk` 리스트로 반환합니다."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [RetrievalChunk(**item) for item in data]
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def save_chunks(self, chunks: list[RetrievalChunk]) -> None:
        """청크 리스트 전체를 JSON 파일에 덮어씁니다."""
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump([asdict(c) for c in chunks], f, ensure_ascii=False, indent=2)

    def add_chunk(self, chunk: RetrievalChunk) -> None:
        """기존 목록 끝에 청크 하나를 추가해 저장합니다."""
        chunks = self.load_chunks()
        chunks.append(chunk)
        self.save_chunks(chunks)

    def delete_chunk(self, title: str) -> None:
        """제목이 정확히 일치하는 청크만 모두 제거합니다."""
        chunks = self.load_chunks()
        filtered = [c for c in chunks if c.title != title]
        self.save_chunks(filtered)

    def search(self, query: str, top_k: int = 3) -> list[RetrievalChunk]:
        """
        BM25 점수가 높은 순으로 상위 `top_k`개 청크를 반환합니다.

        질문에서 유효 토큰이 하나도 나오지 않으면 빈 리스트를 반환합니다.

        Args:
            query: 사용자 검색어 또는 질문 문장.
            top_k: 반환할 최대 청크 개수.

        Returns:
            점수순으로 정렬된 `RetrievalChunk` 리스트(점수 0인 문서는 제외).
        """
        chunks = self.load_chunks()
        if not chunks:
            return []

        query_tokens = _normalize_tokens(query)
        if not query_tokens:
            return []

        doc_tokens_list = [_normalize_tokens(f"{c.title} {c.content}") for c in chunks]
        scores = _bm25_scores(query_tokens, doc_tokens_list)

        ranked = sorted(
            [(scores[i], chunks[i]) for i in range(len(chunks)) if scores[i] > 0],
            key=lambda x: x[0],
            reverse=True,
        )
        return [c for _, c in ranked[:top_k]]
