#!/usr/bin/env python3
"""Audit external provider usefulness before paying or integrating deeply.

The script is intentionally conservative.  If provider keys are absent, it still
reports local analyst-PDF coverage and the exact small-cap sample universe that
should be checked before subscribing to FnSpace/Korean Tickers/FMP/Finnhub/etc.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("\"'")
    return env


def http_json(url: str, timeout: int = 8) -> tuple[bool, object | str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(300_000).decode("utf-8", errors="replace")
        try:
            return True, json.loads(raw)
        except Exception:
            return True, raw[:1000]
    except Exception as exc:
        return False, str(exc)


def provider_checks(samples: list[dict], env: dict[str, str]) -> list[dict]:
    checks: list[dict] = []
    # Keep checks tiny: at most 3 symbols, only when keys exist.
    sample_codes = [s["stock_code"] for s in samples[:3]]

    fmp_key = os.getenv("FMP_API_KEY") or env.get("FMP_API_KEY")
    if fmp_key:
        for code in sample_codes:
            symbol = f"{code}.KS"
            url = f"https://financialmodelingprep.com/api/v3/profile/{urllib.parse.quote(symbol)}?apikey={urllib.parse.quote(fmp_key)}"
            ok, payload = http_json(url)
            checks.append({"provider": "FMP", "symbol": symbol, "ok": ok, "payload_preview": str(payload)[:500]})
    else:
        checks.append({"provider": "FMP", "ok": False, "reason": "FMP_API_KEY 없음: 호출하지 않음"})

    finnhub_key = os.getenv("FINNHUB_API_KEY") or env.get("FINNHUB_API_KEY")
    if finnhub_key:
        for code in sample_codes:
            symbol = f"{code}.KS"
            url = f"https://finnhub.io/api/v1/stock/profile2?symbol={urllib.parse.quote(symbol)}&token={urllib.parse.quote(finnhub_key)}"
            ok, payload = http_json(url)
            checks.append({"provider": "Finnhub", "symbol": symbol, "ok": ok, "payload_preview": str(payload)[:500]})
    else:
        checks.append({"provider": "Finnhub", "ok": False, "reason": "FINNHUB_API_KEY 없음: 호출하지 않음"})

    twelve_key = os.getenv("TWELVE_DATA_API_KEY") or env.get("TWELVE_DATA_API_KEY")
    if twelve_key:
        for code in sample_codes:
            symbol = f"{code}:KRX"
            url = (
                "https://api.twelvedata.com/quote?"
                f"symbol={urllib.parse.quote(symbol)}&apikey={urllib.parse.quote(twelve_key)}"
            )
            ok, payload = http_json(url)
            checks.append({"provider": "TwelveData", "symbol": symbol, "ok": ok, "payload_preview": str(payload)[:500]})
    else:
        checks.append({"provider": "TwelveData", "ok": False, "reason": "TWELVE_DATA_API_KEY 없음: 호출하지 않음"})

    av_key = os.getenv("ALPHAVANTAGE_API_KEY") or env.get("ALPHAVANTAGE_API_KEY")
    if av_key:
        for code in sample_codes[:1]:
            symbol = f"{code}.KS"
            url = (
                "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&"
                f"symbol={urllib.parse.quote(symbol)}&apikey={urllib.parse.quote(av_key)}"
            )
            ok, payload = http_json(url)
            checks.append({"provider": "AlphaVantage", "symbol": symbol, "ok": ok, "payload_preview": str(payload)[:500]})
    else:
        checks.append({"provider": "AlphaVantage", "ok": False, "reason": "ALPHAVANTAGE_API_KEY 없음: 호출하지 않음"})

    checks.append({
        "provider": "KoreanTickers",
        "ok": False,
        "reason": "공식 API key/샘플 응답 확인 전 유료 도입 금지. 시총 500억대 10종목의 segment/consensus/financial coverage 샘플 요청 필요.",
    })
    return checks


def main() -> None:
    conn = sqlite3.connect(DB, timeout=60)
    env = load_env_file()

    bucket_rows = rows(
        conn,
        """
        WITH u AS (
          SELECT stock_code, stock_name, market_cap
          FROM stock_universe
          WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            AND market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
            AND COALESCE(kind_stkcert_nm,'') LIKE '%보통%'
        ),
        a AS (SELECT DISTINCT stock_code FROM analyst_pdf_extracts WHERE stock_code IS NOT NULL)
        SELECT
          CASE
            WHEN market_cap >= 100000 THEN '1조원+'
            WHEN market_cap >= 30000 THEN '3000억~1조'
            WHEN market_cap >= 10000 THEN '1000억~3000억'
            WHEN market_cap >= 5000 THEN '500억~1000억'
            ELSE '500억 미만'
          END AS bucket,
          COUNT(*) AS stocks,
          SUM(CASE WHEN a.stock_code IS NOT NULL THEN 1 ELSE 0 END) AS analyst_covered
        FROM u LEFT JOIN a USING(stock_code)
        GROUP BY bucket
        ORDER BY MIN(market_cap) DESC
        """,
    )
    for r in bucket_rows:
        r["coverage_pct"] = round((r["analyst_covered"] or 0) * 100.0 / (r["stocks"] or 1), 2)

    smallcap_samples = rows(
        conn,
        """
        WITH a AS (SELECT DISTINCT stock_code FROM analyst_pdf_extracts WHERE stock_code IS NOT NULL)
        SELECT u.stock_code, u.stock_name, u.market, u.market_cap,
               CASE WHEN a.stock_code IS NOT NULL THEN 1 ELSE 0 END AS has_local_analyst_pdf
        FROM stock_universe u
        LEFT JOIN a ON a.stock_code = u.stock_code
        WHERE u.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND u.market_cap BETWEEN 4000 AND 7000
          AND u.market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
          AND COALESCE(u.kind_stkcert_nm,'') LIKE '%보통%'
        ORDER BY has_local_analyst_pdf ASC, u.market_cap DESC
        LIMIT 30
        """,
    )
    checks = provider_checks(smallcap_samples, env)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "policy": "무료/유료 외부 API는 샘플 커버리지 확인 전 전략 로직에 연결하지 않는다.",
        "local_analyst_pdf_coverage_by_market_cap": bucket_rows,
        "smallcap_provider_sample_universe": smallcap_samples,
        "provider_checks": checks,
    }

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = OUT_DIR / f"external_provider_sample_audit_{stamp}.json"
    md_path = OUT_DIR / f"external_provider_sample_audit_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# External Provider Sample Audit — {stamp}",
        "",
        payload["policy"],
        "",
        "## Local Analyst PDF Coverage",
        "|bucket|stocks|covered|coverage|",
        "|---|---:|---:|---:|",
    ]
    for r in bucket_rows:
        lines.append(f"|{r['bucket']}|{r['stocks']:,}|{r['analyst_covered']:,}|{r['coverage_pct']}%|")
    lines += [
        "",
        "## Small-cap Sample Universe",
        "|stock|market_cap|local analyst pdf|",
        "|---|---:|---:|",
    ]
    for r in smallcap_samples[:20]:
        lines.append(f"|{r['stock_name']}({r['stock_code']})|{r['market_cap'] or 0:,.0f}|{r['has_local_analyst_pdf']}|")
    lines += ["", "## Provider Checks", "|provider|ok|note|", "|---|---:|---|"]
    for r in checks:
        note = r.get("reason") or r.get("payload_preview") or ""
        lines.append(f"|{r['provider']}|{r['ok']}|{str(note).replace('|','/')[:240]}|")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
