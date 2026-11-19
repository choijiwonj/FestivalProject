"""실제 축제·프로그램·장소 데이터를 JSON 파일에서 DB로 넣는다 (id 기준 upsert, 한 트랜잭션).

사용: python importer.py data/sample_import.json [--dry-run]
DATABASE_URL로 대상 DB를 고른다. 배포 DB(SQLite 아님)면 core 규칙대로 SECRET_KEY도 있어야 한다.
파일 형식: {"festivals": [...], "programs": [...], "places": [...]} — 각 항목의 키는 db.py 컬럼 이름(snake_case).
시간(start_at 등)은 한국시간 ISO 문자열("2027-05-14T13:00:00+09:00", 시간대 없으면 한국시간으로 본다)이고 UTC로 저장한다.
"""
import json
import sys
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import inspect
from sqlalchemy.orm import Session as DB

from core import KST, LOCAL
from db import Base, Festival, Place, Program, SessionLocal, engine
from routers.catalog import Category, Region

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # scheduling.WEEKDAYS


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Venue(Strict):
    label: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Admission(Strict):
    amountKrw: int = Field(ge=0)
    basis: str


class Period(Strict):
    open: str = Field(pattern=r"^\d{2}:\d{2}$")
    close: str = Field(pattern=r"^\d{2}:\d{2}$")


def to_utc(v: datetime | None) -> datetime | None:
    """DB에는 시간대 없는 UTC로 저장한다(db.py·core.aware 규약)."""
    if v is None:
        return None
    return (v if v.tzinfo else v.replace(tzinfo=KST)).astimezone(timezone.utc).replace(tzinfo=None)


UtcTime = Annotated[datetime | None, AfterValidator(to_utc)]


class Record(Strict):
    id: str = Field(min_length=1, max_length=40)
    source_url: str | None = None
    last_verified_at: UtcTime = None


class FestivalIn(Record):
    name: str = Field(min_length=1)
    region: Region
    start_date: date
    end_date: date
    status: str = Field("SCHEDULED", max_length=20)
    thumbnail_url: str | None = None
    tags: list[str] = []
    description: str = ""
    venue: Venue | None = None
    opening_hours: str | None = None
    admission: Admission | None = None
    booths: list = []
    lineup: list = []
    shuttle: dict | None = None
    directions: dict | None = None
    editorial: bool = False

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date가 end_date보다 늦습니다.")
        return self


class ProgramIn(Record):
    festival_id: str
    name: str = Field(min_length=1)
    start_at: UtcTime = None
    end_at: UtcTime = None
    reservation_required: bool | None = None
    cost_krw: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def check_times(self):
        if self.start_at and self.end_at and self.start_at > self.end_at:
            raise ValueError("start_at이 end_at보다 늦습니다.")
        return self


class PlaceIn(Record):
    name: str = Field(min_length=1)
    category: Category
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    address: str
    thumbnail_url: str | None = None
    description: str = ""
    tags: list[str] = []
    opening_hours: dict[Weekday, list[Period]] | None = None
    closed_dates: list[date] = []
    price: dict | None = None
    stay_minutes_estimate: int | None = Field(None, gt=0)


SECTIONS = {"festivals": (FestivalIn, Festival), "programs": (ProgramIn, Program), "places": (PlaceIn, Place)}


class ImportFailed(Exception):
    pass


def validate(data) -> dict[str, list[BaseModel]]:
    if not isinstance(data, dict) or set(data) - set(SECTIONS):
        raise ImportFailed(f"최상위는 {sorted(SECTIONS)} 키만 가진 객체여야 합니다.")
    out, errors = {}, []
    for key, (schema, _) in SECTIONS.items():
        items = data.get(key, [])
        if not isinstance(items, list):
            raise ImportFailed(f"{key}는 배열이어야 합니다.")
        out[key] = []
        for i, item in enumerate(items):
            try:
                out[key].append(schema.model_validate(item))
            except ValidationError as e:
                for err in e.errors():
                    loc = ".".join(str(x) for x in err["loc"])
                    errors.append(f"{key}[{i}]{'.' + loc if loc else ''}: {err['msg']}")
        ids = [r.id for r in out[key]]
        errors += [f"{key}: id 중복 {d}" for d in sorted({x for x in ids if ids.count(x) > 1})]
    if errors:
        raise ImportFailed("검증 실패\n" + "\n".join(errors))
    return out


def run_import(db: DB, data, dry_run: bool = False) -> dict:
    """검증 후 upsert. 오류가 하나라도 있으면 아무것도 쓰지 않는다."""
    records = validate(data)
    file_festivals = {f.id for f in records["festivals"]}
    for i, p in enumerate(records["programs"]):
        if p.festival_id not in file_festivals and db.get(Festival, p.festival_id) is None:
            raise ImportFailed(f"검증 실패\nprograms[{i}].festival_id: 없는 축제 {p.festival_id}")
    counts = {}
    try:
        for key, (_, model) in SECTIONS.items():
            created = updated = 0
            for r in records[key]:
                existing = db.get(model, r.id)
                # 기존 행은 파일에 적힌 필드만 덮어쓴다(빠진 필드를 기본값으로 되돌리지 않는다).
                row = r.model_dump(exclude_unset=existing is not None)
                if "closed_dates" in row:
                    row["closed_dates"] = [d.isoformat() for d in row["closed_dates"]]  # 일정 계산이 문자열로 비교
                if existing is None:
                    created += 1
                    db.add(model(**row))
                else:
                    updated += 1
                    for k, v in row.items():
                        setattr(existing, k, v)
            db.flush()  # 축제를 먼저 넣어 프로그램 FK를 맞춘다
            counts[key] = {"created": created, "updated": updated}
        if dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return counts


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 1:
        print("사용: python importer.py 파일.json [--dry-run]", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"파일을 읽을 수 없습니다: {e}", file=sys.stderr)
        return 1
    if LOCAL:
        Base.metadata.create_all(engine)
    elif missing := [m.__tablename__ for _, m in SECTIONS.values() if not inspect(engine).has_table(m.__tablename__)]:
        print(f"테이블이 없습니다({', '.join(missing)}). 먼저 `alembic upgrade head`를 실행해 주세요.", file=sys.stderr)
        return 1
    try:
        with SessionLocal() as db:
            counts = run_import(db, data, dry_run="--dry-run" in argv)
    except ImportFailed as e:
        print(e, file=sys.stderr)
        return 1
    for key, c in counts.items():
        print(f"{key}: 새로 {c['created']}건, 갱신 {c['updated']}건")
    if "--dry-run" in argv:
        print("--dry-run: 저장하지 않았습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
