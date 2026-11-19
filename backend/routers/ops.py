import logging
import os
import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session as DB

from core import LOCAL, ApiError, ok
from db import get_db
from maintenance import cleanup

router = APIRouter()
log = logging.getLogger(__name__)


def require_cron(request: Request) -> None:
    # Vercel Cron은 CRON_SECRET이 있으면 Authorization: Bearer <값>을 보낸다.
    secret = os.getenv("CRON_SECRET", "")
    if not secret:
        if not LOCAL:
            log.error("CRON_SECRET is not set; /ops/cleanup is disabled")
        raise ApiError(404, "NOT_FOUND", "찾을 수 없습니다.")
    if not secrets.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {secret}".encode()):
        raise ApiError(401, "UNAUTHENTICATED", "인증이 필요합니다.")


@router.api_route("/ops/cleanup", methods=["GET", "POST"], dependencies=[Depends(require_cron)])
def run_cleanup(db: DB = Depends(get_db)):
    return ok(cleanup(db))
