from fastapi import APIRouter, File, UploadFile
from backend.db.vector import FileVectorStore, RetrievalChunk

router = APIRouter(prefix="/api/admin/docs", tags=["docs"])

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
    text = content.decode("utf-8")
    
    # 임시 데모용: 파일 전체를 하나의 청크로 저장 (최대 1000자 제한)
    chunk = RetrievalChunk(page=1, title=file.filename, content=text[:1000])
    store = FileVectorStore()
    store.add_chunk(chunk)
    return {"status": "uploaded", "filename": file.filename}
