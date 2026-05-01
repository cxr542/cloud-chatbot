from fastapi import APIRouter
from pydantic import BaseModel
from backend.db.database import get_settings, set_settings

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])

class PromptRequest(BaseModel):
    prompt: str

@router.get("/prompt")
def read_prompt():
    prompt = get_settings("system_prompt")
    return {"system_prompt": prompt}

@router.post("/prompt")
def update_prompt(body: PromptRequest):
    set_settings("system_prompt", body.prompt)
    return {"status": "updated", "system_prompt": body.prompt}
