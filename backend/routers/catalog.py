"""축제·장소·위치 검색 (공개)."""
from datetime import date, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from core import KAKAO_REST_API_KEY, KST, ApiError, distance_m, field, iso, ok, paginate, sign_location
from db import Festival, Place, Program, get_db

router = APIRouter()

Region = Literal["CHEONAN", "GONGJU", "BORYEONG", "ASAN", "SEOSAN", "NONSAN", "GYERYONG", "DANGJIN", "GEUMSAN",
                 "BUYEO", "SEOCHEON", "CHEONGYANG", "HONGSEONG", "YESAN", "TAEAN"]
REGION_NAMES = {"CHEONAN": "천안", "GONGJU": "공주", "BORYEONG": "보령", "ASAN": "아산", "SEOSAN": "서산",
                "NONSAN": "논산", "GYERYONG": "계룡", "DANGJIN": "당진", "GEUMSAN": "금산", "BUYEO": "부여",
                "SEOCHEON": "서천", "CHEONGYANG": "청양", "HONGSEONG": "홍성", "YESAN": "예산", "TAEAN": "태안"}
Interest = Literal["FOOD", "CAFE", "NATURE", "EXPERIENCE", "CULTURE", "SHOPPING"]
Category = Literal["ATTRACTION", "RESTAURANT", "CAFE", "SHOP", "EXPERIENCE", "LODGING"]
WINDOW_DAYS = 2  # B02: 축제 전후 2일


def festival_summary(f: Festival) -> dict:
    return {"id": f.id, "name": f.name, "region": f.region, "startDate": f.start_date, "endDate": f.end_date,
            "thumbnailUrl": f.thumbnail_url, "tags": f.tags, "status": f.status}


def festival_detail(f: Festival) -> dict:
    return {**festival_summary(f), "description": f.description, "venue": f.venue, "openingHours": f.opening_hours,
            "admission": f.admission, "booths": f.booths, "lineup": f.lineup, "shuttle": f.shuttle,
            "directions": f.directions, "sourceUrl": f.source_url, "lastVerifiedAt": iso(f.last_verified_at),
            "dateSelection": {"min": f.start_date - timedelta(days=WINDOW_DAYS),
                              "max": f.end_date + timedelta(days=WINDOW_DAYS)}}


def program_out(p: Program) -> dict:
    return {"id": p.id, "festivalId": p.festival_id, "name": p.name, "startAt": iso(p.start_at),
            "endAt": iso(p.end_at), "reservationRequired": p.reservation_required, "costKrw": p.cost_krw,
            "sourceUrl": p.source_url, "lastVerifiedAt": iso(p.last_verified_at)}


def place_summary(p: Place, distance: float | None = None) -> dict:
    return {"id": p.id, "name": p.name, "category": p.category, "lat": p.lat, "lng": p.lng, "address": p.address,
            "thumbnailUrl": p.thumbnail_url, "distanceM": round(distance) if distance is not None else None}


def place_detail(p: Place) -> dict:
    return {**place_summary(p), "description": p.description, "tags": p.tags, "openingHours": p.opening_hours,
            "closedDates": p.closed_dates, "price": p.price, "stayMinutesEstimate": p.stay_minutes_estimate,
            "sourceUrl": p.source_url, "lastVerifiedAt": iso(p.last_verified_at)}


def today_kst() -> date:
    return datetime.now(KST).date()


# ponytail: 조건 일부는 메모리에서 거른다(충남 축제 수백 건 규모). 데이터가 커지면 SQL 조건·인덱스로 옮긴다.
def search_festivals(db: DB, q: str | None, from_: date | None, to: date | None, region: str | None,
                     interests: list[str]) -> list[Festival]:
    if from_ and to and from_ > to:
        raise ApiError(400, "INVALID_DATE_RANGE", "시작일이 종료일보다 늦습니다.", field("from", "after_to"))
    stmt = select(Festival)
    if region:
        stmt = stmt.where(Festival.region == region)
    if from_ is None and to is None:
        stmt = stmt.where(Festival.end_date >= today_kst())
    if from_:
        stmt = stmt.where(Festival.end_date >= from_)
    if to:
        stmt = stmt.where(Festival.start_date <= to)
    rows = list(db.scalars(stmt))
    q = (q or "").strip()
    if q:
        rows = [f for f in rows if q in f.name or q in REGION_NAMES.get(f.region, "")]
    if interests:
        rows = [f for f in rows if set(f.tags) & set(interests)]
    return rows


@router.get("/festivals/recommendations")
def recommendations(from_: date | None = Query(None, alias="from"), to: date | None = None,
                    region: Region | None = None, interests: list[Interest] = Query([]),
                    limit: int = Query(3, ge=1, le=10), db: DB = Depends(get_db)):
    rows = search_festivals(db, None, from_, to, region, [])

    def kind(f: Festival) -> str:
        if f.editorial:
            return "EDITORIAL"
        return "INTEREST" if set(f.tags) & set(interests) else "SEASONAL"

    order = {"EDITORIAL": 0, "INTEREST": 1, "SEASONAL": 2}
    rows.sort(key=lambda f: (order[kind(f)], f.start_date, f.id))
    reasons = {"EDITORIAL": "운영팀이 추천하는 축제입니다.", "INTEREST": "선택한 관심사와 맞는 축제입니다.",
               "SEASONAL": "곧 열리는 축제입니다."}
    data = [{**festival_summary(f), "recommendationReason": reasons[kind(f)], "recommendationType": kind(f)}
            for f in rows[:limit]]
    return ok(data, generatedAt=iso(datetime.now(KST)))


@router.get("/festivals")
def festivals(q: str | None = Query(None, max_length=100), from_: date | None = Query(None, alias="from"),
              to: date | None = None, region: Region | None = None, interests: list[Interest] = Query([]),
              sort: Literal["START_DATE_ASC", "NAME_ASC"] = "START_DATE_ASC", cursor: str | None = None,
              limit: int = Query(20, ge=1, le=50), db: DB = Depends(get_db)):
    rows = search_festivals(db, q, from_, to, region, interests)
    rows.sort(key=(lambda f: (f.start_date, f.id)) if sort == "START_DATE_ASC" else (lambda f: (f.name, f.id)))
    chunk, page = paginate(rows, cursor, limit)
    return ok([festival_summary(f) for f in chunk], page=page)


def get_festival(db: DB, festival_id: str) -> Festival:
    f = db.get(Festival, festival_id)
    if not f:
        raise ApiError(404, "FESTIVAL_NOT_FOUND", "축제를 찾을 수 없습니다.")
    return f


@router.get("/festivals/{festival_id}")
def festival(festival_id: str, db: DB = Depends(get_db)):
    return ok(festival_detail(get_festival(db, festival_id)))


@router.get("/festivals/{festival_id}/programs")
def programs(festival_id: str, date_: date | None = Query(None, alias="date"), db: DB = Depends(get_db)):
    get_festival(db, festival_id)
    rows = db.scalars(select(Program).where(Program.festival_id == festival_id)).all()
    if date_:
        rows = [p for p in rows if p.start_at and iso(p.start_at)[:10] == date_.isoformat()]
    rows = sorted(rows, key=lambda p: (p.start_at is None, iso(p.start_at) or "", p.id))
    return ok([program_out(p) for p in rows])


@router.get("/places")
def places(q: str | None = Query(None, max_length=100), bbox: str | None = None, lat: float | None = None,
           lng: float | None = None, festivalId: str | None = None, radiusM: int = Query(5000, ge=100, le=20000),
           categories: list[Category] = Query([]), cursor: str | None = None, limit: int = Query(20, ge=1, le=50),
           db: DB = Depends(get_db)):
    modes = [bbox is not None, lat is not None or lng is not None, festivalId is not None]
    if sum(modes) != 1:
        raise ApiError(400, "LOCATION_FILTER_REQUIRED", "bbox, lat·lng, festivalId 중 하나만 지정해 주세요.")
    stmt = select(Place)
    if categories:
        stmt = stmt.where(Place.category.in_(categories))
    rows = list(db.scalars(stmt))

    if bbox is not None:
        try:
            west, south, east, north = (float(v) for v in bbox.split(","))
        except ValueError:
            raise ApiError(400, "INVALID_BBOX", "bbox 형식은 west,south,east,north 입니다.", field("bbox", "format"))
        if west >= east or south >= north or distance_m(south, west, south, east) > 50000:
            raise ApiError(400, "INVALID_BBOX", "조회 범위가 올바르지 않습니다(최대 폭 50km).", field("bbox", "range"))
        hits = [(p, None) for p in rows if west <= p.lng <= east and south <= p.lat <= north]
    else:
        if festivalId is not None:
            venue = get_festival(db, festivalId).venue
            if not venue or venue.get("lat") is None:
                raise ApiError(422, "FESTIVAL_LOCATION_UNKNOWN", "축제 위치가 확인되지 않았습니다.")
            lat, lng = venue["lat"], venue["lng"]
        elif lat is None or lng is None:
            raise ApiError(400, "LOCATION_FILTER_REQUIRED", "lat과 lng를 함께 지정해 주세요.")
        hits = [(p, d) for p in rows if (d := distance_m(lat, lng, p.lat, p.lng)) <= radiusM]

    q = (q or "").strip()
    if q:
        hits = [(p, d) for p, d in hits if q in p.name or q in p.address]
    hits.sort(key=lambda h: (h[1] if h[1] is not None else 0, h[0].id))
    chunk, page = paginate(hits, cursor, limit)
    return ok([place_summary(p, d) for p, d in chunk], page=page)


@router.get("/places/{place_id}")
def place(place_id: str, db: DB = Depends(get_db)):
    p = db.get(Place, place_id)
    if not p:
        raise ApiError(404, "PLACE_NOT_FOUND", "장소를 찾을 수 없습니다.")
    return ok(place_detail(p))


@router.get("/locations/search")
def locations(q: str = Query(min_length=2, max_length=100), limit: int = Query(5, ge=1, le=10)):
    if not KAKAO_REST_API_KEY:
        raise ApiError(503, "PROVIDER_UNAVAILABLE", "위치 검색 서비스가 설정되지 않았습니다.", retryable=True)
    try:
        r = httpx.get("https://dapi.kakao.com/v2/local/search/keyword.json", params={"query": q, "size": limit},
                      headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}, timeout=5)
        r.raise_for_status()
        docs = r.json()["documents"]
    except (httpx.HTTPError, KeyError, ValueError):
        raise ApiError(503, "PROVIDER_UNAVAILABLE", "위치 검색에 실패했습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
    out = []
    for d in docs:
        label, lat, lng = d["place_name"], float(d["y"]), float(d["x"])
        out.append({"locationId": sign_location(label, lat, lng), "label": label, "lat": lat, "lng": lng})
    return ok(out)
