import os
import tempfile
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

os.environ.update(DATABASE_URL=f"sqlite:///{tempfile.mkdtemp()}/test.db", DEV_LOGIN="1", SEED_DEMO="1",
                  SECRET_KEY="test-secret")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import create_session, sign_location  # noqa: E402
from db import SessionLocal  # noqa: E402
from main import app  # noqa: E402
import planner  # noqa: E402
from planner import check_dates, find_conflicts  # noqa: E402
from routers.auth import upsert_user  # noqa: E402

START = date.today() + timedelta(days=10)  # seed 축제: START ~ START+2
ORIGIN = {"locationId": sign_location("예시 출발역", 36.35, 127.38)}


def login(c: TestClient) -> TestClient:
    c.get("/api/v1/auth/login/dev", follow_redirects=False)
    c.headers["X-CSRF-Token"] = c.get("/api/v1/auth/session").json()["data"]["csrfToken"]
    return c


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="https://testserver") as c:
        yield login(c)


def trip(**kw):
    return {"primaryFestivalId": "fst_demo_01", "startDate": START.isoformat(),
            "endDate": (START + timedelta(days=1)).isoformat(), "origin": ORIGIN, "transport": "CAR", **kw}


def test_check_dates_b02():
    fs, fe = date(2027, 5, 14), date(2027, 5, 16)
    assert check_dates(date(2027, 5, 13), date(2027, 5, 14), fs, fe) is None
    assert check_dates(date(2027, 5, 12), date(2027, 5, 13), fs, fe) == "DATE_NO_FESTIVAL_OVERLAP"
    assert check_dates(date(2027, 5, 11), date(2027, 5, 12), fs, fe) == "DATE_OUT_OF_FESTIVAL_WINDOW"
    assert check_dates(date(2027, 5, 15), date(2027, 5, 14), fs, fe) == "DATE_RANGE_INVALID"
    assert check_dates(date(2027, 5, 14), date(2027, 5, 16), fs, fe) == "TRIP_DURATION_EXCEEDED"


def test_conflicts_all_pairs():
    def p(id_, s, e):
        return SimpleNamespace(id=id_, name=id_, start_at=datetime(2027, 5, 14, s), end_at=datetime(2027, 5, 14, e))
    pairs = [c["relatedProgramIds"] for c in find_conflicts([p("A", 13, 18), p("B", 14, 15), p("C", 16, 17)])]
    assert pairs == [["A", "B"], ["A", "C"]]  # 인접하지 않은 A–C도 잡는다


def test_generation_failure_marks_job_failed(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("boom")
    monkeypatch.setattr(planner, "build_plan", boom)
    r = client.post("/api/v1/trip-requests", json=trip(budgetKrw=1), headers={"Idempotency-Key": str(uuid.uuid4())})
    job = client.get(f"/api/v1/jobs/{r.json()['data']['jobId']}").json()["data"]
    assert job["status"] == "FAILED" and job["error"]["code"] == "GENERATION_FAILED", job


def test_public_and_auth_basics(client):
    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.get("/api/v1/me").status_code == 401
        assert anon.get("/api/v1/festivals").json()["data"][0]["id"] == "fst_demo_01"
    r = client.get("/api/v1/festivals", params={"q": "없는축제"})
    assert r.status_code == 200 and r.json()["data"] == [] and r.json()["page"]["hasNext"] is False
    assert r.headers["cache-control"] == "private, no-store"
    places = client.get("/api/v1/places", params={"festivalId": "fst_demo_01", "radiusM": 5000}).json()["data"]
    assert [p["id"] for p in places] == ["pl_demo_01", "pl_demo_02", "pl_demo_03"]  # 22km 밖 상점 제외, 거리순
    # CSRF 없는 쓰기는 거절
    assert client.post("/api/v1/trip-requests/validate", json=trip(), headers={"X-CSRF-Token": "x"}).status_code == 403


def test_validate_rules(client):
    r = client.post("/api/v1/trip-requests/validate", json=trip(lodgingPreference="HOTEL"))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (d["days"], d["nights"], d["normalizedInput"]["origin"]["label"]) == (2, 1, "예시 출발역")
    bad = client.post("/api/v1/trip-requests/validate",
                      json=trip(startDate=(START - timedelta(days=2)).isoformat(),
                                endDate=(START - timedelta(days=1)).isoformat()))
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "DATE_NO_FESTIVAL_OVERLAP"  # T02
    forged = client.post("/api/v1/trip-requests/validate", json=trip(origin={"locationId": "loc_fake.abc"}))
    assert forged.json()["error"]["code"] == "LOCATION_NOT_FOUND"
    tbd = client.post("/api/v1/trip-requests/validate", json=trip(requiredProgramIds=["pr_demo_09"]))
    assert tbd.json()["error"]["code"] == "PROGRAM_TIME_UNCONFIRMED"


def test_create_idempotent_and_owner_only(client):
    key = str(uuid.uuid4())
    body = trip(requiredProgramIds=["pr_demo_01", "pr_demo_02"], budgetKrw=300000)
    r1 = client.post("/api/v1/trip-requests", json=body, headers={"Idempotency-Key": key})
    r2 = client.post("/api/v1/trip-requests", json=body, headers={"Idempotency-Key": key})
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["data"]["jobId"] == r2.json()["data"]["jobId"]  # T03
    assert client.post("/api/v1/trip-requests", json=trip(), headers={"Idempotency-Key": key}).status_code == 409

    job = client.get(f"/api/v1/jobs/{r1.json()['data']['jobId']}").json()["data"]
    assert job["status"] == "SUCCEEDED", job
    plan = client.get(f"/api/v1/trip-plans/{job['result']['planId']}").json()["data"]
    assert plan["status"] == "INFEASIBLE" and plan["conflicts"][0]["relatedProgramIds"] == ["pr_demo_01", "pr_demo_02"]  # T05
    assert plan["budget"]["status"] == "UNKNOWN"  # 교통비 미확인 → 0원 아님 (T06)
    assert client.get("/api/v1/me/trip-plans").json()["data"][0]["id"] == plan["id"]

    with SessionLocal() as db:
        other = create_session(db, upsert_user(db, "dev", "other-user", "다른사람", None))
    with TestClient(app, base_url="https://testserver", cookies={"sid": other}) as c2:
        assert c2.get(f"/api/v1/trip-plans/{plan['id']}").status_code == 404  # B09
        assert c2.get("/api/v1/me/trip-plans").json()["data"] == []


def test_settings(client):
    s = client.get("/api/v1/me/settings").json()["data"]
    v = s["version"]
    loc = sign_location("집 근처역", 36.3, 127.4)
    off = client.patch("/api/v1/me/settings", json={"version": v, "travelDefaults": {"originLocationId": loc}})
    assert off.json()["error"]["code"] == "ORIGIN_STORAGE_DISABLED"
    on = client.patch("/api/v1/me/settings", json={"version": v, "privacy": {"saveOrigin": True},
                                                   "travelDefaults": {"originLocationId": loc, "transport": "CAR"}})
    assert on.status_code == 200 and on.json()["data"]["travelDefaults"]["origin"]["label"] == "집 근처역"
    stale = client.patch("/api/v1/me/settings", json={"version": v, "travelDefaults": {"travelers": 2}})
    assert stale.status_code == 409  # T12
    v = on.json()["data"]["version"]
    disabled = client.patch("/api/v1/me/settings", json={"version": v, "privacy": {"saveOrigin": False}}).json()["data"]
    assert disabled["travelDefaults"]["origin"] is None and disabled["travelDefaults"]["transport"] == "CAR"  # T14
    assert client.delete("/api/v1/me/settings/origin").status_code == 400
    assert client.patch("/api/v1/me/settings", json={"version": 1, "x": 1}).json()["error"]["code"] == "UNKNOWN_FIELD"


def test_logout(client):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        assert c.post("/api/v1/auth/logout").status_code == 204
        assert c.get("/api/v1/me/trip-plans").status_code == 401  # T15
