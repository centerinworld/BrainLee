"""
backtest_common.py -- shared infrastructure for backtest_strategies/*.py

Split out of backtest.py on 2026-09-03 (token/risk reduction: backtest.py was
14,098 lines holding 35 independent strategies + shared helpers in one file).
This module owns: DB access (DB_PATH / the Postgres-routing sqlite3 shim),
module-level caches/constants, and every helper function used by 2+ strategies
(cost model, chart pattern detectors, PIT/disclosure-date helpers, the generic
portfolio simulator, metrics calculator, etc). Pure relocation -- no behavior
was changed; every function body below is byte-identical to the original.
"""

"""
backtest.py — AI 적극검토 전략 백테스트 엔진 v5

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 매수 조건 (AI 적극검토 콤보 로직과 완전 일치)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [0] ★ 시장 추세 필터 (v5 강화):
      KOSPI 현재가 > KOSPI MA120  (MA60→MA120, 더 엄격한 하락장 차단)

  [A] 추세 스크리너 (Minervini — 전부 필수):
      현재가 > MA120 > MA200 (장기 정배열)
      MA20 > MA60 (단기 정배열 최소 조건)
      현재가 >= 52주 최고가 × 80%
      RSI(14) >= 60
      거래량 > 20일 평균 × 2.0배

  [B] 가치 OR 수급 (하나 이상 필수):
      ▸ 가치: Graham 할인 ≥ 25% OR (PBR < 0.7 AND 0 < PER < 10), AND 영업이익 > 0
      ▸ 수급: 기관 5일 순매수 > 0 AND 외국인 5일 순매수 > 0 (동반 매수)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 매도 조건 (매일 보유 종목 전체 체크, 우선순위 순)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ★ 익절: 매수가 대비 +15% 이상 (v5: 20%→15%, 더 빠른 수익 확정)
  ① 하드 손절: 매수가 대비 -6% 이하
  ★ 추적 손절: 고점 대비 -10% 이하 (수익 보호)
  ② MA60 붕괴: 종가 < MA60
  ★ 최소 보유: 진입 후 5거래일 미만이면 MA 청산 스킵 (단기 변동성 방지)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 미래참조(Look-ahead) 방지 조치
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • 재무 공시일 지연:
      분기보고서: 분기 종료 후 45일 (Q1→5/15, Q2→8/15, Q3→11/15)
      사업보고서(연간): 회계연도 종료 후 90일 (12월 결산→다음해 3/31)
  • 기술 지표: 과거 데이터만 사용 (윈도우 모두 현재 포함 이전)
  • 매수 집행: 시그널 발생 당일 종가 (실무 근사; 더 보수적으로는 익일 시가)
  • 워밍업: start_date 이전 300거래일치 가격 로드 → day1부터 MA200 계산 가능

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 생존자 편향 안내
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  price_history 에 있는 모든 6자리 종목 사용(상폐 종목 포함).
  단, 해당 종목의 가격 데이터가 DB에 없으면 편향 잔존 가능.
"""

import sqlite3 as _sqlite3
import json
import uuid
import math
import re
import argparse
import logging
import bisect
from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from config import IS_POSTGRES
from db_compat import connect_primary_db

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
WARMUP_DAYS = 300   # MA200 + 여유분


class _DatabaseRouter:
    """Route the primary stock DB to PostgreSQL while preserving side DBs."""

    Row = _sqlite3.Row
    Connection = _sqlite3.Connection

    @staticmethod
    def connect(database, *args, **kwargs):
        if IS_POSTGRES and str(database) == DB_PATH:
            return connect_primary_db(timeout=float(kwargs.get("timeout", 30)))
        return _sqlite3.connect(database, *args, **kwargs)


sqlite3 = _DatabaseRouter()


# ══════════════════════════════════════════════════════════════
#  DB 초기화
# ══════════════════════════════════════════════════════════════


def init_backtest_db():
    if IS_POSTGRES:
        return
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id          INTEGER PRIMARY KEY,
            run_id      TEXT UNIQUE,
            name        TEXT,
            start_date  TEXT,
            end_date    TEXT,
            per_stock   REAL DEFAULT 10000000,
            max_pos     INTEGER DEFAULT 10,
            status      TEXT DEFAULT 'running',
            total_return_pct  REAL,
            ann_return_pct    REAL,
            win_rate          REAL,
            total_trades      INTEGER,
            profit_trades     INTEGER,
            max_drawdown_pct  REAL,
            trades_json       TEXT,
            equity_json       TEXT,
            summary_text      TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 런 방법론 메타데이터 (Codex P0-2, 2026-07-13): 같은 전략명 아래 서로 다른
    # 방법론(체결타이밍/시총기준/슬롯방식)의 결과가 섞이는 것을 방지.
    # 프론트/비교는 run_hash 단위로 결과를 특정해야 함.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_run_specs (
            run_id          TEXT PRIMARY KEY,
            strategy        TEXT,
            engine_version  TEXT,
            git_commit      TEXT,
            signal_timing   TEXT,   -- close_D / intraday
            execution_timing TEXT,  -- same_close / next_open
            market_cap_mode TEXT,   -- current / asof_approx / pit
            universe_version TEXT,
            allocation_rule TEXT,   -- fixed_slot / dynamic / compounding
            fee_model       TEXT,
            parameter_json  TEXT,
            run_hash        TEXT,
            supersedes_run_id TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS trg_backtest_done_requires_spec
        BEFORE UPDATE OF status ON backtest_runs
        WHEN NEW.status='done'
          AND NOT EXISTS (SELECT 1 FROM backtest_run_specs s WHERE s.run_id=NEW.run_id)
        BEGIN
          SELECT RAISE(ABORT, 'completed backtest requires immutable run spec');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_backtest_insert_done_requires_spec
        BEFORE INSERT ON backtest_runs
        WHEN NEW.status='done'
        BEGIN
          SELECT RAISE(ABORT, 'insert completed backtest as running, record spec, then publish');
        END;
    """)
    conn.commit()
    conn.close()




_GIT_COMMIT_CACHE = None


def _record_run_spec(run_id: str, strategy: str, engine_version: str,
                     params: dict, signal_timing: str = "close_D",
                     execution_timing: str = "same_close",
                     market_cap_mode: str = "current",
                     allocation_rule: str = "fixed_slot",
                     universe_version: str = "stock_universe_current",
                     fee_model: str = "fee0.015%+tax0.18%+slip_tier") -> None:
    """백테스트 런의 방법론 메타데이터 기록 (Codex P0-2). 실패해도 런은 계속."""
    global _GIT_COMMIT_CACHE
    try:
        import hashlib as _hl, json as _js, subprocess as _sp
        if _GIT_COMMIT_CACHE is None:
            try:
                _GIT_COMMIT_CACHE = _sp.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime", timeout=5,
                ).decode().strip()
            except Exception:
                _GIT_COMMIT_CACHE = "unknown"
        c = sqlite3.connect(DB_PATH, timeout=120)
        from run_registry import source_snapshot as _source_snapshot
        canonical_params = dict(params)
        canonical_params["_source_snapshot"] = _source_snapshot(c)
        from pathlib import Path as _Path
        canonical_params["_code_fingerprint"] = {
            name: _hl.sha256((_Path(__file__).resolve().parent / name).read_bytes()).hexdigest()[:16]
            for name in ("backtest.py", "portfolio_engine.py", "security_master.py", "run_registry.py")
        }
        pj = _js.dumps(canonical_params, ensure_ascii=False, sort_keys=True, default=str)
        run_hash = _hl.sha1(
            f"{strategy}|{engine_version}|{signal_timing}|{execution_timing}|"
            f"{market_cap_mode}|{universe_version}|{allocation_rule}|{fee_model}|"
            f"{pj}|{_GIT_COMMIT_CACHE}".encode()
        ).hexdigest()[:12]
        c.execute("""
            INSERT INTO backtest_run_specs
            (run_id, strategy, engine_version, git_commit, signal_timing,
             execution_timing, market_cap_mode, universe_version,
             allocation_rule, fee_model, parameter_json, run_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (run_id) DO UPDATE SET
              strategy=excluded.strategy,
              engine_version=excluded.engine_version,
              git_commit=excluded.git_commit,
              signal_timing=excluded.signal_timing,
              execution_timing=excluded.execution_timing,
              market_cap_mode=excluded.market_cap_mode,
              universe_version=excluded.universe_version,
              allocation_rule=excluded.allocation_rule,
              fee_model=excluded.fee_model,
              parameter_json=excluded.parameter_json,
              run_hash=excluded.run_hash
        """, (run_id, strategy, engine_version, _GIT_COMMIT_CACHE, signal_timing,
              execution_timing, market_cap_mode, universe_version,
              allocation_rule, fee_model, pj, run_hash))
        c.commit(); c.close()
    except Exception as _e:
        logger.warning(f"[run_spec] 기록 실패 {run_id}: {_e}")




def _register_execution_artifacts(run_id: str, initial_cash: float, final_cash: float,
                                  asof_mktcap: bool = True, markets: tuple = ("KOSPI", "KOSDAQ")) -> None:
    """엄격체결(next_open)+현금원장 엔진 공용 아티팩트 등록.

    2026-07-14 발견/수정: sector/recovery/turnaround/v8/v12/golden_cross가
    strict_exec+현금원장으로 이관됐지만 이 등록 호출이 없어 derive_status()가
    영원히 'legacy'로 고정되는 버그가 있었음(execution_contract/cash_reconciliation
    아티팩트 부재 → execution=False). run_registry.register_artifact 3종을
    공용 헬퍼로 추출해 이관된 6개 엔진 전부에 연결.
    """
    try:
        from run_registry import register_artifact
        conn = sqlite3.connect(DB_PATH, timeout=120)
        spec_row = conn.execute(
            "SELECT run_hash FROM backtest_run_specs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not spec_row or not spec_row[0]:
            conn.close()
            return
        run_hash = spec_row[0]
        register_artifact(run_hash, "execution_contract", True, {
            "signal_timing": "close_D", "execution_timing": "next_open",
            "integer_shares": True, "cash_never_negative": True,
        })
        cash_delta = final_cash - initial_cash  # 순손익은 최종현금-초기현금 자체(현금원장 엔진은 별도 재구성 불필요)
        register_artifact(run_hash, "cash_reconciliation", final_cash >= -1.0, {
            "initial_cash": initial_cash, "final_cash": final_cash, "delta": cash_delta,
        })
        market_ph = ",".join("?" * len(markets))
        # 2026-08-12: 기존엔 security_master_history/security_share_history 전체
        # 기간(2015~)을 대상으로 approx 비율을 계산해, 실제로는 2020-03 이후만
        # 사용하는 전략도 2015~2019년(전체 approx의 대부분) 때문에 영원히
        # point_in_time_coverage 게이트를 통과 못했음. 이 run이 실제로 커버하는
        # start_date~end_date 구간과 겹치는 레코드만 대상으로 좁혀서 재계산.
        run_row = conn.execute(
            "SELECT start_date, end_date FROM backtest_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        r_start, r_end = (run_row[0], run_row[1]) if run_row else (None, None)
        if r_start and r_end:
            pit_counts = dict(conn.execute(f"""
                SELECT interval_quality,COUNT(*) FROM security_master_history
                WHERE market IN ({market_ph}) AND is_tradable=1 AND is_etf_etn=0
                  AND effective_from <= ?
                  AND (effective_to IS NULL OR effective_to >= ?)
                GROUP BY interval_quality
            """, markets + (r_end, r_start)).fetchall()) if asof_mktcap else {}
            share_counts = dict(conn.execute(f"""
                SELECT sh.quality,COUNT(*) FROM security_share_history sh
                WHERE EXISTS (
                  SELECT 1 FROM security_master_history sm
                  WHERE sm.stock_code=sh.stock_code AND sm.market IN ({market_ph})
                    AND sm.is_tradable=1 AND sm.is_etf_etn=0)
                  AND sh.effective_from <= ?
                  AND (sh.effective_to IS NULL OR sh.effective_to >= ?)
                GROUP BY sh.quality
            """, markets + (r_end, r_start)).fetchall()) if asof_mktcap else {}
        else:
            # start_date/end_date를 못 찾으면 안전하게 기존(전체기간) 방식으로 폴백
            pit_counts = dict(conn.execute(f"""
                SELECT interval_quality,COUNT(*) FROM security_master_history
                WHERE market IN ({market_ph}) AND is_tradable=1 AND is_etf_etn=0
                GROUP BY interval_quality
            """, markets).fetchall()) if asof_mktcap else {}
            share_counts = dict(conn.execute(f"""
                SELECT sh.quality,COUNT(*) FROM security_share_history sh
                WHERE EXISTS (
                  SELECT 1 FROM security_master_history sm
                  WHERE sm.stock_code=sh.stock_code AND sm.market IN ({market_ph})
                    AND sm.is_tradable=1 AND sm.is_etf_etn=0)
                GROUP BY sh.quality
            """, markets).fetchall()) if asof_mktcap else {}
        approx_count = sum(v for k, v in pit_counts.items() if "approx" in str(k))
        approx_share_count = sum(v for k, v in share_counts.items() if "approx" in str(k) or "fallback" in str(k))
        register_artifact(run_hash, "point_in_time_coverage", bool(
            asof_mktcap and approx_count == 0 and approx_share_count == 0
        ), {
            "master_counts": pit_counts, "approx_intervals": approx_count,
            "share_counts": share_counts, "approx_share_intervals": approx_share_count,
            "period_scoped": bool(r_start and r_end),
            "run_period": [r_start, r_end],
        })
        conn.close()
    except Exception as _e:
        logger.warning(f"[run_artifact] 기록 실패 {run_id}: {_e}")


# ══════════════════════════════════════════════════════════════
#  거래비용 표준 모델 (V1~V10 + 텐버거 공통 적용)
# ══════════════════════════════════════════════════════════════


FEE_PER_LEG = 0.00015   # 수수료 편도 0.015% (HTS 기준)
SELL_TAX    = 0.00180   # 거래세 0.18% (매도, 2024~ 코스피/코스닥 공통)
# 슬리피지: 시총(억원) 기준 차등 (호가 스프레드 + 시장충격)
_SLIP_TIERS = [
    (10_000, 0.001),   # 1조+  → 0.1%
    ( 1_000, 0.002),   # 1000억~1조 → 0.2%
    (   100, 0.004),   # 100~1000억 → 0.4%
    (     0, 0.008),   # <100억 → 0.8%
]


def _tx_cost(mkt_cap_억: float) -> tuple:
    """(매수비용율, 매도비용율) 반환. 수수료 + 거래세 + 슬리피지."""
    slip = next(r for thr, r in _SLIP_TIERS if mkt_cap_억 >= thr)
    return FEE_PER_LEG + slip, FEE_PER_LEG + SELL_TAX + slip



def _load_pit_shares_history(conn, stock_codes: list) -> dict:
    """2026-08-25: stock_price_daily.shares(발행주식수, 2020-01~현재 3,111종목 커버)를
    종목별 (일자, 주식수) 시계열로 로드한다. 이 값 × 해당일 종가로 "그 시점 실제
    시가총액"을 계산할 수 있어, stock_universe.market_cap(오늘 시점 고정값)을 과거
    구간 필터에 쓰던 룩어헤드 편향(asof_approx/current 등급의 근본 원인)을 해소할
    수 있다. 아직 실제 전략에 배선되지 않은 재사용 헬퍼 — _pit_market_cap()과 함께
    개별 전략에 붙일 때는 반드시 재검증 후 격리 적용할 것(turnaround/regime_adaptive
    조정계수 작업과 동일한 방식 — 한 곳에 배선하고 다른 전략 영향 없음을 확인).
    반환: {stock_code: [(date_str, shares), ...] 날짜 오름차순}
    """
    if not stock_codes:
        return {}
    out: dict = {}
    placeholders = ",".join("?" for _ in stock_codes)
    rows = conn.execute(
        f"""SELECT stock_code, bas_dt, shares FROM stock_price_daily
            WHERE stock_code IN ({placeholders}) AND shares IS NOT NULL AND shares > 0
            ORDER BY stock_code, bas_dt""",
        list(stock_codes),
    ).fetchall()
    for code, bas_dt, shares in rows:
        d = str(bas_dt)
        iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
        out.setdefault(code, []).append((iso, float(shares)))
    return out




def _pit_market_cap_억(shares_history: dict, stock_code: str, date_str: str, close_price: float) -> float | None:
    """date_str 시점 기준 가장 최근 발행주식수 × 그날 종가로 실제 시가총액(억원)을 계산.
    해당 종목의 발행주식수 이력이 아예 없으면 None(호출측에서 fallback 처리)."""
    events = shares_history.get(stock_code)
    if not events or close_price <= 0:
        return None
    shares = None
    for edate, sh in events:
        if edate <= date_str:
            shares = sh
        else:
            break
    if shares is None:
        return None
    return round(shares * close_price / 1e8, 2)  # 원 -> 억원




def _load_corp_action_factors(conn, stock_codes: list, data_asof_ts: str = None) -> dict:
    """2026-08-23: corporate_action_events에서 확정(factor_confirmed)된 조정계수를
    종목별로 미리 로드. 특정 전략(turnaround/regime_adaptive/composite)의 보유기간이
    유상증자·감자 등 확인된 기업행위를 가로지를 때, 원시 종가 비율 대신 이 계수로
    진입가를 보정해 진짜 수익률을 계산하기 위함. 다른 전략은 이 함수를 호출하지
    않으므로 기존 결과에 영향 없음.

    2026-09-04: adjustment_status는 매일 00:10 "기업행위조정계수후속확정" 잡이
    review_required→factor_confirmed로 계속 승격시키는 살아있는 값이라, data_asof_ts
    없이 호출하면 같은 과거 구간을 재실행해도 실행 시점마다 다른 확정 집합을 읽어
    거래건수·손익이 흔들린다(회귀검증 재현성 붕괴의 실제 원인). data_asof_ts를
    'YYYY-MM-DD HH:MM:SS' 형식으로 주면 그 시각까지 확정된 계수만 사용해 재실행해도
    항상 동일한 결과를 보장한다. None(기본값)이면 기존과 동일하게 항상 최신 확정
    상태를 사용 — 라이브 대시보드 동작은 변경 없음.

    반환: {stock_code: [(event_date_str, backward_price_factor), ...] (날짜 오름차순)}
    """
    if not stock_codes:
        return {}
    out: dict = {}
    placeholders = ",".join("?" for _ in stock_codes)
    asof_clause = " AND updated_at <= ?" if data_asof_ts else ""
    params = list(stock_codes) + ([data_asof_ts] if data_asof_ts else [])
    rows = conn.execute(
        f"""SELECT stock_code, event_date, backward_price_factor
            FROM corporate_action_events
            WHERE stock_code IN ({placeholders})
              AND adjustment_status='factor_confirmed'
              AND backward_price_factor IS NOT NULL
              {asof_clause}""",
        params,
    ).fetchall()
    for code, edate, factor in rows:
        out.setdefault(code, []).append((str(edate)[:10], float(factor)))
    for code in out:
        out[code].sort(key=lambda x: x[0])
    return out




def _corp_action_adjusted_entry(factors: dict, code: str, entry_date: str,
                                 exit_date: str, entry_price: float) -> float:
    """entry_date와 exit_date(둘 다 'YYYY-MM-DD') 사이(진입일 초과, 청산일 이하)에
    확정된 기업행위가 있으면, 그 누적 조정계수를 진입가에 곱해 청산일 기준가와
    비교 가능하게 만든다. 이벤트가 없으면 원래 entry_price 그대로 반환."""
    events = factors.get(code)
    if not events:
        return entry_price
    factor = 1.0
    for edate, f in events:
        if entry_date < edate <= exit_date:
            factor *= f
    return entry_price * factor




def _net_profit(entry_p: float, exit_p: float, qty: int,
                mkt_cap_억: float) -> tuple:
    """거래비용 차감 후 순이익(원), 순수익률(소수) 반환."""
    buy_r, sell_r = _tx_cost(mkt_cap_억)
    gross = (exit_p - entry_p) * qty
    cost  = entry_p * qty * buy_r + exit_p * qty * sell_r
    net   = gross - cost
    base  = entry_p * qty
    return round(net), round((net / base) * 100, 2) if base else 0.0


# ══════════════════════════════════════════════════════════════
#  재무 공시일 — 실제 DART 공시일 우선, 없으면 법정기한 fallback
# ══════════════════════════════════════════════════════════════

# (stock_code, year, quarter, is_annual) → avail_date (다음 영업일)


_DISC_DATES: dict = {}


def _load_disc_dates(conn) -> None:
    """fin_disclosure_dates 테이블을 메모리에 로드. 백테스트 시작 시 1회 호출."""
    global _DISC_DATES
    try:
        rows = conn.execute(
            "SELECT stock_code, year, quarter, is_annual, avail_date FROM fin_disclosure_dates"
        ).fetchall()
        _DISC_DATES = {(r[0], r[1], r[2], r[3]): r[4] for r in rows}
    except Exception:
        _DISC_DATES = {}  # 테이블 없으면 formula fallback만 사용




def _release_date(year: int, quarter: int, is_annual: bool, stock_code: str = None) -> str:
    """
    재무 데이터 시장 공개일.
    우선순위: ① DART 실제 공시일 다음 영업일 (fin_disclosure_dates)
              ② 법정기한 공식 (분기+45일, 연간 익년 3월31일)
    """
    if stock_code and _DISC_DATES:
        ia = 1 if is_annual else 0
        q_key = 4 if is_annual else quarter
        key = (stock_code, year, q_key, ia)
        if key in _DISC_DATES:
            return _DISC_DATES[key]
    # formula fallback
    if is_annual:
        return f"{year + 1}-03-31"
    release_map = {
        1: f"{year}-05-15",
        2: f"{year}-08-15",
        3: f"{year}-11-15",
        4: f"{year + 1}-02-15",
    }
    return release_map.get(quarter, f"{year}-12-31")




def _get_financial_as_of(fin_rows: list, target_date: str,
                         stock_code: str = None) -> Optional[tuple]:
    """
    target_date 기준으로 이미 공시된 가장 최신 재무 데이터 반환.
    fin_rows: (year, quarter, rev, op, eps, bps, equity, net_inc, roe, is_annual[, avail_date])
    row[10] = avail_date가 있으면 사용, 없으면 _release_date() 공식 fallback.
    """
    best = None
    best_key = (-1, -1)
    for row in fin_rows:
        y, q = row[0], row[1]
        if y is None or q is None:
            continue
        is_ann = row[-1] if len(row) == 10 else row[9]
        # avail_date embedded in row[10]
        if len(row) > 10 and row[10]:
            release = row[10]
        else:
            release = _release_date(y, q, bool(is_ann), stock_code)
        if release <= target_date:
            key = (y, q)
            if key > best_key:
                best_key = key
                best = row
    return best


# ══════════════════════════════════════════════════════════════
#  Piotroski F-Score (2026-09-01 신규) — 미국 학계 펀더멘털 퀄리티 스코어
#  (Joseph Piotroski, 2000, "Value Investing: The Use of Historical Financial
#  Statement Information to Separate Winners from Losers"). 이 DB에서 계산 가능한
#  7개 컴포넌트로 재현: ROA>0 / ROA개선 / OCF>0 / OCF>NI(발생액 품질, Sloan 1996과
#  동일 원리) / 부채비율감소 / 매출총이익률개선(근사) / 자산회전율개선.
#  Walk-forward 라벨검증(2026-09-01, scratchpad research_pioneer_factors.py) 통과:
#  검증기(2023+) F>=6 평균 14.95% vs F<=2 평균 10.92%, 학습기도 동일 방향(15.09%
#  vs 5.58%) — 두 기간 모두 상위스코어가 하위스코어를 상회, 재현성 확인.
# ══════════════════════════════════════════════════════════════


def _ma(arr: list, n: int) -> Optional[float]:
    """단순 이동평균. arr[-n:]을 사용하므로 항상 과거 데이터만 참조."""
    if len(arr) < n:
        return None
    return sum(arr[-n:]) / n


# ════════════════════════════════════════════════════════════════════
# 차트 컨플루언스 공통 모듈 (2026-07-18, 사용자 지시: 전 전략 공통 적용)
#
# 배경: V-EXTREME 1차 버전이 "거래량 급증"만으로 진입해 패닉투매 절정에서
# 매수하는 사례가 다수 발견됨(-82.8% 참패). 일봉추세+주봉구조+캔들패턴
# 3요소 컨플루언스로 교체 후 -82.8%→+20.6%/승률 20.8%→43.1%로 개선 실증.
# 이에 따라 특정 전략 전용이 아닌 공통 모듈로 승격 — 모든 엔진이
# chart_confluence 파라미터로 동일 로직을 사용한다.
#
# 바닥 판단(매수 게이트): ①일봉 MA5>MA10 ②주봉 higher-low ③상승 캔들패턴
# 고점 판단(매도 트리거): ①일봉 MA5<MA10 ②주봉 lower-high ③하락 캔들패턴
# → 각각 2개 이상 합의(confluence)일 때만 발동. 모든 계산은 당일까지의
#   과거 데이터만 사용(주봉은 "마감된 주"만 집계 — 진행중 주 제외로 룩어헤드 방지).
# ════════════════════════════════════════════════════════════════════



def _chart_prep(dates: list, lows: list, closes: list) -> dict:
    """일봉 시계열 → 주봉/월봉 집계 일괄 준비 (2026-07-18 확장: 월봉 추가).
    반환 dict 키:
      wk_low/wk_close/i2wk — 마감된 주봉 저가/종가 리스트 + 일자인덱스→마감주 개수
      mo_low/mo_close/i2mo — 마감된 월봉 저가/종가 리스트 + 일자인덱스→마감월 개수
    진행중 주/월은 제외(마감된 것만) — 룩어헤드 방지."""
    wk_low: list = []; wk_close: list = []; i2wk: list = []
    mo_low: list = []; mo_close: list = []; i2mo: list = []
    wk_key = None; wlo = None; wcl = None
    mo_key = None; mlo = None; mcl = None
    for wi, d in enumerate(dates):
        wkey = datetime.strptime(d, '%Y-%m-%d').isocalendar()[:2]
        mkey = d[:7]
        if wk_key is None:
            wk_key = wkey; wlo = lows[wi]; wcl = closes[wi]
        elif wkey == wk_key:
            wlo = min(wlo, lows[wi]); wcl = closes[wi]
        else:
            wk_low.append(wlo); wk_close.append(wcl)
            wk_key = wkey; wlo = lows[wi]; wcl = closes[wi]
        if mo_key is None:
            mo_key = mkey; mlo = lows[wi]; mcl = closes[wi]
        elif mkey == mo_key:
            mlo = min(mlo, lows[wi]); mcl = closes[wi]
        else:
            mo_low.append(mlo); mo_close.append(mcl)
            mo_key = mkey; mlo = lows[wi]; mcl = closes[wi]
        i2wk.append(len(wk_low))
        i2mo.append(len(mo_low))
    return {"wk_low": wk_low, "wk_close": wk_close, "i2wk": i2wk,
            "mo_low": mo_low, "mo_close": mo_close, "i2mo": i2mo}


# 구버전 호환 (3-tuple) — 기존 호출부가 남아있을 경우를 위한 어댑터


def _chart_weekly_prep(dates: list, lows: list, closes: list) -> tuple:
    ch = _chart_prep(dates, lows, closes)
    return ch["wk_low"], ch["wk_close"], ch["i2wk"]




def _chart_rsi14(closes: list, i: int) -> Optional[float]:
    """단순평균 RSI14 (Wilder 지수평활 아님 — 백테스트 일관성 우선, 과거데이터만 사용)."""
    if i < 14:
        return None
    gains = losses = 0.0
    for j in range(i - 13, i + 1):
        ch = closes[j] - closes[j - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)




def _chart_bullish_candle(opens: list, highs: list, lows: list, closes: list, i: int) -> bool:
    """해머 또는 상승장악형 — 최근 1~3일 내 하나라도 있으면 True."""
    for j in range(max(0, i - 2), i + 1):
        if j < 1:
            continue
        o, h, lo, c = opens[j], highs[j], lows[j], closes[j]
        if o <= 0:
            continue
        body = abs(c - o)
        rng = h - lo
        if rng <= 0:
            continue
        lower_wick = min(o, c) - lo
        upper_wick = h - max(o, c)
        is_hammer = body > 0 and lower_wick >= body * 2.0 and upper_wick <= body * 0.5
        is_engulf = (closes[j-1] < opens[j-1] and c > o and
                     o <= closes[j-1] and c >= opens[j-1] and opens[j-1] > 0)
        if is_hammer or is_engulf:
            return True
    return False




def _chart_bearish_candle(opens: list, highs: list, lows: list, closes: list, i: int) -> bool:
    """유성형 또는 하락장악형 — 최근 1~3일 내 하나라도 있으면 True."""
    for j in range(max(0, i - 2), i + 1):
        if j < 1:
            continue
        o, h, lo, c = opens[j], highs[j], lows[j], closes[j]
        if o <= 0:
            continue
        body = abs(c - o)
        rng = h - lo
        if rng <= 0:
            continue
        lower_wick = min(o, c) - lo
        upper_wick = h - max(o, c)
        is_star = body > 0 and upper_wick >= body * 2.0 and lower_wick <= body * 0.5
        is_engulf = (closes[j-1] > opens[j-1] and c < o and
                     o >= closes[j-1] and c <= opens[j-1] and opens[j-1] > 0)
        if is_star or is_engulf:
            return True
    return False


# ── 컨플루언스 구성/임계값 (2026-07-18 튜닝 확정) ──
# 실측(연속운용 2020-03~2026-03, v2/recovery/extreme 3전략 × 임계 2/3/4 전수):
#   6요소(월봉+RSI+구조돌파 포함) 버전은 모든 조합에서 3요소 대비 열등
#   (v2: 170.3→154.6/133.2/84.2, extreme: 20.6→14.9/-20.5/-14.8, recovery도 동일 방향).
#   월봉·RSI는 시차가 커서 일 단위 진입 타이밍에는 노이즈로 작용함이 실증됨.
# → 기본 구성은 검증된 핵심 3요소(daily/weekly/candle) + 임계 2로 확정.
#   monthly/rsi/structure 컴포넌트는 코드에 보존(_CHART_COMPONENTS에 추가하면 활성화)
#   — 단 재활성화 전 반드시 signal_experiment_ledger의 기각 기록 확인할 것.


_CHART_COMPONENTS = ('daily', 'weekly', 'candle')
_CHART_BOTTOM_MIN = 2
_CHART_TOP_MIN = 2


def _chart_bottom_confluence(closes: list, opens: list, highs: list, lows: list,
                             chart: dict, i: int) -> int:
    """찐바닥 컨플루언스 점수(0~6) — 2026-07-18 사용자 지시로 6요소 확장.
    ①일봉추세 MA5>MA10  ②주봉 higher-low  ③상승 캔들패턴(해머/장악형)
    ④월봉 반등(마감월 저가 상승 or 종가 반등)  ⑤RSI14 과매도(<35) 터치 후 상승 전환
    ⑥하락구조 이탈(종가가 직전 20일 종가 최고 돌파)"""
    chart = chart or {}
    comps = _CHART_COMPONENTS
    score = 0
    # ① 일봉 추세
    if 'daily' in comps and i >= 10:
        ma5 = sum(closes[i-4:i+1]) / 5
        ma10 = sum(closes[i-9:i+1]) / 10
        if ma5 > ma10:
            score += 1
    # ② 주봉 higher-low
    if 'weekly' in comps:
        wk_low = chart.get("wk_low", []); i2wk = chart.get("i2wk", [])
        n_wk = i2wk[i] if i < len(i2wk) else 0
        if n_wk >= 5:
            last_low = wk_low[n_wk-1]
            prev_lows = wk_low[max(0, n_wk-5):n_wk-1]
            if prev_lows and last_low > min(prev_lows):
                score += 1
    # ③ 캔들 반전패턴
    if 'candle' in comps and _chart_bullish_candle(opens, highs, lows, closes, i):
        score += 1
    # ④ 월봉 반등 (기본 비활성 — 실측 열등, 위 주석 참조)
    if 'monthly' in comps:
        mo_low = chart.get("mo_low", []); mo_close = chart.get("mo_close", []); i2mo = chart.get("i2mo", [])
        n_mo = i2mo[i] if i < len(i2mo) else 0
        if n_mo >= 4:
            m_higher_low = mo_low[n_mo-1] > min(mo_low[max(0, n_mo-4):n_mo-1])
            m_close_up = mo_close[n_mo-1] > mo_close[n_mo-2]
            if m_higher_low or m_close_up:
                score += 1
    # ⑤ RSI 과매도 반전 (기본 비활성)
    if 'rsi' in comps and i >= 30:
        r_now = _chart_rsi14(closes, i)
        r_prev = _chart_rsi14(closes, i - 3)
        if r_now is not None and r_prev is not None and r_now > r_prev:
            r_min = min(x for x in (_chart_rsi14(closes, j) for j in range(i - 14, i + 1))
                        if x is not None)
            if r_min < 35:
                score += 1
    # ⑥ 하락구조 이탈 (기본 비활성)
    if 'structure' in comps and i >= 21 and closes[i] > max(closes[i-20:i]):
        score += 1
    return score




def _chart_top_confluence(closes: list, opens: list, highs: list, lows: list,
                          chart: dict, i: int) -> int:
    """찐고점 컨플루언스 점수(0~6) — 바닥 판단의 대칭.
    ①일봉추세 MA5<MA10  ②주봉 lower-high  ③하락 캔들패턴(유성/장악형)
    ④월봉 꺾임(마감월 종가 하락)  ⑤RSI14 과열(>65) 터치 후 하락 전환
    ⑥지지 이탈(종가가 직전 20일 종가 최저 하향돌파)"""
    chart = chart or {}
    comps = _CHART_COMPONENTS
    score = 0
    if 'daily' in comps and i >= 10:
        ma5 = sum(closes[i-4:i+1]) / 5
        ma10 = sum(closes[i-9:i+1]) / 10
        if ma5 < ma10:
            score += 1
    if 'weekly' in comps:
        wk_close = chart.get("wk_close", []); i2wk = chart.get("i2wk", [])
        n_wk = i2wk[i] if i < len(i2wk) else 0
        if n_wk >= 5:
            last_close = wk_close[n_wk-1]
            prev_closes = wk_close[max(0, n_wk-5):n_wk-1]
            if prev_closes and last_close < max(prev_closes):
                score += 1
    if 'candle' in comps and _chart_bearish_candle(opens, highs, lows, closes, i):
        score += 1
    if 'monthly' in comps:
        mo_close = chart.get("mo_close", []); i2mo = chart.get("i2mo", [])
        n_mo = i2mo[i] if i < len(i2mo) else 0
        if n_mo >= 4 and mo_close[n_mo-1] < mo_close[n_mo-2]:
            score += 1
    if 'rsi' in comps and i >= 30:
        r_now = _chart_rsi14(closes, i)
        r_prev = _chart_rsi14(closes, i - 3)
        if r_now is not None and r_prev is not None and r_now < r_prev:
            r_max = max(x for x in (_chart_rsi14(closes, j) for j in range(i - 14, i + 1))
                        if x is not None)
            if r_max > 65:
                score += 1
    if 'structure' in comps and i >= 21 and closes[i] < min(closes[i-20:i]):
        score += 1
    return score





def _graham_price(eps: float, bps: float) -> Optional[float]:
    """Graham 내재가치: √(22.5 × EPS × BPS)."""
    if eps and bps and eps > 0 and bps > 0:
        return math.sqrt(22.5 * eps * bps)
    return None




def _rsi(prices: list, n: int = 14) -> Optional[float]:
    """
    RSI(n) 계산. prices는 최신이 마지막(ASC).
    signal_engine.py 의 _calc_price_indicators 와 동일 로직.
    """
    if len(prices) < n + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(len(prices) - n, len(prices))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_g  = sum(gains)  / n
    avg_l  = sum(losses) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)




def _52w_pct(prices: list, i: int) -> float:
    """
    52주 가격 범위 내 현재 위치 (0%=52주 저점, 100%=52주 고점).
    실증 결과: ≥65% 구간이 winRate 1.54x (최강 알파).
    """
    window = prices[max(0, i - 252): i + 1]
    lo = min(window)
    hi = max(window)
    if hi <= lo:
        return 50.0
    return (prices[i] - lo) / (hi - lo) * 100.0




def _score_entry(i: int, prices: list, volumes: list,
                 sc: str = None, day: str = None,
                 hs_data: dict = None) -> float:
    """
    매수 후보 점수화 함수 (높을수록 우선 매수).
    ① 거래량 비율 (0.35): 오늘 거래량 / 20일 평균 (최대 3배 클리핑)
    ② RSI 점수 (0.35): RSI 55 근방이 최적 (0~1 정규화)
    ③ 진입 품질 (0.20): MA20 대비 괴리 (MA20과 가까울수록 우량 진입)
    ④ HS 수출 보너스 (0.10): 2개월 전 수출 YoY > 10% 시 보너스
    """
    curr = prices[i] if prices[i] and prices[i] > 0 else 0
    if curr <= 0:
        return 0.0

    # ① 거래량 비율
    vol_window = [v for v in volumes[max(0, i-19):i] if v and v > 0]
    avg_vol20 = sum(vol_window) / len(vol_window) if vol_window else 0
    vol_ratio = (volumes[i] / avg_vol20) if avg_vol20 > 0 else 1.0
    vol_score = min(vol_ratio, 3.0) / 3.0  # 0~1

    # ② RSI 점수 (60~80 최고 — 실증: RSI>70=1.25x lift, 과매수가 유리)
    rsi_val = _rsi(prices[max(0, i-30):i+1], 14)
    if rsi_val is None:
        rsi_score = 0.5
    elif 60 <= rsi_val <= 80:
        rsi_score = 1.0                          # 실증 최고 구간
    elif rsi_val > 80:
        rsi_score = max(0.3, 1.0 - (rsi_val - 80) / 40.0)  # 극과매수 소폭 감점
    else:
        rsi_score = max(0.0, rsi_val / 60.0)    # 60 미만은 선형 감점

    # ③ 진입 품질: MA20 대비 괴리율 (MA20 위 5% 이내가 최선)
    ma20 = _ma(prices[max(0, i-19):i+1], 20)
    if ma20 and ma20 > 0:
        gap = (curr - ma20) / ma20  # 0~0.05 = 좋음, >0.1 = 과열
        entry_quality = max(0.0, 1.0 - gap / 0.10)
    else:
        entry_quality = 0.5

    # ④ HS 수출 보너스 (선택적)
    hs_bonus = 0.0
    if hs_data and sc and day and sc in hs_data:
        # 2개월 전 데이터 참조 (무역통계 발표 지연 보정)
        y, m = int(day[:4]), int(day[5:7])
        m -= 2
        if m <= 0:
            m += 12; y -= 1
        ref_ym = f"{y}-{m:02d}"
        yoy = _get_export_yoy(hs_data[sc], ref_ym)
        if yoy is not None:
            if yoy >= 20:
                hs_bonus = 0.10   # 수출 20%+ 고성장
            elif yoy >= 10:
                hs_bonus = 0.05   # 수출 10%+ 성장
            elif yoy >= 0:
                hs_bonus = 0.02   # 수출 안정

    base = vol_score * 0.35 + rsi_score * 0.35 + entry_quality * 0.20
    return base + hs_bonus


# ══════════════════════════════════════════════════════════════
#  매수 시그널 — AI 적극검토 콤보 로직 완전 재현
# ══════════════════════════════════════════════════════════════


def _is_buy_signal(
    i: int,            # 현재 인덱스 (전체 배열 기준, 워밍업 포함)
    sim_start_i: int,  # 시뮬레이션 시작 인덱스 (워밍업 이후)
    dates: list,
    prices: list,
    volumes: list,
    frn_net: list,
    inst_net: list,
    fin_rows: list,
) -> bool:
    """
    AI 적극검토 콤보 로직 재현 (signal_engine.py 와 동일 기준):

    [A] 추세 스크리너 — 전부 필수 (Minervini)
      ① 장기 정배열: curr > MA120 > MA200
      ② 단기 정배열: MA20 > MA60 이상
      ③ 52주 고점 -20% 이내
      ④ RSI(14) ≥ 60  (TREND_RSI_MIN=60)
      ⑤ 거래량 > 20일평균 × 2.0배  (TREND_VOL_RATIO_MIN=2.0)

    [B] 가치 OR 수급 — 하나 이상 충족 (콤보 추가 스크리너)
      ▸ 가치: Graham 할인 ≥ 25% OR (PBR < 0.7 AND 0 < PER < 10), AND 영업이익 > 0
      ▸ 수급: 기관 5일 순매수 > 0 AND 외국인 5일 순매수 > 0 (동반 매수)
    """
    if i < sim_start_i:
        return False

    curr = prices[i]
    d    = dates[i]

    # ══ [A] 추세 스크리너 (Minervini — 전부 필수) ══════════════
    p_slice = prices[max(0, i - 199): i + 1]
    if len(p_slice) < 120:
        return False

    ma20  = sum(p_slice[-20:]) / 20  if len(p_slice) >= 20  else None
    ma60  = sum(p_slice[-60:]) / 60  if len(p_slice) >= 60  else None
    ma120 = sum(p_slice[-120:]) / 120 if len(p_slice) >= 120 else None
    ma200 = sum(p_slice) / len(p_slice) if len(p_slice) >= 200 else None

    if ma120 is None or ma200 is None or ma20 is None or ma60 is None:
        return False

    # ① 장기 정배열
    if not (curr > ma120 and curr > ma200 and ma120 > ma200):
        return False

    # ② 단기 정배열 (MA20 > MA60 최소 조건)
    if not (ma20 > ma60):
        return False

    # ③ 52주 고점 -20% 이내
    high52 = max(prices[max(0, i - 251): i + 1])
    if curr < high52 * 0.80:
        return False

    # ④ RSI(14) ≥ 60
    rsi_val = _rsi(prices[max(0, i - 28): i + 1])
    if rsi_val is None or rsi_val < 60:
        return False

    # ⑤ 거래량 > 20일 평균 × 2.0배
    vol_window = [v for v in volumes[max(0, i - 20): i] if v and v > 0]
    if not vol_window or not volumes[i] or volumes[i] <= 0:
        return False
    avg20v = sum(vol_window) / len(vol_window)
    # RSI강도에 따라 볼륨 기준 차등 (실증: RSI>70=1.25x, 하락장 반등필터)
    vol_threshold = 1.5 if (rsi_val and rsi_val >= 70) else 2.0
    if volumes[i] <= avg20v * vol_threshold:
        return False

    # ══ [B] 가치 OR 수급 OR RS강도 (하나 이상 필수) ════════════
    # ※ 수급 데이터가 부족한 구간(57일치 제한)을 RS 상대강도로 보완
    # signal_engine 콤보 = 추세 + (가치 OR 재무). 재무 대용으로 RS 사용.

    # 가치 스크리너 (Graham 공시일 지연 적용)
    value_ok = False
    fin = _get_financial_as_of(fin_rows, d)
    if fin is not None:
        _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann, *_ = fin
        if op and op > 0 and bps and bps > 0 and eps and eps > 0:
            pbr  = curr / bps
            per  = curr / eps
            gp   = _graham_price(eps, bps)
            disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
            # 심층 저평가: Graham 25% 이상 OR (PBR<0.7 AND PER<10)
            value_ok = (disc >= 25) or (pbr < 0.7 and 0 < per < 10)

    # 수급 스크리너: 기관 AND 외국인 5일 동반 순매수 (실시간 데이터)
    frn5  = sum(frn_net[max(0, i - 4): i + 1])
    inst5 = sum(inst_net[max(0, i - 4): i + 1])
    supply_ok = (frn5 > 0 and inst5 > 0)

    # RS 상대강도: 종목 3개월 수익률이 KOSPI보다 +5%p 이상 아웃퍼폼
    # (재무 스크리너 대용 — signal_engine RS 계산과 동일 기준)
    rs_ok = False
    if len(prices) >= 63 and i >= 62:
        stock_3m = (prices[i] - prices[i - 62]) / prices[i - 62] * 100 if prices[i - 62] > 0 else 0
        # kospi_ret는 호출 함수에서 주입되지 않으므로 price_history 없이 근사:
        # 동일 인덱스 기준으로는 계산 불가 → 단순 절대 수익률 기준으로 대체
        # 3개월 수익률 > +5% (상대강도 최소 기준)
        rs_ok = stock_3m > 5.0

    return value_ok or supply_ok or rs_ok


# ══════════════════════════════════════════════════════════════
#  매도 조건 — 날짜별 단일 계산
# ══════════════════════════════════════════════════════════════


def _check_sell(
    i: int,
    prices: list,
    pos: dict,
    stop_loss: float = -0.08,
    take_profit: float = 0.25,
    trail_stop: float = -0.10,
    big_gate: float = None,   # 2026-08-10: None이면 기존 동작(항상 take_profit/trail_stop) 완전 동일.
                               # 값 지정 시(예: 0.5) 진입가 대비 수익률이 이 임계 이상이면 익절을
                               # 사실상 해제(9999)하고 trail_big(넓은 추적손절)만 적용 — V-GC의
                               # 검증된 "메가수익 확장구간" 패턴(+50%↑ trail -35%로 완화) 재사용.
                               # 텐버거 실증(capture_rate_analysis_20260810): 익절 있는 v4/v2/sector는
                               # 텐버거 거래 평균실현 3.6~17.5%인데 익절없는 전략은 16.7~75.0% — 대부분
                               # 거래(회전형 v4의 핵심 특성)는 그대로 두고 극소수 대박 조짐 포지션만
                               # 추가로 태우기 위한 조건부 완화.
    trail_big: float = -0.35,
) -> Optional[str]:
    """
    매도 사유 문자열 반환, 없으면 None.
    take_profit / trail_stop: 시장 국면에 따라 동적 조정
      - 극강세(KOSPI>MA60×1.15): TP=9999(없음), trail=-0.20  ← winner 충분히 보유
      - 강세(>×1.05):             TP=0.75,   trail=-0.15
      - 보통(>×1.0):              TP=0.35,   trail=-0.12
      - 약세(<×1.0):              TP=0.20,   trail=-0.10
    """
    curr = prices[i]

    # 고점 업데이트
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct      = (curr - pos['entry_price']) / pos['entry_price']
    peak     = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1   # 매 호출 시 보유일 증가

    # 2026-08-10: 대박 확장구간 게이트 — pct가 big_gate 이상이면 익절 해제, trail만 넓게
    eff_take_profit = take_profit
    eff_trail_stop  = trail_stop
    if big_gate is not None and pct >= big_gate:
        eff_take_profit = 9999.0
        eff_trail_stop  = trail_big

    # ★ 익절 (극강세장이면 사실상 없음)
    if pct >= eff_take_profit:
        return f"익절(+{pct*100:.0f}%)"

    # ① 하드 손절 (-8%, 즉시, 최소보유 무관)
    if pct <= stop_loss:
        return f"손절({stop_loss * 100:.0f}%)"

    # ★ 추적 손절: trail_stop은 국면별 완화 (극강세 -20%, 강세 -15%, 보통 -12%, 약세 -10%)
    if hold_days >= 5:
        trail_pct = (curr - peak) / peak if peak > 0 else 0
        if trail_pct <= eff_trail_stop and pct > 0.03:
            return f"추적손절(고점{eff_trail_stop*100:.0f}%)"

    # ② MA60 붕괴 (최소 보유 5일 이후만 적용)
    if hold_days >= 5:
        ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
        if ma60 is not None and curr < ma60:
            return "MA60 붕괴"

    return None


# ══════════════════════════════════════════════════════════════
#  포트폴리오 시뮬레이션 — 날짜별 전체 스캔
# ══════════════════════════════════════════════════════════════


def _run_portfolio(
    sim_dates: list,               # 시뮬레이션 기간 거래일 (ASC)
    stock_data: Dict[str, dict],   # code → {dates, prices, volumes, frn, inst, fins, sim_start_i}
    per_stock: float,
    max_positions: int,
    stop_loss: float = -0.08,
    market_bullish: Optional[Dict[str, bool]] = None,  # ★ 시장 추세 필터 (날짜→bool)
    take_profit_map: Optional[Dict[str, float]] = None,  # ★ 국면별 익절 (날짜→float)
    trail_stop_map: Optional[Dict[str, float]] = None,   # ★ 국면별 추적손절 (날짜→float)
    asof_mktcap: bool = False,      # 2026-07-27: as-of 시총 게이트 (룩어헤드 제거)
    shares_asof_fn=None,            # code, day -> shares_issued (as-of)
    mktcap_min: float = 1000.0,     # 억원
    big_gate: float = None,         # 2026-08-10: 대박 확장구간 게이트 (None=기존 동작 완전 동일)
    trail_big: float = -0.35,
) -> Tuple[list, list]:
    """
    매일(sim_dates 하루씩) 전 종목을 스캔:
      1) 기존 보유 종목 → 매도 조건 체크 (손절/MA60/MA20)
      2) 빈 슬롯이 있으면 → 매수 조건 충족 종목 편입
      3) 포지션 가득 찬 경우 → 품질 기반 로테이션 (15% 우세 시 교체)

    Returns: (trades, equity_curve)
    """
    positions: Dict[str, dict] = {}    # code → position dict
    trades:    List[dict]       = []
    equity_curve: List[dict]    = []
    # P0: next-bar execution (Codex timing fix)
    _pb: Dict[str, dict] = {}   # pending buys  {sc → meta}
    _ps: Dict[str, dict] = {}   # pending sells {sc → meta}

    total_capital = per_stock * max_positions
    cash = total_capital

    # HS 수출 데이터 로드 (점수 보너스용)
    try:
        _hs_data = _load_trade_signals()
    except Exception:
        _hs_data = {}

    # 날짜→인덱스 빠른 조회
    date_idx: Dict[str, Dict[str, int]] = {
        sc: {dt: idx for idx, dt in enumerate(d['dates'])}
        for sc, d in stock_data.items()
    }

    def _mark_to_market(day: str) -> float:
        val = cash
        for sc, pos in positions.items():
            idx_map = date_idx.get(sc, {})
            if day in idx_map:
                i = idx_map[day]
                val += stock_data[sc]['prices'][i] * pos['qty']
            else:
                val += pos.get('cost', pos['entry_price'] * pos['qty'])
        return val

    def _slot_limit(day: str) -> int:
        # 복리형 계좌: 1억원/10슬롯에서 출발하되 평가자산이 늘면 11, 12... 슬롯 허용.
        return max(1, int(_mark_to_market(day) // per_stock))

    for day in sim_dates:

        # ── Phase A: 전일 매도 신호 → 오늘 시가/종가 집행 (D+1 execution) ────
        to_remove_ps = []
        for sc in list(_ps.keys()):
            if sc not in positions:
                to_remove_ps.append(sc)
                continue
            pos     = positions[sc]
            sd      = stock_data[sc]
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue   # 오늘 데이터 없음 → 다음 거래일로 이연
            i = idx_map[day]
            opens_arr = sd.get('opens', [])
            op = opens_arr[i] if i < len(opens_arr) else 0.0
            exec_price = op if op > 0 else sd['prices'][i]
            net_amt, net_pct = _net_profit(
                pos['entry_price'], exec_price, pos['qty'],
                pos.get('mkt_cap_억', sd.get('mkt_cap_억', 500))
            )
            trades.append({
                'stock_code':  sc,
                'entry_date':  pos['entry_date'],
                'exit_date':   day,
                'entry_price': pos['entry_price'],
                'exit_price':  exec_price,
                'qty':         pos['qty'],
                'profit_pct':  net_pct,
                'profit_amt':  net_amt,
                'exit_reason': _ps[sc].get('reason', '매도'),
            })
            cash += pos.get('cost', pos['entry_price'] * pos['qty']) + net_amt
            del positions[sc]
            to_remove_ps.append(sc)
        for sc in to_remove_ps:
            _ps.pop(sc, None)

        # ── Phase B: 전일 매수 신호 → 오늘 시가/종가 집행 (D+1 execution) ────
        sorted_buys = sorted(_pb.items(), key=lambda x: x[1].get('score', 0), reverse=True)
        for sc, meta in sorted_buys:
            if sc in positions or len(positions) >= _slot_limit(day):
                continue
            sd      = stock_data[sc]
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i  = idx_map[day]
            opens_arr = sd.get('opens', [])
            op = opens_arr[i] if i < len(opens_arr) else 0.0
            exec_price = op if op > 0 else sd['prices'][i]
            budget = min(per_stock, cash)
            if budget < exec_price:
                continue
            qty = int(budget / exec_price)
            cost = qty * exec_price
            if qty <= 0 or cost > cash:
                continue
            cash -= cost
            positions[sc] = {
                'entry_date':  day,
                'entry_price': exec_price,
                'qty':         qty,
                'cost':        cost,
                'peak_price':  exec_price,
                'hold_days':   0,
                'mkt_cap_억':  sd.get('mkt_cap_억', 500),
            }
        _pb.clear()

        # ── hold_days 증가 ─────────────────────────────────────────────────
        for pos in positions.values():
            pos['hold_days'] = pos.get('hold_days', 0) + 1

        # ── Phase D: 매도 신호 탐지 → _ps 큐 (D+1 집행) ─────────────────
        # 하락장에도 항상 실행 — 손절/추적손절은 시장방향과 무관하게 적용
        tp_today    = take_profit_map.get(day, 0.25)  if take_profit_map  else 0.25
        trail_today = trail_stop_map.get(day, -0.10)  if trail_stop_map   else -0.10
        for sc, pos in list(positions.items()):
            if sc in _ps:
                continue  # 이미 매도 큐에 있음
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i  = idx_map[day]
            sd = stock_data[sc]
            reason = _check_sell(i, sd['prices'], pos, stop_loss,
                                  take_profit=tp_today, trail_stop=trail_today,
                                  big_gate=big_gate, trail_big=trail_big)
            if reason is None:
                continue
            _ps[sc] = {'reason': reason}

        # ── Phase C: 시장 추세 필터 ────────────────────────────────────────
        # 하락장: 신규 매수만 차단. 손절은 Phase D에서 이미 처리됨.
        if market_bullish is not None and not market_bullish.get(day, True):
            equity_curve.append({'date': day, 'equity': round(_mark_to_market(day))})
            continue

        # ── Phase E: 매수 신호 탐지 → _pb 큐 (D+1 집행) ─────────────────
        # 현재 빈 슬롯 + 내일 매도 예정 슬롯 = 채울 수 있는 슬롯
        current_limit = _slot_limit(day)
        free_slots = current_limit - len(positions) + len(_ps)
        candidates = []
        if free_slots > 0:
            for sc, sd in stock_data.items():
                if sc in positions or sc in _ps:
                    continue
                idx_map = date_idx.get(sc, {})
                if day not in idx_map:
                    continue
                i = idx_map[day]
                if asof_mktcap and shares_asof_fn is not None:
                    sh = shares_asof_fn(sc, day)
                    if sh <= 0 or sh * sd['prices'][i] / 1e8 < mktcap_min:
                        continue
                if not _is_buy_signal(
                    i, sd['sim_start_i'],
                    sd['dates'], sd['prices'], sd['volumes'],
                    sd['frn'],   sd['inst'],   sd['fins'],
                ):
                    continue
                score = _score_entry(i, sd['prices'], sd['volumes'],
                                     sc=sc, day=day, hs_data=_hs_data)
                candidates.append((score, sc, sd, i))
            candidates.sort(key=lambda x: x[0], reverse=True)
            for score, sc, sd, i in candidates[:free_slots]:
                _pb[sc] = {'score': score}

        # ── Phase F: 로테이션 탐지 → _ps/_pb 큐에 추가 ──────────────────
        # 포지션 가득 & 더 좋은 후보 있을 때, 최하위 보유 종목과 교체 신호
        if len(positions) >= current_limit and candidates:
            held_scores = {}
            for held_sc, pos in list(positions.items()):
                if pos.get('hold_days', 0) < 5 or held_sc in _ps:
                    continue
                hidx = date_idx.get(held_sc, {}).get(day, -1)
                if hidx < 0:
                    continue
                held_scores[held_sc] = _score_entry(
                    hidx, stock_data[held_sc]['prices'],
                    stock_data[held_sc]['volumes'],
                    sc=held_sc, day=day, hs_data=_hs_data,
                )
            if held_scores:
                worst_sc    = min(held_scores, key=held_scores.get)
                worst_score = held_scores[worst_sc]
                for score, sc, sd, i in candidates:
                    if sc in positions or sc in _pb:
                        continue
                    if score >= worst_score * 1.35:  # 1.15→1.35: 수익중 winner 보호
                        _ps[worst_sc] = {
                            'reason': f'로테이션교체(점수{worst_score:.2f}→{score:.2f})',
                        }
                        _pb[sc] = {'score': score}
                        break

        # ── Phase G: 에쿼티 커브 ──────────────────────────────────────────
        equity_curve.append({'date': day, 'equity': round(_mark_to_market(day))})

    # ── 기간 종료: 미청산 포지션 강제 청산 ─────────────────
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        idx_map = date_idx.get(sc, {})
        sd = stock_data[sc]
        if last_day and last_day in idx_map:
            curr = sd['prices'][idx_map[last_day]]
        else:
            curr = sd['prices'][-1] if sd['prices'] else pos['entry_price']
        net_amt, net_pct = _net_profit(
            pos['entry_price'], curr, pos['qty'],
            pos.get('mkt_cap_억', sd.get('mkt_cap_억', 500))
        )
        trades.append({
            'stock_code':  sc,
            'entry_date':  pos['entry_date'],
            'exit_date':   last_day or pos['entry_date'],
            'entry_price': pos['entry_price'],
            'exit_price':  curr,
            'qty':         pos['qty'],
            'profit_pct':  net_pct,
            'profit_amt':  net_amt,
            'exit_reason': '기간종료',
        })

    return trades, equity_curve


# ══════════════════════════════════════════════════════════════
#  성과 지표 계산
# ══════════════════════════════════════════════════════════════


def _calc_metrics(trades: list, equity_curve: list,
                  start_date: str, end_date: str,
                  total_capital: float) -> dict:
    if not trades:
        return {
            'total_return_pct': 0, 'ann_return_pct': 0, 'cagr': 0,
            'win_rate': 0, 'total_trades': 0, 'profit_trades': 0,
            'max_drawdown_pct': 0, 'sharpe': 0, 'pl_ratio': 0,
            'total_profit_amt': 0,
            'summary': '해당 기간 동안 신호가 발생한 종목이 없습니다.'
        }

    profit_t = [t for t in trades if t['profit_amt'] > 0]
    loss_t   = [t for t in trades if t['profit_amt'] <= 0]

    total_profit = sum(t['profit_amt'] for t in trades)
    win_rate     = round(len(profit_t) / len(trades) * 100, 1) if trades else 0

    # 손익비 (평균 수익 / 평균 손실)
    avg_win  = sum(t['profit_amt'] for t in profit_t) / len(profit_t) if profit_t else 0
    avg_loss = abs(sum(t['profit_amt'] for t in loss_t)) / len(loss_t) if loss_t else 1
    pl_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

    days  = max((datetime.strptime(end_date, '%Y-%m-%d') -
                 datetime.strptime(start_date, '%Y-%m-%d')).days, 1)
    years = days / 365.25

    total_ret_pct = round(total_profit / total_capital * 100, 2) if total_capital else 0
    end_val       = total_capital + total_profit
    if total_capital > 0 and years > 0 and end_val > 0:
        cagr = round(((end_val / total_capital) ** (1 / years) - 1) * 100, 2)
    elif total_capital > 0 and end_val <= 0:
        cagr = -100.0   # 전액 손실
    else:
        cagr = 0
    ann_ret_pct   = round(total_ret_pct / years, 2)

    # MDD (에쿼티 커브 기준)
    peak   = total_capital
    max_dd = 0.0
    for e in equity_curve:
        eq = e['equity']
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (eq - peak) / peak * 100
            max_dd = min(max_dd, dd)

    # 샤프지수 (일별 수익률, 무위험 3%)
    sharpe = 0.0
    if len(equity_curve) > 5:
        eq_vals = [e['equity'] for e in equity_curve]
        daily_r = [(eq_vals[i] - eq_vals[i - 1]) / eq_vals[i - 1]
                   for i in range(1, len(eq_vals)) if eq_vals[i - 1] > 0]
        if len(daily_r) > 5:
            rf      = 0.03 / 252
            mean_r  = sum(daily_r) / len(daily_r)
            std_r   = (sum((r - mean_r) ** 2 for r in daily_r) / len(daily_r)) ** 0.5
            sharpe  = round((mean_r - rf) / std_r * (252 ** 0.5), 2) if std_r > 0 else 0

    return {
        'total_return_pct':  total_ret_pct,
        'ann_return_pct':    ann_ret_pct,
        'cagr':              cagr,
        'win_rate':          win_rate,
        'total_trades':      len(trades),
        'profit_trades':     len(profit_t),
        'max_drawdown_pct':  round(max_dd, 2),
        'sharpe':            sharpe,
        'pl_ratio':          pl_ratio,
        'total_profit_amt':  int(total_profit),
    }


# ══════════════════════════════════════════════════════════════
#  메인 백테스트
# ══════════════════════════════════════════════════════════════


def _save_result(run_id: str, result: dict):
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        UPDATE backtest_runs SET
            status='done',
            total_return_pct=?, ann_return_pct=?, win_rate=?,
            total_trades=?, profit_trades=?, max_drawdown_pct=?,
            trades_json=?, summary_text=?
        WHERE run_id=?
    """, (
        result.get('total_return_pct', 0), result.get('ann_return_pct', 0),
        result.get('win_rate', 0),         result.get('total_trades', 0),
        result.get('profit_trades', 0),    result.get('max_drawdown_pct', 0),
        json.dumps(result, ensure_ascii=False),
        result.get('summary', ''),
        run_id,
    ))
    conn.commit(); conn.close()


# ══════════════════════════════════════════════════════════════
#  V1 매수 시그널 — 미너비니 트렌드 (추세 추종)
# ══════════════════════════════════════════════════════════════


def _is_buy_v1(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V1 트렌드 (미너비니 — 실증 보강):
      [A] 현재가 > MA20 > MA60 (정배열 핵심 2줄)
      [B] 현재가 > MA120 (중기 추세 확인)
      [C] RSI 42~88 → 실증: RSI>70=1.25x (오히려 좋음) → 상한 72→88로 완화
      [D] 거래량 5일 평균 > 20일 평균 × 1.2 (거래량 방향성 확인)
      [E] 52주 범위 65%+ 위치 → 실증 1.54x 최강 알파
    """
    if i < sim_start_i or i < 60:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    # [E] 52주 범위 65%+ (실증 최강 필터, 먼저 체크)
    if i >= 120 and _52w_pct(prices, i) < 65:
        return False

    ma20  = _ma(prices[max(0, i-19):i+1], 20)
    ma60  = _ma(prices[max(0, i-59):i+1], 60)
    ma120 = _ma(prices[max(0, i-119):i+1], 120) if i >= 120 else None

    # [A] 정배열 MA20 > MA60
    if not (ma20 and ma60):
        return False
    if not (curr > ma20 > ma60):
        return False
    # [B] 중기 추세
    if ma120 and curr < ma120:
        return False
    # [C] RSI — 상한 72→88 (RSI>70 경험적 양호)
    rsi = _rsi(prices[max(0, i-30):i+1], 14)
    if rsi is None or not (42 <= rsi <= 88):
        return False
    # [D] 거래량 방향성: 5일 > 20일 × 1.2
    v20 = _ma(volumes[max(0, i-19):i+1], 20)
    v5  = _ma(volumes[max(0, i-4):i+1], 5)
    if v20 and v5 and v20 > 0 and v5 < v20 * 1.2:
        return False
    return True




def _is_buy_v10(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V6 이익폭발 — 실증 보강:
    실증 문제: 매출YoY=0.94x (신호없음), RSI<80 상한이 RSI>70 종목(1.25x) 필터링
    수정: RSI 상한 완화 + 매출 YoY 기준 완화 (52W 필터 제거 — 고성장 알파 차단)
      [A] 영업이익 YoY > 50%
      [B] 매출 YoY > 10% (20%→10%, 실증에서 매출증가 단독 신호 약함)
      [C] 영업이익률 > 5%
      [D] 2분기 연속 영업이익 성장
      [F] RSI 40~88 (상한 80→88, RSI>70=1.25x 경험적 양호)
    52W 필터 제거 이유: 이익폭발 종목은 펀더멘털 드라이브로 52W 어떤 구간에서도
    발생 가능. 필터가 오히려 고성장 알파를 차단 (V6 avg5: +37.5%→+16.0% 확인).
    """
    if i < sim_start_i:
        return False
    d    = dates[i]
    curr = prices[i]

    p_slice = prices[max(0, i - 60): i + 1]
    if len(p_slice) < 60:
        return False
    ma60 = sum(p_slice[-60:]) / 60
    if curr < ma60 * 0.95:
        return False

    # [F] RSI — 상한 80→88
    rsi_val = _rsi(prices[max(0, i - 28): i + 1])
    if rsi_val is None or rsi_val < 40 or rsi_val > 88:
        return False

    # 재무 조건: 공시 지연 적용, 최소 3개 분기 필요
    fin = _get_financial_as_of(fin_rows, d)
    if fin is None:
        return False
    y0, q0, rev0, op0, eps0, bps0, eq0, ni0, roe0, ann0, *_ = fin

    if ann0:   # 연간 데이터는 스킵 (분기 데이터만 사용)
        return False
    if op0 is None or rev0 is None or op0 <= 0 or rev0 <= 0:
        return False

    # 영업이익률 [C]
    op_margin = op0 / rev0 * 100 if rev0 > 0 else 0
    if op_margin < 5.0:
        return False

    # 1년 전 동일 분기 (YoY 비교)
    ya_candidates = [r for r in fin_rows
                     if r[1] == q0 and r[0] == y0 - 1 and not r[9]
                     and (r[10] if len(r) > 10 and r[10] else _release_date(r[0], r[1], bool(r[9]))) <= d]
    if not ya_candidates:
        return False
    ya = ya_candidates[0]
    op_ya, rev_ya = ya[3], ya[2]   # [3]=operating_profit, [2]=revenue
    if op_ya is None or rev_ya is None or op_ya <= 0 or rev_ya <= 0:
        return False

    # [A] 영업이익 YoY
    op_yoy = (op0 - op_ya) / abs(op_ya) * 100
    if op_yoy < 50:
        return False

    # [B] 매출 YoY (완화: 20→10, 매출증가 단독신호 약함)
    rev_yoy = (rev0 - rev_ya) / rev_ya * 100
    if rev_yoy < 10:
        return False

    # [D] 2분기 연속 성장: 직전 분기 데이터 확인
    prev_q = q0 - 1 if q0 > 1 else 4
    prev_y = y0 if q0 > 1 else y0 - 1
    prev_candidates = [r for r in fin_rows
                       if r[1] == prev_q and r[0] == prev_y and not r[9]
                       and (r[10] if len(r) > 10 and r[10] else _release_date(r[0], r[1], bool(r[9]))) <= d]
    if prev_candidates:
        op1 = prev_candidates[0][3]   # [3]=operating_profit
        if op1 is not None and op1 > 0 and op0 < op1 * 0.8:
            # 직전 분기보다 영업이익이 20% 이상 감소하면 탈락
            return False

    return True


# ══════════════════════════════════════════════════════════════
#  경험적 신호 — 52주 강세 돌파 (52W Breakout Momentum)
#
#  실증 분석 결론 (2020~2025, 982,889 샘플):
#    52주 고점 근처(70%+): win_rate 17.5% (base 11.3%, 리프트 1.54x) ← 최강
#    MA위+5일상승+52W고점 조합: 1.64x 리프트
#    RSI<40 (과매도):            0.87x — 오히려 불리
#    매출 증가:                    0.94x — 신호 없음
#
#  결론: "강한 것은 더 강해진다" — 과매도/역발상 아닌 모멘텀이 실증적 알파
# ══════════════════════════════════════════════════════════════


def _is_buy_v11(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V7 이익가속 YoY (2026-06-26 재설계 — 계절성 제거):
    ★ 적합장세: 하락장(α+11.5%), AI랠리(α+15.7%). 광역 상승장/회복장은 부진.
    분기 공시 지연 한계(45일) → 이익 개선이 주가에 선반영된 후 매수되는 구조적 제약.
    YoY(전년 동기 대비)로 계절성을 제거하고 스마트머니 동반 확인.

      [A] 최근 분기 영업이익 YoY > 30% (계절성 무관, 전년 동기 대비 이익 가속)
      [B] 직전 분기도 흑자 + YoY 양수 성장 (연속 이익 개선 확인)
      [C] 52주 범위 상위 50~88% (가격 방향성 확인, 극단 과매수 제외)
      [D] MA20 > MA60 (단기-중기 추세 정배열)
      [E] 기관 OR 외국인 5일 합산 순매수 (AND→OR, 스마트머니 유입)
    """
    if i < sim_start_i or i < 120:
        return False
    d    = dates[i]
    curr = prices[i]

    # [C] + [D]: 가격 모멘텀 필터
    p_slice = prices[max(0, i - 252): i + 1]
    if len(p_slice) < 60:
        return False
    ma20 = sum(p_slice[-20:]) / 20
    ma60 = sum(p_slice[-60:]) / 60

    # [D] 단기-중기 추세 정배열
    if not (curr > ma20 and ma20 > ma60):
        return False

    # [C] 52주 범위 상위 50~88% (너무 낮으면 아직 안 움직임, 너무 높으면 이미 소진)
    p252 = p_slice[-252:] if len(p_slice) >= 252 else p_slice
    p52w_high = max(p252)
    p52w_low  = min(p252)
    if p52w_high > p52w_low:
        range_pct = (curr - p52w_low) / (p52w_high - p52w_low)
        if range_pct < 0.50 or range_pct > 0.88:
            return False

    # [E] 기관 OR 외국인 5일 합산 양수
    if i >= 5:
        inst5 = sum(inst_net[max(0, i - 4): i + 1])
        frn5  = sum(frn_net[max(0, i - 4): i + 1])
        if inst5 <= 0 and frn5 <= 0:
            return False

    # 재무: 분기 데이터 (공시 지연 적용)
    available = [r for r in fin_rows
                 if not r[9] and r[1] in (1, 2, 3, 4)
                 and (r[10] if len(r) > 10 and r[10] else _release_date(r[0], r[1], bool(r[9]))) <= d]
    available.sort(key=lambda r: (r[0], r[1]), reverse=True)

    if len(available) < 1:
        return False

    # YoY 조회용 dict (연도+분기 키)
    by_quarter: dict = {}
    for r in available:
        key = (r[0], r[1])
        if key not in by_quarter:
            by_quarter[key] = r

    f0 = available[0]
    year0, q0 = f0[0], f0[1]
    op0 = f0[3]
    if op0 is None or op0 <= 0:
        return False

    # [A] 최근 분기 YoY > 30% (전년 동기 대비, 계절성 제거)
    f0_yoy = by_quarter.get((year0 - 1, q0))
    if f0_yoy is None or f0_yoy[3] is None or f0_yoy[3] <= 0:
        return False
    if (op0 - f0_yoy[3]) / f0_yoy[3] < 0.30:
        return False

    # [B] 직전 분기도 흑자 + YoY 양수 (연속 이익 개선)
    if len(available) >= 2:
        f1 = available[1]
        year1, q1 = f1[0], f1[1]
        op1 = f1[3]
        if op1 is None or op1 <= 0:
            return False  # 직전 분기 적자 제외
        f1_yoy = by_quarter.get((year1 - 1, q1))
        if f1_yoy is not None and f1_yoy[3] is not None and f1_yoy[3] > 0:
            if op1 <= f1_yoy[3]:  # 직전 분기 YoY 감소면 제외
                return False

    return True


# ══════════════════════════════════════════════════════════════
#  V12 매수 시그널 — 섹터 대세 상승 (효성중공업·LS Electric 유형)
# ══════════════════════════════════════════════════════════════


def _sell_signal_vbr_newhigh_fail(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V8 52W돌파 — 신고가 갱신 실패: 진입 근거(52주 신고가 돌파)가 최근 40거래일간
    재확인되지 않으면(신고가 갱신 실패) 추세 소진으로 보고 청산.
    2026-07-17 실증: 6기간 검증 결과 기각(ledger 참조), 기본 미적용."""
    held = pos.get('hold_days', 0)
    if held < 40:
        return None
    prices = sd['prices']
    if i < 40:
        return None
    recent40_high = max(prices[i-39:i+1])
    if prices[i] < recent40_high * 0.999:  # 최근 40일 내 신고가 갱신 없음
        # 40일 윈도 전체에서 신고가가 '오늘' 갱신됐는지 체크(위 조건이 항상 참이 아니게)
        window_max_idx = prices.index(recent40_high, i-39, i+1)
        if window_max_idx < i - 5:  # 신고가가 5일 이상 전 → 최근 갱신 없음
            return "신고가갱신실패(V8)"
    return None




def _run_generic_backtest_with_sc(version: str, signal_fn,
                                   start_date: str, end_date: str,
                                   per_stock: float, max_positions: int,
                                   run_name: str, run_id: str,
                                   stop_loss: float, take_profit: float,
                                   mktcap_min: int = 1000) -> str:    # 기본값 1000억 (억원 단위)
    """
    _run_generic_backtest와 동일하나 signal_fn에 _sc(stock_code) 키워드 인자를 전달.
    V10+HS, V11+HS처럼 시그널 함수가 종목 코드 접근이 필요한 경우 사용.
    """
    init_backtest_db()
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        fin_all: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT f.stock_code, f.year, f.quarter,
                   f.revenue, f.operating_profit, f.eps, f.bps,
                   f.total_equity, f.net_income, f.roe,
                   CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END,
                   COALESCE(d.avail_date,
                     CASE WHEN f.is_annual=1 THEN printf('%d-03-31', f.year+1)
                          WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                          WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                          WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                          ELSE printf('%d-02-15', f.year+1) END) as avail_date
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d ON
                d.stock_code = f.stock_code AND d.year = f.year
                AND d.quarter = CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                AND d.is_annual = CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
            WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 4) OR (f.is_annual=1)
            ORDER BY f.stock_code, f.year, f.quarter
        """).fetchall():
            fin_all.setdefault(r[0], []).append(r[1:])

        stock_codes = [r[0] for r in conn.execute("""
            SELECT ph.stock_code, COUNT(*) AS cnt
            FROM price_history ph
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE market_cap >= ?
                  AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            ) su ON ph.stock_code = su.stock_code
            WHERE ph.date>=? AND ph.date<=? AND ph.close>0
            GROUP BY ph.stock_code HAVING COUNT(*) >= 200
        """, (mktcap_min, warmup_start, end_date)).fetchall()]
        mktcap_map = {}
        if stock_codes:
            ph = ",".join("?" * len(stock_codes))
            mktcap_map = {
                sc: (float(mc) if mc is not None else mktcap_min)
                for sc, mc in conn.execute(
                    f"SELECT stock_code, market_cap FROM stock_universe WHERE stock_code IN ({ph})",
                    stock_codes,
                ).fetchall()
            }

        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0),
                       COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (sc, warmup_start, end_date)).fetchall()
            if len(rows) < 200:
                continue
            dates   = [r[0] for r in rows]
            prices  = [float(r[1]) for r in rows]
            volumes = [float(r[2]) for r in rows]
            frn     = [float(r[3]) for r in rows]
            inst    = [float(r[4]) for r in rows]
            sim_start_i = next((i for i, d in enumerate(dates) if d >= start_date), len(dates))
            stock_data[sc] = {
                'dates': dates, 'prices': prices, 'volumes': volumes,
                'frn': frn, 'inst': inst, 'fins': fin_all.get(sc, []),
                'sim_start_i': sim_start_i,
                'mkt_cap_억': mktcap_map.get(sc, mktcap_min),
            }

        kospi_prices = {r[0]: r[1] for r in conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 AND date>=? AND date<=?
        """, (warmup_start, end_date)).fetchall()}

        # O(1) 날짜 → 인덱스 사전 (핵심 성능 최적화)
        date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                    for sc, d in stock_data.items()}

        # KOSPI MA120 사전계산
        kp_vals = [kospi_prices.get(d, 0) for d in sim_dates]
        kp_valids: list = []
        kma120_map: dict = {}
        for ki, sim_date in enumerate(sim_dates):
            kp = kp_vals[ki]
            if kp > 0:
                kp_valids.append(kp)
            kma120_map[sim_date] = (sum(kp_valids[-120:]) / len(kp_valids[-120:])
                                    if len(kp_valids) >= 120 else 0)

        trades = []
        equity_curve = []
        portfolio: Dict[str, dict] = {}
        cash = per_stock * max_positions

        for sim_date in sim_dates:
            kp = kospi_prices.get(sim_date, 0)
            kp_ma120 = kma120_map.get(sim_date, 0)
            market_bullish = kp > kp_ma120 if kp_ma120 > 0 else True

            # 매도 체크
            for sc in list(portfolio.keys()):
                sd = stock_data.get(sc)
                if sd is None:
                    continue
                i = date_idx.get(sc, {}).get(sim_date, -1)
                if i < 0:
                    continue
                curr = sd['prices'][i]
                pos = portfolio[sc]
                entry = pos['entry_price']
                pct = (curr - entry) / entry
                peak = max(pos.get('peak', entry), curr)
                pos['peak'] = peak
                days_held = pos.get('days_held', 0) + 1
                pos['days_held'] = days_held

                reason = None
                if pct >= take_profit:
                    reason = f'익절 {pct*100:.1f}%'
                elif pct <= stop_loss:
                    reason = f'손절 {pct*100:.1f}%'
                elif (peak - entry) / entry > 0.05 and (curr - peak) / peak < -0.10:
                    reason = f'추적손절 {(curr-peak)/peak*100:.1f}%'
                elif days_held > 5:
                    prices_slice = sd['prices'][max(0, i - 60):i + 1]
                    ma60 = sum(prices_slice[-60:]) / len(prices_slice[-60:]) if len(prices_slice) >= 60 else 0
                    if ma60 > 0 and curr < ma60:
                        reason = 'MA60붕괴'

                if reason:
                    profit_amt, return_pct = _net_profit(
                        entry, curr, pos['qty'],
                        pos.get('mkt_cap_억', sd.get('mkt_cap_억', 500))
                    )
                    cash += entry * pos['qty'] + profit_amt
                    trades.append({
                        'stock_code': sc, 'stock_name': pos.get('stock_name', sc),
                        'entry_date': pos['entry_date'],
                        'exit_date': sim_date, 'entry_price': entry, 'exit_price': curr,
                        'qty': pos['qty'], 'profit_amt': profit_amt,
                        'return_pct': return_pct, 'reason': reason,
                    })
                    del portfolio[sc]

            # 매수 체크
            if market_bullish and len(portfolio) < max_positions:
                for sc, sd in stock_data.items():
                    if sc in portfolio or len(portfolio) >= max_positions:
                        continue
                    i = date_idx.get(sc, {}).get(sim_date, -1)
                    if i < 0 or i < sd['sim_start_i']:
                        continue
                    buy = signal_fn(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['frn'], sd['inst'], sd['fins'],
                        _sc=sc,
                    )
                    if buy:
                        curr = sd['prices'][i]
                        qty = max(1, int(per_stock / curr))
                        if cash >= curr * qty:
                            cash -= curr * qty
                            portfolio[sc] = {
                                'entry_date': sim_date, 'entry_price': curr,
                                'qty': qty, 'peak': curr, 'days_held': 0,
                                'mkt_cap_억': sd.get('mkt_cap_억', mktcap_min),
                            }

            total_val = cash + sum(
                pos['qty'] * (stock_data[sc]['prices'][date_idx[sc].get(sim_date, -1)]
                              if date_idx[sc].get(sim_date, -1) >= 0
                              else pos['entry_price'])
                for sc, pos in portfolio.items() if sc in stock_data
            )
            equity_curve.append({'date': sim_date, 'equity': total_val})

        # 강제 청산
        for sc, pos in portfolio.items():
            sd = stock_data.get(sc)
            if sd:
                last_p = sd['prices'][-1]
                profit_amt, return_pct = _net_profit(
                    pos['entry_price'], last_p, pos['qty'], pos.get('mkt_cap_억', 500)
                )
                trades.append({
                    'stock_code': sc, 'stock_name': pos.get('stock_name', sc),
                    'entry_date': pos['entry_date'],
                    'exit_date': sim_dates[-1] if sim_dates else end_date,
                    'entry_price': pos['entry_price'], 'exit_price': last_p,
                    'qty': pos['qty'],
                    'profit_amt': profit_amt,
                    'return_pct': return_pct,
                    'reason': '기간종료',
                })

        metrics = _calc_metrics(trades, equity_curve, start_date, end_date,
                                per_stock * max_positions)

        # 종목별 손익 요약
        from collections import defaultdict
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        top_winners = sorted(per_name.items(), key=lambda x: -x[1])[:5]
        top_losers  = sorted(per_name.items(), key=lambda x:  x[1])[:5]

        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"전략: {version} | CAGR: {metrics.get('cagr', 0):.1f}% | "
            f"MDD: {metrics.get('max_drawdown_pct', 0):.1f}% | "
            f"승률: {metrics.get('win_rate', 0):.1f}% | "
            f"거래: {metrics.get('total_trades', 0)}건"
        )

        result = {
            **metrics,
            'equity_curve': equity_curve[-252:],
            'trades':       sorted(trades, key=lambda x: x.get('exit_date', ''), reverse=True),
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in top_winners],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in top_losers],
            'summary':      summary_text,
        }
        conn.close()
        _save_result(run_id, result)
    except Exception as e:
        try:
            conn.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                         (str(e), run_id))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
        logger.error(f"[백테스트/{version}] 오류: {e}", exc_info=True)
    return run_id




def _sell_signal_v1(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V1 MA추세추종 — 데스크로스: 매수 조건(MA정배열) 자체가 역전될 때 청산
    기존 방식(손실권 -7% 한정)은 손절 역할과 중복/경쟁 → 비효율.
    새 접근: MA20이 MA60 아래로 실제 교차(데스크로스) = 정배열의 직접적 붕괴.
    이익/손실 무관하게 발동. 15일 이상 보유 후에만 (초기 휩소 방지).
    """
    prices = sd['prices']
    held   = pos.get('hold_days', 0)
    if held < 15 or i < 3:
        return None
    # 현재 MA
    ma20_c = _ma(prices[max(0, i-19):i+1], 20)
    ma60_c = _ma(prices[max(0, i-59):i+1], 60)
    # 2일 전 MA (데스크로스 교차 감지)
    ma20_p = _ma(prices[max(0, i-21):i-1], 20)
    ma60_p = _ma(prices[max(0, i-61):i-1], 60)
    if not all([ma20_c, ma60_c, ma20_p, ma60_p]):
        return None
    # 이전: MA20 >= MA60 → 현재: MA20 < MA60 × 0.999 (데스크로스 확정)
    if ma20_p >= ma60_p * 0.999 and ma20_c < ma60_c * 0.999:
        return "데스크로스(V1)"
    return None




def _sell_signal_v2_momentum(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V2 가치매수 — 순수 시그널 기반 청산 (2026-07-17 실증):
    768개 트레이드 고점분석 — 중앙값 고점도달 175거래일(임의 max_hold와 무관),
    고점 이후 10일 평균 반납폭 +27.5%p. 반납이 큰 그룹에서 공통 관측된 두 신호:
    모멘텀반전(20일 수익률 음전환, 반납 33.3%p vs 미발생 23.2%p)
    + 거래량급감(고점 대비 반토막, 반납 31.1%p vs 미발생 23.7%p).
    두 신호 동시 발생 시 즉시 청산 — '몇일 보유'가 아니라 '시장이 꺾인 증거'로 매도.
    """
    held = pos.get('hold_days', 0)
    if held < 20:
        return None
    prices = sd['prices']
    if i < 25 or i - 20 < 0:
        return None
    mom_now = (prices[i] - prices[i - 20]) / prices[i - 20] if prices[i - 20] > 0 else 0
    if mom_now >= 0:
        return None  # 모멘텀 아직 양전 — 매도 안함
    vols = sd['volumes']
    if i < 5:
        return None
    v_peak5 = max(vols[max(0, i - 20):i + 1]) if i >= 1 else 0
    v_now = vols[i]
    if v_peak5 <= 0 or v_now >= v_peak5 * 0.5:
        return None  # 거래량 급감 아직 미확인
    return "모멘텀반전+거래량급감(V2)"




def _run_generic_backtest(version: str, signal_fn,
                           start_date: str, end_date: str,
                           per_stock: float, max_positions: int,
                           run_name: str, run_id: str,
                           stop_loss: float, take_profit: float,
                           trail_stop: float = -0.10,
                           mktcap_min: int = 1000,     # 기본값 1000억 (억원 단위)
                           max_new_per_month: int = 10,
                           use_market_filter: bool = True,
                           strategy_key: str = None,
                           sell_signal_fn = None,
                           entry_bonus_fn = None,          # (code, day)→float: 보너스 큰 종목 우선 진입 (0이면 기존 순서 유지)
                           asof_mktcap: bool = True,       # 2026-07-13 기본화: as-of 시총(당일 주가×상장주식수) — 현재시총 룩어헤드 제거
                           avoid_overheat: float = None,   # 진입일 40일 수익률 +N(1.0=+100%) 초과 급등주 제외 (V-GC 채택 필터)
                           chart_confluence: bool = False,  # 2026-07-18: 일봉+주봉+캔들 컨플루언스(2/3 합의) 진입게이트+고점청산 (공통 모듈)
                           data_asof_ts: str = None) -> str:
    """V10/V11 공통 백테스트 실행기 (V4 run_backtest 구조 재활용).
    use_market_filter=False: V11 흑자전환처럼 하락장에서도 매수해야 하는 전략에 사용.
    strategy_key: DB에 저장할 전략 키 (v10, v11, v_trend 등). None이면 'combo' 기본값.

    data_asof_ts: 2026-09-04 신규. financial_data는 DART재검증 백그라운드 잡
    (scripts/data_integrity_followup.py, 매일 00:05)이 계속 값을 UPDATE하므로,
    이 값 없이 같은 과거 구간을 재실행하면 실행 시점마다 다른 재무값을 읽어
    value/v2처럼 문턱값 근처 신호가 흔들릴 수 있다(회귀검증 재현성 붕괴 원인).
    'YYYY-MM-DD HH:MM:SS' 형식으로 주면 그 시각까지 반영된 재무값만 사용해
    재실행해도 항상 동일한 결과를 보장한다. None(기본값)이면 기존과 동일하게
    항상 최신 재무값을 사용 — 라이브 대시보드 동작은 변경 없음.
    """
    init_backtest_db()
    _strat_key = strategy_key or version.lower().replace('+', '_').replace(' ', '_')
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status,strategy)
            VALUES (?,?,?,?,?,?,'running',?)
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions, _strat_key))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running', strategy=? WHERE run_id=?",
                     (_strat_key, run_id))
        conn.commit()

    _record_run_spec(
        run_id,
        _strat_key,
        "generic_cashledger_v2_20260714",
        {
            "version": version,
            "signal_fn": getattr(signal_fn, "__name__", str(signal_fn)),
            "start": start_date,
            "end": end_date,
            "per_stock": per_stock,
            "max_positions_initial": max_positions,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trail_stop": trail_stop,
            "mktcap_min": mktcap_min,
            "max_new_per_month": max_new_per_month,
            "use_market_filter": use_market_filter,
            "asof_mktcap": asof_mktcap,
            "avoid_overheat": avoid_overheat,
            "chart_confluence": chart_confluence,
            "sell_signal_fn": getattr(sell_signal_fn, "__name__", None),
            "entry_bonus_fn": getattr(entry_bonus_fn, "__name__", None),
            "data_asof_ts": data_asof_ts,
        },
        signal_timing="close_D",
        execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule="dynamic_slot_count_fixed_ticket",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            # KOSPI 데이터 없으면 전체 종목에서 날짜 추출
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]

        # 재무 데이터 로드
        # data_asof_ts 지정 시 그 시각 이후 UPDATE된 행은 제외(재현성 고정용, 2026-09-04).
        fin_all: Dict[str, list] = {}
        for r in conn.execute(f"""
            SELECT f.stock_code, f.year, f.quarter,
                   f.revenue, f.operating_profit, f.eps, f.bps,
                   f.total_equity, f.net_income, f.roe,
                   CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END,
                   COALESCE(d.avail_date,
                     CASE WHEN f.is_annual=1 THEN printf('%d-03-31', f.year+1)
                          WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                          WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                          WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                          ELSE printf('%d-02-15', f.year+1) END) as avail_date
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d ON
                d.stock_code = f.stock_code AND d.year = f.year
                AND d.quarter = CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                AND d.is_annual = CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
            WHERE ((f.is_annual=0 AND f.quarter BETWEEN 1 AND 4)
               OR (f.is_annual=1))
              {"AND f.updated_at <= ?" if data_asof_ts else ""}
            ORDER BY f.stock_code, f.year, f.quarter
        """, ([data_asof_ts] if data_asof_ts else [])).fetchall():
            sc = r[0]
            fin_all.setdefault(sc, []).append(r[1:])

        # as-of 모드는 현재 유니버스를 읽지 않고 신호일에 유효한 security master를 사용한다.
        if asof_mktcap:
            stock_codes = [r[0] for r in conn.execute("""
                SELECT ph.stock_code, COUNT(*) AS cnt
                FROM price_history ph
                JOIN security_master_history sm ON sm.stock_code=ph.stock_code
                  AND substr(ph.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(ph.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                WHERE ph.date>=? AND ph.date<=? AND ph.close>0
                GROUP BY ph.stock_code HAVING COUNT(*) >= 200
            """, (warmup_start, end_date)).fetchall()]
        else:
            stock_codes = [r[0] for r in conn.execute("""
                SELECT ph.stock_code, COUNT(*) AS cnt
                FROM price_history ph
                JOIN stock_universe su ON ph.stock_code=su.stock_code
                WHERE su.market_cap>=? AND LENGTH(su.stock_code)=6
                  AND ph.date>=? AND ph.date<=? AND ph.close>0
                GROUP BY ph.stock_code HAVING COUNT(*) >= 200
            """, (mktcap_min, warmup_start, end_date)).fetchall()]
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0
        mktcap_map = {}
        if stock_codes:
            ph = ",".join("?" * len(stock_codes))
            mktcap_map = {
                sc: (float(mc) if mc is not None else mktcap_min)
                for sc, mc in conn.execute(
                    f"SELECT stock_code, market_cap FROM stock_universe WHERE stock_code IN ({ph})",
                    stock_codes,
                ).fetchall()
            }

        # 종목별 데이터 로드
        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            try:
                rows = conn.execute("""
                    SELECT date, close, COALESCE(volume,0),
                           COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0),
                           COALESCE(open,0),
                           COALESCE(high,close), COALESCE(low,close)
                    FROM price_history
                    WHERE stock_code=? AND date>=? AND date<=? AND close>0
                    ORDER BY date ASC
                """, (sc, warmup_start, end_date)).fetchall()
                if len(rows) < 200:
                    continue
                dates   = [r[0] for r in rows]
                prices  = [float(r[1]) for r in rows]
                volumes = [float(r[2]) for r in rows]
                frn     = [float(r[3]) for r in rows]
                inst    = [float(r[4]) for r in rows]
                opens   = [float(r[5]) for r in rows]
                highs   = [float(r[6]) for r in rows]
                lows    = [float(r[7]) for r in rows]
                sim_start_i = next((i for i, d in enumerate(dates) if d >= start_date), len(dates))
                stock_data[sc] = {
                    'dates': dates, 'prices': prices, 'volumes': volumes,
                    'frn': frn, 'inst': inst, 'fins': fin_all.get(sc, []),
                    'sim_start_i': sim_start_i, 'opens': opens,
                    'highs': highs, 'lows': lows,
                    'mkt_cap_억': mktcap_map.get(sc, mktcap_min),
                }
                if chart_confluence:
                    stock_data[sc]['chart'] = _chart_prep(dates, lows, prices)
            except Exception:
                continue

        # KOSPI 시장 필터
        market_bullish: Dict[str, bool] = {}
        try:
            kospi_rows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r[0] for r in kospi_rows]
            k_prices = [float(r[1]) for r in kospi_rows]
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    continue
                kma60  = _ma(k_prices[max(0, ki - 59):  ki + 1], 60)
                kma120 = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                # 이중 조건: MA60 OR MA120 둘 중 하나라도 위여야 매수 허용
                # 둘 다 하회하면 확인된 하락장 → 매수 차단
                above_ma60  = (kma60  is None) or (k_prices[ki] > kma60)
                above_ma120 = (kma120 is None) or (k_prices[ki] > kma120)
                market_bullish[kd] = above_ma60 or above_ma120
        except Exception:
            pass

        conn.close()

        # 전략별 데이터 기반 매도 함수 (익절/손절/trail 파라미터 반영)
        def _check_sell_generic(i, prices, pos, sd=None):
            curr = prices[i]
            if curr > pos.get('peak_price', pos['entry_price']):
                pos['peak_price'] = curr
            pct       = (curr - pos['entry_price']) / pos['entry_price']
            peak      = pos.get('peak_price', pos['entry_price'])
            hold_days = pos.get('hold_days', 0)
            pos['hold_days'] = hold_days + 1
            # 익절
            if pct >= take_profit:
                return f"익절(+{pct*100:.0f}%)"
            # 손절
            if pct <= stop_loss:
                return f"손절({stop_loss*100:.0f}%)"
            # ★ 전략별 데이터 기반 매도 (진입조건 역전) — 시간기반 180일 대체
            if sell_signal_fn and sd:
                extra_reason = sell_signal_fn(i, sd, pos)
                if extra_reason:
                    return extra_reason
            # ★ 고점 컨플루언스 청산 (2026-07-18 공통 모듈): 이익권(+10%)에서
            # 일봉 MA5<MA10 + 주봉 lower-high + 하락캔들 중 2개 이상 합의 시 선제 정리
            if chart_confluence and sd and pct >= 0.10:
                if _chart_top_confluence(
                    sd['prices'], sd['opens'], sd['highs'], sd['lows'], sd.get('chart'), i) >= _CHART_TOP_MIN:
                    return f"고점차트청산(+{pct*100:.0f}%)"
            if hold_days >= 5:
                trail = (curr - peak) / peak if peak > 0 else 0
                # 추적손절: trail_stop 파라미터로 조정 가능 (기본 -10%)
                if trail <= trail_stop and pct > 0.03:
                    return f"추적손절(고점-{abs(trail)*100:.0f}%)"
                ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
                if ma60 is not None and curr < ma60:
                    return "MA60 붕괴"
            # 초기 부진 조기 청산 (-10% 이상 손실 지속 + MA20 하향 = 진입 오류)
            # 기준 -10%: 일시적 조정(-5%)은 실적 우량주에서 회복 가능
            if hold_days >= 20:
                ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
                if pct <= -0.10 and ma20 and curr < ma20:
                    return f"초기부진({pct*100:.0f}%)"
            # 안전망: 횡보 장기 보유 방지 (240일 이상 + 손익 -5%~+15% 구간)
            # sell_signal_fn이 있는 전략은 위에서 처리됨 — 여기는 V10/V11 등 fallback
            if hold_days >= 240 and -0.05 < pct < 0.15:
                ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
                if ma20 and curr < ma20:  # 단기 추세 하락 확인
                    return f"장기횡보({pct*100:+.0f}%)"
            return None

        # 포트폴리오 시뮬레이션 (signal_fn 주입)
        total_capital = per_stock * max_positions
        cash = total_capital
        date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                    for sc, d in stock_data.items()}
        positions: Dict[str, dict] = {}
        trades:    list = []
        equity_curve: list = []
        monthly_buys: Dict[str, int] = {}  # 'YYYY-MM' → 월별 신규 매수 건수
        _pb_g: Dict[str, dict] = {}   # pending buys  (D+1 집행)
        _ps_g: Dict[str, dict] = {}   # pending sells (D+1 집행)

        def _marked_equity(day: str) -> float:
            value = cash
            for sc, pos in positions.items():
                i = date_idx.get(sc, {}).get(day)
                price = stock_data[sc]['prices'][i] if i is not None else pos['entry_price']
                value += price * pos['qty']
            return value

        def _dynamic_limit(day: str) -> int:
            # 1억원/1천만원 단위로 시작하고, 평가자산 1.1억원부터 11번째 슬롯을 허용한다.
            return max(1, int(_marked_equity(day) // per_stock))

        for day in sim_dates:

            # ── Phase A: 전일 매도 신호 → 오늘 시가/종가 집행 ────────
            to_remove = []
            for sc in list(_ps_g.keys()):
                if sc not in positions:
                    to_remove.append(sc)
                    continue
                pos = positions[sc]
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i   = im[day]
                op  = sd['opens'][i] if i < len(sd.get('opens', [])) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                net_amt, net_pct = _net_profit(
                    pos['entry_price'], curr, pos['qty'],
                    pos.get('mkt_cap_억', sd.get('mkt_cap_억', 500))
                )
                trades.append({
                    'stock_code':  sc,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   day,
                    'entry_price': pos['entry_price'],
                    'exit_price':  curr,
                    'qty':         pos['qty'],
                    'profit_pct':  net_pct,
                    'profit_amt':  net_amt,
                    'exit_reason': _ps_g[sc].get('reason', '매도'),
                })
                cash += pos.get('cost', pos['entry_price'] * pos['qty']) + net_amt
                del positions[sc]
                to_remove.append(sc)
            for sc in to_remove:
                _ps_g.pop(sc, None)

            # ── Phase B: 전일 매수 신호 → 오늘 시가/종가 집행 ────────
            sorted_buys = sorted(_pb_g.items(), key=lambda x: 0, reverse=False)
            for sc, meta in sorted_buys:
                if sc in positions or len(positions) >= _dynamic_limit(day):
                    continue
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                month_key = day[:7]
                if monthly_buys.get(month_key, 0) >= max_new_per_month:
                    continue
                i  = im[day]
                op = sd['opens'][i] if i < len(sd.get('opens', [])) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                budget = min(per_stock, cash)
                qty = int(budget // curr)
                if qty < 1:
                    continue
                cost = qty * curr
                cash -= cost
                positions[sc] = {'entry_date': day, 'entry_price': curr,
                                 'qty': qty, 'cost': cost,
                                 'peak_price': curr, 'hold_days': 0,
                                 'mkt_cap_억': sd.get('mkt_cap_억', mktcap_min)}
                monthly_buys[month_key] = monthly_buys.get(month_key, 0) + 1
            _pb_g.clear()

            # ── Phase D: 매도 신호 탐지 → _ps_g 큐 ──────────────────
            # 하락장에도 항상 실행 — 손절/추적손절은 시장방향과 무관하게 적용
            for sc, pos in list(positions.items()):
                if sc in _ps_g:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i  = im[day]
                sd = stock_data[sc]
                reason = _check_sell_generic(i, sd['prices'], pos, sd=sd)
                if reason is None:
                    continue
                _ps_g[sc] = {'reason': reason}

            # ── 시장 필터 (use_market_filter=False인 전략은 하락장에서도 매수) ───
            # 손절은 Phase D에서 이미 처리됨 — 신규 매수만 차단
            if use_market_filter and market_bullish and not market_bullish.get(day, True):
                equity_curve.append({'date': day, 'equity': round(_marked_equity(day))})
                continue

            # ── Phase E: 매수 신호 탐지 → _pb_g 큐 ──────────────────
            month_key = day[:7]
            free_slots = _dynamic_limit(day) - len(positions) + len(_ps_g)
            if free_slots > 0 and monthly_buys.get(month_key, 0) < max_new_per_month:
                cap = max_new_per_month - monthly_buys.get(month_key, 0)
                if entry_bonus_fn is not None:
                    # 보너스 있는 종목 우선 진입 — 무보너스 종목끼리는 기존(dict) 순서 유지 (stable sort)
                    sigs = []
                    for sc, sd in stock_data.items():
                        if sc in positions or sc in _ps_g or sc in _pb_g:
                            continue
                        im = date_idx.get(sc, {})
                        if day not in im:
                            continue
                        i = im[day]
                        if asof_mktcap:
                            sh = _shares_asof(sc, day)
                            if sh <= 0 or sh * sd['prices'][i] / 1e8 < mktcap_min:
                                continue
                        if avoid_overheat is not None and i >= 40:
                            _c40 = sd['prices'][i - 40]
                            if _c40 > 0 and (sd['prices'][i] / _c40 - 1) > avoid_overheat:
                                continue
                        if not signal_fn(i, sd['sim_start_i'], sd['dates'], sd['prices'],
                                         sd['volumes'], sd['frn'], sd['inst'], sd['fins']):
                            continue
                        # 바닥 컨플루언스 게이트 (2026-07-18 공통 모듈)
                        if chart_confluence and _chart_bottom_confluence(
                            sd['prices'], sd['opens'], sd['highs'], sd['lows'], sd.get('chart'), i) < _CHART_BOTTOM_MIN:
                            continue
                        sigs.append((sc, float(entry_bonus_fn(sc, day) or 0.0)))
                    sigs.sort(key=lambda x: -x[1])
                    for sc, _ in sigs[:cap]:
                        _pb_g[sc] = {}
                else:
                    for sc, sd in stock_data.items():
                        if len(_pb_g) >= cap:
                            break
                        if sc in positions or sc in _ps_g or sc in _pb_g:
                            continue
                        im = date_idx.get(sc, {})
                        if day not in im:
                            continue
                        i = im[day]
                        if asof_mktcap:
                            sh = _shares_asof(sc, day)
                            if sh <= 0 or sh * sd['prices'][i] / 1e8 < mktcap_min:
                                continue
                        if avoid_overheat is not None and i >= 40:
                            _c40 = sd['prices'][i - 40]
                            if _c40 > 0 and (sd['prices'][i] / _c40 - 1) > avoid_overheat:
                                continue
                        if not signal_fn(i, sd['sim_start_i'], sd['dates'], sd['prices'],
                                         sd['volumes'], sd['frn'], sd['inst'], sd['fins']):
                            continue
                        # 바닥 컨플루언스 게이트 (2026-07-18 공통 모듈)
                        if chart_confluence and _chart_bottom_confluence(
                            sd['prices'], sd['opens'], sd['highs'], sd['lows'], sd.get('chart'), i) < _CHART_BOTTOM_MIN:
                            continue
                        _pb_g[sc] = {}

            # ── 에쿼티 ────────────────────────────────────────────────
            equity_curve.append({'date': day, 'equity': round(_marked_equity(day))})

        # 강제 청산
        last_day = sim_dates[-1] if sim_dates else None
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            has_fresh_final_price = bool(last_day and last_day in im)
            curr = sd['prices'][im[last_day]] if has_fresh_final_price else 0.0
            final_reason = '기간종료' if has_fresh_final_price else '기간종료(시세부재 전액손실)'
            net_amt, net_pct = _net_profit(
                pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500)
            )
            trades.append({
                'stock_code': sc, 'entry_date': pos['entry_date'],
                'exit_date': last_day or pos['entry_date'],
                'entry_price': pos['entry_price'], 'exit_price': curr,
                'qty': pos['qty'],
                'profit_pct': net_pct,
                'profit_amt': net_amt,
                'exit_reason': final_reason,
            })
            cash += pos.get('cost', pos['entry_price'] * pos['qty']) + net_amt
            del positions[sc]

        # 종목명 + 성과
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for idx in range(0, len(codes), 100):
            batch = codes[idx:idx+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn2.execute(f"""
                SELECT DISTINCT ph.stock_code, COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn2.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)
        from collections import defaultdict
        monthly = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        per_name = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        exit_reasons = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1
        bear_days  = sum(1 for v in market_bullish.values() if not v) if use_market_filter else 0
        total_days = len(market_bullish) if use_market_filter else 0
        filter_pct = round(bear_days / total_days * 100, 1) if total_days else 0
        mkt_label = f"하락장 차단: {bear_days}/{total_days}일({filter_pct}%)" if use_market_filter else "시장필터 없음 (흑자전환 전략 — 하락장에서도 매수)"

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ {version}: 엄격 다음날시가·정수주식·실제현금·동적복리 / {'KOSPI MA120 필터' if use_market_filter else '시장필터 없음'} / 익절{take_profit*100:.0f}% / 손절{stop_loss*100:.0f}% / 추적손절-12%\n"
            f"{mkt_label}\n"
            f"총 거래: {metrics['total_trades']}건  승률: {metrics['win_rate']}%  "
            f"CAGR: {metrics['cagr']}%  MDD: {metrics['max_drawdown_pct']}%  샤프: {metrics['sharpe']}\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )
        result = {
            **metrics,
            'monthly':      [{'month': k, 'profit': v} for k, v in sorted(monthly.items())],
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x: -x[1])[:5]],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x:  x[1])[:5]],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True),
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        try:
            from run_registry import register_artifact
            spec_conn = sqlite3.connect(DB_PATH, timeout=30)
            spec_row = spec_conn.execute(
                "SELECT run_hash FROM backtest_run_specs WHERE run_id=?", (run_id,)
            ).fetchone()
            spec_conn.close()
            if spec_row and spec_row[0]:
                run_hash = spec_row[0]
                register_artifact(run_hash, "execution_contract", True, {
                    "signal_timing": "close_D", "execution_timing": "next_open",
                    "integer_shares": True, "stale_price_exit": "zero_recovery_if_no_period_end_price",
                })
                expected_cash = total_capital + sum(float(t.get('profit_amt') or 0) for t in trades)
                cash_delta = cash - expected_cash
                register_artifact(run_hash, "cash_reconciliation", abs(cash_delta) < 1.0, {
                    "initial_cash": total_capital, "final_cash": cash,
                    "ledger_expected_cash": expected_cash, "delta": cash_delta,
                })
                pit_conn = sqlite3.connect(DB_PATH, timeout=30)
                pit_counts = dict(pit_conn.execute("""
                    SELECT interval_quality,COUNT(*) FROM security_master_history
                    WHERE market IN ('KOSPI','KOSDAQ') AND is_tradable=1 AND is_etf_etn=0
                    GROUP BY interval_quality
                """).fetchall()) if asof_mktcap else {}
                share_counts = dict(pit_conn.execute("""
                    SELECT sh.quality,COUNT(*) FROM security_share_history sh
                    WHERE EXISTS (
                      SELECT 1 FROM security_master_history sm
                      WHERE sm.stock_code=sh.stock_code AND sm.market IN ('KOSPI','KOSDAQ')
                        AND sm.is_tradable=1 AND sm.is_etf_etn=0)
                    GROUP BY sh.quality
                """).fetchall()) if asof_mktcap else {}
                shares_missing = pit_conn.execute("""
                    SELECT COUNT(*) FROM security_master_history sm
                    WHERE sm.is_tradable=1 AND sm.is_etf_etn=0
                      AND sm.market IN ('KOSPI','KOSDAQ') AND NOT EXISTS (
                      SELECT 1 FROM security_share_history sh WHERE sh.stock_code=sm.stock_code)
                """).fetchone()[0] if asof_mktcap else 0
                pit_conn.close()
                approx_count = sum(v for k, v in pit_counts.items() if "approx" in str(k))
                approx_share_count = sum(v for k, v in share_counts.items() if "approx" in str(k) or "fallback" in str(k))
                register_artifact(run_hash, "point_in_time_coverage", bool(
                    asof_mktcap and approx_count == 0 and approx_share_count == 0 and shares_missing == 0
                ), {
                    "master_counts": pit_counts, "approx_intervals": approx_count,
                    "share_counts": share_counts, "approx_share_intervals": approx_share_count,
                    "eligible_master_rows_without_shares": shares_missing,
                    "share_resolver": "security_share_history",
                    "note": "근사 상장구간·주식수 또는 주식수 미확정 종목이 하나라도 있으면 verified 승격 금지",
                })
        except Exception as artifact_error:
            logger.warning(f"[run_artifact] 기록 실패 {run_id}: {artifact_error}")
        return run_id

    except Exception as e:
        import traceback
        err = f"{e}\n{traceback.format_exc()}"
        try:
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


# ══════════════════════════════════════════════════════════════
#  V8 — 수출 선행지표 멀티팩터 (HS 무역통계 + 고용 데이터 활용)
# ══════════════════════════════════════════════════════════════



HS_DB_PATH  = "/Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab/data/hs_trade_lab.db"
EMP_DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/employment_monitor/employment.db"


def _load_trade_signals() -> Dict[str, Dict[str, float]]:
    """
    HS 무역통계 DB에서 종목별 월별 수출액 로드.
    반환: {stock_code: {ym: export_value}} (예: {'000660': {'2022-03': 900000000, ...}})
    ym 형식: 'YYYY-MM'
    """
    try:
        conn = sqlite3.connect(HS_DB_PATH, timeout=10)
        rows = conn.execute("""
            SELECT m.stock_code, t.period_ym, SUM(t.export_value) AS total_exp
            FROM hs_code_company_map m
            JOIN trade_series_cache t ON m.hs_code = t.hs_code
            WHERE t.export_value > 0
            GROUP BY m.stock_code, t.period_ym
            ORDER BY m.stock_code, t.period_ym
        """).fetchall()
        conn.close()
        result: Dict[str, Dict[str, float]] = {}
        for sc, ym, val in rows:
            result.setdefault(sc, {})[ym] = float(val)
        return result
    except Exception:
        return {}




def _get_export_yoy(trade_sc: Dict[str, float], ref_ym: str) -> Optional[float]:
    """
    주어진 period_ym 기준으로 YoY 수출 증가율 계산.
    ref_ym: 'YYYY-MM' 형식, 1년 전 동일 월과 비교.
    """
    if not trade_sc or ref_ym not in trade_sc:
        return None
    y, m = int(ref_ym[:4]), int(ref_ym[5:7])
    ya_ym = f"{y - 1}-{m:02d}"
    if ya_ym not in trade_sc:
        return None
    base = trade_sc[ya_ym]
    if base <= 0:
        return None
    return (trade_sc[ref_ym] - base) / base * 100.0




def _date_to_ym(date_str: str, lag_months: int = 2) -> str:
    """
    날짜 문자열('YYYY-MM-DD')을 2개월 전 ym('YYYY-MM')으로 변환.
    무역통계는 약 1-2개월 후 공표 → 2개월 지연 적용.
    """
    y, m = int(date_str[:4]), int(date_str[5:7])
    m -= lag_months
    if m <= 0:
        m += 12
        y -= 1
    return f"{y}-{m:02d}"




def _score_stock(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
    code: str = None,
    dilution_map: "Dict[str, list]" = None,
    buyback_map: "Dict[str, list]" = None,
    patent_map: "Dict[str, list]" = None,
) -> int:
    """
    종목 품질 점수 (0~100 + 이벤트 보정 -6~+6).

    [흑자전환] +35점 최대:
      현재 최근 분기 OP > 30억       → +15점
      직전 분기도 OP > 30억          → +10점
      1년 전 동기 OP < 10억 (전환)   → +10점

    [추세 정배열] +25점 최대:
      현재가 > MA20 > MA60           → +15점
      현재가 > MA120                 → +10점

    [거래량 급등] +15점:
      10일 평균 대비 1.5배+          → +10점
      10일 평균 대비 2.0배+          → +5점 추가

    [수급 확인] +20점 최대:
      기관 5일 순매수 양수           → +10점
      외국인 5일 순매수 양수         → +10점

    [가치 보너스] +5점:
      Graham 할인 20%+ OR PBR<1.2   → +5점

    [이벤트 보정] (code + 각 map이 주어졌을 때만, 기본 비활성):
      +특허/기술이전/R&D/라이선스(365일 이내, dart_rd_patent_signals) 최대 +3점
        — tech_transfer/license +3, patent +2, rd_contract +1 (최고값만 반영)
      +자사주 매입/신탁/소각(180일 이내, treasury_buyback) 최대 +3점
        — 소각(cancellation) +3, 취득결정/취득결과/acquisition/trust +2 (최고값만 반영)
      -희석위험(365일 이내 CB/BW/EB/RIGHTS, dilution_events, 실질 미발행 공시 제외) 최대 -6점
        — 1~2건 -3점, 3건 이상 -6점

    기준 점수: 60점 이상 = 매수 신호
    """
    if i < sim_start_i or i < 60:
        return 0
    curr = prices[i]
    if curr <= 0:
        return 0

    # ── [절대 모멘텀 사전 필터] ──
    # 3개월(-15%) 또는 1개월(-5%) 심한 하락주 진입 금지
    if i >= 63:
        ret3m = (curr - prices[i-63]) / prices[i-63]
        if ret3m < -0.15:
            return 0   # 3개월 -15% 이상 하락
    if i >= 21:
        ret1m = (curr - prices[i-21]) / prices[i-21]
        if ret1m < -0.05:
            return 0   # 1개월 -5% 이상 하락

    score = 0

    # ── [흑자전환] ──
    available = [r for r in fin_rows
                 if not r[9] and r[1] in (1, 2, 3, 4)
                 and (r[10] if len(r) > 10 and r[10] else _release_date(r[0], r[1], bool(r[9]))) <= dates[i]]
    available.sort(key=lambda r: (r[0], r[1]), reverse=True)
    if len(available) >= 1:
        op0 = available[0][3]
        rev0 = available[0][2]
        if op0 and op0 >= 3_000_000_000:
            score += 15
            if len(available) >= 2:
                op1 = available[1][3]
                if op1 and op1 >= 3_000_000_000:
                    score += 10
            # 1년 전 적자 (흑자전환)
            y0, q0 = available[0][0], available[0][1]
            ya_cands = [r for r in fin_rows
                        if r[1] == q0 and r[0] == y0 - 1 and not r[9]
                        and (r[10] if len(r) > 10 and r[10] else _release_date(r[0], r[1], bool(r[9]))) <= dates[i]]
            if ya_cands:
                op_ya = ya_cands[0][3]
                if op_ya is not None and op_ya < 1_000_000_000:
                    score += 10
    else:
        return 0   # 재무 데이터 없으면 0점

    # ── [추세] ──
    ma20  = _ma(prices[max(0, i-19):i+1], 20)
    ma60  = _ma(prices[max(0, i-59):i+1], 60)
    ma120 = _ma(prices[max(0, i-119):i+1], 120) if i >= 120 else None
    if ma20 and ma60 and curr > ma20 > ma60:
        score += 15
    if ma120 and curr > ma120:
        score += 10

    # ── [거래량] ──
    vol_window = [v for v in volumes[max(0, i-9):i] if v and v > 0]
    if vol_window and volumes[i] and volumes[i] > 0:
        avg10v = sum(vol_window) / len(vol_window)
        if avg10v > 0:
            ratio = volumes[i] / avg10v
            if ratio >= 1.5:
                score += 10
            if ratio >= 2.0:
                score += 5

    # ── [수급] — 기관+외국인 동반 순매수가 핵심 ──
    if i >= 5:
        inst5 = sum(inst_net[i-4:i+1])
        frn5  = sum(frn_net[i-4:i+1])
        if inst5 > 0 and frn5 > 0:
            score += 20   # 동반 순매수: 강한 수급 확인 (20점)
        elif inst5 > 0 or frn5 > 0:
            score += 5    # 한쪽만: 약한 수급 (5점)

    # ── [가치 보너스] ──
    fin_latest = _get_financial_as_of(fin_rows, dates[i])
    if fin_latest:
        _y, _q, _rev, _op, eps, bps, _eq, _ni, _roe, _ann, *_ = fin_latest
        if eps and eps > 0 and bps and bps > 0:
            import math as _m2
            graham_iv = _m2.sqrt(22.5 * eps * bps)
            if graham_iv > 0 and curr <= graham_iv * 0.80:
                score += 5
            else:
                pbr = curr / bps
                if pbr < 1.2:
                    score += 5

    # ── [이벤트 보정] — code + 각 map이 주어졌을 때만 (기본 비활성, opt-in) ──
    if code:
        asof = dates[i]
        if patent_map is not None:
            best = 0
            for ev_date, sig_type in patent_map.get(code, ()):
                if ev_date > asof:
                    continue
                if (datetime.strptime(asof, "%Y-%m-%d") - datetime.strptime(ev_date, "%Y-%m-%d")).days > 365:
                    continue
                pts = {"tech_transfer": 3, "license": 3, "patent": 2, "rd_contract": 1}.get(sig_type, 0)
                best = max(best, pts)
            score += best
        if buyback_map is not None:
            best = 0
            for ev_date, ev_type in buyback_map.get(code, ()):
                if ev_date > asof:
                    continue
                if (datetime.strptime(asof, "%Y-%m-%d") - datetime.strptime(ev_date, "%Y-%m-%d")).days > 180:
                    continue
                if ev_type in ("소각", "cancellation"):
                    best = max(best, 3)
                elif ev_type in ("취득결정", "취득결과", "acquisition", "trust"):
                    best = max(best, 2)
            score += best
        if dilution_map is not None:
            cutoff = (datetime.strptime(asof, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            cnt = sum(1 for d in dilution_map.get(code, ()) if cutoff <= d <= asof)
            if cnt >= 3:
                score -= 6
            elif cnt >= 1:
                score -= 3

    return score




_SECTOR_GROUPS: dict = {
    "전력기기": {"codes": ["267260", "010120", "298040", "017040", "103590", "033100", "000500", "001440"]},
    "원자력": {"codes": ["034020", "052690", "051600", "105840", "032820", "046120", "094820", "457550", "126720"]},
    "화장품/뷰티": {"codes": ["241710", "051900", "090430", "078520", "161890", "027050", "003350"]},
    "의료기기/미용": {"codes": ["214150", "214450", "278470", "336570", "149980", "145020"]},
    "반도체": {"codes": ["005930", "000660", "042700", "166090", "240810", "058470", "009150", "357780", "089030", "039030"]},
    "기판패키지": {"codes": ["222800", "353200", "095340", "007810", "007660", "195870"]},
    "2차전지": {"codes": ["247540", "006400", "373220", "086520", "066970", "003670"]},
    "방산": {"codes": ["047810", "012450", "272210", "064350", "000880", "079550"]},
    "조선": {"codes": ["009540", "329180", "010140", "042660"]},
    "바이오": {"codes": ["207940", "068270", "000100", "326030", "302440", "196170", "298380", "141080"]},
}


_sector_score_memo: dict = {}  # (sector_key, ym) → score  캐시


