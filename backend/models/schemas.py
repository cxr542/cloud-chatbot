from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageRef(BaseModel):
    """Q&A 등에서 출처 한 건입니다. 미디어는 ``badge`` 에 구간 문자열을 씁니다."""

    no: int
    title: str
    badge: str = ""
    source_type: Literal["page", "video", "image"] = "page"
    # 동영상: uploads 실제 파일명·썸네일·구간 초(있으면) — 브라우저 플레이어·포스터에 사용
    media_file: str | None = None
    thumb_sec: float | None = None
    start_sec: float | None = None
    end_sec: float | None = None


class AskQuestionRequest(BaseModel):
    text: str = Field(..., description="사용자 질문")
    difficulty: Literal["초급", "중급", "고급"] = "초급"


class AskQuestionResponse(BaseModel):
    reply: str
    pages: list[PageRef] = []
    page: PageRef | None = None  # 하위 호환성 유지


class SourceItem(BaseModel):
    """출처 찾기 한 줄 카드입니다. PDF는 페이지, 동영상은 ``badge``에 타임 레이블이 들어가면 좋습니다."""

    p: int
    t: str
    d: str
    badge: str = ""
    source_type: Literal["page", "video", "image"] = "page"
    media_file: str | None = None
    thumb_sec: float | None = None
    start_sec: float | None = None
    end_sec: float | None = None


class SummaryRequest(BaseModel):
    mode: Literal["topic", "range", "all"]
    topic: str | None = None
    start: str | None = None
    end: str | None = None


class SummaryBullet(BaseModel):
    """요약 카드·PNG 등 시각화용 한 줄 요약."""

    page: int
    text: str
    source: str = ""


class SummaryResponse(BaseModel):
    text: str
    pages: list[int]
    headline: str = ""
    bullets: list[SummaryBullet] = Field(default_factory=list)


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
    difficulty: Literal["초급", "중급", "고급"] = Field(
        "초급",
        description="Q&A와 동일한 난이도 라벨. 퀴즈 LLM 출제 지침에 반영됩니다.",
    )


class ChatRequest(BaseModel):
    message: str = Field(..., description="사용자 메시지")


class ChatResponse(BaseModel):
    reply: str


class MbtiChatTurn(BaseModel):
    """나의 MBTI 찾기 대화 한 턴(사용자 말 또는 직전 도우미 응답)."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=16000)


class MbtiChatRequest(BaseModel):
    """브라우저가 보내는 멀티턴 채팅 목록입니다. 마지막 줄은 사용자가 새로 입력한 내용입니다."""

    messages: list[MbtiChatTurn] = Field(..., min_length=1, max_length=42)

    @model_validator(mode="after")
    def _last_must_be_user(self) -> MbtiChatRequest:
        if self.messages[-1].role != "user":
            raise ValueError("마지막 메시지는 사용자(user)의 말이어야 합니다.")
        return self


class MbtiChatResponse(BaseModel):
    """MBTI 진행 안내 또는 분석 결과를 담습니다."""

    reply: str


class StickerPackItem(BaseModel):
    """스티커 팩 한 장(표정 패널)입니다."""

    idx: int = Field(..., ge=1, le=12, description="1~12번 표정 인덱스")
    label: str = Field(..., description="한글 라벨(예: 멍때리기)")
    mime_type: str = Field(default="image/png", description="inline_data 에 대응하는 MIME 타입")
    image_base64: str = Field(..., description="base64 로 인코딩된 이미지 바이너리")


class StickerPackResponse(BaseModel):
    """업로드 사진 참조 후 12개 표정 패널을 모은 응답입니다. 일부 실패 시에도 성공 패널만 채워집니다."""

    items: list[StickerPackItem] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="특정 표정 생성 실패·경고 등 짧은 메시지(사용자 안내)",
    )
