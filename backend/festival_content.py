"""Reviewed festival metadata and date-specific hours; no provider calls at request time."""
import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def profiles():
    path = Path(__file__).parent / "data/festival_content.json"
    if not path.exists():
        # 저장소에 포함된 파일이므로 없으면 배포 누락이다. 앱은 보충 자료 없이 계속 동작한다.
        log.error("festival content file missing: %s", path)
        return {}
    return {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))["festivals"]}


def detail_extras(festival_id):
    item = profiles().get(festival_id, {})
    return {key: item[key] for key in ("highlights", "programNotice", "sources", "contact") if key in item}


def verified_visit_window(festival_id, day):
    key = day[5:10]
    for period in profiles().get(festival_id, {}).get("visitSchedule", []):
        start, end = period["from"], period["to"]
        if (start <= key <= end) if start <= end else (key >= start or key <= end):
            return {"open": period["open"], "close": period["close"]}
    return None
