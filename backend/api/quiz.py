from __future__ import annotations

from fastapi import APIRouter

from backend.dependencies import get_services
from backend.models.schemas import GenerateQuizRequest, QuizQuestion

router = APIRouter()


@router.post("/api/generate-quiz", response_model=list[QuizQuestion])
def generate_quiz(body: GenerateQuizRequest) -> list[QuizQuestion]:
    services = get_services()
    return services.quiz.generate(body.type, body.count)
