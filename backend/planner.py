"""여행 입력 검증(F08)과 일정 생성(F10·F11).

현재 생성 단계: AI 장소 추천·경로 공급자 미연결.
→ 주 축제·필수 프로그램·필수 장소만으로 계획을 만들고, 미확인 항목은 미확인으로 남긴다(B05).
"""
import logging
from datetime import date, timedelta
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DB

from core import ApiError, field, iso, new_id, now, verify_location
from db import Festival, Job, Place, Program, SessionLocal, TripPlan, TripRequest
from routers.catalog import WINDOW_DAYS, Interest

log = logging.getLogger(__name__)
MAX_TRIP_DAYS = 2  # MVP 제안값(정의서 9장 미확정). 확정되면 여기만 바꾼다.


class OriginIn(BaseModel):
    locationId: str
    label: str | None = None
    lat: float | None = None
    lng: float | None = None


class TripRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primaryFestivalId: str
    additionalFestivalIds: list[str] = []
    startDate: date
    endDate: date
    origin: OriginIn
    transport: Literal["CAR", "PUBLIC_TRANSIT"]
    travelers: int = Field(1, ge=1, le=20)
    budgetKrw: int | None = Field(None, ge=0)
    interests: list[Interest] = []
    style: Literal["RELAXED", "PACKED"] = "RELAXED"
    lodgingPreference: Literal["HOTEL", "PENSION", "GUESTHOUSE", "ANY", "SELF_ARRANGED"] | None = None
    requiredPlaceIds: list[str] = Field([], max_length=10)
    requiredProgramIds: list[str] = Field([], max_length=10)
    additionalRequests: str | None = Field(None, max_length=1000)


DATE_MESSAGES = {
    "DATE_RANGE_INVALID": ("endDate", "종료일은 시작일과 같거나 늦어야 합니다."),
    "DATE_OUT_OF_FESTIVAL_WINDOW": ("startDate", "축제 기간 전후 2일 안에서 날짜를 골라 주세요."),
    "DATE_NO_FESTIVAL_OVERLAP": ("startDate", "여행에 축제 개최일을 하루 이상 포함해 주세요."),
    "TRIP_DURATION_EXCEEDED": ("endDate", f"여행 기간은 최대 {MAX_TRIP_DAYS}일입니다."),
}


def check_dates(start: date, end: date, f_start: date, f_end: date) -> str | None:
    """B02. 위반 코드 또는 None."""
    window = timedelta(days=WINDOW_DAYS)
    if start > end:
        return "DATE_RANGE_INVALID"
    if start < f_start - window or end > f_end + window:
        return "DATE_OUT_OF_FESTIVAL_WINDOW"
    if max(start, f_start) > min(end, f_end):
        return "DATE_NO_FESTIVAL_OVERLAP"
    if (end - start).days + 1 > MAX_TRIP_DAYS:
        return "TRIP_DURATION_EXCEEDED"
    return None


def reject(code: str, message: str, path: str, reason: str = "invalid"):
    raise ApiError(422, code, message, field(path, reason))


def validate_trip(db: DB, inp: TripRequestInput) -> dict:
    """ValidationResult. 위반 시 422 ApiError."""
    if inp.additionalFestivalIds:
        reject("FEATURE_NOT_ENABLED", "여러 축제 연결은 아직 지원하지 않습니다.", "additionalFestivalIds", "not_enabled")
    fest = db.get(Festival, inp.primaryFestivalId)
    if not fest:
        reject("FESTIVAL_NOT_FOUND", "축제를 찾을 수 없습니다.", "primaryFestivalId", "not_found")
    if fest.status == "CANCELLED":
        reject("FESTIVAL_UNAVAILABLE", "취소된 축제입니다. 축제 정보를 확인해 주세요.", "primaryFestivalId", "cancelled")
    if code := check_dates(inp.startDate, inp.endDate, fest.start_date, fest.end_date):
        path, msg = DATE_MESSAGES[code]
        reject(code, msg, path, code.lower())

    origin = verify_location(inp.origin.locationId)
    if not origin:
        reject("LOCATION_NOT_FOUND", "출발지를 검색 결과에서 다시 선택해 주세요.", "origin", "not_found")

    trip_dates = {(inp.startDate + timedelta(days=i)).isoformat() for i in range((inp.endDate - inp.startDate).days + 1)}
    program_ids = list(dict.fromkeys(inp.requiredProgramIds))
    for pid in program_ids:
        p = db.get(Program, pid)
        if not p or p.festival_id != fest.id:
            reject("PROGRAM_NOT_FOUND", "선택한 축제의 프로그램이 아닙니다.", "requiredProgramIds", "not_found")
        if p.start_at is None or p.end_at is None:
            reject("PROGRAM_TIME_UNCONFIRMED", f"'{p.name}'은 시간이 확정되지 않아 고정 일정으로 고를 수 없습니다.",
                   "requiredProgramIds", "time_unknown")
        if iso(p.start_at)[:10] not in trip_dates:
            reject("PROGRAM_OUTSIDE_TRIP", f"'{p.name}'은 여행 날짜에 열리지 않습니다.", "requiredProgramIds", "outside_trip")
    place_ids = list(dict.fromkeys(inp.requiredPlaceIds))
    for pid in place_ids:
        if not db.get(Place, pid):
            reject("PLACE_NOT_FOUND", "필수 장소를 찾을 수 없습니다.", "requiredPlaceIds", "not_found")

    days = len(trip_dates)
    nights = days - 1
    lodging = None if nights == 0 else (inp.lodgingPreference or "ANY")  # 당일 여행은 숙박 선호 무시
    warnings = []
    if inp.budgetKrw is None:
        warnings.append({"code": "BUDGET_NOT_SET", "message": "예산을 입력하지 않아 예산 판단을 하지 않습니다."})
    normalized = inp.model_copy(update={
        "origin": OriginIn(**origin), "lodgingPreference": lodging, "requiredProgramIds": program_ids,
        "requiredPlaceIds": place_ids, "interests": list(dict.fromkeys(inp.interests)),
    })
    return {"valid": True, "normalizedInput": normalized.model_dump(mode="json"), "days": days, "nights": nights,
            "warnings": warnings}


# ---- 생성 ----

def build_plan(db: DB, req: TripRequest) -> TripPlan:
    inp = req.input
    fest = db.get(Festival, inp["primaryFestivalId"])
    start, end = date.fromisoformat(inp["startDate"]), date.fromisoformat(inp["endDate"])
    programs = sorted((db.get(Program, pid) for pid in inp["requiredProgramIds"]), key=lambda p: iso(p.start_at))
    places = [db.get(Place, pid) for pid in inp["requiredPlaceIds"]]
    admission = (fest.admission or {}).get("amountKrw")

    days, n = [], 0

    def stop(**kw) -> dict:
        nonlocal n
        n += 1
        base = {"id": f"stop_{n:02d}", "placeId": None, "festivalId": None, "programId": None, "startAt": None,
                "endAt": None, "required": True, "recommendationReason": None, "costKrw": None,
                "costStatus": "UNKNOWN", "sourceUrl": None}
        return {**base, **kw}

    d = start
    while d <= end:
        stops = []
        if fest.start_date <= d <= fest.end_date:
            stops.append(stop(type="FESTIVAL", festivalId=fest.id, name=fest.name, recommendationReason="선택한 주 축제",
                              costKrw=admission, costStatus="VERIFIED" if admission is not None else "UNKNOWN",
                              sourceUrl=fest.source_url))
        for p in programs:
            if iso(p.start_at)[:10] == d.isoformat():
                stops.append(stop(type="PROGRAM", festivalId=fest.id, programId=p.id, name=p.name,
                                  startAt=iso(p.start_at), endAt=iso(p.end_at), recommendationReason="필수 프로그램",
                                  costKrw=p.cost_krw, costStatus="VERIFIED" if p.cost_krw is not None else "UNKNOWN",
                                  sourceUrl=p.source_url))
        days.append({"date": d.isoformat(), "stops": stops, "legs": []})
        d += timedelta(days=1)
    # 일정 엔진 연결 전: 필수 장소는 날짜·시간 미배정으로 첫날에 둔다(조용히 빼지 않음).
    for pl in places:
        days[0]["stops"].append(stop(type="PLACE", placeId=pl.id, name=pl.name, recommendationReason="필수 장소",
                                     sourceUrl=pl.source_url))

    conflicts = find_conflicts(programs)

    warnings = [
        {"code": "ROUTE_UNAVAILABLE", "message": "경로 서비스가 아직 연결되지 않아 이동시간을 계산하지 않았습니다.", "stopIds": []},
        {"code": "NO_CANDIDATES", "message": "AI 장소 추천이 아직 연결되지 않아 축제·필수 일정만 담았습니다.", "stopIds": []},
    ]
    if places:
        warnings.append({"code": "SCHEDULE_PENDING", "message": "필수 장소의 방문 시간은 아직 정하지 않았습니다.",
                         "stopIds": [s["id"] for s in days[0]["stops"] if s["type"] == "PLACE"]})

    budget = make_budget(inp, days)
    if budget["unknownItemCount"]:
        warnings.append({"code": "COST_UNKNOWN", "message": "확인하지 못한 비용이 있습니다.", "stopIds": []})
    nights = len(days) - 1
    return TripPlan(
        id=new_id("plan"), user_id=req.user_id, request_id=req.id, version=1,
        title=f"{fest.name} " + ("당일 여행" if nights == 0 else f"{nights}박 {len(days)}일"),
        # 경로·운영시간 검증 전이므로 READY가 될 수 없다.
        status="INFEASIBLE" if conflicts else "NEEDS_REVIEW",
        input_snapshot=inp, days=days, budget=budget, warnings=warnings, conflicts=conflicts, created_at=now())


def find_conflicts(programs: list) -> list:
    """시작순 정렬된 프로그램의 모든 쌍을 비교한다(최대 10개라 O(n²)로 충분)."""
    return [{"code": "REQUIRED_CONSTRAINT_CONFLICT", "message": f"'{a.name}'과 '{b.name}'의 시간이 겹칩니다.",
             "relatedPlaceIds": [], "relatedProgramIds": [a.id, b.id], "suggestedChanges": []}
            for a, b in combinations(programs, 2) if b.start_at < a.end_at]


def make_budget(inp: dict, days: list) -> dict:
    items = [{"category": "TRANSPORT", "amountKrw": None, "basis": "GROUP", "quantity": 1, "status": "UNKNOWN"}]
    if len(days) > 1 and inp["lodgingPreference"] != "SELF_ARRANGED":
        items.append({"category": "LODGING", "amountKrw": None, "basis": "ROOM", "quantity": len(days) - 1,
                      "status": "UNKNOWN"})
    for day in days:
        for s in day["stops"]:
            if s["type"] in ("FESTIVAL", "PROGRAM"):
                known = s["costStatus"] == "VERIFIED"
                items.append({"category": "ADMISSION", "amountKrw": s["costKrw"] * inp["travelers"] if known else None,
                              "basis": "PERSON", "quantity": inp["travelers"], "status": s["costStatus"]})
    known_total = sum(i["amountKrw"] for i in items if i["amountKrw"] is not None)
    unknown = sum(1 for i in items if i["amountKrw"] is None)
    limit = inp["budgetKrw"]
    if limit is None:
        status = "NOT_SET"
    elif known_total > limit:
        status = "OVER"
    elif unknown:
        status = "UNKNOWN"
    else:
        status = "WITHIN"
    return {"currency": "KRW", "scope": "GROUP_TOTAL", "limitKrw": limit, "knownTotalKrw": known_total,
            "estimatedTotalKrw": None, "unknownItemCount": unknown, "status": status, "items": items,
            "lodgingIncluded": inp["lodgingPreference"] != "SELF_ARRANGED"}


# ponytail: FastAPI BackgroundTasks로 같은 프로세스에서 실행. Vercel 서버리스는 응답 후 프로세스가 멈출 수 있으니
# 배포 전에 Upstash Workflow 단계 실행으로 옮긴다(기술 스택 정리표 5장).
def run_generation(job_id: str) -> None:
    with SessionLocal() as db:
        try:
            job = db.get(Job, job_id)
            job.status, job.stage, job.updated_at = "RUNNING", "SCHEDULING", now()
            db.commit()
            plan = build_plan(db, db.get(TripRequest, job.request_id))
            db.add(plan)
            job.status, job.stage, job.result, job.updated_at = "SUCCEEDED", "SAVING", {"planId": plan.id}, now()
            db.commit()
        except Exception:
            log.exception("plan generation failed: %s", job_id)
            try:
                db.rollback()
                job = db.get(Job, job_id)
                job.status, job.updated_at = "FAILED", now()
                job.error = {"code": "GENERATION_FAILED", "message": "계획을 만들지 못했습니다. 다시 시도해 주세요.",
                             "retryable": True}
                db.commit()
            except Exception:
                log.exception("could not mark job FAILED: %s", job_id)
