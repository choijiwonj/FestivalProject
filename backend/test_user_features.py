import os
import tempfile
import uuid
from datetime import date, timedelta
from io import BytesIO

for key, value in {"DATABASE_URL": f"sqlite:///{tempfile.mkdtemp()}/test.db", "DEV_LOGIN": "1",
                   "SEED_DEMO": "1", "SECRET_KEY": "test-secret"}.items():
    os.environ.setdefault(key, value)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from core import create_session, now, sign_location  # noqa: E402
from db import Job, PlanExport, Post, Session, SessionLocal, TripPlan, TripRequest, Upload, User, UserSettings  # noqa: E402
from main import app  # noqa: E402
from routers.auth import upsert_user  # noqa: E402

START = date.today() + timedelta(days=10)
TRIP = {"primaryFestivalId": "fst_demo_01", "startDate": START.isoformat(),
        "endDate": (START + timedelta(days=1)).isoformat(),
        "origin": {"locationId": sign_location("예시 출발지", 36.35, 127.38)}, "transport": "CAR"}


@pytest.fixture(scope="module", autouse=True)
def tables():
    with TestClient(app, base_url="https://testserver"):  # lifespan: 테이블 생성 + 시드
        yield


@pytest.fixture(autouse=True)
def no_live_keys(monkeypatch):
    for k in ("ODSAY_API_KEY", "GEMINI_API_KEY", "KAKAO_MOBILITY_REST_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def user_client(name="회원") -> TestClient:
    with SessionLocal() as db:
        sid = create_session(db, upsert_user(db, "dev", uuid.uuid4().hex, name, None))
    c = TestClient(app, base_url="https://testserver", cookies={"sid": sid})
    c.headers["X-CSRF-Token"] = c.get("/api/v1/auth/session").json()["data"]["csrfToken"]
    return c


def new_plan(c: TestClient) -> dict:
    r = c.post("/api/v1/trip-requests", json=TRIP, headers={"Idempotency-Key": str(uuid.uuid4())})
    job = c.get(r.json()["data"]["statusUrl"]).json()["data"]
    assert job["status"] == "SUCCEEDED", job
    return c.get(f"/api/v1/trip-plans/{job['result']['planId']}").json()["data"]


def image(fmt="PNG", **save) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, fmt, **save)
    return buf.getvalue()


def upload(c: TestClient, data: bytes, ctype="image/png"):
    return c.post("/api/v1/uploads", files={"file": ("a.png", data, ctype)})


def test_favorite_and_delete_plan():
    a, other = user_client(), user_client()
    plan = new_plan(a)
    url = f"/api/v1/trip-plans/{plan['id']}"
    r = a.patch(url, json={"isFavorite": True})
    assert r.status_code == 200 and r.json()["data"]["isFavorite"] is True
    assert [p["id"] for p in a.get("/api/v1/me/trip-plans", params={"isFavorite": True}).json()["data"]] == [plan["id"]]
    assert a.patch(url, json={"isFavorite": True, "title": "x"}).json()["error"]["code"] == "UNKNOWN_FIELD"
    assert a.patch(url, json={"isFavorite": "yes"}).status_code == 400
    assert other.patch(url, json={"isFavorite": False}).status_code == 404
    assert other.delete(url).status_code == 404

    child = a.post(f"{url}/regenerations", json={**TRIP, "baseVersion": plan["version"]},
                   headers={"Idempotency-Key": str(uuid.uuid4())})
    child_plan_id = a.get(child.json()["data"]["statusUrl"]).json()["data"]["result"]["planId"]
    with SessionLocal() as db:  # 내보내기 행은 직접 넣는다(파일 생성은 test_api가 검증)
        uid = db.get(TripPlan, plan["id"]).user_id
        db.add(Job(id="job_exp_del", user_id=uid, type="EXPORT",
                   status="SUCCEEDED", stage="EXPORTING", created_at=now(), updated_at=now()))
        db.flush()
        db.add(PlanExport(id="exp_del", user_id=uid, plan_id=plan["id"],
                          job_id="job_exp_del", plan_version=1, snapshot={}, format="PDF", selected_dates=[],
                          status="SUCCEEDED", content=b"x", idempotency_key=str(uuid.uuid4()), body_hash="h",
                          created_at=now(), updated_at=now()))
        db.commit()
        gen_ids = {(j.result or {}).get("planId"): j.id for j in db.scalars(select(Job).where(Job.user_id == uid))}
    assert a.delete(url).status_code == 204
    assert a.get(url).status_code == 404
    with SessionLocal() as db:
        assert db.get(PlanExport, "exp_del") is None and db.get(Job, "job_exp_del") is None
        assert db.get(Job, gen_ids[plan["id"]]).result is None  # 지운 계획을 가리키던 생성 결과는 비운다
        assert db.get(Job, gen_ids[child_plan_id]).result == {"planId": child_plan_id}
    assert a.get(f"/api/v1/jobs/{gen_ids[plan['id']]}").json()["data"]["result"] is None
    assert plan["id"] not in [r["planId"] for r in a.get("/api/v1/me/trip-requests").json()["data"]]
    kept = a.get(f"/api/v1/trip-plans/{child_plan_id}").json()["data"]
    assert kept["parentPlanId"] == plan["id"]  # 자식 계획은 남는다


def test_upload_validation_and_avatar():
    a, other = user_client(), user_client()
    r = upload(a, image(), ctype="image/gif")  # 헤더가 아니라 실제 내용으로 판단
    assert r.status_code == 201, r.text
    up = r.json()["data"]
    assert up["contentType"] == "image/png"
    assert upload(a, b"not an image").json()["error"]["code"] == "UPLOAD_INVALID_TYPE"
    assert upload(a, image("GIF")).status_code == 422
    assert upload(a, b"0" * (5 * 1024 * 1024 + 1)).status_code == 413
    with TestClient(app, base_url="https://testserver") as anon:
        assert upload(anon, image()).status_code == 401
        got = anon.get(up["url"])
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"].startswith("public, max-age=")
    Image.open(BytesIO(got.content)).verify()

    exif = Image.Exif()
    exif[0x010F] = "Camera"  # Make
    jpg = upload(a, image("JPEG", exif=exif), ctype="image/jpeg").json()["data"]
    assert not Image.open(BytesIO(a.get(jpg["url"]).content)).getexif()  # 메타데이터 제거

    me = a.patch("/api/v1/me", json={"avatarMediaId": up["id"]}).json()["data"]
    assert me["avatarUrl"] == up["url"]
    assert other.patch("/api/v1/me", json={"avatarMediaId": up["id"]}).json()["error"]["code"] == "MEDIA_INVALID"
    assert a.patch("/api/v1/me", json={"avatarMediaId": None}).json()["data"]["avatarUrl"] is None


def test_upload_mpo_and_pixel_limit(monkeypatch):
    a = user_client()
    mpo = image("MPO", save_all=True, append_images=[Image.new("RGB", (8, 8))])  # 휴대폰 카메라 JPEG
    r = upload(a, mpo, ctype="image/jpeg")
    assert r.status_code == 201, r.text
    assert r.json()["data"]["contentType"] == "image/jpeg"
    assert Image.open(BytesIO(a.get(r.json()["data"]["url"]).content)).format == "JPEG"
    monkeypatch.setattr("routers.uploads.MAX_PIXELS", 63)
    r = upload(a, image())  # 8x8 = 64화소
    assert r.status_code == 413 and r.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_posts_crud_and_my_posts():
    a, other = user_client("작성자"), user_client()
    cover = upload(a, image()).json()["data"]
    body = "가" * 100
    assert a.post("/api/v1/posts", json={"festivalId": "fst_demo_01", "body": "  "}).status_code == 422
    assert a.post("/api/v1/posts", json={"festivalId": "fst_demo_01", "body": "가" * 2001}).status_code == 422
    assert a.post("/api/v1/posts", json={"festivalId": "없음", "body": "x"}).json()["error"]["code"] == "FESTIVAL_NOT_FOUND"
    assert other.post("/api/v1/posts", json={"festivalId": "fst_demo_01", "body": "x",
                                             "coverUploadId": cover["id"]}).status_code == 422  # 남의 사진
    p = a.post("/api/v1/posts", json={"festivalId": "fst_demo_01", "body": body, "coverUploadId": cover["id"]})
    assert p.status_code == 201, p.text
    post = p.json()["data"]
    assert post["coverUrl"] == cover["url"] and post["author"]["nickname"] == "작성자"
    second = a.post("/api/v1/posts", json={"festivalId": "fst_demo_01", "body": "둘째"}).json()["data"]

    with TestClient(app, base_url="https://testserver") as anon:
        page = anon.get("/api/v1/posts", params={"festivalId": "fst_demo_01", "limit": 1}).json()
        assert page["data"][0]["id"] == second["id"] and page["page"]["hasNext"] is True  # 최신순
        assert anon.get(f"/api/v1/posts/{post['id']}").json()["data"]["body"] == body
        assert anon.get("/api/v1/posts", params={"festivalId": "fst_none"}).json()["data"] == []

    mine = a.get("/api/v1/me/posts").json()
    assert [m["id"] for m in mine["data"]] == [second["id"], post["id"]] and mine["page"]["hasNext"] is False
    assert mine["data"][1] == {"id": post["id"], "excerpt": body[:80], "createdAt": post["createdAt"],
                               "festivalId": "fst_demo_01", "coverUrl": cover["url"]}
    assert other.get("/api/v1/me/posts").json()["data"] == []

    url = f"/api/v1/posts/{post['id']}"
    assert other.patch(url, json={"body": "남의 글"}).status_code == 404
    assert other.delete(url).status_code == 404
    edited = a.patch(url, json={"body": "고친 글", "coverUploadId": None}).json()["data"]
    assert edited["body"] == "고친 글" and edited["coverUrl"] is None
    assert a.patch(url, json={}).json()["error"]["code"] == "EMPTY_UPDATE"
    assert a.delete(url).status_code == 204
    assert a.get(url).status_code == 404


def test_delete_account_removes_only_own_data():
    a, other = user_client(), user_client()
    new_plan(a)
    cover = upload(a, image()).json()["data"]
    a.post("/api/v1/posts", json={"festivalId": "fst_demo_01", "body": "탈퇴할 글", "coverUploadId": cover["id"]})
    a.patch("/api/v1/me", json={"avatarMediaId": cover["id"]})
    other_post = other.post("/api/v1/posts", json={"festivalId": "fst_demo_01", "body": "남는 글"}).json()["data"]
    new_plan(other)
    old_sid = a.cookies["sid"]
    uid = a.get("/api/v1/me").json()["data"]["id"]

    assert a.delete("/api/v1/me", headers={"X-CSRF-Token": "x"}).status_code == 403
    r = a.delete("/api/v1/me")
    assert r.status_code == 204 and 'sid=""' in r.headers["set-cookie"]
    with TestClient(app, base_url="https://testserver", cookies={"sid": old_sid}) as stale:
        assert stale.get("/api/v1/me").status_code == 401
    assert other.get(f"/api/v1/posts/{other_post['id']}").status_code == 200
    assert len(other.get("/api/v1/me/trip-plans").json()["data"]) == 1
    assert other.get(cover["url"]).status_code == 404
    with SessionLocal() as db:
        assert db.get(User, uid) is None
        for model in (Session, UserSettings, Post, PlanExport, Job, TripPlan, TripRequest, Upload):
            assert db.scalar(select(func.count()).select_from(model).where(model.user_id == uid)) == 0, model
