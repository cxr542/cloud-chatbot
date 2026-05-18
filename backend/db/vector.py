from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _project_root() -> Path:
    """이 파일(`backend/db/vector.py`) 위치를 기준으로 프로젝트 루트 디렉터리를 반환합니다."""
    return Path(__file__).resolve().parent.parent.parent


def _default_vector_path() -> str:
    """실행 시 현재 작업 디렉터리와 무관하게 사용할 `data/vector.json`의 절대 경로를 반환합니다."""
    return str(_project_root() / "data" / "vector.json")


def _hangul_ngram_tokens(tok: str) -> list[str]:
    """
    공백 없이 붙은 한글 PDF 본문에서 한 토큰이 수백 자가 되어도,
    질문의 짧은 한글과 BM25가 맞물리도록 2·3음절 n-gram을 추가합니다.

    Args:
        tok: 공백 분리 뒤의 한 조각(한글만 있을 때 의미가 큼).

    Returns:
        원본에는 포함하지 않은 n-gram 문자열만 담은 리스트.
    """
    if not tok or not re.fullmatch(r"[가-힣]+", tok):
        return []
    # 청크 한 토큰이 비정상적으로 길면 앞부분만 사용해 토큰 폭주를 막습니다.
    cap = 480
    s = tok if len(tok) <= cap else tok[:cap]
    out: list[str] = []
    if len(s) >= 2:
        out.extend(s[i : i + 2] for i in range(len(s) - 1))
    if len(s) >= 3:
        out.extend(s[i : i + 3] for i in range(len(s) - 2))
    return out


def _normalize_tokens(text: str) -> list[str]:
    """
    질문·문서 텍스트를 BM25 검색용 토큰 리스트로 바꿉니다.

    유니코드에서 `\\w`는 한글·영문·숫자 등 단어 문자로 인식되므로,
    한글과 라틴·숫자 사이에 공백을 넣어 분리한 뒤 구두점을 공백으로 바꾸고 나눕니다.

    PDF 추출본은 한글이 공백 없이 이어지는 경우가 많아 한 토큰이 매우 길어집니다.
    이때 질문의 짧은 단어와 토큰이 정확히 일치하지 않아 BM25 점수가 전부 0이 되는 문제가 있어,
    **한글 연속 구간에는 2·3음절 n-gram**을 추가로 넣어 검색이 되도록 합니다.

    Args:
        text: 원본 문자열.

    Returns:
        소문자화·토큰화된 문자열 리스트(빈 토큰은 제외).
    """
    # macOS 등에서 파일명이 NFD(자모 분해)로 들어오면 PDF·동영상 제목 토큰이 한 덩어리가 되어
    # 사용자가 NFC로 입력한 질문('클라우드', '개요' 등)과 한 글자도 안 겹칠 수 있습니다.
    t = unicodedata.normalize("NFC", text or "").lower()
    t = re.sub(r"([a-z0-9])([가-힣])", r"\1 \2", t)
    t = re.sub(r"([가-힣])([a-z0-9])", r"\1 \2", t)
    cleaned = re.sub(r"[^\w\s]", " ", t)
    base = [x for x in cleaned.split() if x]
    expanded: list[str] = []
    for tok in base:
        expanded.append(tok)
        expanded.extend(_hangul_ngram_tokens(tok))
    return expanded


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


def _substring_fallback_pick_chunks(
    query: str,
    chunks: list[RetrievalChunk],
    *,
    top_k: int,
) -> list[RetrievalChunk]:
    """
    BM25 점수가 전부 0일 때(짧은 질문·특수문자만 등) 부분 문자열 일치로 청크를 고릅니다.

    한글 n-gram 보강 뒤에도 매칭이 없을 수 있어 최후 수단으로 둡니다.
    """
    q = unicodedata.normalize("NFC", query.strip()).lower()
    if len(q) < 2:
        return []
    qs = re.sub(r"[^\w\s가-힣a-z0-9]", " ", q)
    parts = [p for p in qs.split() if len(p) >= 2]
    if not parts:
        parts = [q[: min(len(q), 40)]]
    parts = parts[:8]
    scored: list[tuple[int, RetrievalChunk]] = []
    for c in chunks:
        blob = unicodedata.normalize("NFC", f"{c.title} {c.content}").lower()
        score = sum(blob.count(p) for p in parts)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


@dataclass(frozen=True)
class RetrievalChunk:
    """RAG 검색 결과로 넘기는 PDF 페이지 또는 미디어 요약 단위 텍스트 조각입니다."""

    page: int
    title: str
    content: str
    embedding: list[float] | None = field(default=None)
    ref_time_band: str | None = field(default=None)


def _chunk_from_dict(item: dict) -> RetrievalChunk:
    """
    ``vector.json`` 한 레코드를 ``RetrievalChunk`` 로 바꿉니다.

    예전 형식(임베딩 필드 없음)과 호환하고, 알 수 없는 키는 무시합니다.

    Args:
        item: JSON 객체(dict).

    Returns:
        동결된 청크 인스턴스.
    """
    emb = item.get("embedding")
    if emb is not None and not isinstance(emb, list):
        emb = None
    rtb = item.get("ref_time_band")
    rtb_clean = None
    if isinstance(rtb, str) and rtb.strip():
        rtb_clean = rtb.strip()
    return RetrievalChunk(
        page=int(item["page"]),
        title=str(item["title"]),
        content=str(item["content"]),
        embedding=[float(x) for x in emb] if emb else None,
        ref_time_band=rtb_clean,
    )


def _bm25_ordered_chunks(query: str, chunks: list[RetrievalChunk]) -> list[RetrievalChunk]:
    """
    BM25 점수가 양수인 청크를 점수 내림차순으로 반환하고,
    전부 0이면 부분 문자열 폴백으로 최대 ``len(chunks)``개까지 돌려줍니다.

    Args:
        query: 사용자 질문.
        chunks: 인덱싱된 전체 청크.

    Returns:
        BM25(또는 폴백) 순서의 청크 리스트.
    """
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
    if ranked:
        return [c for _, c in ranked]
    return _substring_fallback_pick_chunks(query, chunks, top_k=len(chunks))


def _rrf_fuse(
    rankings: list[list[RetrievalChunk]],
    *,
    top_k: int,
    k_rrf: float = 58.0,
) -> list[RetrievalChunk]:
    """
    Reciprocal Rank Fusion(RRF): 여러 순위표(임베딩·BM25 등)를 점수 없이 합쳐 균형 잡힌 후보를 고릅니다.

    임베딩만 쓰면 키워드가 딱 맞는 페이지가 밀릴 수 있고, BM25만 쓰면 의미 매칭을 놓칠 수 있어
    교육용 RAG에서 두 목록을 함께 쓰면 답 품질이 안정되는 경우가 많습니다.

    Args:
        rankings: 비어 있지 않은 ``RetrievalChunk`` 순위 리스트들(앞쪽이 더 높은 순위).
        top_k: 최대 반환 개수.
        k_rrf: 순위 보정 상수(통상 58~60).

    Returns:
        융합 순으로 정렬된 최대 ``top_k``개 청크.
    """
    from collections import defaultdict

    scores: defaultdict[tuple[str, int], float] = defaultdict(float)
    key_chunk: dict[tuple[str, int], RetrievalChunk] = {}
    for rlist in rankings:
        if not rlist:
            continue
        for rank, ch in enumerate(rlist):
            key = (ch.title, ch.page)
            scores[key] += 1.0 / (k_rrf + float(rank) + 1.0)
            key_chunk[key] = ch
    if not scores:
        return []
    ordered_keys = sorted(scores.keys(), key=lambda kx: scores[kx], reverse=True)
    return [key_chunk[k] for k in ordered_keys[:top_k]]


def _kb_media_tag_chunks(chunks: list[RetrievalChunk]) -> list[RetrievalChunk]:
    """
    관리자 업로드 규칙상 제목에 ``(동영상)`` 또는 ``(이미지)`` 가 붙은 지식 청크만 골라 담습니다.

    PDF 페이지 청크가 많을 때 **전체** BM25·임베딩 순위만 쓰면 미디어 요약 청크가 상위 후보에서
    밀려 Q&A 답변·출처 카드에 거의 안 나오는 문제가 생깁니다. 미디어만 따로 순위를 매겨
    RRF에 한 겹 더 얹어 균형을 맞춥니다.

    Args:
        chunks: ``vector.json`` 에서 읽은 전체 청크입니다.

    Returns:
        미디어 계열 청크만 담은 리스트(원본 순서 유지).
    """
    return [c for c in chunks if "(동영상)" in c.title or "(이미지)" in c.title]


class FileVectorStore:
    """
    디스크(JSON)에 저장된 청크를 검색하는 저장소입니다.

    **로컬 임베딩** 이 있으면 BM25(키워드)·부분 문자열 순위와 **RRF로 합성**해 상위 청크를 고릅니다.
    임베딩만으로 슬롯을 채우지 않아, 키워드와 의미가 함께 반영됩니다.
    임베딩이 없으면 BM25·부분 문자열 폴백만 사용합니다.
    어드민과 챗봇이 같은 ``data/vector.json`` 을 공유하며, 경로는 프로젝트 루트 기준입니다.
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
                return [_chunk_from_dict(item) for item in data if isinstance(item, dict)]
        except (json.JSONDecodeError, FileNotFoundError, TypeError, KeyError, ValueError):
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

    def add_chunks(self, new_chunks: list[RetrievalChunk]) -> None:
        """
        기존 목록 끝에 청크 여러 개를 한 번에 추가합니다.

        PDF 한 권 단위로 배치 임베딩 후 저장할 때 디스크 I/O를 줄이기 위해 사용합니다.

        Args:
            new_chunks: 추가할 ``RetrievalChunk`` 목록.
        """
        if not new_chunks:
            return
        chunks = self.load_chunks()
        chunks.extend(new_chunks)
        self.save_chunks(chunks)

    def delete_chunk(self, title: str) -> None:
        """제목이 정확히 일치하는 청크만 모두 제거합니다."""
        chunks = self.load_chunks()
        filtered = [c for c in chunks if c.title != title]
        self.save_chunks(filtered)

    def search(self, query: str, top_k: int = 3) -> list[RetrievalChunk]:
        """
        **임베딩 순위**와 **BM25(또는 부분 문자열) 순위**를 RRF로 합쳐 상위 ``top_k`` 를 고릅니다.

        예전처럼 임베딩만으로 슬롯을 다 채우면, 엉뚱한 의미 유사 페이지가 질문을 덮어쓸 수 있어
        키워드가 강한 페이지가 섞이도록 합니다. 임베딩 벡터가 없는 청크는 BM25 쪽 순위에만 나타납니다.

        동영상·이미지 요약 청크는 수가 적어도 PDF에 밀리지 않게 **미디어 전용** BM25·임베딩 순위를
        추가로 넣어 같은 RRF에 섞습니다.

        Args:
            query: 사용자 검색어 또는 질문 문장.
            top_k: 반환할 최대 청크 개수.

        Returns:
            융합·보완된 ``RetrievalChunk`` 리스트.
        """
        chunks = self.load_chunks()
        if not chunks:
            return []

        q_strip = query.strip()
        query_tokens = _normalize_tokens(query)

        bm25_rank: list[RetrievalChunk] = []
        if query_tokens:
            bm25_rank = _bm25_ordered_chunks(query, chunks)
        elif q_strip:
            bm25_rank = _substring_fallback_pick_chunks(
                query, chunks, top_k=min(len(chunks), 80)
            )

        from backend.services.local_embeddings import (
            embed_query_vector_for_retrieval,
            embedding_similarity_dot,
        )

        emb_rank: list[RetrievalChunk] = []
        with_vec = [c for c in chunks if c.embedding]
        qv = embed_query_vector_for_retrieval(query) if (q_strip and with_vec) else None
        if qv is not None:
            emb_rank = sorted(
                with_vec,
                key=lambda c: embedding_similarity_dot(qv, c.embedding),
                reverse=True,
            )

        media_chunks = _kb_media_tag_chunks(chunks)
        media_bm25_rank: list[RetrievalChunk] = []
        if media_chunks and query_tokens:
            media_bm25_rank = _bm25_ordered_chunks(query, media_chunks)
        elif media_chunks and q_strip:
            media_bm25_rank = _substring_fallback_pick_chunks(
                query, media_chunks, top_k=min(len(media_chunks), 80)
            )

        media_emb_rank: list[RetrievalChunk] = []
        media_with_vec = [c for c in media_chunks if c.embedding]
        if qv is not None and media_with_vec:
            media_emb_rank = sorted(
                media_with_vec,
                key=lambda c: embedding_similarity_dot(qv, c.embedding),
                reverse=True,
            )

        rankings: list[list[RetrievalChunk]] = []
        if emb_rank:
            rankings.append(emb_rank)
        if bm25_rank:
            rankings.append(bm25_rank)
        if media_bm25_rank:
            rankings.append(media_bm25_rank)
        if media_emb_rank:
            rankings.append(media_emb_rank)

        fused: list[RetrievalChunk] = []
        if len(rankings) >= 2:
            fused = _rrf_fuse(rankings, top_k=top_k)
        elif len(rankings) == 1:
            fused = rankings[0][:top_k]

        if fused:
            return fused[:top_k]

        if not q_strip:
            return []

        return _substring_fallback_pick_chunks(query, chunks, top_k=top_k)
