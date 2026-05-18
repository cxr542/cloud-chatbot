from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.config import settings
from backend.models.schemas import StickerPackResponse
from backend.services.sticker_pack import decode_and_resize_reference, generate_sticker_pack_async

router = APIRouter()


@router.post("/api/sticker-pack", response_model=StickerPackResponse)
async def sticker_pack(file: UploadFile = File(..., description="참조용 인물 사진(JPEG·PNG 등)")) -> StickerPackResponse:
    """
    한 장의 얼사진을 받아 12종 LINE 스타일 스티커 패널(base64) 목록을 생성합니다.

    Gemini 이미지 할당량·네트워크에 따라 전부 생성에 수 분이 걸릴 수 있습니다.
    """
    if not (settings.LLM_API_KEY or "").strip():
        raise HTTPException(
            status_code=503,
            detail="LLM(Gemini) API 키가 설정되어 있지 않아 이미지 생성을 할 수 없습니다. .env의 LLM_API_KEY를 확인해 주세요.",
        )
    ctype = (file.content_type or "").lower()
    if ctype and not ctype.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="이미지 파일만 업로드할 수 있습니다.",
        )
    raw = await file.read()
    cap = settings.max_sticker_upload_bytes()
    if len(raw) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"이미지 용량은 {settings.MAX_STICKER_UPLOAD_MB}MB 이하여야 합니다.",
        )
    try:
        ref = decode_and_resize_reference(raw, max_edge=settings.GEMINI_STICKER_REF_MAX_EDGE)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return await generate_sticker_pack_async(ref)
