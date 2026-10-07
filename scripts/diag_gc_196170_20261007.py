#!/usr/bin/env python3
"""Stock_Strategy §22-3: golden_cross '첫 갈림일 추적' — 기준선 vs 조정을 같은 데이터에서 실행해 매 거래일 후보·순위·체결을 비교한다.
DB 기록 없음(결과 UPDATE·spec 기록을 가로챈다). 사용: python3 scripts/diag_gc_196170_20261007.py 20.3~21.11 [196170]"""
from __future__ import annotations
import json, sys, uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
import backtest_strategies.golden_cross as gc  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from run_registry import source_snapshot  # noqa: E402

PERIODS = {"20.3~21.11": ("2020-03-01", "2021-11-30"), "21.12~22.10": ("2021-12-01", "2022-10-31"),
           "22.11~23.10": ("2022-11-01", "2023-10-31"), "23.11~24.12": ("2023-11-01", "2024-12-31")}


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


def run(label, adjusted, no_block=False, watch=()):
    s, e = PERIODS[label]
    sink = {}
    real = gc.sqlite3.connect
    rid = "diag_" + uuid.uuid4().hex[:6]
    c = connect_stock_db()
    c.execute("INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) VALUES(?,?,?,?,?,?,'running')",
              (rid, "gc-diag", "golden_cross", s, e, 10_000_000)); c.commit(); c.close()
    gc.GC_DIAG = []; gc.GC_DIAG_WATCH = set(watch); gc.GC_DIAG_NO_BLOCK = no_block
    try:
        with mock.patch.object(gc, "_record_run_spec", lambda *a, **k: None), \
             mock.patch.object(gc, "_register_execution_artifacts", lambda *a, **k: None), \
             mock.patch.object(gc.sqlite3, "connect", lambda *a, **k: Spy(real(*a, **k), sink)):
            gc.run_backtest_golden_cross(s, e, adjusted_prices=adjusted, run_id=rid)
        diag = gc.GC_DIAG
    finally:
        gc.GC_DIAG = None; gc.GC_DIAG_WATCH = set(); gc.GC_DIAG_NO_BLOCK = False
        c = connect_stock_db(); c.execute("DELETE FROM backtest_runs WHERE run_id=?", (rid,)); c.commit(); c.close()
    p = sink["p"]
    return {"ret": p[0], "trades": json.loads(p[6]), "diag": diag}


def snap():
    from db_compat import connect_primary_db
    c = connect_primary_db(timeout=300)
    out = [source_snapshot(c, start_date='2020-01-01', end_date='2026-03-31').get("fingerprint"), bc._data_revision_extras(c)]
    c.close()
    return json.dumps(out, sort_keys=True, default=str)


if __name__ == "__main__":
    label = sys.argv[1]; watch = sys.argv[2:] or ["196170"]
    f0 = snap()
    base = run(label, False, watch=watch)
    adj = run(label, True, watch=watch)
    f1 = snap()
    nb = run(label, True, no_block=True, watch=watch)
    f2 = snap()
    out = {"label": label, "data_same": f0 == f1 == f2, "base": base, "adj": adj, "noblock": nb}
    Path(f"/private/tmp/claude-501/-Volumes-Realtek-NVME-stock-dashboard/4cc0e7b8-d4e0-4170-8e41-c86dda150a5f/scratchpad/gc_diag_{label}.json").write_text(json.dumps(out, ensure_ascii=False, default=str))
    print("data_same", out["data_same"], "ret", base["ret"], adj["ret"], nb["ret"], "trades", len(base["trades"]), len(adj["trades"]), len(nb["trades"]))
