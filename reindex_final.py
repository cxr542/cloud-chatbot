import os
import fitz
import json
from pathlib import Path

# RetrievalChunk 클래스 모방 (dataclass 대신 dict 사용)
def reindex_final():
    BASE_DIR = Path(".").resolve()
    UPLOAD_DIR = BASE_DIR / "uploads"
    VECTOR_FILE = BASE_DIR / "data" / "vector.json"
    
    # 기존 데이터 초기화
    new_chunks = []
    
    pdf_files = list(UPLOAD_DIR.glob("*.pdf"))
    print(f"[*] Starting re-indexing for: {[f.name for f in pdf_files]}")
    
    for pdf_path in pdf_files:
        filename = pdf_path.name
        print(f"[-] Processing {filename}...")
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                # 한글 깨짐 방지를 위해 원문 그대로 추출
                text = page.get_text("text")
                if text.strip():
                    chunk = {
                        "page": page_num + 1,
                        "title": f"{filename} (p.{page_num + 1})",
                        "content": text[:2000]
                    }
                    new_chunks.append(chunk)
            print(f"    -> {len(doc)} pages added.")
        except Exception as e:
            print(f"    [ERROR] Failed to process {filename}: {e}")

    # UTF-8 인코딩을 명시하여 파일 저장
    with open(VECTOR_FILE, "w", encoding="utf-8") as f:
        json.dump(new_chunks, f, ensure_ascii=False, indent=2)
        
    print(f"\n[SUCCESS] Re-indexing complete! Total chunks: {len(new_chunks)}")

if __name__ == "__main__":
    reindex_final()
