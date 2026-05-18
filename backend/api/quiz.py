from __future__ import annotations

from fastapi import APIRouter

from backend.dependencies import get_services
from backend.models.schemas import GenerateQuizRequest, QuizQuestion

router = APIRouter()


@router.post("/api/generate-quiz", response_model=list[QuizQuestion])
def generate_quiz(body: GenerateQuizRequest) -> list[QuizQuestion]:
    """학습자 난이도와 문항 유형에 맞춰 퀴즈 목록을 만듭니다(부족 시 내장 샘플로 보완)."""
    services = get_services()
    return services.quiz.generate(body.type, body.count, body.difficulty)
