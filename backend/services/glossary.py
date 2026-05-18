"""용어 비교: 지식 베이스(임베딩 검색)+LLM 우선, 실패 시 간단 패턴 매칭 문구."""

from __future__ import annotations

from backend.config import settings
from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.models.schemas import CompareTermsResponse
from backend.services.llm import LLMService
from backend.services.quiz import _build_quiz_context_bundle


def _dedupe_chunks(chunks: list[RetrievalChunk]) -> list[RetrievalChunk]:
    """같은 제목·페이지 청크는 한 번만 남겨 검색 결과를 모읍니다.

    Args:
        chunks: 순서 있는 후보 목록.

    Returns:
        중복이 제거된 리스트.
    """
    seen: set[tuple[str, int]] = set()
    out: list[RetrievalChunk] = []
    for c in chunks:
        key = (c.title or "", int(c.page))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


class GlossaryEngine:
    """`/api/compare-terms` 처리. 과거 교재용 페이지 맵 등은 참고 주석 용도로만 남깁니다."""

    def __init__(self, store: FileVectorStore, llm_service: LLMService) -> None:
        self._store = store
        self._llm = llm_service
        self.term_pages = {
            "SSO": 76,
            "RBAC": 73,
            "ABAC": 74,
            "MFA": 77,
            "ACL": 78,
            "HA": 69,
            "DR": 70,
            "Observability": 71,
            "FinOps": 79,
            "쿠버네티스": 52,
        }

    def _gather_compare_chunks(self, term_a: str, term_b: str) -> list[RetrievalChunk]:
        """두 용어 각각 및 결합 검색으로 RAG 근거 청크를 모읍니다.

        Args:
            term_a: 첫 번째 키워드.
            term_b: 두 번째 키워드.

        Returns:
            용량 상한까지의 청크 리스트.
        """
        a = (term_a or "").strip()
        b = (term_b or "").strip()
        merged: list[RetrievalChunk] = []
        merged.extend(self._store.search(a, top_k=10))
        merged.extend(self._store.search(b, top_k=10))
        merged.extend(self._store.search(f"{a} {b}", top_k=12))
        merged.extend(self._store.search(f"{a}와 {b} 차이", top_k=8))
        return _dedupe_chunks(merged)[:28]

    def _static_pattern_compare(self, term_a: str, term_b: str) -> CompareTermsResponse | None:
        """예전처럼 몇 가지 알려진 쌍만 고정 카피로 맞춥니다(API·검색 없을 때 참고용).

        Args:
            term_a: 용어 A.
            term_b: 용어 B.

        Returns:
            알려진 쌍이면 응답, 아니면 None.
        """
        key = f"{term_a.strip().upper()}|{term_b.strip().upper()}"
        if "RBAC" in key and "ABAC" in key:
            return CompareTermsResponse(
                left="RBAC: 역할 중심, 관리 단순",
                right="ABAC: 속성 기반, 정책 세밀",
                diff="RBAC는 운영 단순성, ABAC는 정책 유연성이 강점입니다.",
            )
        if "HA" in key and "DR" in key:
            return CompareTermsResponse(
                left="HA: 장애 시 서비스 지속",
                right="DR: 재해 후 복구",
                diff="HA는 즉시성, DR은 복구 전략·절차 중심입니다.",
            )
        return None

    def _generic_fallback(self, term_a: str, term_b: str) -> CompareTermsResponse:
        """검색·LLM 없이 사용자 입력만 보여 주는 안전 문구입니다.

        Args:
            term_a: 용어 A.
            term_b: 용어 B.

        Returns:
            플레이스홀더 비교 카피.
        """
        return CompareTermsResponse(
            left=f"「{term_a}」: 자료 검색 결과가 거의 없거나 API 키가 없습니다.",
            right=f"「{term_b}」: 위와 동일.",
            diff="업로드한 PDF·동영상 요약이 충분한지 확인하거나 `.env`의 LLM 키를 설정해 주세요. "
            "(키와 자료가 있으면 해당 문단만 근거로 비교 문장을 만듭니다.)",
        )

    def compare(self, term_a: str, term_b: str) -> CompareTermsResponse:
        """두 용어 비교 카피를 만듭니다(검색 근거 + LLM 또는 폴백).

        단순 **문자열 LIKE 검색 아님** — ``FileVectorStore.search`` 로 임베딩+BM25 계열 순위입니다.

        Args:
            term_a: 사용자가 입력한 용어 또는 개념 A.
            term_b: 사용자가 입력한 용어 또는 개념 B.

        Returns:
            ``CompareTermsResponse`` (left/right/diff 줄).
        """
        a = (term_a or "").strip()
        b = (term_b or "").strip()
        if not a or not b:
            return CompareTermsResponse(
                left="용어 A를 입력해 주세요.",
                right="용어 B를 입력해 주세요.",
                diff="두 칸 모두 채운 뒤 다시 「비교하기」를 눌러 주세요.",
            )
        hits = self._gather_compare_chunks(a, b)
        bundle = _build_quiz_context_bundle(hits, max_chars=16000)

        if settings.LLM_API_KEY and len(bundle) >= 80:
            gen = self._llm.compare_terms_with_context(a, b, bundle)
            if gen is not None:
                return gen

        stub = self._static_pattern_compare(a, b)
        if stub is not None:
            return stub

        if len(bundle) >= 120:
            return CompareTermsResponse(
                left=f"「{a}」 관련 페이지를 검색했으나 요약 카피를 자동 작성하지 못했습니다.",
                right=f"「{b}」 관련 페이지를 검색했으나 요약 카피를 자동 작성하지 못했습니다.",
                diff="네트워크·모델 응답을 확인하고 잠시 후 다시 시도해 주세요. (근거 페이지는 검색 결과에 존재할 수 있습니다.)",
            )

        return self._generic_fallback(a, b)
