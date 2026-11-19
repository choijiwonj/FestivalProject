"""Calculate verified and estimated totals separately, including place visits."""
from math import ceil
from simulation_data import config, enabled


def calculate_budget(inp, days):
    people = inp["travelers"]
    settings = config()["budget"] if enabled() else {}
    items = []

    def add(category, unit, quantity=1, basis="GROUP", status="UNKNOWN", label=None):
        items.append({"category": category, "unitAmountKrw": unit,
                      "amountKrw": unit * quantity if unit is not None else None,
                      "basis": basis, "quantity": quantity, "status": status if unit is not None else "UNKNOWN",
                      "label": label or category})

    legs = [leg for day in days for leg in day.get("legs", [])]
    if enabled():
        if inp["transport"] == "CAR":
            km = sum(leg.get("distanceMeters") or 0 for leg in legs) / 1000
            cost = ceil((km * settings["carKrwPerKm"] + len(days) * (
                settings["parkingKrwPerDay"] + settings["tollAllowanceKrwPerDay"])) / 100) * 100 if legs else len(days) * settings["carDailyAllowanceKrw"]
            add("TRANSPORT", cost, status="ESTIMATED", label="차량 운행·주차·통행료 예상")
        else:
            add("TRANSPORT", settings["transitKrwPerPersonPerDay"], people * len(days), "PERSON_DAY", "ESTIMATED", "대중교통 예상")
    else:
        add("TRANSPORT", None, label="교통비")

    if len(days) > 1 and inp["lodgingPreference"] != "SELF_ARRANGED":
        hotel = next((stop for day in days for stop in day["stops"] if stop["type"] == "LODGING"), None)
        price = hotel.get("costKrw") if hotel else None
        basis = hotel.get("costBasis", "ROOM") if hotel else "ROOM"
        status = hotel.get("costStatus", "UNKNOWN") if hotel else "UNKNOWN"
        if price is None and enabled():
            price, status = config()["categories"]["LODGING"]["amountKrw"], "ESTIMATED"
        quantity = len(days) - 1
        capacity = settings.get("roomCapacity", 2)
        quantity *= people if basis == "PERSON" else ceil(people / capacity)
        rooms_assumed = basis != "PERSON" and people > capacity
        if rooms_assumed and status == "VERIFIED":
            status = "ESTIMATED"  # 객실 수를 인원으로 추정했으므로 확인된 합계에 넣지 않는다.
        label = "숙박 예상" if status == "ESTIMATED" else "숙박비"
        add("LODGING", price, quantity, basis, status, f"{label} ({capacity}인 1실 가정)" if rooms_assumed else label)

    for day in days:
        restaurants = 0
        for stop in day["stops"]:
            if stop["type"] == "LODGING":
                continue
            if stop["type"] == "PLACE" and not stop.get("startAt"):
                continue
            category = stop.get("costCategory", "ADMISSION")
            if category == "RESTAURANT":
                restaurants += 1
            basis = stop.get("costBasis", "PERSON")
            add(category, stop.get("costKrw"), people if basis == "PERSON" else 1,
                basis, stop.get("costStatus", "UNKNOWN"), stop["name"])
        if enabled():
            meals = max(0, settings["mealsPerDay"] - restaurants)
            if meals:
                add("FOOD", settings["mealKrwPerPerson"], meals * people, "PERSON_MEAL", "ESTIMATED", f"{day['date']} 식사 예산")

    known = sum(item["amountKrw"] for item in items if item["amountKrw"] is not None and item["status"] == "VERIFIED")
    has_estimates = any(item["status"] == "ESTIMATED" for item in items)
    total = sum(item["amountKrw"] or 0 for item in items)
    unknown = sum(item["amountKrw"] is None for item in items)
    limit = inp["budgetKrw"]
    status = "NOT_SET" if limit is None else "OVER" if total > limit else "UNKNOWN" if unknown else "WITHIN"
    return {"currency": "KRW", "scope": "GROUP_TOTAL", "limitKrw": limit, "knownTotalKrw": known,
            "estimatedTotalKrw": total if has_estimates else None, "unknownItemCount": unknown,
            "status": status, "items": items, "lodgingIncluded": inp["lodgingPreference"] != "SELF_ARRANGED"}
