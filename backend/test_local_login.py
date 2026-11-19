"""Local auth tests always use an isolated SQLite test database."""
import os
import tempfile
import uuid
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/local-login.db")
os.environ.setdefault("SECRET_KEY", "local-login-test-secret")
os.environ.setdefault("COOKIE_SECURE", "0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from main import app
from db import SessionLocal, User, LocalCredential, Session
from core import ApiError, hash_token
from password_auth import create_local_account, hash_password, verify_password, login_throttle, LoginThrottle

PASSWORD = "test-password-2026!"

@pytest.fixture
def account():
    login_throttle.attempts.clear()
    with TestClient(app, base_url="https://testserver") as client:
        username = "local_" + uuid.uuid4().hex[:10]
        with SessionLocal() as db:
            user = create_local_account(db, username, PASSWORD, "테스트여행자")
            user_id = user.id
        yield client, username, user_id

def test_scrypt_salt_and_password_verification():
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a != b and PASSWORD not in a
    assert verify_password(PASSWORD, a)
    assert not verify_password("wrong-password", a)
    assert not verify_password(PASSWORD, "broken-hash")
    assert not verify_password(PASSWORD, a.replace("32768", "1073741824"))

def test_real_login_session_csrf_and_logout(account):
    client, username, user_id = account
    response = client.post("/api/v1/auth/login", json={"username": username.upper(), "password": PASSWORD}, headers={"Origin":"https://testserver"})
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["user"]["id"] == user_id and body["csrfToken"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert PASSWORD not in response.text and "scrypt" not in response.text
    assert client.get("/api/v1/auth/session").json()["data"] == body
    assert client.patch("/api/v1/me", json={"nickname":"업데이트"}).status_code == 403
    assert client.patch("/api/v1/me", json={"nickname":"업데이트"}, headers={"X-CSRF-Token":body["csrfToken"]}).status_code == 200
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token":body["csrfToken"]}).status_code == 204
    assert client.get("/api/v1/auth/session").json()["data"]["user"] is None

def test_unknown_id_and_bad_password_share_error(account):
    client, username, _ = account
    responses = [client.post("/api/v1/auth/login", json={"username":u,"password":p}) for u,p in [(username,"wrong-password"),("unknown-local-id",PASSWORD)]]
    assert all(r.status_code == 401 for r in responses)
    assert responses[0].json()["error"] == responses[1].json()["error"]
    assert "sid" not in client.cookies

def test_login_rotates_old_session(account):
    client, username, _ = account
    body={"username":username,"password":PASSWORD}
    client.post("/api/v1/auth/login", json=body)
    old=client.cookies.get("sid")
    client.post("/api/v1/auth/login", json=body)
    assert client.cookies.get("sid") != old
    with SessionLocal() as db:
        assert db.get(Session, hash_token(old)) is None

def test_inactive_account_cannot_login(account):
    client, username, user_id = account
    with SessionLocal() as db:
        db.get(User,user_id).status="DISABLED"
        db.commit()
    assert client.post("/api/v1/auth/login",json={"username":username,"password":PASSWORD}).status_code==401

def test_cross_origin_login_is_rejected(account):
    client, username, _ = account
    response=client.post("/api/v1/auth/login",json={"username":username,"password":PASSWORD},headers={"Origin":"https://untrusted.invalid"})
    assert response.status_code==403
    assert response.json()["error"]["code"]=="ORIGIN_INVALID"

def test_account_creation_never_overwrites_existing_password(account):
    _, username, user_id = account
    with SessionLocal() as db:
        old_hash=db.get(LocalCredential,user_id).password_hash
        with pytest.raises(ValueError,match="이미 존재"):
            create_local_account(db,username,"another-password","다른여행자")
        assert db.get(LocalCredential,user_id).password_hash==old_hash

def test_throttle_has_bounded_attempt_count():
    throttle=LoginThrottle(limit=2)
    throttle.check("test-client");throttle.check("test-client")
    with pytest.raises(ApiError) as exc: throttle.check("test-client")
    assert exc.value.status==429

def test_login_input_rejects_extra_fields_and_long_password(account):
    client, username, _ = account
    for payload in [{"username":username,"password":"x"*129},{"username":username,"password":PASSWORD,"admin":True}]:
        response=client.post("/api/v1/auth/login",json=payload)
        assert response.status_code in (400,422)
        assert PASSWORD not in response.text
