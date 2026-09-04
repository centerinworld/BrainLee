from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
DISPLAY_AUDIT = ROOT_DIR / "market_radar_exports" / "telegram_display_audit_recent_30000.csv"
REPORT_DIR = ROOT_DIR.parent / "research_outputs"


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0] or 0)


def pct(value: int, total: int) -> float:
    return round(value / total * 100, 2) if total else 0.0


def load_display_counts() -> Counter[str]:
    counts: Counter[str] = Counter()
    with DISPLAY_AUDIT.open(encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            counts[row["display_status"]] += 1
    return counts


def audit(baseline_flow_posts: int, baseline_display_ready: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    total_posts = scalar(conn, "SELECT COUNT(*) FROM telegram_post_cache")
    mapped_posts = scalar(conn, "SELECT COUNT(*) FROM telegram_post_cache WHERE mapping_status='mapped'")
    trade_posts = scalar(
        conn,
        """
        SELECT COUNT(*) FROM telegram_post_cache
        WHERE raw_text LIKE '수출%' OR raw_text LIKE '수입%'
           OR title LIKE '수출%' OR title LIKE '수입%'
           OR raw_text LIKE '%수출데이터%' OR raw_text LIKE '%수입데이터%'
           OR raw_text LIKE '%수출 데이터%' OR raw_text LIKE '%수입 데이터%'
        """,
    )
    flow_posts = scalar(conn, "SELECT COUNT(DISTINCT post_message_id) FROM telegram_company_hs_flow_map")
    flow_rows = scalar(conn, "SELECT COUNT(*) FROM telegram_company_hs_flow_map")
    duplicate_keys = scalar(
        conn,
        """
        SELECT COUNT(*) FROM (
          SELECT post_message_id,flow_type,hs_code,stock_code,COUNT(*) c
          FROM telegram_company_hs_flow_map GROUP BY 1,2,3,4 HAVING c>1
        )
        """,
    )
    malformed_hs = scalar(
        conn,
        """
        SELECT COUNT(*) FROM telegram_company_hs_flow_map
        WHERE hs_code NOT GLOB '[0-9]*' OR LENGTH(hs_code) NOT IN (4,6,8,10)
        """,
    )
    orphan_rows = scalar(
        conn,
        """
        SELECT COUNT(*) FROM telegram_company_hs_flow_map f
        LEFT JOIN telegram_post_cache p ON p.message_id=f.post_message_id
        WHERE p.message_id IS NULL
        """,
    )
    stale_exact = scalar(
        conn,
        """
        SELECT COUNT(*) FROM hs_code_company_map m
        WHERE m.mapping_status='exact'
          AND m.note LIKE '텔레그램 @BeOn_BeClear 검증 메시지 기반 exact 매핑:%'
          AND NOT EXISTS (
            SELECT 1 FROM telegram_company_hs_flow_map f
            WHERE f.hs_code=m.hs_code AND f.stock_code=m.stock_code
          )
        """,
    )
    suspicious_rows = scalar(
        conn,
        """
        SELECT COUNT(*) FROM telegram_company_hs_flow_map
        WHERE (hs_code='9021290000' AND (product_title LIKE '%이온주입%' OR product_title LIKE '%반도체%'))
           OR (hs_code LIKE '8409998%' AND product_title NOT LIKE '%선박%' AND product_title NOT LIKE '%엔진%')
           OR (hs_code='8536410000' AND product_title LIKE '%1,000V 초과%')
           OR (hs_code='8536491000' AND product_title LIKE '%1,000V 이하%')
        """,
    )
    monthly = [
        dict(row)
        for row in conn.execute(
            """
            SELECT substr(p.posted_at,1,7) month,
                   COUNT(DISTINCT p.message_id) total_posts,
                   COUNT(DISTINCT f.post_message_id) flow_posts
            FROM telegram_post_cache p
            LEFT JOIN telegram_company_hs_flow_map f ON f.post_message_id=p.message_id
            GROUP BY substr(p.posted_at,1,7) ORDER BY month
            """
        )
    ]
    conn.close()

    display = load_display_counts()
    display_ready = display["ready_card"] + display["ready_mapping"]
    return {
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "@BeOn_BeClear telegram_post_cache full history",
        "period": {"first": monthly[0]["month"] if monthly else None, "last": monthly[-1]["month"] if monthly else None},
        "coverage": {
            "total_posts": total_posts,
            "mapped_status_posts": mapped_posts,
            "mapped_status_pct": pct(mapped_posts, total_posts),
            "trade_posts": trade_posts,
            "exact_flow_posts": flow_posts,
            "exact_flow_pct": pct(flow_posts, trade_posts),
            "flow_evidence_rows": flow_rows,
            "display_ready_posts": display_ready,
            "display_ready_pct": pct(display_ready, total_posts),
            "partial_product_hs": display["partial_product_hs"],
            "partial_metadata": display["partial_metadata"],
        },
        "improvement": {
            "baseline_exact_flow_posts": baseline_flow_posts,
            "exact_flow_posts_added": flow_posts - baseline_flow_posts,
            "exact_flow_pct_before": pct(baseline_flow_posts, trade_posts),
            "exact_flow_pct_after": pct(flow_posts, trade_posts),
            "baseline_display_ready_posts": baseline_display_ready,
            "display_ready_posts_added": display_ready - baseline_display_ready,
            "display_ready_pct_before": pct(baseline_display_ready, total_posts),
            "display_ready_pct_after": pct(display_ready, total_posts),
        },
        "integrity": {
            "duplicate_flow_keys": duplicate_keys,
            "malformed_hs_codes": malformed_hs,
            "orphan_flow_rows": orphan_rows,
            "unsupported_generated_exact_rows": stale_exact,
            "known_cross_domain_false_matches": suspicious_rows,
        },
        "display_status_counts": dict(display),
        "monthly": monthly,
    }


def write_report(report: dict, output_stem: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"{output_stem}.json"
    md_path = REPORT_DIR / f"{output_stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    c = report["coverage"]
    i = report["improvement"]
    q = report["integrity"]
    md = f"""# Telegram HS mapping full audit

## Conclusion

- Full cache: {c['total_posts']:,} posts, period {report['period']['first']} to {report['period']['last']}.
- Exact company-HS-flow coverage: {c['exact_flow_posts']:,}/{c['trade_posts']:,} ({c['exact_flow_pct']:.2f}%).
- Display-ready coverage: {c['display_ready_posts']:,}/{c['total_posts']:,} ({c['display_ready_pct']:.2f}%).
- Exact flow improvement: {i['baseline_exact_flow_posts']:,} -> {c['exact_flow_posts']:,} (+{i['exact_flow_posts_added']:,}, {i['exact_flow_pct_before']:.2f}% -> {i['exact_flow_pct_after']:.2f}%).
- Display-ready improvement: {i['baseline_display_ready_posts']:,} -> {c['display_ready_posts']:,} (+{i['display_ready_posts_added']:,}, {i['display_ready_pct_before']:.2f}% -> {i['display_ready_pct_after']:.2f}%).

## Integrity gates

- Duplicate flow keys: {q['duplicate_flow_keys']}
- Malformed HS codes: {q['malformed_hs_codes']}
- Orphan flow rows: {q['orphan_flow_rows']}
- Unsupported generated exact rows: {q['unsupported_generated_exact_rows']}
- Known cross-domain false matches: {q['known_cross_domain_false_matches']}

## Remaining scope

- Product-HS only: {c['partial_product_hs']:,} posts.
- Metadata only: {c['partial_metadata']:,} posts.
- Non-listed/private companies remain visible as metadata and are not forced onto listed stocks.
- Coverage measures mapping availability, not independent confirmation that every company owns the full national HS trade value.

## Reproduction

```bash
/Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python hs_trade_lab/scripts/rebuild_telegram_flow_mappings.py
/Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python hs_trade_lab/scripts/export_telegram_display_audit.py --limit 30000
/Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python hs_trade_lab/scripts/audit_telegram_hs_coverage.py
```
"""
    md_path.write_text(md, encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit full Telegram-to-HS mapping coverage and integrity.")
    parser.add_argument("--baseline-flow-posts", type=int, default=18307)
    parser.add_argument("--baseline-display-ready", type=int, default=19128)
    parser.add_argument("--output-stem", default="telegram_hs_full_audit_20260823")
    args = parser.parse_args()
    report = audit(args.baseline_flow_posts, args.baseline_display_ready)
    json_path, md_path = write_report(report, args.output_stem)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), **report["coverage"], **report["integrity"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
