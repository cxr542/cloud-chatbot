from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PageRef(BaseModel):
    no: int
    title: str


class AskQuestionRequest(BaseModel):
    text: str = Field(..., description="사용자 질문")
    difficulty: Literal["초급", "중급", "고급"] = "초급"


class AskQuestionResponse(BaseModel):
    reply: str
    pages: list[PageRef] = []
    page: PageRef | None = None  # 하위 호환성 유지


class SourceItem(BaseModel):
    p: int
    t: str
    d: str


class SummaryRequest(BaseModel):
    mode: Literal["topic", "range", "all"]
    topic: str | None = None
    start: str | None = None
    end: str | None = None


class SummaryResponse(BaseModel):
    text: str
    pages: list[int]


class CompareTermsRequest(BaseModel):
    term_a: str = Field(..., alias="termA")
    term_b: str = Field(..., alias="termB")
    model_config = ConfigDict(populate_by_name=True)


class CompareTermsResponse(BaseModel):
    left: str
    right: str
    diff: str


class QuizQuestion(BaseModel):
    q: str
    c: list[str]
    a: int
    e: str


class GenerateQuizRequest(BaseModel):
    type: Literal["mcq", "ox", "mix"] = "mcq"
    count: int = Field(10, ge=1, le=50)


class ProgressState(BaseModel):
    completed_terms: list[str] = []


class ChatRequest(BaseModel):
    message: str = Field(..., description="사용자 메시지")


class ChatResponse(BaseModel):
    reply: str
