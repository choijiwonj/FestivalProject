import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from core import LOCAL, ApiError, error_response, limit_by_ip, limit_by_user, new_id, request_id_var
from db import Base, SessionLocal, engine
from routers import auth, catalog, exports, me, ops, posts, trips, uploads


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ponytail: create_all로 테이블 생성. 운영 DB 스키마가 생기면 Alembic 마이그레이션으로 바꾼다.
    Base.metadata.create_all(engine)
    if os.getenv("SEED_DEMO") == "1":
        from seed import seed
        with SessionLocal() as db:
            seed(db)
    yield


app = FastAPI(title="축제잇길 API", version="0.3", lifespan=lifespan)

v1 = APIRouter(prefix="/api/v1")
# 로컬(SQLite)·테스트는 계획 생성을 수십 번 하므로 생성 한도만 크게 둔다.
TRIP_LIMIT_PER_HOUR = 1000 if LOCAL else 10
v1.include_router(auth.router)
v1.include_router(catalog.router, dependencies=[
    Depends(limit_by_ip("catalog", 120, 60)),
    Depends(limit_by_user("locations", 30, 60, "GET", "/locations/search"))])
v1.include_router(trips.router, dependencies=[
    Depends(limit_by_user("trips", TRIP_LIMIT_PER_HOUR, 3600, "POST", "/trip-requests", "/regenerations"))])
v1.include_router(posts.router, dependencies=[Depends(limit_by_user("posts", 30, 3600, "POST", "/posts"))])
v1.include_router(uploads.router, dependencies=[Depends(limit_by_user("uploads", 30, 3600, "POST", "/uploads"))])
for r in (exports, me, ops):
    v1.include_router(r.router)
app.include_router(v1)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id_var.set(new_id("req"))
    resp = await call_next(request)
    # Q06: 개인 응답 공유 캐시 금지. ponytail: 공개 조회도 일단 no-store, 캐시가 필요해지면 공개 경로만 연다.
    resp.headers.setdefault("Cache-Control", "private, no-store")
    return resp


@app.exception_handler(ApiError)
async def api_error(_: Request, e: ApiError):
    return error_response(e)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, e: RequestValidationError):
    errs = e.errors()
    fields = [{"path": ".".join(str(x) for x in err["loc"][1:]), "reason": err["type"]} for err in errs]
    if any(err["type"] == "extra_forbidden" for err in errs):
        return error_response(ApiError(422, "UNKNOWN_FIELD", "알 수 없는 항목이 있습니다.", fields))
    return error_response(ApiError(400, "INVALID_REQUEST", "입력 형식을 확인해 주세요.", fields))


@app.exception_handler(HTTPException)
async def http_error(_: Request, e: HTTPException):
    if e.status_code == 404:
        return error_response(ApiError(404, "NOT_FOUND", "찾을 수 없습니다."))
    return JSONResponse({"error": {"code": "HTTP_ERROR", "message": str(e.detail), "fields": [], "retryable": False}},
                        status_code=e.status_code)
