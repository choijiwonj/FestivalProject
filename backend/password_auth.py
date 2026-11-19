"""Local credentials; no plaintext passwords are stored or logged."""
import base64
from collections import deque
import hashlib
import hmac
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from sqlalchemy import select, text
from core import ApiError, KAKAO_REDIRECT_URI, new_id, now
from db import LocalCredential, User, UserSettings

# OWASP scrypt configuration: N=2^15, r=8, p=3 (32 MiB).
N, R, P = 32768, 8, 3


def hash_password(password: str) -> str:
    if not 8 <= len(password) <= 128:
        raise ValueError("비밀번호는 8~128자로 입력하세요.")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, encoded_salt, encoded_key = stored.split("$")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (N, R, P) or len(password) > 128:
            return False
        salt = base64.b64decode(encoded_salt, validate=True)
        expected = base64.b64decode(encoded_key, validate=True)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def normalize_username(username: str) -> str:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,49}", username):
        raise ValueError("아이디는 영문·숫자·밑줄·마침표·하이픈으로 3~50자입니다.")
    return username


def create_local_account(db, username: str, password: str, nickname: str = "여행자") -> User:
    username = normalize_username(username)
    if db.scalar(select(User).where(User.provider == "local", User.provider_user_id == username)):
        raise ValueError("이미 존재하는 아이디입니다. 기존 비밀번호는 변경하지 않았습니다.")
    nickname = nickname.strip()
    if not 2 <= len(nickname) <= 20:
        raise ValueError("닉네임은 2~20자로 입력하세요.")
    password_hash = hash_password(password)
    user = User(id=new_id("usr"), provider="local", provider_user_id=username, nickname=nickname, created_at=now())
    try:
        if db.get_bind().dialect.name == 'postgresql':
            # Supabase Data API roles must never be able to read password hashes.
            db.execute(text('ALTER TABLE public.local_credentials ENABLE ROW LEVEL SECURITY'))
            roles = set(db.scalars(text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")))
            for role in ('anon', 'authenticated'):
                if role in roles:
                    db.execute(text(f'REVOKE ALL ON TABLE public.local_credentials FROM {role}'))
        db.add(user)
        db.flush()
        db.add_all([UserSettings(user_id=user.id, interests=[]), LocalCredential(user_id=user.id, password_hash=password_hash, created_at=now())])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return user


def check_login_origin(request):
    # JSON-only input + same-origin check protects the unauthenticated login POST.
    import os
    origin = request.headers.get("origin")
    if origin is None:
        return  # CLI and native clients have no browser Origin.
    configured = os.getenv("FRONTEND_ORIGIN", "").rstrip("/")
    callback = urlsplit(KAKAO_REDIRECT_URI)
    allowed = {str(request.base_url).rstrip("/"), f"{callback.scheme}://{callback.netloc}"}
    if configured:
        allowed.add(configured)
    if origin.rstrip("/") not in allowed:
        raise ApiError(403, "ORIGIN_INVALID", "로그인 화면을 새로고침한 뒤 다시 시도해 주세요.")


class LoginThrottle:
    """Small per-process guard. Shared rate limiting is needed for multi-worker deployments."""
    def __init__(self, limit=10, window=60):
        self.limit, self.window = limit, window
        self.attempts: dict[str, deque] = {}
        self.lock = threading.Lock()

    def check(self, key: str):
        now = time.monotonic()
        with self.lock:
            if len(self.attempts) > 1000:
                self.attempts = {k: v for k, v in self.attempts.items() if v and now - v[-1] < self.window}
            queue = self.attempts.setdefault(key, deque())
            while queue and now - queue[0] >= self.window:
                queue.popleft()
            if len(queue) >= self.limit:
                raise ApiError(429, "RATE_LIMITED", "로그인 시도가 많아요. 잠시 후 다시 시도해 주세요.", retryable=True)
            queue.append(now)


login_throttle = LoginThrottle()
