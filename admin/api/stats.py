from fastapi import APIRouter
from backend.db.database import get_recent_chat_logs

router = APIRouter(prefix="/api/admin/stats", tags=["stats"])

@router.get("/logs")
def get_logs():
    logs = get_recent_chat_logs(100)
    return {"logs": logs}
