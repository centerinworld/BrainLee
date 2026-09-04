#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

from collectors.dart_dilution_collector import collect_dilution_events
from collectors.dart_equity_issue_collector import collect_equity_issue_events
from collectors.kiwoom_margin_collector import collect_kiwoom_margin_daily
from collectors.dart_backlog_collector import collect_backlog_quarterly
from collectors.dart_cost_collector import collect_cost_quarterly


def _dart_status() -> dict:
    out = {}
    for name, key in [("key1", config.DART_API_KEY), ("key2", config.DART_API_KEY2), ("key3", config.DART_API_KEY3)]:
        if not key:
            out[name] = {"status": "NONE", "message": "missing"}
            continue
        try:
            r = requests.get(
                "https://opendart.fss.or.kr/api/company.json",
                params={"crtfc_key": key, "corp_code": "00126380"},
                timeout=10,
            )
            j = r.json()
            out[name] = {"status": j.get("status"), "message": j.get("message")}
        except Exception as e:
            out[name] = {"status": "ERR", "message": str(e)}
    return out


def main() -> None:
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "dart_status": _dart_status(),
        "steps": {},
    }

    report["steps"]["dilution_events"] = collect_dilution_events(days=365)
    report["steps"]["equity_issue_events"] = collect_equity_issue_events(since=(datetime.now().replace(year=datetime.now().year - 1)).date().isoformat(), missing_only=True)
    report["steps"]["margin_balance_daily"] = collect_kiwoom_margin_daily(limit=500)

    # DART quota 정상(000)인 경우만 본문 대량 파서 실행
    statuses = [v.get("status") for v in report["dart_status"].values()]
    if "000" in statuses:
        y_to = datetime.now().year
        report["steps"]["order_backlog"] = collect_backlog_quarterly(year_from=2020, year_to=y_to, limit=None, report_type="CFS")
        report["steps"]["order_backlog_missing_retry"] = collect_backlog_quarterly(
            year_from=2020,
            year_to=y_to,
            limit=None,
            report_type="CFS",
            missing_only=True,
            eligible_only=True,
        )
        report["steps"]["cost_structure"] = collect_cost_quarterly(year_from=y_to - 5, year_to=y_to, limit=None, report_type="CFS")
    else:
        report["steps"]["order_backlog"] = {"skipped": True, "reason": "dart_quota_exceeded"}
        report["steps"]["cost_structure"] = {"skipped": True, "reason": "dart_quota_exceeded"}

    out = f"/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/business_signal_collectors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(out)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
