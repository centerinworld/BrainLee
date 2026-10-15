#!/usr/bin/env python3
"""삼성전기(009150) 숫자 데이터 미결 상태 정리.

적용 범위:
- `financial_data` 원장을 기준으로 `canonical_financial_data`를 재동기화한다.
- `financial_field_verification` 미결 사유를 `stock_collection_config`에 기록한다.

하지 않는 일:
- FnGuide 값만 보고 `financial_data`/`cash_flow_data`를 채우지 않는다.
- DART 원문이 없는 OFS 2026Q2/CapEx/감가상각 칸은 자동 확정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_write_gate import ensure_canonical_schema, gate_financial_row, upsert_canonical_financial  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

CODE = "009150"
RUN_PREFIX = "repair_009150_numeric_status"
BACKUP = "canonical_financial_data_backup_009150_20261008"
FIN_FIELDS = {"revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity"}


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _quality_by_key(conn):
    out = {}
    rows = _rows(
        conn,
        """SELECT year, quarter, report_type, field, status
           FROM financial_field_verification
           WHERE stock_code=? AND field IN ('revenue','operating_profit','net_income',
                                            'total_assets','total_liabilities','total_equity')""",
        (CODE,),
    )
    grouped = defaultdict(list)
    for r in rows:
        grouped[(int(r["year"]), int(r["quarter"]), str(r["report_type"]))].append(r)
    for key, vals in grouped.items():
        statuses = {str(v["status"]) for v in vals}
        if vals and all(s.startswith("확정") for s in statuses):
            out[key] = 6.5
        elif any(s.startswith("확정") for s in statuses) and not any(s.startswith("원인") for s in statuses):
            out[key] = 5.5
        else:
            out[key] = None
    return out


def _pending_summary(conn):
    rows = _rows(
        conn,
        """SELECT year, quarter, report_type, field, db_value, fnguide_value, status, cause
           FROM financial_field_verification
           WHERE stock_code=? AND (status LIKE '원인%' OR status LIKE '미확인%')
           ORDER BY year DESC, quarter DESC, report_type, field""",
        (CODE,),
    )
    by_cause = Counter((r["status"], r.get("cause") or "DB 없음") for r in rows)
    examples = [
        {
            "year": r["year"],
            "quarter": r["quarter"],
            "report_type": r["report_type"],
            "field": r["field"],
            "status": r["status"],
            "cause": r.get("cause"),
            "db_value": r.get("db_value"),
            "fnguide_value": r.get("fnguide_value"),
        }
        for r in rows[:20]
    ]
    return rows, by_cause, examples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    run_id = f"{RUN_PREFIX}_{datetime.now():%Y%m%d_%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_primary_db(timeout=120, readonly=not args.apply)
    gate_conn = conn if args.apply else sqlite3.connect(":memory:")
    ensure_canonical_schema(gate_conn)

    raw_rows = _rows(
        conn,
        """SELECT id, stock_code, year, quarter, is_annual, report_type,
                  revenue, operating_profit, net_income, total_assets, total_liabilities,
                  total_equity, capital_stock, eps, bps, dps, roe, data_source
           FROM financial_data
           WHERE stock_code=? AND year>=2023
           ORDER BY year, quarter, is_annual, report_type, id""",
        (CODE,),
    )
    quality = _quality_by_key(conn)
    pending_rows, by_cause, examples = _pending_summary(conn)

    before = _rows(
        conn,
        """SELECT * FROM canonical_financial_data WHERE stock_code=? AND year>=2023
           ORDER BY year, quarter, is_annual, report_type""",
        (CODE,),
    )

    changes = []
    refused = []
    for r in raw_rows:
        payload = {
            "stock_code": r["stock_code"],
            "year": r["year"],
            "quarter": r["quarter"],
            "is_annual": r["is_annual"],
            "report_type": r["report_type"] or "CFS",
            "revenue": r["revenue"],
            "operating_profit": r["operating_profit"],
            "net_income": r["net_income"],
            "total_assets": r["total_assets"],
            "total_liabilities": r["total_liabilities"],
            "total_equity": r["total_equity"],
            "capital_stock": r["capital_stock"],
            "eps": r["eps"],
            "bps": r["bps"],
            "dps": r["dps"],
            "roe": r["roe"],
            "data_source": r["data_source"],
        }
        ok, fixed, reason = gate_financial_row(gate_conn, payload)
        if not ok:
            refused.append({"id": r["id"], "year": r["year"], "quarter": r["quarter"], "report_type": r["report_type"], "reason": reason})
            continue
        qkey = (int(r["year"]), 0 if r["is_annual"] else int(r["quarter"]), str(r["report_type"] or "CFS"))
        changes.append({"source_id": r["id"], "payload": fixed, "quality_score": quality.get(qkey)})

    result = {
        "run_id": run_id,
        "mode": "apply" if args.apply else "dry_run",
        "canonical_rows_before": len(before),
        "financial_rows_to_sync": len(changes),
        "refused": refused,
        "pending_count": len(pending_rows),
        "pending_by_cause": {f"{k[0]}|{k[1]}": v for k, v in by_cause.items()},
        "pending_examples": examples,
    }

    if args.apply:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP} (run_id TEXT, backed_up_at TEXT, row_json TEXT)")
        conn.executemany(
            f"INSERT INTO {BACKUP} (run_id, backed_up_at, row_json) VALUES (?,?,?)",
            [(run_id, now, json.dumps(r, ensure_ascii=False, default=str)) for r in before],
        )
        for item in changes:
            upsert_canonical_financial(
                conn,
                item["payload"],
                source_row_id=item["source_id"],
                decision_reason=run_id,
                quality_score=item["quality_score"],
            )
        pending_detail = {
            "summary": result["pending_by_cause"],
            "examples": examples,
            "action": "DART 원문 확보 전 FnGuide 단독값 자동 적용 금지. OFS 2026Q2/감가상각/D&A는 원문 수집 후 재검증.",
        }
        configs = [
            (
                CODE,
                "fs_pending:field_verification_20261008",
                json.dumps(pending_detail, ensure_ascii=False, default=str),
                "삼성전기 미결 필드 사유 고정 — 다른 AI 재검토용",
                run_id,
                now,
                now,
            )
        ]
        conn.executemany(
            """INSERT INTO stock_collection_config
               (stock_code, config_key, config_value, reason, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT (stock_code, config_key) DO UPDATE SET
                 config_value=EXCLUDED.config_value,
                 reason=EXCLUDED.reason,
                 source=EXCLUDED.source,
                 updated_at=EXCLUDED.updated_at""",
            configs,
        )
        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                now,
                "canonical_financial_data+stock_collection_config",
                "009150 삼성전기 canonical 재동기화 및 미결 사유 기록",
                len(changes),
                "financial_data 원장 → write_gate → canonical 재동기화; FnGuide 단독 미결은 보류 사유 기록",
                json.dumps({"canonical_rows_before": len(before), "pending_by_cause": result["pending_by_cause"]}, ensure_ascii=False),
                json.dumps({"canonical_synced": len(changes), "pending_recorded": len(pending_rows)}, ensure_ascii=False),
                "scripts/review/repair_009150_numeric_status_20261008.py",
                run_id,
            ),
        )
        conn.commit()
        after_bad = _rows(
            conn,
            """SELECT c.year,c.quarter,c.is_annual,c.report_type,
                      c.revenue, f.revenue AS raw_revenue,
                      c.operating_profit, f.operating_profit AS raw_operating_profit,
                      c.net_income, f.net_income AS raw_net_income,
                      c.total_assets, f.total_assets AS raw_total_assets,
                      c.total_liabilities, f.total_liabilities AS raw_total_liabilities,
                      c.total_equity, f.total_equity AS raw_total_equity,
                      c.quality_score, c.decision_reason
               FROM canonical_financial_data c
               JOIN financial_data f ON f.id=c.source_row_id
               WHERE c.stock_code=? AND c.year>=2023
                 AND (COALESCE(c.revenue,-999999)<>COALESCE(f.revenue,-999999)
                   OR COALESCE(c.operating_profit,-999999)<>COALESCE(f.operating_profit,-999999)
                   OR COALESCE(c.net_income,-999999)<>COALESCE(f.net_income,-999999)
                   OR COALESCE(c.total_assets,-999999)<>COALESCE(f.total_assets,-999999)
                   OR COALESCE(c.total_liabilities,-999999)<>COALESCE(f.total_liabilities,-999999)
                   OR COALESCE(c.total_equity,-999999)<>COALESCE(f.total_equity,-999999))
               ORDER BY c.year,c.quarter,c.is_annual,c.report_type""",
            (CODE,),
        )
        result["canonical_mismatch_after"] = after_bad
        result["applied"] = True
    else:
        result["applied"] = False

    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"{run_id}_{'apply' if args.apply else 'dry'}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({k: result[k] for k in ("run_id", "mode", "financial_rows_to_sync", "refused", "pending_count", "pending_by_cause", "applied")}, ensure_ascii=False, indent=2))
    if args.apply:
        print("canonical_mismatch_after", len(result["canonical_mismatch_after"]))


if __name__ == "__main__":
    main()
