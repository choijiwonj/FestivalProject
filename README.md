# 축제잇길

충남 지역축제 연계 맞춤형 여행 플래너. 요구사항 정의서 v1.1 · API 명세서 v0.3 · 기술 스택 정리표 기준.

```
frontend/  Next.js · TypeScript · Tailwind (Vercel 프로젝트 1)
  src/lib/api.ts         /api/v1 호출, CSRF, 오류 형식
  src/app/               홈 · 축제 상세 · 조건 입력(3단계) · 생성 진행 · 계획 · 마이페이지
backend/   FastAPI · SQLAlchemy (Vercel 프로젝트 2)
  main.py                앱, 공통 오류 처리, Cache-Control
  core.py                응답 봉투·페이지·세션·CSRF·서명된 위치 ID
  db.py                  테이블 모델
  planner.py             여행 입력 검증(B02 등)·계획 생성
  routers/               auth · catalog(축제·장소·위치) · trips · me
```

브라우저는 프런트 도메인의 `/api/*`만 호출하고, Next.js rewrites가 백엔드로 넘긴다(`BACKEND_URL`). 쿠키·CSRF·카카오 콜백이 한 도메인에서 처리된다.

## 환경변수

백엔드 (`backend/.env` 또는 셸 환경변수)

| 이름 | 로컬 예 | 설명 |
|---|---|---|
| DATABASE_URL | `sqlite:///./dev.db` | 배포는 Supabase PostgreSQL URL |
| SECRET_KEY | 긴 임의 문자열 | 위치 ID 서명. 배포 필수 |
| COOKIE_SECURE | `0` | 로컬 http에서만 0, 배포는 기본값 1 |
| DEV_LOGIN | `1` | 카카오 없이 `/api/v1/auth/login/dev` 로그인. **배포 금지** |
| SEED_DEMO | `1` | 가상 축제·장소 데이터 넣기. **배포 금지** |
| KAKAO_REST_API_KEY | | 카카오 로그인 + 위치 검색 |
| KAKAO_CLIENT_SECRET | | 카카오 앱에서 켰을 때만 |
| KAKAO_REDIRECT_URI | `http://localhost:3000/api/v1/auth/callback/kakao` | 카카오 앱에 같은 주소 등록 |

프런트 (`frontend/.env.local`)

| 이름 | 로컬 예 | 설명 |
|---|---|---|
| BACKEND_URL | `http://localhost:8000` | rewrites 대상 |
| NEXT_PUBLIC_LOGIN_PROVIDER | `dev` | 배포는 `kakao`(기본값). 빌드 시점에 들어감 |

## 실행

```sh
# 백엔드 (http://localhost:8000/docs)
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
fastapi dev main.py
pytest

# 프런트 (http://localhost:3000)
cd frontend
npm install
npm run dev
```

## 아직 연결 안 된 것

- 계획 생성: AI 장소 추천(Gemini)·경로(카카오모빌리티/ODsay)·일정 엔진 미연결 → 주 축제·필수 프로그램·필수 장소만 담고 `NEEDS_REVIEW`로 저장. 이동시간·비용은 미확인으로 남김.
- 생성 작업은 FastAPI BackgroundTasks로 실행 → 배포 전 Upstash Workflow로 이전.
- 테이블은 시작 시 `create_all` → 운영 DB 전에 Alembic.
- 게시판·사진 업로드·재추천·내보내기·계정/탈퇴·서비스 안내 API.
