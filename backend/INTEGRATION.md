# 백엔드 통합 안내

GitHub main `46766a8e2411722fd5875f4d18727f65fe098853`와 기존 로컬 백엔드를 통합했습니다.

- 아이디 로그인, scrypt 비밀번호, 계정 생성 명령과 PostgreSQL 요청/작업 저장 순서 수정을 유지합니다.
- 일정 배치, 예상 비용, 축제 상세 보충 자료, PDF·PNG 예상 비용 표기를 유지합니다.
- 후기 CRUD, 사진, 즐겨찾기, 계획 삭제, 탈퇴, 요청 제한, 정리 작업과 Alembic을 추가합니다.
- 공개 후기 API는 `routers/posts.py`에서만 등록합니다. `routers/board.py`는 기존 `board_posts` 응답 변환용입니다.
- 기존 공개 후기와 회원 작성 후기를 함께 조회하며, 기존 데이터는 이동하거나 삭제하지 않습니다.
- 회원 후기 작성 시 `title`, `visitedOn`을 선택적으로 받을 수 있습니다. 기존 요청 형식도 지원합니다.
- 회원 탈퇴 시 아이디 로그인 인증 정보도 삭제합니다. 기존 공개 후기는 회원 계정과 연결되지 않은 자료이므로 유지합니다.
- `0003` 마이그레이션은 기존 `local_credentials`, `board_posts`를 보존하고 새 후기 필드를 추가합니다.
- `SIMULATION_DATA`는 시연용 추정 자료 사용 여부입니다. 실제 운영은 0으로 설정합니다.

## DB 전환 및 실행 (PowerShell)

아래 명령은 직접 실행하는 시점에 DB를 변경합니다. 코드 통합 과정에서는 운영 DB를 변경하지 않았습니다.
로컬 파일 백업에는 원격 Supabase DB의 내용이 포함되지 않으므로 DB 백업을 별도로 확보하세요.

```powershell
cd C:\Users\User\FestivalProject\backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

기존 create_all 방식의 DB를 처음 Alembic으로 전환할 때:

```powershell
.\.venv\Scripts\python.exe migrate.py --adopt-existing
```

빈 DB 또는 이미 Alembic으로 관리 중인 DB:

```powershell
.\.venv\Scripts\python.exe migrate.py
```

전환 도구는 기본 테이블·컬럼을 확인한 뒤에만 0001 이력을 등록하고 최신 버전으로 올립니다.
이력이 없는 DB에 새 테이블·인덱스가 일부 존재하면 자동 전환을 중단합니다. 수동 stamp를 반복하지 말고 상태를 확인하세요.
전환 후 `alembic check`로 모델 일치 여부를 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

기존 `.env`는 그대로 사용합니다. `FRONTEND_ORIGIN`은 실제 프런트 주소, `CRON_SECRET`은 정리 작업을 사용할 경우 설정합니다.
운영 PostgreSQL에서 `DEV_LOGIN=0`, `SEED_DEMO=0`이어야 하며 HTTPS 배포에서는 `COOKIE_SECURE=1`로 설정합니다.

## 검증

테스트에는 운영 DATABASE_URL을 사용하지 않습니다. SQLite 임시 DB와 테스트 키를 지정해 실행합니다.
전체 테스트에는 로그인, 플래너, 내보내기, 후기/사진/탈퇴, 요청 제한, 새 DB 마이그레이션 및 기존 계정 보존 전환 검증이 포함됩니다.

프런트 코드는 이번 백엔드 통합에서 변경하지 않았습니다. 내보내기 상태 `EXPIRED` 처리와 저장소 CI의 프런트 명령은 배포 전에 별도로 맞춰야 합니다.
