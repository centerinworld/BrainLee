#!/usr/bin/env python3
"""Measure recent close-price agreement between price_history and Naver Finance."""
from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs" / "naver_recent_price_agreement_20260712.json"
ITEM_RE = re.compile(r'data="([^"]+)"')


def fetch(code: str) -> tuple[str, dict[str, float]]:
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=120&requestType=0"
    try:
        text = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).text
        rows = {}
        for raw in ITEM_RE.findall(text):
            fields = raw.split("|")
            if len(fields) >= 5 and len(fields[0]) == 8 and float(fields[4] or 0) > 0:
                rows[f"{fields[0][:4]}-{fields[0][4:6]}-{fields[0][6:8]}"] = float(fields[4])
        return code, rows
    except Exception:
        return code, {}


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    codes = [r[0] for r in conn.execute(
        """WITH x AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY base_date DESC,id DESC) rn FROM stock_universe)
           SELECT stock_code FROM x WHERE rn=1 AND market IN ('KOSPI','KOSDAQ')
             AND COALESCE(stock_type,'보통주')='보통주' ORDER BY stock_code LIMIT 200"""
    )]
    histories = dict(ThreadPoolExecutor(max_workers=6).map(fetch, codes))
    comparisons = []
    per_stock = []
    for code in codes:
        internal = {r["date"][:10]: float(r["close"]) for r in conn.execute(
            "SELECT date,close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 60", (code,)
        )}
        external = histories.get(code, {})
        diffs = []
        for date in set(internal) & set(external):
            diff = abs(internal[date]/external[date]-1)*100
            comparisons.append(diff)
            diffs.append(diff)
        if diffs:
            per_stock.append({"stock_code": code, "points": len(diffs), "within_1pct": sum(d<=1 for d in diffs)/len(diffs)*100,
                              "median_abs_diff_pct": sorted(diffs)[len(diffs)//2]})
    conn.close()
    comparisons.sort()
    result = {
        "sample_stocks": len(codes), "stocks_with_overlap": len(per_stock), "overlap_points": len(comparisons),
        "within_0_1pct": round(sum(d<=0.1 for d in comparisons)/len(comparisons)*100,2) if comparisons else None,
        "within_1pct": round(sum(d<=1 for d in comparisons)/len(comparisons)*100,2) if comparisons else None,
        "within_5pct": round(sum(d<=5 for d in comparisons)/len(comparisons)*100,2) if comparisons else None,
        "median_abs_diff_pct": round(comparisons[len(comparisons)//2],4) if comparisons else None,
        "p95_abs_diff_pct": round(comparisons[int(len(comparisons)*0.95)],4) if comparisons else None,
        "worst_stocks": sorted(per_stock, key=lambda r: (r["within_1pct"], -r["median_abs_diff_pct"]))[:20],
        "verified_at": datetime.now().isoformat(timespec="seconds"), "source": "Naver Finance fchart daily",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
