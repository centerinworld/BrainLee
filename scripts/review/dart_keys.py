"""DART 일괄 수집용 키 순서·KEY2 상한(2026-10-03 사용자 지시: "키 4개를 순차적으로 모두 사용").

순서: KEY1 → KEY3 → KEY4 → KEY2. 한 키의 일일 한도(status 020)가 차면 다음 키로 넘어간다.
KEY2는 운영 공시 수집(스케줄러)이 매일 쓰는 키라 마지막에 쓰고, 일괄 작업 사용량을 하루 KEY2_BULK_DAILY_CAP(기본 10,000)건으로 제한한다.
사용량은 프로세스 간 공유 파일(data/dart_key2_bulk_usage.json)에 날짜별로 센다.
"""
import fcntl
import json
import os
from datetime import date
from pathlib import Path

import config

USAGE = Path(__file__).resolve().parents[2] / "data" / "dart_key2_bulk_usage.json"
CAP = int(os.getenv("KEY2_BULK_DAILY_CAP", "10000"))
KEY2 = getattr(config, "DART_API_KEY2", None)


def ordered_keys():
    ks = [config.DART_API_KEY, getattr(config, "DART_API_KEY3", None), getattr(config, "DART_API_KEY4", None), KEY2]
    return list(dict.fromkeys(k for k in ks if k))


def allow(key):
    """호출 직전에 부른다. KEY2면 공유 카운터를 올리고 상한을 넘으면 False."""
    if key != KEY2:
        return True
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        try:
            d = json.loads(fh.read() or "{}")
        except ValueError:
            d = {}
        today = date.today().isoformat()
        n = d.get(today, 0)
        if n >= CAP:
            return False
        d = {today: n + 1}
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(d))
    return True
