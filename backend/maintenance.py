"""정기 정리(Vercel Cron → /api/v1/ops/cleanup). 사용자 계획은 지우지 않는다."""
import logging
from datetime import timedelta

from sqlalchemy import delete, exists, select, update
from sqlalchemy.orm import Session as DB

from core import now
from db import Job, PlanExport, Post, RateLimit, Session, TripPlan, TripRequest, Upload, User

log = logging.getLogger(__name__)

EXPORT_KEEP_DAYS = 7
FAILED_JOB_KEEP_DAYS = 30
EXPORT_EXPIRED_ERROR = {"code": "EXPORT_EXPIRED", "retryable": True,
                        "message": f"보관 기간({EXPORT_KEEP_DAYS}일)이 지나 파일이 삭제됐어요. 다시 내보내 주세요."}


def cleanup(db: DB) -> dict:
    try:
        counts = _cleanup(db)
    except Exception:
        log.exception("cleanup failed")
        raise
    log.info("cleanup done: %s", counts)
    return counts


def _cleanup(db: DB) -> dict:
    t = now()
    counts = {
        "sessions": db.execute(delete(Session).where(Session.expires_at < t)).rowcount,
        # 가장 긴 구간이 1시간이므로 하루 지난 구간은 더 이상 쓰이지 않는다.
        "rateLimits": db.execute(delete(RateLimit).where(RateLimit.window_start < t - timedelta(days=1))).rowcount,
        # 행은 남기고 파일만 비운다 → 다운로드는 410 EXPORT_EXPIRED
        "exportFiles": db.execute(update(PlanExport).where(
            PlanExport.created_at < t - timedelta(days=EXPORT_KEEP_DAYS), PlanExport.content.is_not(None))
            .values(content=None, status="EXPIRED", error=EXPORT_EXPIRED_ERROR)).rowcount,
        # 올리고 하루가 지나도 후기 표지·프로필 사진 어디에도 쓰이지 않은 사진
        "orphanUploads": db.execute(delete(Upload).where(
            Upload.created_at < t - timedelta(days=1), ~exists().where(Post.cover_upload_id == Upload.id),
            ~exists().where(User.avatar_url == "/api/v1/uploads/" + Upload.id))).rowcount,
        # 내보내기가 가리키는 작업은 FK라 남긴다. 성공한 생성 작업은 내 요청 목록이 조인하므로 남긴다.
        "failedJobs": db.execute(delete(Job).where(
            Job.status == "FAILED", Job.updated_at < t - timedelta(days=FAILED_JOB_KEEP_DAYS),
            ~exists().where(PlanExport.job_id == Job.id))).rowcount,
    }
    # 멱등 키 24시간: 작업·계획이 모두 없는 요청만 지운다(위에서 실패 작업을 지운 요청 포함).
    # ponytail: 계획이 있는 요청의 키는 무기한 남는다(키 재사용 시 원래 요청 재전송). 만료가 필요하면 조회 쪽에서 created_at을 본다.
    orphan = select(TripRequest.id).where(
        TripRequest.created_at < t - timedelta(hours=24),
        ~exists().where(Job.request_id == TripRequest.id), ~exists().where(TripPlan.request_id == TripRequest.id))
    counts["tripRequests"] = db.execute(delete(TripRequest).where(TripRequest.id.in_(orphan))).rowcount
    db.commit()
    return counts
