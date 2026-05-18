"""이미지·동영상 파일에서 지식 베이스(RAG)용 텍스트를 만드는 보조 함수.

검색·임베딩 파이프라인은 문자열 청크를 전제로 하므로,
미디어는 **설명 또는 화면 내 텍스트를 한글로 요약한 문자열**로 바꾼 뒤 ``vector.json`` 에 넣습니다.

동영상 처리는 업로드 루트에서 스레드 풀로 돌립니다(sync ``time.sleep`` 이 이벤트 루프를 붙들지 않도록).
"""

from __future__ import annotations

import io
import logging
import re
import time
from pathlib import Path

from google.api_core.exceptions import DeadlineExceeded, ServiceUnavailable

from backend.config import settings

logger = logging.getLogger(__name__)

_VIDEO_MIME_BY_SUFFIX: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".m4v": "video/x-m4v",
}


def video_mime_type_for_suffix(suffix_lower: str) -> str | None:
    """
    업로드 확장자(소문자, 점 포함)에 대응하는 동영상 MIME 을 반환합니다.

    ``mimetypes`` 가 모르는 확장자(.m4v 등) 때문에 클라우드 LLM 업로드가 실패하는 경우를 막습니다.
    """
    return _VIDEO_MIME_BY_SUFFIX.get(suffix_lower)


_IMAGE_PROMPT = """이 자료는 클라우드·IT 교육용 챗봇의 지식 베이스에 들어갑니다.

다음을 한국어로만 작성하세요.
1) 화면에 보이는 **텍스트**가 있으면 가능한 한 빠짐없이 옮겨 적으세요.
2) 다이어그램·스크린샷·표라면 무엇을 설명하는지 **2~4문장**으로 요약하세요.
3) 추측·일반 상식 보충은 하지 마시고, 이미지에 근거한 내용만 쓰세요.
4) 전체 800자 내외를 넘지 않게 간결하게 정리하세요."""


_VIDEO_PROMPT = """이 영상은 클라우드·IT 교육용 챗봇 **지식 베이스**에 들어갑니다.
학습자가 **뒤에서 무엇이든 질문**하므로, 짧은 줄거리가 아니라 **질문에 바로 답할 수 있는 정보 밀도**로 쓰세요.

한국어만 쓰고, 추측·상식 보충·영상에 없는 내용은 절대 넣지 마세요.

아래 **형식**(대괄호 라벨은 그대로)을 지키되, 내용이 조금이라도 있으면 블록은 **최대한 채우세요**. 억지로 채우라는 뜻이 아니라 **화면·음성·자막 근거가 있으면 빼먹지 말 것**입니다.

[주제] 이 영상이 다루는 주제 한 문장.

[사실·수치·이름]
- 제품·서비스·클라우드 용어 정의, SLA, 가격·한도·단위, URL, 콘솔·포털 메뉴 경로, 명령 예, IP·포트, 리전명 등 **보이거나 들리는 대로** bullet.

[화면·UI 텍스트]
- 자막, 슬라이드, 라벨, 버튼, 토스트, 에러 문구, 표·리스트에 보이는 문자를 **가능한 문자 그대로** 한 줄에 하나씩.

[순서·절차]
- 시연·가이드가 있으면 1) 2) 3) 번호 목록. 각 단계 옆에 중요한 메뉴명·클릭 대상을 적으세요.

[함정·주의·반례]
- 강사가 '주의', '틀리기 쉽다', '절대 안 됨'이라고 말한 내용이 있으면 bullet.

[질문 예상 Q&A]
- 이 영상 내용만으로 답할 수 있는 **짧은 질문 6~12개**를 적고, 각 줄에 ``Q: … / A: …`` 형식으로 한 줄씩만 쓰세요(한글).

[한 줄 요약]
- 위 블록들과 모순되지 않게 1~2문장.

**분량:** 본문만 **3500~5000자**가 되도록 쓰세요(REF_TIME 블록 제외). 잘릴 위험이 있으면 [질문 예상 Q&A]를 줄이지 말고 [한 줄 요약]을 줄이세요.

화려한 표현보다 **원문·자막·화면 글자에 가까운 인용**을 우선하세요.

연결 출처 UI를 위해 **본문 맨 끝**에 아래 블록만 **정확히** 붙이세요.

<<<REF_TIME>>>
구간표시=영상 전체
<<<END_REF_TIME>>>

타임코드를 **자막·슬라이드 번호·화면 속 시계** 근거로 추정할 수 있으면 ``구간표시=약 01:05–06:40 (추정)``처럼 MM:SS 또는 HH:MM:SS 형으로 적습니다. 전혀 알 수 없을 때만 ``영상 전체``."""


def _gemini_video_request_options(*, rpc_deadline_sec: float) -> dict[str, object]:
    """
    ``generate_content`` 에 넘길 GAPIC ``request_options`` 입니다.

    기본 재시도(약 600초 예산)·지수 백오프가 unary 안에 깔려 있으면 ``timeout`` 을 크게 줘도
    ``RetryError: Timeout of 600.0s exceeded`` 로 끊기는 경우가 있습니다. 재시도는 끄고
    한 번 호출당 ``rpc_deadline_sec`` 만 허용한 뒤, 상위 루프(``GEMINI_VIDEO_UNARY_ATTEMPTS``)가
    503 등을 간격 두고 재시도합니다.

    Args:
        rpc_deadline_sec: 이 RPC 하나에 허용하는 최대 대기 시간(초).

    Returns:
        ``request_options`` 에 그대로 넣을 작은 dict.
    """
    return {"timeout": float(rpc_deadline_sec), "retry": False}


_KB_REF_TIME_BLOCK_RE = re.compile(
    r"\s*<<<REF_TIME>>>\s*구간표시\s*[=＝]\s*(.+?)\s*<<<END_REF_TIME>>>\s*",
    re.DOTALL | re.IGNORECASE,
)


def _gather_gemini_generate_content_text(response: object, *, filename: str) -> tuple[str, str]:
    """
    ``generate_content`` 응답에서 본문 문자열을 안전하게 모읍니다.

    차단·후보 없음 등으로 ``response.text`` 가 예외를 던지는 경우가 있어
    ``getattr(..., None)`` 로는 부족하고, ``parts`` 조합으로 한 번 더 시도합니다.

    Args:
        response: Google Generative AI SDK 가 돌려준 응답 객체.
        filename: 로그에 붙일 파일명(추적용).

    Returns:
        (본문 텍스트, 비었을 때 진단용 한 줄 설명). 본문이 있으면 진단은 빈 문자열입니다.
    """
    note_bits: list[str] = []
    pb = getattr(response, "prompt_feedback", None)
    if pb is not None:
        note_bits.append(f"prompt_feedback={pb!r}")
    cands = getattr(response, "candidates", None)
    if not cands:
        note_bits.append("candidates=비어 있음")
    else:
        for i, cand in enumerate(cands[:3]):
            fr = getattr(cand, "finish_reason", None)
            fr_name = getattr(fr, "name", None) or str(fr)
            note_bits.append(f"cand[{i}].finish_reason={fr_name}")

    text_out = ""
    try:
        text_out = (response.text or "").strip()
    except (ValueError, AttributeError) as e:
        note_bits.append(f"response.text 오류({type(e).__name__})={e!r}")

    if not text_out and cands:
        try:
            cand0 = cands[0]
            prts = getattr(getattr(cand0, "content", None), "parts", None) or []
            text_out = "".join(getattr(p, "text", "") or "" for p in prts).strip()
        except (IndexError, AttributeError, TypeError):
            diag = "; ".join(note_bits) if note_bits else "후보 해석 불가"
            return "", diag

    if text_out:
        return text_out, ""
    note = "; ".join(note_bits) if note_bits else "원인 불명 빈 문자열"
    logger.warning("동영상 요약 Gemini 응답에 본문이 없음(%s): %s", filename, note)
    return "", note


def strip_kb_ref_time_footer(text: str) -> tuple[str, str | None]:
    """
    동영상 요약 끝의 ``REF_TIME`` 블록을 본문에서 떼고, UI·저장소용 구간 문자열만 얻습니다.

    Args:
        text: 클라우드 LLM 등이 생성한 문자열입니다.

    Returns:
        튜플 (청크본문 텍스트, 구간표시 한 줄 또는 None).
    """
    if not text or not text.strip():
        return "", None
    m = _KB_REF_TIME_BLOCK_RE.search(text)
    if not m:
        return text.strip(), None
    band_one = re.sub(r"\s+", " ", (m.group(1) or "").strip()).strip()
    cleaned = _KB_REF_TIME_BLOCK_RE.sub("\n\n", text).strip()
    return cleaned, band_one or None


def _gemini_extract_with_image_part(model_name: str, image_part: object) -> str | None:
    """LLM 멀티모달 API로 이미지 파트 하나와 프롬프트를 넘겨 문자열 설명만 받습니다.

    Args:
        model_name: ``GenerativeModel`` 이름(프로젝트 LLM 코드와 통일).
        image_part: ``PIL.Image.Image`` 또는 inline data 등 SDK가 허용하는 파트.

    Returns:
        모델이 돌려준 텍스트(실패 시 None).
    """
    import google.generativeai as genai

    genai.configure(api_key=settings.LLM_API_KEY)
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(
        [_IMAGE_PROMPT, image_part],
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=2048,
            temperature=0.35,
        ),
    )
    text = (getattr(response, "text", None) or "").strip()
    if text:
        return text
    try:
        cand = response.candidates[0]
        parts = getattr(getattr(cand, "content", None), "parts", None) or []
        return "".join(getattr(p, "text", "") or "" for p in parts).strip() or None
    except (IndexError, AttributeError, TypeError):
        return None


def extract_text_from_image_bytes_for_kb(content: bytes, filename: str) -> str:
    """이미지 바이트에서 지식 베이스용 설명 텍스트를 만듭니다.

    ``LLM_API_KEY``(또는 설정에 통합된 키)가 있으면 LLM 멀티모달로 시각·텍스트 내용을 서술하고,
    없거나 오류 시에는 파일명 기반 안내 문구만 반환합니다(임베딩은 BM25·파일명 일부에 의존).

    Args:
        content: 업로드된 이미지 원시 바이트.
        filename: 원본 파일명(로그·폴백 문구용).

    Returns:
        ``vector.json`` 청크 ``content`` 로 쓸 문자열.
    """
    if not settings.LLM_API_KEY:
        return (
            f"[이미지 자료] {filename} — LLM API 키가 설정되지 않아 화면 내용을 자동으로 글로 바꾸지 못했습니다. "
            "같은 내용을 PDF 또는 TXT로도 올려 주시거나 .env 에 LLM_API_KEY 를 설정해 주세요."
        )

    try:
        from PIL import Image
    except ImportError:
        logger.exception("Pillow 가 없어 이미지를 열 수 없습니다.")
        return (
            f"[이미지 자료] {filename} — 이미지 처리 라이브러리(Pillow)가 없습니다. requirements.txt 설치 후 다시 시도하세요."
        )

    try:
        img = Image.open(io.BytesIO(content))
        if getattr(img, "mode", "") in ("RGBA", "P"):
            img = img.convert("RGB")
    except Exception as e:
        logger.warning("이미지 열기 실패 (%s): %s", filename, e)
        return f"[이미지 자료] {filename} — 파일을 이미지로 읽지 못했습니다. 형식(.png/.jpg 등)을 확인해 주세요."

    try:
        out = _gemini_extract_with_image_part("gemini-2.5-flash", img)
        if out:
            body = out[:2800]
            return body
    except Exception as e:
        logger.warning("이미지 LLM 설명 실패 (%s): %s", filename, e)

    return (
        f"[이미지 자료] {filename} — 모델 호출에 실패했습니다. API 키·할당량을 확인하거나 나중에 다시 업로드해 주세요."
    )


def extract_text_from_video_path_for_kb(
    file_path: Path,
    filename: str,
    *,
    mime_type: str | None = None,
) -> str:
    """로컬 동영상 파일 경로에서 지식 베이스용 설명 텍스트를 만듭니다.

    클라우드 LLM 제공자의 **파일 API**로 업로드한 뒤 요약합니다. 대기 시간이 길면 ``VIDEO_GEMINI_WAIT_SEC`` 를 늘립니다.
    unary 요약 중 ``504 Deadline Exceeded``(Google 쪽 포함)면 ``GEMINI_VIDEO_UNARY_ATTEMPTS`` 간격으로 재시도하고,
    이후 한 번 스트리밍으로 받아 이어붙이는 폴백까지 시도합니다.
    호출 스레드에서 ``time.sleep`` 을 사용하므로 비동기 핸들러에서는 스레드 풀 위에서만 호출하세요.

    Args:
        file_path: ``uploads`` 등에 저장된 실제 파일 경로.
        filename: 사용자에게 보일 파일명.
        mime_type: 파일 업로드용 MIME (없으면 SDK 추론 실패 가능성이 있습니다).

    Returns:
        청크 ``content`` 로 사용할 문자열.
    """
    if not settings.LLM_API_KEY:
        return (
            f"[동영상 자료] {filename} — LLM API 키가 없어 음성·화면 내용을 추출하지 못했습니다. "
            ".env 에 키를 넣거나 대본 TXT를 추가 업로드해 주세요."
        )

    wait_cap = settings.VIDEO_GEMINI_WAIT_SEC
    vf_name: str | None = None

    try:
        import google.generativeai as genai
        from google.generativeai import protos
        from google.generativeai.types import HarmBlockThreshold, HarmCategory

        genai.configure(api_key=settings.LLM_API_KEY)
        up_kw: dict[str, object] = {"path": str(file_path)}
        if mime_type:
            up_kw["mime_type"] = mime_type
        vf = genai.upload_file(**up_kw)
        vf_name = vf.name

        waited = 0
        poll_interval = 2
        while waited < wait_cap:
            vf = genai.get_file(vf_name)

            if vf.state == protos.File.State.ACTIVE:
                break
            if vf.state == protos.File.State.FAILED:
                return (
                    f"[동영상 자료] {filename} — 외부 LLM 서비스 쪽 파일 처리가 실패(FAILED)했습니다. "
                    "코덱·해상도·길이를 줄이거나 mp4 로 변환 후 다시 올려 주세요."
                )
            if vf.state == protos.File.State.PROCESSING:
                time.sleep(poll_interval)
                waited += poll_interval
                continue

            # STATE_UNSPECIFIED 등: 잠깐 두고 상태가 갱신되기를 기다립니다.
            time.sleep(1)
            waited += 1
        else:
            return (
                f"[동영상 자료] {filename} — 처리 대기({wait_cap}s) 안에 활성화되지 않았습니다. "
                "VIDEO_GEMINI_WAIT_SEC 값을 키우거나 더 짧은 클립을 사용해 보세요."
            )

        # 교육 자료에서 가끔 보수적으로 막혀 빈 응답이 나오는 경우를 줄이기 위해
        # 위험도 임계값만 한 단계 완화합니다(여전히 HIGH 는 차단).
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        }

        model = genai.GenerativeModel(settings.GEMINI_VIDEO_MODEL)
        rpc_timeout = float(settings.GEMINI_VIDEO_RPC_TIMEOUT_SEC)
        unary_attempts = int(settings.GEMINI_VIDEO_UNARY_ATTEMPTS)
        retry_sleep_s = float(settings.GEMINI_VIDEO_RETRY_WAIT_SEC)
        gc = genai.types.GenerationConfig(
            max_output_tokens=8192,
            temperature=0.18,
        )

        def _unary_twice_on(vf_obj: object) -> str:
            """같은 활성 파일에 대해 unary 를 두 번까지 시도합니다(빈 본문 재시도용)."""
            ro = _gemini_video_request_options(rpc_deadline_sec=rpc_timeout)
            resp = model.generate_content(
                [_VIDEO_PROMPT, vf_obj],
                generation_config=gc,
                safety_settings=safety_settings,
                stream=False,
                request_options=ro,
            )
            text, diag = _gather_gemini_generate_content_text(resp, filename=filename)
            if text:
                return text
            logger.warning("동영상 unary 빈 본문 재호출 예정(%s): %s", filename, diag or "?")
            time.sleep(2)
            rf = genai.get_file(vf_name)
            resp2 = model.generate_content(
                [_VIDEO_PROMPT, rf],
                generation_config=gc,
                safety_settings=safety_settings,
                stream=False,
                request_options=ro,
            )
            text2, diag2 = _gather_gemini_generate_content_text(resp2, filename=filename)
            if not text2:
                logger.warning("동영상 unary 2회 모두 빈 본문(%s): %s", filename, diag2 or "?")
            return text2

        def _stream_concat_on(vf_obj: object) -> str:
            """스트리밍 응답을 이어붙여 한 번 더 시도합니다(504 완화·긴 처리에 도움이 되는 경우가 있습니다)."""
            ro = _gemini_video_request_options(rpc_deadline_sec=rpc_timeout)
            it = model.generate_content(
                [_VIDEO_PROMPT, vf_obj],
                generation_config=gc,
                safety_settings=safety_settings,
                stream=True,
                request_options=ro,
            )
            blobs: list[str] = []
            for piece in it:
                t = getattr(piece, "text", None) or ""
                if t.strip():
                    blobs.append(t)
                    continue
                try:
                    cands = getattr(piece, "candidates", None) or []
                    if not cands:
                        continue
                    prts = getattr(getattr(cands[0], "content", None), "parts", None) or []
                    blobs.append(
                        "".join(getattr(p, "text", "") or "" for p in prts),
                    )
                except (IndexError, AttributeError, TypeError):
                    continue
            return "".join(blobs).strip()

        summarized = ""
        for attempt_idx in range(unary_attempts):
            vf = genai.get_file(vf_name)
            try:
                summarized = _unary_twice_on(vf).strip()
            except (DeadlineExceeded, ServiceUnavailable) as de:
                logger.warning(
                    "동영상 unary 일시 실패 시도 %d/%d (%s, RPC 데드라인=%ss): %s",
                    attempt_idx + 1,
                    unary_attempts,
                    filename,
                    rpc_timeout,
                    de,
                )
                summarized = ""
            if summarized:
                return summarized[:12000]
            if attempt_idx + 1 < unary_attempts:
                logger.info(
                    "동영상 재시도 전 대기 %.0fs (시도 %d/%d 종료 후, %s)",
                    retry_sleep_s,
                    attempt_idx + 1,
                    unary_attempts,
                    filename,
                )
                time.sleep(retry_sleep_s)

        vf = genai.get_file(vf_name)
        try:
            stream_out = _stream_concat_on(vf).strip()
            if stream_out:
                logger.info("동영상 요약 성공(stream 폴백): %s", filename)
                return stream_out[:12000]
        except (DeadlineExceeded, ServiceUnavailable):
            logger.warning("동영상 스트림 일시 실패 (503/연결 리셋·타임아웃 등 가능, %s)", filename)

        rpc_i = settings.GEMINI_VIDEO_RPC_TIMEOUT_SEC
        ua_i = settings.GEMINI_VIDEO_UNARY_ATTEMPTS
        return (
            f"[동영상 자료] {filename} — 요약 호출 도중 연결이 끊겼습니다(예: 503, Connection reset by peer)."
            f" GEMINI_VIDEO_RPC_TIMEOUT_SEC({rpc_i}s)·UNARY_ATTEMPTS({ua_i})·GEMINI_VIDEO_RETRY_WAIT_SEC 로"
            " 재시도 간격을 조정한 뒤 다시 업로드하거나, 더 짧은 클립·낮은 해상도·대본(txt)을 권장합니다."
        )
    except ValueError as e:
        logger.warning("동영상 MIME/업로드 인자 문제 (%s): %s", filename, e)
    except Exception as e:
        logger.exception("동영상 LLM 추출 중 예외(%s)", filename)

    finally:
        if vf_name:
            try:
                import google.generativeai as genai

                genai.configure(api_key=settings.LLM_API_KEY)
                genai.delete_file(vf_name)
            except Exception:
                logger.debug("Gemini 업로드 파일 삭제 생략(%s)", vf_name, exc_info=True)

    return (
        f"[동영상 자료] {filename} — 내용 추출에 실패했습니다. 형식(mp4)·길이·API 할당량을 확인하거나 나중에 다시 시도해 주세요."
    )
