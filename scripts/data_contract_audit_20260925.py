#!/usr/bin/env python3
"""HANDOFF §12 R9: daily data-contract audit for the defect types found repeatedly in 2026-09 -> data_contract_check_log (+ Telegram and data_fix_log on FAIL).

  default_missing      NEW tables (vs the stored baseline) whose created_at/updated_at columns have no PG DEFAULT (INSERTs that omit them would store NULL)
  cfs_ofs_mixed_ttm    (V2) valuation_history rows whose stored per_ttm/ttm_net_income does NOT equal the sum of four quarters of ONE basis (ttm_basis); FAIL if any.
                       Also reported: stocks whose latest 4 quarters mix CFS/OFS (informational; they get no TTM or a single-basis TTM by construction).
  snapshot_lookahead   (V3) real leakage measures: (a) share of stocks whose snapshot PER equals the SAME-quarter valuation_history.per (the legacy quarter-frozen value; leaked
                       original 99%, rebuilt v4 0%) - FAIL > 1%; (b) sampled snapshot PERs whose implied TTM only exists in a window not yet filed at snapshot_date - FAIL if any
  unit_inversion       (V4) stocks where market_cap(억) vs close*shares_issued/1e8 differ by >5x, CLASSIFIED: if the latest price_history close differs from the universe row's close by >=3x
                       the row is STALE (post-base_date reverse split / liquidation-trading crash), otherwise it is a real unit inversion. FAIL only for real inversions; the list with
                       causes is stored in detail.
  universe_fresh       stock_universe.base_date age in days (the universe stopped refreshing on 2026-09-04): warn > 5, fail > 10
  date_format          rows in the last 30 days violating the expected text-date format of key tables
  close_verify_fresh   the official-close comparison (price_close_verify_log) ran within 5 days and its last run was ok
Thresholds are in CHECKS. Idempotent per (check_date, check_name). Exit code 2 on any FAIL.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

DDL = """CREATE TABLE IF NOT EXISTS data_contract_check_log (
  check_date TEXT NOT NULL, check_name TEXT NOT NULL, status TEXT DEFAULT 'ok', value DOUBLE PRECISION DEFAULT NULL, threshold DOUBLE PRECISION DEFAULT NULL,
  detail TEXT DEFAULT '', created_at TIMESTAMP DEFAULT now(), PRIMARY KEY (check_date, check_name))"""
DATE_COLS = [("price_history", "date", r"^\d{4}-\d{2}-\d{2}"), ("valuation_history", "period_end", r"^\d{4}-\d{2}-\d{2}"),
             ("strategy_feature_snapshot", "snapshot_date", r"^\d{4}-\d{2}-\d{2}$"), ("short_sell_daily", "bas_dt", r"^\d{8}$"),
             ("kiwoom_credit_balance", "dt", r"^\d{8}$")]


def main() -> int:
    conn = connect_primary_db(timeout=300)
    conn.execute(DDL)
    conn.commit()
    today = date.today().isoformat()
    results = []   # (name, status, value, threshold, detail)

    # default_missing (baseline = table list stored in the previous run's detail)
    tabs = sorted({r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name IN ('created_at','updated_at') "
        "AND column_default IS NULL AND table_name NOT ILIKE '%backup%'").fetchall()})
    prev = conn.execute("SELECT detail FROM data_contract_check_log WHERE check_name='default_missing' ORDER BY check_date DESC LIMIT 1").fetchone()
    try:
        base = set(json.loads(prev[0])["tables"]) if prev and prev[0].startswith("{") else None
    except Exception:  # noqa: BLE001 - a truncated/corrupt baseline must not break the audit: treat as "no baseline" (first-run behaviour)
        base = None
    new = sorted(set(tabs) - base) if base is not None else []
    results.append(("default_missing", "fail" if new else "ok", float(len(new)), 0.0,
                    json.dumps({"tables": tabs, "new": new}, ensure_ascii=False)))

    # cfs_ofs_mixed_ttm (V2): consistency of the stored TTM with ONE statement basis
    fdq = {}
    for code, y, q, rt, ni in conn.execute("SELECT stock_code,year,quarter,report_type,net_income FROM financial_data "
                                           "WHERE is_annual IS FALSE AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL").fetchall():
        fdq.setdefault(code, {"CFS": {}, "OFS": {}})["CFS" if rt == "CFS" else "OFS"][int(y) * 4 + int(q)] = float(ni)
    lastyq = conn.execute("SELECT year, quarter FROM valuation_history WHERE per_ttm IS NOT NULL ORDER BY year DESC, quarter DESC LIMIT 1").fetchone()
    bad_rows, checked = [], 0
    for code, y, q, ttm, basis in conn.execute(
            "SELECT stock_code, year, quarter, ttm_net_income, ttm_basis FROM valuation_history WHERE per_ttm IS NOT NULL AND year >= ?",
            (int(lastyq[0]) - 1 if lastyq else 2025,)).fetchall():
        checked += 1
        k = int(y) * 4 + int(q)
        qs = fdq.get(code, {}).get(basis or "", {})
        if not basis or not all((k - i) in qs for i in range(4)) or abs(sum(qs[k - i] for i in range(4)) - float(ttm)) > max(1.0, abs(float(ttm)) * 1e-9):
            bad_rows.append(code)
    r = conn.execute("""WITH q AS (SELECT stock_code, year*4+quarter AS qi, report_type FROM financial_data
        WHERE is_annual IS FALSE AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL),
      pref AS (SELECT stock_code, qi, CASE WHEN bool_or(report_type='CFS') THEN 'CFS' ELSE min(report_type) END AS rt FROM q GROUP BY 1,2),
      lastq AS (SELECT stock_code, qi, rt, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY qi DESC) rn FROM pref)
      SELECT count(*) FILTER (WHERE mixed), count(*) FROM (SELECT stock_code, count(DISTINCT rt) > 1 AS mixed FROM lastq WHERE rn<=4 GROUP BY 1) s""").fetchone()
    results.append(("cfs_ofs_mixed_ttm", "fail" if bad_rows else "ok", float(len(bad_rows)), 0.0,
                    json.dumps({"inconsistent_ttm_rows": len(bad_rows), "checked": checked, "examples": bad_rows[:5],
                                "info_stocks_mixing_in_latest_4q": f"{r[0]}/{r[1]}"}, ensure_ascii=False)))

    # snapshot_lookahead (V3)
    look_detail, look_status, look_value = {}, "ok", 0.0
    yq = conn.execute("SELECT MAX(snapshot_date) FROM strategy_feature_snapshot WHERE snapshot_date IN "
                      "(SELECT period_end::text FROM valuation_history WHERE per IS NOT NULL)").fetchone()
    if yq and yq[0]:
        r = conn.execute("""SELECT count(*) FILTER (WHERE abs(a.per - v.per) <= abs(v.per) * 1e-4), count(*)
            FROM strategy_feature_snapshot a JOIN valuation_history v ON v.stock_code=a.stock_code AND v.period_end::text=a.snapshot_date
            WHERE a.snapshot_date=? AND a.per IS NOT NULL AND v.per IS NOT NULL""", (yq[0],)).fetchone()
        share = float(r[0]) / max(float(r[1]), 1) * 100
        look_detail["same_quarter_match"] = f"{r[0]}/{r[1]} at {yq[0]} ({share:.2f}%)"
        look_value = share
        if share > 1:
            look_status = "fail"
    import random
    random.seed(11)
    sample = conn.execute("""SELECT stock_code, snapshot_date, market_cap_억, per FROM strategy_feature_snapshot
        WHERE per IS NOT NULL AND market_cap_억 > 0 AND snapshot_date >= ? ORDER BY snapshot_date DESC LIMIT 4000""",
        ((date.today() - timedelta(days=400)).isoformat(),)).fetchall()
    sample = random.sample(sample, min(400, len(sample)))
    viol, tested = [], 0
    for code, sd, mc, per in sample:
        implied = float(mc) * 1e8 / float(per)
        sdt = date.fromisoformat(str(sd)[:10])
        match_ok = match_future = False
        for basis, qs in (fdq.get(code) or {}).items():
            for qi in list(qs):
                if not all((qi - i) in qs for i in range(4)):
                    continue
                if abs(sum(qs[qi - i] for i in range(4)) - implied) > abs(implied) * 0.03:
                    continue
                yy, qq = divmod(qi - 1, 4)
                qq += 1
                pe = date(yy + (1 if qq == 4 else 0), (qq * 3) % 12 + 1, 1) - timedelta(days=1)
                avail = pe + timedelta(days=90 if qq == 4 else 45)
                if avail <= sdt:
                    match_ok = True
                else:
                    match_future = True
        tested += 1
        if match_future and not match_ok:
            viol.append((code, str(sd)[:10]))
    look_detail["filing_lag_violations"] = f"{len(viol)}/{tested} sampled rows (examples {viol[:3]})"
    if viol:
        look_status = "fail"
        look_value = max(look_value, float(len(viol)))
    results.append(("snapshot_lookahead", look_status, round(look_value, 2), 1.0, json.dumps(look_detail, ensure_ascii=False)))

    # unit_inversion (V4) — classify each flagged stock instead of just counting
    flagged = conn.execute("""SELECT u.stock_code, u.stock_name, u.close, u.shares_issued, u.market_cap, u.base_date::text,
        u.market_cap*1e8/(u.close*u.shares_issued) AS ratio,
        (SELECT p.close FROM price_history p WHERE p.stock_code=u.stock_code AND p.close>0 ORDER BY p.date DESC LIMIT 1) AS px_now
        FROM stock_universe u WHERE u.base_date=(SELECT max(base_date) FROM stock_universe) AND u.shares_issued>0 AND u.close>0 AND u.market_cap>0
          AND (u.market_cap*1e8/(u.close*u.shares_issued) > 5 OR u.market_cap*1e8/(u.close*u.shares_issued) < 0.2)""").fetchall()
    total_u = conn.execute("SELECT count(*) FROM stock_universe WHERE base_date=(SELECT max(base_date) FROM stock_universe) AND shares_issued>0 AND close>0 AND market_cap>0").fetchone()[0]
    stale, real = [], []
    for code, name, cl, shs, mc, bd, ratio, px_now in flagged:
        move = (float(px_now) / float(cl)) if (px_now and cl) else None
        if move is not None and (move >= 3 or move <= 1 / 3):
            cause = "post-base_date reverse split (price x%.1f)" % move if move >= 3 else "liquidation-trading crash (price x%.3f)" % move
            stale.append({"code": code, "name": name, "ratio": round(float(ratio), 3), "cause": cause})
        else:
            real.append({"code": code, "name": name, "ratio": round(float(ratio), 3)})
    results.append(("unit_inversion", "fail" if len(real) / max(total_u, 1) * 100 > 0.5 or len(real) > 5 else ("warn" if real else "ok"), float(len(real)), 5.0,
                    json.dumps({"flagged": len(flagged), "stale_universe_rows": stale, "real_unit_inversions": real}, ensure_ascii=False)))
    age = conn.execute("SELECT (CURRENT_DATE - max(base_date)::date) FROM stock_universe").fetchone()[0]
    results.append(("universe_fresh", "fail" if age and age > 10 else ("warn" if age and age > 5 else "ok"), float(age or 0), 5.0,
                    f"stock_universe max base_date is {age} days old (collector stalled since 2026-09-04; splits/liquidations after that date leave stale rows)"))

    # date_format
    bad_all, detail = 0, {}
    since = (date.today() - timedelta(days=30)).isoformat()
    for t, col, rx in DATE_COLS:
        try:
            recent_cond = {"price_history": f"date >= '{since}'", "valuation_history": "year >= 2025", "strategy_feature_snapshot": "1=1",
                           "short_sell_daily": f"bas_dt >= '{since.replace('-', '')}'", "kiwoom_credit_balance": f"dt >= '{since.replace('-', '')}'"}[t]
            vals = conn.execute(f"SELECT CAST({col} AS TEXT) FROM {t} WHERE {recent_cond} LIMIT 20000").fetchall()
            bad = sum(1 for (v,) in vals if v is None or not re.match(rx, str(v)))
            detail[f"{t}.{col}"] = f"{bad}/{len(vals)}"
            bad_all += bad
        except Exception as exc:  # noqa: BLE001
            detail[f"{t}.{col}"] = f"error {str(exc)[:60]}"
            conn.rollback()
    results.append(("date_format", "fail" if bad_all else "ok", float(bad_all), 0.0, json.dumps(detail, ensure_ascii=False)))

    # close_verify_fresh
    r = conn.execute("SELECT trade_date, status, checked_at FROM price_close_verify_log ORDER BY id DESC LIMIT 1").fetchone()
    if r:
        age = (date.today() - date.fromisoformat(str(r[0])[:10])).days
        results.append(("close_verify_fresh", "fail" if (r[1] != "ok" or age > 5) else "ok", float(age), 5.0, f"last trade_date {r[0]} status {r[1]} ({age}d ago)"))
    else:
        results.append(("close_verify_fresh", "warn", None, 5.0, "no verification run recorded yet"))

    fails = []
    for name, status, value, thr, det in results:
        conn.execute("INSERT INTO data_contract_check_log(check_date,check_name,status,value,threshold,detail) VALUES(?,?,?,?,?,?) "
                     "ON CONFLICT (check_date,check_name) DO UPDATE SET status=excluded.status, value=excluded.value, threshold=excluded.threshold, detail=excluded.detail, created_at=now()",
                     (today, name, status, value, thr, det if name == "default_missing" else det[:4000]))
        print(f"{name:20s} {status:5s} value={value} thr={thr} {det[:110]}")
        if status == "fail":
            fails.append(f"{name}={value} (임계 {thr})")
            nid = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM data_fix_log").fetchone()[0]
            conn.execute("INSERT INTO data_fix_log(id, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id) VALUES(?,?,?,?,?,?,?,?,?)",
                         (nid, "data_contract_check_log", f"contract audit FAIL: {name}", int(value or 0), "audit_failure (no data changed)", det[:200], "alerted",
                          "data_contract_audit_20260925.py", f"contract_audit_{datetime.now():%Y%m%d_%H%M%S}"))
    conn.commit()
    if fails:
        try:
            import notifier
            notifier.send("⚠️ 데이터 계약 점검 실패: " + "; ".join(fails), key=f"contract_audit_{today}")
        except Exception as exc:  # noqa: BLE001
            print("alert failed:", exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
