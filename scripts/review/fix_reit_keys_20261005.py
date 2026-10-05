#!/usr/bin/env python3
"""리츠 기간 키 원상 복구(2026-10-05, FINANCIAL_STATEMENTS.md §9-2-8 #3).

리츠(6개월 결산)는 분기 개념이 달라 회계 키 재배치 대상이 아닌데, 11종목이 `fs_quirk:fiscal_period=REIT_6M` 표시 없이 결산월만 기록돼
rekey_fiscal_20261004 계열·fiscal_conflict_20261005 가 일반 비12월 결산처럼 옮겼다 → 기간이 아직 끝나지 않은 2027년 행이 생김.
1) 11종목 REIT_6M 표시  2) 재무·현금흐름 행의 (year, quarter)를 최초 백업(rekey_fiscal_20261004_223105, 재배치 전 상태)으로 복구
   (값은 그대로 — 키만, data_source의 '+fiscal_rekey' 표시 제거)  3) fs_quirk:fiscal_keyed 표시 제거
4) REIT_6M 293940·377190 의 2027년 2분기 행: backfill_dart_q2_financials 가 to_fiscal(리츠 예외 없음)로 쓴 2026년 2분기 중복 — 같은 값이면 삭제(백업)
기본 dry-run, --apply.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

REITS = ("330590", "334890", "338100", "357120", "357250", "395400", "417310", "432320", "448730", "451800", "481850")
DUP = ("293940", "377190")
BASE = "rekey_fiscal_20261004_223105"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    ph = ",".join("?" * len(REITS))
    moves = {}
    for t in ("financial_data", "cash_flow_data"):
        orig = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT id, year, quarter FROM {t}_backup_rekey_20261004 WHERE run_id=? AND stock_code IN ({ph})", (BASE,) + REITS).fetchall()}
        cur = {r[0]: (r[1], r[2]) for r in conn.execute(f"SELECT id, year, quarter FROM {t} WHERE stock_code IN ({ph})", REITS).fetchall()}
        moves[t] = [(i, cur[i], orig[i]) for i in orig if i in cur and cur[i] != orig[i]]
        print(t, "원래 키로 복구", len(moves[t]))
    dups = []
    for code in DUP:
        for t, cols in (("financial_data", "revenue, total_assets, total_equity"), ("cash_flow_data", "operating_cf, investing_cf")):
            a27 = conn.execute(f"SELECT id, {cols} FROM {t} WHERE stock_code=? AND year=2027 AND quarter=2 AND NOT is_annual", (code,)).fetchall()
            for r in a27:
                r = tuple(r)
                same = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE stock_code=? AND year=2026 AND quarter=2 AND NOT is_annual AND ({' AND '.join(f'{c_.strip()} IS NOT DISTINCT FROM ?' for c_ in cols.split(','))})",
                                    (code,) + r[1:]).fetchone()[0]
                print(t, code, "2027Q2", r[0], "2026Q2와 같은 값" if same else "값 다름 → 보류")
                if same:
                    dups.append((t, r[0]))
    if not a.apply:
        return
    run_id = f"reit_key_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for t in ("financial_data", "cash_flow_data"):
        conn.execute(f"INSERT INTO {t}_backup_rekey_20261004 SELECT *, ? FROM {t} WHERE stock_code IN ({ph})", (run_id,) + REITS)
        mv = moves[t]
        for k, (i, _, _) in enumerate(mv):
            conn.execute(f"UPDATE {t} SET quarter=? WHERE id=?", (-(k + 100), i))
        for i, _, (y, q) in mv:
            conn.execute(f"UPDATE {t} SET year=?, quarter=?, data_source=replace(COALESCE(data_source,''), '+fiscal_rekey', ''), updated_at=? WHERE id=?", (y, q, now, i))
    for t, i in dups:
        conn.execute(f"INSERT INTO {t}_backup_rekey_20261004 SELECT *, ? FROM {t} WHERE id=?", (run_id + "_dup", i))
        conn.execute(f"DELETE FROM {t} WHERE id=?", (i,))
    for code in REITS:
        conn.execute("""INSERT INTO stock_collection_config(stock_code, config_key, config_value) VALUES (?, 'fs_quirk:fiscal_period', 'REIT_6M')
                        ON CONFLICT (stock_code, config_key) DO UPDATE SET config_value='REIT_6M'""", (code,))
        conn.execute("DELETE FROM stock_collection_config WHERE stock_code=? AND config_key='fs_quirk:fiscal_keyed'", (code,))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    n = sum(len(v) for v in moves.values())
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", "리츠 11종목 기간 키 원상 복구", n + len(dups), "재배치 전 백업(rekey_fiscal_20261004_223105) 키로, REIT_6M 표시",
                  json.dumps({t: len(v) for t, v in moves.items()}), f"키 복구 {n}, 2027Q2 중복 삭제 {len(dups)}", "scripts/review/fix_reit_keys_20261005.py", run_id))
    conn.commit()
    print("적용 완료", run_id, n, len(dups))


if __name__ == "__main__":
    main()
