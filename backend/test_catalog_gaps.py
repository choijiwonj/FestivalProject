import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

for key, value in {"DATABASE_URL": f"sqlite:///{tempfile.mkdtemp()}/test.db", "DEV_LOGIN": "1",
                   "SEED_DEMO": "1", "SECRET_KEY": "test-secret"}.items():
    os.environ.setdefault(key, value)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from db import Base, Festival, Place, Program  # noqa: E402
from importer import ImportFailed, run_import  # noqa: E402
from main import app  # noqa: E402

SAMPLE = json.loads((Path(__file__).parent / "data" / "sample_import.json").read_text(encoding="utf-8"))


@pytest.fixture
def tmp_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/import.db")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def counts(db):
    return [db.scalar(select(func.count()).select_from(m)) for m in (Festival, Program, Place)]


def test_import_sample_is_idempotent(tmp_db):
    first = run_import(tmp_db, SAMPLE)
    assert first["festivals"] == {"created": 1, "updated": 0} and first["places"]["created"] == 2
    assert counts(tmp_db) == [1, 2, 2]
    again = run_import(tmp_db, SAMPLE)
    assert again["programs"] == {"created": 0, "updated": 2}
    assert counts(tmp_db) == [1, 2, 2]
    program = tmp_db.get(Program, "pr_sample_01")
    assert program.start_at == datetime(2027, 8, 20, 11, 0)  # KST 20:00 → UTC naive 11:00
    assert tmp_db.get(Place, "pl_sample_01").closed_dates == ["2027-08-21"]


def test_dry_run_writes_nothing(tmp_db):
    assert run_import(tmp_db, SAMPLE, dry_run=True)["festivals"]["created"] == 1
    assert counts(tmp_db) == [0, 0, 0]


@pytest.mark.parametrize(("patch", "message"), [
    (lambda d: d["places"][1].update(category="BAR"), "places[1].category"),
    (lambda d: d["festivals"][0].update(unknown=1), "festivals[0].unknown"),
    (lambda d: d["programs"][0].update(festival_id="fst_none"), "programs[0].festival_id"),
])
def test_invalid_record_rejected_without_partial_write(tmp_db, patch, message):
    data = json.loads(json.dumps(SAMPLE))
    patch(data)
    with pytest.raises(ImportFailed, match=message.replace("[", r"\[").replace("]", r"\]")):
        run_import(tmp_db, data)
    assert counts(tmp_db) == [0, 0, 0]


def test_festivals_bbox_and_sql_filters():
    with TestClient(app, base_url="https://testserver") as c:
        hit = c.get("/api/v1/festivals", params={"bbox": "126.2,36.7,126.4,36.8"}).json()["data"]
        assert [f["id"] for f in hit] == ["fst_demo_01"]  # fst_demo_02는 venue 없음
        assert hit[0]["venue"]["lat"] == 36.745
        assert c.get("/api/v1/festivals", params={"bbox": "127,36,128,37"}).json()["data"] == []
        assert c.get("/api/v1/festivals", params={"bbox": "1,2,3"}).json()["error"]["code"] == "INVALID_BBOX"
        assert c.get("/api/v1/festivals", params={"bbox": "120,30,130,40"}).status_code == 400
        assert [f["id"] for f in c.get("/api/v1/festivals", params={"q": "태안"}).json()["data"]] == ["fst_demo_01"]
        assert [f["id"] for f in c.get("/api/v1/festivals", params={"q": "불꽃"}).json()["data"]] == ["fst_demo_02"]
        assert c.get("/api/v1/festivals", params={"q": "%"}).json()["data"] == []
