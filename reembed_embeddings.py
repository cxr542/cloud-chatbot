"""
기존 ``data/vector.json`` 에서 ``embedding`` 이 비어 있는 청크만 골라
로컬 ``sentence-transformers`` 모델로 벡터를 채웁니다.

**주의:** 용량이 큰 JSON이 되므로, 실행 전에 ``data/vector.json`` 백업을 권장합니다.

실행 (프로젝트 루트에서):

- macOS: ``python3 reembed_embeddings.py``
- Windows: ``python reembed_embeddings.py``
"""

from __future__ import annotations

# config가 .env 를 읽도록
import backend.config  # noqa: F401
from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.services.local_embeddings import chunk_to_embed_text, embed_texts_safe


def main() -> None:
    """
    벡터 파일을 열어 임베딩이 없는 행만 인코딩한 뒤 다시 저장합니다.
    """
    store = FileVectorStore()
    chunks = store.load_chunks()
    if not chunks:
        print("[*] vector.json 에 청크가 없습니다. 종료합니다.")
        return

    need_idx = [i for i, c in enumerate(chunks) if not c.embedding]
    if not need_idx:
        print("[OK] 모든 청크에 이미 embedding 이 있습니다.")
        return

    print(f"[*] 임베딩이 필요한 청크: {len(need_idx)} / {len(chunks)}")
    texts = [chunk_to_embed_text(chunks[i].title, chunks[i].content) for i in need_idx]
    vectors = embed_texts_safe(texts)
    new_list: list[RetrievalChunk] = list(chunks)
    for j, i in enumerate(need_idx):
        c = new_list[i]
        vec = vectors[j] if j < len(vectors) else None
        new_list[i] = RetrievalChunk(
            page=c.page,
            title=c.title,
            content=c.content,
            embedding=vec,
            ref_time_band=c.ref_time_band,
        )

    store.save_chunks(new_list)
    filled = sum(1 for c in new_list if c.embedding)
    print(f"[SUCCESS] 저장 완료. embedding 이 있는 청크: {filled} / {len(new_list)}")


if __name__ == "__main__":
    main()
