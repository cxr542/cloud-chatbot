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

class AdminHTTPBasic(HTTPBasic):
    """브라우저가 아닌 /docs 등에서 인증이 없을 때 메시지를 한글로 돌려 교육용으로 이해하기 쉽게 합니다."""

    def make_not_authenticated_error(self) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "관리자 인증이 필요합니다. "
                "방법 ① 주소를 http://127.0.0.1:9998/ 처럼 루트(/)로 열어 화면 안내에 따라 로그인. "
                "방법 ② /docs 의 Authorize → HTTPBasic 에 .env 의 ADMIN_USERNAME / ADMIN_PASSWORD 입력."
            ),
            headers={"WWW-Authenticate": "Basic"},
        )


security = AdminHTTPBasic()


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
def read_admin_html():
    """
    관리자 HTML은 공개로 내려주고, 데이터/API는 HTTP Basic으로 보호합니다.

    첫 화면부터 Basic을 요구하면 일부 브라우저에서 JSON Not authenticated 응답만 보일 수 있어,
    로그인은 admin.html 이 API 요청에 Authorization 헤더를 붙이는 방식으로 처리합니다.
    """
    return FileResponse(str(BASE_DIR / "admin.html"))

app.include_router(stats_router, dependencies=[Depends(get_current_admin)])
app.include_router(upload_router, dependencies=[Depends(get_current_admin)])
app.include_router(settings_router, dependencies=[Depends(get_current_admin)])
