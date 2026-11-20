import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from types import SimpleNamespace

for key, value in {"DATABASE_URL": f"sqlite:///{tempfile.mkdtemp()}/test.db", "DEV_LOGIN": "1",
                   "SEED_DEMO": "1", "SECRET_KEY": "test-secret"}.items():
    os.environ.setdefault(key, value)

import httpx  # noqa: E402
import pytest  # noqa: E402

import planner  # noqa: E402
import recommendations  # noqa: E402
from core import ApiError  # noqa: E402

DAY = date(2027, 5, 14)  # 금요일
HOURS = {"fri": [{"open": "09:00", "close": "18:00"}]}
TRIP = {"startDate": "2027-05-14", "endDate": "2027-05-14", "transport": "CAR", "travelers": 2, "budgetKrw": None,
        "interests": ["NATURE"], "style": "RELAXED", "lodgingPreference": None, "requiredPlaceIds": [],
        "additionalRequests": "개인 요청 문장"}
FESTIVAL = SimpleNamespace(id="fst_1", name="축제", venue={"lat": 36.0, "lng": 126.0}, tags=["NATURE"],
                           description="가" * 500, start_date=DAY, end_date=DAY)


def place(pid, dlat=0.0, tags=(), hours=HOURS, closed=()):
    return SimpleNamespace(id=pid, name=pid, category="ATTRACTION", tags=list(tags), lat=36.0 + dlat, lng=126.0,
                           opening_hours=hours, closed_dates=list(closed), price=None, stay_minutes_estimate=60,
                           last_verified_at=None, source_url=None)


def response(recs, usage=None):
    text = json.dumps({"recommendations": recs, "conflicts": []})
    body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}
    if usage:
        body["usageMetadata"] = usage
    return httpx.Response(200, request=httpx.Request("POST", "https://example.test"), json=body)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    recommendations._cache.clear()
    clock = Clock()
    monkeypatch.setattr(recommendations, "time", clock)
    yield clock
    recommendations._cache.clear()


def call(candidates, programs=()):
    return recommendations.recommend_places("k", "m", TRIP, FESTIVAL, candidates, list(programs))


def test_festival_context_payload_and_logs_without_user_text(monkeypatch, caplog):
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.append(kw) or response(
        [{"placeId": "a", "priority": 1, "reason": "자연"}],
        {"promptTokenCount": 11, "candidatesTokenCount": 7, "totalTokenCount": 18}))
    program = SimpleNamespace(name="공연", start_at=datetime(2027, 5, 14, 4, tzinfo=timezone.utc),
                              end_at=datetime(2027, 5, 14, 5, tzinfo=timezone.utc))
    with caplog.at_level(logging.INFO, logger="recommendations"):
        call([place("a")], [program])
    text = sent[0]["json"]["contents"][0]["parts"][0]["text"]
    ctx = json.loads(text)["festivalContext"]
    assert ctx["tags"] == ["NATURE"] and len(ctx["description"]) == 300
    assert ctx["startDate"] == ctx["endDate"] == "2027-05-14"
    assert ctx["requiredPrograms"] == [{"name": "공연", "startAt": "2027-05-14T13:00:00+09:00",
                                        "endAt": "2027-05-14T14:00:00+09:00"}]
    assert "개인 요청 문장" not in text  # 자유 입력은 Gemini로 보내지 않는다(test_api 개인정보 계약).
    line = next(r.getMessage() for r in caplog.records if "outcome=ok" in r.getMessage())
    assert "model=m" in line and "attempt=1" in line and "total_tokens=18" in line and "prompt_tokens=11" in line
    assert "개인 요청" not in caplog.text and "자연" not in caplog.text


def test_deadline_stops_retries_and_raises_retryable(monkeypatch, fresh):
    timeouts = []

    def slow_503(url, **kw):
        timeouts.append(kw["timeout"])
        fresh.now += kw["timeout"]
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", slow_503)
    with pytest.raises(ApiError) as err:
        call([place("a")])
    assert err.value.retryable and err.value.code == "TIMEOUT"
    assert timeouts == [25.0, 19.0]  # 25초 + 대기 1초 뒤 남은 19초만 기다리고, 다음 재시도는 하지 않는다
    assert fresh.now - 1000 <= recommendations.DEADLINE_SECONDS


def test_cache_hit_miss_expiry_and_failures_not_cached(monkeypatch, fresh):
    calls = []
    outputs = iter([response([{"placeId": "x", "priority": 1, "reason": "없는 장소"}])] * 2
                   + [response([{"placeId": "a", "priority": 1, "reason": "자연"}])] * 2)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append(1) or next(outputs))
    with pytest.raises(ApiError):  # 후보 밖 ID는 검증에서 거부되고 캐시되지 않는다
        call([place("a")])
    assert len(calls) == 2
    first = call([place("a")])
    assert len(calls) == 3 and first.recommendations[0].placeId == "a"
    assert call([place("a")]).recommendations[0].reason == "자연" and len(calls) == 3  # hit
    fresh.now += recommendations.CACHE_TTL_SECONDS + 1
    call([place("a")])
    assert len(calls) == 4  # 만료 후 다시 호출


def test_candidates_drop_closed_and_rank_interest_first():
    rows = [
        place("near", 0.001),
        place("nature_far", 0.02, tags=["NATURE"]),
        place("closed_date", 0.0005, tags=["NATURE"], closed=["2027-05-14"]),
        place("closed_weekday", 0.0005, hours={"mon": [{"open": "09:00", "close": "18:00"}]}),
        place("unknown_hours", 0.003, hours=None),
        place("outside", 0.2, tags=["NATURE"]),
    ]
    db = SimpleNamespace(scalars=lambda _: rows)
    got = planner.nearby_recommendation_candidates(db, 36.0, 126.0, [], [DAY], ["NATURE"])
    assert [p.id for p in got] == ["nature_far", "near", "unknown_hours"]


def test_fallback_picks_up_to_three_with_honest_reasons():
    got = planner.fallback_recommendations(
        [place("n", tags=["NATURE"]), place("b"), place("c"), place("d")], ["NATURE"])
    assert [r["placeId"] for r in got] == ["n", "b", "c"]
    assert [r["priority"] for r in got] == [1, 2, 3]
    assert got[0]["reason"] == "관심사와 맞고 축제장과 가까운 곳입니다."
    assert got[1]["reason"] == "축제장과 가까운 곳입니다."
