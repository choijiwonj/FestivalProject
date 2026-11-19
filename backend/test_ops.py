import os
import tempfile
from datetime import timedelta

for key, value in {"DATABASE_URL": f"sqlite:///{tempfile.mkdtemp()}/test.db", "DEV_LOGIN": "1",
                   "SEED_DEMO": "1", "SECRET_KEY": "test-secret"}.items():
    os.environ.setdefault(key, value)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import new_id, now  # noqa: E402
from db import Job, PlanExport, Session, SessionLocal, TripRequest, User  # noqa: E402
from main import app  # noqa: E402

CLEANUP = "/api/v1/ops/cleanup"


@pytest.fixture
def c():
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_login_rate_limited_per_ip(c):
    ip = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}  # 첫 값이 클라이언트
    codes = [c.get("/api/v1/auth/login/none", headers=ip).status_code for _ in range(21)]
    assert codes[:20] == [404] * 20
    r = c.get("/api/v1/auth/login/none", headers=ip)
    assert r.status_code == 429
    assert r.json()["error"] == {"code": "RATE_LIMITED", "message": r.json()["error"]["message"],
                                 "fields": [], "retryable": True}
    # 다른 IP는 따로 센다
    assert c.get("/api/v1/auth/login/none", headers={"x-forwarded-for": "198.51.100.7"}).status_code == 404


def test_cleanup_requires_cron_secret(c, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert c.get(CLEANUP).status_code == 404
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    assert c.get(CLEANUP).status_code == 401
    assert c.post(CLEANUP, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get(CLEANUP, headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_cleanup_keeps_plans_and_referenced_rows(c, monkeypatch):
    old = now() - timedelta(days=40)
    with SessionLocal() as db:
        user = User(id=new_id("usr"), provider="test", provider_user_id=new_id("p"), nickname="정리", created_at=old)
        orphan = TripRequest(id=new_id("trq"), user_id=user.id, input={}, idempotency_key=new_id("k")[:36],
                             body_hash="x", created_at=old)
        failed = Job(id=new_id("job"), user_id=user.id, request_id=orphan.id, type="PLAN_GENERATION",
                     status="FAILED", stage="VALIDATING", created_at=old, updated_at=old)
        export_job = Job(id=new_id("job"), user_id=user.id, request_id=None, type="EXPORT", status="FAILED",
                         stage="DONE", created_at=old, updated_at=old)
        export = PlanExport(id=new_id("exp"), user_id=user.id, plan_id="pln_none", job_id=export_job.id,
                            plan_version=1, snapshot={}, format="PDF", selected_dates=[], status="SUCCEEDED",
                            content=b"pdf", idempotency_key="k", body_hash="x", created_at=old, updated_at=old)
        db.add_all([user, orphan, failed, export_job, export,
                    Session(id=new_id("ses"), user_id=user.id, csrf_token="t", created_at=old, expires_at=old)])
        db.commit()
        ids = orphan.id, failed.id, export_job.id, export.id
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    counts = c.post(CLEANUP, headers={"Authorization": "Bearer s3cret"}).json()["data"]
    assert min(counts["sessions"], counts["failedJobs"], counts["tripRequests"], counts["exportFiles"]) >= 1
    with SessionLocal() as db:
        assert db.get(TripRequest, ids[0]) is None and db.get(Job, ids[1]) is None
        assert db.get(Job, ids[2]) is not None  # 내보내기가 가리키는 작업은 남김
        record = db.get(PlanExport, ids[3])
        assert record is not None and record.content is None  # 행은 남기고 파일만 비움
