"""퀴즈 문항 생성: 지식 베이스 + LLM 우선, 실패 시 로컬 샘플 풀."""

from __future__ import annotations

import random
from collections.abc import Iterable

from backend.config import settings
from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.models.schemas import QuizQuestion
from backend.services.llm import LLMService, _chunk_text_for_gemini


def _is_placeholder_video_fail(content: str) -> bool:
    """동영상 요약 실패 플레이스홀더면 퀴즈 근거에서 제외합니다.

    Args:
        content: 청크 본문.

    Returns:
        실패 안내문이면 True.
    """
    s = (content or "").strip()
    return s.startswith("[동영상 자료]") and "실패" in s


def _usable_quiz_chunks(chunks: Iterable[RetrievalChunk]) -> list[RetrievalChunk]:
    """너무 짧거나 동영상 추출 실패만 있는 청크는 출제 컨텍스트에서 뺍니다.

    Args:
        chunks: 검색·로드로 모은 후보들.

    Returns:
        정제된 목록.
    """
    out: list[RetrievalChunk] = []
    for c in chunks:
        body = (c.content or "").strip()
        if len(body) < 50:
            continue
        if _is_placeholder_video_fail(body):
            continue
        out.append(c)
    return out


def _build_quiz_context_bundle(chunks: list[RetrievalChunk], *, max_chars: int = 18000) -> str:
    """LLM 퀴즈 프롬프트에 넣을 발췌 문자열을 만듭니다(Q&A와 동일 청크 포맷 활용).

    Args:
        chunks: 모델에 줄 상위 청크들.
        max_chars: 전체 상한(토큰 과다 방지).

    Returns:
        섹션 헤더와 본문을 이어 붙인 긴 문자열.
    """
    parts: list[str] = []
    total = 0
    for c in chunks:
        block = _chunk_text_for_gemini(c, max_chars=3200).strip()
        if not block:
            continue
        head = f"[{c.title} · p.{c.page}]"
        piece = f"### {head}\n{block}"
        if total + len(piece) > max_chars:
            break
        parts.append(piece)
        total += len(piece)
    return "\n\n".join(parts)


class QuizEngine:
    """퀴즈 API 뒤쪽 엔진. ``vector.json`` 근거를 LLM에 넘기고 부족하면 샘플 문제로 채웁니다."""

    def __init__(self, store: FileVectorStore, llm_service: LLMService) -> None:
        self._store = store
        self._llm = llm_service
        self._mcq = [
            QuizQuestion(q="SSO의 핵심 목적은?", c=["한 번 로그인으로 여러 서비스 접근", "암호를 길게 만들기", "로그를 삭제하기", "비용 정산 자동화"], a=0, e="SSO는 단일 인증으로 다중 서비스 접근을 지원합니다."),
            QuizQuestion(q="RBAC는 무엇을 기준으로 권한을 부여하나요?", c=["위치", "역할(Role)", "시간", "디바이스"], a=1, e="RBAC는 역할 기반입니다."),
            QuizQuestion(q="ABAC의 특징은?", c=["역할만 사용", "정책+속성 기반", "수동 승인만 가능", "네트워크 속도 측정"], a=1, e="ABAC는 속성과 정책을 사용합니다."),
            QuizQuestion(q="MFA는 어떤 개념인가요?", c=["다중 장애 복구", "다중 인증 요소", "다중 클러스터", "다중 리전 과금"], a=1, e="MFA는 추가 인증요소를 요구합니다."),
            QuizQuestion(q="쿠버네티스의 대표 기능은?", c=["컨테이너 오케스트레이션", "문서 OCR", "비밀번호 복구", "DNS 루트 관리"], a=0, e="쿠버네티스는 컨테이너 운영 자동화를 담당합니다."),
        ]
        self._ox = [
            QuizQuestion(q="SSO는 한 번 로그인으로 여러 서비스 접근을 돕는다.", c=["O", "X"], a=0, e="맞습니다."),
            QuizQuestion(q="RBAC는 사용자 속성만으로 권한을 판단한다.", c=["O", "X"], a=1, e="아닙니다. 역할 기반입니다."),
        ]

    def _gather_context_chunks(self) -> list[RetrievalChunk]:
        """여러 검색 쿼리로 벡터 저장소에서 다양한 페이지를 모읍니다.

        Returns:
            중복을 줄인 ``RetrievalChunk`` 목록.
        """
        seeds = [
            "클라우드 기초",
            "보안",
            "가상화",
            "컨테이너 쿠버네티스",
            "네트워크 VPC",
            "접근 제어 IAM",
            "운영 모니터링",
        ]
        seen: set[tuple[str, int]] = set()
        merged: list[RetrievalChunk] = []
        for s in seeds:
            for c in self._store.search(s, top_k=7):
                key = (c.title or "", int(c.page))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(c)
                if len(merged) >= 36:
                    return _usable_quiz_chunks(merged)
        if len(merged) < 12:
            for c in self._store.load_chunks():
                key = (c.title or "", int(c.page))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(c)
                if len(merged) >= 42:
                    break
        return _usable_quiz_chunks(merged)

    def _fallback_generate(self, quiz_type: str, count: int) -> list[QuizQuestion]:
        """API 키 없음·근거 부족·LLM 실패 시 쓰는 내장 샘플 문제(순서만 섞음).

        Args:
            quiz_type: ``mcq`` / ``ox`` / ``mix``.
            count: 필요한 문항 수.

        Returns:
            ``count`` 이하로 자른 리스트.
        """
        if quiz_type == "ox":
            pool = list(self._ox)
        elif quiz_type == "mix":
            pool = list(self._mcq[:3] + self._ox)
        else:
            pool = list(self._mcq)
        random.shuffle(pool)
        out: list[QuizQuestion] = []
        while len(out) < count:
            out.extend(pool)
        return out[:count]

    def _merge_fill(self, primary: list[QuizQuestion], quiz_type: str, count: int) -> list[QuizQuestion]:
        """LLM이 일부만 돌려준 경우 샘플 문제로 개수를 채웁니다(질문 텍스트 중복은 스킵).

        Args:
            primary: 모델 또는 검증을 통과한 앞부분.
            quiz_type: 요청 유형.
            count: 목표 개수.

        Returns:
            길이가 ``count`` 인 리스트.
        """
        out = list(primary)
        seen_q = {x.q.strip() for x in out}
        need = count - len(out)
        if need <= 0:
            return out[:count]
        for item in self._fallback_generate(quiz_type, need + 8):
            if item.q.strip() in seen_q:
                continue
            out.append(item)
            seen_q.add(item.q.strip())
            if len(out) >= count:
                break
        if len(out) < count:
            out.extend(self._fallback_generate(quiz_type, count - len(out)))
        return out[:count]

    def generate(self, quiz_type: str, count: int, difficulty: str = "초급") -> list[QuizQuestion]:
        """요청 유형·개수에 맞춰 문제 목록을 만듭니다(지식+RAG 스타일 검색 + LLM 우선).

        Args:
            quiz_type: ``mcq`` / ``ox`` / ``mix``.
            count: 1~50.
            difficulty: ``초급`` / ``중급`` / ``고급`` — LLM 출제 프롬프트에만 반영됩니다(샘플 풀 대체 시에는 무시).

        Returns:
            ``QuizQuestion`` 리스트.
        """
        n = max(1, min(50, int(count)))
        qt = quiz_type if quiz_type in ("mcq", "ox", "mix") else "mcq"
        lvl = difficulty if str(difficulty) in ("초급", "중급", "고급") else "초급"
        contexts = self._gather_context_chunks()
        bundle = _build_quiz_context_bundle(contexts)
        gen: list[QuizQuestion] = []
        if settings.LLM_API_KEY and len(bundle) >= 80:
            gen = self._llm.generate_quiz_from_documents(bundle, qt, n, lvl)
        if gen:
            return self._merge_fill(gen, qt, n)
        return self._fallback_generate(qt, n)
