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
  exporter.py            계획 스냅샷 PDF·날짜별 PNG/ZIP 생성
  routers/               auth · catalog(축제·장소·위치) · trips · exports · me
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
| GEMINI_API_KEY | Google AI Studio에서 발급한 키 | 서버에서 Gemini 추천 호출. 로컬에서 키 없이 SEED_DEMO=1이면 테스트용 추천 동작 |
| GEMINI_MODEL | `gemini-3.5-flash-lite` | 선택. 생략하면 이 모델을 사용 |
| ODSAY_API_KEY | ODsay에서 발급한 서버용 API 키 | 시내·고속/시외버스·열차 예상 경로. 실제 운행 시각·대기시간·막차는 미확인 |
| KAKAO_REST_API_KEY | | 카카오 로그인 + 위치 검색 |
| KAKAO_MOBILITY_REST_API_KEY | 카카오디벨로퍼스 REST API 키 | 자동차 길찾기. 키가 있으면 `CAR` 계획에 첫 축제장까지 예상 이동시간과 거리 저장 |
| KAKAO_CLIENT_SECRET | | 카카오 앱에서 켰을 때만 |
| KAKAO_REDIRECT_URI | `http://localhost:3000/api/v1/auth/callback/kakao` | 카카오 앱에 같은 주소 등록 |
| EXPORT_STORAGE_DIR | `backend/storage/exports` | 선택. PDF·PNG·ZIP 저장 위치 |
| EXPORT_FONT_PATH | `C:/Windows/Fonts/malgun.ttf` | 선택. 배포 서버의 한글 TTF/TTC 글꼴 경로 |

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

## 생성 기능의 현재 범위와 남은 연결

대중교통은 ODsay 시내·장거리 경로를 당일·1박 2일 일정에 연결한다. 장거리 후보 최대 3개를 순서대로 확인하고 첫 완성 경로를 사용하며, 계획 하나 안의 동일 좌표 요청은 메모리 캐시로 재사용한다. 승차 준비시간은 구간마다 15분을 가정한다. 경로가 없는 직선거리 1km 이하 구간은 거리 1.5배/분당 60m로 도보를 추정하며 보행 가능 여부는 미확인이다. `legs[].segments`에 세부 구간과 추정 근거를 저장하고 `serviceTimeStatus`, `waitingTimeStatus`는 `UNCONFIRMED`로 표시한다. 총 요금은 미확인으로 둔다. 실제 운행·대기·막차를 검증하지 않으며 계획은 검토 필요 상태다.

- 계획 생성: Gemini는 DB에서 확보한 후보의 적합도를 순위로 반환한다. 로컬에서 `SEED_DEMO=1`과 `GEMINI_API_KEY`를 함께 설정하면 가상 후보로 실제 호출을 확인할 수 있다. 자차 여행은 고정 공연을 기준으로 날짜별 필수 장소와 추천 장소를 배치한다. 1박 2일은 같은 숙소를 첫날 도착점과 둘째 날 출발점으로 연결하고 마지막 날 최초 출발지로 돌아온다. 이동시간은 조회 시점 기준 예상치다. 대중교통은 연결 전이다.

로컬에서 시간표를 확인하려면 `fst_demo_01`의 시작일로 당일 자차 여행을 만들고 `requiredProgramIds`에 `pr_demo_01`, `requiredPlaceIds`에 `pl_demo_02`를 넣는다. 장소 영업시간은 한국시간 기준 요일 키 `mon`~`sun`과 구간 배열을 사용한다. 예시 형식은 `{"mon":[{"open":"09:00","close":"13:00"}]}`이다.

1박 2일은 `fst_demo_01` 첫날과 다음 날을 선택하고 `lodgingPreference: "HOTEL"`, `requiredProgramIds: ["pr_demo_01", "pr_demo_04"]`를 보낸다. 숙소 후보는 축제장 반경 5km 안의 `LODGING` 장소이며 특정 숙박 유형은 `tags`에 `HOTEL`, `PENSION`, `GUESTHOUSE`를 사용한다. 숙소 도착·출발 시간은 경로 계산으로 얻은 예상 시각이다. 직접 예약한 숙소의 좌표는 현재 요청에서 받지 않으므로 `SELF_ARRANGED`이면 연결 구간을 미확인으로 표시한다.
- 계획 상세에서 PDF 또는 날짜별 PNG 내보내기를 요청할 수 있다. 여러 날짜의 PNG는 ZIP으로 묶고, 파일에는 계획 버전·생성 시각·일정·이동 구간·예산·경고를 포함한다. 파일 다운로드는 계획 소유자에게만 허용한다. 로컬 파일 저장은 배포 시 S3 호환 객체 저장소로 교체한다.
- 생성·내보내기 작업은 FastAPI BackgroundTasks로 실행 → 배포 전 Upstash Workflow로 이전.
- 테이블은 시작 시 `create_all` → 운영 DB 전에 Alembic.
- 게시판·사진 업로드·계정/탈퇴·서비스 안내 API.
