# cloud-chatbot 배포

교육생(`/`)·관리자(`/admin/`)를 **한 URL**에서 nginx로 나눕니다.

## 빠른 체크리스트

1. **GitHub push** — 로컬 `main`이 origin보다 앞서 있으면 먼저 push (아래 0단계)
2. [Render](https://render.com) → **New Web Service** → `cxr542/cloud-chatbot` → **Docker**
3. Plan **Starter ($7)** 이상 — Free 512MB는 `sentence-transformers` 로 OOM 날 수 있음
4. 환경 변수: `LLM_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`
5. Deploy 후 URL 확인: `/home`, `/`, `/admin/`

## Netlify는 이 저장소에 쓰지 않습니다

이 앱은 **FastAPI 2프로세스 + sentence-transformers** 입니다.  
Netlify는 Next.js·정적 사이트용이며, 예전에 쓰신 Netlify 배포는 **`gdrb-scheduler`** (`cursorstudy/experiments/dynamic_deploy_test`) 쪽입니다.

→ Netlify 절차: [`cursorstudy/DEPLOY-NETLIFY.md`](../../cursorstudy/DEPLOY-NETLIFY.md)

### 0. GitHub에 코드 올리기 (필수)

```bash
cd ~/okestro-app/apps/cloud-chatbot
git push origin main
```

HTTPS 인증이 안 되면: GitHub **Settings → SSH keys** 후 `git remote set-url origin git@github.com:cxr542/cloud-chatbot.git`

---

## Docker 호스팅 (Railway / Fly.io / Render 등)

**Runtime: Docker**, 저장소 루트 `Dockerfile`.  
Free 512MB 플랜은 임베딩 로딩 시 OOM이 날 수 있어 **1GB 이상** 플랜을 권장합니다.

### 1. 서비스 만들기 (예: Render)

1. 호스트 대시보드 → **New Web Service** (또는 동일 개념)
2. GitHub **`cxr542/cloud-chatbot`** 연결
3. **Docker** 선택
4. **Environment Variables** (필수):

| Key | 설명 |
|-----|------|
| `LLM_API_KEY` | Google AI Studio / Gemini API 키 |
| `ADMIN_USERNAME` | 관리자 로그인 (예: `admin`) |
| `ADMIN_PASSWORD` | 관리자 비밀번호 (강한 값) |

5. Deploy

URL 예: `https://cloud-chatbot-xxxx.onrender.com` (호스트마다 다름)

- 홈: `/home`
- 교육생: `/`
- 관리자: `/admin/` (HTTP Basic — `.env` 와 동일)

### 2. Blueprint (Render만, 선택)

루트 `render.yaml` → Render **New Blueprint** 로 일괄 생성 가능.

### 3. 로컬 Docker 테스트

```bash
cd apps/cloud-chatbot
docker build -t cloud-chatbot .
docker run --rm -p 10000:10000 \
  -e LLM_API_KEY=your_key \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=test \
  cloud-chatbot
```

브라우저: http://localhost:10000/home

### 4. 참고

- `uploads/` 의 대용량 mp4는 Git·이미지에 포함하지 않습니다.
- SQLite·업로드는 컨테이너 재배포 시 초기화될 수 있습니다. 영구 디스크는 호스트별 Volume/Disk 설정을 쓰세요.
- 로컬 개발: `python run.py` (9999 / 9998)
