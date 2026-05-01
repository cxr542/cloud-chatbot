from __future__ import annotations

from dataclasses import dataclass

from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.services.glossary import GlossaryEngine
from backend.services.llm import LLMService
from backend.services.quiz import QuizEngine
from backend.services.rag import RagService


@dataclass
class Services:
    rag: RagService
    quiz: QuizEngine
    glossary: GlossaryEngine
    progress: dict[str, list[str]]


def _default_chunks() -> list[RetrievalChunk]:
    return [
        RetrievalChunk(76, "SSO 개념", "SSO는 한 번 로그인으로 여러 서비스에 접근하도록 돕는 인증 방식입니다."),
        RetrievalChunk(73, "RBAC", "RBAC는 역할 기반 접근제어로 역할 단위 권한 관리를 단순화합니다."),
        RetrievalChunk(74, "ABAC", "ABAC는 사용자/리소스/환경 속성에 기반해 세밀한 접근 정책을 적용합니다."),
        RetrievalChunk(77, "MFA", "MFA는 비밀번호 외 추가 인증요소를 요구해 계정 탈취 위험을 낮춥니다."),
        RetrievalChunk(69, "HA", "HA는 장애 상황에서도 서비스 중단을 최소화하도록 이중화와 자동복구를 설계합니다."),
        RetrievalChunk(70, "DR", "DR은 대규모 장애 후 복구 절차와 목표 복구시간(RTO/RPO)을 다룹니다."),
        RetrievalChunk(71, "Observability", "Observability는 로그/메트릭/트레이스로 시스템 상태를 관찰하는 능력입니다."),
        RetrievalChunk(79, "FinOps", "FinOps는 엔지니어링/재무/운영 협업으로 클라우드 비용을 최적화합니다."),
        RetrievalChunk(52, "쿠버네티스 기초", "쿠버네티스는 컨테이너 오케스트레이션으로 배포/확장/복구를 자동화합니다."),
    ]


store = FileVectorStore()
if not store.load_chunks():
    store.save_chunks(_default_chunks())

_services = Services(
    rag=RagService(store, LLMService()),
    quiz=QuizEngine(),
    glossary=GlossaryEngine(),
    progress={"completed_terms": []},
)


def get_services() -> Services:
    return _services
