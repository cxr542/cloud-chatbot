# cloud-chatbot 배포 (Render + Docker)

교육생(`/`)·관리자(`/admin/`)를 **한 URL**에서 nginx로 나눕니다.

## 1. Render에서 서비스 만들기

1. [Render](https://render.com) 로그인 → **New +** → **Web Service**
2. GitHub `cxr542/cloud-chatbot` 연결
3. **Runtime: Docker**
4. Plan: **Starter 이상 권장** (sentence-transformers 메모리, 기본 512MB free는 부족할 수 있음)
5. **Environment Variables** (필수):

| Key | 설명 |
|-----|------|
| `LLM_API_KEY` | Google AI Studio / Gemini API 키 |
| `ADMIN_USERNAME` | 관리자 로그인 (예: `admin`) |
| `ADMIN_PASSWORD` | 관리자 비밀번호 (강한 값) |

6. Deploy

배포 URL 예: `https://cloud-chatbot-xxxx.onrender.com`

- 홈: `/home`
- 교육생: `/`
- 관리자: `/admin/` (HTTP Basic — `.env` 와 동일)

## 2. Blueprint (선택)

저장소 루트의 `render.yaml` → Render **New Blueprint** 로 일괄 생성 가능.

## 3. 로컬 Docker 테스트

```bash
cd apps/cloud-chatbot   # 또는 저장소 루트
docker build -t cloud-chatbot .
docker run --rm -p 10000:10000 \
  -e LLM_API_KEY=your_key \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=test \
  cloud-chatbot
```

브라우저: http://localhost:10000/home

## 4. 참고

- `uploads/` 의 대용량 mp4는 Git·이미지에 포함하지 않습니다. PDF·`data/vector.json`·DB는 포함됩니다.
- SQLite·업로드는 컨테이너 재배포 시 초기화될 수 있습니다. 영구 디스크가 필요하면 Render Disk 추가를 검토하세요.
- 로컬 개발은 기존처럼 `python run.py` (9999 / 9998) 를 사용합니다.
