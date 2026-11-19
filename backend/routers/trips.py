"""여행 조건 검증·생성·작업 조회·계획 조회 (G04·G05)."""
import hashlib
import logging
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from core import ApiError, aware, defer, iso, new_id, now, ok, paginate, require_user
from db import Festival, Job, PlanExport, TripPlan, TripRequest, User, get_db
from planner import TripRequestInput, run_generation, validate_trip

router = APIRouter()
log = logging.getLogger(__name__)


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    isFavorite: bool = Field(strict=True)


class RegenerationInput(TripRequestInput):
    baseVersion: int = Field(ge=1, strict=True)


@router.post("/trip-plans/{plan_id}/regenerations", status_code=202)
def regenerate(plan_id: str, body: RegenerationInput, background: BackgroundTasks,
               idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
               user: User = Depends(require_user), db: DB = Depends(get_db)):
    parent = db.get(TripPlan, plan_id)
    if parent is None or parent.user_id != user.id:
        raise ApiError(404, "PLAN_NOT_FOUND", "여행 계획을 찾을 수 없습니다.")
    try:
        raw_key = str(uuid.UUID(idempotency_key or ""))
    except ValueError:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key(UUID) 헤더가 필요합니다.")
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"/trip-plans/{plan_id}/regenerations:{raw_key}"))
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()

    def replay():
        old = db.scalar(select(TripRequest).where(TripRequest.user_id == user.id, TripRequest.idempotency_key == key))
        if old is None:
            return None
        if old.body_hash != digest:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 키로 다른 내용이 전송되었습니다.")
        return accepted(old, db.scalar(select(Job).where(Job.request_id == old.id)))

    if response := replay():
        return response
    if parent.version != body.baseVersion:
        raise ApiError(409, "VERSION_CONFLICT", "계획이 변경되었습니다. 다시 조회해 주세요.")
    conditions = TripRequestInput.model_validate(body.model_dump(exclude={"baseVersion"}))
    normalized = validate_trip(db, conditions)["normalizedInput"]
    normalized["_parentPlanId"] = parent.id
    req = TripRequest(id=new_id("trq"), user_id=user.id, input=normalized,
                      idempotency_key=key, body_hash=digest, created_at=now())
    job = Job(id=new_id("job"), user_id=user.id, request_id=req.id, type="PLAN_GENERATION",
              status="QUEUED", stage="VALIDATING", created_at=now(), updated_at=now())
    try:
        db.add(req)
        db.flush()  # Persist the parent before inserting its referencing job.
        db.add(job)
        db.commit()
    except IntegrityError as exc:
        original = exc.orig
        diag = getattr(original, "diag", None)
        log.warning("[trip DB error] code: %s table: %s constraint: %s", getattr(original, "sqlstate", None),
                    getattr(diag, "table_name", None), getattr(diag, "constraint_name", None))
        db.rollback()
        response = replay()
        if response is None:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "요청이 다른 요청과 충돌했습니다. 다시 시도해 주세요.", retryable=True)
        return response
    defer(background, run_generation, job.id)
    return accepted(req, job)



def job_out(j: Job) -> dict:
    return {"id": j.id, "type": j.type, "status": j.status, "stage": j.stage, "result": j.result, "error": j.error,
            "createdAt": iso(j.created_at), "updatedAt": iso(j.updated_at)}


def plan_out(p: TripPlan) -> dict:
    return {"id": p.id, "requestId": p.request_id, "parentPlanId": p.parent_plan_id, "version": p.version,
            "title": p.title, "status": p.status, "inputSnapshot": p.input_snapshot, "days": p.days,
            "budget": p.budget, "warnings": p.warnings, "conflicts": p.conflicts, "isFavorite": p.is_favorite,
            "createdAt": iso(p.created_at)}


def accepted(req: TripRequest, job: Job):
    return ok({"requestId": req.id, "jobId": job.id, "statusUrl": f"/api/v1/jobs/{job.id}"}, status=202)


@router.post("/trip-requests/validate")
def validate(body: TripRequestInput, user: User = Depends(require_user), db: DB = Depends(get_db)):
    return ok(validate_trip(db, body))


@router.post("/trip-requests", status_code=202)
def create_trip(body: TripRequestInput, background: BackgroundTasks,
                idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                user: User = Depends(require_user), db: DB = Depends(get_db)):
    try:
        key = str(uuid.UUID(idempotency_key or ""))
    except ValueError:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key(UUID) 헤더가 필요합니다.")
    body_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()

    def replay():
        old = db.scalar(select(TripRequest).where(TripRequest.user_id == user.id, TripRequest.idempotency_key == key))
        if not old:
            return None
        if old.body_hash != body_hash:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 키로 다른 내용이 전송되었습니다.")
        return accepted(old, db.scalar(select(Job).where(Job.request_id == old.id)))

    # ponytail: 키 24시간 만료 정리는 아직 없음(무기한 보관). 정리 배치를 붙일 때 created_at 기준으로 지운다.
    if resp := replay():
        return resp
    result = validate_trip(db, body)  # 생성 직전 최신 상태로 재검증
    req = TripRequest(id=new_id("trq"), user_id=user.id, input=result["normalizedInput"], idempotency_key=key,
                      body_hash=body_hash, created_at=now())
    job = Job(id=new_id("job"), user_id=user.id, request_id=req.id, type="PLAN_GENERATION", status="QUEUED",
              stage="VALIDATING", created_at=now(), updated_at=now())
    try:
        db.add(req)
        db.flush()  # Persist the parent before inserting its referencing job.
        db.add(job)
        db.commit()
    except IntegrityError as exc:
        original = exc.orig
        diag = getattr(original, "diag", None)
        log.warning("[trip DB error] code: %s table: %s constraint: %s", getattr(original, "sqlstate", None),
                    getattr(diag, "table_name", None), getattr(diag, "constraint_name", None))
        db.rollback()
        if resp := replay():
            return resp
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "요청이 다른 요청과 충돌했습니다. 다시 시도해 주세요.", retryable=True)
    defer(background, run_generation, job.id)
    return accepted(req, job)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    j = db.get(Job, job_id)
    if not j or j.user_id != user.id:  # 타인 리소스도 404
        raise ApiError(404, "JOB_NOT_FOUND", "작업을 찾을 수 없습니다.")
    # 재시작으로 멈춘 작업은 읽을 때 실패로 정리한다
    if j.status in ("QUEUED", "RUNNING") and aware(j.updated_at) < now() - timedelta(minutes=10):
        j.status, j.updated_at = "FAILED", now()
        j.error = {"code": "GENERATION_TIMEOUT", "message": "계획을 만드는 데 너무 오래 걸렸어요. 다시 시도해 주세요.",
                   "retryable": True}
        db.commit()
    extra = {} if j.status in ("SUCCEEDED", "FAILED") else {"pollAfterSeconds": 3}
    req = db.get(TripRequest, j.request_id) if j.request_id else None
    return ok({**job_out(j), "festivalId": req.input.get("primaryFestivalId") if req else None}, **extra)


@router.get("/trip-plans/{plan_id}")
def get_plan(plan_id: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    p = db.get(TripPlan, plan_id)
    if not p or p.user_id != user.id:
        raise ApiError(404, "PLAN_NOT_FOUND", "여행 계획을 찾을 수 없습니다.")
    return ok(plan_out(p))


def own_plan(db: DB, user: User, plan_id: str) -> TripPlan:
    p = db.get(TripPlan, plan_id)
    if not p or p.user_id != user.id:
        raise ApiError(404, "PLAN_NOT_FOUND", "여행 계획을 찾을 수 없습니다.")
    return p


@router.patch("/trip-plans/{plan_id}")
def patch_plan(plan_id: str, body: PlanPatch, user: User = Depends(require_user), db: DB = Depends(get_db)):
    p = own_plan(db, user, plan_id)
    p.is_favorite = body.isFavorite
    db.commit()
    return ok(plan_out(p))


@router.delete("/trip-plans/{plan_id}", status_code=204)
def delete_plan(plan_id: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    p = own_plan(db, user, plan_id)
    # 내보내기 파일과 그 작업도 함께 지운다. 생성 요청·작업 기록은 남긴다.
    # 재생성으로 만든 자식 계획은 parent_plan_id(FK 아닌 문자열)만 가리키므로 그대로 둔다.
    job_ids = list(db.scalars(select(PlanExport.job_id).where(PlanExport.plan_id == p.id)))
    db.execute(delete(PlanExport).where(PlanExport.plan_id == p.id))
    db.execute(delete(Job).where(Job.id.in_(job_ids)))
    # 생성 작업 결과가 지운 계획을 가리키지 않게 한다(JSON 조회는 DB마다 달라 파이썬에서 거른다).
    for job in db.scalars(select(Job).where(Job.user_id == user.id, Job.type == "PLAN_GENERATION")):
        if (job.result or {}).get("planId") == p.id:
            job.result = None
    db.delete(p)
    db.commit()


@router.get("/me/trip-plans")
def my_plans(cursor: str | None = None, limit: int = Query(20, ge=1, le=50), isFavorite: bool | None = None,
             user: User = Depends(require_user), db: DB = Depends(get_db)):
    stmt = select(TripPlan).where(TripPlan.user_id == user.id)
    if isFavorite is not None:
        stmt = stmt.where(TripPlan.is_favorite == isFavorite)
    rows = list(db.scalars(stmt.order_by(TripPlan.created_at.desc(), TripPlan.id.desc())))
    chunk, page = paginate(rows, cursor, limit)
    data = []
    for p in chunk:
        fest = db.get(Festival, p.input_snapshot["primaryFestivalId"])
        data.append({"id": p.id, "title": p.title, "startDate": p.input_snapshot["startDate"],
                     "endDate": p.input_snapshot["endDate"], "primaryFestivalName": fest.name if fest else None,
                     "status": p.status, "isFavorite": p.is_favorite, "createdAt": iso(p.created_at),
                     "parentPlanId": p.parent_plan_id})
    return ok(data, page=page)


@router.get("/me/trip-requests")
def my_requests(cursor: str | None = None, limit: int = Query(20, ge=1, le=50),
                status: Literal["ACTIVE", "FAILED"] | None = None,
                user: User = Depends(require_user), db: DB = Depends(get_db)):
    stmt = (select(TripRequest, Job).join(Job, Job.request_id == TripRequest.id)
            .where(TripRequest.user_id == user.id)
            .order_by(TripRequest.created_at.desc(), TripRequest.id.desc()))
    if status == "ACTIVE":
        stmt = stmt.where(Job.status.in_(["QUEUED", "RUNNING"]))
    elif status == "FAILED":
        stmt = stmt.where(Job.status == "FAILED")
    rows = list(db.execute(stmt))
    chunk, page = paginate(rows, cursor, limit)
    data = []
    for req, job in chunk:
        fest = db.get(Festival, req.input["primaryFestivalId"])
        data.append({"id": req.id, "primaryFestival": {"id": req.input["primaryFestivalId"],
                                                       "name": fest.name if fest else None},
                     "startDate": req.input["startDate"], "endDate": req.input["endDate"], "jobId": job.id,
                     "jobStatus": job.status, "stage": job.stage, "planId": (job.result or {}).get("planId"),
                     "error": job.error, "createdAt": iso(req.created_at)})
    return ok(data, page=page)
