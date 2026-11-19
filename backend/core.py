"""공통 규약: 응답 봉투, 오류, 페이지, 세션·CSRF, 서명된 위치 ID."""
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session as DB

from db import DATABASE_URL, RateLimit, Session, User, get_db

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 오프셋으로 충분
LOCAL = DATABASE_URL.startswith("sqlite")  # SQLite면 로컬 개발·테스트, 아니면 배포로 본다
if not LOCAL and not os.getenv("SECRET_KEY"):
    raise RuntimeError("배포 환경(SQLite 아닌 DATABASE_URL)에서는 SECRET_KEY 환경변수가 필요합니다.")
# 로컬에서만 미설정 시 프로세스별 임시 키를 쓴다(재시작하면 위치 ID가 무효화).
SECRET_KEY = (os.getenv("SECRET_KEY") or secrets.token_hex(32)).encode()
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"
DEV_LOGIN = os.getenv("DEV_LOGIN") == "1"
if DEV_LOGIN and not LOCAL:
    raise RuntimeError("DEV_LOGIN=1은 로컬(SQLite) 전용입니다. 배포 환경에서는 끄세요.")
if os.getenv("SEED_DEMO") == "1" and not LOCAL:
    raise RuntimeError("SEED_DEMO=1은 로컬(SQLite) 전용입니다. 배포 환경에서는 끄세요.")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET", "")
KAKAO_REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI", "http://localhost:3000/api/v1/auth/callback/kakao")
SESSION_COOKIE = "sid"
SESSION_DAYS = 30

request_id_var: ContextVar[str] = ContextVar("request_id", default="req_unknown")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite는 tz를 버리므로 naive 값은 UTC로 간주한다."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return aware(dt).astimezone(KST).isoformat() if dt else None


# ---- 응답 ----

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: list | None = None, retryable: bool = False):
        self.status, self.code, self.message, self.fields, self.retryable = status, code, message, fields or [], retryable


def field(path: str, reason: str) -> list:
    return [{"path": path, "reason": reason}]


def meta(**extra) -> dict:
    return {"requestId": request_id_var.get(), **extra}


def ok(data, status: int = 200, page: dict | None = None, **extra) -> JSONResponse:
    body = {"data": data}
    if page is not None:
        body["page"] = page
    body["meta"] = meta(**extra)
    return JSONResponse(jsonable_encoder(body), status_code=status)


def error_response(e: ApiError) -> JSONResponse:
    body = {"error": {"code": e.code, "message": e.message, "fields": e.fields, "retryable": e.retryable}, "meta": meta()}
    return JSONResponse(body, status_code=e.status)


# ponytail: offset 커서. 목록이 커지고 정렬 중 삽입이 잦아지면 (정렬값,id) keyset 커서로 바꾼다.
def paginate(items: list, cursor: str | None, limit: int) -> tuple[list, dict]:
    try:
        start = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
    except ValueError:
        start = -1
    if start < 0:
        raise ApiError(400, "INVALID_CURSOR", "잘못된 페이지 정보입니다.", field("cursor", "invalid"))
    end = start + limit
    has_next = end < len(items)
    nxt = base64.urlsafe_b64encode(str(end).encode()).decode() if has_next else None
    return items[start:end], {"nextCursor": nxt, "hasNext": has_next}


def defer(background, fn, *args) -> None:
    """Vercel은 응답 뒤 함수가 멈출 수 있어 요청 안에서 바로 실행한다. 그 밖에서는 BackgroundTasks."""
    if os.getenv("VERCEL"):
        fn(*args)
    else:
        background.add_task(fn, *args)


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


# ---- 세션 ----

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_session(request: Request, db: DB) -> Session | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    s = db.get(Session, hash_token(token))
    if not s or aware(s.expires_at) < now() or s.user.status != "ACTIVE":
        return None
    return s


def create_session(db: DB, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Session(id=hash_token(token), user_id=user.id, csrf_token=secrets.token_urlsafe(32),
                   created_at=now(), expires_at=now() + timedelta(days=SESSION_DAYS)))
    db.commit()
    return token


def check_csrf(request: Request, s: Session) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not hmac.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), s.csrf_token.encode()):
        raise ApiError(403, "CSRF_INVALID", "요청을 확인할 수 없습니다. 새로고침 후 다시 시도해 주세요.")


def require_user(request: Request, db: DB = Depends(get_db)) -> User:
    s = current_session(request, db)
    if not s:
        raise ApiError(401, "UNAUTHENTICATED", "로그인이 필요합니다.")
    check_csrf(request, s)
    return s.user


# ---- 위치 ID: 서버가 검색 결과에 서명 → 클라이언트가 임의 좌표를 만들 수 없음 ----

def sign_location(label: str, lat: float, lng: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps([label, lat, lng], ensure_ascii=False).encode()).decode()
    sig = hmac.new(SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"loc_{payload}.{sig}"


def verify_location(location_id: str | None) -> dict | None:
    if not location_id or not location_id.startswith("loc_") or "." not in location_id:
        return None
    payload, sig = location_id[4:].rsplit(".", 1)
    expected = hmac.new(SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    label, lat, lng = json.loads(base64.urlsafe_b64decode(payload.encode()))
    return {"locationId": location_id, "label": label, "lat": lat, "lng": lng}


# ---- 요청 제한 ----
# ponytail: DB 고정 구간 카운터(요청마다 쓰기 1번, 구간 경계에서 최대 2배 통과). 트래픽이 커지면 Redis/Upstash로 옮긴다.

def client_ip(request: Request) -> str:
    # Vercel은 x-forwarded-for 첫 값에 실제 클라이언트 IP를 넣는다.
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def rate_limit(db: DB, key: str, limit: int, window_seconds: int) -> None:
    start = datetime.fromtimestamp(int(now().timestamp()) // window_seconds * window_seconds, timezone.utc)
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(RateLimit).values(key=key[:200], window_start=start, count=1)
    stmt = stmt.on_conflict_do_update(index_elements=["key", "window_start"], set_={"count": RateLimit.count + 1})
    count = db.scalar(stmt.returning(RateLimit.count))
    db.commit()
    if count > limit:
        raise ApiError(429, "RATE_LIMITED", "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.", retryable=True)


def limit_by_ip(name: str, limit: int, window_seconds: int):
    def dependency(request: Request, db: DB = Depends(get_db)) -> None:
        rate_limit(db, f"{name}:{client_ip(request)}", limit, window_seconds)
    return dependency


def limit_by_user(name: str, limit: int, window_seconds: int, method: str, *path_suffixes: str):
    """라우터 전체에 걸고 method+경로 끝이 맞는 요청만 로그인 사용자별로 센다(비로그인은 라우트가 401)."""
    def dependency(request: Request, db: DB = Depends(get_db)) -> None:
        if request.method != method or not request.url.path.endswith(path_suffixes):
            return
        if s := current_session(request, db):
            rate_limit(db, f"{name}:{s.user_id}", limit, window_seconds)
    return dependency
