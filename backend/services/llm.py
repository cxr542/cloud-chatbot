from __future__ import annotations

import json
import re
from typing import Literal

from backend.config import settings
from backend.db.database import get_settings
from backend.db.vector import RetrievalChunk, _normalize_tokens
from backend.models.schemas import CompareTermsResponse, QuizQuestion
from backend.services.mbti_prompt import build_mbti_system_instruction

# 교육생 화면(index.html)의 메인 탭·설정 메뉴 안내와 맞춰 두었습니다.
GREETING_REPLY = """안녕하세요! ☁️ 클라우드 학습 도우미입니다.

업로드된 학습 문서를 바탕으로 답해 드려요.

**메인 탭**(상단·학습)

| 메뉴 | 설명 |
| :--- | :--- |
| 💬 Q&A | 학습 문서를 근거로 질문에 답합니다 |
| 🔍 출처 찾기 | 문서에서 관련 페이지 검색(의미+키워드) |
| 📝 요약 | 주제·페이지 범위·전체 요약을 봅니다 |
| ⚖️ 용어 비교 | 두 용어의 차이를 비교합니다 |
| 🧠 퀴즈 | 4지선다·OX·혼합으로 복습합니다 |

(탭 줄에서는 위 학습 메뉴가 **왼쪽**부터 이 순서대로 붙고, 줄 **오른쪽 끝**에만 **머리 식히기 ▾**가 있습니다.)

**☕ 머리 식히기**(탭 줄 오른쪽 끝 ▾)

| 메뉴 | 설명 |
| :--- | :--- |
| 🎨 이모티콘 | 귀여운 스티커 패널 12종 만들기(ZIP 저장 가능) |
| 📊 나의 MBTI 찾기 | 간단 성향 분석 대화형 안내입니다 |

**⚙️ 설정**(홈 오른쪽 ▾메뉴)

| 메뉴 | 설명 |
| :--- | :--- |
| 📌 오답노트 | 틀린 문제를 모아 다시 봅니다 |
| ⚙️ 난이도 설정 | 초급·중급·고급. Q&A 답변과 퀴즈 출제 난이도에 반영됩니다 |

📚 상단 **지식 베이스**에서 PDF 목록·페이지 열람이 가능해요.

클라우드 용어로 구체적으로 질문해 보세요!"""


def is_greeting_query(query: str) -> bool:
    """
    짧은 인사인지 판별합니다.

    영어 ``hi``/``hello``는 단어 경계(\\b)로만 매칭해 ``히스토리`` 등 오탐을 줄입니다.
    한국어는 ``안녕``, ``하이``, ``반갑`` 포함 여부로 판단합니다.
    """
    q = query.strip().lower()
    if not q:
        return False
    if any(marker in q for marker in ("안녕", "하이", "반갑")):
        return True
    return bool(re.search(r"\bhello\b", q)) or bool(re.search(r"\bhi\b", q))


def _is_iaas_paas_comparison_question(query: str) -> bool:
    """
    질문이 IaaS와 PaaS의 차이·비교인지 대략적으로 판별합니다.

    Mock 모드에서 마크다운 표 예시를 덧붙일 때만 사용합니다.
    """
    ql = query.lower()
    if "iaas" not in ql or "paas" not in ql:
        return False
    if any(m in query for m in ("차이", "비교", "대비")):
        return True
    if any(m in ql for m in ("different", "compare", "versus")):
        return True
    if " vs " in ql or re.search(r"\bvs\b", ql):
        return True
    return False


def _iaas_paas_markdown_table() -> str:
    """교육용 IaaS/PaaS 비교 표(마크다운) 문자열을 반환합니다."""
    return (
        "**비교 요약 (표)**\n\n"
        "| 비교 항목 | IaaS | PaaS |\n"
        "| :--- | :--- | :--- |\n"
        "| 제공 범위 | 가상 서버·스토리지·네트워크 등 인프라 자원 | 앱 실행용 플랫폼·런타임·미들웨어까지 제공 |\n"
        "| 사용자 관리 책임 | OS·패치·플랫폼 계층까지 직접 관리 비중 큼 | 주로 애플리케이션 코드·설정에 집중 |\n"
        "| 특징 | 세부 인프라 통제·커스터마이징에 유리 | 배포·운영 단순화·개발 속도에 유리 |\n"
    )


_STOCK_SYSTEM_PROMPTS_FROZEN = frozenset(
    {
        "친절한 클라우드 학습 도우미입니다.",
        "친절하고 명확하게 답변해주는 클라우드 학습 도우미입니다.",
    }
)


def _is_stock_system_prompt(persona: str) -> bool:
    """DB에 넣어 둔 기본 문구와 같으면 '관리자 커스텀'으로 보지 않습니다."""
    return (persona or "").strip() in _STOCK_SYSTEM_PROMPTS_FROZEN


def _persona_first_line(persona: str, *, max_len: int = 220) -> str:
    """시스템 프롬프트에서 첫 번째 의미 있는 한 줄만 잘라 돌려줍니다."""
    for raw in persona.splitlines():
        s = raw.strip()
        if s:
            if len(s) > max_len:
                return s[: max_len - 1] + "…"
            return s
    return ""


def _document_base_title(chunk_title: str) -> str:
    """`파일.pdf (p.N)` 형태에서 파일명만 뽑습니다."""
    return re.sub(r"\s*\(p\.\d+\)\s*", "", chunk_title).strip()


def _bibliography_ref_token(chunk: RetrievalChunk) -> str:
    """
    답변 말미 📚 참고 문헌에 넣을 **페이지**(``Np``)·**동영상 구간**(텍스트)·**이미지** 표기 하나를 만듭니다.

    PDF는 기존처럼 페이지 번호로 묶고, 동영상·이미지는 ``1p`` 대신 사람이 읽기 쉬운 레이블을 씁니다.

    Args:
        chunk: LLM 또는 Mock 답변에 실린 검색 청크.

    Returns:
        참고 줄 오른쪽에 오는 토큰(예: ``18p``, ``영상 전체``, ``약 01:05–06:40 (추정)``).
    """
    title = chunk.title or ""
    if "(동영상)" in title:
        band = (chunk.ref_time_band or "").strip()
        return band if band else "영상 전체 (요약)"
    if "(이미지)" in title:
        return "이미지 (요약)"
    return f"{chunk.page}p"


def _is_kb_media_summary_chunk(chunk_title: str) -> bool:
    """
    관리자 파이프라인에서 번들 제목 끝에 붙는 ``(동영상)`` · ``(이미지)`` 요약 청크인지 판별합니다.

    PDF 추출본용 ``_clean_chunk_content``(하이픈·꺾쇠 제거 등)를 거치면 CLI·UI 경로 표기가 깨질 수 있어
    Q&A용 발췌 경로를 나눕니다.

    Args:
        chunk_title: ``RetrievalChunk.title`` 값입니다.

    Returns:
        미디어 요약 청크이면 True.
    """
    t = chunk_title or ""
    return "(동영상)" in t or "(이미지)" in t


def _strip_pdf_boilerplate(raw: str) -> str:
    """목차·머리글·저작권 등 검색·요약에 방해되는 반복 문구를 줄입니다."""
    if not raw:
        return ""
    t = raw
    t = re.sub(r"ⓒ\s*\d{4}[\s\S]{0,220}?(?:Reserved\.?|Ltd\.?)", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"©\s*\d{4}[\s\S]{0,220}?Reserved\.?", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"(?:\(c\)|\(C\))\s*\d{4}[\s\S]{0,220}?Reserved\.?", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"OKESTRO\s+Co\.?\s*,?\s*Ltd\.?", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"All\s+Rights\s+Reserved\.?", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"Oke\s+Wiki\s*\([^)]*\)", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d{1,2}C\s*>\s*", " ", t)
    t = re.sub(r"(?:^|\s)\d{1,3}\s+(?=ⓒ|©|\(c\)|\(C\))", " ", t)
    return t


def _clean_chunk_content(raw: str) -> str:
    """Mock 스니펫용으로 본문을 한 줄로 정리합니다."""
    raw = _strip_pdf_boilerplate(raw)
    clean_content = re.sub(r"\n+", " ", raw)
    clean_content = re.sub(r"(?:©|\(c\)|\(C\)).*?Reserved\.", "", clean_content, flags=re.IGNORECASE)
    clean_content = re.sub(r"[\->|]", "", clean_content)
    return re.sub(r"\s+", " ", clean_content).strip()


def _mock_readability_spacing(text: str) -> str:
    """
    PDF에서 줄바꿈이 사라져 한글·영문·숫자가 붙어 나올 때, Mock 표시만 조금 읽기 쉽게 띄웁니다.

    형태소 분석은 하지 않고 경계 문자만 건드려 오탐을 줄입니다.

    Args:
        text: 정리된 한 줄 스니펫.

    Returns:
        가벼운 공백·문장 구분이 들어간 문자열.
    """
    if not text:
        return ""
    s = text
    s = re.sub(r"\?([가-힣ㄱ-ㅎ])", r"? \1", s)
    s = re.sub(r"!([가-힣ㄱ-ㅎ])", r"! \1", s)
    s = re.sub(r"\.([가-힣ㄱ-ㅎ])", r". \1", s)
    s = re.sub(r"([가-힣])([A-Za-z]{2,})", r"\1 \2", s)
    s = re.sub(r"([A-Za-z]{2,})(?=[가-힣])", r"\1 ", s)
    s = re.sub(r"(\d)(?=[가-힣ㄱ-ㅎ])", r"\1 ", s)
    s = re.sub(r"(?<=[가-힣])(\d{2,})", r" \1", s)
    return re.sub(r"\s+", " ", s).strip()


def _video_kb_placeholder_card_snippet(raw: str, *, max_len: int) -> str | None:
    """동영상 Gemini 요약 실패 시 저장된 고정 플레이스홀더면 출처 카드용 짧은 안내로 바꿉니다.

    ``content`` 가 그대로 남아 있으면 출처 찾기 카드에 에러 문구가 노출되므로,
    교육생에게는 **원인 요약 + 다음 조치**만 보여 줍니다(지식 베이스 원문은 변경하지 않음).

    Args:
        raw: 청크 ``content`` 원문.
        max_len: 카드 스니펫 상한(말줄임에 맞춤).

    Returns:
        치환 문구 또는 플레이스홀더가 아니면 None.
    """
    s = (raw or "").strip()
    if not s.startswith("[동영상 자료]"):
        return None
    msg: str | None = None
    if "내용 추출에 실패했습니다" in s:
        msg = (
            "동영상 자동 요약이 완료되지 않았습니다. 관리자 화면에서 해당 파일을 다시 업로드·재색인해 보시고, "
            "API 키·할당량·영상 길이·네트워크를 확인해 주세요."
        )
    elif "요약 호출 도중 연결이 끊겼습니다" in s:
        msg = (
            "동영상 요약 중 연결이 끊겼습니다(타임아웃 등). GEMINI_VIDEO_RPC_TIMEOUT_SEC·재시도 설정을 늘리거나 "
            "영상을 나눠 올린 뒤 재색인해 주세요."
        )
    if msg is None:
        return None
    if len(msg) <= max_len:
        return msg
    return msg[: max(1, max_len - 1)].rstrip() + "…"


def source_search_card_snippet(raw: str, keyword: str, *, max_len: int = 380) -> str:
    """
    출처 찾기 결과 카드 아래에 붙일 **짧은 미리보기** 한 줄을 만듭니다.

    ``_clean_chunk_content`` 로 저작권·머리글 등을 줄인 뒤, 남은 PDF 찌꺼기(번호·브레드크럼 등)와
    중간 목차 문자열을 조금 더 덜어내고, 가능하면 **검색어가 나타나는 구간**만 잘라 보여 줍니다.
    긴 원문 미리보기보다 카드에서는 내용 한가운데 위주가 나오도록 합니다.

    Args:
        raw: 페이지 청크의 ``content``.
        keyword: 사용자가 입력한 검색어 또는 질문.
        max_len: 말줄임을 포함한 목표 길이 상한입니다.

    Returns:
        카드용 한 줄 문자열(비었면 짧은 안내 문구).
    """
    ph = _video_kb_placeholder_card_snippet(raw or "", max_len=max_len)
    if ph is not None:
        return ph
    t = _clean_chunk_content(raw or "")
    # PDF 하단 «26 » 같이 본문 직전 페이지 숫자 한두 개는 출처 카드에서 제거(짧은 줄 제목은 드묾).
    for _ in range(2):
        t = re.sub(r"^\s*\d{1,3}\s+", "", t).strip()
    for _ in range(6):
        old_t = t
        t = re.sub(
            r"^\s*\d{1,3}\s+(?=\d{1,3}\s|\d{1,2}[Cc]\s|[ⓒ©]|[Ww]iki\b|[Ww]ikipedia\b)",
            "",
            t,
        )
        t = re.sub(r"^\s*\d{1,2}[Cc]\s+", "", t)
        t = re.sub(r"\b\d{1,3}\s+[cC]{1,10}\s*>\s*", " ", t)
        t = re.sub(r"\b\d{1,2}[Cc]\s*>\s*", " ", t, flags=re.IGNORECASE)
        t = re.sub(r"\s+", " ", t).strip()
        if t == old_t:
            break
    if not t or len(t) < 14:
        return "이 페이지에는 별도 미리보기가 없거나 본문이 짧습니다.「보기」에서 PDF 원문을 열어 주세요."

    excerpt = t
    kw = (keyword or "").strip()
    if kw:
        low_excerpt = excerpt.lower()
        kw_l = kw.lower()
        idx = low_excerpt.find(kw_l) if kw_l else -1
        if idx < 0:
            for tok in _normalize_tokens(keyword):
                if len(tok) < 2:
                    continue
                j = excerpt.lower().find(tok.lower())
                if j >= 0:
                    idx = j
                    break
        if idx >= 0:
            pad_b = 48
            pad_a = min(320, max_len + 40)
            start = max(0, idx - pad_b)
            end = min(len(excerpt), idx + len(kw) + pad_a)
            excerpt = excerpt[start:end]
            if start > 0:
                excerpt = "…" + excerpt
            if end < len(t):
                excerpt = excerpt + "…"
        elif len(excerpt) > max_len:
            excerpt = excerpt[: max_len - 1].rstrip() + "…"
    elif len(excerpt) > max_len:
        excerpt = excerpt[: max_len - 1].rstrip() + "…"

    excerpt = _mock_readability_spacing(excerpt)
    if len(excerpt) > max_len + 24:
        excerpt = excerpt[:max_len].rstrip() + "…"
    return excerpt if excerpt.strip() else t[:max_len]


def _chunk_text_for_gemini(c: RetrievalChunk, max_chars: int = 3800) -> str:
    """
    LLM에게 넘길 때 목차·저작권 잡음을 줄인 청크 본문을 만듭니다.

    원문 페이지 인용에는 ``c.title``·page 번호가 남도록 별도 레이블로 주고,
    내용만 짧게 정리해 답변의 문장 흐름이 나아지도록 합니다.

    동영상·이미지 요약 청크는 PDF용 정리(``_clean_chunk_content``)를 거치지 않고
    줄바꿈·기호를 유지해 기술 용어·경로가 깨지지 않게 합니다.

    Args:
        c: 검색으로 고른 페이지 청크.
        max_chars: 모델 입력 부담 상한.

    Returns:
        정제·truncate된 한 덩어리 문자열.
    """
    raw = c.content or ""
    if _is_kb_media_summary_chunk(c.title or ""):
        hint = ""
        if "(동영상)" in (c.title or ""):
            band = (getattr(c, "ref_time_band", None) or "").strip()
            if band:
                hint = (
                    "[시간 정보] 이 동영상 요약이 UI·출처에 연결되는 구간 표기: 「"
                    + band
                    + "」. 답변에 이 영상을 근거로 쓸 때는 가능한 한 **추정 재생 시각**을 "
                    + "``약 3분 20초`` 또는 ``(약 05:10–08:40)``처럼 본문 안에 넣어 주세요.\n\n"
                )
        t = (hint + raw).strip()
        t = re.sub(r"\r\n?", "\n", t)
        t = re.sub(r"\n{5,}", "\n\n\n\n", t)
        t = t.strip()
        media_cap = max(max_chars, 8000)
        if len(t) <= media_cap:
            return t
        return t[:media_cap].rstrip() + "…"

    merged = _strip_pdf_boilerplate(raw)
    t = _clean_chunk_content(merged)
    if len(t) <= max_chars:
        return t
    return t[:max_chars].rstrip() + "…"


def _bundle_chunks_for_summary(
    contexts: list[RetrievalChunk],
    *,
    max_chunks: int,
    max_chars_per_chunk: int,
    max_total_chars: int,
) -> str | None:
    """청크를 페이지 순으로 묶어 LLM 입력 문자열을 만듭니다."""
    excerpts: list[str] = []
    total = 0
    ordered = sorted(contexts, key=lambda c: (c.title, c.page))
    for c in ordered[:max_chunks]:
        stripped = _strip_pdf_boilerplate(c.content)
        cc = _clean_chunk_content(stripped)
        if len(cc) < 22:
            continue
        piece = cc[:max_chars_per_chunk]
        remain = max_total_chars - total
        if remain < 180:
            break
        if len(piece) > remain:
            piece = piece[:remain]
        label = f"{_document_base_title(c.title)} · {c.page}p"
        block = f"[{label}]\n{piece}"
        excerpts.append(block)
        total += len(block) + 8

    if not excerpts:
        return None

    return "\n\n---\n\n".join(excerpts)


def _snippet_around_keyword(query: str, clean_content: str, *, pad_before: int = 80, pad_after: int = 280) -> str:
    """질문 토큰(한글 n-gram 포함)이 들어간 구간 위주로 스니펫을 만듭니다."""
    low = clean_content.lower()
    for kw in _normalize_tokens(query):
        if len(kw) < 2:
            continue
        idx = low.find(kw.lower())
        if idx != -1:
            start = max(0, idx - pad_before)
            end = min(len(clean_content), idx + pad_after)
            snippet = clean_content[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(clean_content):
                snippet = snippet + "..."
            return snippet
    cap = 850
    body = clean_content[:cap] + ("…" if len(clean_content) > cap else "")
    return body


def _mock_evidence_sections(query: str, contexts: list[RetrievalChunk], *, max_docs: int = 4, chunks_per_doc: int = 2) -> str:
    """
    Mock Q&A 본문용: BM25 상위 청크를 문서·페이지별로 묶어, 질문과 맞는 구간 발췌를 여러 개 이어 붙입니다.

    청크 하나만 쓰면 검색 미리보기처럼 보이므로, 문서당 최대 `chunks_per_doc`개까지 발췌를 넣습니다.
    """
    by_doc: dict[str, list[RetrievalChunk]] = {}
    order: list[str] = []
    for c in contexts:
        base = _document_base_title(c.title)
        if base not in by_doc:
            by_doc[base] = []
            order.append(base)
        row = by_doc[base]
        if len(row) >= chunks_per_doc:
            continue
        if any(x.page == c.page for x in row):
            continue
        row.append(c)

    sections: list[str] = []
    n_doc = 0
    for base in order:
        if n_doc >= max_docs:
            break
        row = by_doc.get(base) or []
        if not row:
            continue
        n_doc += 1
        parts: list[str] = []
        for ch in row:
            cc = _clean_chunk_content(ch.content)
            sn = _mock_readability_spacing(_snippet_around_keyword(query, cc))
            parts.append(f"> **p.{ch.page}** · {sn}")
        sections.append(f"**{base}**\n" + "\n".join(parts))
    return "\n\n".join(sections)


def _quiz_difficulty_instruction(difficulty: str) -> str:
    """
    퀴즈 LLM 프롬프트에 붙일 난이도 지침 블록을 만듭니다(Q&A 난이도와 같은 체계).

    Args:
        difficulty: ``초급`` / ``중급`` / ``고급``(호출 측에서 이미 정규화했다고 가정).

    Returns:
        프롬프트에 삽입할 여러 줄 규칙 문자열.
    """
    d = (difficulty or "초급").strip()
    if d == "고급":
        return (
            "- **출제 난이도: 고급** — 동작 원리·트레이드오프·오개념 구분·운영·설계 맥락을 묻는 문항을 우선합니다.\n"
            "- 보기에 그럴듯한 기술적 함정을 섞을 수 있고, 해설은 근거 위주로 간결하게 다룹니다."
        )
    if d == "중급":
        return (
            "- **출제 난이도: 중급** — 개념 정의에 더해 적용·비교·간단한 시나리오 판단을 포함합니다.\n"
            "- 전문 용어는 자료에 나온 범위 안에서 쓰며, 해설은 핵심만 명확히 적습니다."
        )
    return (
        "- **출제 난이도: 초급** — 핵심 용어·정의·기본 개념 위주로, 한 문항에 한 주제를 다룹니다.\n"
        "- 질문·보기·해설은 짧고 이해하기 쉬운 말로 쓰며, 어려운 용어가 나오면 한 줄로 풀어 설명합니다."
    )


class LLMService:
    def answer(self, query: str, contexts: list[RetrievalChunk], difficulty: str) -> str:
        prompt = get_settings("system_prompt") or "친절한 클라우드 학습 도우미입니다."

        if is_greeting_query(query):
            tag = _persona_first_line(prompt, max_len=180)
            if tag and not _is_stock_system_prompt(prompt):
                return f"*{tag}*\n\n{GREETING_REPLY}"
            return GREETING_REPLY

        if not contexts:
            return f"죄송합니다. '{query}'와(과) 관련된 정보를 문서에서 찾을 수 없었습니다. 다른 키워드로 다시 질문해 주시겠어요?"

        # --- 진짜 AI (Gemini) 연동 ---
        if settings.LLM_API_KEY:
            try:
                import google.generativeai as genai

                genai.configure(api_key=settings.LLM_API_KEY)
                model = genai.GenerativeModel(
                    "gemini-2.5-flash",
                    system_instruction=(
                        f"{prompt}\n\n"
                        "위 지침은 모든 답변에서 일관되게 지키세요. "
                        "사용자 메시지에 주어진 [참고 문서] 밖의 추측은 하지 마세요.\n"
                        "한국어 답변은 문장이 건조하게 나열되지 않게 접속어와 적절한 종결로 읽기 좋게 이어 주세요."
                    ),
                )

                # 여러 문서의 내용을 하나로 합침(목차·반복 줄 정리 후 전달해 문장 생성이 매끄럽게 돕습니다)
                context_text = "\n\n".join(
                    [f"[문서: {c.title}]\n{_chunk_text_for_gemini(c)}" for c in contexts]
                )

                ai_prompt = f"""다음 기술 규칙을 반드시 따르세요.
- 반드시 제공된 [참고 문서] 내용만을 바탕으로 답변하세요.
- [참고 문서]에 서로 다른 주제가 섞여 있으면, **질문과 직접 관련된 문단만** 골라 쓰고, 관련 없어 보이는 문단은 인용·요약하지 마세요.
- 제목에 「(동영상)」또는「(이미지)」가 붙은 문서는 PDF 페이지가 아니라 **영상·그림 한 편 전체를 압축한 요약**입니다. 질문과 근거가 맞을 때만 쓰고, PDF 내용과 **섞어서 새 사실을 조합·추론하지 마세요**. 동영상 문서 안에 **[질문 예상 Q&A]**, **[사실·수치·이름]**, **[순서·절차]** 등 섹션이 있으면 그 안의 문장·수치·경로를 우선 근거로 삼고 빠뜨리지 마세요.
- **답변 형식:** 단순 검색 결과처럼 인용만 나열하지 말고, 질문에 맞게 문장을 **재구성**하세요.
  · 첫 부분에 질문에 대한 **핵심 답(1~2문장)** 을 먼저 제시하세요.
  · 이어서 근거·세부 설명을 **2개 이상의 단락**(빈 줄로 구분)으로 쓰세요.
  · [참고 문서]의 표현을 그대로 길게 베껴 쓰기보다, 독자가 이해하기 쉬운 말로 **풀어 설명**하세요.
  · 한국어는 **종결 어미와 접속어**(또한, 한편, 정리하면, 예를 들어 등)를 써서 문장이 덜 끊기게 **말하듯** 이어 주세요.
  · 문장이 너무 길어지면 쉼표나 마침표로 **호흡**을 나누세요. 제목·머리글 나열 톤은 피하세요.
- 페이지·출처 인용은 반드시 **완성된 짧은 형태**만 쓰세요. 예: ``(Cloud_Overview.pdf · p.18)``, ``(파일명.pdf, 20p)``. 동영상 요약은 페이지 대신 **구간과 추정 시간**을 씁니다—예: ``(강의클립.mp4 · 구간: 영상 전체)``, ``(... · 구간: 약 01:05–06:40)``, 본문 설명 속에는 가능하면 **「약 N분 M초」** 표현도 포함하세요. 이미지 요약은 ``(슬라이드.png · 이미지 자료)``.
  **절대 금지:** ``[문``, ``[문서:`` 처럼 **열린 대괄호만 두고 문장을 끝내거나** 인용을 미완성으로 두는 것입니다.
- 답변 끝까지 문장을 마친 후(종결어미 포함) 참고 블록이 이어져야 합니다.
- 만약 여러 문서가 제공되었다면, 각 문서의 차이점이나 보완적인 내용을 종합하여 답변하세요.
- 질문에 **비교·차이** 의미가 있으면(예: 차이, 비교, vs, 대비, versus, differentiate, compare) 문서 근거가 될 때 답변 **본문 안에 반드시** GitHub 스타일 **마크다운 파이프 표**를 포함하세요. 표 없이 글만으로 비교만 서술하는 것은 피하세요.
  · 표 형식(필수): 헤더 행 한 줄 → 다음 줄 구분선(`| :--- | :--- |` 또는 3열 이상이면 열 개수에 맞춤) → 그 아래 데이터 행들. HTML `<table>` 태그는 사용하지 마세요.
  · 비교 대상이 둘(IaaS와 PaaS 등)이면 **최소 3개 이상의 행**(비교 항목 축)으로 표를 채우세요.
  · 열 이름에 비교 대상 명칭을 넣고, 행에는 제공 범위·관리 책임·유연성·적합한 사용 예 등 문서에 근거한 축을 넣습니다.
  · 표 셀은 짧게 쓰고, 긴 비유·설명은 표 앞·뒤 본문에 두세요.
  · 비교 대상이 3개 이상이면 표 가로가 너무 넓어지지 않게 핵심 축만 표에 넣고 나머지는 글로 보완합니다.

[참고 문서]
{context_text}

[질문]
{query}

시스템 역할(시스템 지시)을 유지한 채 위 규칙에 맞게 답하세요. 답변은 '{difficulty}' 수준에 맞춰야 합니다.
"""
                response = model.generate_content(
                    ai_prompt,
                    generation_config=genai.types.GenerationConfig(
                        # Gemini 2.5류는 추론(내부 thinking)용 토큰을 포함해 한도를 쓸 수 있어,
                        # 2048에서는 본문이 중간(예: ``[문`` 직후)에서 끊긴 것처럼 보일 때가 있습니다.
                        max_output_tokens=16384,
                        temperature=0.55,
                    ),
                )

                text_out = (getattr(response, "text", None) or "").strip()
                if not text_out and getattr(response, "candidates", None):
                    try:
                        cand = response.candidates[0]
                        parts = getattr(getattr(cand, "content", None), "parts", None) or []
                        text_out = "".join(getattr(p, "text", "") or "" for p in parts).strip()
                    except (IndexError, AttributeError, ValueError):
                        text_out = ""

                try:
                    c0 = response.candidates[0]
                    fr = getattr(c0, "finish_reason", None)
                    fr_name = (getattr(fr, "name", None) or str(fr or "")).upper()
                    if fr_name == "MAX_TOKENS":
                        tail = (
                            "\n\n> ⚠️ **알림:** 모델 출력 길이 제한에 걸려 답변이 일부만 전달된 것 같습니다."
                            "\n원하시면 질문을 나누어 다시 보내 주세요."
                        )
                        text_out = (text_out or "") + tail
                except (IndexError, AttributeError, TypeError):
                    pass

                # 실제 검색된 모든 페이지 번호와 파일명을 매핑 (중복 제거 및 그룹화)
                from collections import defaultdict

                ref_dict: defaultdict[str, list[str]] = defaultdict(list)
                for c in contexts:
                    name = _document_base_title(c.title)
                    ref_dict[name].append(_bibliography_ref_token(c))

                ref_parts = []
                for name in sorted(ref_dict.keys()):
                    pages_joined = ", ".join(sorted(set(ref_dict[name])))
                    ref_parts.append(f"- {name}: {pages_joined}")

                additional_info = f"\n\n📚 **참고 문헌:**\n" + "\n".join(ref_parts)
                return (text_out or "(응답 본문을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.)") + additional_info
            except Exception as e:
                return f"⚠️ AI 생성 중 오류가 발생했습니다. (API 키를 확인해주세요): {str(e)}"
        # -----------------------------

        # 여러 컨텍스트를 조합하여 답변 생성 (Mock). LLM 없을 때도 문서당·페이지별로 발췌를 여러 개 붙여 검색 미리보기 느낌을 줄입니다.
        evidence = _mock_evidence_sections(query, contexts, max_docs=5, chunks_per_doc=2)

        from collections import defaultdict

        ref_dict = defaultdict(list)
        for c in contexts:
            name = _document_base_title(c.title)
            ref_dict[name].append(_bibliography_ref_token(c))

        ref_parts = []
        for name in sorted(ref_dict.keys()):
            pages = ", ".join(sorted(set(ref_dict[name])))
            ref_parts.append(f"- {name}: {pages}")

        additional_info = f"\n\n📚 **참고 문헌:**\n" + "\n".join(ref_parts) if contexts else ""

        bases_ordered: list[str] = []
        seen_base: set[str] = set()
        for c in contexts:
            b = _document_base_title(c.title)
            if b not in seen_base:
                seen_base.add(b)
                bases_ordered.append(b)

        doc_count = len(bases_ordered)
        doc_labels = ", ".join(bases_ordered[:8])
        qshort = query.strip() or "이 주제"

        multi_note = (
            f"자료가 **{doc_count}종**({doc_labels})이라, 같은 질문에 대한 설명이 문서마다 나뉘어 있을 수 있어요."
            if doc_count > 1
            else ""
        )

        response = ""
        # Mock 모드에서는 교육생 화면에 관리자 시스템 프롬프트 원문 서두를 붙이지 않습니다.
        if difficulty == "고급":
            response = (
                f"🎓 **맞춤형 고급 답변**\n\n"
                f"「{qshort}」에 대해 **업로드된 학습 자료**에서 질문과 맞는 문단을 골라 정리했습니다. "
                f"{multi_note}\n\n"
                f"### 문서 근거(페이지별)\n\n{evidence}\n\n"
                f"설계·아키텍처 관점에서 해석할 때는 같은 페이지를 원문과 함께 대조해 보시는 것이 좋습니다.{additional_info}"
            )
        elif difficulty == "중급":
            response = (
                f"📝 **핵심 요약**\n\n"
                f"「{qshort}」와(과) 연결되는 내용을 자료에서 찾아 **문장으로 풀기 전 단계**인 근거 묶음으로 보여 드립니다. "
                f"{multi_note}\n\n"
                f"### 근거가 되는 문단\n\n{evidence}\n\n"
                f"개념의 윤곽을 잡은 뒤에는 아래 참고 문헌 페이지를 넓혀 읽어 보세요.{additional_info}"
            )
        else:
            response = (
                f"💡 **신입사원용 정리**(자료 원문 발췌)\n\n"
                f"「{qshort}」에 대한 설명으로 자료에서 **질문과 맞아 보이는 문단**을 골라 왔어요. "
                f"{multi_note}\n\n"
                f"아래 블록(>)은 페이지별로 적어 둔 **원문 근거**예요.\n\n"
                f"{evidence}\n\n"
                f"더 말랑하게 한 덩어리 설명으로 듣고 싶다면, `.env`에 **LLM API 키**(`LLM_API_KEY` 등)가 설정된 상태로 다시 질문해 보세요.{additional_info}"
            )

        if _is_iaas_paas_comparison_question(query):
            response = response + "\n\n" + _iaas_paas_markdown_table()

        return response

    def summarize_topic(
        self,
        topic: str,
        contexts: list[RetrievalChunk],
        *,
        difficulty: str = "초급",
    ) -> str | None:
        """
        주제별 요약 탭용 짧은 마크다운을 생성합니다.

        API 키가 없거나 오류 시 None을 반환하고, 호출 측에서 발췌 기반 폴백을 씁니다.
        """
        topic = topic.strip()
        if not contexts or not topic:
            return None

        bundle = _bundle_chunks_for_summary(
            contexts,
            max_chunks=12,
            max_chars_per_chunk=1600,
            max_total_chars=22000,
        )
        if not bundle:
            return None

        if settings.LLM_API_KEY:
            try:
                import google.generativeai as genai

                genai.configure(api_key=settings.LLM_API_KEY)
                model = genai.GenerativeModel("gemini-2.5-flash")
                prompt = f"""역할: 교육용 클라우드 문서 요약 작성자.

요약 주제: 「{topic}」

아래 [발췌]는 PDF에서 추출된 텍스트입니다.
목차·전역 메뉴·저작권·위키 경로(예: 7C > …)·중복 목차 나열은 요약에 넣지 마세요.

규칙:
- [발췌]에 적힌 내용만 사용하고, 외부 지식이나 추측은 쓰지 마세요.
- 주제와 직접 관련 있는 내용만 골라 **4~7개** 불릿으로 요약하세요.
- 각 불릿은 **한두 문장**으로 끝내세요.
- 마크다운 불릿은 반드시 `- ` 로 시작합니다.
- 각 불릿 문장 끝에 출처를 `(파일명 일부 · Np)` 형태로 붙이세요. 파일명·페이지는 대괄호 레이블과 맞추세요.
- 표·코드블록은 사용하지 마세요.

독자 난이도: {difficulty}

[발췌]
{bundle}

위 규칙을 지켜 요약만 출력하세요."""

                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(max_output_tokens=8192, temperature=0.42),
                )
                out = (response.text or "").strip()
                return out or None
            except Exception:
                return None

        return None

    def summarize_span(
        self,
        span_label: str,
        contexts: list[RetrievalChunk],
        *,
        scope: Literal["range", "all"],
        difficulty: str = "초급",
    ) -> str | None:
        """
        페이지 구간·전체 스캔 요약 탭용 통합 서술 마크다운을 생성합니다.

        범위 안의 가능한 많은 청크를 묶어 한 번에 요약해, 페이지별 나열이 아닌 흐름 중심 결과를 기대합니다.
        API 키가 없거나 오류 시 None.
        """
        if not contexts:
            return None

        bundle = _bundle_chunks_for_summary(
            contexts,
            max_chunks=96,
            max_chars_per_chunk=3400,
            max_total_chars=72000,
        )
        if not bundle:
            return None

        if scope == "range":
            scope_hint = (
                "이 자료는 사용자가 지정한 **연속 페이지 구간**에서 추출된 본문입니다. "
                "**페이지 번호 순으로 항목만 나열하지 마세요.** 구간 전체가 하나의 글로 읽히도록 "
                "**주제·개념의 전개와 연결**을 통합해 서술하세요."
            )
        else:
            scope_hint = (
                "자료는 문서 **여러 구간에서 샘플링**된 페이지입니다(모든 페이지가 포함된 것은 아님). "
                "과장하지 말고, 발췌 안에서 보이는 **반복 축·공통 주제**를 중심으로 문서 전반의 그림을 조심스럽게 정리하세요."
            )

        if settings.LLM_API_KEY:
            try:
                import google.generativeai as genai

                genai.configure(api_key=settings.LLM_API_KEY)
                model = genai.GenerativeModel("gemini-2.5-flash")
                prompt = f"""역할: 클라우드 학습 자료 편집자.

문체는 교육용으로 명료하게. 독자 난이도: {difficulty}

[발췌]
{bundle}

위 형식을 지켜 결과만 출력하세요."""

                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(max_output_tokens=8192, temperature=0.42),
                )
                out = (response.text or "").strip()
                return out or None
            except Exception:
                return None

        return None

    def _strip_code_fence_json(self, text: str) -> str:
        """모델이 마크다운 코드 펜스로 감싼 경우 안쪽 JSON 문자열만 꺼냅니다.

        Args:
            text: Gemini가 돌려준 원문입니다.

        Returns:
            펜스를 제거한 문자열. 펜스가 없으면 공백만 정리한 원문입니다.
        """
        t = (text or "").strip()
        if not t.startswith("```"):
            return t
        lines = t.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def _parse_quiz_json_array(self, raw: str) -> list:
        """퀴즈 JSON 배열을 느슨하게 잘라 ``json.loads`` 합니다.

        Args:
            raw: 모델 전체 출력.

        Returns:
            파싱된 파이썬 리스트. 실패하면 빈 리스트.
        """
        t = self._strip_code_fence_json(raw)
        i0 = t.find("[")
        i1 = t.rfind("]")
        if i0 < 0 or i1 <= i0:
            return []
        try:
            data = json.loads(t[i0 : i1 + 1])
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def _quiz_item_from_dict(self, d: object, quiz_type: str) -> QuizQuestion | None:
        """JSON 객체 한 덩어리를 ``QuizQuestion`` 으로 검증·변환합니다.

        Args:
            d: 배열의 원소(보통 dict).
            quiz_type: ``mcq`` / ``ox`` / ``mix`` (mix는 kind에 따라 4지 또는 OX 허용).

        Returns:
            유효하면 모델 객체, 아니면 None.
        """
        if not isinstance(d, dict):
            return None
        q = str(d.get("q") or "").strip()
        if len(q) < 4:
            return None
        e = str(d.get("e") or "").strip() or "발췌 내용을 다시 확인해 보세요."
        kind_raw = str(d.get("kind") or "").lower()
        effective = quiz_type
        if quiz_type == "mix":
            if kind_raw in ("ox", "true_false", "tf", "o"):
                effective = "ox"
            else:
                effective = "mcq"
        if effective == "ox":
            try:
                a = int(d.get("a", 0))
            except (TypeError, ValueError):
                return None
            if a not in (0, 1):
                return None
            return QuizQuestion(q=q, c=["O", "X"], a=a, e=e)
        c = d.get("c")
        if not isinstance(c, list) or len(c) != 4:
            return None
        opts = [str(x).strip() for x in c]
        if any(len(o) < 1 for o in opts) or len(set(opts)) < 4:
            return None
        try:
            a = int(d.get("a", 0))
        except (TypeError, ValueError):
            return None
        if a not in (0, 1, 2, 3):
            return None
        return QuizQuestion(q=q, c=opts, a=a, e=e)

    def generate_quiz_from_documents(
        self,
        context_bundle: str,
        quiz_type: str,
        count: int,
        difficulty: str = "초급",
    ) -> list[QuizQuestion]:
        """지식 베이스 발췌를 근거 문자열로 받아 Gemini 에게 퀴즈 JSON 생성을 맡깁니다.

        ``LLM_API_KEY`` 가 없거나 출력이 비정상이면 **빈 리스트**를 돌려 호출 측이 샘플 문제 풀로
        채우도록 합니다.

        Args:
            context_bundle: 문서 제목·페이지 레이블과 본문 발췌를 이어 붙인 문자열입니다.
            quiz_type: 출제 유형(4지·OX·혼합).
            count: 사용자가 요청한 문제 수(1~50).
            difficulty: ``초급``/``중급``/``고급`` — 문항·보기·해설의 깊이를 조절하는 지침으로 씁니다.

        Returns:
            검증을 통과한 ``QuizQuestion`` 리스트(최대 ``count``개).
        """
        if not settings.LLM_API_KEY:
            return []
        bundle = (context_bundle or "").strip()
        if len(bundle) < 80:
            return []
        n = max(1, min(50, int(count)))
        lvl = difficulty if str(difficulty).strip() in ("초급", "중급", "고급") else "초급"
        if quiz_type == "ox":
            type_hint = "모든 문항을 OX 형식(kind는 ox, 보기는 반드시 [\"O\",\"X\"] 순서)으로 출제합니다."
        elif quiz_type == "mix":
            type_hint = (
                "4지선다(kind mcq)와 OX(kind ox)를 골고루 섞어 출제합니다. "
                "가능하면 OX 비중이 전체의 약 절반이 되도록 합니다."
            )
        else:
            type_hint = "모든 문항을 4지선다(kind는 mcq)로만 출제합니다."

        diff_hint = _quiz_difficulty_instruction(lvl)
        temp_by_level = {"초급": 0.42, "중급": 0.5, "고급": 0.55}
        temperature = temp_by_level.get(lvl, 0.5)

        prompt = f"""역할: 클라우드·IT 교육 챗봇의 「퀴즈」탭 출제 도우미.

아래 [자료 발췌]에 적힌 내용에만 근거해 문제를 만드세요. 발췌에 없는 사실은 넣지 마세요.

요청:
- 문항 개수: **정확히 {n}개**
- {type_hint}
{diff_hint}

출력 규격(매우 중요):
- 설명 문장 없이 **JSON 배열만** 한 블록으로 출력합니다.
- 각 원소 예:
  - 4지선다: {{"kind":"mcq","q":"질문?","c":["보기1","보기2","보기3","보기4"],"a":정답 인덱스 0~3,"e":"해설"}}
  - OX: {{"kind":"ox","q":"참·거짓을 판별할 한 문장.","c":["O","X"],"a":0 또는 1,"e":"해설"}}

[자료 발췌]
{bundle}
"""

        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.LLM_API_KEY)
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(max_output_tokens=8192, temperature=temperature),
            )
            raw = (getattr(response, "text", None) or "").strip()
            if not raw and getattr(response, "candidates", None):
                try:
                    cand = response.candidates[0]
                    parts = getattr(getattr(cand, "content", None), "parts", None) or []
                    raw = "".join(getattr(p, "text", "") or "" for p in parts).strip()
                except (IndexError, AttributeError, TypeError):
                    raw = ""
            arr = self._parse_quiz_json_array(raw)
            out: list[QuizQuestion] = []
            for item in arr:
                qq = self._quiz_item_from_dict(item, quiz_type)
                if qq is None:
                    continue
                if any(existing.q.strip() == qq.q.strip() for existing in out):
                    continue
                out.append(qq)
                if len(out) >= n:
                    break
            return out
        except Exception:
            return []

    def _parse_json_object_loose(self, raw: str) -> dict | None:
        """모델 출력에서 단일 JSON 객체를 느슨하게 잘라 파싱합니다(용어 비교 등).

        Args:
            raw: 모델 출력 전체 문자열.

        Returns:
            dict 또는 실패 시 None.
        """
        t = self._strip_code_fence_json(raw)
        i0 = t.find("{")
        i1 = t.rfind("}")
        if i0 < 0 or i1 <= i0:
            return None
        try:
            obj = json.loads(t[i0 : i1 + 1])
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    def compare_terms_with_context(
        self,
        term_a: str,
        term_b: str,
        context_bundle: str,
    ) -> CompareTermsResponse | None:
        """두 용어를 [자료 발췌] 근거로만 비교한 뒤 JSON ``left``·``right``·``diff`` 로 돌려받습니다.

        API 키가 없거나 JSON이 비정상이면 None 입니다(호출 측에서 내장 비교 문구로 폴백).

        Args:
            term_a: 첫 번째 용어 라벨.
            term_b: 두 번째 용어 라벨.
            context_bundle: 검색으로 모은 청크 발췌 문자열.

        Returns:
            파싱 성공 시 ``CompareTermsResponse``, 아니면 None.
        """
        if not settings.LLM_API_KEY:
            return None
        bundle = (context_bundle or "").strip()
        if len(bundle) < 60:
            return None
        ta = (term_a or "").strip()
        tb = (term_b or "").strip()
        if not ta or not tb:
            return None

        prompt = f"""역할: 클라우드 교육용 「용어 비교」 화면에 붙일 카피를 씁니다.

아래 [자료 발췌]에 적힌 내용**만** 근거로, 두 용어를 비교하세요. 발췌에 없는 내용은 넣지 마세요.

용어 A: 「{ta}」
용어 B: 「{tb}」

출력 규격(설명 문장 없이 **JSON 한 덩어리만**):
{{"left":"「{ta}」 요약(1~2문장, 자료 근거)","right":"「{tb}」 요약(1~2문장)","diff":"차이·관계(2~4문장)"}}

[자료 발췌]
{bundle}
"""

        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.LLM_API_KEY)
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(max_output_tokens=4096, temperature=0.42),
            )
            raw = (getattr(response, "text", None) or "").strip()
            if not raw and getattr(response, "candidates", None):
                try:
                    cand = response.candidates[0]
                    parts = getattr(getattr(cand, "content", None), "parts", None) or []
                    raw = "".join(getattr(p, "text", "") or "" for p in parts).strip()
                except (IndexError, AttributeError, TypeError):
                    raw = ""
            obj = self._parse_json_object_loose(raw)
            if not obj:
                return None
            left = str(obj.get("left") or "").strip()
            right = str(obj.get("right") or "").strip()
            diff = str(obj.get("diff") or "").strip()
            if len(left) < 6 or len(right) < 6 or len(diff) < 10:
                return None
            return CompareTermsResponse(left=left, right=right, diff=diff)
        except Exception:
            return None

    def mbti_chat_reply(self, messages: list[dict[str, str]]) -> str:
        """MBTI 진행 규칙에 맞춰 Gemini 멀티턴 대화로 한 번의 답변을 생성합니다.

        Args:
            messages: ``[{"role":"user"|"assistant","content":"..."}]`` 순서 목록입니다.
              마지막 원소는 반드시 ``user`` 이어야 합니다(호출부에서 검증).

        Returns:
            모델이 생성한 사용자에게 보여 줄 한국어 문자열(API 키 없음·오류 시 안내 문구).
        """
        system_txt = build_mbti_system_instruction()
        rows = messages or []

        def _gather_text(resp: object) -> str:
            txt = (getattr(resp, "text", None) or "").strip()
            if txt or not getattr(resp, "candidates", None):
                return txt
            try:
                cand = resp.candidates[0]
                parts = getattr(getattr(cand, "content", None), "parts", None) or []
                return "".join(getattr(p, "text", "") or "" for p in parts).strip()
            except (IndexError, AttributeError, TypeError):
                return ""

        if not settings.LLM_API_KEY:
            return (
                "이 기능은 서버에 **AI API 키**가 설정된 경우에만 동작합니다. "
                "관리 설정(`LLM_API_KEY` 등)을 확인해 주세요."
            )

        if not rows:
            return "대화 기록이 비어 있습니다. 화면에서 **대화 시작**을 눌러 주세요."
        last = rows[-1]
        if (last.get("role") or "").strip().lower() != "user":
            return "내부 오류: 마지막 메시지가 사용자 역할이 아닙니다. 페이지를 새로고침한 뒤 다시 시도해 주세요."

        history: list[dict[str, object]] = []
        for turn in rows[:-1]:
            r = str(turn.get("role") or "").strip().lower()
            raw_c = turn.get("content")
            part = raw_c.strip() if isinstance(raw_c, str) else ""
            if not part:
                continue
            if len(part) > 12000:
                part = part[:12000] + "\n …(메시지가 길어 일부만 전달했습니다)"
            gemini_role = "user" if r == "user" else "model"
            history.append({"role": gemini_role, "parts": [part]})

        tail_raw = last.get("content")
        user_last = tail_raw.strip() if isinstance(tail_raw, str) else ""
        if len(user_last) > 12000:
            user_last = user_last[:12000]

        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.LLM_API_KEY)
            model = genai.GenerativeModel(
                "gemini-2.5-flash",
                system_instruction=system_txt,
            )
            chat = model.start_chat(history=history)
            response = chat.send_message(
                user_last,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=8192,
                    temperature=0.62,
                ),
            )
            text_out = _gather_text(response)
            try:
                c0 = response.candidates[0]
                fr = getattr(c0, "finish_reason", None)
                fr_name = (getattr(fr, "name", None) or str(fr or "")).upper()
                if fr_name == "MAX_TOKENS":
                    text_out = (
                        (text_out or "")
                        + "\n\n> ⚠️ 출력 길이 제한으로 일부만 전달된 것 같습니다."
                        "\n중요한 줄을 다시 짧게 물어봐 주세요."
                    )
            except (IndexError, AttributeError, TypeError):
                pass

            if text_out:
                return text_out
            return "(응답 본문을 가져오지 못했습니다. 잠시 후 같은 내용으로 다시 보내 주세요.)"
        except Exception as e:
            err = str(e).strip()
            if len(err) > 300:
                err = err[:300] + "…"
            return f"⚠️ MBTI 대화 생성 중 오류가 발생했습니다. 네트워크·설정을 확인해 주세요. ({err})"