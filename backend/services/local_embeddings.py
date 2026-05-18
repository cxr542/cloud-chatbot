"""
로컬 CPU에서 문장 임베딩을 계산합니다.

교육용으로 ``sentence-transformers`` 한 가지 모델만 사용하고,
쿼리·문서 벡터는 **L2 정규화**해 두어 코사인 유사도를 내적으로 계산합니다.
"""

from __future__ import annotations

from backend.config import settings

# (모델명, SentenceTransformer 인스턴스) — 프로세스당 한 번 로드합니다.
_model_singleton: tuple[str, object] | None = None


def chunk_to_embed_text(title: str, content: str, *, max_content_chars: int = 2000) -> str:
    """
    하나의 청크를 임베딩 모델에 넣을 단일 문자열로 만듭니다.

    제목과 본문을 이어 붙여 질문과의 의미적 매칭에 제목(파일명·페이지) 정보가
    섞이도록 합니다.

    Args:
        title: 청크 제목(파일명·페이지 등).
        content: 본문 텍스트.
        max_content_chars: 본문 최대 글자 수(모델 입력 길이·속도 보호).

    Returns:
        임베딩 입력용 문자열.
    """
    body = content if len(content) <= max_content_chars else content[:max_content_chars]
    return f"{title}\n{body}"


def _load_model():
    """
    HuggingFace ``SentenceTransformer`` 모델을 최초 1회만 로드합니다.

    ``LOCAL_EMBEDDING_MODEL`` 설정이 바뀌면 새 이름으로 다시 로드합니다.

    Returns:
        sentence_transformers.SentenceTransformer 인스턴스.

    Raises:
        ImportError: sentence-transformers 또는 의존 패키지가 없을 때.
    """
    global _model_singleton
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError(
            "로컬 임베딩을 쓰려면 pip install sentence-transformers 로 설치하세요."
        ) from e

    model_name = settings.LOCAL_EMBEDDING_MODEL
    if _model_singleton and _model_singleton[0] == model_name:
        return _model_singleton[1]

    loaded = SentenceTransformer(model_name)
    _model_singleton = (model_name, loaded)
    return loaded


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    문자열 여러 개를 한 번에 임베딩하고, 각 벡터를 L2 정규화한 뒤 파이썬 리스트로 반환합니다.

    Args:
        texts: 비어 있지 않은 입력 문장·문단 목록.

    Returns:
        ``texts`` 와 같은 길이의 벡터 리스트(각 벡터는 float 리스트).

    Raises:
        ImportError: 모델 라이브러리가 없을 때.
        ValueError: texts가 비어 있을 때.
    """
    if not texts:
        raise ValueError("embed_texts 에는 최소 한 개의 문자열이 필요합니다.")
    model = _load_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    # 배치 1개일 때 1차원 ndarray가 나올 수 있어 항상 행 단위 리스트로 맞춥니다.
    if hasattr(vectors, "ndim") and vectors.ndim == 1:
        return [vectors.tolist()]
    return vectors.tolist()


def embed_texts_safe(texts: list[str]) -> list[list[float] | None]:
    """
    embed_texts 와 같으나, 오류 시 전부 None이 담긴 리스트를 돌려 업로드·검색이 죽지 않게 합니다.

    Args:
        texts: 임베딩할 문자열 목록(빈 리스트면 빈 리스트 반환).

    Returns:
        원소별 벡터 또는 None.
    """
    if not texts:
        return []
    try:
        got = embed_texts(texts)
        return got
    except (ImportError, OSError, RuntimeError, ValueError):
        return [None] * len(texts)


def embed_query_vector_for_retrieval(query: str) -> list[float] | None:
    """
    벡터 저장소 검색 전용 질문 임베딩입니다.

    청크는 ``chunk_to_embed_text(제목, 본문)`` 과 같이 **제목 줄 + 본문** 형태이므로,
    질문도 ``질문`` 줄 다음에 본문을 두는 형태로 맞추면 코사인 매칭이 안정되는 경우가 많습니다.

    Args:
        query: 사용자 질문 또는 검색어.

    Returns:
        정규화 벡터, 실패 시 None.
    """
    q = query.strip()
    if not q:
        return None
    boxed = chunk_to_embed_text("질문", q, max_content_chars=1600)
    got = embed_texts_safe([boxed])
    if not got or not got[0]:
        return None
    return got[0]


def embed_query_vector(query: str) -> list[float] | None:
    """
    검색 외 호환용 질문 임베딩입니다. 가능하면 검색에는 ``embed_query_vector_for_retrieval`` 을 쓰세요.

    Args:
        query: 사용자 질문.

    Returns:
        정규화된 벡터; 실패·공백만 있는 질문이면 None.
    """
    q = query.strip()
    if not q:
        return None
    try:
        return embed_texts([q])[0]
    except (ImportError, OSError, RuntimeError, ValueError):
        return None


def embedding_similarity_dot(query_vec: list[float], doc_vec: list[float] | None) -> float:
    """
    두 벡터가 모두 L2 정규화되어 있다고 가정하고 코사인 유사도(내적)를 계산합니다.

    Args:
        query_vec: 질문 임베딩.
        doc_vec: 문서 청크 임베딩(None이면 0점).

    Returns:
        -1~1에 가까운 유사도(통상 양수 위주).
    """
    if not doc_vec or not query_vec:
        return 0.0
    if len(query_vec) != len(doc_vec):
        return 0.0
    return float(sum(a * b for a, b in zip(query_vec, doc_vec)))
