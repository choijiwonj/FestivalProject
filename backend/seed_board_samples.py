"""Run explicitly to insert four labelled samples; repeat runs keep existing posts."""
import json
import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from db import BoardPost, Festival, SessionLocal, engine


def seed_samples(db, refresh_samples=False):
    items = json.loads((Path(__file__).parent / "data" / "board_samples.json").read_text(encoding="utf-8"))
    added = 0
    unmatched = []
    for item in items:
        existing = db.get(BoardPost, item["id"])
        if existing and not (refresh_samples and existing.is_sample):
            continue
        try:
            festival = db.scalar(select(Festival).where(Festival.name.contains(item["festivalKeyword"]),
                                                        Festival.region == item["region"])
                                 .order_by(Festival.start_date.desc(), Festival.id).limit(1))
            if festival is None:
                unmatched.append(item["id"])
            values = dict(
                id=item["id"], festival_id=festival.id if festival else None,
                festival_name=festival.name if festival else item["festivalName"], region=item["region"],
                author_name=item["author"], title=item["title"], body=item["body"], is_sample=True,
                visited_on=date.fromisoformat(item["visitedOn"]),
                created_at=datetime.fromisoformat(item["createdAt"]).astimezone(timezone.utc).replace(tzinfo=None),
            )
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid sample {item.get('id')}: {error}") from None
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            db.add(BoardPost(**values))
        added += 1
    db.commit()
    if unmatched:
        print(f"Warning: {len(unmatched)} sample(s) saved without festival link:", ", ".join(unmatched))
    return added


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-samples", action="store_true", help="Refresh only the four labelled sample IDs")
    args = parser.parse_args()
    try:
        BoardPost.__table__.create(engine, checkfirst=True)
        if engine.dialect.name == "postgresql":
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE board_posts ENABLE ROW LEVEL SECURITY"))
                for role in ("anon", "authenticated"):
                    if connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                        connection.execute(text(f"REVOKE ALL ON TABLE board_posts FROM {role}"))
        with SessionLocal() as db:
            print("Saved labelled sample posts:", seed_samples(db, refresh_samples=args.refresh_samples))
    except Exception as error:
        print("Board seed failed:", str(error) if isinstance(error, (ValueError, KeyError)) else type(error).__name__)
        raise SystemExit(1)
