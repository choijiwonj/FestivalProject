"""Trip plan PDF/PNG export jobs (F15/S18)."""
import hashlib
import uuid
from datetime import date, timedelta
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from core import ApiError, aware, defer, iso, new_id, now, ok, require_user
from db import Job, PlanExport, TripPlan, User, get_db
from exporter import run_export
from routers.trips import plan_out

router = APIRouter()


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["PDF", "PNG"]
    version: int = Field(ge=1, strict=True)
    dates: list[str] | None = None


def normalize_dates(plan: TripPlan, requested: list[str] | None) -> list[str]:
    available = [day["date"] for day in plan.days]
    if requested is None:
        return available
    if not requested:
        raise ApiError(422, "EXPORT_DATES_REQUIRED", "내보낼 날짜를 하나 이상 선택해 주세요.")
    if len(requested) != len(set(requested)):
        raise ApiError(422, "EXPORT_DATES_DUPLICATED", "내보낼 날짜를 중복해서 선택할 수 없습니다.")
    try:
        for value in requested:
            date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ApiError(422, "EXPORT_DATE_INVALID", "날짜는 YYYY-MM-DD 형식이어야 합니다.") from None
    if unknown := set(requested) - set(available):
        raise ApiError(422, "EXPORT_DATE_NOT_IN_PLAN", f"계획에 없는 날짜입니다: {sorted(unknown)[0]}")
    return [value for value in available if value in requested]


def export_out(record: PlanExport) -> dict:
    succeeded = record.status == "SUCCEEDED"
    return {"id": record.id, "planId": record.plan_id, "jobId": record.job_id,
            "planVersion": record.plan_version, "format": record.format, "dates": record.selected_dates,
            "status": record.status,
            "downloadUrl": f"/api/v1/exports/{record.id}/download" if succeeded else None,
            "expiresAt": None, "contentType": record.content_type if succeeded else None,
            "filename": record.filename if succeeded else None, "error": record.error,
            "createdAt": iso(record.created_at), "updatedAt": iso(record.updated_at)}


@router.post("/trip-plans/{plan_id}/exports", status_code=202)
def create_export(plan_id: str, body: ExportInput, background: BackgroundTasks,
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                  user: User = Depends(require_user), db: DB = Depends(get_db)):
    plan = db.get(TripPlan, plan_id)
    if plan is None or plan.user_id != user.id:
        raise ApiError(404, "PLAN_NOT_FOUND", "여행 계획을 찾을 수 없습니다.")
    if plan.status == "INFEASIBLE":
        raise ApiError(409, "PLAN_NOT_EXPORTABLE", "조건 충돌을 해결한 뒤 내보내 주세요.")
    if body.version != plan.version:
        raise ApiError(409, "VERSION_CONFLICT", "계획이 변경되었습니다. 다시 조회해 주세요.")
    try:
        raw_key = str(uuid.UUID(idempotency_key or ""))
    except ValueError:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key(UUID) 헤더가 필요합니다.")
    dates = normalize_dates(plan, body.dates)
    canonical = f"{body.format}:{body.version}:{','.join(dates)}"
    digest = hashlib.sha256(canonical.encode()).hexdigest()

    def replay():
        old = db.scalar(select(PlanExport).where(PlanExport.plan_id == plan.id,
                                                 PlanExport.idempotency_key == raw_key))
        if old is None:
            return None
        if old.body_hash != digest:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 키로 다른 내용이 전송되었습니다.")
        return ok({"exportId": old.id, "jobId": old.job_id,
                   "statusUrl": f"/api/v1/exports/{old.id}"}, status=202)

    if response := replay():
        return response
    export_id, job_id, timestamp = new_id("exp"), new_id("job"), now()
    job = Job(id=job_id, user_id=user.id, request_id=None, type="EXPORT", status="QUEUED",
              stage="EXPORTING", created_at=timestamp, updated_at=timestamp)
    record = PlanExport(id=export_id, user_id=user.id, plan_id=plan.id, job_id=job.id,
                        plan_version=plan.version, snapshot=plan_out(plan), format=body.format,
                        selected_dates=dates, status="QUEUED", storage_key=None, content_type=None,
                        filename=None, error=None, idempotency_key=raw_key, body_hash=digest,
                        created_at=timestamp, updated_at=timestamp)
    db.add_all([job, record])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        response = replay()
        if response is None:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "요청이 다른 요청과 충돌했습니다. 다시 시도해 주세요.", retryable=True)
        return response
    defer(background, run_export, record.id)
    return ok({"exportId": record.id, "jobId": job.id,
               "statusUrl": f"/api/v1/exports/{record.id}"}, status=202)


@router.get("/exports/{export_id}")
def get_export(export_id: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    record = db.get(PlanExport, export_id)
    if record is None or record.user_id != user.id:
        raise ApiError(404, "EXPORT_NOT_FOUND", "내보내기 작업을 찾을 수 없습니다.")
    # 재시작으로 멈춘 작업은 읽을 때 실패로 정리한다(trips.get_job과 같은 기준)
    if record.status in ("QUEUED", "RUNNING") and aware(record.updated_at) < now() - timedelta(minutes=10):
        job = db.get(Job, record.job_id)
        record.status = job.status = "FAILED"
        record.error = job.error = {"code": "EXPORT_TIMEOUT", "retryable": True,
                                    "message": "파일을 만드는 데 너무 오래 걸렸어요. 다시 시도해 주세요."}
        record.updated_at = job.updated_at = now()
        db.commit()
    extra = {} if record.status in ("SUCCEEDED", "FAILED") else {"pollAfterSeconds": 3}
    return ok(export_out(record), **extra)


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, user: User = Depends(require_user), db: DB = Depends(get_db)):
    record = db.get(PlanExport, export_id)
    if record is None or record.user_id != user.id:
        raise ApiError(404, "EXPORT_NOT_FOUND", "내보내기 작업을 찾을 수 없습니다.")
    if record.status != "SUCCEEDED":
        raise ApiError(409, "EXPORT_NOT_READY", "파일 생성이 아직 완료되지 않았습니다.")
    if record.content is None:
        raise ApiError(404, "EXPORT_FILE_NOT_FOUND", "내보내기 파일을 찾을 수 없습니다.")
    # 파일명에 한글이 있어 RFC 5987 형식으로 보낸다
    return Response(content=record.content, media_type=record.content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.filename)}"})
