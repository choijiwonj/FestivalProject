"""자차 여행의 날짜별 필수 일정, 추천 장소 및 이동 시간을 계산한다."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable
from types import SimpleNamespace

from core import KST, iso
from db import Place
from routing import CarRoute, get_car_route

log = logging.getLogger(__name__)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Coordinates = tuple[float, float]


@dataclass
class Visit:
    stop: dict
    location: Coordinates
    duration: timedelta
    windows: list[tuple[datetime, datetime]]


@dataclass
class ScheduledVisit:
    visit: Visit
    start: datetime
    drive: CarRoute

    @property
    def end(self) -> datetime:
        return self.start + self.visit.duration


@dataclass
class DaySchedule:
    visits: list[ScheduledVisit]
    final_route: CarRoute | None
    final_issue: str | None = None


@dataclass
class DayContext:
    day: dict
    start: Coordinates
    destination: Coordinates
    start_lodging: dict | None
    end_lodging: dict | None
    day_start: datetime
    day_end: datetime
    order: list[Visit]
    schedule: DaySchedule


def opening_windows(place: Place, day: date) -> list[tuple[datetime, datetime]]:
    """요일별 영업 구간을 한국시간으로 해석한다."""
    if not isinstance(place.opening_hours, dict):
        return []
    periods = place.opening_hours.get(WEEKDAYS[day.weekday()])
    if not isinstance(periods, list):
        return []
    windows = []
    for period in periods:
        if not isinstance(period, dict):
            log.warning("malformed opening_hours period skipped: place=%s", place.id)
            continue
        try:
            start = datetime.combine(day, time.fromisoformat(period["open"]), KST)
            end = datetime.combine(day, time.fromisoformat(period["close"]), KST)
        except (KeyError, TypeError, ValueError):
            log.warning("malformed opening_hours period skipped: place=%s", place.id)
            continue
        if start < end:
            windows.append((start, end))
    return sorted(windows)



def prepare_draft_places(places: dict[str, Place], stops: list[dict], warnings: list[dict]) -> dict:
    """Use explicit planning assumptions without changing source records or claiming verified hours."""
    prepared = dict(places)
    durations = {"CAFE": 60, "RESTAURANT": 75, "ATTRACTION": 90, "SHOP": 60}
    for stop in stops:
        place = places[stop["placeId"]]
        assumptions = []
        hours = place.opening_hours
        duration = place.stay_minutes_estimate
        if not isinstance(hours, dict) or not hours:
            hours = {day: [{"open": "09:00", "close": "20:00"}] for day in WEEKDAYS}
            assumptions.append("영업시간 미확인: 09~20시 사이 방문하는 가안이며 실제 영업 여부 확인이 필요합니다.")
        if not isinstance(duration, (int, float)) or duration <= 0:
            duration = durations.get(place.category, 60)
            assumptions.append(f"체류시간 {duration}분은 업종별 계획용 추정값입니다.")
        if not assumptions:
            continue
        stop.update(scheduleAssumptions=assumptions, estimatedDurationMinutes=duration)
        prepared[place.id] = SimpleNamespace(
            id=place.id, lat=place.lat, lng=place.lng, opening_hours=hours,
            closed_dates=place.closed_dates, stay_minutes_estimate=duration,
        )
        warnings.append({"code": "SCHEDULE_ASSUMPTION", "message": f"{stop['name']}: " + " ".join(assumptions),
                         "stopIds": [stop["id"]]})
    return prepared


def evaluate_order(order: list[Visit], origin: Coordinates, destination: Coordinates,
                   get_route: Callable[[Coordinates, Coordinates], CarRoute | None], day_start: datetime,
                   day_end: datetime) -> DaySchedule | None:
    """각 방문의 가장 이른 시작 시간을 구하고 첫 고정 일정 앞의 대기 시간을 줄인다."""
    scheduled: list[ScheduledVisit] = []
    previous_location, previous_end = origin, day_start
    for visit in order:
        if not any(max(opens, previous_end) + visit.duration <= closes for opens, closes in visit.windows):
            return None
        drive = get_route(previous_location, visit.location)
        if drive is None:
            return None
        arrival = previous_end + timedelta(minutes=drive.duration_minutes)
        start = next((max(opens, arrival) for opens, closes in visit.windows
                      if max(opens, arrival) + visit.duration <= closes), None)
        if start is None:
            return None
        scheduled.append(ScheduledVisit(visit, start, drive))
        previous_location, previous_end = visit.location, start + visit.duration

    anchor = next((i for i, item in enumerate(scheduled) if item.visit.stop["type"] == "PROGRAM"), 1)
    for i in range(min(anchor, len(scheduled) - 1) - 1, -1, -1):
        current, following = scheduled[i:i + 2]
        latest_end = following.start - timedelta(minutes=following.drive.duration_minutes)
        current.start = max(
            (min(closes, latest_end) - current.visit.duration for opens, closes in current.visit.windows
             if min(closes, latest_end) - current.visit.duration >= max(opens, current.start)),
            default=current.start,
        )

    final_route = get_route(previous_location, destination)
    if final_route is None:
        return DaySchedule(scheduled, None, "마지막 목적지까지 자동차 경로를 찾지 못했습니다.")
    if previous_end + timedelta(minutes=final_route.duration_minutes) >= day_end:
        return DaySchedule(scheduled, None, "예상 이동시간을 반영하면 당일 마지막 목적지에 도착하기 어렵습니다.")
    return DaySchedule(scheduled, final_route)


def schedule_car_trip(days: list[dict], places: dict[str, Place], origin: Coordinates, festival_location: Coordinates,
                      api_key: str, warnings: list[dict], conflicts: list[dict],
                      lodging: tuple[Coordinates, dict, dict] | None = None, route_loader=None) -> bool:
    """고정 공연을 기준으로 날짜별 방문을 배치하고 숙소와 원출발지를 연결한다."""
    route_cache: dict[tuple[Coordinates, Coordinates], CarRoute | None] = {}
    if route_loader is None and len(days) == 1 and days[0]["legs"]:
        initial_leg = days[0]["legs"][0]
        route_cache[(origin, festival_location)] = CarRoute(
            initial_leg["durationMinutes"] * 60, initial_leg["distanceMeters"],
        )

    def get_route(start: Coordinates, end: Coordinates) -> CarRoute | None:
        if start == end:
            return CarRoute(0, 0)
        key = (start, end)
        if key not in route_cache:
            route_cache[key] = (route_loader or get_car_route)(api_key, start, end)
        return route_cache[key]

    place_stops = [stop for day in days for stop in day["stops"] if stop["type"] == "PLACE"]
    places = prepare_draft_places(places, place_stops, warnings)
    draft_mode = any(stop.get("scheduleAssumptions") for stop in place_stops)
    simulation_mode = any(stop.get("dataMode") == "SIMULATION" or stop.get("visitWindow") for day in days for stop in day["stops"])
    contexts = []
    program_ids = []
    for index, day in enumerate(days):
        day_start = datetime.combine(date.fromisoformat(day["date"]), time.min, KST)
        day_end = day_start + timedelta(days=1)
        has_program = any(stop["type"] == "PROGRAM" for stop in day["stops"])
        if (draft_mode or simulation_mode) and not has_program:
            day_start += timedelta(hours=9)
        start_location = destination = origin
        start_lodging = end_lodging = None
        if lodging is not None:
            lodging_location, arrival_stop, departure_stop = lodging
            if index == 0:
                destination, end_lodging = lodging_location, arrival_stop
            else:
                start_location, start_lodging = lodging_location, departure_stop

        order = []
        day_program_ids = []
        for stop in day["stops"]:
            if stop["type"] == "FESTIVAL":
                duration = (timedelta(minutes=stop.get("visitDurationMinutes", 120))
                            if (draft_mode or stop.get("visitWindow")) and not has_program else timedelta())
                festival_windows = [(day_start, day_end)]
                if duration:
                    window = stop.get("visitWindow", {"open": "10:00", "close": "20:00"})
                    festival_windows = [(datetime.combine(day_start.date(), time.fromisoformat(window["open"]), KST),
                                         datetime.combine(day_start.date(), time.fromisoformat(window["close"]), KST))]
                    if stop.get("dataMode") != "SIMULATION" and stop.get("scheduleSource") != "VERIFIED":
                        stop["scheduleAssumptions"] = ["축제 방문은 10~20시 사이 120분으로 가정했습니다. 실제 운영시간 확인이 필요합니다."]
                        warnings.append({"code": "SCHEDULE_ASSUMPTION", "message": stop["scheduleAssumptions"][0],
                                         "stopIds": [stop["id"]]})
                order.append(Visit(stop, festival_location, duration, festival_windows))
            elif stop["type"] == "PROGRAM":
                start, end = datetime.fromisoformat(stop["startAt"]), datetime.fromisoformat(stop["endAt"])
                if not day_start <= start < end <= day_end:
                    conflicts.append({"code": "REQUIRED_CONSTRAINT_CONFLICT",
                                      "message": "필수 공연 시간이 여행 날짜를 벗어납니다.",
                                      "relatedPlaceIds": [], "relatedProgramIds": [stop["programId"]],
                                      "suggestedChanges": []})
                    return False
                day_program_ids.append(stop["programId"])
                order.append(Visit(stop, festival_location, end - start, [(start, end)]))

        if order and get_route(start_location, order[0].location) is None:
            warnings.append({"code": "LODGING_ROUTE_UNAVAILABLE" if start_lodging else "ROUTE_UNAVAILABLE",
                             "message": f"{index + 1}일차 첫 방문지까지 자동차 경로를 찾지 못했습니다.",
                             "stopIds": [order[0].stop["id"]]})
            return False
        schedule = evaluate_order(order, start_location, destination, get_route, day_start, day_end)
        if schedule is None:
            conflicts.append({"code": "REQUIRED_CONSTRAINT_CONFLICT",
                              "message": f"{index + 1}일차 필수 공연 시작 시간까지 도착하기 어렵습니다.",
                              "relatedPlaceIds": [], "relatedProgramIds": day_program_ids, "suggestedChanges": []})
            return False
        contexts.append(DayContext(day, start_location, destination, start_lodging, end_lodging,
                                   day_start, day_end, order, schedule))
        program_ids.extend(day_program_ids)

    windows_by_id = {
        stop["id"]: [
            [] if context.day["date"] in (place.closed_dates or []) else opening_windows(place, context.day_start.date())
            for context in contexts
        ]
        for stop in place_stops
        for place in [places[stop["placeId"]]]
    }
    trip_end = contexts[-1].day_end
    # 마감이 빠른 필수 장소에 먼저 자리를 준다. 추천 장소는 Gemini 순위를 유지한다.
    for stop in sorted(place_stops, key=lambda s: (
        not s["required"], max((closes for windows in windows_by_id[s["id"]] for _, closes in windows), default=trip_end)
        if s["required"] else trip_end,
    )):
        place = places[stop["placeId"]]
        day_windows = windows_by_id[stop["id"]]
        reason = None
        is_conflict = False
        if all(context.day["date"] in (place.closed_dates or []) for context in contexts):
            reason, is_conflict = "여행 날짜에 모두 휴무입니다.", True
        elif not any(day_windows) or not place.stay_minutes_estimate or place.stay_minutes_estimate < 1:
            reason = "영업시간 또는 예상 체류시간을 확인할 수 없습니다."
        elif not insert_place(contexts, stop, place, day_windows, get_route):
            reason, is_conflict = "영업시간·체류시간·이동시간과 마지막 도착 조건을 맞춰 배치하지 못했습니다.", True

        if reason is not None:
            message = f"{stop['name']}: {reason}"
            if stop["required"] and is_conflict:
                conflicts.append({"code": "REQUIRED_CONSTRAINT_CONFLICT", "message": message,
                                  "relatedPlaceIds": [place.id], "relatedProgramIds": program_ids,
                                  "suggestedChanges": []})
            else:
                warnings.append({"code": "PLACE_SCHEDULE_UNCONFIRMED", "message": message, "stopIds": [stop["id"]]})

    scheduled_ids = {item.visit.stop["id"] for context in contexts for item in context.schedule.visits}
    unplaced = [stop for stop in place_stops if stop["id"] not in scheduled_ids]
    for index, context in enumerate(contexts):
        save_day_schedule(context, unplaced if index == 0 else [], warnings)
    warnings.append({"code": "ROUTE_ESTIMATED", "message": "자동차 이동시간은 경로를 조회한 시점 기준 예상치입니다.", "stopIds": []})
    return True


def insert_place(contexts: list[DayContext], stop: dict, place: Place,
                 day_windows: list[list[tuple[datetime, datetime]]],
                 get_route: Callable[[Coordinates, Coordinates], CarRoute | None]) -> bool:
    partial = None
    best = None
    for context, windows in zip(contexts, day_windows):
        if not windows:
            continue
        visit = Visit(stop, (place.lat, place.lng), timedelta(minutes=place.stay_minutes_estimate), windows)
        order = context.order
        first_index = int(bool(order) and order[0].stop["type"] == "FESTIVAL"
                          and not order[0].stop.get("scheduleAssumptions")
                          and order[0].stop.get("dataMode") != "SIMULATION"
                          and not order[0].stop.get("visitWindow"))
        for index in range(first_index, len(order) + 1):
            candidate_order = order[:index] + [visit] + order[index:]
            candidate = evaluate_order(candidate_order, context.start, context.destination, get_route,
                                       context.day_start, context.day_end)
            if candidate is None:
                continue
            if candidate.final_route is not None:
                if stop.get("scheduleAssumptions") or stop.get("dataMode") == "SIMULATION":
                    def travel_seconds(schedule):
                        return sum(item.drive.duration_seconds for item in schedule.visits) + (
                            schedule.final_route.duration_seconds if schedule.final_route else 0)
                    score = (travel_seconds(candidate) - travel_seconds(context.schedule),
                             context.day["date"], candidate.visits[-1].end)
                    if best is None or score < best[0]:
                        best = (score, context, candidate_order, candidate)
                    continue
                context.order, context.schedule = candidate_order, candidate
                return True
            if context.schedule.final_route is None and partial is None:
                partial = (context, candidate_order, candidate)
    if best is not None:
        _, context, order, schedule = best
        context.order, context.schedule = order, schedule
        return True
    if partial is None:
        return False
    context, order, schedule = partial
    context.order, context.schedule = order, schedule
    return True


def save_day_schedule(context: DayContext, unplaced: list[dict], warnings: list[dict]) -> None:
    day, schedule = context.day, context.schedule
    day["stops"] = (
        ([context.start_lodging] if context.start_lodging else [])
        + [item.visit.stop for item in schedule.visits] + unplaced
        + ([context.end_lodging] if context.end_lodging else [])
    )
    day["legs"] = []
    has_timed_visit = any(item.visit.duration > timedelta() for item in schedule.visits)
    location = context.start
    previous_id = context.start_lodging["id"] if context.start_lodging else "ORIGIN"
    for index, item in enumerate(schedule.visits):
        departure = item.start - timedelta(minutes=item.drive.duration_minutes) if has_timed_visit else None
        arrival = item.start if has_timed_visit else None
        stop = item.visit.stop
        if location != item.visit.location:
            day["legs"].append(item.drive.as_leg(previous_id, stop["id"], departure, arrival))
        if index == 0 and context.start_lodging:
            context.start_lodging["startAt"] = iso(departure)
        if stop["type"] == "PLACE" or (stop["type"] == "FESTIVAL" and item.visit.duration > timedelta()):
            stop.update(startAt=iso(item.start), endAt=iso(item.end))
        location, previous_id = item.visit.location, stop["id"]

    if schedule.final_route is None:
        warnings.append({"code": "LODGING_ROUTE_UNAVAILABLE" if context.end_lodging else "RETURN_ROUTE_UNAVAILABLE",
                         "message": schedule.final_issue,
                         "stopIds": [] if previous_id == "ORIGIN" else [previous_id]})
        return

    departure = schedule.visits[-1].end if has_timed_visit else None
    arrival = departure + timedelta(minutes=schedule.final_route.duration_minutes) if departure else None
    destination_id = context.end_lodging["id"] if context.end_lodging else "ORIGIN"
    if location != context.destination:
        day["legs"].append(schedule.final_route.as_leg(previous_id, destination_id, departure, arrival))
    if context.end_lodging:
        context.end_lodging["startAt"] = iso(arrival)
