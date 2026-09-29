"""
ETF_check/etf_delisting_watch.py — 상장폐지(추정) ETF 자동 감지·제외 (2026-09-29 신규)

문제: KIS 마스터파일(etf_universe_sync_v3.SOURCE_VERSION)이 KRX 상장폐지를 즉시 반영하지
않아(관찰 사례: 488210 KIWOOM K-반도체북미공급망 — KRX 상장폐지 공시 9/22, 9/23부터 거래량 0,
그런데도 마스터파일엔 계속 남아 있어 유니버스에서 안 빠짐), full_pdf_audit.health()의
all-or-nothing 판정(빈 PDF 1개라도 있으면 그날 전체가 "미완료")이 한 종목 때문에 계속 걸려
"ETF 데이터 갱신 안 됨" 경고가 영구적으로 뜨는 결함이 있었다(사용자 보고, 2026-09-29).

해법: 같은 티커가 최근 N개(기본 2) 수집일 연속으로 빈 PDF/오류를 반환하면(1175개 중 딱 이
티커들만 — 전체 장애라면 대부분 티커가 같이 비어있을 것이므로 구분됨) "상장폐지 추정"으로
자동 분류해 `etf_delisting_exclusion` 테이블에 기록하고, health() 판정에서 제외한다. 나중에
그 티커가 다시 성공하면(예: 일시적 사이트 오류였을 뿐 실제로는 살아있었던 경우) 자동으로
제외 해제한다 — 되돌릴 수 있는, 자기 교정형 설계.

매일 파이프라인(run_etf_daily_pipeline.sh)의 full_pdf 수집 직후에 이 스크립트를 실행하도록
연결한다(신규 상장폐지가 생길 때마다 자동으로 계속 처리되게).

실행: cd runtime && venv/bin/python3 ETF_check/etf_delisting_watch.py [--threshold 2]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).with_name("etf_check.db")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_delisting_exclusion (
            etf_ticker TEXT PRIMARY KEY,
            etf_name TEXT,
            first_empty_date TEXT NOT NULL,
            last_empty_date TEXT NOT NULL,
            consecutive_empty_days INTEGER NOT NULL,
            excluded_at TEXT NOT NULL,
            note TEXT
        )
        """
    )
    conn.commit()


def _recent_status(conn: sqlite3.Connection, ticker: str, n: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT base_date, status FROM etf_pdf_full_snapshot WHERE etf_ticker=? ORDER BY base_date DESC LIMIT ?",
        (ticker, n),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def sweep(conn: sqlite3.Connection, threshold: int = 2) -> dict:
    ensure_table(conn)
    now = datetime.now().isoformat(timespec="seconds")

    tickers = [r[0] for r in conn.execute("SELECT etf_ticker FROM etf_meta WHERE is_active=1")]
    already = {r[0] for r in conn.execute("SELECT etf_ticker FROM etf_delisting_exclusion")}

    newly_excluded, restored = [], []

    for ticker in tickers:
        recent = _recent_status(conn, ticker, threshold)
        is_persistently_empty = len(recent) >= threshold and all(s in ("empty", "error") for _, s in recent)

        if is_persistently_empty and ticker not in already:
            name_row = conn.execute("SELECT etf_name FROM etf_meta WHERE etf_ticker=?", (ticker,)).fetchone()
            conn.execute(
                """INSERT INTO etf_delisting_exclusion
                   (etf_ticker, etf_name, first_empty_date, last_empty_date, consecutive_empty_days, excluded_at, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ticker, name_row[0] if name_row else None, recent[-1][0], recent[0][0], len(recent), now,
                 "auto: consecutive empty/error PDF, likely delisted (KIS master file lag)"),
            )
            newly_excluded.append(ticker)
        elif ticker in already:
            # 자기 교정: 최신 수집이 성공이면 다시 살아있는 것으로 보고 제외 해제
            latest = _recent_status(conn, ticker, 1)
            if latest and latest[0][1] == "success":
                conn.execute("DELETE FROM etf_delisting_exclusion WHERE etf_ticker=?", (ticker,))
                restored.append(ticker)

    conn.commit()
    return {
        "threshold_days": threshold,
        "newly_excluded": newly_excluded,
        "restored": restored,
        "total_excluded": conn.execute("SELECT COUNT(*) FROM etf_delisting_exclusion").fetchone()[0],
        "checked_at": now,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=int, default=2)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        result = sweep(conn, threshold=args.threshold)
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
