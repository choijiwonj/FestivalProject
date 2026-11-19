import json
import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from core import (COOKIE_SECURE, DEV_LOGIN, KAKAO_CLIENT_SECRET, KAKAO_REDIRECT_URI, KAKAO_REST_API_KEY,
                  SESSION_COOKIE, SESSION_DAYS, ApiError, check_csrf, create_session, current_session, new_id, now, ok)
from db import User, UserSettings, get_db

router = APIRouter()
PROVIDERS = {"kakao"} | ({"dev"} if DEV_LOGIN else set())
STATE_COOKIE = "oauth_state"
log = logging.getLogger(__name__)


def user_out(u: User) -> dict:
    return {"id": u.id, "nickname": u.nickname, "avatarUrl": u.avatar_url}


def safe_return_to(path: str | None) -> str:
    # 내부 경로만 허용 (//evil.com, /\evil.com 같은 외부 이동 차단)
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/"


def upsert_user(db: DB, provider: str, provider_user_id: str, nickname: str | None, email: str | None) -> User:
    query = select(User).where(User.provider == provider, User.provider_user_id == provider_user_id)
    user = db.scalar(query)
    if user:
        return user
    user = User(id=new_id("usr"), provider=provider, provider_user_id=provider_user_id,
                nickname=(nickname or "여행자").strip()[:20] or "여행자", email=email, created_at=now())
    db.add(user)
    db.add(UserSettings(user_id=user.id, interests=[]))
    try:
        db.commit()
    except IntegrityError:
        # 동시에 처음 로그인하면 다른 요청이 먼저 만든 사용자를 쓴다.
        db.rollback()
        return db.scalars(query).one()
    return user


def login_redirect(db: DB, user: User, return_to: str) -> RedirectResponse:
    resp = RedirectResponse(return_to, status_code=302)
    resp.set_cookie(SESSION_COOKIE, create_session(db, user), max_age=SESSION_DAYS * 86400,
                    httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


def login_failed() -> RedirectResponse:
    resp = RedirectResponse("/me?login=failed", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.get("/auth/session")
def session(request: Request, db: DB = Depends(get_db)):
    s = current_session(request, db)
    return ok({"user": user_out(s.user) if s else None, "csrfToken": s.csrf_token if s else None})


@router.get("/auth/login/{provider}")
def login(provider: str, returnTo: str | None = None, db: DB = Depends(get_db)):
    if provider not in PROVIDERS:
        raise ApiError(404, "PROVIDER_NOT_SUPPORTED", "지원하지 않는 로그인 방식입니다.")
    return_to = safe_return_to(returnTo)
    if provider == "dev":  # DEV_LOGIN=1 일 때만 존재. 카카오 키 없이 로컬 개발용.
        return login_redirect(db, upsert_user(db, "dev", "dev-user", "개발자", None), return_to)
    if not KAKAO_REST_API_KEY:
        raise ApiError(503, "PROVIDER_UNAVAILABLE", "로그인 서비스가 설정되지 않았습니다.", retryable=True)
    # ponytail: state만 사용. 카카오 PKCE 지원 확인 후 code_challenge 추가.
    state = secrets.token_urlsafe(24)
    url = "https://kauth.kakao.com/oauth/authorize?" + urlencode(
        {"client_id": KAKAO_REST_API_KEY, "redirect_uri": KAKAO_REDIRECT_URI, "response_type": "code", "state": state})
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(STATE_COOKIE, json.dumps([state, return_to]), max_age=600,
                    httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
    return resp


@router.get("/auth/callback/{provider}")
def callback(provider: str, request: Request, code: str | None = None, state: str | None = None,
             db: DB = Depends(get_db)):
    try:
        saved_state, return_to = json.loads(request.cookies.get(STATE_COOKIE, ""))
    except (ValueError, TypeError):
        log.warning("Kakao login failed: missing or invalid state cookie")
        return login_failed()
    if provider != "kakao" or not code or not state or not secrets.compare_digest(state, saved_state):
        return login_failed()
    try:
        data = {"grant_type": "authorization_code", "client_id": KAKAO_REST_API_KEY,
                "redirect_uri": KAKAO_REDIRECT_URI, "code": code}
        if KAKAO_CLIENT_SECRET:
            data["client_secret"] = KAKAO_CLIENT_SECRET
        tok = httpx.post("https://kauth.kakao.com/oauth/token", data=data, timeout=10)
        tok.raise_for_status()
        me = httpx.get("https://kapi.kakao.com/v2/user/me", timeout=10,
                       headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
        me.raise_for_status()
        info = me.json()
        kakao_id = str(info["id"])
        account = info.get("kakao_account") or {}
    except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
        response = getattr(exc, "response", None)
        log.warning("Kakao login failed: %s status=%s", type(exc).__name__,
                    response.status_code if response is not None else None)
        return login_failed()
    user = upsert_user(db, "kakao", kakao_id, (account.get("profile") or {}).get("nickname"),
                       account.get("email"))
    return login_redirect(db, user, safe_return_to(return_to))


@router.post("/auth/logout", status_code=204)
def logout(request: Request, db: DB = Depends(get_db)):
    s = current_session(request, db)
    if s:
        check_csrf(request, s)
        db.delete(s)
        db.commit()
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
