from __future__ import annotations

import re
from typing import Literal

from backend.config import settings
from backend.db.database import get_settings
from backend.db.vector import RetrievalChunk

# 교육생 화면(index.html)의 메인 탭·설정 메뉴 안내와 맞춰 두었습니다.
GREETING_REPLY = """안녕하세요! ☁️ 클라우드 학습 도우미입니다.

업로드된 학습 문서를 바탕으로 답해 드려요.

**메인 탭**

| 메뉴 | 설명 |
| :--- | :--- |
| 💬 Q&A | 학습 문서를 근거로 질문에 답합니다 |
| 🔍 출처 찾기 | 키워드로 관련 페이지를 찾습니다 |
| 📝 요약 | 주제·페이지 범위·전체 요약을 봅니다 |
| 🧠 퀴즈 | 4지선다·OX·혼합으로 복습합니다 |
| ⚖️ 용어 비교 | 두 용어의 차이를 비교합니다 |

**⚙️ 설정 메뉴**(홈 오른쪽)

| 메뉴 | 설명 |
| :--- | :--- |
| 📌 오답노트 | 틀린 문제를 모아 다시 봅니다 |
| 📊 진도 체크 | 용어 학습 완료 현황을 봅니다 |
| ⚙️ 난이도 설정 | 초급·중급·고급 설명 깊이를 바꿉니다 |

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


def _document_base_title(chunk_title: str) -> str:
    """`파일.pdf (p.N)` 형태에서 파일명만 뽑습니다."""
    return re.sub(r"\s*\(p\.\d+\)\s*", "", chunk_title).strip()


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


def _snippet_around_keyword(query: str, clean_content: str) -> str:
    """질문 토큰이 들어간 구간 위주로 짧은 스니펫을 만듭니다."""
    q_clean = re.sub(r"[^\w\s]", "", query).lower()
    tokens = q_clean.split()
    keyword = tokens[0] if tokens else query.lower()
    idx = clean_content.lower().find(keyword.lower())

    if idx != -1:
        start = max(0, idx - 40)
        end = min(len(clean_content), idx + 120)
        snippet = clean_content[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(clean_content):
            snippet = snippet + "..."
        return snippet
    return clean_content[:150] + "..."


def _dedupe_first_chunk_per_doc(contexts: list[RetrievalChunk], max_docs: int = 5) -> list[RetrievalChunk]:
    """BM25 순서를 유지하면서 문서(파일)별 대표 청크 하나씩만 고릅니다."""
    seen: set[str] = set()
    out: list[RetrievalChunk] = []
    for c in contexts:
        base = _document_base_title(c.title)
        if base in seen:
            continue
        seen.add(base)
        out.append(c)
        if len(out) >= max_docs:
            break
    return out


class LLMService:
    def answer(self, query: str, contexts: list[RetrievalChunk], difficulty: str) -> str:
        prompt = get_settings("system_prompt") or "친절한 클라우드 학습 도우미입니다."

        if is_greeting_query(query):
            return GREETING_REPLY

        if not contexts:
            return f"죄송합니다. '{query}'와(과) 관련된 정보를 문서에서 찾을 수 없었습니다. 다른 키워드로 다시 질문해 주시겠어요?"

        # --- 진짜 AI (Gemini) 연동 ---
        if settings.LLM_API_KEY:
            try:
                import google.generativeai as genai

                genai.configure(api_key=settings.LLM_API_KEY)
                model = genai.GenerativeModel("gemini-2.5-flash")

                # 여러 문서의 내용을 하나로 합침
                context_text = "\n\n".join([f"[문서: {c.title}]\n{c.content}" for c in contexts])

                ai_prompt = f"""[시스템 지침]
{prompt}
- 반드시 제공된 [참고 문서] 내용만을 바탕으로 답변하세요.
- 답변 내에서 특정 페이지를 언급할 때는 반드시 해당 내용이 포함된 [문서: ...] 제목의 페이지 번호(p.XX)를 정확하게 인용하세요.
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

위의 시스템 지침을 반드시 준수하여 답변하세요. 답변은 '{difficulty}' 수준에 맞춰야 합니다.
"""
                response = model.generate_content(ai_prompt)

                # 실제 검색된 모든 페이지 번호와 파일명을 매핑 (중복 제거 및 그룹화)
                from collections import defaultdict

                ref_dict: defaultdict[str, list[str]] = defaultdict(list)
                for c in contexts:
                    name = re.sub(r"\s*\(p\.\d+\)\s*", "", c.title).strip()
                    ref_dict[name].append(f"{c.page}p")

                ref_parts = []
                for name in sorted(ref_dict.keys()):
                    pages_joined = ", ".join(sorted(set(ref_dict[name])))
                    ref_parts.append(f"- {name}: {pages_joined}")

                additional_info = f"\n\n📚 **참고 문헌:**\n" + "\n".join(ref_parts)
                return response.text + additional_info
            except Exception as e:
                return f"⚠️ AI 생성 중 오류가 발생했습니다. (API 키를 확인해주세요): {str(e)}"
        # -----------------------------

        # 여러 컨텍스트를 조합하여 답변 생성 (Mock). 문서별로 최소 1개 청크는 본문에 반영합니다.
        per_doc = _dedupe_first_chunk_per_doc(contexts, max_docs=5)
        doc_labels = ", ".join(_document_base_title(c.title) for c in per_doc)

        from collections import defaultdict

        ref_dict = defaultdict(list)
        for c in contexts:
            name = _document_base_title(c.title)
            ref_dict[name].append(f"{c.page}p")

        ref_parts = []
        for name in sorted(ref_dict.keys()):
            pages = ", ".join(sorted(set(ref_dict[name])))
            ref_parts.append(f"- {name}: {pages}")

        additional_info = f"\n\n📚 **참고 문헌:**\n" + "\n".join(ref_parts) if contexts else ""

        if len(per_doc) == 1:
            main_context = per_doc[0]
            clean_title = _document_base_title(main_context.title)
            clean_content = _clean_chunk_content(main_context.content)
            snippet = _snippet_around_keyword(query, clean_content)
        else:
            blocks: list[str] = []
            for ch in per_doc:
                dname = _document_base_title(ch.title)
                cc = _clean_chunk_content(ch.content)
                sn = _snippet_around_keyword(query, cc)
                blocks.append(f"• **{dname}** (p.{ch.page} 근처 발췌)\n  {sn}")
            multi_snippet = "\n\n".join(blocks)
            clean_title = doc_labels
            snippet = multi_snippet

        response = ""
        # Mock 모드에서는 프롬프트를 직접 노출하지 않고 답변 스타일로만 사용합니다.
        if difficulty == "고급":
            if len(per_doc) > 1:
                response = (
                    f"🎓 **맞춤형 고급 답변**\n\n"
                    f"BM25 검색 상위 근거에 **{len(per_doc)}개 문서**가 포함되어 있습니다 ({doc_labels}). "
                    f"문서별로 가져온 발췌는 아래와 같습니다.\n\n{snippet}\n\n"
                    f"각 문서의 세부 맥락은 참고 문헌의 페이지를 확인해 보세요.{additional_info}"
                )
            else:
                response = (
                    f"🎓 **맞춤형 고급 답변**\n\n전문적인 관점에서 설명해 드립니다. {clean_title} 문서에 따르면,\n\n"
                    f"{snippet}\n\n시스템 설계 시 이 부분을 깊게 고려해 보세요.{additional_info}"
                )
        elif difficulty == "중급":
            if len(per_doc) > 1:
                response = (
                    f"📝 **핵심 요약**\n\n"
                    f"다음 **{len(per_doc)}개 문서**에서 관련 내용을 찾았습니다 ({doc_labels}).\n\n{snippet}\n\n"
                    f"문서마다 초점이 다를 수 있으니 참고 문헌을 함께 보시면 좋습니다.{additional_info}"
                )
            else:
                response = (
                    f"📝 **핵심 요약**\n\n{clean_title}에서 설명하는 주요 포인트는 다음과 같습니다.\n\n"
                    f"{snippet}\n\n개념 구조를 파악하는 데 도움이 되실 거예요.{additional_info}"
                )
        else:
            if len(per_doc) > 1:
                response = (
                    f"💡 **신입사원을 위한 쉬운 설명**\n\n"
                    f"질문과 관련해 **{len(per_doc)}개 PDF**에서 글을 찾았어요 ({doc_labels}).\n\n{snippet}\n\n"
                    f"더 깊게 보시려면 아래 참고 문헌 페이지를 열어 보세요.{additional_info}"
                )
            else:
                response = (
                    f"💡 **신입사원을 위한 쉬운 설명**\n\n{clean_title} 문서에서 찾은 관련 내용이에요.\n\n"
                    f"'{snippet}'\n\n이해하기 어렵다면 다시 물어봐 주세요!{additional_info}"
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

                response = model.generate_content(prompt)
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

대상: {span_label}

{scope_hint}

아래 [발췌]에 적힌 내용**만** 사용하세요. 외부 지식·추측 금지.
목차·저작권·위키 이동 경로·반복 네비 문구는 출력에서 생략하세요.

출력 형식(마크다운, 표·코드블록 금지):
1. **한 줄 요약** — 한 문장으로 이 구간(또는 샘플)의 핵심을 말합니다.
2. **통합 요약** — 2~5개의 짧은 문단으로, 개념이 어떻게 이어지는지 **하나의 흐름**으로 서술합니다. 「p.◯에서는 …」처럼 페이지만 도는 나열은 피합니다.
3. **핵심 포인트** — `- ` 로 시작하는 불릿 5~9개. 용어·주의점 위주. 필요할 때만 문장 끝에 `(파일명 일부 · Np)` 출처를 붙입니다.

문체는 교육용으로 명료하게. 독자 난이도: {difficulty}

[발췌]
{bundle}

위 형식을 지켜 결과만 출력하세요."""

                response = model.generate_content(prompt)
                out = (response.text or "").strip()
                return out or None
            except Exception:
                return None

        return None