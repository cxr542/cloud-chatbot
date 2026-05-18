"""지식 청크(``RetrievalChunk``)에서 API용 출처 레이블(페이지 대신 시간 등)을 만듭니다."""

from __future__ import annotations

import zlib

from backend.db.vector import RetrievalChunk
from backend.models.schemas import PageRef, SourceItem
from backend.services.video_kb_ui import (
    kb_video_upload_basename,
    parse_video_time_band_seconds,
    poster_seek_seconds,
)


def stagger_duplicate_video_thumbs_page_refs(refs: list[PageRef]) -> list[PageRef]:
    """같은 응답 안에 같은 ``media_file`` 동영상이 여러 카드로 나올 때 썸네일 시각을 어긋나게 합니다.

    질문·검색 결과가 같은 영상 근거를 여러 줄로 줄 때 모두 같은 프레임만 보이는 문제를 줄입니다.

    Args:
        refs: 변환 그대로의 ``PageRef`` 리스트.

    Returns:
        ``thumb_sec`` 만 조정한 새 리스트(Q&A ``pages`` 용).
    """
    counts: dict[str, int] = {}

    out: list[PageRef] = []
    for pr in refs:
        if pr.source_type != "video" or not pr.media_file:
            out.append(pr)
            continue
        mf = pr.media_file
        n = counts.get(mf, 0)
        counts[mf] = n + 1
        base = float(pr.thumb_sec) if pr.thumb_sec is not None else 35.0
        if n > 0:
            bump_raw = zlib.crc32(f"{mf}::row{n}".encode("utf-8")) & 0xFFFFFFFF
            bump = float(bump_raw % 220) * 0.42 + 22.0
            base = min(5900.0, base + bump)
        out.append(pr.model_copy(update={"thumb_sec": round(base, 2)}))
    return out


def stagger_duplicate_video_thumbs_source_items(items: list[SourceItem]) -> list[SourceItem]:
    """출처 찾기 응답에서 동영상 카드별로 포스터가 겹치지 않게 조정합니다(``media_file`` 반복 분기).

    Args:
        items: ``chunk_to_source_item`` 결과 목록.

    Returns:
        ``thumb_sec`` 이 조정된 ``SourceItem`` 리스트.
    """
    counts: dict[str, int] = {}

    out: list[SourceItem] = []
    for it in items:
        if it.source_type != "video" or not it.media_file:
            out.append(it)
            continue
        mf = it.media_file
        n = counts.get(mf, 0)
        counts[mf] = n + 1
        base = float(it.thumb_sec) if it.thumb_sec is not None else 35.0
        if n > 0:
            bump_raw = zlib.crc32(f"{mf}::src{n}".encode("utf-8")) & 0xFFFFFFFF
            bump = float(bump_raw % 218) * 0.41 + 19.5
            base = min(5900.0, base + bump)
        out.append(it.model_copy(update={"thumb_sec": round(base, 2)}))
    return out


def chunk_to_page_ref(c: RetrievalChunk) -> PageRef:
    """Q&A 응답의 ``pages`` 항목을 청크에서 채웁니다(PDF=N페이지·동영상=구간 문구).

    Args:
        c: 검색으로 고른 ``RetrievalChunk``.

    Returns:
        프론트가 뱃지·모달 분기에 쓰는 ``PageRef``.
    """
    title = c.title or ""
    if "(동영상)" in title:
        band = ((c.ref_time_band or "").strip() or "영상 전체")
        mf = kb_video_upload_basename(title)
        s0, s1 = parse_video_time_band_seconds(band)
        thumb = poster_seek_seconds(media_filename=mf, start_sec=s0, band_label=band)
        return PageRef(
            no=1,
            title=title,
            badge=band,
            source_type="video",
            media_file=mf,
            thumb_sec=float(thumb) if mf else None,
            start_sec=float(s0) if s0 is not None else None,
            end_sec=float(s1) if s1 is not None else None,
        )
    if "(이미지)" in title:
        return PageRef(no=1, title=title, badge="이미지", source_type="image")
    return PageRef(no=int(c.page), title=title, badge="", source_type="page")


def chunk_to_source_item(c: RetrievalChunk, snippet: str) -> SourceItem:
    """출처 찾기 탭 한 줄 카드에 맞는 ``SourceItem`` 을 만듭니다.

    Args:
        c: 검색 청크.
        snippet: 카드 본문 미리보기 문자열.

    Returns:
        ``SourceItem`` (``badge``·``source_type`` 으로 PDF/미디어 표기를 구분).
    """
    title = c.title or ""
    if "(동영상)" in title:
        band = ((c.ref_time_band or "").strip() or "영상 전체")
        mf = kb_video_upload_basename(title)
        s0, s1 = parse_video_time_band_seconds(band)
        thumb = poster_seek_seconds(media_filename=mf, start_sec=s0, band_label=band)
        return SourceItem(
            p=c.page,
            t=title,
            d=snippet,
            badge=band,
            source_type="video",
            media_file=mf,
            thumb_sec=float(thumb) if mf else None,
            start_sec=float(s0) if s0 is not None else None,
            end_sec=float(s1) if s1 is not None else None,
        )
    if "(이미지)" in title:
        return SourceItem(p=c.page, t=title, d=snippet, badge="이미지", source_type="image")
    return SourceItem(p=c.page, t=title, d=snippet, badge="", source_type="page")
