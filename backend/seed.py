"""로컬 개발용 가상 데이터 (SEED_DEMO=1). 실제 축제·장소 정보가 아니다."""
from datetime import date, datetime, timedelta, timezone

from core import KST, now
from db import Festival, Place, Program


def kst(y, m, d, h, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=KST).astimezone(timezone.utc)  # DB에는 UTC로 저장


def seed(db) -> None:
    if db.get(Festival, "fst_demo_01"):
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
        db.add(Place(id=f"pl_demo_0{i}", name=f"[예시] {cat.lower()} {i}", category=cat, lat=36.745 + dlat,
                     lng=126.298, address="충남 태안군 예시로 1", stay_minutes_estimate=60))
    db.commit()
