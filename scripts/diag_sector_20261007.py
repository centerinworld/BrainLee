#!/usr/bin/env python3
"""Stock_Strategy §24-2: sector_focus '첫 갈림일 추적' — 기준선 vs 조정을 같은 데이터에서 실행, 리밸런싱일마다 섹터 점수·BUY 섹터·리더 후보 순위·보유·현금을 비교. DB 기록 없음."""
from __future__ import annotations
import json, sys, uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_strategies.sector as sec  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402

PERIODS = {"20.3~21.11": ("2020-03-01", "2021-11-30")}


class Spy:
    def __init__(self, real, sink):
        self._r, self._s = real, sink

    def execute(self, sql, params=()):
        if "status='done'" in sql and "trades_json" in sql:
            self._s["p"] = params
            return None
        return self._r.execute(sql, params)

    def __getattr__(self, n):
        return getattr(self._r, n)


def run(label, adjusted):
    s, e = PERIODS[label]
    sink = {}
    real = sec.sqlite3.connect
    rid = "diag_" + uuid.uuid4().hex[:6]
    sec.SEC_DIAG = []
    try:
        with mock.patch.object(sec, "_record_run_spec", lambda *a, **k: None), \
             mock.patch.object(sec, "_register_execution_artifacts", lambda *a, **k: None), \
             mock.patch.object(sec.sqlite3, "connect", lambda *a, **k: Spy(real(*a, **k), sink)):
            sec.run_backtest_sector(s, e, adjusted_prices=adjusted, run_id=rid)
        diag = sec.SEC_DIAG
    finally:
        sec.SEC_DIAG = None
        c = connect_stock_db(); c.execute("DELETE FROM backtest_runs WHERE run_id=?", (rid,)); c.commit(); c.close()
    p = sink["p"]
    return {"ret": p[1], "trades": json.loads(p[5])["trades"], "diag": diag}


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "20.3~21.11"
    base = run(label, False); adj = run(label, True)
    out = {"label": label, "base": base, "adj": adj}
    Path(f"/private/tmp/claude-501/-Volumes-Realtek-NVME-stock-dashboard/4cc0e7b8-d4e0-4170-8e41-c86dda150a5f/scratchpad/sec_diag_{label}.json").write_text(json.dumps(out, ensure_ascii=False, default=str))
    print("ret", base["ret"], adj["ret"])
