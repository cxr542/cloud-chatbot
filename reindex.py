import os
import fitz
from pathlib import Path
from backend.db.vector import FileVectorStore, RetrievalChunk

def reindex_all():
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
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                # 텍스트 추출 시 유니코드 보존을 위해 기본 설정 사용
                text = page.get_text("text")
                if text.strip():
                    chunk = RetrievalChunk(
                        page=page_num + 1, 
                        title=f"{filename} (p.{page_num + 1})", 
                        content=text[:2000]
                    )
                    store.add_chunk(chunk)
            print(f"    -> {len(doc)} pages processed")
        except Exception as e:
            print(f"    [ERROR] {filename}: {e}")

    print("\n[FINISH] Re-indexing complete!")

if __name__ == "__main__":
    reindex_all()
