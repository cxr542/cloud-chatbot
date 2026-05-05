from __future__ import annotations

import re

from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.models.schemas import SummaryBullet, SummaryResponse
from backend.services.llm import LLMService, _clean_chunk_content, _document_base_title, _strip_pdf_boilerplate

# 주제별 검색 보강(BM25가 한글 합성어와 약하게 맞을 때)
_TOPIC_ALIASES: dict[str, list[str]] = {
    "접근제어": ["접근 제어", "ACL", "RBAC", "ABAC", "인증", "인가", "권한관리", "보안"],
    "접근 제어": ["접근제어", "ACL", "RBAC", "ABAC", "인증", "인가"],
    "보안": ["보안", "암호화", "인증", "인가", "RBAC"],
    "네트워크": ["네트워크", "VPC", "Subnet", "방화벽"],
}


def _alias_list(topic: str) -> list[str]:
    t = topic.strip()
    if not t:
        return []
    compact = t.replace(" ", "")
    out: list[str] = [t, compact]
    if compact in _TOPIC_ALIASES:
        out.extend(_TOPIC_ALIASES[compact])
    if t in _TOPIC_ALIASES:
        out.extend(_TOPIC_ALIASES[t])
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        x = x.strip()
        if len(x) >= 2 and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _topic_score(raw_content: str, aliases: list[str]) -> int:
    low = raw_content.lower()
    s = 0
    for a in aliases:
        if len(a) < 2:
            continue
        al = a.lower()
        if al in low:
            s += 22 + low.count(al) * 6
    if "okestro" in low or "o ke stro" in low:
        s -= 18
    if low.count("클라우드") > 22 and "➜" not in raw_content:
        s -= 14
    if "all rights reserved" in low:
        s -= 8
    return s


def _snippet_around_topic(clean_one_line: str, aliases: list[str], max_len: int = 200) -> str:
    """정제된 한 줄 본문에서 주제·별칭 또는 용어 정의(➜) 주변만 잘라 요약 느낌을 냅니다."""
    cl = clean_one_line.strip()
    if not cl:
        return ""

    for needle in sorted(aliases, key=len, reverse=True):
        if len(needle) < 2:
            continue
        idx = cl.lower().find(needle.lower())
        if idx != -1:
            start = max(0, idx - 48)
            end = min(len(cl), idx + max_len)
            snip = cl[start:end].strip()
            if start > 0:
                snip = "…" + snip
            if end < len(cl):
                snip = snip + "…"
            return snip

    if "➜" in cl:
        tail = cl.split("➜", 1)[1].strip()
        frag = tail[:max_len]
        return frag + ("…" if len(tail) > max_len else "")

    tail = re.sub(r"^\d{1,3}\s+", "", cl)
    frag = tail[:max_len]
    return frag + ("…" if len(tail) > max_len else "")


def _snippet_general(clean_one_line: str, max_len: int = 190) -> str:
    cl = clean_one_line.strip()
    if not cl:
        return ""
    if "➜" in cl:
        tail = cl.split("➜", 1)[1].strip()
        frag = tail[:max_len]
        return frag + ("…" if len(tail) > max_len else "")
    tail = re.sub(r"^\d{1,3}\s+", "", cl)
    frag = tail[:max_len]
    return frag + ("…" if len(tail) > max_len else "")


def _chunk_source_label(c: RetrievalChunk) -> str:
    t = (c.title or "").strip()
    m = re.search(r"\(p\.(\d+)\)", t)
    pg = m.group(1) if m else str(c.page)
    fn = t.split("(")[0].strip()
    return f"{fn} · {pg}p"


def _unique_sorted_pages(chunks: list[RetrievalChunk]) -> list[int]:
    return sorted({c.page for c in chunks})


def _reference_section_markdown(chunks: list[RetrievalChunk], *, max_pairs: int = 96) -> str:
    """Q&A 답변 하단과 같은 형식: 📚 참고 문헌 + 파일별 페이지 목록."""
    from collections import defaultdict

    seen: set[tuple[str, int]] = set()
    ref_dict: defaultdict[str, list[int]] = defaultdict(list)
    n = 0
    for c in sorted(chunks, key=lambda ch: (_document_base_title(ch.title).lower(), int(ch.page))):
        name = _document_base_title(c.title) or "문서"
        pg = int(c.page)
        key = (name, pg)
        if key in seen:
            continue
        seen.add(key)
        ref_dict[name].append(pg)
        n += 1
        if n >= max_pairs:
            break

    lines = []
    for fname in sorted(ref_dict.keys(), key=lambda s: s.lower()):
        pages_joined = ", ".join(f"{p}p" for p in sorted(set(ref_dict[fname])))
        lines.append(f"- {fname}: {pages_joined}")

    if not lines:
        return ""

    return "📚 **참고 문헌:**\n\n" + "\n".join(lines)


def _chunks_for_topic(store: FileVectorStore, topic: str) -> list[RetrievalChunk]:
    topic = topic.strip() or "클라우드"
    aliases = _alias_list(topic)
    queries = list(dict.fromkeys([topic] + aliases[:10]))
    seen: set[tuple[str, int]] = set()
    merged: list[RetrievalChunk] = []

    for q in queries:
        if len(q) < 2:
            continue
        for c in store.search(q, top_k=12):
            key = (c.title, c.page)
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)

    merged.sort(key=lambda ch: _topic_score(ch.content, aliases), reverse=True)

    compact = topic.replace(" ", "")
    if compact != "클라우드" and topic != "클라우드":
        filtered = [c for c in merged if _topic_score(c.content, aliases) > 0]
        if len(filtered) >= 2:
            merged = filtered

    return merged[:14] if merged else store.search(topic, top_k=12)


def _chunks_for_range(store: FileVectorStore, start: str | None, end: str | None, *, max_chunks: int = 120) -> list[RetrievalChunk]:
    try:
        s = int(start) if start not in (None, "") else 1
        e = int(end) if end not in (None, "") else s + 24
    except ValueError:
        s, e = 1, 30
    if s > e:
        s, e = e, s
    all_c = store.load_chunks()
    in_range = [c for c in all_c if s <= c.page <= e]
    in_range.sort(key=lambda c: (c.title, c.page))
    return in_range[:max_chunks]


def _chunks_for_all(store: FileVectorStore, *, max_chunks: int = 48) -> list[RetrievalChunk]:
    all_c = store.load_chunks()
    if not all_c:
        return []
    all_c = sorted(all_c, key=lambda c: (c.title, c.page))
    n = len(all_c)
    if n <= max_chunks:
        return all_c
    step = max(1, n // max_chunks)
    return all_c[::step][:max_chunks]


def _bullets_from_llm_markdown(md: str) -> list[SummaryBullet]:
    """Gemini 등이 출력한 `- ... (파일 · Np)` 패턴에서 카드용 불릿을 추출합니다."""
    out: list[SummaryBullet] = []
    for line in md.split("\n"):
        line = line.strip()
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        m = re.search(r"\(([^)]+?)\s*·\s*(\d+)\s*p\)\s*$", body)
        if m:
            src = m.group(1).strip()
            pg = int(m.group(2))
            txt = body[: m.start()].strip().rstrip("., ")
        else:
            src, pg, txt = "", 0, body
        if len(txt) >= 12:
            out.append(SummaryBullet(page=pg, text=txt, source=src))
    return out[:8]


def _build_fallback_bullets(
    chunks: list[RetrievalChunk],
    *,
    aliases: list[str] | None,
    span_fallback: bool = False,
) -> tuple[list[SummaryBullet], str]:
    """발췌만으로 요약형 불릿을 만듭니다(LLM 없음 또는 실패 시)."""
    bullets: list[SummaryBullet] = []
    md_parts: list[str] = []
    seen_sig: set[str] = set()

    for c in chunks[:10]:
        stripped = _strip_pdf_boilerplate(c.content)
        cc = _clean_chunk_content(stripped)
        if aliases:
            snip = _snippet_around_topic(cc, aliases)
        else:
            snip = _snippet_general(cc)
        if len(snip) < 36:
            continue
        sig = snip[:56]
        if sig in seen_sig:
            continue
        seen_sig.add(sig)

        src = _chunk_source_label(c)
        bullets.append(SummaryBullet(page=c.page, text=snip, source=src))
        md_parts.append(f"- **{src}**  \n  {snip}")

    intro = "**문서 발췌 기반 요약입니다.**\n\n"
    if span_fallback:
        intro += (
            "페이지 구간·전체 요약은 AI가 켜진 서버에서 **구간 전체 본문을 묶어 통합 서술**합니다. "
            "현재는 페이지 순으로 짧게 뽑은 결과입니다.\n\n"
        )
    intro += (
        "원문 PDF에는 목차·머리글이 섞여 있어, 주제·용어 정의(➜) 주변 위주로 짧게 모았습니다.\n\n"
        "**핵심 포인트**\n\n"
    )
    return bullets, intro + "\n\n".join(md_parts)


def build_summary(
    store: FileVectorStore,
    llm: LLMService,
    mode: str,
    topic: str | None,
    start: str | None,
    end: str | None,
) -> SummaryResponse:
    topic_q = (topic or "").strip()

    if mode == "topic":
        headline = f"주제 · {topic_q or '클라우드'}"
        chunks = _chunks_for_topic(store, topic_q)
        aliases = _alias_list(topic_q or "클라우드")
    elif mode == "range":
        try:
            ss = int(start) if start not in (None, "") else 1
            ee = int(end) if end not in (None, "") else ss + 24
        except ValueError:
            ss, ee = 1, 30
        if ss > ee:
            ss, ee = ee, ss
        headline = f"페이지 {ss}-{ee}"
        chunks = _chunks_for_range(store, start, end)
        aliases = None
    else:
        headline = "문서 전체 스캔"
        chunks = _chunks_for_all(store)
        aliases = None

    if not chunks:
        md = (
            "**요약할 내용을 찾지 못했습니다.**\n\n"
            "- 주제별 모드에서는 **검색어**를 구체적으로 적어 보세요.\n"
            "- 페이지별 모드에서는 문서에 있는 **페이지 번호 범위**인지 확인해 보세요.\n"
        )
        return SummaryResponse(text=md, pages=[], headline=headline, bullets=[])

    pages = _unique_sorted_pages(chunks)
    ref_section = _reference_section_markdown(chunks)

    synopsis: str | None = None
    if mode == "topic" and topic_q:
        synopsis = llm.summarize_topic(topic_q, chunks, difficulty="초급")
    elif mode == "range":
        synopsis = llm.summarize_span(headline, chunks, scope="range", difficulty="초급")
    elif mode == "all":
        synopsis = llm.summarize_span(headline, chunks, scope="all", difficulty="초급")

    bullets: list[SummaryBullet]
    body_md: str

    if synopsis:
        bullets = _bullets_from_llm_markdown(synopsis)
        if len(bullets) < 2:
            bullets, _fb = _build_fallback_bullets(
                chunks,
                aliases=aliases or (topic_q and _alias_list(topic_q)) or None,
                span_fallback=mode in ("range", "all"),
            )
        body_md = (
            f"**{headline}**\n\n"
            "**AI 통합 요약** (문서 발췌만 근거)\n\n"
            f"{synopsis.strip()}\n\n"
            "────────────────────\n\n"
            f"{ref_section}\n"
        )
    else:
        bullets, fb_md = _build_fallback_bullets(
            chunks,
            aliases=aliases,
            span_fallback=mode in ("range", "all"),
        )
        body_md = f"**{headline}**\n\n{fb_md}\n\n────────────────────\n\n{ref_section}\n"

    return SummaryResponse(
        text=body_md.strip(),
        pages=pages[:32],
        headline=headline,
        bullets=bullets[:10],
    )
