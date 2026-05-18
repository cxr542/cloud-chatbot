"""
참조 사진을 활용해 LINE형 손그림 스티커 패널(표정)을 Gemini 이미지 모델로 생성합니다.

외부 패키지 ``google-genai`` 없이 ``generativelanguage.googleapis.com`` REST API
(urllib + JSON)만 써서, ``pip install -r requirements.txt`` 후 바로 동작하도록 했습니다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from typing import Any

from PIL import Image

from backend.config import settings
from backend.models.schemas import StickerPackItem, StickerPackResponse

logger = logging.getLogger(__name__)

_GENAI_LANGUAGE_API = "https://generativelanguage.googleapis.com/v1beta"

# 사용자 정의 12가지 표정(라벨은 UI·프롬프트에 그대로 씁니다).
STICKER_EXPRESSIONS: list[tuple[int, str]] = [
    (1, "감동"),
    (2, "멍때리기"),
    (3, "오열"),
    (4, "삐짐"),
    (5, "선글라스(자신감)"),
    (6, "볼꼬집"),
    (7, "어쩔?"),
    (8, "최고"),
    (9, "당황"),
    (10, "꿀잠"),
    (11, "잔망윙크"),
    (12, "경악"),
]


def _build_sticker_prompt(expression_ko: str) -> str:
    """
    한 장의 스티커를 위한 멀티모달 프롬프트 문자열을 만듭니다.

    Args:
        expression_ko: 이번 패널에 반영할 표정·동작 설명(한글).

    Returns:
        Gemini ``generate_content`` 에 넣을 텍스트 파트.
    """
    return (
        "첨부된 사진을 바탕으로 Z세대 스타일의 귀여운 손그림 LINE 스타일 스티커 **한 장(한 패널)**만 만들어 줘.\n\n"
        "- 스타일: 세련된 미니멀 드로잉, '하찮은데 힙한' 감성, 파스텔톤 포인트.\n"
        f"- 이번 스티커의 표현: {expression_ko}\n"
        "- 특징: 사진 속 인물의 얼굴·헤어·안경 등 시각적 특징을 유지한 카리커처처럼, "
        "흰색 배경, 선명한 스티커 외곽선, 과한 사실 사진 느낌·그라데이션은 피함.\n"
        "- 금지: 여러 칸 합성·콜라주·여러 표정을 한 이미지에 넣기. 오직 이 표현 하나만.\n"
        "- 출력: 투명도가 어렵다면 흰색 단색 배경의 PNG 품질로."
    )


def _pil_for_gemini(im: Image.Image) -> Image.Image:
    """
    Pillow 이미지를 Gemini 멀티모달 입력에 맞게 RGB 로 맞춥니다.

    RGBA·팔레트 모드는 흰 배경 위에 합성해 인물 실루엣이 깨지지 않게 합니다.

    Args:
        im: 사용자가 올린 원본 ``PIL.Image``.

    Returns:
        RGB 모드로 변환된 이미지(새 객체일 수 있음).
    """
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if im.mode == "P":
        return _pil_for_gemini(im.convert("RGBA"))
    if im.mode != "RGB":
        return im.convert("RGB")
    return im


def decode_and_resize_reference(raw: bytes, *, max_edge: int) -> Image.Image:
    """
    업로드 바이트를 열고, 긴 변이 ``max_edge`` 를 넘지 않도록 비율 유지 축소합니다.

    Args:
        raw: JPEG/PNG 등 바이너리.
        max_edge: 가로·세로 중 긴 변의 상한(픽셀).

    Returns:
        RGB 로 정규화된 ``PIL.Image``.

    Raises:
        ValueError: 이미지를 열 수 없거나 빈 파일인 경우.
    """
    if not raw:
        raise ValueError("빈 이미지 데이터입니다.")
    try:
        im = Image.open(BytesIO(raw))
        im.load()
    except OSError as e:
        raise ValueError("지원되는 이미지(JPEG·PNG 등)로 열 수 없습니다.") from e
    im = _pil_for_gemini(im)
    w, h = im.size
    m = max(w, h)
    if m > max_edge:
        scale = max_edge / float(m)
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    return im


def _reference_jpeg_inline_b64(reference_rgb: Image.Image) -> tuple[str, str]:
    """
    참조 ``PIL`` 이미지를 JPEG 로 인코딩해 REST ``inline_data`` 용(base64 문자열)을 만듭니다.

    Args:
        reference_rgb: RGB 참조 얼사진.

    Returns:
        ``(mime_type, base64 문자열)`` — ``mime_type`` 은 보통 ``image/jpeg`` 입니다.
    """
    buf = BytesIO()
    reference_rgb.save(buf, format="JPEG", quality=88)
    data = buf.getvalue()
    return "image/jpeg", base64.standard_b64encode(data).decode("ascii")


def _first_inline_image_from_rest_json(data: dict[str, Any]) -> tuple[bytes, str] | None:
    """
    ``generateContent`` REST JSON 의 **모든** ``candidates[*].content.parts`` 에서 이미지 블록을 찾습니다.

    일부 응답은 첫 후보에는 텍스트만 있고 다른 후보에 이미지를 두기도 해 전부 훑습니다.

    Args:
        data: 응답 본문 dict.

    Returns:
        첫 번째 유효 ``(바이너리, mime_type)`` 또는 없음.
    """
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        raise RuntimeError(err.get("message") or json.dumps(err, ensure_ascii=False))
    cands = data.get("candidates") or []
    for cand in cands:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            if not isinstance(part, dict):
                continue
            raw = part.get("inlineData") or part.get("inline_data")
            if not isinstance(raw, dict):
                continue
            b64 = raw.get("data")
            if not b64:
                continue
            mt = (raw.get("mimeType") or raw.get("mime_type") or "image/png").strip()
            try:
                blob = base64.standard_b64decode(b64)
            except (ValueError, TypeError):
                continue
            if blob:
                return blob, mt
    return None


def _text_snippet_from_rest_json(data: dict[str, Any], *, max_len: int = 200) -> str:
    """
    응답에 텍스트 파트만 있을 때 디버깅용으로 한 줄 미리보기 문자열을 만듭니다.

    Args:
        data: Gemini JSON 본문.
        max_len: 잘라낼 길이 상한입니다.

    Returns:
        줄바꿈·공백을 정리한 짧은 문자열(비어 있을 수 있음).
    """
    chunks: list[str] = []
    for cand in data.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        parts = ((cand.get("content") or {}).get("parts")) or []
        for p in parts:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                chunks.append(p["text"])
    merged = " ".join(chunks).replace("\r", " ").replace("\n", " ")
    merged = " ".join(merged.split())
    if len(merged) > max_len:
        return merged[: max_len - 1] + "…"
    return merged
def _rest_generate_content_image(
    reference_rgb: Image.Image,
    prompt: str,
    *,
    model: str,
    api_key: str,
    timeout_sec: float,
) -> tuple[bytes, str]:
    """
    Gemini ``v1beta`` ``generateContent`` 로 텍스트+참조 이미지 한 장을 보내고, 출력 이미지 바이너리를 받습니다.

    Args:
        reference_rgb: 참조 얼사진(RGB).
        prompt: 사용자 텍스트(스티커 스타일·표정 안내 등).
        model: 예: ``gemini-2.5-flash-image``.
        api_key: Google AI Studio API 키.
        timeout_sec: HTTP 전체 타임아웃(초).

    Returns:
        ``(image_bytes, mime_type)``

    Raises:
        RuntimeError: HTTP 오류·JSON 오류 필드·응답에 이미지가 없음.
    """
    ref = reference_rgb.copy()
    mime_in, data_b64 = _reference_jpeg_inline_b64(ref)
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_in, "data": data_b64}},
                ],
            }
        ],
        # 네이티브 이미지 모델이 텍스트만 반환하는 경우를 줄입니다(미지원 키는 API 가 무시할 수 있음).
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    qstr = urllib.parse.urlencode({"key": api_key.strip()})
    path_model = urllib.parse.quote(model.strip(), safe="")
    url = f"{_GENAI_LANGUAGE_API}/models/{path_model}:generateContent?{qstr}"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            inner = err_body.get("error") if isinstance(err_body, dict) else None
            msg = inner.get("message") if isinstance(inner, dict) else str(err_body)
        except Exception:
            msg = getattr(e, "reason", "") or str(e)
        raise RuntimeError(msg or f"HTTP {e.code}") from e
    except OSError as e:
        raise RuntimeError(f"네트워크 오류: {e}") from e
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError("Gemini 응답을 JSON 으로 읽지 못했습니다.") from e
    got = _first_inline_image_from_rest_json(data)
    if not got:
        extra = ""
        pf = data.get("promptFeedback") if isinstance(data, dict) else None
        if isinstance(pf, dict):
            br = pf.get("blockReason")
            if br:
                extra = f" (차단 사유: {br})"
        cands = data.get("candidates") if isinstance(data, dict) else None
        if isinstance(cands, list) and cands:
            fr = (cands[0] or {}).get("finishReason") if isinstance(cands[0], dict) else None
            if fr:
                extra += f" (finishReason: {fr})"
        snip = _text_snippet_from_rest_json(data) if isinstance(data, dict) else ""
        if snip:
            extra += f' 텍스트일부:{snip[:180]!s}'
        raise RuntimeError("모델 응답에 이미지(inline_data)가 없거나 후보가 비어 있습니다." + extra)
    return got


def _generate_one_panel(reference_rgb: Image.Image, idx: int, label_ko: str) -> StickerPackItem:
    """
    참조 얼사진과 표정 라벨로 스티커 패널 한 장을 Gemini에 요청합니다(동기).

    모델이 가끔 텍스트만 반환(stop) 하는 경우가 있어 ``GEMINI_STICKER_PANEL_ATTEMPTS`` 만큼
    짧은 간격으로 재호출합니다.

    Args:
        reference_rgb: ``decode_and_resize_reference`` 로 만든 RGB 이미지(스레드마다 ``copy()`` 권장).
        idx: 1~12 패널 번호.
        label_ko: 표정 한글 라벨.

    Returns:
        base64 가 채워진 ``StickerPackItem``.

    Raises:
        RuntimeError: API 키 누락·HTTP 오류·이미지 미반환 등.
    """
    if not (settings.LLM_API_KEY or "").strip():
        raise RuntimeError("LLM_API_KEY(또는 GOOGLE_API_KEY 등)가 비어 있습니다.")

    att = settings.GEMINI_STICKER_PANEL_ATTEMPTS
    last_err: RuntimeError | None = None

    retry_tail = ""

    for attempt in range(max(1, att)):
        try:
            prompt = _build_sticker_prompt(label_ko) + retry_tail
            blob, mime = _rest_generate_content_image(
                reference_rgb,
                prompt,
                model=settings.GEMINI_STICKER_MODEL,
                api_key=settings.LLM_API_KEY,
                timeout_sec=float(settings.GEMINI_STICKER_PER_IMAGE_TIMEOUT_SEC),
            )
            b64out = base64.standard_b64encode(blob).decode("ascii")
            return StickerPackItem(idx=idx, label=label_ko, mime_type=mime, image_base64=b64out)
        except RuntimeError as e:
            last_err = e
            retry_tail = (
                "\n\n[재시도 지시] 이전 출력에 스티커 그림 파일이 빠졌습니다. 참조 얼사진과 같은 인물 카리커처로, "
                "이 표정 패널 **한 장**의 래스터 이미지만 생성해 반환 하세요. 답변은 이미지 인라인 파트 필수입니다."
            )
            if attempt + 1 >= max(1, att):
                break
            time.sleep(0.45 + random.random() * 0.95)
    raise last_err or RuntimeError("스티커 패널 생성에 실패했습니다.")


async def generate_sticker_pack_async(reference: Image.Image) -> StickerPackResponse:
    """
    12개 표정을 병렬(세마포어로 개수 제한)로 생성해 ``StickerPackResponse`` 를 만듭니다.

    한 패널이 실패해도 나머지는 채우고, ``warnings`` 에 이유를 남깁니다.

    Args:
        reference: 정규화·리사이즈된 RGB 참조 이미지.

    Returns:
        생성에 성공한 항목만 담은 응답(정렬: idx 오름차순).
    """
    sem = asyncio.Semaphore(settings.GEMINI_STICKER_CONCURRENCY)
    timeout = float(settings.GEMINI_STICKER_PER_IMAGE_TIMEOUT_SEC)
    items_out: list[StickerPackItem] = []
    warnings: list[str] = []

    async def one(idx: int, label: str) -> None:
        async with sem:
            try:
                item = await asyncio.wait_for(
                    asyncio.to_thread(_generate_one_panel, reference.copy(), idx, label),
                    timeout=timeout,
                )
                items_out.append(item)
            except asyncio.TimeoutError:
                warnings.append(f"{idx}. {label}: 시간 초과({int(timeout)}초)")
            except Exception as e:
                logger.warning("sticker panel %s %s failed: %s", idx, label, e)
                warnings.append(f"{idx}. {label}: {type(e).__name__}: {e}")

    await asyncio.gather(*(one(i, lab) for i, lab in STICKER_EXPRESSIONS))
    items_out.sort(key=lambda x: x.idx)
    return StickerPackResponse(items=items_out, warnings=warnings)
