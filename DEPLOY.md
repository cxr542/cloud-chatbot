# cloud-chatbot 배포

## 팀 공유 (권장) — 사무실 Mac IP + 포트

Render 없이 **같은 LAN** 팀원이 호스트 Mac IP로 접속합니다.

→ **[DEPLOY-LAN.md](./DEPLOY-LAN.md)** · `./scripts/lan-share.sh`

```bash
cd ~/okestro-app/apps/cloud-chatbot
source .venv/bin/activate
./scripts/lan-share.sh
# → http://192.168.x.x:10000/home  를 팀에 공유
```

교육생(`/`)·관리자(`/admin/`)·홈(`/home`)은 **포트 10000** nginx 게이트웨이 하나로 제공됩니다.

---

## 로컬 개발 (본인만)

```bash
python run.py
```

- 교육생: http://localhost:9999  
- 관리자: http://localhost:9998  
- `/admin/` 경로는 9999에서 동작하지 않음 → 팀 공유 시 `lan-share.sh` 사용

---

## (선택) 외부 HTTPS — Cloudflare Tunnel

[DEPLOY-OFFICE-SHARE.md](./DEPLOY-OFFICE-SHARE.md) · `./scripts/office-share.sh`

---

## (선택) 클라우드 Docker — Render 등

24/7·LAN 밖 접속이 필요할 때만 사용합니다.

**Runtime: Docker**, 루트 `Dockerfile`. Plan **1GB+** 권장 (`sentence-transformers`).

1. GitHub `cxr542/cloud-chatbot` push  
2. Render → **New Web Service** → **Docker**  
3. 환경 변수: `LLM_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`  
4. URL 확인: `/home`, `/`, `/admin/`

Blueprint: 루트 `render.yaml`

로컬 Docker 테스트:

```bash
cd apps/cloud-chatbot
docker build -t cloud-chatbot .
docker run --rm -p 10000:10000 \
  -e LLM_API_KEY=your_key \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=test \
  cloud-chatbot
```

---

## 참고

- `uploads/` 대용량 mp4는 Git에 포함하지 않습니다.
- SQLite·업로드는 호스트 Mac 또는 **Render 재배포·재시작** 시 초기화될 수 있습니다.
- **챗봇 성격(시스템 프롬프트)** 은 SQLite `settings.system_prompt`에 저장됩니다. 컨테이너가 새로 뜨면 관리자 UI에서만 넣은 내용은 사라질 수 있습니다.
  - **권장:** 프로젝트 루트 `my_prompt.txt`에 백업해 Git에 포함 → 배포 시 DB가 기본 문구일 때 자동 반영(`seed_system_prompt_from_bootstrap`).
  - Render에서 UI로만 수정한 뒤 재배포했다면, 관리자 `/admin/`에서 다시 **성격 적용**하거나 `my_prompt.txt`를 push 후 재배포하세요.
  - 장기 보존: Render **Disk** 마운트 또는 `SYSTEM_PROMPT` 환경 변수(긴 텍스트는 파일 쪽이 관리하기 쉬움).
- Netlify는 이 앱에 맞지 않습니다 (FastAPI + 임베딩).
