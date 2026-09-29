#!/usr/bin/env python3
"""Build non-destructive corporate-action and price-basis metadata tables."""
from __future__ import annotations

from db_compat import connect_primary_db
import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"

DDL = """
CREATE TABLE IF NOT EXISTS price_series_registry (
  series_name TEXT PRIMARY KEY,
  price_basis TEXT NOT NULL,
  intended_use TEXT NOT NULL,
  source_detail TEXT NOT NULL,
  mixed_basis_risk INTEGER NOT NULL DEFAULT 0,
  policy_note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corporate_action_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_code TEXT NOT NULL,
  event_date TEXT NOT NULL,
  event_type TEXT NOT NULL,
  old_shares REAL,
  new_shares REAL,
  share_ratio REAL,
  backward_price_factor REAL,
  evidence_report_name TEXT,
  evidence_rcept_no TEXT,
  evidence_url TEXT,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  adjustment_status TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(stock_code, event_date, event_type)
);
CREATE INDEX IF NOT EXISTS idx_cae_code_date ON corporate_action_events(stock_code, event_date);
CREATE INDEX IF NOT EXISTS idx_cae_status ON corporate_action_events(adjustment_status, event_type);
CREATE TABLE IF NOT EXISTS corporate_action_no_price_effect (
  event_id BIGINT PRIMARY KEY, stock_code TEXT, event_date TEXT, method TEXT, reason TEXT, evidence_rcept_no TEXT, classified_at TEXT
);
CREATE VIEW stock_price_daily_adjusted_v AS
WITH ev0 AS (
  -- 2026-09-24: 권리락 없는 유상증자(제3자배정·일반공모)는 가격 조정 제외(corporate_action_no_price_effect)
  SELECT id, stock_code, event_date, backward_price_factor, source, confidence
  FROM corporate_action_events
  WHERE adjustment_status='factor_confirmed' AND backward_price_factor>0
    AND NOT EXISTS (SELECT 1 FROM corporate_action_no_price_effect x WHERE x.event_id=corporate_action_events.id)
),
ev1 AS (
  -- (1) 같은 유상증자의 정정공시마다 동일 계수가 날짜만 달리 확정된 경우(2026-09-24: 62종목
  --     277쌍, 예 001140 0.7905×7회)를 1건으로: 60일 안에 같은 계수가 뒤에 또 있으면 앞 건 제외
  --     (정정공시는 권리락일 이전이므로 가장 늦은 건을 남긴다).
  SELECT e.* FROM ev0 e
  WHERE NOT EXISTS (
    SELECT 1 FROM ev0 l
    WHERE l.stock_code=e.stock_code AND l.id<>e.id
      AND ABS(l.backward_price_factor-e.backward_price_factor) <= 1e-6*e.backward_price_factor
      AND (l.event_date > e.event_date OR (l.event_date = e.event_date AND l.id > e.id))
      AND CAST(l.event_date AS DATE) <= CAST(e.event_date AS DATE) + 60
  )
),
ev AS (
  -- (2) 같은 날 같은 방향(희석<1 / 병합>1) 사건이 여러 파이프라인에서 각각 확정된 경우(40건)도
  --     1건만: DART 출처 우선 → confidence 높은 순.
  SELECT stock_code, event_date, backward_price_factor,
         ROW_NUMBER() OVER (
           PARTITION BY stock_code, event_date, CASE WHEN backward_price_factor>1 THEN 1 ELSE 0 END
           ORDER BY CASE WHEN source LIKE 'DART%' THEN 0 ELSE 1 END, confidence DESC, id
         ) AS dn
  FROM ev1
),
base AS (
  SELECT s.*,
         COALESCE((
           SELECT EXP(SUM(LN(e.backward_price_factor)))
           FROM ev e
           WHERE e.stock_code=s.stock_code
             AND e.dn=1
             AND e.event_date > substr(s.bas_dt,1,4)||'-'||substr(s.bas_dt,5,2)||'-'||substr(s.bas_dt,7,2)
         ), 1.0) AS adjustment_factor
  FROM stock_price_daily s
)
SELECT bas_dt, stock_code, stock_name, market,
       open_price*adjustment_factor AS open_price,
       high_price*adjustment_factor AS high_price,
       low_price*adjustment_factor AS low_price,
       close_price*adjustment_factor AS close_price,
       CASE WHEN adjustment_factor>0 THEN volume/adjustment_factor END AS volume,
       trade_amt, market_cap, shares,
       adjustment_factor,
       CASE WHEN adjustment_factor=1.0 THEN 'raw_no_confirmed_action' ELSE 'confirmed_actions_adjusted' END AS adjustment_status
FROM base;
"""


def _compact(value: str | None) -> str:
    return "".join(str(value or "").split())


def _classify_by_report(name: str) -> str | None:
    n = _compact(name)
    if "주식병합" in n or "액면병합" in n or "자본감소" in n or "감자" in n:
        return "stock_merge_or_reduction"
    if "주식분할" in n or "액면분할" in n:
        return "stock_split"
    if "무상증자" in n:
        return "bonus_issue"
    if "유상증자" in n:
        return "rights_issue"
    return None


def _nearest_disclosure(conn: sqlite3.Connection, code: str, event_date: str) -> sqlite3.Row | None:
    # 2026-09-24: dart_disclosures.rcept_dt는 'YYYY-MM-DD'인데 예전엔 'YYYYMMDD' 범위로 BETWEEN 해서
    # 같은 해 구간은 항상 0건(예 '2026-08-01' >= '20260715'가 거짓) → 공시 매칭이 전혀 안 돼
    # 분할·병합이 전부 unclassified로 떨어졌다. 대시 형식으로 조회하고 거리 정렬은 파이썬에서 한다.
    d = datetime.strptime(event_date, "%Y-%m-%d")
    lo = (d - timedelta(days=45)).strftime("%Y-%m-%d")
    hi = (d + timedelta(days=10)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT rcept_dt, report_nm, rcept_no, dart_url
        FROM dart_disclosures
        WHERE stock_code=? AND rcept_dt BETWEEN ? AND ?
          AND (report_nm LIKE '%분할%' OR report_nm LIKE '%병합%'
               OR report_nm LIKE '%무상증자%' OR report_nm LIKE '%유상증자%'
               OR report_nm LIKE '%감자%' OR report_nm LIKE '%자본감소%')
          AND report_nm NOT LIKE '%종속회사%'
        """,
        (code, lo, hi),
    ).fetchall()
    if not rows:
        return None

    def _rank(r):
        # 날짜 거리 → 정정공시([...]) 후순위 → 최신 공시 우선 (예전 ORDER BY와 동일)
        try:
            rd = datetime.strptime(str(r["rcept_dt"])[:10], "%Y-%m-%d")
        except ValueError:
            return (10**6, 1, 0)
        return (abs((rd - d).days), 1 if str(r["report_nm"]).startswith("[") else 0, -rd.toordinal())

    return sorted(rows, key=_rank)[0]


def build(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    conn.row_factory = sqlite3.Row
    # 2026-09-24: DDL이 SQLite 전용 `CREATE VIEW IF NOT EXISTS`여서 PostgreSQL에서 매일
    # SyntaxError → 2026-08-07 이후 이 잡과 후속 체인(시장국면·설명형신호·전진신호·
    # 신호사후성과·전진검증감사)이 전부 멈췄고, 위 DROP만 성공해 뷰도 사라져 있었다.
    # 뷰는 직전에 DROP하므로 plain CREATE VIEW로 충분하다.
    #
    # ON CONFLICT 갱신은 이 스크립트가 만든 원행(source=stock_price_daily_shares[+DART])만
    # 대상으로 하고, 후속 매칭(terp/ratio_reduction/marcap/DART 파이프라인)이 이미
    # factor_confirmed로 확정한 행을 review_required로 되돌리지 않는다.
    conn.execute("DROP VIEW IF EXISTS stock_price_daily_adjusted_v")
    conn.executescript(DDL)
    from price_integrity import ensure_schema, rebuild_views, native_script
    from scripts.audit_price_jumps_and_build_canonical import DDL as AUDIT_DDL
    native_script(conn, AUDIT_DDL)
    ensure_schema(conn)
    rebuild_views(conn)
    now = datetime.now().isoformat(timespec="seconds")
    registry = [
        ("price_history", "adjusted_intended_mixed_risk", "research/backtest signal calculation",
         "KIS adjusted-price collectors plus legacy historical sources", 1,
         "Do not overwrite from raw sources. Use quality view and exclude unexplained jumps.", now),
        ("stock_price_daily", "unadjusted", "execution-price and listed-share verification",
         "public/KRX-style daily OHLCV with market cap and listed shares", 0,
         "Use as raw execution reference; adjustment factors only for confirmed capital actions.", now),
    ]
    conn.executemany(
        """INSERT INTO price_series_registry VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(series_name) DO UPDATE SET price_basis=excluded.price_basis,
           intended_use=excluded.intended_use,source_detail=excluded.source_detail,
           mixed_basis_risk=excluded.mixed_basis_risk,policy_note=excluded.policy_note,updated_at=excluded.updated_at""",
        registry,
    )
    share_rows = conn.execute(
        """
        WITH s AS (
          SELECT stock_code, bas_dt, shares,
                 LAG(shares) OVER(PARTITION BY stock_code ORDER BY bas_dt) AS prev_shares
          FROM stock_price_daily WHERE shares>0
        )
        SELECT stock_code, bas_dt, prev_shares, shares
        FROM s WHERE prev_shares>0 AND ABS(shares/prev_shares-1)>=0.01
        ORDER BY stock_code, bas_dt
        """
    ).fetchall()
    events = []
    counts: dict[str, int] = {}
    for row in share_rows:
        code = row["stock_code"]
        event_date = f"{row['bas_dt'][:4]}-{row['bas_dt'][4:6]}-{row['bas_dt'][6:8]}"
        old_shares, new_shares = float(row["prev_shares"]), float(row["shares"])
        ratio = new_shares / old_shares
        disclosure = _nearest_disclosure(conn, code, event_date)
        report_type = _classify_by_report(disclosure["report_nm"]) if disclosure else None
        if report_type:
            event_type = report_type
            confidence = 0.9 if not str(disclosure["report_nm"]).startswith("[") else 0.8
            source = "stock_price_daily_shares+DART"
        elif ratio >= 1.5:
            event_type, confidence, source = "share_increase_unclassified", 0.55, "stock_price_daily_shares"
        elif ratio <= 0.67:
            event_type, confidence, source = "share_reduction_unclassified", 0.55, "stock_price_daily_shares"
        elif ratio > 1:
            event_type, confidence, source = "rights_or_other_issue", 0.45, "stock_price_daily_shares"
        else:
            event_type, confidence, source = "reduction_or_cancellation", 0.45, "stock_price_daily_shares"

        ratio_consistent = (
            (event_type == "stock_split" and ratio >= 1.5)
            or (event_type == "stock_merge_or_reduction" and ratio <= 0.67)
            or (event_type == "bonus_issue" and ratio >= 1.05)
        )
        adjustable = ratio_consistent and confidence >= 0.8
        if report_type and not ratio_consistent:
            confidence = min(confidence, 0.65)
        price_factor = old_shares / new_shares if adjustable else None
        minor_change = 0.95 <= ratio <= 1.05
        status = "factor_confirmed" if adjustable else ("not_price_adjusting" if minor_change else "review_required")
        note = (
            "Confirmed event; backward raw-price factor is old_shares/new_shares."
            if adjustable else
            "Share change is within 5%; retained for dilution review but no discontinuity factor is required."
            if minor_change else
            "No automatic price rewrite; event economics or type is not sufficiently confirmed."
        )
        events.append((code, event_date, event_type, old_shares, new_shares, ratio, price_factor,
                       disclosure["report_nm"] if disclosure else None,
                       disclosure["rcept_no"] if disclosure else None,
                       disclosure["dart_url"] if disclosure else None,
                       source, confidence, status, note, now, now))
        counts[event_type] = counts.get(event_type, 0) + 1

    if not dry_run:
        conn.executemany(
            """INSERT INTO corporate_action_events(
                 stock_code,event_date,event_type,old_shares,new_shares,share_ratio,backward_price_factor,
                 evidence_report_name,evidence_rcept_no,evidence_url,source,confidence,adjustment_status,note,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(stock_code,event_date,event_type) DO UPDATE SET
                 old_shares=excluded.old_shares,new_shares=excluded.new_shares,share_ratio=excluded.share_ratio,
                 backward_price_factor=excluded.backward_price_factor,evidence_report_name=excluded.evidence_report_name,
                 evidence_rcept_no=excluded.evidence_rcept_no,evidence_url=excluded.evidence_url,source=excluded.source,
                 confidence=excluded.confidence,adjustment_status=excluded.adjustment_status,note=excluded.note,updated_at=excluded.updated_at
               WHERE corporate_action_events.source IN ('stock_price_daily_shares','stock_price_daily_shares+DART')
                 AND (corporate_action_events.adjustment_status <> 'factor_confirmed'
                      OR excluded.adjustment_status = 'factor_confirmed')""",
            events,
        )
        conn.commit()
    # The structural quality view intentionally does not claim that an event
    # explains a return.  That verdict belongs to the downstream jump audit.
    confirmed_events = sum(1 for event in events if event[12] == 'factor_confirmed')
    explained = conn.execute("SELECT COUNT(*) FROM price_jump_audit WHERE classification='confirmed_corporate_action'").fetchone()[0]
    # 2026-09-28: 1천만 행 뷰 전체 COUNT가 statement timeout에 걸려 이미 커밋한 갱신 뒤에
    # 잡 전체가 실패로 끝났고, 그 뒤에 붙은 가격감사·forward 신호 체인이 8/14 이후 매일
    # 건너뛰어졌다. 이 값은 진단용이므로 실패해도 잡을 실패시키지 않는다.
    try:
        unexplained = conn.execute("SELECT COUNT(*) FROM price_history_quality_v WHERE quality_status='unexplained_jump'").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        unexplained = f"unavailable: {type(exc).__name__}"
    return {"share_change_events": len(events), "confirmed_adjustment_events": confirmed_events,
            "event_types": counts, "explained_price_jumps_in_last_audit": explained,
            "unexplained_price_jumps": unexplained, "price_jump_audit_rebuild_required": not dry_run}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    conn = connect_primary_db(timeout=60)
    try:
        # 2026-09-29: 기본 statement_timeout(30s)이 이 스크립트의 stock_price_daily 전체 LAG
        # 윈도우함수 스캔(격리 실행 시 ~18~20초, 스케줄러 동시부하 시 30초 초과)에는 너무 빠듯해서
        # 매일 실패 → 이 스크립트가 이 잡 체인의 첫 단계라 가격급변감사·설명형신호·전략센터전진신호·
        # 신호사후성과·전진검증감사까지 전부 연쇄로 안 돌고 있었음(2026-08-13 이후 live_signal_registry
        # 신규 신호 0건으로 발견). 이 세션에서만 여유를 주고, 전역 30s는 그대로 유지.
        conn.execute("SET statement_timeout='120s'")
        print(build(conn, args.dry_run))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
