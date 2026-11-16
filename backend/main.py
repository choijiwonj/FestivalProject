import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from core import ApiError, error_response, new_id, request_id_var
from db import Base, SessionLocal, engine
from routers import auth, catalog, me, trips


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
for r in (auth, catalog, trips, me):
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
