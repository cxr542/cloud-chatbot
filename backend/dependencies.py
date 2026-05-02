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


store = FileVectorStore()
# 기본 더미 데이터를 더 이상 사용하지 않으며, uploads 폴더의 실제 데이터만 사용합니다.

_services = Services(
    rag=RagService(store, LLMService()),
    quiz=QuizEngine(),
    glossary=GlossaryEngine(),
    progress={"completed_terms": []},
)


def get_services() -> Services:
    return _services
