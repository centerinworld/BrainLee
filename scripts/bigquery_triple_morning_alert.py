#!/usr/bin/env python3
"""
BigQuery 3배주 패턴 아침 리포트 + 텔레그램 발송

실행:
  /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python scripts/bigquery_triple_morning_alert.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests


PROJECT_ID = os.getenv("BQ_PROJECT_ID", "project-d8a62269-8156-4f96-870")
DATASET_ID = os.getenv("BQ_DATASET_ID", "stock_dashboard")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TOP_N = int(os.getenv("TRIPLE_ALERT_TOP_N", "15"))


def _client():
    from google.cloud import bigquery
    return bigquery.Client(project=PROJECT_ID)


def _query_rows(client):
    table_daily = f"`{PROJECT_ID}.{DATASET_ID}.triple_pattern_daily`"
    sql = f"""
    SELECT
      run_date, stock_code, stock_name, sector, market,
      mktcap_100m, triple_pattern_score, roe, op_margin_pct,
      avg_inst_60d_100m, avg_frn_60d_100m, pct_below_52w_high
    FROM {table_daily}
    WHERE run_date = (
      SELECT MAX(run_date) FROM {table_daily}
    )
    ORDER BY triple_pattern_score DESC, mktcap_100m ASC
    LIMIT @top_n
    """
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("top_n", "INT64", TOP_N)]
    )
    return list(client.query(sql, job_config=cfg).result())


def _render(rows):
    if not rows:
        return "📉 [3배주 패턴] 오늘 후보가 없습니다."

    run_date = str(rows[0]["run_date"])
    lines = [
        "🚀 <b>3배주/우상향 패턴 아침 리포트</b>",
        f"기준일: {run_date}",
        f"후보 수: {len(rows)}개",
        "",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i:02d}. {r['stock_name']}({r['stock_code']}) "
            f"[{r['market']}/{r['sector']}] "
            f"score {float(r['triple_pattern_score'] or 0):.1f} · "
            f"ROE {float(r['roe'] or 0):.1f}% · OPM {float(r['op_margin_pct'] or 0):.1f}% · "
            f"수급 {float(r['avg_inst_60d_100m'] or 0):.0f}/{float(r['avg_frn_60d_100m'] or 0):.0f}억"
        )
    return "\n".join(lines)


def _send_telegram(msg: str) -> bool:
    try:
        from notifier import send
        return send(msg)
    except Exception:
        return False


def _save_local_report(msg: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"bq_triple_morning_{ts}.md"
    path.write_text(msg, encoding="utf-8")
    return str(path)


def main():
    client = _client()
    rows = _query_rows(client)
    msg = _render(rows)
    sent = _send_telegram(msg)
    saved = _save_local_report(msg)
    print(json.dumps({
        "rows": len(rows),
        "telegram_sent": sent,
        "saved_report": saved,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

