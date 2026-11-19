"""Update the three reviewed festival records atomically, backing up old fields first."""
import json
from datetime import datetime, timezone
from pathlib import Path
from db import Festival, SessionLocal

FIELDS = ("description", "venue", "opening_hours", "admission", "directions", "shuttle", "source_url", "last_verified_at")


def update_content():
    root = Path(__file__).parent
    items = json.loads((root / "data/festival_content.json").read_text(encoding="utf-8"))["festivals"]
    checked = datetime.now(timezone.utc).replace(tzinfo=None)
    backup = []
    with SessionLocal() as db:
        for item in items:
            row = db.get(Festival, item["id"])
            if row is None or row.name != item["name"]:
                raise ValueError("Festival ID/name mismatch; nothing committed")
            backup.append({"id": row.id, **{field: getattr(row, field) for field in FIELDS}})
            row.description = item["description"]
            row.venue = {**(row.venue or {}), "label": item["venueLabel"]}
            row.opening_hours = item["openingHours"]
            row.admission = {**item["admission"], "lastVerifiedAt": checked.isoformat() + "Z"}
            for field in ("directions", "shuttle"):
                setattr(row, field, {**item[field], "lastVerifiedAt": checked.isoformat() + "Z"})
            row.source_url = item["sourceUrl"]
            row.last_verified_at = checked
        backup_dir = root / "content_backups"
        backup_dir.mkdir(exist_ok=True)
        file = backup_dir / f"festival_details_{checked.strftime('%Y%m%d_%H%M%S_%f')}.json"
        file.write_text(json.dumps(backup, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        db.commit()
    return len(items)


if __name__ == "__main__":
    try:
        print("Updated reviewed festival details:", update_content())
    except Exception as error:
        print("Content update failed:", type(error).__name__)
        raise SystemExit(1)
