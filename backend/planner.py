"""여행 입력 검증(F08)과 일정 생성(F10·F11)."""
import logging
import os
from datetime import date, timedelta
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from core import ApiError, distance_m, field, iso, new_id, now, verify_location
from db import Festival, Job, Place, Program, SessionLocal, TripPlan, TripRequest
from recommendations import DEFAULT_MODEL, MAX_CANDIDATES, RecommendationResult, recommend_places
from routers.catalog import WINDOW_DAYS, Interest
from routing import get_car_route
from scheduling import schedule_car_trip
from transit import TransitRouteSession

log = logging.getLogger(__name__)
MAX_TRIP_DAYS = 2  # MVP 제안값(정의서 9장 미확정). 확정되면 여기만 바꾼다.
RECOMMENDATION_RADIUS_M = 5000


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

def nearby_recommendation_candidates(db: DB, lat: float, lng: float, required_ids: list[str]) -> list[Place]:
    nearby = []
    for place in db.scalars(select(Place).where(Place.id.not_in(required_ids), Place.category != "LODGING")):
        distance = distance_m(lat, lng, place.lat, place.lng)
        if distance <= RECOMMENDATION_RADIUS_M:
            nearby.append((distance, place))
    nearby.sort(key=lambda item: (item[0], item[1].id))
    return [place for _, place in nearby]


def find_overnight_lodging(db: DB, inp: dict, venue: dict) -> Place | None:
    preference = inp["lodgingPreference"]
    lat, lng = venue.get("lat"), venue.get("lng")
    if preference == "SELF_ARRANGED" or lat is None or lng is None:
        return None

    candidates = []
    for place in db.scalars(select(Place).where(Place.category == "LODGING")):
        if (place.lat is None or place.lng is None or inp["startDate"] in (place.closed_dates or [])
                or (preference not in (None, "ANY") and preference not in (place.tags or []))):
            continue
        distance = distance_m(lat, lng, place.lat, place.lng)
        if distance <= RECOMMENDATION_RADIUS_M:
            candidates.append((place.id not in inp["requiredPlaceIds"], distance, place.id, place))
    return min(candidates)[-1] if candidates else None


def recommend_demo_place(candidates: list[Place]) -> dict:
    return {
        "placeId": candidates[0].id,
        "priority": 1,
        "reason": "필수 장소를 제외한 주변 후보 중 축제장에 가장 가깝습니다.",
    }


def add_initial_route(inp: dict, days: list[dict], venue: dict, warnings: list[dict], api_key: str) -> None:
    if inp["transport"] != "CAR":
        warnings.append({"code": "ROUTE_UNAVAILABLE",
                         "message": "대중교통 경로가 아직 연결되지 않았습니다.", "stopIds": []})
        return

    if not api_key:
        warnings.append({"code": "ROUTE_UNAVAILABLE",
                         "message": "자동차 길찾기가 아직 설정되지 않았습니다.", "stopIds": []})
        return

    first_festival_day = next(
        (day for day in days if any(stop["type"] == "FESTIVAL" for stop in day["stops"])),
        None,
    )
    origin = inp["origin"]
    if first_festival_day is None or any(point.get(key) is None for point in (origin, venue) for key in ("lat", "lng")):
        warnings.append({"code": "ROUTE_UNAVAILABLE",
                         "message": "출발지 또는 축제장 위치를 확인할 수 없습니다.", "stopIds": []})
        return

    festival_stop = next(stop for stop in first_festival_day["stops"] if stop["type"] == "FESTIVAL")
    route = get_car_route(api_key, (origin["lat"], origin["lng"]), (venue["lat"], venue["lng"]))
    if route is None:
        warnings.append({"code": "ROUTE_UNAVAILABLE", "message": "출발지에서 축제장까지 자동차 경로를 찾지 못했습니다.",
                         "stopIds": [festival_stop["id"]]})
        return

    first_festival_day["legs"].append(route.as_leg("ORIGIN", festival_stop["id"]))
    warnings.append({"code": "ROUTE_PARTIAL",
                     "message": "조회 시점 기준 예상 이동시간입니다. 전체 여행 경로는 추가 확인이 필요합니다.",
                     "stopIds": [festival_stop["id"]]})


def build_plan(db: DB, req: TripRequest) -> TripPlan:
    inp = dict(req.input)
    parent_plan_id = inp.pop("_parentPlanId", None)
    fest = db.get(Festival, inp["primaryFestivalId"])
    start, end = date.fromisoformat(inp["startDate"]), date.fromisoformat(inp["endDate"])
    programs = sorted((db.get(Program, pid) for pid in inp["requiredProgramIds"]), key=lambda p: iso(p.start_at))
    places = [db.get(Place, pid) for pid in inp["requiredPlaceIds"]]
    admission = (fest.admission or {}).get("amountKrw")
    venue = fest.venue or {}
    lat, lng = venue.get("lat"), venue.get("lng")
    overnight_car = (end - start).days == 1
    lodging = find_overnight_lodging(db, inp, venue) if overnight_car else None

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
    # 시간표가 계산되기 전에는 첫날 목록에 미배정 장소를 모아 둔다.
    for pl in places:
        if lodging is None or pl.id != lodging.id:
            days[0]["stops"].append(stop(type="PLACE", placeId=pl.id, name=pl.name, recommendationReason="필수 장소",
                                         sourceUrl=pl.source_url))

    conflicts = find_conflicts(programs)

    warnings = []
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key and os.getenv("SEED_DEMO") != "1":
        warnings.append({"code": "NO_CANDIDATES",
                         "message": "AI 장소 추천이 아직 연결되지 않아 축제·필수 일정만 담았습니다.", "stopIds": []})
    elif lat is None or lng is None:
        warnings.append({"code": "FESTIVAL_LOCATION_UNKNOWN",
                         "message": "축제장 좌표를 확인할 수 없어 주변 장소를 추천하지 못했습니다.", "stopIds": []})
    else:
        candidates = nearby_recommendation_candidates(db, lat, lng, inp["requiredPlaceIds"])
        if api_key and (candidates or places):
            model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
            candidates = candidates[:MAX_CANDIDATES - len(places)]
            try:
                result = recommend_places(api_key, model, inp, fest, places + candidates)
            except ApiError as exc:
                if not exc.retryable:
                    raise
                warnings.append({"code": "AI_RECOMMENDATION_FALLBACK",
                                 "message": "AI 추천을 완료하지 못해 가까운 주변 장소로 대체했습니다." if candidates
                                 else "AI 추천을 완료하지 못해 축제와 필수 일정만 구성했습니다.", "stopIds": []})
                result = RecommendationResult.model_validate({
                    "recommendations": [recommend_demo_place(candidates)] if candidates else [],
                    "conflicts": [],
                })
            candidate_by_id = {place.id: place for place in candidates}
            for recommendation in result.recommendations:
                if recommended_place := candidate_by_id.get(recommendation.placeId):
                    days[0]["stops"].append(stop(type="PLACE", placeId=recommended_place.id, name=recommended_place.name,
                                                 required=False, recommendationReason=recommendation.reason,
                                                 sourceUrl=recommended_place.source_url))
            for conflict in result.conflicts:
                warnings.append({"code": "RECOMMENDATION_REVIEW_REQUIRED", "message": conflict.reason,
                                 "stopIds": [s["id"] for s in days[0]["stops"] if s["placeId"] == conflict.placeId]})
            if not result.recommendations and not result.conflicts:
                warnings.append({"code": "NO_CANDIDATES",
                                 "message": "여행 조건에 맞는 추가 추천 장소가 없습니다.", "stopIds": []})
        elif candidates:
            recommendation = recommend_demo_place(candidates)
            place_id, reason = recommendation.get("placeId"), recommendation.get("reason")
            if not isinstance(place_id, str) or not isinstance(reason, str) or not reason.strip():
                raise ValueError("추천 응답에 장소 ID와 추천 이유가 필요합니다.")
            candidate_by_id = {place.id: place for place in candidates}
            recommended_place = candidate_by_id.get(place_id)
            if recommended_place is None:
                raise ValueError("추천 장소 ID가 후보 목록에 없습니다.")
            days[0]["stops"].append(stop(type="PLACE", placeId=recommended_place.id, name=recommended_place.name,
                                         required=False, recommendationReason=reason,
                                         sourceUrl=recommended_place.source_url))
        else:
            warnings.append({"code": "NO_CANDIDATES",
                             "message": "필수 장소를 제외한 축제장 반경 5km 안에 추천 후보가 없습니다.", "stopIds": []})

    route_key = os.getenv("KAKAO_MOBILITY_REST_API_KEY", "").strip()
    first_day = days[0]
    origin = inp["origin"]
    origin_location = (origin.get("lat"), origin.get("lng"))
    lodging_link = None
    if lodging is not None:
        lodging_details = {"type": "LODGING", "placeId": lodging.id, "name": lodging.name, "sourceUrl": lodging.source_url}
        lodging_link = ((lodging.lat, lodging.lng),
                        stop(**lodging_details, recommendationReason="첫날 숙소 도착"),
                        stop(**lodging_details, recommendationReason="둘째 날 숙소 출발"))
    elif overnight_car:
        reason = ("직접 예약한 숙소의 좌표가 필요합니다." if inp["lodgingPreference"] == "SELF_ARRANGED"
                  else "축제장 주변에서 숙박 조건에 맞는 숙소 위치를 확인할 수 없습니다.")
        warnings.append({"code": "LODGING_UNCONFIRMED",
                         "message": f"{reason} 숙소 도착과 다음 날 출발 구간을 확인할 수 없습니다.", "stopIds": []})

    scheduled = False
    if inp['transport'] == 'PUBLIC_TRANSIT':
        warnings.append({'code': 'TRANSIT_TIMETABLE_UNCONFIRMED',
                         'message': '대중교통 일정은 예상 소요시간 기준입니다. 실제 운행 시각·대기시간·막차는 미확인입니다.', 'stopIds': []})
        warnings.append({'code': 'TRANSIT_CONNECTION_ESTIMATED',
                         'message': '장거리 승차 전 준비시간은 15분을 가정합니다. 근거리 도보 추정 구간은 실제 보행 가능 여부를 확인해야 합니다.', 'stopIds': []})
        transit_key = os.getenv('ODSAY_API_KEY', '').strip()
        if not transit_key:
            warnings.append({'code': 'ROUTE_UNAVAILABLE', 'message': 'ODsay 키가 설정되지 않았습니다.', 'stopIds': []})
        elif None not in (*origin_location, lat, lng) and not conflicts and (len(days) == 1 or lodging_link):
            place_by_id = {s['placeId']: db.get(Place, s['placeId']) for s in first_day['stops'] if s['type'] == 'PLACE'}
            transit_session = TransitRouteSession(transit_key)
            try:
                scheduled = schedule_car_trip(days, place_by_id, origin_location, (lat, lng), transit_key,
                                              warnings, conflicts, lodging_link, route_loader=transit_session.schedule_loader)
            except ApiError as exc:
                warnings.append({'code': exc.code, 'message': exc.message, 'stopIds': []})
            for warning in warnings:
                warning['message'] = warning['message'].replace('자동차', '대중교통')
    is_car = inp['transport'] == 'CAR'
    link_overnight = lodging_link is not None and bool(route_key) and None not in origin_location and not conflicts
    if is_car and not link_overnight:
        # 2일차에는 확인되지 않은 숙소 대신 최초 출발지를 표시하지 않는다.
        if len(days) == 1 or not overnight_car or not route_key or any(s["type"] == "FESTIVAL" for s in first_day["stops"]):
            add_initial_route(inp, days[:1] if overnight_car else days, venue, warnings, route_key)
    if is_car and (link_overnight or (len(days) == 1 and first_day["legs"] and not conflicts)):
        place_by_id = {s["placeId"]: db.get(Place, s["placeId"]) for s in first_day["stops"] if s["type"] == "PLACE"}
        scheduled = schedule_car_trip(days, place_by_id, origin_location, (lat, lng), route_key,
                                      warnings, conflicts, lodging_link)
    if scheduled:
        warnings = [w for w in warnings if w["code"] != "ROUTE_PARTIAL"]
    else:
        if lodging_link is not None:
            _, arrival_stop, departure_stop = lodging_link
            first_day["stops"].append(arrival_stop)
            days[1]["stops"].insert(0, departure_stop)
        unscheduled_place_ids = [s["id"] for s in first_day["stops"] if s["type"] == "PLACE"]
        if unscheduled_place_ids:
            warnings.append({"code": "SCHEDULE_PENDING", "message": "장소의 방문 시간은 아직 정하지 않았습니다.",
                             "stopIds": unscheduled_place_ids})

    budget = make_budget(inp, days)
    if budget["unknownItemCount"]:
        warnings.append({"code": "COST_UNKNOWN", "message": "확인하지 못한 비용이 있습니다.", "stopIds": []})
    nights = len(days) - 1
    return TripPlan(
        id=new_id("plan"), user_id=req.user_id, request_id=req.id, version=1, parent_plan_id=parent_plan_id,
        title=f"{fest.name} " + ("당일 여행" if nights == 0 else f"{nights}박 {len(days)}일"),
        # 교통비 등 확인이 남아 있으므로 READY로 표시하지 않는다.
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


# ponytail: core.defer로 실행. 로컬은 BackgroundTasks, Vercel은 요청 안에서 동기 실행이라 생성이
# Vercel 함수 maxDuration 안에 끝나야 한다. 생성이 더 길어지면 큐(Upstash Workflow 등)로 옮긴다.
def run_generation(job_id: str) -> None:
    with SessionLocal() as db:
        try:
            job = db.get(Job, job_id)
            job.status, job.stage, job.updated_at = "RUNNING", "SCHEDULING", now()
            db.commit()

            plan = build_plan(db, db.get(TripRequest, job.request_id))
            db.add(plan)
            # 시간 초과로 이미 실패 처리됐으면 계획을 저장하지 않는다(RUNNING일 때만 완료로 바꾼다).
            saved = db.query(Job).filter(Job.id == job_id, Job.status == "RUNNING").update(
                {"status": "SUCCEEDED", "stage": "SAVING", "result": {"planId": plan.id}, "updated_at": now()})
            if not saved:
                db.rollback()
                return
            db.commit()

        except ApiError as exc:
            db.rollback()
            try:
                job = db.get(Job, job_id)
                job.status, job.updated_at = "FAILED", now()
                job.error = {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
                db.commit()
            except Exception:
                log.exception("could not mark job FAILED: %s", job_id)

        except Exception:
            log.exception("plan generation failed: %s", job_id)
            try:
                db.rollback()
                job = db.get(Job, job_id)
                job.status, job.updated_at = "FAILED", now()
                job.error = {
                    "code": "GENERATION_FAILED",
                    "message": "계획을 만들지 못했습니다. 다시 시도해 주세요.",
                    "retryable": True,
                }
                db.commit()
            except Exception:
                log.exception("could not mark job FAILED: %s", job_id)