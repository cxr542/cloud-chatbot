from pathlib import Path
import secrets
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.config import settings
from admin.api.stats import router as stats_router
from admin.api.upload import router as upload_router
from admin.api.settings import router as settings_router

app = FastAPI(title="Cloud Chatbot Admin API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()

def get_current_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect admin username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

BASE_DIR = Path(__file__).parent.parent

@app.get("/")
def read_admin_html(admin: str = Depends(get_current_admin)):
    return FileResponse(str(BASE_DIR / "admin.html"))

app.include_router(stats_router, dependencies=[Depends(get_current_admin)])
app.include_router(upload_router, dependencies=[Depends(get_current_admin)])
app.include_router(settings_router, dependencies=[Depends(get_current_admin)])
