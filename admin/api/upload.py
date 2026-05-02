import os
import fitz  # PyMuPDF
from pathlib import Path
from fastapi import APIRouter, File, UploadFile, HTTPException
from backend.db.vector import FileVectorStore, RetrievalChunk

router = APIRouter(prefix="/api/admin/docs", tags=["docs"])
BASE_DIR = Path(__file__).parent.parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

@router.get("/")
def list_docs():
    store = FileVectorStore()
    return {"chunks": store.load_chunks()}

@router.delete("/{title}")
def delete_doc(title: str):
    store = FileVectorStore()
    store.delete_chunk(title)
    return {"status": "deleted", "title": title}

@router.post("/upload")
async def upload_doc(file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename
    store = FileVectorStore()
    
    if filename.endswith(".pdf"):
        # 파일 저장
        save_path = UPLOAD_DIR / filename
        with open(save_path, "wb") as f:
            f.write(content)
            
        # PDF 처리
        doc = fitz.open(stream=content, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text()
            if text.strip():
                # 페이지별로 청크 생성 (제목에 페이지 정보 포함)
                chunk = RetrievalChunk(page=page_num + 1, title=f"{filename} (p.{page_num + 1})", content=text[:2000])
                store.add_chunk(chunk)
        return {"status": "uploaded", "filename": filename, "pages": len(doc)}
    
    elif filename.endswith(".txt"):
        # TXT 처리
        text = content.decode("utf-8")
        chunk = RetrievalChunk(page=1, title=filename, content=text[:2000])
        store.add_chunk(chunk)
        return {"status": "uploaded", "filename": filename}
    
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Use .pdf or .txt")
