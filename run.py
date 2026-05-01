import subprocess
import sys
import time
import os

def run_servers():
    print("🚀 클라우드 챗봇 플랫폼 서버 가동을 시작합니다...")
    
    # .env 파일 확인
    if not os.path.exists(".env"):
        print("⚠️ .env 파일이 없습니다. 기본 설정을 생성합니다.")
        with open(".env", "w", encoding="utf-8") as f:
            f.write("ADMIN_USERNAME=admin\nADMIN_PASSWORD=cloud1234!\nDB_PATH=chatbot_data.db\n")

    # 1. 백엔드 & 교육생 서버 (Port 9999)
    print("📦 [1/2] 교육생 서버 시작 중 (Port 9999)...")
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", "9999", "--reload"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    # 2. 관리자 서버 (Port 9998)
    print("⚙️ [2/2] 관리자 서버 시작 중 (Port 9998)...")
    admin_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "admin.main:app", "--port", "9998", "--reload"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    print("\n✅ 모든 서버가 가동되었습니다!")
    print("-" * 50)
    print("🏠 게이트웨이 페이지: http://localhost:9999/home")
    print("📖 교육생 모드 직접 접속: http://localhost:9999")
    print("🛠️ 관리자 모드 직접 접속: http://localhost:9998")
    print("-" * 50)
    print("종료하려면 Ctrl+C를 누르세요.\n")

    try:
        while True:
            # 서버 출력 모니터링 (필요 시)
            line1 = backend_proc.stdout.readline()
            if line1:
                print(f"[Backend] {line1.strip()}")
            
            line2 = admin_proc.stdout.readline()
            if line2:
                print(f"[Admin] {line2.strip()}")
            
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n👋 서버를 종료합니다...")
        backend_proc.terminate()
        admin_proc.terminate()
        print("✅ 종료 완료.")

if __name__ == "__main__":
    run_servers()
