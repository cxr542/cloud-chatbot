import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.env_file import load_env_file


def _relay_uvicorn_stdout(proc: subprocess.Popen, line_prefix: str) -> None:
    """자식(uvicorn) 표준 출력 파이프를 백그라운드에서 줄 단위로 읽어 부모 터미널에 붙입니다.

    교육생(9999) 프로세스가 오랫동안 로그를 내지 않을 때 부모 메인 스레드가
    ``backend_proc.stdout.readline()`` 에만 막히면 **관리자(9998) 로그 줄이 처리되지 않는**
    순차 읽기 문제가 생깁니다. 프로세스마다 별도 스레드로 분리하면 둘 다 실시간에 가깝게 보입니다.

    Args:
        proc: ``Popen`` 으로 띄운 uvicorn 프로세스입니다.
        line_prefix: 한 줄 앞에 붙일 태그(예: ``\"[Backend]\"``).

    Returns:
        없음(스레드 대상 함수).
    """
    stream = proc.stdout
    if stream is None:
        return
    try:
        for line in iter(stream.readline, ""):
            print(f"{line_prefix} {line.rstrip()}", flush=True)
    except Exception:
        return


def run_servers():
    """교육생(9999)·관리자(9998) 서버를 각각 uvicorn 자식 프로세스로 띄우고 터미널에 로그를 모아 보여줍니다.

    자식 프로세스 출력은 파이프로 넘기므로 ``PYTHONUNBUFFERED=1`` 로 버퍼링을 줄이고,
    두 서버 각각 **전용 스레드**에서 파이프를 읽어 로그가 서로에게 막히지 않게 합니다.

    Returns:
        없음. Ctrl+C 시 자식을 종료합니다.

    Raises:
        SystemExit: uvicorn 미설치 또는 즉시 종료·포트 충돌 등으로 기동 실패 시.
    """
    print("🚀 클라우드 챗봇 플랫폼 서버 가동을 시작합니다...")

    # 지금 실행 중인 파이썬과 동일한 인터프리터로 uvicorn 자식 프로세스를 띄우므로, 여기서 먼저 확인합니다.
    if importlib.util.find_spec("uvicorn") is None:
        print(
            "❌ 'uvicorn' 모듈이 없습니다. 서버가 실제로는 뜨지 않은 상태일 수 있습니다.\n"
            "   macOS 예시:\n"
            "     cd 프로젝트폴더\n"
            "     python3 -m venv .venv\n"
            "     source .venv/bin/activate\n"
            "     python3 -m pip install -r requirements.txt\n"
            "     python3 run.py\n"
            "   Windows PowerShell 예시: .venv\\Scripts\\Activate.ps1 후 동일하게 pip install, python run.py"
        )
        sys.exit(1)
    
    project_root = Path(__file__).resolve().parent

    # .env 파일 확인
    if not (project_root / ".env").exists():
        print("⚠️ .env 파일이 없습니다. 기본 설정을 생성합니다.")
        with open(project_root / ".env", "w", encoding="utf-8") as f:
            f.write("ADMIN_USERNAME=admin\nADMIN_PASSWORD=cloud1234!\nDB_PATH=chatbot_data.db\n")

    # run.py는 별도 프로세스로 uvicorn을 띄우므로, 호스트 등 실행 옵션용 변수는 여기서 .env를 읽어야 합니다.
    load_env_file(project_root / ".env", override=True)

    # Uvicorn 기본값은 127.0.0.1 전용이라, macOS 등에서 localhost가 IPv6(::1)로 먼저 붙으면
    # 브라우저가 "사이트에 연결할 수 없음"이 됩니다. IPv4·IPv6 루프백을 함께 쓰려면 --host :: 가 안전합니다.
    # 특정 환경만 127.0.0.1로 고정하려면 .env 등에 UVICORN_HOST=127.0.0.1 을 두세요.
    uvicorn_host = os.getenv("UVICORN_HOST", "::")

    uvicorn_base = [
        sys.executable,
        "-m",
        "uvicorn",
        "--host",
        uvicorn_host,
        "--reload",
    ]

    # 자식 프로세스 stdout/err 을 PIPE로 읽기 때문에 블록 버퍼링이 걸리면 터미널에 한동안 안 보입니다.
    # PYTHONUNBUFFERED=1 로 앱 로그(uvicorn 접속 로그 등)가 곧바로 부모 줄로 넘어가게 합니다.
    server_env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    # 1. 백엔드 & 교육생 서버 (Port 9999)
    print("📦 [1/2] 교육생 서버 시작 중 (Port 9999)...")
    backend_proc = subprocess.Popen(
        uvicorn_base + ["backend.main:app", "--port", "9999"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=server_env,
    )

    # 2. 관리자 서버 (Port 9998)
    print("⚙️ [2/2] 관리자 서버 시작 중 (Port 9998)...")
    admin_proc = subprocess.Popen(
        uvicorn_base + ["admin.main:app", "--port", "9998"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=server_env,
    )

    time.sleep(2.0)
    dead = []
    if backend_proc.poll() is not None:
        out = backend_proc.stdout.read() if backend_proc.stdout else ""
        dead.append(("교육생(9999)", out))
    if admin_proc.poll() is not None:
        out = admin_proc.stdout.read() if admin_proc.stdout else ""
        dead.append(("관리자(9998)", out))
    if dead:
        print("❌ 서버 프로세스가 바로 종료되었습니다. 위에 uvicorn 오류가 있는지 확인하세요.\n")
        combined = ""
        for label, out in dead:
            print(f"--- {label} ---")
            chunk = (out or "(출력 없음)")[-3000:].strip()
            print(chunk)
            combined += chunk
        if "Address already in use" in combined or "Errno 48" in combined:
            print(
                "\n💡 포트 9998·9999가 이미 다른 프로세스에 잡혀 있습니다.\n"
                "   macOS(zsh): kill -9 $(lsof -t -nP -iTCP:9998 -sTCP:LISTEN) "
                "$(lsof -t -nP -iTCP:9999 -sTCP:LISTEN) 2>/dev/null\n"
                "   Windows(PowerShell): netstat -ano | findstr :9998  등으로 PID 확인 후 taskkill /PID … /F"
            )
        backend_proc.terminate()
        admin_proc.terminate()
        sys.exit(1)

    print("\n✅ 모든 서버가 가동되었습니다!")
    print("-" * 50)
    print("🏠 게이트웨이 페이지: http://localhost:9999/home  (안 되면 http://127.0.0.1:9999/home )")
    print("📖 교육생 모드 직접 접속: http://localhost:9999")
    print("🛠️ 관리자 모드 직접 접속: http://localhost:9998  (안 되면 http://127.0.0.1:9998 )")
    print("-" * 50)
    print("종료하려면 Ctrl+C를 누르세요.\n")

    threading.Thread(
        target=_relay_uvicorn_stdout,
        args=(backend_proc, "[Backend]"),
        daemon=True,
        name="uvicorn-backend-log",
    ).start()
    threading.Thread(
        target=_relay_uvicorn_stdout,
        args=(admin_proc, "[Admin]"),
        daemon=True,
        name="uvicorn-admin-log",
    ).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n👋 서버를 종료합니다...")
        backend_proc.terminate()
        admin_proc.terminate()
        print("✅ 종료 완료.")

if __name__ == "__main__":
    run_servers()
