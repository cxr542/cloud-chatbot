import os
import fitz
from pathlib import Path
from backend.db.vector import FileVectorStore, RetrievalChunk
from backend.services.local_embeddings import chunk_to_embed_text, embed_texts_safe

def reindex_all():
    """
    ``uploads`` 폴더의 PDF를 다시 읽어 ``data/vector.json`` 을 갱신합니다.

    기존 JSON은 ``.bak`` 으로 이름만 바꾼 뒤, 페이지 청크마다 로컬 임베딩을 붙여 저장합니다.

    주의: **이미지·동영상·TXT 로만 쌓인 청크는 이 스크립트가 재생성하지 않습니다.**(PDF만 처리)
    관리자 화면에서 다시 업로드하거나 vector.json 백업에서 병합해야 합니다.
    """
    BASE_DIR = Path(".").resolve()
    UPLOAD_DIR = BASE_DIR / "uploads"
    VECTOR_FILE = BASE_DIR / "data" / "vector.json"
    
    # 1. 기존 벡터 파일 초기화 (백업 후 삭제)
    if VECTOR_FILE.exists():
        os.rename(VECTOR_FILE, str(VECTOR_FILE) + ".bak")
        print(f"[OK] Backup created: {VECTOR_FILE}.bak")
    
    store = FileVectorStore()
    
    # 2. uploads 폴더의 모든 PDF 재인덱싱
    pdf_files = list(UPLOAD_DIR.glob("*.pdf"))
    print(f"[*] Target files: {[f.name for f in pdf_files]}")
    
    for pdf_path in pdf_files:
        filename = pdf_path.name
        print(f"[-] Processing {filename}...")
        try:
            doc = fitz.open(pdf_path)
            batch: list[RetrievalChunk] = []
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text("text")
                if text.strip():
                    body = text[:2000]
                    t = f"{filename} (p.{page_num + 1})"
                    batch.append(RetrievalChunk(page=page_num + 1, title=t, content=body))
            if batch:
                texts = [chunk_to_embed_text(c.title, c.content) for c in batch]
                vecs = embed_texts_safe(texts)
                with_emb = [
                    RetrievalChunk(c.page, c.title, c.content, vecs[i] if i < len(vecs) else None)
                    for i, c in enumerate(batch)
                ]
                store.add_chunks(with_emb)
            print(f"    -> {len(doc)} pages processed")
        except Exception as e:
            print(f"    [ERROR] {filename}: {e}")

    print("\n[FINISH] Re-indexing complete!")

if __name__ == "__main__":
    reindex_all()
