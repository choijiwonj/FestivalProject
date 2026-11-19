import os
import re
from datetime import date, datetime

from dotenv import load_dotenv
from sqlalchemy import JSON, Float, ForeignKey, LargeBinary, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import NullPool

load_dotenv()

# 개발 기본값은 SQLite, 배포는 Supabase PostgreSQL URL을 DATABASE_URL로 넣는다.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
# Supabase가 주는 postgres(ql):// URL을 psycopg 3 드라이버로 연결한다.
DATABASE_URL = re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", DATABASE_URL)
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # 시간 컬럼은 시간대 없는 UTC로 저장하므로 Postgres 세션도 UTC로 고정한다.
    # 서버리스 + Supabase 트랜잭션 풀러: 연결은 풀러가 관리하고(NullPool), prepared statement는 끈다.
    engine = create_engine(DATABASE_URL, poolclass=NullPool,
                           connect_args={"options": "-c timezone=UTC", "prepare_threshold": None})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db


# ponytail: JSON 컬럼(Postgres JSON). 검색이 필요해지면 JSONB + 인덱스로 바꾼다.
class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_user_id: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[str] = mapped_column(String(20))
    avatar_url: Mapped[str | None]
    email: Mapped[str | None]
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime]


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 쿠키 토큰의 sha256
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    user: Mapped[User] = relationship(lazy="joined")


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    version: Mapped[int] = mapped_column()
    origin: Mapped[dict | None]
    transport: Mapped[str | None]
    travelers: Mapped[int | None]
    interests: Mapped[list] = mapped_column(default=list)
    style: Mapped[str | None]
    lodging_preference: Mapped[str | None]
    save_origin: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime | None]
    # 저장 시 version 비교 + 증가를 한 UPDATE로 처리 (동시 수정 → StaleDataError → 409)
    __mapper_args__ = {"version_id_col": version}


class Festival(Base):
    __tablename__ = "festivals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str]
    region: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[date]
    end_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    thumbnail_url: Mapped[str | None]
    tags: Mapped[list] = mapped_column(default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[dict | None]  # {label,lat,lng}
    opening_hours: Mapped[str | None]
    admission: Mapped[dict | None]  # {amountKrw,basis}
    booths: Mapped[list] = mapped_column(default=list)
    lineup: Mapped[list] = mapped_column(default=list)
    shuttle: Mapped[dict | None]
    directions: Mapped[dict | None]
    editorial: Mapped[bool] = mapped_column(default=False)
    source_url: Mapped[str | None]
    last_verified_at: Mapped[datetime | None]


class Program(Base):
    __tablename__ = "festival_programs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    festival_id: Mapped[str] = mapped_column(ForeignKey("festivals.id"))
    name: Mapped[str]
    start_at: Mapped[datetime | None]  # UTC로 저장
    end_at: Mapped[datetime | None]
    reservation_required: Mapped[bool | None]
    cost_krw: Mapped[int | None]
    source_url: Mapped[str | None]
    last_verified_at: Mapped[datetime | None]


class Place(Base):
    __tablename__ = "places"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str]
    category: Mapped[str] = mapped_column(String(20))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    address: Mapped[str]
    thumbnail_url: Mapped[str | None]
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(default=list)
    opening_hours: Mapped[dict | None]
    closed_dates: Mapped[list] = mapped_column(default=list)
    price: Mapped[dict | None]
    stay_minutes_estimate: Mapped[int | None]
    source_url: Mapped[str | None]
    last_verified_at: Mapped[datetime | None]


class TripRequest(Base):
    __tablename__ = "trip_requests"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    input: Mapped[dict]  # 검증·정규화된 입력
    idempotency_key: Mapped[str] = mapped_column(String(36))
    body_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str | None] = mapped_column(ForeignKey("trip_requests.id"))
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict | None]
    error: Mapped[dict | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class TripPlan(Base):
    __tablename__ = "trip_plans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(ForeignKey("trip_requests.id"))
    parent_plan_id: Mapped[str | None]
    version: Mapped[int] = mapped_column(default=1)
    title: Mapped[str]
    status: Mapped[str] = mapped_column(String(20))
    input_snapshot: Mapped[dict]
    days: Mapped[list]
    budget: Mapped[dict]
    warnings: Mapped[list]
    conflicts: Mapped[list]
    is_favorite: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]


class PlanExport(Base):
    __tablename__ = "plan_exports"
    __table_args__ = (UniqueConstraint("plan_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("trip_plans.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    plan_version: Mapped[int]
    snapshot: Mapped[dict]
    format: Mapped[str] = mapped_column(String(8))
    selected_dates: Mapped[list]
    status: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str | None]
    content_type: Mapped[str | None] = mapped_column(String(40))
    filename: Mapped[str | None]
    # ponytail: 파일을 DB 행에 보관(서버리스에 로컬 디스크 없음). 파일이 커지거나 많아지면 객체 저장소로 옮긴다.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)  # 상태 조회 때는 읽지 않는다
    error: Mapped[dict | None]
    idempotency_key: Mapped[str] = mapped_column(String(36))
    body_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class RateLimit(Base):
    """고정 구간 요청 수. 오래된 구간은 maintenance.cleanup이 지운다."""
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)  # "이름:IP" 또는 "이름:사용자ID"
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
