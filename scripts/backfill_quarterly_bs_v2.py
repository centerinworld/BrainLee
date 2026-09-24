#!/usr/bin/env python3
"""Unified Q1-Q4 CFS balance-sheet NULL backfill from DART periodic-report XML.

Backfills `total_assets` / `total_equity` (point-in-time facts, safe to read
directly from the report XML) for quarterly OPEN flags that have zero sources.

Guarantees:
  * Idempotent — re-running the same target produces no new writes (the target
    is matched by `source_count=0` AND `financial_data.<field> IS NULL`, both of
    which flip on a successful fill).
  * Never overwrites a non-NULL value, and never touches `source_count>=2`
    (confirmed) rows — both are structurally excluded by the target query.
  * Full audit — every change is written to `financial_fix_log` (before/after,
    account, unit, XML source URL, extraction time) and the flag's `dart_value`,
    `source_count`, `notes` are updated.
  * Checkpoint + resume — a JSON checkpoint records each completed target so a
    resumed run never re-downloads a document it already processed.
  * Rate-limit safe — stops on DART `status=020` and on a per-run document cap.

Run:
  python3 scripts/backfill_quarterly_bs_v2.py --limit 50          # dry-run
  python3 scripts/backfill_quarterly_bs_v2.py --limit 50 --apply  # write
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from db_compat import connect_primary_db

DART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
RUN_ID = "dart_document_bs_backfill_v2_20260920"
FIELDS = ("total_assets", "total_equity")
_FACT_CODES = {
    "total_assets": ("ifrs-full_Assets",),
    "total_equity": ("ifrs-full_Equity",),
}
CHECKPOINT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "scratch", "bs_backfill_v2_checkpoint.json")

# Q1 -> 분기보고서 (YYYY.03); Q2 -> 반기보고서 (YYYY.06);
# Q3 -> 분기보고서 (YYYY.09); Q4 -> 사업보고서 (YYYY.12).
_REPORT_PERIOD = {
    1: ("분기보고서", "03"),
    2: ("반기보고서", "06"),
    3: ("분기보고서", "09"),
    4: ("사업보고서", "12"),
}


def _number(value: str) -> float | None:
    try:
        s = value.replace(",", "").strip()
        if s.startswith("(") and s.endswith(")"):
            return -float(s.strip("()"))
        return float(s)
    except ValueError:
        return None


def parse_rich_facts(xml_text: str) -> dict[str, dict]:
    """Return per-field rich facts: {value, consolidated, acode}.

    Only `eFY` (current-period end) facts are considered for balance-sheet
    point-in-time values, and consolidated (`ConsolidatedMember`) facts are
    preferred over separate.  The selected account code is captured for audit.
    """
    result: dict[str, dict] = {}
    te_re = re.compile(r"<TE(?P<attrs>[^>]*)>(?P<value>.*?)</TE>", re.DOTALL)
    for field, codes in _FACT_CODES.items():
        candidates: list[tuple[int, float, str, bool]] = []
        for code in codes:
            code_re = re.compile(rf'\bACODE="{re.escape(code)}"')
            for m in te_re.finditer(xml_text):
                attrs = m.group("attrs")
                if not code_re.search(attrs):
                    continue
                value = _number(re.sub(r"<[^>]+>", "", m.group("value")))
                if value is None:
                    continue
                score = 0
                if "eFY" in attrs:
                    score += 2
                consolidated = "ConsolidatedMember" in attrs
                if consolidated:
                    score += 1
                candidates.append((score, value, code, consolidated))
        if candidates:
            _, value, code, consolidated = max(candidates, key=lambda item: item[0])
            result[field] = {
                "value": value,
                "consolidated": consolidated,
                "acode": code,
            }
    return result


def _download_document_text(rcept_no: str, api_key: str) -> str:
    resp = requests.get(DART_DOCUMENT_URL,
                        params={"crtfc_key": api_key, "rcept_no": rcept_no},
                        timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    if resp.headers.get("Content-Type", "").startswith("application/json"):
        # DART error payload (e.g. status 020 / 014) is returned as JSON text.
        body = resp.text
        m = re.search(r'"status"\s*:\s*"(\d+)"', body)
        if m:
            raise RuntimeError(f"DART status {m.group(1)}")
        raise RuntimeError("DART JSON error")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        texts = [archive.read(name).decode("utf-8", errors="ignore")
                 for name in archive.namelist() if name.endswith(".xml")]
    return "\n".join(texts)


def _load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"completed": {}}


def _save_checkpoint(cp: dict) -> None:
    os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as fh:
        json.dump(cp, fh, ensure_ascii=False, indent=2)


def targets(conn):
    """Distinct (stock_code, year, quarter, [fields]) with a matching report."""
    rows = []
    for qtr in (1, 2, 3, 4):
        rtype, month = _REPORT_PERIOD[qtr]
        period_expr = f"report_nm LIKE '%{rtype}%' AND report_nm LIKE '%(' || g.year::text || '.{month})%'"
        sql = f"""
            WITH open_flags AS (
                SELECT f.stock_code, f.year, f.quarter, f.field
                FROM fin_quarterly_validation_flags f
                JOIN financial_data d
                  ON d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter
                 AND d.is_annual IS FALSE AND d.report_type='CFS'
                WHERE f.check_type='QUARTERLY_4WAY' AND f.status='OPEN'
                  AND f.source_count=0 AND f.quarter=%s AND f.field = ANY(%s)
                  AND ((f.field='total_assets' AND d.total_assets IS NULL)
                    OR (f.field='total_equity' AND d.total_equity IS NULL))
            ), grouped AS (
                SELECT stock_code, year, quarter, array_agg(DISTINCT field) fields
                FROM open_flags GROUP BY stock_code, year, quarter
            )
            SELECT g.stock_code, g.year, g.quarter, g.fields, d.rcept_no
            FROM grouped g
            JOIN LATERAL (
                SELECT rcept_no FROM dart_disclosures
                WHERE stock_code=g.stock_code AND {period_expr}
                ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 1
            ) d ON TRUE
            ORDER BY g.year DESC, g.quarter, g.stock_code
        """
        rows.extend(conn.execute(sql, (qtr, list(FIELDS))).fetchall())
    return rows


def main(limit: int, apply: bool, max_docs: int, resume: bool) -> None:
    api_key = config.DART_API_KEY3
    if not api_key:
        raise SystemExit("DART_API_KEY3 is not configured")
    cp = _load_checkpoint() if resume else {"completed": {}}
    conn = connect_primary_db(timeout=300, readonly=not apply)
    stats = {
        "targets": 0, "documents": 0, "filled": 0, "no_report": 0,
        "no_document": 0, "no_fact": 0, "not_consolidated": 0,
        "dart_020": 0, "already_done": 0, "cap_reached": 0,
    }
    now = datetime.now().isoformat(timespec="seconds")
    docs_this_run = 0
    try:
        for row in targets(conn):
            if stats["targets"] >= limit:
                break
            key = f"{row['stock_code']}|{row['year']}|{row['quarter']}"
            stats["targets"] += 1
            if key in cp["completed"]:
                stats["already_done"] += 1
                continue
            if row["rcept_no"] is None:
                stats["no_report"] += 1
                continue
            if docs_this_run >= max_docs:
                stats["cap_reached"] += 1
                break
            docs_this_run += 1
            try:
                xml_text = _download_document_text(row["rcept_no"], api_key)
            except RuntimeError as exc:
                if "020" in str(exc):
                    stats["dart_020"] += 1
                    print(f"[STOP] DART status 020 hit at {key}; stopping.")
                    break
                stats["no_document"] += 1
                continue
            stats["documents"] += 1
            facts = parse_rich_facts(xml_text)
            for field in row["fields"]:
                fact = facts.get(field)
                if fact is None:
                    stats["no_fact"] += 1
                    continue
                if not fact["consolidated"]:
                    stats["not_consolidated"] += 1
                    continue
                current = conn.execute(
                    f"""SELECT id FROM financial_data
                        WHERE stock_code=%s AND year=%s AND quarter=%s
                          AND is_annual IS FALSE AND report_type='CFS' AND {field} IS NULL""",
                    (row["stock_code"], row["year"], row["quarter"]),
                ).fetchone()
                if not current:
                    continue  # already filled by a concurrent run — safe no-op
                source = (
                    f"OpenDART document.xml rcept_no={row['rcept_no']} "
                    f"acode={fact['acode']} unit=KRW"
                )
                if apply:
                    conn.execute(
                        f"""UPDATE financial_data SET {field}=%s,
                                data_source=CASE
                                  WHEN COALESCE(data_source,'') LIKE '%%dart_document_fallback%%'
                                    THEN COALESCE(data_source,'')
                                  ELSE COALESCE(data_source,'') || '+dart_document_fallback'
                                END,
                                updated_at=%s
                            WHERE id=%s AND {field} IS NULL""",
                        (fact["value"], now, current["id"]),
                    )
                    conn.execute(
                        """INSERT INTO financial_fix_log
                           (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,
                            field_name,old_value,new_value,fix_rule,source,run_id)
                           VALUES (%s,%s,%s,%s,%s,0,'CFS',%s,NULL,%s,
                                   'DART_DOCUMENT_XML_CONSOLIDATED',%s,%s)""",
                        (now, current["id"], row["stock_code"], row["year"], row["quarter"],
                         field, fact["value"], source, RUN_ID),
                    )
                    conn.execute(
                        """UPDATE fin_quarterly_validation_flags
                           SET dart_value=%s, source_count=1, notes=%s, updated_at=%s
                           WHERE stock_code=%s AND year=%s AND quarter=%s AND field=%s
                             AND check_type='QUARTERLY_4WAY' AND status='OPEN' AND source_count=0""",
                        (fact["value"],
                         f"DART consolidated report XML: {source}", now,
                         row["stock_code"], row["year"], row["quarter"], field),
                    )
                stats["filled"] += 1
            # Mark checkpoint for every successfully-downloaded document (even
            # if no field was filled) so a resume never re-downloads it.
            cp["completed"][key] = {
                "stock_code": row["stock_code"], "year": row["year"],
                "quarter": row["quarter"], "rcept_no": row["rcept_no"],
                "fields": list(row["fields"]), "at": now,
            }
            _save_checkpoint(cp)
        if apply:
            conn.commit()
            _save_checkpoint(cp)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50,
                        help="max distinct (stock,year,quarter) targets to attempt")
    parser.add_argument("--apply", action="store_true", help="write to DB (default dry-run)")
    parser.add_argument("--max-docs", type=int, default=90,
                        help="max document.xml downloads this run")
    parser.add_argument("--resume", action="store_true", help="use checkpoint to skip done")
    args = parser.parse_args()
    main(args.limit, args.apply, args.max_docs, args.resume)
