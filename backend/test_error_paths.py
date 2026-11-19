import json
import os
import tempfile

for key, value in {"DATABASE_URL": f"sqlite:///{tempfile.mkdtemp()}/test.db", "DEV_LOGIN": "1",
                   "SEED_DEMO": "1", "SECRET_KEY": "test-secret"}.items():
    os.environ.setdefault(key, value)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from routers import auth, catalog  # noqa: E402


@pytest.fixture
def anon():
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_location_search_requires_login(anon):
    assert anon.get("/api/v1/locations/search", params={"q": "대전역"}).status_code == 401


@pytest.mark.parametrize(("status_code", "retryable"), [(401, False), (403, False), (500, True)])
def test_location_search_key_error_is_not_retryable(anon, monkeypatch, status_code, retryable):
    anon.get("/api/v1/auth/login/dev", follow_redirects=False)
    monkeypatch.setattr(catalog, "KAKAO_REST_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "get", lambda url, **k: httpx.Response(status_code, request=httpx.Request("GET", url)))
    error = anon.get("/api/v1/locations/search", params={"q": "대전역"}).json()["error"]
    assert error["code"] == "PROVIDER_UNAVAILABLE" and error["retryable"] is retryable


def test_kakao_profile_without_id_fails_login(anon, monkeypatch):
    request = httpx.Request("GET", "https://example.test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, request=request, json={"access_token": "t"}))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, request=request, json={}))
    anon.cookies.set(auth.STATE_COOKIE, json.dumps(["s", "/"]))
    r = anon.get("/api/v1/auth/callback/kakao", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/me?login=failed"
