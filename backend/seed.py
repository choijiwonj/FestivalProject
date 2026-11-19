"""로컬 개발용 가상 데이터 (SEED_DEMO=1). 실제 축제·장소 정보가 아니다."""
from datetime import date, datetime, timedelta, timezone

from core import KST, now
from db import Festival, Place, Program
from scheduling import WEEKDAYS

DEMO_PLACE_HOURS = {
    "pl_demo_01": {day: [{"open": "09:00", "close": "13:00"}] for day in WEEKDAYS},
    "pl_demo_02": {day: [{"open": "13:00", "close": "20:00"}] for day in WEEKDAYS},
    "pl_demo_03": {day: [{"open": "10:00", "close": "17:00"}] for day in WEEKDAYS},
}


def kst(y, m, d, h, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=KST).astimezone(timezone.utc)  # DB에는 UTC로 저장


def seed_overnight(db, start: date) -> None:
    if db.get(Place, "pl_demo_hotel") is None:
        db.add(Place(id="pl_demo_hotel", name="[예시] 정원 호텔", category="LODGING", tags=["HOTEL"],
                     lat=36.775, lng=126.298, address="충남 태안군 예시로 2"))
    if db.get(Program, "pr_demo_04") is None:
        day = start + timedelta(days=1)
        db.add(Program(id="pr_demo_04", festival_id="fst_demo_01", name="[예시] 둘째 날 오전 공연",
                       start_at=kst(day.year, day.month, day.day, 10), end_at=kst(day.year, day.month, day.day, 11),
                       cost_krw=0))


def seed(db) -> None:
    festival = db.get(Festival, "fst_demo_01")
    if festival is not None:
        for place_id, hours in DEMO_PLACE_HOURS.items():
            place = db.get(Place, place_id)
            if place is not None and place.opening_hours is None:
                place.opening_hours = hours
        seed_overnight(db, festival.start_date)
        db.commit()
        return
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=2)
    db.add(Festival(
        id="fst_demo_01", name="[예시] 봄빛 정원축제", region="TAEAN", start_date=start, end_date=end,
        tags=["NATURE", "CULTURE"], description="개발용 가상 축제입니다.", editorial=True,
        venue={"label": "예시 정원 광장", "lat": 36.745, "lng": 126.298}, admission={"amountKrw": 0, "basis": "PERSON"},
        last_verified_at=now()))
    db.add(Festival(id="fst_demo_02", name="[예시] 바다 불꽃축제", region="BORYEONG", start_date=end,
                    end_date=end + timedelta(days=1), tags=["FOOD"], venue=None, admission=None))
    for i, (h1, h2) in enumerate([(13, 14), (13, 15), (18, 19)], 1):
        db.add(Program(id=f"pr_demo_0{i}", festival_id="fst_demo_01", name=f"[예시] 공연 {i}",
                       start_at=kst(start.year, start.month, start.day, h1), end_at=kst(start.year, start.month, start.day, h2),
                       cost_krw=None if i == 3 else 0))
    db.add(Program(id="pr_demo_09", festival_id="fst_demo_01", name="[예시] 시간 미정 체험", start_at=None, end_at=None))
    for i, (cat, dlat) in enumerate([("CAFE", 0.005), ("RESTAURANT", 0.01), ("ATTRACTION", 0.02), ("SHOP", 0.2)], 1):
        place_id = f"pl_demo_0{i}"
        db.add(Place(id=place_id, name=f"[예시] {cat.lower()} {i}", category=cat, lat=36.745 + dlat,
                     lng=126.298, address="충남 태안군 예시로 1", stay_minutes_estimate=60,
                     opening_hours=DEMO_PLACE_HOURS.get(place_id)))
    seed_overnight(db, start)
    db.commit()
