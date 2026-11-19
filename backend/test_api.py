import json
import io
import os
import tempfile
import uuid

import zipfile
from datetime import date, datetime, timedelta
from types import SimpleNamespace


os.environ.update(DATABASE_URL=f"sqlite:///{tempfile.mkdtemp()}/test.db", DEV_LOGIN="1", SEED_DEMO="1",
                  SECRET_KEY="test-secret")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from pypdf import PdfReader  # noqa: E402

import planner  # noqa: E402
from core import create_session, sign_location  # noqa: E402
from db import Place, Program, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from planner import check_dates, find_conflicts  # noqa: E402
from routers.auth import upsert_user  # noqa: E402
from seed import kst  # noqa: E402

START = date.today() + timedelta(days=10)  # seed 축제: START ~ START+2
ORIGIN = {"locationId": sign_location("예시 출발지", 36.35, 127.38)}


def login(c: TestClient) -> TestClient:
    c.get("/api/v1/auth/login/dev", follow_redirects=False)
    c.headers["X-CSRF-Token"] = c.get("/api/v1/auth/session").json()["data"]["csrfToken"]
    return c


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="https://testserver") as c:
        yield login(c)


@pytest.fixture(autouse=True)
def disable_live_provider_keys(monkeypatch):
    monkeypatch.delenv("ODSAY_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("KAKAO_MOBILITY_REST_API_KEY", raising=False)


def trip(**kw):
    return {"primaryFestivalId": "fst_demo_01", "startDate": START.isoformat(),
            "endDate": (START + timedelta(days=1)).isoformat(), "origin": ORIGIN, "transport": "CAR", **kw}


def test_check_dates_b02():
    fs, fe = date(2027, 5, 14), date(2027, 5, 16)
    assert check_dates(date(2027, 5, 13), date(2027, 5, 14), fs, fe) is None
    assert check_dates(date(2027, 5, 12), date(2027, 5, 13), fs, fe) == "DATE_NO_FESTIVAL_OVERLAP"
    assert check_dates(date(2027, 5, 11), date(2027, 5, 12), fs, fe) == "DATE_OUT_OF_FESTIVAL_WINDOW"
    assert check_dates(date(2027, 5, 15), date(2027, 5, 14), fs, fe) == "DATE_RANGE_INVALID"
    assert check_dates(date(2027, 5, 14), date(2027, 5, 16), fs, fe) == "TRIP_DURATION_EXCEEDED"


def test_conflicts_all_pairs():
    def p(id_, s, e):
        return SimpleNamespace(id=id_, name=id_, start_at=datetime(2027, 5, 14, s), end_at=datetime(2027, 5, 14, e))
    pairs = [c["relatedProgramIds"] for c in find_conflicts([p("A", 13, 18), p("B", 14, 15), p("C", 16, 17)])]
    assert pairs == [["A", "B"], ["A", "C"]]  # 인접하지 않은 A–C도 잡는다


def test_generation_failure_marks_job_failed(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("boom")
    monkeypatch.setattr(planner, "build_plan", boom)
    r = client.post("/api/v1/trip-requests", json=trip(budgetKrw=1), headers={"Idempotency-Key": str(uuid.uuid4())})
    job = client.get(f"/api/v1/jobs/{r.json()['data']['jobId']}").json()["data"]
    assert job["status"] == "FAILED" and job["error"]["code"] == "GENERATION_FAILED", job


def test_public_and_auth_basics(client):
    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.get("/api/v1/me").status_code == 401
        assert anon.get("/api/v1/festivals").json()["data"][0]["id"] == "fst_demo_01"
    r = client.get("/api/v1/festivals", params={"q": "없는축제"})
    assert r.status_code == 200 and r.json()["data"] == [] and r.json()["page"]["hasNext"] is False
    assert r.headers["cache-control"] == "private, no-store"
    places = client.get("/api/v1/places", params={"festivalId": "fst_demo_01", "radiusM": 5000}).json()["data"]
    assert [p["id"] for p in places] == ["pl_demo_01", "pl_demo_02", "pl_demo_03", "pl_demo_hotel"]  # 거리순
    # CSRF 없는 쓰기는 거절
    assert client.post("/api/v1/trip-requests/validate", json=trip(), headers={"X-CSRF-Token": "x"}).status_code == 403


def test_validate_rules(client):
    r = client.post("/api/v1/trip-requests/validate", json=trip(lodgingPreference="HOTEL"))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (d["days"], d["nights"], d["normalizedInput"]["origin"]["label"]) == (2, 1, "예시 출발지")
    bad = client.post("/api/v1/trip-requests/validate",
                      json=trip(startDate=(START - timedelta(days=2)).isoformat(),
                                endDate=(START - timedelta(days=1)).isoformat()))
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "DATE_NO_FESTIVAL_OVERLAP"  # T02
    forged = client.post("/api/v1/trip-requests/validate", json=trip(origin={"locationId": "loc_fake.abc"}))
    assert forged.json()["error"]["code"] == "LOCATION_NOT_FOUND"
    tbd = client.post("/api/v1/trip-requests/validate", json=trip(requiredProgramIds=["pr_demo_09"]))
    assert tbd.json()["error"]["code"] == "PROGRAM_TIME_UNCONFIRMED"


def test_create_idempotent_and_owner_only(client):
    key = str(uuid.uuid4())
    body = trip(requiredProgramIds=["pr_demo_01", "pr_demo_02"], budgetKrw=300000)
    r1 = client.post("/api/v1/trip-requests", json=body, headers={"Idempotency-Key": key})
    r2 = client.post("/api/v1/trip-requests", json=body, headers={"Idempotency-Key": key})
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["data"]["jobId"] == r2.json()["data"]["jobId"]  # T03
    assert client.post("/api/v1/trip-requests", json=trip(), headers={"Idempotency-Key": key}).status_code == 409

    job = client.get(f"/api/v1/jobs/{r1.json()['data']['jobId']}").json()["data"]
    assert job["status"] == "SUCCEEDED", job
    plan = client.get(f"/api/v1/trip-plans/{job['result']['planId']}").json()["data"]
    assert plan["status"] == "INFEASIBLE" and plan["conflicts"][0]["relatedProgramIds"] == ["pr_demo_01", "pr_demo_02"]  # T05
    assert plan["budget"]["status"] == "UNKNOWN"  # 교통비 미확인 → 0원 아님 (T06)
    assert client.get("/api/v1/me/trip-plans").json()["data"][0]["id"] == plan["id"]

    with SessionLocal() as db:
        other = create_session(db, upsert_user(db, "dev", "other-user", "다른사람", None))
    with TestClient(app, base_url="https://testserver", cookies={"sid": other}) as c2:
        assert c2.get(f"/api/v1/trip-plans/{plan['id']}").status_code == 404  # B09
        assert c2.get("/api/v1/me/trip-plans").json()["data"] == []


def test_settings(client):
    s = client.get("/api/v1/me/settings").json()["data"]
    v = s["version"]
    loc = sign_location("집 근처역", 36.3, 127.4)
    off = client.patch("/api/v1/me/settings", json={"version": v, "travelDefaults": {"originLocationId": loc}})
    assert off.json()["error"]["code"] == "ORIGIN_STORAGE_DISABLED"
    on = client.patch("/api/v1/me/settings", json={"version": v, "privacy": {"saveOrigin": True},
                                                   "travelDefaults": {"originLocationId": loc, "transport": "CAR"}})
    assert on.status_code == 200 and on.json()["data"]["travelDefaults"]["origin"]["label"] == "집 근처역"
    stale = client.patch("/api/v1/me/settings", json={"version": v, "travelDefaults": {"travelers": 2}})
    assert stale.status_code == 409  # T12
    v = on.json()["data"]["version"]
    disabled = client.patch("/api/v1/me/settings", json={"version": v, "privacy": {"saveOrigin": False}}).json()["data"]
    assert disabled["travelDefaults"]["origin"] is None and disabled["travelDefaults"]["transport"] == "CAR"  # T14
    assert client.delete("/api/v1/me/settings/origin").status_code == 400
    assert client.patch("/api/v1/me/settings", json={"version": 1, "x": 1}).json()["error"]["code"] == "UNKNOWN_FIELD"


def test_logout(client):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        assert c.post("/api/v1/auth/logout").status_code == 204
        assert c.get("/api/v1/me/trip-plans").status_code == 401  # T15


def generate_trip(client, **conditions):
    accepted = client.post("/api/v1/trip-requests", json=trip(**conditions),
                           headers={"Idempotency-Key": str(uuid.uuid4())})
    assert accepted.status_code == 202, accepted.text
    job_response = client.get(accepted.json()["data"]["statusUrl"])
    assert job_response.status_code == 200, job_response.text
    return job_response.json()["data"]


def generated_plan(client, **conditions):
    job = generate_trip(client, **conditions)
    assert job["status"] == "SUCCEEDED", job
    response = client.get(f"/api/v1/trip-plans/{job['result']['planId']}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize(("required_ids", "recommended_id"), [
    ([], "pl_demo_01"),
    (["pl_demo_01"], "pl_demo_02"),
])
def test_demo_recommendation_is_saved_and_retrievable(client, required_ids, recommended_id):
    plan = generated_plan(client, requiredPlaceIds=required_ids)
    first_day = plan["days"][0]
    places = [stop for stop in first_day["stops"] if stop["type"] == "PLACE"]
    assert [stop["placeId"] for stop in places] == [*required_ids, recommended_id]
    assert all(stop["required"] for stop in places[:-1])
    recommended = places[-1]
    assert recommended["required"] is False
    place = client.get(f"/api/v1/places/{recommended_id}").json()["data"]
    assert recommended["name"] == place["name"]
    assert recommended["recommendationReason"] == "필수 장소를 제외한 주변 후보 중 축제장에 가장 가깝습니다."
    assert recommended["startAt"] is None and recommended["endAt"] is None
    assert first_day["legs"] == []
    assert plan["status"] == "NEEDS_REVIEW"
    assert "NO_CANDIDATES" not in {warning["code"] for warning in plan["warnings"]}


@pytest.mark.parametrize(("conditions", "warning_code"), [
    ({"requiredPlaceIds": ["pl_demo_01", "pl_demo_02", "pl_demo_03"]}, "NO_CANDIDATES"),
    ({"primaryFestivalId": "fst_demo_02", "startDate": (START + timedelta(days=2)).isoformat(),
      "endDate": (START + timedelta(days=2)).isoformat(), "requiredPlaceIds": ["pl_demo_01"]},
     "FESTIVAL_LOCATION_UNKNOWN"),
])
def test_demo_plan_keeps_required_stops_without_recommendations(client, conditions, warning_code):
    plan = generated_plan(client, **conditions)
    stops = plan["days"][0]["stops"]
    assert stops[0]["festivalId"] == conditions.get("primaryFestivalId", "fst_demo_01")
    assert all(stop["required"] for stop in stops)
    assert [stop["placeId"] for stop in stops if stop["type"] == "PLACE"] == conditions["requiredPlaceIds"]
    assert warning_code in {warning["code"] for warning in plan["warnings"]}


@pytest.mark.parametrize(("place_id", "reason"), [
    ("pl_demo_04", "반경 밖에 있는 장소"),
    ("pl_demo_01", "   "),
])
def test_invalid_demo_recommendation_fails_without_saving_plan(client, monkeypatch, place_id, reason):
    monkeypatch.setattr(planner, "recommend_demo_place", lambda _: {
        "placeId": place_id, "priority": 1, "reason": reason,
    })
    before = client.get("/api/v1/me/trip-plans").json()["data"]
    job = generate_trip(client)
    assert job["status"] == "FAILED"
    assert job["result"] is None
    assert client.get("/api/v1/me/trip-plans").json()["data"] == before


def test_demo_recommendation_is_disabled_without_seed_flag(client, monkeypatch):
    monkeypatch.delenv("SEED_DEMO")
    plan = generated_plan(client)
    assert all(stop["required"] for day in plan["days"] for stop in day["stops"])


def test_gemini_retries_invalid_id_and_saves_ranked_places(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    calls = []

    def post(url, *, headers, json: dict, timeout):
        assert url.endswith("/models/test-model:generateContent")
        assert headers["x-goog-api-key"] == "test-key"
        request_text = json["contents"][0]["parts"][0]["text"]
        calls.append(request_text)
        assert "private request text" not in request_text
        assert ORIGIN["locationId"] not in request_text
        assert "responseJsonSchema" in json["generationConfig"]
        recommendations = [
            {"placeId": "pl_demo_01", "priority": 2, "reason": "카페"},
            {"placeId": "pl_demo_04" if len(calls) == 1 else "pl_demo_03",
             "priority": 1, "reason": "자연을 즐길 수 있는 장소"},
        ]
        return gemini_response(recommendations, [
            {"placeId": "pl_demo_02", "code": "INSUFFICIENT_DATA", "reason": "운영시간 확인 필요"},
        ])

    monkeypatch.setattr(httpx, "post", post)
    plan = generated_plan(client, requiredPlaceIds=["pl_demo_02"], interests=["NATURE"],
                          additionalRequests="private request text")
    assert len(calls) == 2
    payload = json.loads(calls[0])
    assert [p["id"] for p in payload["candidates"]] == ["pl_demo_02", "pl_demo_01", "pl_demo_03"]
    assert payload["userConditions"]["interests"] == ["NATURE"]
    assert payload["requiredPlaceIds"] == ["pl_demo_02"]
    stops = [stop for stop in plan["days"][0]["stops"] if stop["type"] == "PLACE"]
    assert [stop["placeId"] for stop in stops] == ["pl_demo_02", "pl_demo_03", "pl_demo_01"]
    assert stops[0]["required"] is True
    assert stops[1]["recommendationReason"] == "자연을 즐길 수 있는 장소"
    assert any(w["code"] == "RECOMMENDATION_REVIEW_REQUIRED" and w["stopIds"] == [stops[0]["id"]]
               for w in plan["warnings"])


def gemini_response(recommendations, conflicts):
    text = json.dumps({"recommendations": recommendations, "conflicts": conflicts})
    return httpx.Response(200, request=httpx.Request("POST", "https://example.test"), json={
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}],
    })


@pytest.mark.parametrize(("failure", "attempts", "code"), [
    ("invalid", 2, "LLM_OUTPUT_INVALID"),
    ("rate_limit", 3, "RATE_LIMITED"),
])
def test_gemini_failure_stores_explicit_fallback(client, monkeypatch, failure, attempts, code):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        if failure == "rate_limit":
            return httpx.Response(429, request=httpx.Request("POST", "https://example.test"))
        return gemini_response([{"placeId": "pl_demo_01", "priority": 2, "reason": "순위 불연속"}], [])

    monkeypatch.setattr(httpx, "post", post)
    before = client.get("/api/v1/me/trip-plans").json()["data"]
    job = generate_trip(client)
    assert len(calls) == attempts
    assert job["status"] == "SUCCEEDED"
    plan = client.get('/api/v1/trip-plans/' + job['result']['planId']).json()['data']
    assert any(w['code'] == 'AI_RECOMMENDATION_FALLBACK' for w in plan['warnings'])
    assert any(s['placeId'] == 'pl_demo_01' for d in plan['days'] for s in d['stops'])


@pytest.mark.parametrize('overnight', [False, True])
def test_transit_schedule_links_and_marks_unconfirmed(client, monkeypatch, overnight):
    from transit import TransitRoute, TransitRouteSession
    monkeypatch.setenv('ODSAY_API_KEY', 'test-key')
    monkeypatch.setattr(TransitRouteSession, 'schedule_loader', lambda *a: TransitRoute(600, 2000,
                       ({'mode': 'BUS', 'durationMinutes': 10},), 1500))
    plan = generated_plan(client, transport='PUBLIC_TRANSIT', lodgingPreference='HOTEL',
                          endDate=(START + timedelta(days=int(overnight))).isoformat(),
                          requiredProgramIds=['pr_demo_01', 'pr_demo_04'] if overnight else ['pr_demo_01'])
    legs = [leg for day in plan['days'] for leg in day['legs']]
    assert legs and all(l['mode'] == 'PUBLIC_TRANSIT' and l['serviceTimeStatus'] == 'UNCONFIRMED' for l in legs)
    assert legs[-1]['toStopId'] == 'ORIGIN'
    assert any(w['code'] == 'TRANSIT_TIMETABLE_UNCONFIRMED' for w in plan['warnings'])
    if overnight:
        assert plan['days'][0]['stops'][-1]['type'] == 'LODGING'
        assert plan['days'][1]['stops'][0]['type'] == 'LODGING'


def test_regeneration_preserves_parent_and_replays(client):
    parent = generated_plan(client)
    url = f"/api/v1/trip-plans/{parent['id']}/regenerations"
    body = {**trip(travelers=2), "baseVersion": parent['version']}
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 202
    accepted = response.json()['data']
    assert client.post(url, json=body, headers=headers).json()['data'] == accepted
    job = client.get(accepted['statusUrl']).json()['data']
    child = client.get('/api/v1/trip-plans/' + job['result']['planId']).json()['data']
    assert child['id'] != parent['id'] and child['parentPlanId'] == parent['id']
    assert child['inputSnapshot']['travelers'] == 2
    assert '_parentPlanId' not in child['inputSnapshot']
    assert client.get('/api/v1/trip-plans/' + parent['id']).json()['data'] == parent
    assert client.post(url, json={**body, 'travelers': 3}, headers=headers).status_code == 409
    assert client.post(url, json={**body, 'baseVersion': 999},
                       headers={'Idempotency-Key': str(uuid.uuid4())}).status_code == 409
    with TestClient(app, base_url='https://testserver') as anon:
        assert anon.post(url, json=body, headers=headers).status_code in (401, 403)


def test_plan_exports_pdf_png_zip_and_idempotency(client, monkeypatch):
    plan = generated_plan(client)
    url = f"/api/v1/trip-plans/{plan['id']}/exports"

    key = str(uuid.uuid4())
    body = {"format": "PDF", "version": plan["version"]}
    accepted = client.post(url, json=body, headers={"Idempotency-Key": key})
    assert accepted.status_code == 202
    assert client.post(url, json=body, headers={"Idempotency-Key": key}).json()["data"] == accepted.json()["data"]
    assert client.post(url, json={**body, "format": "PNG"},
                       headers={"Idempotency-Key": key}).status_code == 409

    record = client.get(accepted.json()["data"]["statusUrl"]).json()["data"]
    assert record["status"] == "SUCCEEDED" and record["contentType"] == "application/pdf"
    downloaded = client.get(record["downloadUrl"])
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"%PDF")
    extracted = "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(downloaded.content)).pages)
    assert plan["title"] in extracted and "계획 버전" in extracted and "확인할 사항" in extracted

    first_date = plan["days"][0]["date"]
    png = client.post(url, json={"format": "PNG", "version": plan["version"], "dates": [first_date]},
                      headers={"Idempotency-Key": str(uuid.uuid4())}).json()["data"]
    png_record = client.get(png["statusUrl"]).json()["data"]
    png_download = client.get(png_record["downloadUrl"])
    assert png_record["contentType"] == "image/png"
    assert Image.open(io.BytesIO(png_download.content)).format == "PNG"

    bundle = client.post(url, json={"format": "PNG", "version": plan["version"]},
                         headers={"Idempotency-Key": str(uuid.uuid4())}).json()["data"]
    bundle_record = client.get(bundle["statusUrl"]).json()["data"]
    with zipfile.ZipFile(io.BytesIO(client.get(bundle_record["downloadUrl"]).content)) as archive:
        assert len(archive.namelist()) == len(plan["days"])
        assert all(name.endswith(".png") for name in archive.namelist())

    assert client.post(url, json={"format": "PDF", "version": 999},
                       headers={"Idempotency-Key": str(uuid.uuid4())}).status_code == 409
    assert client.post(url, json={"format": "PDF", "version": plan["version"], "dates": []},
                       headers={"Idempotency-Key": str(uuid.uuid4())}).status_code == 422


def mock_car_directions(monkeypatch, status_code, body):
    monkeypatch.setenv("KAKAO_MOBILITY_REST_API_KEY", "test-route-key")
    calls = []

    def get(url, *, params, headers, timeout):
        calls.append((url, params, headers))
        return httpx.Response(status_code, request=httpx.Request("GET", url), json=body)

    monkeypatch.setattr(httpx, "get", get)
    return calls


def test_car_route_to_festival_is_saved_when_lodging_is_self_arranged(client, monkeypatch):
    calls = mock_car_directions(monkeypatch, 200, {
        "routes": [{"result_code": 0, "summary": {"duration": 125, "distance": 4000}}],
    })
    plan = generated_plan(client, lodgingPreference="SELF_ARRANGED")
    assert calls == [(
        "https://apis-navi.kakaomobility.com/v1/directions",
        {"origin": "127.38,36.35", "destination": "126.298,36.745", "summary": "true"},
        {"Authorization": "KakaoAK test-route-key"},
    )]
    assert plan["days"][1]["legs"] == []
    festival_day = plan["days"][0]
    leg, = festival_day["legs"]
    assert leg["fromStopId"] == "ORIGIN"
    assert leg["toStopId"] == festival_day["stops"][0]["id"]
    assert (leg["durationMinutes"], leg["distanceMeters"]) == (3, 4000)
    assert leg["status"] == "ESTIMATED" and leg["provider"] == "KAKAO_MOBILITY"
    assert leg["departureAt"] is None and leg["arrivalAt"] is None
    assert plan["status"] == "NEEDS_REVIEW"
    assert any(w["code"] == "LODGING_UNCONFIRMED" for w in plan["warnings"])


@pytest.mark.parametrize(("conditions", "expected_calls"), [
    ({}, 1),
    ({"transport": "PUBLIC_TRANSIT"}, 0),
    ({"primaryFestivalId": "fst_demo_02", "startDate": (START + timedelta(days=2)).isoformat(),
      "endDate": (START + timedelta(days=2)).isoformat()}, 0),
])
def test_unavailable_route_is_not_saved_as_zero_minutes(client, monkeypatch, conditions, expected_calls):
    calls = mock_car_directions(monkeypatch, 200, {"routes": [{"result_code": 103}]})
    plan = generated_plan(client, **conditions)
    assert len(calls) == expected_calls
    assert all(day["legs"] == [] for day in plan["days"])
    assert any(w["code"] == "ROUTE_UNAVAILABLE" for w in plan["warnings"])


@pytest.mark.parametrize("status_code", [403, 503])
def test_route_provider_error_keeps_plan_with_warning(client, monkeypatch, status_code):
    mock_car_directions(monkeypatch, status_code, {"msg": "provider detail"})
    plan = generated_plan(client)
    assert all(day["legs"] == [] for day in plan["days"])
    warning, = [w for w in plan["warnings"] if w["code"] == "ROUTE_UNAVAILABLE"]
    assert "provider detail" not in warning["message"]


SCHEDULE_ORIGIN = (127.38, 36.35)
SCHEDULE_FESTIVAL = (126.298, 36.745)
SCHEDULE_CAFE = (126.298, 36.75)
SCHEDULE_RESTAURANT = (126.298, 36.755)
SCHEDULE_ATTRACTION = (126.298, 36.765)
SCHEDULE_HOTEL = (126.298, 36.775)


def mock_schedule_drives(monkeypatch, overrides=None):
    monkeypatch.setenv("KAKAO_MOBILITY_REST_API_KEY", "test-route-key")
    durations = {
        (SCHEDULE_ORIGIN, SCHEDULE_FESTIVAL): 1800,
        (SCHEDULE_FESTIVAL, SCHEDULE_CAFE): 900,
        (SCHEDULE_FESTIVAL, SCHEDULE_ORIGIN): 1200,
        **(overrides or {}),
    }
    calls = []

    def get(url, *, params, headers, timeout):
        pair = tuple(tuple(round(float(coordinate), 6) for coordinate in params[key].split(","))
                     for key in ("origin", "destination"))
        calls.append(pair)
        duration = durations.get(pair, 600)
        route = ({"result_code": 103} if duration is None
                 else {"result_code": 0, "summary": {"duration": duration, "distance": 4500}})
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "routes": [route],
        })

    monkeypatch.setattr(httpx, "get", get)
    return calls


@pytest.mark.parametrize("return_seconds", [600, 14400])
def test_day_trip_fits_cafe_before_fixed_program_or_explains_failure(client, monkeypatch, return_seconds):
    calls = mock_schedule_drives(monkeypatch, {(SCHEDULE_CAFE, SCHEDULE_FESTIVAL): return_seconds})
    plan = generated_plan(client, endDate=START.isoformat(), requiredProgramIds=["pr_demo_01"])
    day = plan["days"][0]
    program = next(s for s in day["stops"] if s["type"] == "PROGRAM")
    cafe = next(s for s in day["stops"] if s["placeId"] == "pl_demo_01")
    assert program["startAt"][11:16] == "13:00" and program["endAt"][11:16] == "14:00"
    assert (SCHEDULE_FESTIVAL, SCHEDULE_CAFE) in calls
    assert (SCHEDULE_CAFE, SCHEDULE_FESTIVAL) in calls
    if return_seconds == 600:
        assert [s["type"] for s in day["stops"]] == ["FESTIVAL", "PLACE", "PROGRAM"]
        assert cafe["startAt"][11:16] == "11:50" and cafe["endAt"][11:16] == "12:50"
        assert [leg["durationMinutes"] for leg in day["legs"]] == [30, 15, 10, 20]
        assert day["legs"][1]["arrivalAt"] == cafe["startAt"]
        assert day["legs"][2]["departureAt"] == cafe["endAt"]
        assert day["legs"][2]["arrivalAt"] == program["startAt"]
    else:
        assert cafe["startAt"] is None and cafe["endAt"] is None
        assert [leg["durationMinutes"] for leg in day["legs"]] == [30, 20]
        assert any(w["code"] == "PLACE_SCHEDULE_UNCONFIRMED" and w["stopIds"] == [cafe["id"]]
                   for w in plan["warnings"])
    assert plan["status"] == "NEEDS_REVIEW"


def test_day_trip_can_visit_cafe_after_early_program(client, monkeypatch):
    with SessionLocal() as db:
        db.add(Program(id="pr_test_early", festival_id="fst_demo_01", name="이른 공연",
                       start_at=kst(START.year, START.month, START.day, 8),
                       end_at=kst(START.year, START.month, START.day, 9)))
        db.commit()

    mock_schedule_drives(monkeypatch)
    plan = generated_plan(client, endDate=START.isoformat(), requiredProgramIds=["pr_test_early"])
    day = plan["days"][0]
    _, program, cafe = day["stops"]
    assert cafe["startAt"][11:16] == "09:15" and cafe["endAt"][11:16] == "10:15"
    assert day["legs"][1]["fromStopId"] == program["id"]
    assert day["legs"][1]["departureAt"] == program["endAt"]
    assert day["legs"][1]["arrivalAt"] == cafe["startAt"]
    assert day["legs"][2]["fromStopId"] == cafe["id"]
    assert day["legs"][2]["toStopId"] == "ORIGIN"


def test_day_trip_schedules_required_and_multiple_recommended_places_with_return(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: gemini_response([
        {"placeId": "pl_demo_01", "priority": 1, "reason": "카페"},
        {"placeId": "pl_demo_03", "priority": 2, "reason": "관광"},
        {"placeId": "pl_demo_02", "priority": 3, "reason": "식당"},
    ], []))
    calls = mock_schedule_drives(monkeypatch)
    plan = generated_plan(client, endDate=START.isoformat(), requiredProgramIds=["pr_demo_01", "pr_demo_03"],
                          requiredPlaceIds=["pl_demo_02"])
    day = plan["days"][0]
    timed = [stop for stop in day["stops"] if stop["type"] != "FESTIVAL"]
    assert {stop["placeId"] for stop in timed if stop["type"] == "PLACE"} == {
        "pl_demo_01", "pl_demo_02", "pl_demo_03",
    }
    assert next(stop for stop in timed if stop["placeId"] == "pl_demo_02")["required"]
    assert [stop["startAt"][11:16] for stop in timed if stop["type"] == "PROGRAM"] == ["13:00", "18:00"]
    assert all(a["endAt"] <= b["startAt"] for a, b in zip(timed, timed[1:]))
    assert not plan["conflicts"]
    assert day["legs"][-1]["fromStopId"] == timed[-1]["id"]
    assert day["legs"][-1]["departureAt"] == timed[-1]["endAt"]
    assert day["legs"][-1]["toStopId"] == "ORIGIN"
    assert len(calls) == len(set(calls))


def test_day_trip_reports_unschedulable_required_place(client, monkeypatch):
    mock_schedule_drives(monkeypatch, {(SCHEDULE_FESTIVAL, SCHEDULE_RESTAURANT): 10 * 3600})
    plan = generated_plan(client, endDate=START.isoformat(), requiredProgramIds=["pr_demo_01"],
                          requiredPlaceIds=["pl_demo_02"])
    assert plan["status"] == "INFEASIBLE"
    day = plan["days"][0]
    restaurant = next(stop for stop in day["stops"] if stop["placeId"] == "pl_demo_02")
    assert restaurant["required"] and restaurant["startAt"] is None
    assert plan["conflicts"][0]["relatedPlaceIds"] == ["pl_demo_02"]
    assert plan["conflicts"][0]["relatedProgramIds"] == ["pr_demo_01"]


def test_day_trip_prioritizes_required_place_that_closes_earlier(client, monkeypatch):
    mock_schedule_drives(monkeypatch, {
        (SCHEDULE_ATTRACTION, SCHEDULE_CAFE): 7200,
        (SCHEDULE_CAFE, SCHEDULE_ATTRACTION): 7200,
    })
    plan = generated_plan(client, endDate=START.isoformat(), requiredProgramIds=["pr_demo_01"],
                          requiredPlaceIds=["pl_demo_03", "pl_demo_01"])
    day = plan["days"][0]
    cafe = next(s for s in day["stops"] if s["placeId"] == "pl_demo_01")
    attraction = next(s for s in day["stops"] if s["placeId"] == "pl_demo_03")
    assert cafe["endAt"][11:16] < "13:00" < attraction["startAt"][11:16]
    assert not plan["conflicts"]


def test_day_trip_reports_missing_return_without_inventing_duration(client, monkeypatch):
    mock_schedule_drives(monkeypatch, {(SCHEDULE_FESTIVAL, SCHEDULE_ORIGIN): None})
    plan = generated_plan(client, endDate=START.isoformat(), requiredProgramIds=["pr_demo_01"])
    assert plan["status"] == "NEEDS_REVIEW"
    assert all(leg["toStopId"] != "ORIGIN" for leg in plan["days"][0]["legs"])
    assert any(w["code"] == "RETURN_ROUTE_UNAVAILABLE" for w in plan["warnings"])


def test_two_day_car_plan_links_same_hotel_and_returns_to_origin(client, monkeypatch):
    calls = mock_schedule_drives(monkeypatch)
    plan = generated_plan(client, lodgingPreference="HOTEL", requiredProgramIds=["pr_demo_01", "pr_demo_04"],
                          requiredPlaceIds=["pl_demo_02", "pl_demo_hotel"])
    first, second = plan["days"]
    arrival, departure = first["stops"][-1], second["stops"][0]
    assert arrival["type"] == departure["type"] == "LODGING"
    assert arrival["placeId"] == departure["placeId"] == "pl_demo_hotel"
    assert arrival["id"] != departure["id"]
    assert [stop["placeId"] for day in plan["days"] for stop in day["stops"]].count("pl_demo_hotel") == 2
    assert first["legs"][0]["fromStopId"] == "ORIGIN"
    assert first["legs"][-1]["toStopId"] == arrival["id"]
    assert first["legs"][-1]["arrivalAt"] == arrival["startAt"]
    assert second["legs"][0]["fromStopId"] == departure["id"]
    assert second["legs"][0]["departureAt"] == departure["startAt"]
    assert second["legs"][-1]["toStopId"] == "ORIGIN"
    assert [s["startAt"][11:16] for day in plan["days"] for s in day["stops"] if s["type"] == "PROGRAM"] == [
        "13:00", "10:00",
    ]
    assert first["date"] < second["date"]
    assert arrival["startAt"] < departure["startAt"]
    assert plan["conflicts"] == []
    assert len(calls) == len(set(calls))


def test_overnight_starts_before_festival_without_phantom_visit(client, monkeypatch):
    mock_schedule_drives(monkeypatch)
    plan = generated_plan(client, startDate=(START - timedelta(days=1)).isoformat(), endDate=START.isoformat(),
                          requiredProgramIds=["pr_demo_01"])
    first, second = plan["days"]
    assert all(s["type"] != "FESTIVAL" for s in first["stops"])
    assert first["legs"][-1]["toStopId"] == first["stops"][-1]["id"]
    assert second["stops"][0]["type"] == "LODGING"
    assert second["legs"][0]["fromStopId"] == second["stops"][0]["id"]
    assert second["legs"][-1]["toStopId"] == "ORIGIN"


def test_overnight_places_required_visit_on_the_day_it_is_open(client, monkeypatch):
    mock_schedule_drives(monkeypatch)
    with SessionLocal() as db:
        restaurant = db.get(Place, "pl_demo_02")
        original_closed_dates = restaurant.closed_dates
        restaurant.closed_dates = [START.isoformat()]
        db.commit()
        try:
            plan = generated_plan(client, requiredPlaceIds=["pl_demo_02"],
                                  requiredProgramIds=["pr_demo_01", "pr_demo_04"])
        finally:
            restaurant.closed_dates = original_closed_dates
            db.commit()

    first, second = plan["days"]
    assert all(stop["placeId"] != "pl_demo_02" for stop in first["stops"])
    restaurant = next(stop for stop in second["stops"] if stop["placeId"] == "pl_demo_02")
    assert restaurant["startAt"].startswith(second["date"])
    assert plan["conflicts"] == []


def test_missing_lodging_preference_leaves_second_day_route_unconfirmed(client, monkeypatch):
    calls = mock_schedule_drives(monkeypatch)
    plan = generated_plan(client, lodgingPreference="PENSION", requiredProgramIds=["pr_demo_04"])
    assert len(calls) == 1
    assert any(w["code"] == "LODGING_UNCONFIRMED" for w in plan["warnings"])
    assert all(s["type"] != "LODGING" for day in plan["days"] for s in day["stops"])
    assert plan["days"][1]["legs"] == []
    assert plan["days"][1]["stops"][-1]["programId"] == "pr_demo_04"
    assert plan["status"] == "NEEDS_REVIEW"


def test_missing_hotel_arrival_leg_keeps_known_second_day_routes(client, monkeypatch):
    mock_schedule_drives(monkeypatch, {(SCHEDULE_FESTIVAL, SCHEDULE_HOTEL): None})
    plan = generated_plan(client, requiredProgramIds=["pr_demo_01", "pr_demo_04"])
    first, second = plan["days"]
    lodging = first["stops"][-1]
    assert lodging["type"] == "LODGING" and lodging["startAt"] is None
    assert all(leg["toStopId"] != lodging["id"] for leg in first["legs"])
    assert any(w["code"] == "LODGING_ROUTE_UNAVAILABLE" for w in plan["warnings"])
    assert second["legs"][0]["fromStopId"] == second["stops"][0]["id"]


def test_export_download_is_served_from_db_without_storage_dir(client, monkeypatch):
    from db import PlanExport
    plan = generated_plan(client)
    accepted = client.post(f"/api/v1/trip-plans/{plan['id']}/exports", json={"format": "PDF", "version": plan["version"]},
                           headers={"Idempotency-Key": str(uuid.uuid4())}).json()["data"]
    record = client.get(accepted["statusUrl"]).json()["data"]
    downloaded = client.get(record["downloadUrl"])
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"%PDF")
    assert downloaded.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''" in downloaded.headers["content-disposition"]
    with SessionLocal() as db:
        row = db.get(PlanExport, accepted["exportId"])
        assert row.content == downloaded.content and row.storage_key is None


def test_stale_export_is_marked_timed_out(client):
    from db import Job, PlanExport
    plan = generated_plan(client)
    accepted = client.post(f"/api/v1/trip-plans/{plan['id']}/exports", json={"format": "PDF", "version": plan["version"]},
                           headers={"Idempotency-Key": str(uuid.uuid4())}).json()["data"]
    with SessionLocal() as db:
        row, job = db.get(PlanExport, accepted["exportId"]), db.get(Job, accepted["jobId"])
        row.status = job.status = "RUNNING"
        row.updated_at = job.updated_at = datetime.utcnow() - timedelta(minutes=11)
        db.commit()
    record = client.get(accepted["statusUrl"]).json()["data"]
    assert record["status"] == "FAILED" and record["error"]["code"] == "EXPORT_TIMEOUT" and record["error"]["retryable"]
    with SessionLocal() as db:
        assert db.get(Job, accepted["jobId"]).status == "FAILED"


def test_generation_does_not_overwrite_job_failed_meanwhile(client, monkeypatch):
    from db import Job, TripPlan
    real_build = planner.build_plan

    def build_after_timeout(db, req):
        with SessionLocal() as other:  # get_job이 시간 초과로 먼저 실패 처리한 상황
            job = other.scalar(planner.select(Job).where(Job.request_id == req.id))
            job.status, job.error = "FAILED", {"code": "GENERATION_TIMEOUT", "message": "", "retryable": True}
            other.commit()
        return real_build(db, req)
    monkeypatch.setattr(planner, "build_plan", build_after_timeout)
    job = generate_trip(client)
    assert job["status"] == "FAILED" and job["error"]["code"] == "GENERATION_TIMEOUT", job
    with SessionLocal() as db:
        request_id = db.get(Job, job["id"]).request_id
        assert db.scalar(planner.select(TripPlan).where(TripPlan.request_id == request_id)) is None


def test_non_ascii_csrf_token_is_rejected_not_crashing(client):
    r = client.post("/api/v1/trip-requests/validate", json=trip(), headers={"X-CSRF-Token": "토큰".encode()})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_INVALID"


def test_negative_cursor_is_invalid(client):
    import base64
    r = client.get("/api/v1/me/trip-plans", params={"cursor": base64.urlsafe_b64encode(b"-1").decode()})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("missing", ["hours", "duration", "both"])
def test_draft_itinerary_includes_places_with_missing_source_data(client, monkeypatch, missing):
    mock_schedule_drives(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: gemini_response([], [
        {"placeId": pid, "code": "INSUFFICIENT_DATA", "reason": "방문 정보 확인 필요"}
        for pid in ["pl_demo_01", "pl_demo_02", "pl_demo_03"]
    ]))
    ids = ["pl_demo_01", "pl_demo_02", "pl_demo_03"]
    with SessionLocal() as db:
        rows = [db.get(Place, pid) for pid in ids]
        original = [(p.opening_hours, p.stay_minutes_estimate) for p in rows]
        for p in rows:
            if missing in ("hours", "both"):
                p.opening_hours = None
            if missing in ("duration", "both"):
                p.stay_minutes_estimate = None
        db.commit()
        try:
            plan = generated_plan(client, requiredPlaceIds=ids)
            stops = [s for d in plan["days"] for s in d["stops"] if s["type"] == "PLACE"]
            assert {s["placeId"] for s in stops} == set(ids)
            assert all(s["startAt"] and s["endAt"] and s["scheduleAssumptions"] for s in stops)
            assert not plan["conflicts"]
            assert plan["status"] == "NEEDS_REVIEW"
            assert any(w["code"] == "SCHEDULE_ASSUMPTION" for w in plan["warnings"])
            for day in plan["days"]:
                timed = [s for s in day["stops"] if s["startAt"] and s["endAt"]]
                assert all(a["endAt"] <= b["startAt"] for a, b in zip(timed, timed[1:]))
                for stop in [s for s in timed if s["type"] == "PLACE"]:
                    incoming = next(leg for leg in day["legs"] if leg["toStopId"] == stop["id"])
                    assert incoming["arrivalAt"] == stop["startAt"]
                    assert any(leg["fromStopId"] == stop["id"] for leg in day["legs"])
            for row in rows:
                db.refresh(row)
                if missing in ("hours", "both"):
                    assert row.opening_hours is None
                if missing in ("duration", "both"):
                    assert row.stay_minutes_estimate is None
        finally:
            for row, (hours, duration) in zip(rows, original):
                row.opening_hours, row.stay_minutes_estimate = hours, duration
            db.commit()


def test_draft_itinerary_respects_known_closed_day(client, monkeypatch):
    mock_schedule_drives(monkeypatch)
    with SessionLocal() as db:
        place = db.get(Place, "pl_demo_02")
        original = (place.opening_hours, place.stay_minutes_estimate, place.closed_dates)
        place.opening_hours, place.stay_minutes_estimate = None, None
        place.closed_dates = [START.isoformat()]
        db.commit()
        try:
            plan = generated_plan(client, endDate=START.isoformat(), requiredPlaceIds=[place.id])
            stop = next(s for s in plan["days"][0]["stops"] if s["placeId"] == place.id)
            assert stop["startAt"] is None
            assert plan["status"] == "INFEASIBLE"
        finally:
            place.opening_hours, place.stay_minutes_estimate, place.closed_dates = original
            db.commit()


def test_map_filters_out_food_cafes_and_lodging(client):
    response = client.get("/api/v1/places", params=[
        ("bbox", "126.2,36.6,126.4,36.9"), ("categories", "ATTRACTION"),
        ("categories", "EXPERIENCE"), ("limit", "50"),
    ])
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows and all(row["category"] in ("ATTRACTION", "EXPERIENCE") for row in rows)
    assert "pl_demo_03" in {row["id"] for row in rows}


def test_board_samples_are_explicit_idempotent_and_public(client):
    from seed_board_samples import seed_samples
    from db import BoardPost
    from sqlalchemy import select
    with SessionLocal() as db:
        assert seed_samples(db) == 4
        assert seed_samples(db) == 0
        assert len(list(db.scalars(select(BoardPost)))) == 4
    public = TestClient(app, base_url="https://testserver")
    response = public.get("/api/v1/posts?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2 and body["page"]["hasNext"]
    assert all(row["isSample"] and row["likeCount"] == 0 for row in body["data"])
    page2 = public.get("/api/v1/posts", params={"cursor": body["page"]["nextCursor"], "limit": 2}).json()
    assert len(page2["data"]) == 2 and not page2["page"]["hasNext"]
    assert not {row["id"] for row in body["data"]} & {row["id"] for row in page2["data"]}
    filtered = public.get("/api/v1/posts?region=TAEAN").json()["data"]
    assert len(filtered) == 1 and filtered[0]["region"] == "TAEAN"
    detail = public.get(f"/api/v1/posts/{filtered[0]['id']}").json()["data"]
    assert detail["isSample"] and detail["body"].strip()
    assert public.get("/api/v1/posts/missing").status_code == 404
    assert public.get("/api/v1/posts?region=WRONG").status_code == 400
    assert public.get("/api/v1/posts?cursor=bad").status_code == 400
    assert public.get("/api/v1/posts?region=ASAN").json()["data"] == []


@pytest.mark.parametrize("overnight", [False, True])
def test_simulation_catalog_generates_timed_plan_and_estimated_budget(client, monkeypatch, overnight):
    monkeypatch.setenv("SIMULATION_DATA", "1")
    mock_schedule_drives(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: gemini_response([
        {"placeId": "pl_demo_01", "priority": 1, "reason": "쉬어가기 좋은 카페"}
    ], [{"placeId": "pl_demo_02", "code": "INSUFFICIENT_DATA", "reason": "필수 방문지"}]))
    with SessionLocal() as db:
        row = db.get(Place, "pl_demo_02")
        original = (row.opening_hours, row.stay_minutes_estimate, row.price)
        row.opening_hours = row.stay_minutes_estimate = row.price = None
        db.commit()
        try:
            end = START + timedelta(days=int(overnight))
            plan = generated_plan(client, endDate=end.isoformat(), requiredPlaceIds=[row.id],
                                  travelers=3, budgetKrw=500000)
            stops = [stop for day in plan["days"] for stop in day["stops"] if stop["type"] == "PLACE"]
            assert stops and all(stop["startAt"] and stop["endAt"] for stop in stops)
            assert all(stop["costKrw"] is not None for stop in stops)
            assert all(stop["costStatus"] in ("VERIFIED", "ESTIMATED") for stop in stops)
            budget = plan["budget"]
            assert budget["estimatedTotalKrw"] > 0 and budget["unknownItemCount"] == 0
            assert budget["dataMode"] == "SIMULATION"
            assert "dataMode" not in plan["inputSnapshot"]
            assert budget["estimatedTotalKrw"] == sum(item["amountKrw"] for item in budget["items"])
            restaurant = next(item for item in budget["items"] if item["label"] == row.name)
            assert restaurant["amountKrw"] == 15000 * 3
            meals = sum(item["amountKrw"] for item in budget["items"] if item["category"] == "FOOD")
            assert meals == (len(plan["days"]) * 2 - 1) * 12000 * 3
            hotels = [item for item in budget["items"] if item["category"] == "LODGING"]
            assert len(hotels) == int(overnight)
            if overnight:
                assert hotels[0]["quantity"] == 2
            assert not plan["conflicts"]
            assert not any(w["code"] in ("COST_UNKNOWN", "PLACE_SCHEDULE_UNCONFIRMED", "SCHEDULE_ASSUMPTION") for w in plan["warnings"])
            db.refresh(row)
            assert row.opening_hours is None and row.price is None and row.stay_minutes_estimate is None
            # A saved simulation plan remains a valid regeneration input.
            response = client.post(f"/api/v1/trip-plans/{plan['id']}/regenerations",
                                   json={**plan["inputSnapshot"], "baseVersion": plan["version"]},
                                   headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 202, response.text
        finally:
            row.opening_hours, row.stay_minutes_estimate, row.price = original
            db.commit()


def test_simulation_overlay_is_opt_in_and_keeps_real_closed_dates(monkeypatch):
    from simulation_data import place_for_plan
    row = Place(id="simulation_test", name="청포대해수욕장", category="ATTRACTION", lat=36.7, lng=126.3,
                address="충남", closed_dates=[START.isoformat()])
    monkeypatch.setenv("SIMULATION_DATA", "0")
    assert place_for_plan(row) is row
    monkeypatch.setenv("SIMULATION_DATA", "1")
    overlay = place_for_plan(row)
    assert overlay.price["amountKrw"] == 0 and overlay.price["status"] == "ESTIMATED"
    assert overlay.closed_dates == row.closed_dates
    assert row.opening_hours is None and row.price is None
    hotel = Place(id="simulation_hotel", name="청포대하와이펜션", category="LODGING", lat=36.7, lng=126.3,
                  address="충남", closed_dates=[])
    assert place_for_plan(hotel).price["amountKrw"] == 80000


def test_simulation_budget_flags_over_budget_and_excludes_self_arranged_lodging(monkeypatch):
    from budgeting import calculate_budget
    monkeypatch.setenv("SIMULATION_DATA", "1")
    budget = calculate_budget({"travelers": 2, "transport": "CAR", "budgetKrw": 0,
                               "lodgingPreference": "SELF_ARRANGED"},
                              [{"date": "2026-01-01", "stops": [], "legs": []},
                               {"date": "2026-01-02", "stops": [], "legs": []}])
    assert budget["status"] == "OVER" and budget["unknownItemCount"] == 0
    assert not any(item["category"] == "LODGING" for item in budget["items"])
    assert budget["knownTotalKrw"] == 0 < budget["estimatedTotalKrw"]


def test_verified_light_festival_hours_are_seasonal():
    from festival_content import verified_visit_window
    assert verified_visit_window("fst_cn_113_2026", "2026-01-01") == {"open": "17:30", "close": "22:00"}
    assert verified_visit_window("fst_cn_113_2026", "2026-07-01") == {"open": "19:30", "close": "23:00"}
    assert verified_visit_window("fst_cn_182_2026", "2026-12-24") is None


def test_evening_festival_keeps_daytime_places_before_lights(client, monkeypatch):
    from scheduling import schedule_car_trip
    from routing import CarRoute
    days = [{"date": START.isoformat(), "stops": [
        {"id": "light", "type": "FESTIVAL", "name": "야간 축제", "visitDurationMinutes": 120,
         "visitWindow": {"open": "17:30", "close": "22:00"}, "scheduleSource": "VERIFIED"},
        {"id": "cafe", "type": "PLACE", "placeId": "pl_demo_01", "name": "카페", "required": True},
    ], "legs": []}]
    with SessionLocal() as db:
        warnings, conflicts = [], []
        assert schedule_car_trip(days, {"pl_demo_01": db.get(Place, "pl_demo_01")},
                                 (36.7, 126.3), (36.75, 126.3), "test", warnings, conflicts,
                                 route_loader=lambda *args: CarRoute(600, 4000))
    stops = days[0]["stops"]
    assert [stop["id"] for stop in stops] == ["cafe", "light"]
    assert stops[1]["startAt"][11:16] == "17:30"
    assert stops[0]["endAt"] < stops[1]["startAt"]
    assert not conflicts
