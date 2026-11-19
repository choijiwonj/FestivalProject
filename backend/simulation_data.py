"""Opt-in non-persistent catalog overlay for service demonstrations."""
import json
import os
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from festival_content import verified_visit_window

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def enabled():
    return os.getenv("SIMULATION_DATA", "0") == "1"


@lru_cache(maxsize=1)
def config():
    return json.loads((Path(__file__).parent / "data/simulation_catalog.json").read_text(encoding="utf-8"))


def clone(row):
    return SimpleNamespace(**{column.key: getattr(row, column.key) for column in row.__table__.columns})


def place_for_plan(row):
    if row is None or not enabled():
        return row
    data = clone(row)
    settings = dict(config()["categories"].get(row.category, config()["categories"]["ATTRACTION"]))
    compact_name = row.name.replace(" ", "")
    for override in config()["placeOverrides"]:
        if override["nameContains"] in compact_name and override.get("category", row.category) == row.category:
            settings.update(override)
            break
    data.simulation = False
    if not row.opening_hours:
        data.opening_hours = {day: [{"open": settings["open"], "close": settings["close"]}] for day in WEEKDAYS}
        data.simulation = True
    if not row.stay_minutes_estimate:
        data.stay_minutes_estimate = settings["stayMinutes"]
        data.simulation = True
    if not row.price or row.price.get("amountKrw") is None:
        data.price = {"amountKrw": settings["amountKrw"], "basis": settings["basis"],
                      "status": "ESTIMATED", "sourceType": "SIMULATION"}
        data.simulation = True
    data.planning_basis = f"체류 {data.stay_minutes_estimate}분"
    if not row.opening_hours:
        data.planning_basis = f"방문 기준 {settings['open']}~{settings['close']} · " + data.planning_basis
    return data


def festival_for_plan(row):
    if not enabled():
        return row
    data = clone(row)
    if not row.admission or row.admission.get("amountKrw") is None:
        data.admission = {"amountKrw": config()["festival"]["amountKrw"], "basis": "PERSON", "status": "ESTIMATED"}
    return data


def enrich_stops(db, days, festival):
    from db import Place
    for day in days:
        for stop in day["stops"]:
            if stop["type"] == "FESTIVAL":
                stop["costBasis"] = (festival.admission or {}).get("basis", "PERSON")
            if stop["type"] == "FESTIVAL":
                verified_window = verified_visit_window(festival.id, day["date"])
                if verified_window:
                    stop.update(visitWindow=verified_window, visitDurationMinutes=120, scheduleSource="VERIFIED")
                elif enabled():
                    settings = config()["festival"]
                    stop.update(dataMode="SIMULATION", visitDurationMinutes=settings["stayMinutes"],
                                visitWindow={"open": settings["open"], "close": settings["close"]})
            if stop["type"] == "PROGRAM" and enabled() and stop["costKrw"] is None:
                stop.update(costKrw=0, costStatus="ESTIMATED", dataMode="SIMULATION")
            if stop["type"] != "PLACE":
                continue
            place = place_for_plan(db.get(Place, stop["placeId"]))
            if not place:
                continue
            price = place.price or {}
            if price.get("amountKrw") is not None:
                stop.update(costKrw=price["amountKrw"], costStatus=price.get("status", "ESTIMATED"),
                            costBasis=price.get("basis", "PERSON"), costCategory=place.category)
            if getattr(place, "simulation", False):
                stop.update(dataMode="SIMULATION", planningBasis=place.planning_basis)


def lodging_cost(place):
    if not place:
        return {}
    price = place.price or {}
    return {"costKrw": price.get("amountKrw"), "costStatus": price.get("status", "ESTIMATED" if price.get("amountKrw") is not None else "UNKNOWN"),
            "costBasis": price.get("basis", "ROOM")}
