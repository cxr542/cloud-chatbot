from __future__ import annotations

from fastapi import APIRouter

from backend.db.database import log_chat
from backend.dependencies import get_services
from backend.models.schemas import MbtiChatRequest, MbtiChatResponse

router = APIRouter()


@router.post("/api/mbti-chat", response_model=MbtiChatResponse)
def mbti_chat(body: MbtiChatRequest) -> MbtiChatResponse:
    """브라우저 「나의 MBTI 찾기」 탭의 멀티턴 대화에 맞춰 Gemini 응답 한 턴을 돌려줍니다.

    교육·참고용 참여형 안내용이며, 정식 검사가 아니라는 점은 프롬프트 안에서도 밝힙니다.

    Args:
        body: ``messages`` 에 대화 순서 전체와 마지막 사용자 입력이 포함됩니다.

    Returns:
        사용자 화면에 그대로 붙일 하나의 문자열 블록.
    """
    svc = get_services()
    payloads = [{"role": t.role, "content": t.content.strip()} for t in body.messages]
    reply = svc.llm.mbti_chat_reply(payloads)

    last_user_snip = ""
    for row in reversed(body.messages):
        if row.role == "user":
            u = row.content.strip()
            last_user_snip = u[:800] if len(u) > 800 else u
            break

    log_chat(last_user_snip if last_user_snip else "(MBTI 사용자 메시지 없음)", "MBTI", False, reply[:3000])

    return MbtiChatResponse(reply=reply)
