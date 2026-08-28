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

DB_PATH = "/Applications/stock_dashboard/stock.db"
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
                    cwd="/Applications/stock_dashboard", timeout=5,
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
#  기술 지표 헬퍼
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
def run_backtest(start_date: str, end_date: str,
                 per_stock: float = 10_000_000,
                 max_positions: int = 10,
                 run_name: str = None,
                 run_id: str = None,
                 asof_mktcap: bool = True,
                 take_profit: float = 0.25,
                 big_gate: float = None,
                 trail_big: float = -0.35) -> str:
    """
    run_id가 주어지면 해당 레코드(이미 DB에 존재)를 직접 업데이트.
    없으면 새 run_id를 생성하고 INSERT.
    """
    init_backtest_db()
    run_name = run_name or f"백테스트 {start_date[:7]}~{end_date[:7]}"

    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'v4',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        # 이미 INSERT된 레코드 → 상태만 running으로 갱신
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    # 2026-07-18: 이 엔진은 run_spec 기록이 없어 trg_backtest_done_requires_spec에
    # 막혀 완료 불가였던 버그 수정. 최초에 "same_close 레거시"로 기록했으나 재검증 결과
    # 실행기 _run_portfolio는 D+1 pending 큐 체결 + 현금원장(budget 검사·cash 차감)을
    # 갖춘 엄격 엔진임을 확인(P0 Codex timing fix, backtest.py:831) — 실제 동작대로 정정.
    # 2026-07-27: as-of 시총(진입일 주가×상장주식수, security_master_history/
    # security_share_history 기반) 게이트를 기본화 — _run_generic_backtest와 동일 패턴.
    _record_run_spec(
        run_id, "v4", "v4_portfolio_nextopen_cashledger",
        {"per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date, "asof_mktcap": asof_mktcap,
         "take_profit": take_profit, "big_gate": big_gate, "trail_big": trail_big},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    try:
        _load_disc_dates(conn)  # 실제 DART 공시일 로드

        # ── 워밍업 시작일: start_date 보다 WARMUP_DAYS 거래일 이전 ──
        # 달력 기준 약 450일 전(영업일 300일 여유분)
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')

        # ── 시뮬레이션 거래일 목록 (start_date ~ end_date) ─────────
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        if not sim_dates:
            # KOSPI 없으면 전체 종목에서 추출
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]

        # ── 재무 데이터 로드 (분기 + 연간 모두, 실제 공시일 포함) ──
        # 컬럼: year, quarter, rev, op, eps, bps, equity, net_inc, roe, is_annual, avail_date
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
                          ELSE printf('%d-02-15', f.year+1) END
                   ) as avail_date
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d ON
                d.stock_code = f.stock_code AND d.year = f.year
                AND d.quarter = CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                AND d.is_annual = CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
            WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 3)
               OR (f.is_annual=1)
            ORDER BY f.stock_code, f.year, f.quarter
        """).fetchall():
            sc = r[0]
            fin_all.setdefault(sc, []).append(r[1:])   # (y,q,...,is_ann,avail_date)

        # ── 종목 목록 (시총 1000억 이상 + 워밍업 포함 충분한 데이터 보유) ─
        # stock_universe.market_cap은 억원 단위 → 1000억 = 1000
        # 2026-07-27: as-of 모드는 현재시점 유니버스 대신 신호일 시점 security master 사용
        # (룩어헤드 제거, _run_generic_backtest와 동일 패턴).
        MKTCAP_MIN_V4 = 1000.0
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
                INNER JOIN (
                    SELECT stock_code FROM stock_universe
                    WHERE market_cap >= 1000
                      AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                    GROUP BY stock_code
                ) su ON ph.stock_code = su.stock_code
                WHERE ph.date>=? AND ph.date<=? AND ph.close>0
                GROUP BY ph.stock_code
                HAVING COUNT(*) >= 200
            """, (warmup_start, end_date)).fetchall()]

        # ── as-of 상장주식수 이력 (as-of 시총 게이트용) ──────────────
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_v4(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        # ── 시총 로드 (슬리피지 계산용) ─────────────────────────────
        mkt_cap_map: Dict[str, float] = {}
        if stock_codes:
            ph = ','.join('?' * len(stock_codes))
            mkt_cap_map = {r[0]: float(r[1] or 500) for r in conn.execute(
                f"SELECT stock_code, COALESCE(market_cap, 500) FROM stock_universe "
                f"WHERE stock_code IN ({ph})", stock_codes
            ).fetchall()}

        # ── 종목별 데이터 로드 ────────────────────────────────────
        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            try:
                rows = conn.execute("""
                    SELECT date, close,
                           COALESCE(volume, 0),
                           COALESCE(frn_net_buy, 0),
                           COALESCE(inst_net_buy, 0),
                           COALESCE(open, 0)
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
                fins    = fin_all.get(sc, [])

                # 시뮬레이션 시작 인덱스 (start_date 이후 첫 거래일)
                sim_start_i = next(
                    (i for i, d in enumerate(dates) if d >= start_date),
                    len(dates)
                )

                stock_data[sc] = {
                    'dates':       dates,
                    'prices':      prices,
                    'volumes':     volumes,
                    'frn':         frn,
                    'inst':        inst,
                    'fins':        fins,
                    'sim_start_i': sim_start_i,
                    'mkt_cap_억':  mkt_cap_map.get(sc, 500),
                    'opens':       opens,
                }
            except Exception:
                continue

        # ── ★ KOSPI 시장 추세 필터 + 국면별 익절 생성 ─────────────────
        # KOSPI > MA120: 상승장(매수 허용), 아니면 하락장(매수 금지)
        # KOSPI vs MA60 비율로 익절 동적 조정:
        #   KOSPI > MA60 × 1.15: 강세장 → TP=75% (대세 상승장에서 winner 유지)
        #   KOSPI > MA60 × 1.05: 보통 강세 → TP=40%
        #   KOSPI > MA60:         보통장  → TP=25%
        #   KOSPI < MA60:         약세장  → TP=20%
        market_bullish: Dict[str, bool] = {}
        take_profit_map: Dict[str, float] = {}
        trail_stop_map: Dict[str, float] = {}
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
                kma60  = _ma(k_prices[max(0, ki - 59): ki + 1], 60)
                kma120 = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                kp = k_prices[ki]
                # 매수 허용 여부 (MA120 기준)
                if kma120 is None:
                    market_bullish[kd] = True
                else:
                    market_bullish[kd] = kp > kma120
                # 국면별 익절+추적손절 (MA60 비율 기준)
                # TP는 기본 0.25 유지(회전속도 최적, 2026-08-09 take_profit 파라미터화 —
                # 텐버거 population 캡처 실험용, 기본값은 기존과 동일), trail만 강세장에서 살짝 완화
                if kma60 is None:
                    take_profit_map[kd] = take_profit;  trail_stop_map[kd] = -0.10
                elif kp > kma60 * 1.10:    # 강세장: trail -12% (조정에 여유)
                    take_profit_map[kd] = take_profit
                    trail_stop_map[kd]  = -0.12
                elif kp > kma60:           # 보통장
                    take_profit_map[kd] = take_profit
                    trail_stop_map[kd]  = -0.10
                else:                      # 약세장
                    take_profit_map[kd] = take_profit
                    trail_stop_map[kd]  = -0.10
        except Exception:
            pass  # KOSPI 데이터 없으면 필터 비활성화

        conn.close()

        # ── 포트폴리오 시뮬레이션 ───────────────────────────────
        total_capital = per_stock * max_positions
        trades, equity_curve = _run_portfolio(
            sim_dates, stock_data,
            per_stock, max_positions,
            stop_loss=-0.08,
            market_bullish=market_bullish if market_bullish else None,
            take_profit_map=take_profit_map if take_profit_map else None,
            trail_stop_map=trail_stop_map   if trail_stop_map  else None,
            asof_mktcap=asof_mktcap,
            shares_asof_fn=_shares_asof_v4 if asof_mktcap else None,
            mktcap_min=MKTCAP_MIN_V4,
            big_gate=big_gate, trail_big=trail_big,
        )

        # ── 종목명 매핑 ──────────────────────────────────────
        conn2  = sqlite3.connect(DB_PATH, timeout=120)
        name_map: Dict[str, str] = {}
        codes = list({t['stock_code'] for t in trades})
        for i in range(0, len(codes), 100):
            batch = codes[i:i + 100]
            ph = ','.join('?' * len(batch))
            for sc, sn in conn2.execute(f"""
                SELECT DISTINCT ph.stock_code,
                       COALESCE(sm.stock_name, su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_meta sm USING(stock_code)
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn2.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        # ── 성과 지표 ────────────────────────────────────────
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        # 월별 손익
        monthly: dict = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        monthly_list = [{'month': k, 'profit': v} for k, v in sorted(monthly.items())]

        # 종목별 손익
        from collections import defaultdict
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        top_winners = sorted(per_name.items(), key=lambda x: -x[1])[:5]
        top_losers  = sorted(per_name.items(), key=lambda x:  x[1])[:5]

        # 매도 사유별 통계
        exit_reasons: Dict[str, int] = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        # 시장 필터 통계
        bear_days  = sum(1 for v in market_bullish.values() if not v)
        total_days = len(market_bullish)
        filter_pct = round(bear_days / total_days * 100, 1) if total_days > 0 else 0

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 전략 v5: 시장추세필터(KOSPI MA120) / 손절-8% / 추적손절-10% / 익절+15% / 최소보유5일 / 시총1000억+\n"
            f"시장필터: 하락장 {bear_days}일/{total_days}일({filter_pct}%) 매수 차단\n"
            f"총 거래: {metrics['total_trades']}건  수익: {metrics['profit_trades']}건  "
            f"손실: {metrics['total_trades'] - metrics['profit_trades']}건\n"
            f"승률: {metrics['win_rate']}%  손익비: {metrics['pl_ratio']}배  "
            f"총손익: {metrics['total_profit_amt']:,}원\n"
            f"총수익률: {metrics['total_return_pct']}%  CAGR: {metrics['cagr']}%  "
            f"연환산: {metrics['ann_return_pct']}%\n"
            f"MDD: {metrics['max_drawdown_pct']}%  샤프지수: {metrics['sharpe']}\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )

        result = {
            **metrics,
            'monthly':      monthly_list,
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in top_winners],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in top_losers],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True),
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        # 2026-07-18: _run_portfolio는 D+1체결+현금원장 엄격 엔진 — 아티팩트 등록으로
        # derive_status legacy 고정 해제 (초기자본→최종에쿼티는 total_return으로 재구성)
        # 2026-07-27: as-of 시총 게이트 기본화에 맞춰 asof_mktcap 실제값 전달
        _init_cap = per_stock * max_positions
        _final_eq = _init_cap * (1 + float(metrics.get('total_return_pct') or 0) / 100.0)
        _register_execution_artifacts(run_id, _init_cap, _final_eq, asof_mktcap=asof_mktcap)
        return run_id

    except Exception as e:
        import traceback
        err = f"{e}\n{traceback.format_exc()}"
        try:
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


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


def _is_buy_value(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V2 가치매수 — Graham Value + Smart Money AND:
    교훈: 52W 범위 필터 + 느슨한 가치 조건이 역효과 (-5.5%).
    핵심 알파: 깊은 저평가(Graham) + 기관&외국인 동반 진입(AND).
    수정: Graham 조건 복원 + 수급 합산→AND 강화 + MA60 ±5% 유지.
      [A] Graham: PBR<0.7 AND PER<10 OR IV 25%+ 할인 + 영업이익>0
      [B] MA60 대비 5% 이상 하락 중이면 제외 (추세 붕괴 방지)
      [C] 수급 AND: 기관 5일 > 0 AND 외국인 5일 > 0 (합산→AND 강화)
    """
    if i < sim_start_i or i < 60:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    # [B] MA60 -5% 이상 하락 제외
    ma60 = _ma(prices[max(0, i-59):i+1], 60)
    if ma60 and curr < ma60 * 0.95:
        return False

    fin = _get_financial_as_of(fin_rows, dates[i])
    if fin is None:
        return False
    _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann, *_ = fin
    if not op or op <= 0:
        return False

    # [A] Graham 가치 조건 복원
    graham_ok = False
    if eps and eps > 0 and bps and bps > 0:
        import math as _m2
        graham_iv = _m2.sqrt(22.5 * eps * bps)
        if graham_iv > 0 and curr <= graham_iv * 0.75:
            graham_ok = True
        per = curr / eps
        pbr = curr / bps
        if 0 < per < 10 and pbr < 0.7:
            graham_ok = True
    if not graham_ok:
        return False

    # [C] 수급 AND (합산→AND, 더 엄격한 스마트머니 확인)
    if i >= 5:
        inst5 = sum(inst_net[max(0, i-4):i+1])
        frn5  = sum(frn_net[max(0, i-4):i+1])
        if inst5 <= 0 or frn5 <= 0:  # 둘 다 양수여야 함
            return False

    return True


def _is_buy_v2(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V3 재무우량 — 실증 보강:
    실증 문제: 재무지표(영업이익률/ROE) 단독은 신호 없음 (매출증가 0.94x와 유사)
    수정: 재무 우량 + 52W 범위 위치 확인 (추세 맥락 필수)
      [0] 52주 범위 55%+ (실증 최강 필터 추가)
      [A] 수익성 스코어 ≥ 2점 (이전 3점 → 2점, 52W필터가 더 중요)
      [B] 추세 AND 수급 (기존 유지)
    """
    if i < sim_start_i or i < 60:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    # [0] 52주 범위 55%+ (실증 핵심 필터)
    if i >= 120 and _52w_pct(prices, i) < 55:
        return False

    fin = _get_financial_as_of(fin_rows, dates[i])
    if fin is None:
        return False
    _y, _q, rev, op, eps, bps, eq, ni, roe, _ann, *_ = fin

    if not op or op <= 0:
        return False

    score = 0
    if rev and rev > 0:
        op_margin = op / rev
        if op_margin >= 0.05:
            score += 1
        if op_margin >= 0.08:
            score += 1
    if roe and roe >= 10:
        score += 1
    if ni and eq and eq > 0 and ni / eq >= 0.05:
        score += 1

    if score < 2:  # 3→2 (52W 필터가 더 중요)
        return False

    ma20 = _ma(prices[max(0, i-19):i+1], 20)
    ma60 = _ma(prices[max(0, i-59):i+1], 60)
    trend_ok = bool(ma20 and ma60 and curr > ma20 > ma60)

    supply_ok = False
    if i >= 5:
        inst5 = sum(inst_net[max(0, i-4):i+1])
        frn5  = sum(frn_net[max(0, i-4):i+1])
        supply_ok = (inst5 + frn5 > 0)

    return trend_ok and supply_ok


def _is_buy_v5(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V4 수급모멘텀 (Supply-Led Momentum):
    교훈: 52W 범위 추가가 회복장·최근 성과를 크게 훼손.
    MA정배열(MA20>MA60>MA120)이 이미 상승 맥락을 충분히 확인함.
    52W 필터는 중복 조건으로 오히려 기회를 차단.
      [S] 기관+외국인 5일 동반 순매수 (AND — 둘 다 필요)
      [T] MA20 > MA60 > MA120 정배열 (이미 상승 확인)
      [Q] 영업이익 > 0
    """
    if i < sim_start_i or i < 120:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    # [S] 수급: 기관 AND 외국인 5일 동반 순매수
    if i < 5:
        return False
    inst5 = sum(inst_net[i-4:i+1])
    frn5  = sum(frn_net[i-4:i+1])
    if inst5 <= 0 or frn5 <= 0:
        return False

    # [T] 추세: MA20 > MA60 > MA120 정배열
    ma20  = _ma(prices[max(0, i-19):i+1], 20)
    ma60  = _ma(prices[max(0, i-59):i+1], 60)
    ma120 = _ma(prices[max(0, i-119):i+1], 120)
    if not (ma20 and ma60 and ma120):
        return False
    if not (curr > ma20 > ma60 > ma120):
        return False

    # [Q] 실적: 영업이익 > 0
    fin = _get_financial_as_of(fin_rows, dates[i])
    if fin is None or not fin[3] or fin[3] <= 0:
        return False

    return True


def _load_dart_signal_map(min_signal: int = 2, window_days: int = 90) -> dict:
    """
    dart_contracts 테이블에서 최근 window_days 이내 min_signal 이상 수주공시를
    stock_code별 날짜 리스트로 로드.
    Returns: {stock_code: sorted list of disclosed_at ('YYYYMMDD' strings)}
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        rows = conn.execute("""
            SELECT stock_code, disclosed_at FROM dart_contracts
            WHERE signal_strength >= ? AND disclosed_at IS NOT NULL
            ORDER BY disclosed_at ASC
        """, (min_signal,)).fetchall()
        conn.close()
    except Exception:
        return {}

    result: dict = {}
    for sc, dt in rows:
        if sc and dt:
            result.setdefault(sc, []).append(str(dt)[:8])  # YYYYMMDD
    return result


def run_backtest_v1(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    """V1 트렌드 (미너비니 추세추종 기본) — 월 10개 한도"""
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V1', signal_fn=_is_buy_v1,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V1 트렌드 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,         # ★ 추세추종은 후보 많아서 월 10개 제한
        strategy_key='v_trend',
        # sell_signal_fn=_sell_signal_v1,  # 데스크로스 테스트: MA60붕괴 조건과 중복, 효과 없음(25.9%→25.9%)
    )


def run_backtest_v1_dart(start_date: str, end_date: str,
                         per_stock: float = 10_000_000,
                         max_positions: int = 10,
                         dart_min_signal: int = 2,
                         run_name: str = None, run_id: str = None) -> str:
    """
    V1 트렌드 + DART 수주공시 ★2 이상 (최근 90일) 필터.
    DART 5년치 데이터 기반 — 수주공시가 추세추종 매수에 유효한지 검증.
    """
    # DART 수주공시 데이터 사전 로드 (전체 기간)
    dart_map = _load_dart_signal_map(min_signal=dart_min_signal, window_days=9999)

    def _is_buy_v1_dart(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows,
                        _sc=None, **kw):
        if not _is_buy_v1(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows):
            return False
        if _sc not in dart_map:
            return False
        # 현재 날짜 기준 90일 이내 DART 수주공시 확인
        cur_dt = dates[i]  # 'YYYY-MM-DD'
        cutoff = (datetime.strptime(cur_dt, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y%m%d')
        cur_dt8 = cur_dt.replace('-', '')
        return any(cutoff <= dt <= cur_dt8 for dt in dart_map[_sc])

    return _run_generic_backtest_with_sc(
        version='V1+DART', signal_fn=_is_buy_v1_dart,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V1+DART★{dart_min_signal} {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=1000,    # 1000억+ (억원 단위)
    )


# ══════════════════════════════════════════════════════════════
#  V10 매수 시그널 — 이익 폭발 (에스티팜·에이피알·삼양식품 유형)
# ══════════════════════════════════════════════════════════════
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
def _is_buy_hidden_rev(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    52주 강세 돌파 (Breakout Momentum) — 실증 기반:
    [A] 52주 가격 범위 상위 65%+ (강한 추세에 있는 종목)
    [B] MA60 > MA120 × 1.02 (중기 추세 > 장기 추세, 정배열)
    [C] 현재가 MA60 대비 +3%~+25% (추세 위에 올라선 상태)
    [D] 거래량 최근 5일 > 20일 평균 × 1.3 (상승에 거래량 동반)
    [E] 단기 모멘텀: 현재가 > 10일 전 가격 (방향 확인)

    이론 아닌 실증: 2020~2025 982,889 시점 분석에서
    이 조합이 winRate 18.5%, 리프트 1.64x로 최고 성과 확인
    """
    if i < sim_start_i or i < 120:
        return False
    curr = prices[i]

    # [B] MA 정배열 (먼저 체크, 빠른 탈락)
    ma60  = sum(prices[i-59:i+1]) / 60
    ma120 = sum(prices[i-119:i+1]) / 120
    if not (ma60 > ma120 * 1.02):
        return False

    # [C] 현재가 MA60 위 3%~25%
    pos_ma60 = (curr - ma60) / ma60 * 100
    if not (3 <= pos_ma60 <= 25):
        return False

    # [A] 52주 범위 상위 65%+
    low_52w  = min(prices[max(0, i-252):i+1])
    high_52w = max(prices[max(0, i-252):i+1])
    if high_52w <= low_52w:
        return False
    pos_52w = (curr - low_52w) / (high_52w - low_52w) * 100
    if pos_52w < 65:
        return False

    # [D] 거래량 확대
    if i < 20:
        return False
    v20 = sum(volumes[i-20:i]) / 20
    v5  = sum(volumes[i-5:i])  / 5
    if not (v20 > 0 and v5 > v20 * 1.3):
        return False

    # [E] 단기 모멘텀 양수 (10일 전 대비 상승)
    if i >= 10:
        if curr <= prices[i-10]:
            return False

    return True


# ══════════════════════════════════════════════════════════════
#  V11 매수 시그널 — 흑자전환 모멘텀 (이수페타시스·엘앤에프 유형)
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
def _run_backtest_v12(conn, warmup_start, start_date, end_date, sim_dates,
                      per_stock, max_positions, stop_loss, stop_loss_pct,
                      take_profit_pct,
                      strict_exec: bool = True,
                      asof_mktcap: bool = True):
    """
    V12는 섹터별 상대강도를 계산해야 해서 별도 함수로 구현.
    섹터 alpha = 해당 섹터 평균 3개월 수익률 - KOSPI 3개월 수익률
    종목은 섹터 alpha > 0 이고 자체 RS도 양수인 경우만 매수.
    """
    # KOSPI 로드
    kospi_rows = conn.execute("""
        SELECT date, close FROM price_history
        WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
        ORDER BY date ASC
    """, (warmup_start, end_date)).fetchall()
    k_dates  = [r[0] for r in kospi_rows]
    k_prices = {r[0]: float(r[1]) for r in kospi_rows}

    # 시장 상승장 필터
    k_price_list = [float(r[1]) for r in kospi_rows]
    market_bullish = {}
    for ki, kd in enumerate(k_dates):
        if kd < start_date:
            continue
        kma120 = _ma(k_price_list[max(0, ki - 119): ki + 1], 120)
        market_bullish[kd] = (kma120 is None) or (k_price_list[ki] > kma120)

    # 섹터 정보 + 전 종목 로드 (섹터 정의된 종목만; 시총 2000억+ 게이트는 as-of 모드에서 신호평가 시점으로 이동)
    sector_map = {}
    _sector_universe_sql = """
        SELECT stock_code, COALESCE(NULLIF(sector_small,''), NULLIF(sector_large,''), '기타')
        FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
          AND sector_large NOT IN ('기타','벤처기업부','신성장기업부','우선주','리츠','ETF','ETN','','스팩')
    """ + ("" if asof_mktcap else " AND market_cap >= 2000")
    for sc, sec in conn.execute(_sector_universe_sql).fetchall():
        if sec and sec not in ('기타', '벤처기업부', '신성장기업부'):
            sector_map[sc] = sec

    share_intervals: Dict[str, list] = {}
    if asof_mktcap:
        for code, effective_from, effective_to, shares, quality in conn.execute(
            """SELECT stock_code,effective_from,effective_to,shares_issued,quality
               FROM security_share_history ORDER BY stock_code,effective_from"""
        ):
            share_intervals.setdefault(code, []).append(
                (effective_from, effective_to, float(shares or 0), quality)
            )

    def _v12_shares_asof(code: str, day: str) -> float:
        for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
            if effective_from <= day and (effective_to is None or day < effective_to):
                return shares
        return 0.0

    stock_data = {}
    for sc in sector_map:
        rows = conn.execute("""
            SELECT date, close, COALESCE(volume,0), COALESCE(open, close)
            FROM price_history
            WHERE stock_code=? AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (sc, warmup_start, end_date)).fetchall()
        if len(rows) < 60:
            continue
        dates  = [r[0] for r in rows]
        prices = [float(r[1]) for r in rows]
        vols   = [float(r[2]) for r in rows]
        opens  = [float(r[3]) if r[3] and r[3] > 0 else float(r[1]) for r in rows]
        sim_start_i = next((idx for idx, dt in enumerate(dates) if dt >= start_date), len(dates))
        stock_data[sc] = {'dates': dates, 'prices': prices, 'volumes': vols, 'opens': opens,
                          'sim_start_i': sim_start_i}

    # 날짜→인덱스 맵
    date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                for sc, d in stock_data.items()}

    positions = {}
    trades    = []
    equity_curve = []
    total_capital = per_stock * max_positions
    cash = total_capital  # 2026-07-16: 고정슬롯 P&L 누산 → 실제 현금원장 전환

    # 섹터별 3개월 수익률 캐시 (당일 계산, 재계산 최소화)
    _sector_cache = {}
    _sector_cache_date = [None]

    def _get_hot_sectors(day):
        if _sector_cache_date[0] == day:
            return _sector_cache
        _sector_cache.clear()
        _sector_cache_date[0] = day

        # KOSPI 3개월 수익률
        kp_now  = k_prices.get(day)
        if kp_now is None:
            return _sector_cache
        # 약 63 거래일 전 날짜 찾기
        k_date_list = sorted(k_prices.keys())
        idx_now = next((idx for idx, d in enumerate(k_date_list) if d >= day), None)
        if idx_now is None or idx_now < 60:
            return _sector_cache
        kp_63 = k_prices.get(k_date_list[max(0, idx_now - 63)])
        kospi_3m = (kp_now - kp_63) / kp_63 * 100 if kp_63 and kp_63 > 0 else 0

        # KOSPI 1개월(21일) 수익률도 계산 (초기사이클 감지용)
        kp_21  = k_prices.get(k_date_list[max(0, idx_now - 21)])
        kospi_1m = (kp_now - kp_21) / kp_21 * 100 if kp_21 and kp_21 > 0 else 0

        # 섹터별 3개월 + 1개월 수익률
        sec_rets   = {}  # 3M
        sec_rets1m = {}  # 1M
        for sc, sec in sector_map.items():
            if sc not in stock_data:
                continue
            sd = stock_data[sc]
            idx_map = date_idx.get(sc, {})
            i = idx_map.get(day)
            if i is None or i < 63 or i < sd['sim_start_i']:
                continue
            p_now = sd['prices'][i]
            p_63  = sd['prices'][i - 63]
            if p_63 <= 0:
                continue
            ret3m = (p_now - p_63) / p_63 * 100
            sec_rets.setdefault(sec, []).append(ret3m)
            # 1개월 수익률 (21일)
            if i >= 21:
                p_21 = sd['prices'][i - 21]
                if p_21 > 0:
                    sec_rets1m.setdefault(sec, []).append((p_now - p_21) / p_21 * 100)

        for sec, rets in sec_rets.items():
            avg    = sum(rets) / len(rets) if rets else 0
            alpha  = avg - kospi_3m
            rets1m = sec_rets1m.get(sec, [])
            avg1m  = sum(rets1m) / len(rets1m) if rets1m else 0
            alpha1m = avg1m - kospi_1m   # 1개월 alpha (최근 모멘텀)
            _sector_cache[sec] = {
                'alpha':    alpha,
                'alpha1m':  alpha1m,
                'avg_ret':  avg,
                'kospi_3m': kospi_3m,
            }
        return _sector_cache

    v12_pending_sells: list = []
    v12_pending_buys: list = []

    for day in sim_dates:
        # ── strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약) ──
        if strict_exec:
            _still = []
            for sc, reason in v12_pending_sells:
                if sc not in positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    _still.append((sc, reason)); continue
                px = stock_data[sc]['opens'][im[day]]
                pos = positions.pop(sc)
                _v12_pnl_amt, _v12_pnl_pct = _net_profit(pos['entry_price'], px, pos['qty'], pos.get('mkt_cap_억', 500))
                cash += pos['qty'] * pos['entry_price'] + _v12_pnl_amt
                trades.append({
                    'stock_code':  sc,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   day,
                    'entry_price': pos['entry_price'],
                    'exit_price':  px,
                    'qty':         pos['qty'],
                    'profit_pct':  _v12_pnl_pct,
                    'profit_amt':  _v12_pnl_amt,
                    'exit_reason': reason,
                })
            v12_pending_sells = _still
            for sc in v12_pending_buys:
                if sc in positions or len(positions) >= max_positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                px = stock_data[sc]['opens'][im[day]]
                if px <= 0:
                    continue
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / px)
                if qty < 1 or qty * px > cash:
                    continue
                cash -= qty * px
                positions[sc] = {
                    'entry_date': day, 'entry_price': px,
                    'qty': qty,
                    'peak_price': px, 'hold_days': 0,
                }
            v12_pending_buys = []

        # 매도 체크
        sold_today = []
        # 매도 판단에 섹터 현황 필요 (데이터기반 매도용)
        _hot_sec = _get_hot_sectors(day)
        for sc, pos in list(positions.items()):
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i  = idx_map[day]
            sd = stock_data[sc]
            sec      = sector_map.get(sc, '기타')
            s_info   = _hot_sec.get(sec, {})
            reason = _check_sell_v12(i, sd['prices'], pos, stop_loss, stop_loss_pct, take_profit_pct,
                                     sec_alpha=s_info.get('alpha'), sec_alpha1m=s_info.get('alpha1m'))
            if reason is None:
                continue
            if strict_exec:
                if not pos.get('pending_exit'):
                    pos['pending_exit'] = reason
                    v12_pending_sells.append((sc, reason))
                continue
            curr = sd['prices'][i]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            _v12b_amt, _v12b_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
            cash += pos['qty'] * pos['entry_price'] + _v12b_amt
            trades.append({
                'stock_code':  sc,
                'entry_date':  pos['entry_date'],
                'exit_date':   day,
                'entry_price': pos['entry_price'],
                'exit_price':  curr,
                'qty':         pos['qty'],
                'profit_pct':  _v12b_pct,
                'profit_amt':  _v12b_amt,
                'exit_reason': reason,
            })
            sold_today.append(sc)
        for sc in sold_today:
            del positions[sc]

        # 시장 필터
        if market_bullish and not market_bullish.get(day, True):
            marked = sum(
                stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            equity_curve.append({'date': day, 'equity': round(cash + marked)})
            continue

        # 매수 스캔
        if len(positions) < max_positions:
            hot_sectors = _get_hot_sectors(day)

            for sc, sd in stock_data.items():
                if len(positions) >= max_positions:
                    break
                if sc in positions:
                    continue
                sec = sector_map.get(sc, '기타')
                sec_info = hot_sectors.get(sec)
                if sec_info is None:
                    continue
                # ★ 초기사이클 포착: 3M alpha 4~20% + 1M alpha 양수
                # 기존 'alpha > 15%'는 사이클 정점(이미 오른 뒤) 매수 → 최근 -47.7% 원인
                # 변경: 섹터가 막 상승 시작한 단계(3M alpha 4~20%, 1M 모멘텀 유지)
                sec_alpha   = sec_info['alpha']
                sec_alpha1m = sec_info.get('alpha1m', 0)
                if sec_alpha < 4 or sec_alpha > 20:
                    continue   # 너무 낮거나(미회복) 너무 높으면(정점 위험) 제외
                if sec_alpha1m < 0:
                    continue   # 최근 1달 모멘텀이 꺾이면 진입 금지

                idx_map = date_idx.get(sc, {})
                i = idx_map.get(day)
                if i is None or i < sd['sim_start_i'] or i < 120:
                    continue

                p = sd['prices']
                curr = p[i]

                # 시총 2000억+ (as-of): 신호일 기준 주가×상장주식수
                if asof_mktcap:
                    _sh = _v12_shares_asof(sc, day)
                    if _sh <= 0 or _sh * curr / 1e8 < 2000:
                        continue

                # 개별 종목 RS: 섹터 평균 아웃퍼폼 (KOSPI보다 엄격)
                p63_back = p[i - 63] if i >= 63 else None
                if p63_back and p63_back > 0:
                    stock_3m = (curr - p63_back) / p63_back * 100
                    # 섹터 평균보다 낮거나 KOSPI보다 낮으면 탈락
                    if stock_3m < sec_info['avg_ret'] * 0.7 or stock_3m < sec_info['kospi_3m']:
                        continue

                # 가격 > MA60 > MA120 (추세 정배열)
                p_slice = p[max(0, i - 120): i + 1]
                if len(p_slice) < 120:
                    continue
                ma60  = sum(p_slice[-60:]) / 60
                ma120 = sum(p_slice) / len(p_slice)
                if curr < ma60 or ma60 < ma120 * 0.98:
                    continue

                # 52주 고점 -20% 이내 (V4 기준 적용)
                high52 = max(p[max(0, i - 251): i + 1])
                if curr < high52 * 0.80:
                    continue

                # RSI 50~75 (모멘텀 확인, 과열 제외)
                rsi_val = _rsi(p[max(0, i - 28): i + 1])
                if rsi_val is None or rsi_val < 50 or rsi_val > 75:
                    continue

                # 거래량 증가 확인 (최소 10일 평균 1.2배)
                vols = sd['volumes']
                vol_win = [v for v in vols[max(0, i - 10): i] if v > 0]
                if not vol_win or vols[i] < sum(vol_win) / len(vol_win) * 1.2:
                    continue

                if strict_exec:
                    if sc not in v12_pending_buys and \
                       len(positions) + len(v12_pending_buys) < max_positions:
                        v12_pending_buys.append(sc)
                    continue
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / curr)
                if qty < 1 or qty * curr > cash:
                    continue
                cash -= qty * curr
                positions[sc] = {
                    'entry_date':  day,
                    'entry_price': curr,
                    'qty':         qty,
                    'peak_price':  curr,
                    'hold_days':   0,
                }

        marked = sum(
            stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
            for sc, pos in positions.items() if day in date_idx.get(sc, {})
        )
        equity_curve.append({'date': day, 'equity': round(cash + marked)})

    # 기간 종료 강제 청산
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        idx_map = date_idx.get(sc, {})
        sd = stock_data[sc]
        curr = sd['prices'][idx_map[last_day]] if last_day and last_day in idx_map \
               else (sd['prices'][-1] if sd['prices'] else pos['entry_price'])
        pct = (curr - pos['entry_price']) / pos['entry_price']
        _v12f_amt, _v12f_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
        cash += pos['qty'] * pos['entry_price'] + _v12f_amt
        trades.append({
            'stock_code':  sc,
            'entry_date':  pos['entry_date'],
            'exit_date':   last_day or pos['entry_date'],
            'entry_price': pos['entry_price'],
            'exit_price':  curr,
            'qty':         pos['qty'],
            'profit_pct':  _v12f_pct,
            'profit_amt':  _v12f_amt,
            'exit_reason': '기간종료',
        })

    return trades, equity_curve, cash


def _check_sell_v12(i, prices, pos, stop_loss=-0.08, stop_loss_pct=-0.07,
                    take_profit_pct=0.20, sec_alpha=None, sec_alpha1m=None):
    """V12 매도: 익절 +20%, 손절 -7%, 추적손절 -12%, MA60붕괴.
    ★ 데이터기반 추가: 손실권(-7%+) + 섹터모멘텀 소멸(1M alpha 꺾임) → 조기청산.
    """
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr
    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    if pct >= take_profit_pct:
        return f"익절(+{pct*100:.0f}%)"
    if pct <= stop_loss_pct:
        return f"손절({stop_loss_pct*100:.0f}%)"
    # ★ 데이터기반: 손실권(-7%이하) + 섹터 1M 모멘텀 역전 + MA20 붕괴 → 진입조건 소멸
    if pct < -0.07 and hold_days >= 15 and sec_alpha1m is not None:
        if sec_alpha1m < -2:   # 섹터 1개월 alpha 마이너스 전환 = 사이클 역전
            ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
            if ma20 and curr < ma20:
                return "섹터모멘텀소멸(V12)"
    if hold_days >= 5:
        trail_pct = (curr - peak) / peak if peak > 0 else 0
        if trail_pct <= -0.12 and pct > 0.03:
            return f"추적손절(고점-{abs(trail_pct)*100:.0f}%)"
        ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
        if ma60 is not None and curr < ma60:
            return "MA60 붕괴"
    return None


# ══════════════════════════════════════════════════════════════
#  V10/V11 공통 백테스트 실행 (V4 run_backtest 구조 재활용)
# ══════════════════════════════════════════════════════════════
def run_backtest_v10(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V10', signal_fn=_is_buy_v10,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V10 이익폭발 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=500,     # 500억+ (억원 단위)
        max_new_per_month=999,       # 펀더멘탈 전략 — 월 한도 없음 (자연 필터)
        strategy_key='v10',
        # sell_signal_fn=_sell_signal_v6,  # 피크반납 테스트: avg5 동일(7.2%), 기간별 분산 — 기본값 사용
    )


def run_backtest_v11(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V11', signal_fn=_is_buy_v11,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V7 이익가속YoY {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.10, take_profit=0.35,
        mktcap_min=300,                       # 300억+ (억원 단위)
        max_new_per_month=8,
        use_market_filter=True,               # ★ 하락장 진입 차단
        strategy_key='v11',
        # sell_signal_fn=_sell_signal_v7,  # 가속스톨 테스트: +17.0%→+7.4% 하락 — 기본값 사용
    )


def run_backtest_v10_hs(start_date: str, end_date: str,
                        per_stock: float = 10_000_000,
                        max_positions: int = 10,
                        hs_yoy_min: float = 10.0,
                        run_name: str = None, run_id: str = None) -> str:
    """
    V10 이익폭발 + HS 수출 YoY ≥ 10% 필터.
    HS 데이터가 있는 종목(285개)에서만 매수.
    기존 V10과 비교해 HS 조건이 수익률을 실제로 개선하는지 검증.
    """
    # HS 데이터 있는 종목 목록 로드
    trade_all = _load_trade_signals()
    hs_stocks = set(trade_all.keys())

    def _is_buy_v10_hs(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows,
                       _sc=None, **kw):
        if _sc not in hs_stocks:
            return False
        if not _is_buy_v10(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows):
            return False
        ref_ym = _date_to_ym(dates[i], lag_months=2)
        yoy = _get_export_yoy(trade_all.get(_sc, {}), ref_ym)
        return yoy is not None and yoy >= hs_yoy_min

    return _run_generic_backtest_with_sc(
        version='V10+HS', signal_fn=_is_buy_v10_hs,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V10+HS수출≥{hs_yoy_min:.0f}% {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=500,     # 500억+ (억원 단위)
    )


def run_backtest_v11_hs(start_date: str, end_date: str,
                        per_stock: float = 10_000_000,
                        max_positions: int = 10,
                        hs_yoy_min: float = 10.0,
                        run_name: str = None, run_id: str = None) -> str:
    """
    V11 흑자전환 + HS 수출 YoY ≥ 10% 필터.
    """
    trade_all = _load_trade_signals()
    hs_stocks = set(trade_all.keys())

    def _is_buy_v11_hs(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows,
                       _sc=None, **kw):
        if _sc not in hs_stocks:
            return False
        if not _is_buy_v11(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows):
            return False
        ref_ym = _date_to_ym(dates[i], lag_months=2)
        yoy = _get_export_yoy(trade_all.get(_sc, {}), ref_ym)
        return yoy is not None and yoy >= hs_yoy_min

    return _run_generic_backtest_with_sc(
        version='V11+HS', signal_fn=_is_buy_v11_hs,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V11+HS수출≥{hs_yoy_min:.0f}% {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.10, take_profit=0.30,
        mktcap_min=300,    # 300억+ (억원 단위)
    )


def run_backtest_value(start_date: str, end_date: str,
                       per_stock: float = 10_000_000,
                       max_positions: int = 10,
                       chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    """V1 가치매수 (Graham 내재가치 25%+ 할인 OR PBR<0.7 AND PER<10) — 하락장 무관 매수"""
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V1_VALUE', signal_fn=_is_buy_value,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V1 가치매수 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.12, take_profit=0.25,
        mktcap_min=500,     # 500억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=False,      # 하락장에서도 저평가 종목 매수
        strategy_key='v1_value',
        # sell_signal_fn=_sell_signal_v2,  # 스마트머니이탈 테스트: 효과 없음(5.1%→5.0%), 기본값 사용
    )


def run_backtest_v2(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    chart_confluence: bool = True,  # 2026-07-18 채택: 3요소 컨플루언스 게이트 — 연속운용 158.5→170.3(+11.8pp)·승률 39.3→41.9 실측 개선. 13전략 중 유일하게 개선된 전략(나머지는 악화로 기본 off)

                    run_name: str = None, run_id: str = None) -> str:
    """V2 재무스크리너 (수익성 스코어 ≥ 3점: 영업이익률/ROE/ROA 복합 점수)"""
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V2', signal_fn=_is_buy_v2,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V2 재무스크리너 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.10, take_profit=0.20,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=True,
        strategy_key='v2',
        sell_signal_fn=_sell_signal_v3,  # 재무우량: MA정배열 붕괴 + 수급 이탈 시 매도
    )


def run_backtest_v5(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    """V5 수급 주도 모멘텀 (기관+외국인 5일 동반 순매수 + MA정배열)"""
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V5', signal_fn=_is_buy_v5,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V5 수급모멘텀 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=True,
        strategy_key='v5',
        sell_signal_fn=_sell_signal_v4,  # 수급모멘텀: 동반순매수 해소 + 삼중정배열 붕괴 시 매도
    )


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


def run_backtest_hidden_rev(start_date: str, end_date: str,
                           per_stock: float = 10_000_000,
                           max_positions: int = 10,
                           run_name: str = None, run_id: str = None,
                           exit_mode: str = 'trail20',
                           chart_confluence: bool = False,
                           sell_signal_fn=None) -> str:
    """
    52주 강세 돌파 모멘텀 (Breakout Momentum)
    실증 근거: 2020~2025 982,889 샘플 분석 — 52W 고점 근처 + MA위 + 거래량 조합 1.64x 리프트
    exit_mode: 'trail20' = Trail-20%(고점-20% 추적손절, 실증 최적)
               'tp30'    = TP+30% + Trail-10%(기존 방식)
    """
    if exit_mode == 'trail20':
        # Trail-20%: 고점대비 -20% 추적손절, 고정 TP 없음(99999)
        _tp = 99999.0
        _trail = -0.20
        _name = f"52W돌파Trail20 {start_date[:7]}~{end_date[:7]}"
    else:
        _tp = 0.30
        _trail = -0.10
        _name = f"52W돌파TP30 {start_date[:7]}~{end_date[:7]}"
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='VBR', signal_fn=_is_buy_hidden_rev,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or _name,
        run_id=run_id,
        stop_loss=-0.08,    # 추세 추종 — 빠른 손절
        take_profit=_tp,
        trail_stop=_trail,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=True,   # 추세 전략 — 시장 필터 적용
        strategy_key='vbr',
        sell_signal_fn=sell_signal_fn,
    )


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


def run_backtest_v12(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     asof_mktcap: bool = False,  # 2026-07-17 as-of 재검증: current 대비 악화로 기각 → False 유지 (signal_experiment_ledger: v12/sector_precondition)
                     take_profit_pct: float = 0.25,  # 2026-08-09 파라미터화(텐버거 population 캡처 실험용), 기본값 기존과 동일
                     run_name: str = None, run_id: str = None) -> str:
    """V12는 섹터 계산이 필요하므로 별도 흐름."""
    init_backtest_db()
    run_name = run_name or f"V12 섹터대세 {start_date[:7]}~{end_date[:7]}"
    _v12_params = {"per_stock": per_stock, "max_positions": max_positions,
                   "stop_loss": -0.07, "take_profit_pct": take_profit_pct,
                   "strict_exec": True, "asof_mktcap": asof_mktcap,
                   "start": start_date, "end": end_date}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'v12',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    _record_run_spec(run_id, "v12", "v12_v2_strict_20260714", _v12_params,
                     signal_timing="close_D", execution_timing="next_open",
                     market_cap_mode=("asof_approx" if asof_mktcap else "current"),
                     allocation_rule="fixed_slot",
                     universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current")
    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        trades, equity_curve, final_cash = _run_backtest_v12(
            conn, warmup_start, start_date, end_date, sim_dates,
            per_stock, max_positions,
            stop_loss=-0.07, stop_loss_pct=-0.07, take_profit_pct=take_profit_pct,
            asof_mktcap=asof_mktcap,
        )

        # 종목명 매핑
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for i in range(0, len(codes), 100):
            batch = codes[i:i+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn.execute(f"""
                SELECT DISTINCT ph.stock_code,
                       COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        total_capital = per_stock * max_positions
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

        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"★ V12 섹터대세: KOSPI MA120 필터 / 섹터 alpha>10% / 개별 RS / 익절+25% / 손절-7%\n"
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
        conn.close()
        _register_execution_artifacts(run_id, total_capital, final_cash)
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
        conn.close()
        raise


def _sell_signal_v3(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V3 재무우량 데이터 기반 매도 — 손실권 진입조건 역전 시 조기 탈출
    원칙: 이익권은 trail stop + MA60붕괴(기존 공통로직)로 처리.
    이 함수는 명확한 손실권(-7%이하)에서 진입조건(MA정배열+수급)이 역전되면
    기존 180일 대기 없이 조기 청산하는 역할만 수행.
    """
    prices = sd['prices']
    curr   = prices[i]
    pct    = (curr - pos['entry_price']) / pos['entry_price']
    hold   = pos.get('hold_days', 0)

    # 손실이 작거나 이익권이면 개입 안 함 — trail/MA60붕괴에 맡김
    if pct > -0.07 or hold < 25:
        return None

    ma20 = _ma(prices[max(0, i-19):i+1], 20)
    ma60 = _ma(prices[max(0, i-59):i+1], 60)
    if not ma20 or not ma60:
        return None

    # 손실(-7%이하) + MA정배열 붕괴 + 수급 이탈 삼중 확인
    # → 진입조건(MA20>MA60 + 기관+외인>0)이 완전히 소멸: 회복 가능성 없는 상태
    if ma20 < ma60 * 0.96:
        inst15 = sum(sd['inst'][max(0, i-14):i+1])
        frn15  = sum(sd['frn'][max(0, i-14):i+1])
        if inst15 < 0 and frn15 < 0:
            return "손실+MA붕괴+수급이탈(V3)"

    return None


def _sell_signal_v4(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V4 수급모멘텀 데이터 기반 매도 — 손실권에서 진입조건(수급) 역전 시 조기 탈출
    원칙: 이익권은 trail stop + MA60붕괴(기존 공통로직)로 처리.
    이 함수는 손실권(-7%이하)에서 수급 모멘텀이 완전히 꺼지면
    기존 180일 대기 없이 조기 청산.
    """
    prices = sd['prices']
    curr   = prices[i]
    pct    = (curr - pos['entry_price']) / pos['entry_price']
    hold   = pos.get('hold_days', 0)

    # 손실이 작거나 이익권이면 개입 안 함
    if pct > -0.07 or hold < 20:
        return None

    # 20일 롤링 수급 — 월 단위 확인으로 노이즈 최소화
    inst20 = sum(sd['inst'][max(0, i-19):i+1])
    frn20  = sum(sd['frn'][max(0, i-19):i+1])

    # 손실(-7%이하) + 기관 AND 외인 20일 모두 순매도 → 진입조건 완전 소멸
    if inst20 < 0 and frn20 < 0:
        ma20 = _ma(prices[max(0, i-19):i+1], 20)
        ma60 = _ma(prices[max(0, i-59):i+1], 60)
        if ma20 and ma60 and ma20 < ma60 * 0.97:
            return "손실+수급소멸(V4)"

    return None


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


def _sell_signal_v2(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V2 가치매수 — 스마트머니 이탈: 매수 조건(기관&외인 AND 양수) 역전 시 청산
    기존 방식(손실권 -8% + MA120 + MA60): 세 조건 동시 충족이 거의 없어 사실상 미작동.
    새 접근: 기관10일+외인10일 모두 음수 → 매수 진입 근거(스마트머니 동반 매수)가 소멸.
    이익/손실 무관 발동. 20일 이상 보유 후 (수급 노이즈 구간 제외).
    """
    held = pos.get('hold_days', 0)
    if held < 20:
        return None
    inst10 = sum(sd['inst'][max(0, i-9):i+1])
    frn10  = sum(sd['frn'][max(0, i-9):i+1])
    # 매수 조건(기관>0 AND 외인>0)의 역전 → 기관&외인 모두 이탈
    if inst10 < 0 and frn10 < 0:
        return "스마트머니이탈(V2)"
    return None


def _sell_signal_v6(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V6 이익폭발 — 피크 반납: 이익 스토리 소진 신호(피크 게인 40%+ 반납)
    기존 방식(손실권 -6% + RSI<30): 이익폭발 종목은 손실권에 잘 안 들어와 사실상 미작동.
    새 접근: 25%+ 피크 게인을 달성한 후 그 게인의 40%를 반납 → 이익 스토리 소진.
    trail stop(-10%)보다 먼저 발동해 이익권에서 조기 청산.
    주: peak_gain=50%일 때 trail=-10% 발동(curr=peak×0.9), giveback=40% 발동(curr=peak×0.6+entry×0.4)
    """
    held  = pos.get('hold_days', 0)
    if held < 25:
        return None
    entry = pos['entry_price']
    curr  = sd['prices'][i]
    peak  = pos.get('peak_price', entry)

    peak_gain = (peak - entry) / entry
    if peak_gain < 0.25:   # 25% 이상 피크 달성 전이면 아직 이익폭발 미확인
        return None

    curr_gain = (curr - entry) / entry
    giveback  = (peak_gain - curr_gain) / peak_gain if peak_gain > 0 else 0
    if giveback > 0.40:
        return f"피크반납{giveback*100:.0f}%(V6)"
    return None


def _sell_signal_v7(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V7 이익가속 — 가속 스톨: 45일 후 신고점 없고 MA20 붕괴 시 가속 소진 청산
    기존 방식(손실권 -7% + MA+수급): 이익가속 종목은 손실권 진입 드물어 사실상 미작동.
    새 접근: 45일 보유 → 최근 25일 최고가가 진입가+8% 이내(신고가 갱신 없음) + MA20 하향.
    이익가속 종목은 매수 후 빠르게 상승해야 함 — 45일 후도 8% 이하면 가속 스토리 소진.
    """
    held  = pos.get('hold_days', 0)
    if held < 45:
        return None
    prices = sd['prices']
    entry  = pos['entry_price']
    curr   = prices[i]

    # 최근 25일 최고가가 진입가+8% 이내 → 가속 진행 중 신고가 부재
    recent_high = max(prices[max(0, i-24):i+1])
    if recent_high < entry * 1.08:
        ma20 = _ma(prices[max(0, i-19):i+1], 20)
        if ma20 and curr < ma20:
            return "가속스톨(V7)"
    return None


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
                           chart_confluence: bool = False) -> str:  # 2026-07-18: 일봉+주봉+캔들 컨플루언스(2/3 합의) 진입게이트+고점청산 (공통 모듈)
    """V10/V11 공통 백테스트 실행기 (V4 run_backtest 구조 재활용).
    use_market_filter=False: V11 흑자전환처럼 하락장에서도 매수해야 하는 전략에 사용.
    strategy_key: DB에 저장할 전략 키 (v10, v11, v_trend 등). None이면 'combo' 기본값.
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
            WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 4)
               OR (f.is_annual=1)
            ORDER BY f.stock_code, f.year, f.quarter
        """).fetchall():
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

HS_DB_PATH  = "/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db"
EMP_DB_PATH = "/Applications/stock_dashboard/employment_monitor/employment.db"


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


def _load_employment_signals() -> Dict[str, Dict[str, int]]:
    """
    고용보험 DB에서 종목별 연도별 고용인원 로드.
    반환: {stock_code: {ym: worker_count}} (예: {'000660': {'2024-12': 21493, ...}})
    ym 형식: 'YYYY-MM' (대부분 12월 = 연말 기준)
    """
    try:
        conn = sqlite3.connect(EMP_DB_PATH, timeout=10)
        rows = conn.execute("""
            SELECT stock_code, ym, worker_count
            FROM employment_company
            WHERE worker_count IS NOT NULL AND worker_count > 0
            ORDER BY stock_code, ym
        """).fetchall()
        conn.close()
        result: Dict[str, Dict[str, int]] = {}
        for sc, ym, cnt in rows:
            result.setdefault(sc, {})[ym] = int(cnt)
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


def _run_backtest_v8(conn, warmup_start, start_date, end_date, sim_dates,
                     per_stock, max_positions,
                     stop_loss_pct, take_profit_pct, max_hold_days,
                     strict_exec: bool = True):
    """
    V8 선행지표 멀티팩터 포트폴리오 시뮬레이터.

    매수 조건 (선행지표 기반 — '싸게 사서 비싸게 판다'):
      [A] 수출 YoY 가속: 최근 2개월 수출 YoY 모두 > 10%
                         + 최근 3개월 YoY 평균 > 직전 3개월 YoY 평균 (가속 확인)
      [B] 가격 선진입:   현재가 < MA60 × 1.15 (강한 상승 추세 진입 전, 바닥권~초기)
                         OR 52주 고점 대비 -30% 이상 하락
      [C] 재무 건전:     최근 공시 영업이익 > 0 (적자 기업 제외)
      [D] 고평가 제외:   BPS > 0 이면 PBR < 4.0
      [E] 시장 필터:     KOSPI > MA120 (하락장 매수 금지)
      [F] 고용 보조:     연간 고용 YoY > 3% (데이터 있을 때만 추가 점수)

    매도 조건:
      ① 익절: +take_profit_pct
      ② 손절: -stop_loss_pct
      ③ 추적 손절: 고점 대비 -15% (수익 구간에서만)
      ④ MA60 붕괴 (최소 5일 보유 후)
      ⑤ 최대 보유 max_hold_days 초과 시 강제 청산 (선행지표 미실현 대비)
    """
    # ── 수출 + 고용 데이터 로드 ────────────────────────────────
    trade_all = _load_trade_signals()    # {sc: {ym: export_val}}
    emp_all   = _load_employment_signals()  # {sc: {ym: worker_cnt}}

    # ── KOSPI 시장 필터 ────────────────────────────────────────
    kospi_rows = conn.execute("""
        SELECT date, close FROM price_history
        WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
        ORDER BY date ASC
    """, (warmup_start, end_date)).fetchall()
    k_dates      = [r[0] for r in kospi_rows]
    k_price_list = [float(r[1]) for r in kospi_rows]
    market_bullish: Dict[str, bool] = {}
    for ki, kd in enumerate(k_dates):
        if kd < start_date:
            continue
        kma120 = _ma(k_price_list[max(0, ki - 119): ki + 1], 120)
        market_bullish[kd] = (kma120 is None) or (k_price_list[ki] > kma120)

    # ── 재무 데이터 로드 ────────────────────────────────────────
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
        WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 4)
           OR (f.is_annual=1)
        ORDER BY f.stock_code, f.year, f.quarter
    """).fetchall():
        sc = r[0]
        fin_all.setdefault(sc, []).append(r[1:])

    # ── 종목 목록: 수출 데이터 있는 종목만 (trade_all 키셋)
    #    + price_history에서 충분한 데이터 보유 확인 ─────────────
    export_stocks = set(trade_all.keys())
    stock_codes = [r[0] for r in conn.execute("""
        SELECT ph.stock_code, COUNT(*) AS cnt
        FROM price_history ph
        INNER JOIN (
            SELECT stock_code FROM stock_universe
            WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        ) su ON ph.stock_code = su.stock_code
        WHERE ph.date>=? AND ph.date<=? AND ph.close>0
        GROUP BY ph.stock_code HAVING COUNT(*) >= 200
    """, (warmup_start, end_date)).fetchall()
    if r[0] in export_stocks]

    # ── 종목별 가격 데이터 로드 ────────────────────────────────
    stock_data: Dict[str, dict] = {}
    for sc in stock_codes:
        rows = conn.execute("""
            SELECT date, close, COALESCE(volume,0),
                   COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0),
                   COALESCE(open, close)
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
        opens   = [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows]

        # ── 데이터 품질 필터: 단일일 50% 이상 급등락 종목 제외
        # (두 시리즈 혼재, 권리락 미반영 등 데이터 오염 방지)
        bad_data = False
        for pi in range(1, len(prices)):
            if prices[pi - 1] > 0:
                ratio = prices[pi] / prices[pi - 1]
                if ratio > 3.0 or ratio < 0.20:   # 하루 3배 이상 또는 80% 이상 하락
                    bad_data = True
                    break
        if bad_data:
            continue

        sim_start_i = next((idx for idx, dt in enumerate(dates) if dt >= start_date), len(dates))
        stock_data[sc] = {
            'dates': dates, 'prices': prices, 'volumes': volumes, 'opens': opens,
            'frn': frn, 'inst': inst, 'fins': fin_all.get(sc, []),
            'sim_start_i': sim_start_i,
            'trade': trade_all.get(sc, {}),   # 이 종목의 월별 수출액
            'emp':   emp_all.get(sc, {}),     # 이 종목의 연별 고용인원
        }

    date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                for sc, d in stock_data.items()}

    total_capital = per_stock * max_positions
    # 2026-07-16 개선: 고정슬롯 P&L 누산 → 실제 현금원장. 매수 시 가용현금 검사+차감,
    # 부족 시 주문 거부(cash 음수 금지). equity_curve도 cash+마킹포지션 기준으로 정정.
    cash = total_capital
    positions: Dict[str, dict] = {}
    trades:    list = []
    equity_curve: list = []

    def _check_sell_v8(i, prices, pos, trade_sc=None, d=None):
        """
        V8 매도: 선행지표 기반 보유 전략
        - 수출 YoY 이 여전히 양호하면 MA60 붕괴에도 보유 (선행지표 우선)
        - 수출이 꺾이거나 손절선 도달 시 청산
        """
        curr = prices[i]
        if curr > pos.get('peak_price', pos['entry_price']):
            pos['peak_price'] = curr
        pct       = (curr - pos['entry_price']) / pos['entry_price']
        peak      = pos.get('peak_price', pos['entry_price'])
        hold_days = pos.get('hold_days', 0)
        pos['hold_days'] = hold_days + 1

        # 익절 (항상 우선)
        if pct >= take_profit_pct:
            return f"익절(+{pct*100:.0f}%)"

        # 하드 손절 (항상 우선)
        if pct <= -stop_loss_pct:
            return f"손절(-{stop_loss_pct*100:.0f}%)"

        # 추적 손절: 고점 대비 -15% (수익 구간, 5일 이상 보유)
        if hold_days >= 5 and pct > 0.05:
            trail = (curr - peak) / peak if peak > 0 else 0
            if trail <= -0.15:
                return f"추적손절(고점-{abs(trail)*100:.0f}%)"

        # 수출 선행지표 소멸 체크 — 진입조건 역전 시 청산
        # 진입조건: yoy1 > 2%(양전환). 소멸: 최근 달 음전환 OR 이전 달도 부진
        if hold_days >= 25 and trade_sc is not None and d is not None:
            ref_ym1 = _date_to_ym(d, lag_months=2)
            ref_ym2 = _date_to_ym(d, lag_months=3)
            yoy1 = _get_export_yoy(trade_sc, ref_ym1)
            yoy2 = _get_export_yoy(trade_sc, ref_ym2)
            if yoy1 is not None:
                # 케이스 A: 최근 달 명확히 음전환 → 변곡점 소멸
                if yoy1 < -3:
                    return "수출역전청산(V9)"
                # 케이스 B: 최근 달 보합이고 이전 달도 음전환 → 전환 실패
                if yoy1 < 2 and yoy2 is not None and yoy2 < 0:
                    return "수출전환실패청산(V9)"

        # 장기횡보 안전망 (시간기반 대신 — 수익 없이 오래 끌면 기회비용)
        if hold_days >= 240 and -0.05 < pct < 0.15:
            ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
            if ma20 and curr < ma20:
                return f"장기횡보청산(V9,{hold_days}일)"

        # MA200 붕괴 (장기 하락 구조 편입, 30일 이상 보유)
        if hold_days >= 30:
            ma200 = _ma(prices[max(0, i - 199): i + 1], 200)
            if ma200 is not None and curr < ma200 * 0.92:
                return "MA200 붕괴"

        return None

    def _is_buy_v8_signal(sc, sd, i, d):
        """
        V8 매수 시그널 v2 — '수출 선행 + 일시 눌림목 매수'

        철학: 수출이 증가하는 기업이 단기 조정 구간에 있을 때 매수.
              → 장기 추세(MA200)는 살아 있으나 단기적으로 MA60 이하 또는 근접
              → 수출 데이터(선행 2개월)가 YoY 회복/성장 구간
              → RSI 35~60 (과매도 후 회복 초기)
        """
        if i < sd['sim_start_i']:
            return False
        curr   = sd['prices'][i]
        trade  = sd['trade']
        emp    = sd['emp']
        fins   = sd['fins']

        # ══ [A] 수출 YoY 진짜 변곡점 포착 — 음수→양수 전환 ══════
        # 철학: "수출 선행"의 진짜 선행 = 아직 주가에 반영 안 된 전환 순간
        #   기존 문제: yoy1>8% AND yoy2>8% = "확인된 성장" → 주가 이미 반영 후 진입
        #   수정: 최근 수출 양성 전환 + 이전엔 부진(진짜 변곡) + 주가 미반영(MA60 근처)
        ref_ym1 = _date_to_ym(d, lag_months=2)   # 가장 최근 공표된 달
        ref_ym2 = _date_to_ym(d, lag_months=3)   # 그 전달
        ref_ym3 = _date_to_ym(d, lag_months=4)   # 3개월 전
        ref_ym4 = _date_to_ym(d, lag_months=5)   # 4개월 전
        ref_ym5 = _date_to_ym(d, lag_months=6)   # 5개월 전

        yoy1 = _get_export_yoy(trade, ref_ym1)
        if yoy1 is None:
            return False

        # [A-1] 최근 수출 YoY 양성(최소 +2%) — 음수면 진입 불가
        if yoy1 < 2:
            return False

        # [A-2] 진짜 변곡점 확인: 이전 달들 중 부진(≤2%) 또는 음수가 있어야 함
        #   케이스 A(역전): 이전 3~4개월 중 0% 이하 있고 최근 전환 → 진짜 선행 매수
        #   케이스 B(급가속): 이전 대비 15%p 이상 급개선 → 예상 못 한 서프라이즈
        #   케이스 C(지속 성장) 제거 — 이미 주가에 반영된 상태 → 사후 추종
        yoy2 = _get_export_yoy(trade, ref_ym2)
        yoy3 = _get_export_yoy(trade, ref_ym3)
        yoy4 = _get_export_yoy(trade, ref_ym4)
        yoy5 = _get_export_yoy(trade, ref_ym5)
        older = [y for y in [yoy2, yoy3, yoy4] if y is not None]
        if not older:
            return False
        avg_older   = sum(older) / len(older)
        had_negative = any(y <= 2 for y in older)   # 이전에 부진/음수 있었는가
        accelerated  = yoy1 >= avg_older + 15        # 갑작스러운 급개선
        if not had_negative and not accelerated:
            return False   # 케이스 C(지속 성장) 진입 차단 ← 기존 부진 원인

        # ══ [B] 가격 구조: 장기 추세 안에서 단기 눌림 ══════════
        # 철학: 수출이 이미 성장 중이고, 주가가 이제 막 MA60을 돌파하는 순간 매수
        #       → 너무 이른 진입(MA60 아래) X, 너무 늦은 진입(Minervini 완성) X
        p_all = sd['prices'][max(0, i - 250): i + 1]
        if len(p_all) < 120:
            return False
        ma60  = sum(p_all[-60:]) / 60
        ma120 = sum(p_all[-120:]) / 120
        ma200 = sum(p_all) / len(p_all) if len(p_all) >= 200 else None

        # [B-1] 현재가 MA60 위에 있어야 함 (실증: MA60아래=0.87x 불리)
        # 이전: MA60 근처 또는 아래 허용 → 실증에서 MA아래는 음의 신호
        # 수정: MA60 위 + 너무 과열 아닌 수준 (MA60의 130% 이하)
        if curr < ma60:
            return False  # MA60 아래 → 실증적으로 불리
        if curr > ma60 * 1.20:
            return False  # 이미 주가 반영 구간(MA60+20% 초과) — 선행성 없음

        # [B-2] MA60이 10일 전보다 높거나 같다 (MA60 방향 전환 또는 상승 중)
        if i >= 10:
            ma60_10ago = sum(sd['prices'][max(0, i - 69): i - 9]) / 60 \
                         if (i - 9) >= 60 else None
            if ma60_10ago is not None and ma60 < ma60_10ago * 0.99:
                return False  # MA60 여전히 하락 중

        # [B-3] 장기 하락 구조 제외: MA120 > MA200 * 0.92 (장기 상승 구조)
        if ma200 is not None and ma120 < ma200 * 0.92:
            return False

        # [B-4] 52주 고점 대비 -35% 이상 하락이면 너무 큰 낙폭 → 제외
        high52 = max(sd['prices'][max(0, i - 251): i + 1])
        if high52 > 0 and curr < high52 * 0.65:
            return False

        # ══ [C] RSI 42~80 (실증: RSI>70=1.25x 양호, 상한 완화) ═════
        rsi_val = _rsi(p_all[-29:] if len(p_all) >= 29 else p_all)
        if rsi_val is None or rsi_val < 42 or rsi_val > 80:
            return False

        # ══ [D] 재무 건전: 영업이익 > 0 ═════════════════════
        fin = _get_financial_as_of(fins, d)
        if fin is not None:
            _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann, *_ = fin
            if op is not None and op <= 0:
                return False  # 영업적자 제외
            if bps and bps > 0 and curr / bps > 6.0:
                return False  # 극단적 고평가(PBR>6) 제외

        # ══ [E] 고용 보조: 연간 고용 감소(-5% 이상) 기업 제외 ══
        if emp:
            emp_sorted = sorted(
                [(ym, cnt) for ym, cnt in emp.items() if ym <= d[:7]],
                reverse=True
            )
            if len(emp_sorted) >= 2:
                (y1, cnt1), (_, cnt2) = emp_sorted[0], emp_sorted[1]
                if cnt2 > 0 and (cnt1 - cnt2) / cnt2 * 100 < -5.0:
                    return False

        return True

    v8_pending_sells: list = []  # (sc, reason)
    v8_pending_buys: list = []   # sc

    for day in sim_dates:
        # ── strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약) ──
        if strict_exec:
            _still = []
            for sc, reason in v8_pending_sells:
                if sc not in positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    _still.append((sc, reason)); continue
                i = im[day]
                px = stock_data[sc]['opens'][i]
                pos = positions.pop(sc)
                _pnl_amt, _net_pct = _net_profit(pos['entry_price'], px, pos['qty'], pos.get('mkt_cap_억', 500))
                cash += pos['qty'] * pos['entry_price'] + _pnl_amt
                trades.append({
                    'stock_code':  sc,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   day,
                    'entry_price': pos['entry_price'],
                    'exit_price':  px,
                    'qty':         pos['qty'],
                    'profit_pct':  _net_pct,
                    'profit_amt':  _pnl_amt,
                    'exit_reason': reason,
                })
            v8_pending_sells = _still
            for sc in v8_pending_buys:
                if sc in positions or len(positions) >= max_positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i = im[day]
                px = stock_data[sc]['opens'][i]
                if px <= 0:
                    continue
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / px)
                if qty < 1 or qty * px > cash:
                    continue  # 현금 부족 → 주문 거부
                cash -= qty * px
                positions[sc] = {
                    'entry_date': day, 'entry_price': px,
                    'qty': qty,
                    'peak_price': px, 'hold_days': 0,
                }
            v8_pending_buys = []

        # 매도 체크
        sold = []
        for sc, pos in list(positions.items()):
            im = date_idx.get(sc, {})
            if day not in im:
                continue
            i      = im[day]
            sd     = stock_data[sc]
            reason = _check_sell_v8(i, sd['prices'], pos, trade_sc=sd['trade'], d=day)
            if reason is None:
                continue
            if strict_exec:
                if not pos.get('pending_exit'):
                    pos['pending_exit'] = reason
                    v8_pending_sells.append((sc, reason))
                continue
            curr = sd['prices'][i]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            _pnl_amt, _net_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
            cash += pos['qty'] * pos['entry_price'] + _pnl_amt
            trades.append({
                'stock_code':  sc,
                'entry_date':  pos['entry_date'],
                'exit_date':   day,
                'entry_price': pos['entry_price'],
                'exit_price':  curr,
                'qty':         pos['qty'],
                'profit_pct':  _net_pct,
                'profit_amt':  _pnl_amt,
                'exit_reason': reason,
            })
            sold.append(sc)
        for sc in sold:
            del positions[sc]

        # 시장 필터 (하락장 매수 금지)
        if market_bullish and not market_bullish.get(day, True):
            marked = sum(
                stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            equity_curve.append({'date': day, 'equity': round(cash + marked)})
            continue

        # 매수 스캔
        if len(positions) < max_positions:
            for sc, sd in stock_data.items():
                if len(positions) >= max_positions:
                    break
                if sc in positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i = im[day]
                if not _is_buy_v8_signal(sc, sd, i, day):
                    continue
                if strict_exec:
                    if sc not in v8_pending_buys and \
                       len(positions) + len(v8_pending_buys) < max_positions:
                        v8_pending_buys.append(sc)
                    continue
                curr = sd['prices'][i]
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / curr)
                if qty < 1 or qty * curr > cash:
                    continue
                cash -= qty * curr
                positions[sc] = {
                    'entry_date':  day,
                    'entry_price': curr,
                    'qty':         qty,
                    'peak_price':  curr,
                    'hold_days':   0,
                }

        # 에쿼티 커브 (현금원장 기준)
        marked = sum(
            stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
            for sc, pos in positions.items() if day in date_idx.get(sc, {})
        )
        equity_curve.append({'date': day, 'equity': round(cash + marked)})

    # 기간 종료 강제 청산
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        sd  = stock_data[sc]
        im  = date_idx.get(sc, {})
        curr = sd['prices'][im[last_day]] if last_day and last_day in im else sd['prices'][-1]
        pct  = (curr - pos['entry_price']) / pos['entry_price']
        _pnl_amt, _net_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
        cash += pos['qty'] * pos['entry_price'] + _pnl_amt
        trades.append({
            'stock_code':  sc,
            'entry_date':  pos['entry_date'],
            'exit_date':   last_day or pos['entry_date'],
            'entry_price': pos['entry_price'],
            'exit_price':  curr,
            'qty':         pos['qty'],
            'profit_pct':  _net_pct,
            'profit_amt':  _pnl_amt,
            'exit_reason': '기간종료',
        })

    return trades, equity_curve, len(stock_data), market_bullish, cash


def run_backtest_v8(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    run_name: str = None, run_id: str = None) -> str:
    """
    V8 수출 선행지표 멀티팩터 백테스트.
    HS 무역통계(월별 수출 YoY) + 고용 데이터를 선행 신호로 활용.
    """
    init_backtest_db()
    run_name = run_name or f"V8 수출선행 {start_date[:7]}~{end_date[:7]}"
    _v8_params = {"per_stock": per_stock, "max_positions": max_positions,
                  "stop_loss_pct": 0.10, "take_profit_pct": 0.30, "max_hold_days": 252,
                  "strict_exec": True, "start": start_date, "end": end_date}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'v8',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    # 2026-07-27: 코드 재확인 결과 v8은 애초에 시총 기반 유니버스 필터를 전혀
    # 쓰지 않음(종목선정은 수출데이터 보유여부(trade_all 키셋)만 기준, mkt_cap_억은
    # _net_profit 슬리피지 계산에만 쓰이고 항상 기본값 500 — positions에 실제 시총이
    # 저장된 적이 없음). "current"라는 라벨은 실제로 존재하지 않는 현재시총 룩어헤드를
    # 있는 것처럼 오기술한 것이므로 megatrend/earnings_conviction과 동일하게
    # "not_applicable"로 정정(as-of 리트로핏 대상 아님 — 고칠 시총필터 자체가 없음).
    _record_run_spec(run_id, "v8", "v8_v2_strict_20260714", _v8_params,
                     signal_timing="close_D", execution_timing="next_open",
                     market_cap_mode="not_applicable", allocation_rule="fixed_slot")
    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        if not sim_dates:
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]

        trades, equity_curve, n_stocks, market_bullish, final_cash = _run_backtest_v8(
            conn, warmup_start, start_date, end_date, sim_dates,
            per_stock, max_positions,
            stop_loss_pct=0.10,      # 선행 매수 → 넓은 손절 허용
            take_profit_pct=0.30,    # 선행 매수 → 충분한 상승 기다림
            max_hold_days=252,       # 최대 1년 보유 (선행지표 실현 대기)
        )

        # 종목명 매핑
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for idx in range(0, len(codes), 100):
            batch = codes[idx:idx+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn.execute(f"""
                SELECT DISTINCT ph.stock_code, COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        total_capital = per_stock * max_positions
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        from collections import defaultdict
        monthly = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        exit_reasons: Dict[str, int] = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        bear_days  = sum(1 for v in market_bullish.values() if not v)
        total_days = len(market_bullish)
        filter_pct = round(bear_days / total_days * 100, 1) if total_days else 0

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  수출데이터 종목수: {n_stocks}\n"
            f"★ V8 수출선행: HS무역통계 월별수출 YoY>10%(2개월연속) + 가격선진입(MA60근처) + 영업이익>0\n"
            f"  매개변수: 익절+30% / 손절-10% / 추적손절-15% / 최대보유252일 / KOSPI MA120 필터\n"
            f"하락장 차단: {bear_days}/{total_days}일({filter_pct}%)\n"
            f"총 거래: {metrics['total_trades']}건  승률: {metrics['win_rate']}%  "
            f"CAGR: {metrics['cagr']}%  MDD: {metrics['max_drawdown_pct']}%  샤프: {metrics['sharpe']}\n"
            f"손익비: {metrics['pl_ratio']}배  총손익: {metrics.get('total_profit_amt',0):,}원\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )
        result = {
            **metrics,
            'strategy': 'v8',
            'monthly':      [{'month': k, 'profit': v} for k, v in sorted(monthly.items())],
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)}
                             for k, v in sorted(per_name.items(), key=lambda x: -x[1])[:5]],
            'top_losers':   [{'name': k, 'profit': int(v)}
                             for k, v in sorted(per_name.items(), key=lambda x:  x[1])[:5]],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True),
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        _register_execution_artifacts(run_id, total_capital, final_cash)
        return run_id

    except Exception as e:
        import traceback
        err = f"{e}\n{traceback.format_exc()}"
        try:
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


# ══════════════════════════════════════════════════════════════
#  레짐 적응형 전략: BULL→V1 MA추세, BEAR→V7 흑자전환 자동 전환
# ══════════════════════════════════════════════════════════════
def run_backtest_regime_adaptive(start_date: str, end_date: str,
                                  per_stock: float = 10_000_000,
                                  max_positions: int = 10,
                                  strict_exec: bool = True,
                                  run_name: str = None,
                                  run_id: str = None) -> str:
    """
    레짐 적응형 전략 (Meta-V):
      - BULL (KOSPI > MA120): V1 MA추세 신호 → 추세추종
      - BEAR (KOSPI < MA120): V7 흑자전환 신호 → 구조적 전환주 (하락장에서도 매수)
    전략 전환은 매 거래일 자동으로 이뤄짐.
    """
    init_backtest_db()
    _strat_key = 'regime_adaptive'
    _ra_params = {"per_stock": per_stock, "max_positions": max_positions, "strict_exec": strict_exec,
                  "start": start_date, "end": end_date}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status,strategy)
            VALUES (?,?,?,?,?,?,'running',?)
        """, (run_id, run_name or f"레짐 적응형 {start_date[:7]}~{end_date[:7]}",
              start_date, end_date, per_stock, max_positions, _strat_key))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running', strategy=? WHERE run_id=?",
                     (_strat_key, run_id))
        conn.commit()

    # 2026-07-27: 코드 재확인 결과 이 엔진의 유니버스 쿼리(전 종목, market_cap 조건
    # 없음)와 실제 신호함수 _is_buy_v1/_is_buy_v11 둘 다 시총 파라미터를 받지도,
    # stock_universe.market_cap을 참조하지도 않음 — "V1 스스로 1000억+ 확인" 주석은
    # run_backtest_v1(독립엔진)의 자체 유니버스 사전필터와 혼동된 stale 주석이었음.
    # 즉 애초에 고칠 시총 필터 자체가 없어 "current" 라벨이 부정확했으므로
    # v8과 동일하게 "not_applicable"로 정정(as-of 리트로핏 대상 아님).
    _record_run_spec(
        run_id, "regime_adaptive", "regime_v2_strict_20260716", _ra_params,
        signal_timing="close_D", execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
    )

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        # 재무 데이터 로드
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
            WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 4)
               OR (f.is_annual=1)
            ORDER BY f.stock_code, f.year, f.quarter
        """).fetchall():
            fin_all.setdefault(r[0], []).append(r[1:])

        # 전체 종목 (6자리 숫자 코드, 충분한 데이터 보유)
        # 2026-07-27 정정: 시총 필터는 여기도 _is_buy_v1/_is_buy_v11 내부에도 없음
        # (과거 주석이 잘못 기술 — 아래 참조).
        stock_codes = [r[0] for r in conn.execute("""
            SELECT stock_code, COUNT(*) AS cnt FROM price_history
            WHERE date>=? AND date<=? AND close>0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code HAVING COUNT(*) >= 200
        """, (warmup_start, end_date)).fetchall()]

        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            try:
                rows = conn.execute("""
                    SELECT date, close, COALESCE(volume,0),
                           COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0),
                           COALESCE(open,0)
                    FROM price_history
                    WHERE stock_code=? AND date>=? AND date<=? AND close>0
                    ORDER BY date ASC
                """, (sc, warmup_start, end_date)).fetchall()
                if len(rows) < 200:
                    continue
                dates_  = [r[0] for r in rows]
                prices_ = [float(r[1]) for r in rows]
                vols_   = [float(r[2]) for r in rows]
                frn_    = [float(r[3]) for r in rows]
                inst_   = [float(r[4]) for r in rows]
                opens_  = [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows]
                sim_i   = next((i for i, d in enumerate(dates_) if d >= start_date), len(dates_))
                stock_data[sc] = {
                    'dates': dates_, 'prices': prices_, 'volumes': vols_, 'opens': opens_,
                    'frn': frn_, 'inst': inst_, 'fins': fin_all.get(sc, []),
                    'sim_start_i': sim_i,
                }
            except Exception:
                continue

        # KOSPI 레짐: True=BULL, False=BEAR
        # 히스테리시스 버퍼: BULL→BEAR는 MA120 * 0.97 이하, BEAR→BULL은 MA120 * 1.03 이상
        # → 횡보 구간 잦은 전환 방지
        market_bullish: Dict[str, bool] = {}
        try:
            krows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r[0] for r in krows]
            k_prices = [float(r[1]) for r in krows]
            cur_regime = True  # 초기 상태: BULL
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    # 워밍업 기간: 레짐 초기화
                    kma = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                    if kma is not None:
                        cur_regime = k_prices[ki] > kma
                    continue
                kma = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                if kma is not None:
                    if cur_regime and k_prices[ki] < kma * 0.97:
                        cur_regime = False   # BULL→BEAR: MA120 3% 아래 돌파 시
                    elif not cur_regime and k_prices[ki] > kma * 1.03:
                        cur_regime = True    # BEAR→BULL: MA120 3% 위 돌파 시
                market_bullish[kd] = cur_regime
        except Exception:
            pass

        conn.close()

        def _check_sell_adaptive(i, prices, pos):
            curr = prices[i]
            peak = pos.get('peak_price', pos['entry_price'])
            if curr > peak:
                pos['peak_price'] = curr
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            hold = pos.get('hold_days', 0)
            pos['hold_days'] = hold + 1
            stop = pos.get('stop_loss', -0.10)
            take = pos.get('take_profit',  0.25)
            if pct >= take:
                return f"익절(+{pct*100:.0f}%)"
            if pct <= stop:
                return f"손절({stop*100:.0f}%)"
            if hold >= 5:
                trail = (curr - peak) / peak if peak > 0 else 0
                if trail <= -0.12 and pct > 0.03:
                    return f"추적손절(고점-{abs(trail)*100:.0f}%)"
                ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
                if ma60 is not None and curr < ma60:
                    return "MA60 붕괴"
            return None

        total_capital = per_stock * max_positions
        cash = total_capital  # 2026-07-16: 현금원장 전환
        date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])} for sc, d in stock_data.items()}
        positions: Dict[str, dict] = {}
        ra_pending_sells: list = []
        ra_pending_buys: list = []
        trades:    list = []
        equity_curve: list = []
        monthly_buys: Dict[str, int] = {}
        regime_switches: list = []   # 레짐 전환 기록
        prev_regime = None

        for day in sim_dates:
            is_bull = market_bullish.get(day, True)
            cur_regime = 'BULL' if is_bull else 'BEAR'
            if cur_regime != prev_regime:
                regime_switches.append({'date': day, 'to': cur_regime})
                prev_regime = cur_regime

            # strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약)
            if strict_exec:
                _still = []
                for sc, reason in ra_pending_sells:
                    if sc not in positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        _still.append((sc, reason)); continue
                    px = stock_data[sc]['opens'][im[day]]
                    pos = positions.pop(sc)
                    _ra_amt, _ra_pct = _net_profit(pos['entry_price'], px, pos['qty'], pos.get('mkt_cap_억', 500))
                    cash += pos['qty'] * pos['entry_price'] + _ra_amt
                    trades.append({
                        'stock_code': sc, 'entry_date': pos['entry_date'], 'exit_date': day,
                        'entry_price': pos['entry_price'], 'exit_price': px, 'qty': pos['qty'],
                        'profit_pct': _ra_pct, 'profit_amt': _ra_amt, 'exit_reason': reason,
                        'entry_regime': pos.get('regime', '?'),
                    })
                ra_pending_sells = _still
                for sc, regime_at_signal, stop_val, take_val in ra_pending_buys:
                    if sc in positions or len(positions) >= max_positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    px = stock_data[sc]['opens'][im[day]]
                    if px <= 0:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    qty = int(budget / px)
                    if qty < 1 or qty * px > cash:
                        continue
                    cash -= qty * px
                    positions[sc] = {
                        'entry_date': day, 'entry_price': px, 'qty': qty, 'peak_price': px,
                        'hold_days': 0, 'stop_loss': stop_val, 'take_profit': take_val,
                        'regime': regime_at_signal,
                    }
                ra_pending_buys = []

            # 매도
            sold = []
            for sc, pos in list(positions.items()):
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                reason = _check_sell_adaptive(im[day], stock_data[sc]['prices'], pos)
                if reason is None:
                    continue
                if strict_exec:
                    if not pos.get('pending_exit'):
                        pos['pending_exit'] = reason
                        ra_pending_sells.append((sc, reason))
                    continue
                i_ex  = im[day]
                curr  = stock_data[sc]['prices'][i_ex]
                pct   = (curr - pos['entry_price']) / pos['entry_price']
                _ra2_amt, _ra2_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
                cash += pos['qty'] * pos['entry_price'] + _ra2_amt
                trades.append({
                    'stock_code':  sc,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   day,
                    'entry_price': pos['entry_price'],
                    'exit_price':  curr,
                    'qty':         pos['qty'],
                    'profit_pct':  _ra2_pct,
                    'profit_amt':  _ra2_amt,
                    'exit_reason': reason,
                    'entry_regime': pos.get('regime', '?'),
                })
                sold.append(sc)
            for sc in sold:
                del positions[sc]

            # 매수 (레짐별 신호 함수 전환)
            month_key = day[:7]
            if len(positions) < max_positions:
                for sc, sd in stock_data.items():
                    if len(positions) >= max_positions:
                        break
                    if sc in positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]

                    if is_bull:
                        # BULL: V1 MA추세 신호 (1000억+ 시총 포함 — V1 스스로 확인)
                        signal_ok = _is_buy_v1(
                            i, sd['sim_start_i'], sd['dates'], sd['prices'],
                            sd['volumes'], sd['frn'], sd['inst'], sd['fins'])
                        stop_val, take_val = -0.08, 0.20
                    else:
                        # BEAR: V7 흑자전환 신호 (300억+ 포함)
                        signal_ok = _is_buy_v11(
                            i, sd['sim_start_i'], sd['dates'], sd['prices'],
                            sd['volumes'], sd['frn'], sd['inst'], sd['fins'])
                        stop_val, take_val = -0.10, 0.30

                    if not signal_ok:
                        continue
                    if strict_exec:
                        if sc not in [x[0] for x in ra_pending_buys] and \
                           len(positions) + len(ra_pending_buys) < max_positions:
                            ra_pending_buys.append((sc, cur_regime, stop_val, take_val))
                            monthly_buys[month_key] = monthly_buys.get(month_key, 0) + 1
                        continue
                    curr = sd['prices'][i]
                    budget = min(per_stock, cash * 0.99)
                    qty = int(budget / curr)
                    if qty < 1 or qty * curr > cash:
                        continue
                    cash -= qty * curr
                    positions[sc] = {
                        'entry_date': day, 'entry_price': curr,
                        'qty': qty, 'peak_price': curr, 'hold_days': 0,
                        'stop_loss': stop_val, 'take_profit': take_val,
                        'regime': cur_regime,
                    }
                    monthly_buys[month_key] = monthly_buys.get(month_key, 0) + 1

            marked = sum(
                stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            equity_curve.append({'date': day, 'equity': round(cash + marked)})

        # 강제 청산
        last_day = sim_dates[-1] if sim_dates else None
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            curr = sd['prices'][im[last_day]] if last_day and last_day in im else sd['prices'][-1]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            _raf_amt, _raf_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
            cash += pos['qty'] * pos['entry_price'] + _raf_amt
            trades.append({
                'stock_code': sc, 'entry_date': pos['entry_date'],
                'exit_date': last_day or pos['entry_date'],
                'entry_price': pos['entry_price'], 'exit_price': curr,
                'qty': pos['qty'],
                'profit_pct': _raf_pct,
                'profit_amt': _raf_amt,
                'exit_reason': '기간종료',
                'entry_regime': pos.get('regime', '?'),
            })

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

        bull_buys = sum(1 for t in trades if t.get('entry_regime') == 'BULL')
        bear_buys = sum(1 for t in trades if t.get('entry_regime') == 'BEAR')
        bull_days = sum(1 for v in market_bullish.values() if v)
        bear_days = sum(1 for v in market_bullish.values() if not v)

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 레짐 적응형: BULL({bull_days}일→V1 MA추세) / BEAR({bear_days}일→V7 흑자전환)\n"
            f"매수: BULL={bull_buys}건 / BEAR={bear_buys}건  |  레짐 전환: {len(regime_switches)}회\n"
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
            'regime_switches': regime_switches[:20],
        }
        _save_result(run_id, result)
        _register_execution_artifacts(run_id, total_capital, cash)
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
#  복합 스코어링 시그널 (100점 기반 선택적 매수)
# ══════════════════════════════════════════════════════════════
def _score_stock(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> int:
    """
    종목 품질 점수 (0~100).

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

    return score


def run_backtest_composite(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    score_threshold: int = 60,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    복합 스코어링 전략 (V10 선택적 복합 시그널).

    핵심: 100점 스코어에서 threshold(기본 60점) 이상인 종목만 매수.
    60점 달성 = 최소 3가지 독립 조건 동시 충족.

    동적 익절: 점수 60~69 → +20%, 70~79 → +30%, 80+ → +40%
    손절: -10% 고정
    MA60 붕괴 시 즉시 매도
    """
    init_backtest_db()
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    run_id   = run_id or str(uuid.uuid4())[:8]
    run_name = run_name or f"composite_{start_date[:4]}"
    strategy = "composite"
    # 2026-07-27: 코드 재확인 결과 이 엔진도 유니버스 쿼리에 market_cap 조건이 없고
    # score 계산·positions에도 실제 시총이 쓰이지 않음(mkt_cap_억은 항상 기본값 500) —
    # v8/regime_adaptive와 동일하게 "current" 라벨이 부정확했으므로 정정.
    _record_run_spec(
        run_id, "composite", "composite_v2_strict_20260716",
        {"per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
    )

    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,"
        "per_stock,max_pos,status) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, run_name, strategy, start_date, end_date, per_stock, max_positions, "running"),
    )
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            raise ValueError("시뮬레이션 날짜가 없습니다.")

        # KOSPI 레짐 로드
        market_bullish: Dict[str, bool] = {}
        try:
            krows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r["date"]  for r in krows]
            k_prices = [float(r["close"]) for r in krows]
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    continue
                kma = _ma(k_prices[max(0, ki-119):ki+1], 120)
                market_bullish[kd] = (kma is None) or (k_prices[ki] > kma)
        except Exception:
            pass

        # 종목 데이터 로드
        stock_codes = [r[0] for r in conn.execute("""
            SELECT stock_code, COUNT(*) AS cnt FROM price_history
            WHERE date>=? AND date<=? AND close>0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code HAVING COUNT(*) >= 200
        """, (warmup_start, end_date)).fetchall()]

        stock_data: Dict[str, dict] = {}
        date_idx:   Dict[str, dict] = {}

        for sc in stock_codes:
            rows = conn.execute("""
                SELECT date, close, volume,
                       COALESCE(frn_net_buy, 0) AS frn,
                       COALESCE(inst_net_buy, 0) AS inst,
                       COALESCE(open, 0) AS open_p
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (sc, warmup_start, end_date)).fetchall()
            if len(rows) < 120:
                continue
            fin_rows = conn.execute("""
                SELECT f.year, f.quarter, f.revenue, f.operating_profit, f.eps, f.bps,
                       f.total_equity, f.net_income, f.roe, f.is_annual,
                       COALESCE(d.avail_date,
                         CASE WHEN f.is_annual=1 THEN printf('%d-03-31', f.year+1)
                              WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=? AND d.year=f.year
                    AND d.quarter=CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                    AND d.is_annual=CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
                WHERE f.stock_code=? AND f.report_type IN ('CFS','') AND f.quarter IN (1,2,3,4)
                ORDER BY f.year DESC, f.quarter DESC
            """, (sc, sc)).fetchall()
            if not fin_rows:
                continue
            fin_all = [(r["year"], r["quarter"], r["revenue"], r["operating_profit"],
                        r["eps"], r["bps"], r["total_equity"], r["net_income"],
                        r["roe"], bool(r["is_annual"]), r["avail_date"]) for r in fin_rows]

            dts = [r["date"] for r in rows]
            prs = [float(r["close"]) for r in rows]
            vls = [float(r["volume"]) if r["volume"] else 0.0 for r in rows]
            fns = [float(r["frn"]) if r["frn"] else 0.0 for r in rows]
            ins = [float(r["inst"]) if r["inst"] else 0.0 for r in rows]
            ops = [float(r["open_p"]) if r["open_p"] else 0.0 for r in rows]

            # sim_start_i
            sim_i = next((j for j, d in enumerate(dts) if d >= start_date), len(dts))

            stock_data[sc] = {
                'dates': dts, 'prices': prs, 'volumes': vls,
                'frn': fns, 'inst': ins, 'fins': fin_all,
                'sim_start_i': sim_i, 'opens': ops,
            }
            date_idx[sc] = {d: j for j, d in enumerate(dts)}

        conn.row_factory = None

        # 시뮬레이션
        cash = per_stock * max_positions  # 2026-07-16: 실현손익 누산(capital) → 현금원장 전환
        positions: Dict[str, dict] = {}
        trades: List[dict] = []
        daily_pnl: List[Tuple[str, float]] = []
        _pb: Dict[str, dict] = {}   # pending buys  (D+1 집행)
        _ps: Dict[str, dict] = {}   # pending sells (D+1 집행)

        for day in sim_dates:

            # ── Phase A: 전일 매도 신호 → 오늘 시가/종가 집행 ────────
            to_remove_ps = []
            for sc in list(_ps.keys()):
                if sc not in positions:
                    to_remove_ps.append(sc)
                    continue
                pos = positions[sc]
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i   = im[day]
                op  = sd['opens'][i] if i < len(sd['opens']) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                ep   = pos['entry_price']
                qty  = pos['qty']
                _cmp_amt, _cmp_pct = _net_profit(ep, curr, qty, pos.get('mkt_cap_억', 500))
                cash += qty * ep + _cmp_amt
                held = pos.get('hold_days', 0)
                trades.append({
                    'sc': sc, 'entry': pos['entry_date'], 'exit': day,
                    'entry_price': ep, 'exit_price': curr,
                    'return_pct': _cmp_pct, 'pnl': _cmp_amt,
                    'reason': _ps[sc].get('reason', '매도'),
                    'score': pos.get('score', 0), 'held_days': held,
                })
                del positions[sc]
                to_remove_ps.append(sc)
            for sc in to_remove_ps:
                _ps.pop(sc, None)

            # ── Phase B: 전일 매수 신호 → 오늘 시가/종가 집행 ────────
            sorted_buys = sorted(_pb.items(), key=lambda x: x[1].get('score', 0), reverse=True)
            for sc, meta in sorted_buys:
                if sc in positions or len(positions) >= max_positions:
                    continue
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i   = im[day]
                op  = sd['opens'][i] if i < len(sd['opens']) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                if curr <= 0:
                    continue
                s = meta.get('score', 60)
                take_p = 0.40 if s >= 80 else (0.30 if s >= 70 else 0.20)
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / curr)
                if qty < 1 or qty * curr > cash:
                    continue
                cash -= qty * curr
                positions[sc] = {
                    'entry_date': day, 'entry_price': curr, 'qty': qty,
                    'score': s, 'take_profit': take_p, 'hold_days': 0,
                    'mkt_cap_억': meta.get('mkt_cap_억', 500),
                }
            _pb.clear()

            # ── hold_days 증가 ─────────────────────────────────────
            for pos in positions.values():
                pos['hold_days'] = pos.get('hold_days', 0) + 1

            # ── Phase C: 매도 신호 탐지 → _ps 큐 ─────────────────
            for sc, pos in list(positions.items()):
                if sc in _ps:
                    continue
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i    = im[day]
                curr = sd['prices'][i]
                if curr <= 0:
                    continue
                ep   = pos['entry_price']
                ret  = (curr - ep) / ep
                held = pos.get('hold_days', 0)
                take = pos.get('take_profit', 0.25)
                exit_reason = None
                if ret >= take:
                    exit_reason = f"익절{take*100:.0f}%"
                elif ret <= -0.10:
                    exit_reason = "손절-10%"
                elif held > 5:
                    ma60_e = _ma(sd['prices'][max(0, i-59):i+1], 60)
                    if ma60_e and curr < ma60_e:
                        exit_reason = "MA60붕괴"
                    # 240일 장기횡보 보류: 하락장 -1.3%→-25.3% 악화로 미적용
                if exit_reason:
                    _ps[sc] = {'reason': exit_reason}

            # ── Phase D: 매수 신호 탐지 → _pb 큐 ─────────────────
            if len(positions) < max_positions:
                candidates = []
                for sc, sd in stock_data.items():
                    if sc in positions or sc in _ps:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]
                    s = _score_stock(
                        i, sd['sim_start_i'], sd['dates'], sd['prices'],
                        sd['volumes'], sd['frn'], sd['inst'], sd['fins'])
                    if s >= score_threshold:
                        candidates.append((s, sc))
                candidates.sort(key=lambda x: -x[0])
                for s, sc in candidates:
                    if len(_pb) + len(positions) - len(_ps) >= max_positions:
                        break
                    _pb[sc] = {'score': s}

            # ── Phase E: 일별 PnL ──────────────────────────────────
            portfolio_val = cash
            for sc, pos in positions.items():
                sd = stock_data[sc]
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i = im[day]
                curr = sd['prices'][i]
                portfolio_val += curr * pos['qty']
            daily_pnl.append((day, portfolio_val))

        # 미청산 포지션 강제 청산
        last_day = sim_dates[-1] if sim_dates else end_date
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            if last_day in im:
                i    = im[last_day]
                curr = sd['prices'][i]
                ep   = pos['entry_price']
                qty  = pos['qty']
                _cmpf_amt, _cmpf_pct = _net_profit(ep, curr, qty, pos.get('mkt_cap_억', 500))
                cash += qty * ep + _cmpf_amt
                trades.append({
                    'sc': sc, 'entry': pos['entry_date'], 'exit': last_day,
                    'entry_price': ep, 'exit_price': curr,
                    'return_pct': _cmpf_pct, 'pnl': _cmpf_amt,
                    'reason': '기간종료', 'score': pos.get('score', 0),
                    'held_days': pos.get('hold_days', 0),
                })

        # 집계
        total_trades   = len(trades)
        winners        = [t for t in trades if t['return_pct'] > 0]
        losers         = [t for t in trades if t['return_pct'] <= 0]
        win_rate       = len(winners) / total_trades * 100 if total_trades else 0
        total_invested = per_stock * max_positions
        total_ret_pct  = (cash - total_invested) / total_invested * 100 if total_invested else 0

        avg_win  = sum(t['return_pct'] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t['return_pct'] for t in losers) / len(losers)   if losers  else 0
        pf       = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        # 스코어 분포
        score_dist = {
            '60-69': len([t for t in trades if 60 <= t.get('score', 0) < 70]),
            '70-79': len([t for t in trades if 70 <= t.get('score', 0) < 80]),
            '80+':   len([t for t in trades if t.get('score', 0) >= 80]),
        }

        days = len(sim_dates)
        yrs  = days / 252
        cagr = (cash / total_invested) ** (1 / yrs) * 100 - 100 if yrs > 0 and total_invested > 0 else 0

        summary = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 복합 스코어링 전략 (threshold={score_threshold}점)\n"
            f"스코어 분포: 60-69점={score_dist['60-69']}건 / 70-79점={score_dist['70-79']}건 / 80+점={score_dist['80+']}건\n"
            f"총 거래: {total_trades}건  승률: {win_rate:.1f}%  Profit Factor: {pf:.2f}\n"
            f"avg 수익: {avg_win:+.1f}%  avg 손실: {avg_loss:+.1f}%\n"
            f"CAGR: {cagr:.2f}%  총수익: {total_ret_pct:+.1f}%\n"
        )

        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("""
            UPDATE backtest_runs SET
                status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, summary_text=?, trades_json=?, strategy=?
            WHERE run_id=?
        """, (total_ret_pct, cagr, win_rate, total_trades,
              summary, json.dumps(trades, ensure_ascii=False), strategy, run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        _register_execution_artifacts(run_id, total_invested, cash)
        return run_id

    except Exception as e:
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        raise


# ══════════════════════════════════════════════════════════════
#  Meta-V 2.0: BULL → 복합스코어링, BEAR → V7 흑자전환
# ══════════════════════════════════════════════════════════════
def run_backtest_meta_v2(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    score_threshold: int = 65,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    Meta-V 2.0 전략:
      - BULL (KOSPI > MA120): 복합스코어링(65점+)으로 매수
      - BEAR (KOSPI < MA120): V7 흑자전환 단독으로 매수 (MA추세 무관)

    복합스코어링은 상승장/회복장에서 역대 최고 성과.
    V7은 하락장에서 유일하게 양수 (+80.3%).
    두 전략을 레짐에 맞게 조합하여 전 기간 양수 목표.
    """
    init_backtest_db()
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    run_id   = run_id or str(uuid.uuid4())[:8]
    run_name = run_name or f"meta_v2_{start_date[:4]}"
    strategy = "meta_v2"

    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,"
        "per_stock,max_pos,status) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, run_name, strategy, start_date, end_date, per_stock, max_positions, "running"),
    )
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")

        # 시뮬레이션 날짜 (영업일)
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            raise ValueError("시뮬레이션 날짜가 없습니다.")

        # KOSPI 레짐 (히스테리시스 ±2%)
        market_bullish: Dict[str, bool] = {}
        try:
            krows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r["date"]  for r in krows]
            k_prices = [float(r["close"]) for r in krows]
            cur_regime = True
            for ki, kd in enumerate(k_dates):
                kma = _ma(k_prices[max(0, ki-119):ki+1], 120)
                if kma is not None:
                    if cur_regime and k_prices[ki] < kma * 0.98:
                        cur_regime = False
                    elif not cur_regime and k_prices[ki] > kma * 1.02:
                        cur_regime = True
                if kd >= start_date:
                    market_bullish[kd] = cur_regime
        except Exception:
            pass

        # 종목 데이터 로드
        stock_codes = [r[0] for r in conn.execute("""
            SELECT stock_code, COUNT(*) cnt FROM price_history
            WHERE date>=? AND date<=? AND close>0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code HAVING COUNT(*) >= 200
        """, (warmup_start, end_date)).fetchall()]

        stock_data: Dict[str, dict] = {}
        date_idx:   Dict[str, dict] = {}

        for sc in stock_codes:
            rows = conn.execute("""
                SELECT date, close, volume,
                       COALESCE(frn_net_buy, 0) frn,
                       COALESCE(inst_net_buy, 0) inst
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (sc, warmup_start, end_date)).fetchall()
            if len(rows) < 120:
                continue
            fin_rows = conn.execute("""
                SELECT f.year, f.quarter, f.revenue, f.operating_profit, f.eps, f.bps,
                       f.total_equity, f.net_income, f.roe, f.is_annual,
                       COALESCE(d.avail_date,
                         CASE WHEN f.is_annual=1 THEN printf('%d-03-31', f.year+1)
                              WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=? AND d.year=f.year
                    AND d.quarter=CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                    AND d.is_annual=CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
                WHERE f.stock_code=? AND f.report_type IN ('CFS','') AND f.quarter IN (1,2,3,4)
                ORDER BY f.year DESC, f.quarter DESC
            """, (sc, sc)).fetchall()
            if not fin_rows:
                continue
            fin_all = [(r["year"], r["quarter"], r["revenue"], r["operating_profit"],
                        r["eps"], r["bps"], r["total_equity"], r["net_income"],
                        r["roe"], bool(r["is_annual"]), r["avail_date"]) for r in fin_rows]
            dts = [r["date"] for r in rows]
            prs = [float(r["close"]) for r in rows]
            vls = [float(r["volume"]) if r["volume"] else 0.0 for r in rows]
            fns = [float(r["frn"]) if r["frn"] else 0.0 for r in rows]
            ins = [float(r["inst"]) if r["inst"] else 0.0 for r in rows]
            sim_i = next((j for j, d in enumerate(dts) if d >= start_date), len(dts))
            stock_data[sc] = {
                'dates': dts, 'prices': prs, 'volumes': vls,
                'frn': fns, 'inst': ins, 'fins': fin_all, 'sim_start_i': sim_i,
            }
            date_idx[sc] = {d: j for j, d in enumerate(dts)}

        conn.row_factory = None

        # 시뮬레이션
        capital = 0.0
        positions: Dict[str, dict] = {}
        trades: List[dict] = []
        bull_days = 0
        bear_days = 0
        prev_bull = True   # 초기 상태: BULL

        for si, day in enumerate(sim_dates):
            is_bull = market_bullish.get(day, True)
            if is_bull:
                bull_days += 1
            else:
                bear_days += 1

            # BULL→BEAR 전환 감지: 전 포지션 즉시 강제 청산
            if prev_bull and not is_bull and positions:
                for sc, pos in list(positions.items()):
                    im = date_idx.get(sc, {})
                    if day in im:
                        i    = im[day]
                        curr = stock_data[sc]['prices'][i]
                        ep   = pos['entry_price']
                        ret  = (curr - ep) / ep
                        capital += ret * per_stock
                        trades.append({
                            'sc': sc, 'entry': pos['entry_date'], 'exit': day,
                            'entry_price': ep, 'exit_price': curr,
                            'return_pct': ret * 100, 'pnl': ret * per_stock,
                            'reason': 'BEAR전환강제청산', 'score': pos.get('score', 0),
                            'mode': 'bull',
                        })
                positions.clear()
            prev_bull = is_bull

            # BULL 구간: 일반 매도 체크 (익절/손절/MA60붕괴)
            if is_bull:
                to_sell = []
                for sc, pos in positions.items():
                    sd  = stock_data[sc]
                    im  = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i    = im[day]
                    curr = sd['prices'][i]
                    if curr <= 0:
                        continue
                    ep   = pos['entry_price']
                    ret  = (curr - ep) / ep
                    held = si - next((j for j, d in enumerate(sim_dates) if d == pos['entry_date']), si)
                    take = pos.get('take_profit', 0.25)
                    stop = pos.get('stop_loss', -0.10)

                    exit_reason = None
                    if ret >= take:
                        exit_reason = f"익절{take*100:.0f}%"
                    elif ret <= stop:
                        exit_reason = f"손절{stop*100:.0f}%"
                    elif held > 5:
                        ma60_e = _ma(sd['prices'][max(0, i-59):i+1], 60)
                        if ma60_e and curr < ma60_e:
                            exit_reason = "MA60붕괴"
                        # 240일 장기횡보 보류: 하락장 -1.3%→-25.3% 악화로 미적용

                    if exit_reason:
                        pnl = ret * per_stock
                        capital += pnl
                        trades.append({
                            'sc': sc, 'entry': pos['entry_date'], 'exit': day,
                            'entry_price': ep, 'exit_price': curr,
                            'return_pct': ret * 100, 'pnl': pnl,
                            'reason': exit_reason, 'score': pos.get('score', 0),
                            'mode': pos.get('mode', 'bull'),
                        })
                        to_sell.append(sc)
                for sc in to_sell:
                    del positions[sc]

            # 매수: BULL 구간에서만 복합 스코어링 진입 (BEAR=현금 보유)
            if is_bull and len(positions) < max_positions:
                candidates = []
                for sc, sd in stock_data.items():
                    if sc in positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]
                    s = _score_stock(
                        i, sd['sim_start_i'], sd['dates'], sd['prices'],
                        sd['volumes'], sd['frn'], sd['inst'], sd['fins'])
                    if s >= score_threshold:
                        candidates.append((s, sc))
                candidates.sort(key=lambda x: -x[0])
                for s, sc in candidates:
                    if len(positions) >= max_positions:
                        break
                    sd   = stock_data[sc]
                    i    = date_idx[sc][day]
                    curr = sd['prices'][i]
                    if curr <= 0:
                        continue
                    # 점수 기반 익절
                    take_p = 0.40 if s >= 80 else 0.30 if s >= 70 else 0.20
                    positions[sc] = {
                        'entry_date': day, 'entry_price': curr,
                        'qty': max(1, int(per_stock / curr)),
                        'score': s, 'take_profit': take_p, 'stop_loss': -0.10,
                        'mode': 'bull',
                    }

        # 미청산 강제 청산
        last_day = sim_dates[-1]
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            if last_day in im:
                i    = im[last_day]
                curr = sd['prices'][i]
                ep   = pos['entry_price']
                ret  = (curr - ep) / ep
                capital += ret * per_stock
                trades.append({
                    'sc': sc, 'entry': pos['entry_date'], 'exit': last_day,
                    'entry_price': ep, 'exit_price': curr,
                    'return_pct': ret * 100, 'pnl': ret * per_stock,
                    'reason': '기간종료', 'score': pos.get('score', 0),
                    'mode': pos.get('mode', 'bull'),
                })

        # 집계
        total_trades  = len(trades)
        winners       = [t for t in trades if t['return_pct'] > 0]
        losers        = [t for t in trades if t['return_pct'] <= 0]
        win_rate      = len(winners) / total_trades * 100 if total_trades else 0
        total_invested = per_stock * max_positions
        total_ret_pct = capital / total_invested * 100 if total_invested else 0
        avg_win  = sum(t['return_pct'] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t['return_pct'] for t in losers) / len(losers)   if losers  else 0
        pf       = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        days = len(sim_dates)
        yrs  = days / 252
        cagr = ((1 + capital / total_invested) ** (1 / yrs) - 1) * 100 if yrs > 0 and total_invested > 0 else 0

        summary = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ Meta-V 2.0: BULL={bull_days}일(복합스코어링+{score_threshold}점) / BEAR={bear_days}일(현금보유)\n"
            f"총 거래: {total_trades}건  승률: {win_rate:.1f}%  Profit Factor: {pf:.2f}\n"
            f"avg 수익: {avg_win:+.1f}%  avg 손실: {avg_loss:+.1f}%\n"
            f"CAGR: {cagr:.2f}%  총수익: {total_ret_pct:+.1f}%\n"
        )

        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("""
            UPDATE backtest_runs SET
                status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, summary_text=?, trades_json=?, strategy=?
            WHERE run_id=?
        """, (total_ret_pct, cagr, win_rate, total_trades,
              summary, json.dumps(trades, ensure_ascii=False), strategy, run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        return run_id

    except Exception as e:
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        raise


# ══════════════════════════════════════════════════════════════
#  골든크로스 모멘텀 전략 (V-GC)
#
#  실증 근거 (2020~2026, 6기간 검증):
#    5/6 기간 KOSPI 아웃퍼폼, 평균 알파 +37.4%
#    하락장 -1.4% (KOSPI -22.9% 대비 +21.5%α) ← 핵심 강점
#
#  진입 조건:
#    [A] MA20 > MA60 (단기 정배열)
#    [B] 최근 15일 내 MA20이 MA60을 골든크로스
#    [C] 5일 평균거래량 > 20일 평균거래량 × 1.2 (거래량 확인)
#    [D] RS6M(6개월 KOSPI 대비 상대강도) > -20
#    [E] 최근 5일 중 1일이라도 ±50% 이상 등락 없음 (분할/합병 미조정 제거)
#
#  정렬: RS6M 내림차순 (상대강도 높은 종목 우선 진입)
#
#  매도 조건 (피크이지/모멘텀이지):
#    손절: -12% 이하 (갭리스크 감안)
#    Trail25%: 이익 5%+ 달성 후 고점대비 -25% 하락 시 매도
#    Trail30%: 이익 50%+ 달성 시 고점대비 -30% (대박 종목 홀드 연장)
#    만료: 300거래일 초과
# ══════════════════════════════════════════════════════════════
def run_backtest_golden_cross(
    start_date: str, end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    run_name: str = None, run_id: str = None,
    cross_days: int = 15,
    vol_ratio: float = 1.2,
    rs6m_min: float = -20.0,
    trail_pct: float = -0.30,  # 2026-07-21 -0.25→-0.30: 연속운용(2020-03~2026-03) 212.08%→215.65%, 승률27.6→28.8%, trail_big는 그대로 유지해야 개선(둘 다 -0.30로 맞추면 209.27%로 오히려 악화 — 메가수익주 확장구간을 건드리면 안 됨)
    trail_big: float = -0.35,
    stop: float = -0.12,
    max_hold: int = 300,
    max_new_per_month: int = 8,
    min_mktcap: float = 4000,  # 2026-08-10: 2000→4000 변경. 사용자 지시("10버거 종목을 확대하되
                                # 아닌 종목을 걸러낼수 있는 지표")로 384건 교차전략(golden_cross 등
                                # 4개 전략) 텐버거 매칭 데이터에서 golden_cross 단독(238건) 성공/손절
                                # 비교 시 시총이 단조판별력 보유(baseline succ33.8%→mktcap1조+ succ42.2%,
                                # 학습/검증 양쪽 방향일치+강화 확인). 실제 전략 min_mktcap을
                                # 2000/3000/4000/5000/6000/7000/8000/10000 전수 스윕한 결과 5000만
                                # fat-tail 이상치(특정 대형주 3종목이 우연히 그 임계값에서만 편입, 지속
                                # 검증 불가로 폐기)이고 3000~8000은 대체로 견고. 6기간 매트릭스 공정비교:
                                # baseline avg6=25.28%(4/6양수) → 3000=33.33% → **4000=35.48%**(4/6양수,
                                # 거의 전구간 개선 — 하락장 방어도 개선, 최근/최신 구간 대폭개선).
    hot_sector_boost: bool = False,  # 2026-07-13 철회: as-of 유니버스에서 무효(+15.7 vs 무부스트 +17.4) — 채택 근거가 유니버스 룩어헤드였음
    big_gate: float = 0.50,
    hot_boost_1: float = 15.0,
    hot_boost_2: float = 5.0,
    hot_top_n: int = 2,
    prog_bonus_pt: float = None,    # 프로그램매매 5일 누적 순매수 양수 종목 랭킹 보너스 (예: 10.0)
    export_bonus_pt: float = None,  # 수출 YoY 양수(2개월 시차 as-of) 종목 랭킹 보너스 (예: 10.0)
    sector_export_bonus_pt: float = None,  # 섹터 실물수출지표(관세청 44종→sector_mid 매핑) YoY>+5% 랭킹 보너스 — 2026-07-12 기각(시차 지표가 모멘텀 랭킹 오염)
    gc_flow_bonus_pt: float = None,        # 기관+외인 5일 순매수 양수 랭킹 보너스 — 2026-07-12 기각(추세초기 수급 부재)
    pyramid_gain: float = None,            # 보유 수익 +N 도달 시 0.5티켓 추가매수 1회 (예: 0.15) — 이기는 포지션 증액
    compounding: bool = False,             # 복리 모드: 티켓 = 현재 에쿼티/max_positions (기본 고정 1,000만)
    asof_mktcap: bool = True,              # 2026-07-13 기본화: 시총 필터를 as-of(진입일 주가×상장주식수)로 적용 — 현재시총 룩어헤드 제거. as-of 실측 avg6 +17.4%(부스트 없음, 2/6 양수)
    avoid_overheat: float = 1.0,           # 2026-07-13 기본 채택: 진입일 40일 수익률 +100% 초과 급등주 진입 제외.
                                            # 근거: 스냅샷 라벨 실증(급등직후 6개월 -30%하락률 37~41%, 기준율 3.6배) →
                                            # as-of 6기간 백테스트 baseline -0.6% → oh0.7 +20.5% / oh1.0 +17.8%(3/6양수, 채택) / oh1.5 +14.9% (전 범위 강건)
    chart_confluence: bool = False,        # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    market_ma_gate: int = None,            # KOSPI가 N일선 아래면 신규 진입만 중단(보유/청산 불변)
) -> str:
    """
    골든크로스 모멘텀 전략 (V-GC).
    hot_sector_boost=True(선택): 진입 랭킹에 주도섹터 보너스 추가 — 당일 기준(as-of)
    sector_large별 유니버스 평균 ret20 랭킹 1위 섹터 +15pt, 2위 +5pt.
    (2026-07-11 StockEasy 주도섹터 바스켓 실증에서 착안, 룩어헤드 없음)
    trail_big -0.35: +50% 이상 수익 구간 추적손절 완화 — 메가수익주(금양 +442% 등) 보유 지속.
    2026-07-13 편향 정정 결과 (6기간, 다음날 시가·as-of 근사 시총):
      기본선 avg6 -0.6%(2/6 양수), 40거래일 +100% 초과 과열 제외 시 +17.8%(3/6 양수).
      과거 +71.1% 결과는 현재 유니버스/시총 룩어헤드가 포함되어 폐기한다.
    ⚠️ sector_large는 현재 시점 분류를 과거에 적용하므로 PIT 섹터 이력 구축 전까지 연구용이다.
    RS6M 상대강도로 후보 종목 정렬 후 진입, Trail-25%/30% 추적손절 매도.
    시총 2000억+(중대형주) 종목 대상, 분할/합병 미조정 데이터 자동 제거.
    - 2000억 이상으로 제한: 소형주 골든크로스는 약세/회복장에서 성능 급락(avg5 -11%→+28%로 개선).
    """
    init_backtest_db()
    run_name = run_name or f"V-GC골든크로스Trail25 {start_date[:7]}~{end_date[:7]}"
    # 방법론 메타 기록 (Codex P0-2)
    _gc_params = {
        "cross_days": cross_days, "vol_ratio": vol_ratio, "rs6m_min": rs6m_min,
        "trail_pct": trail_pct, "trail_big": trail_big, "stop": stop, "max_hold": max_hold,
        "min_mktcap": min_mktcap, "hot_sector_boost": hot_sector_boost,
        "avoid_overheat": avoid_overheat, "asof_mktcap": asof_mktcap,
        "chart_confluence": chart_confluence, "market_ma_gate": market_ma_gate,
        "per_stock": per_stock, "max_positions": max_positions,
        "start": start_date, "end": end_date,
    }
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'golden_cross',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running',strategy='golden_cross' WHERE run_id=?",
                     (run_id,))
        conn.commit()

    _record_run_spec(
        run_id, "golden_cross", "gc_v3_overheat_20260713", _gc_params,
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule=("compounding" if compounding else "fixed_slot"),
    )

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=300)).strftime('%Y-%m-%d')

        # KOSPI 데이터 (RS6M 계산용)
        kospi_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0
            ORDER BY date
        """).fetchall()
        k_dates = [r[0] for r in kospi_rows]
        k_prices = [float(r[1]) for r in kospi_rows]
        k_idx = {d: i for i, d in enumerate(k_dates)}

        def _get_k6m(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date:
                        idx = k_idx[d]; break
            if idx is None or idx < 126: return None
            p0, p126 = k_prices[idx], k_prices[idx - 126]
            return (p0 / p126 - 1) * 100 if p126 > 0 else None

        def _market_gate_open(day: str) -> bool:
            if not market_ma_gate:
                return True
            idx = k_idx.get(day)
            if idx is None:
                return False
            window = int(market_ma_gate)
            if window <= 1 or idx + 1 < window:
                return False
            return k_prices[idx] >= sum(k_prices[idx - window + 1:idx + 1]) / window

        # 종목 로드 (시총 min_mktcap+, KOSPI/KOSDAQ, 6자리 코드)
        # stock_universe.market_cap은 억원 단위 → 500억 = 500
        codes = conn.execute("""
            SELECT DISTINCT p.stock_code, su.market_cap
            FROM price_history p
            INNER JOIN stock_universe su ON p.stock_code = su.stock_code
            WHERE p.date >= ? AND p.date <= ? AND p.close >= 1000
            AND su.market_cap >= ?
            AND su.market IN ('KOSPI','KOSDAQ')
            AND LENGTH(p.stock_code) = 6
            AND p.stock_code NOT LIKE '%^%'
            AND p.stock_code NOT LIKE 'GC%'
            AND p.stock_code NOT LIKE 'CL%'
            AND p.stock_code NOT LIKE '%-F'
            AND p.stock_code NOT LIKE '%=%'
            AND p.stock_code NOT LIKE 'NQ%'
            ORDER BY su.market_cap DESC
        """, (start_date, end_date, 0 if asof_mktcap else min_mktcap)).fetchall()

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _gc_shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume, 0), COALESCE(open, close),
                       COALESCE(high, close), COALESCE(low, close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>=500
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 145: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 미조정 필터: 하루 ±50% 이상 변동 제거
            has_artifact = any(
                c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.5 or c_list[i]/c_list[i-1] > 2.0)
                for i in range(1, len(c_list))
            )
            if has_artifact: continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'o': [float(r[3]) for r in rows],
                'h': [float(r[4]) for r in rows],
                'lo': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 500,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        # conn은 _is_sector_buy 에서 계속 사용되므로 루프 후에 닫는다

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx: Dict[str, Dict[str, int]] = {
            c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()
        }

        # 주도섹터 부스트: sector_large 매핑 로드 (as-of ret20으로 일별 랭킹 계산)
        sec_of: Dict[str, str] = {}
        if hot_sector_boost:
            for r in conn.execute(
                "SELECT stock_code, sector_large FROM stock_universe "
                "WHERE stock_code IN ({})".format(",".join("?" * len(sd))),
                list(sd.keys()),
            ).fetchall():
                if r[1] and r[1] != '기타':
                    sec_of[r[0]] = r[1]

        def _hot_sectors_of(day: str) -> dict:
            """당일 기준 섹터별 유니버스 평균 ret20 → {섹터: 랭크(1|2)}. 룩어헤드 없음."""
            agg: Dict[str, list] = {}
            for code2, s2 in sd.items():
                sec2 = sec_of.get(code2)
                if not sec2:
                    continue
                i2 = didx[code2].get(day)
                if i2 is None or i2 < 20:
                    continue
                c0, c20 = s2['c'][i2], s2['c'][i2 - 20]
                if c20 > 0:
                    agg.setdefault(sec2, []).append(c0 / c20 - 1)
            avg2 = {k: sum(v) / len(v) for k, v in agg.items() if len(v) >= 5}
            top = sorted(avg2.items(), key=lambda x: -x[1])[:hot_top_n]
            return {s3: (i3 + 1) for i3, (s3, a3) in enumerate(top) if a3 >= 0.03}

        # 프로그램매매 보너스: 종목별 일자→순매수량 로드 (as-of 5일 누적, 데이터 2020-12~)
        prog_map: Dict[str, Dict[str, float]] = {}
        if prog_bonus_pt is not None and sd:
            for r in conn.execute(
                "SELECT stock_code, dt, AVG(net_buy_qty) FROM broker_program_stock_daily "
                "WHERE dt>=? AND dt<=? AND net_buy_qty IS NOT NULL AND stock_code IN ({}) "
                "GROUP BY stock_code, dt".format(",".join("?" * len(sd))),
                [warmup_start, end_date] + list(sd.keys())).fetchall():
                prog_map.setdefault(r[0], {})[r[1]] = float(r[2] or 0)

        def _prog_positive(code: str, day: str) -> bool:
            m = prog_map.get(code)
            if not m:
                return False
            i0 = didx[code].get(day)
            if i0 is None:
                return False
            days5 = sd[code]['d'][max(0, i0 - 4):i0 + 1]
            vals = [m[d5] for d5 in days5 if d5 in m]
            return bool(vals) and sum(vals) > 0

        # 수출 YoY 보너스: hs_trade_lab 월별 수출 (공표시차 2개월 as-of)
        export_map: Dict[str, Dict[str, float]] = {}
        if export_bonus_pt is not None:
            export_map = _load_trade_signals()

        # 섹터 실물수출지표 보너스: 관세청 44종(quant_major_indicator_series) → sector_mid 매핑
        SEC_EXPORT_MAP = {
            'public:23:1':  ['자동차 및 부품', '자동차 신품 부품 제조업', '자동차 차체나 트레일러 제조업'],
            'public:23:2':  ['자동차 및 부품', '자동차 신품 부품 제조업'],
            'public:23:27': ['자동차 및 부품'], 'public:23:33': ['자동차 및 부품'],
            'public:23:4':  ['반도체', '반도체 제조업'], 'public:23:5': ['반도체', '반도체 제조업'],
            'public:23:6':  ['반도체', '반도체 제조업'], 'public:23:32': ['반도체', '반도체 제조업'],
            'public:23:40': ['반도체', '전자부품 제조업'],
            'public:23:11': ['디스플레이', '전자부품 제조업'],
            'public:23:12': ['전자부품 제조업'], 'public:23:13': ['전자부품 제조업'],
            'public:23:7':  ['선박 및 보트 건조업'], 'public:23:21': ['선박 및 보트 건조업'],
            'public:23:8':  ['소재'], 'public:23:20': ['소재'], 'public:23:34': ['소재'], 'public:23:39': ['소재'],
            'public:23:18': ['1차 비철금속 제조업'], 'public:23:19': ['1차 비철금속 제조업'],
            'public:23:9':  ['생활용품'],
            'public:23:10': ['제약 및 바이오', '의약품 제조업', '기초 의약물질 제조업'],
            'public:23:25': ['제약 및 바이오', '의료장비 및 서비스'],
            'public:23:26': ['제약 및 바이오', '의약품 제조업'],
            'public:23:24': ['의료장비 및 서비스'],
            'public:23:14': ['에너지'], 'public:23:35': ['에너지'],
            'public:23:17': ['기초 화학물질 제조업', '기타 화학제품 제조업', '합성고무 및 플라스틱 물질 제조업'],
            'public:23:41': ['기초 화학물질 제조업'], 'public:23:42': ['기초 화학물질 제조업', '기타 화학제품 제조업'],
            'public:23:22': ['특수 목적용 기계 제조업', '일반 목적용 기계 제조업'],
            'public:23:23': ['특수 목적용 기계 제조업', '자본재'],
            'public:23:38': ['자본재', '특수 목적용 기계 제조업'],
            'public:23:36': ['자본재'], 'public:23:37': ['자본재'], 'public:23:43': ['자본재'],
            'public:23:29': ['내구 소비재 및 의류', '봉제의복 제조업'],
            'public:23:30': ['음식료 및 담배'], 'public:23:31': ['음식료 및 담배'],
            'public:23:44': ['펄프, 종이 및 판지 제조업'],
        }
        sec_export_series: Dict[str, Dict[str, float]] = {}   # indicator_key → {ym: 수출액}
        smid_of: Dict[str, str] = {}                           # stock_code → sector_mid
        smid_indicators: Dict[str, list] = {}                  # sector_mid → [indicator_key]
        if sector_export_bonus_pt is not None and sd:
            for r in conn.execute(
                "SELECT indicator_key, period, value FROM quant_major_indicator_series "
                "WHERE indicator_key LIKE 'public:23:%' AND series_name LIKE '%수출액' AND value>0"
            ).fetchall():
                sec_export_series.setdefault(r[0], {})[r[1]] = float(r[2])
            for r in conn.execute(
                "SELECT stock_code, sector_mid FROM stock_universe WHERE stock_code IN ({})".format(
                    ",".join("?" * len(sd))), list(sd.keys())).fetchall():
                if r[1]:
                    smid_of[r[0]] = r[1]
            for ik, mids in SEC_EXPORT_MAP.items():
                for mid in mids:
                    smid_indicators.setdefault(mid, []).append(ik)

        # 기관+외인 수급 보너스 (V-RECOVERY 채택 신호 교차이식)
        gc_flow_map: Dict[str, Dict[str, float]] = {}
        if gc_flow_bonus_pt is not None and sd:
            for r in conn.execute(
                "SELECT stock_code, date, COALESCE(inst_net_buy,0)+COALESCE(frn_net_buy,0) "
                "FROM price_history WHERE date>=? AND date<=? AND close>0 AND stock_code IN ({})".format(
                    ",".join("?" * len(sd))),
                [warmup_start, end_date] + list(sd.keys())).fetchall():
                gc_flow_map.setdefault(r[0], {})[r[1]] = float(r[2] or 0)

        def _gc_flow_positive(code: str, day: str) -> bool:
            m = gc_flow_map.get(code)
            if not m:
                return False
            i0 = didx[code].get(day)
            if i0 is None:
                return False
            days5 = sd[code]['d'][max(0, i0 - 4):i0 + 1]
            vals = [m[d5] for d5 in days5 if d5 in m]
            return bool(vals) and sum(vals) > 0

        def _sector_export_hot(code: str, day: str) -> bool:
            """종목 sector_mid에 매핑된 관세청 수출지표(2개월 시차 as-of) YoY > +5% 여부."""
            iks = smid_indicators.get(smid_of.get(code, ""))
            if not iks:
                return False
            y, mo = int(day[:4]), int(day[5:7])
            mo -= 2
            if mo <= 0:
                mo += 12; y -= 1
            ym_ref, ym_prev = f"{y:04d}-{mo:02d}", f"{y-1:04d}-{mo:02d}"
            for ik in iks:
                m = sec_export_series.get(ik)
                if not m:
                    continue
                cur, prev = m.get(ym_ref), m.get(ym_prev)
                if cur and prev and prev > 0 and cur / prev - 1 > 0.05:
                    return True
            return False

        def _export_yoy_positive(code: str, day: str) -> bool:
            m = export_map.get(code)
            if not m:
                return False
            y, mo = int(day[:4]), int(day[5:7])
            mo -= 2  # 공표 시차 2개월
            if mo <= 0:
                mo += 12; y -= 1
            ym_ref = f"{y:04d}-{mo:02d}"
            ym_prev = f"{y-1:04d}-{mo:02d}"
            cur, prev = m.get(ym_ref), m.get(ym_prev)
            if not cur or not prev or prev <= 0:
                return False
            return cur / prev - 1 > 0

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        nm: Dict[str, int] = {}
        eq_peak = cash          # MDD 추적 (2026-07-12)
        max_dd = 0.0
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        def _gc_equity(day: str) -> float:
            return cash + sum(
                p['qty'] * (sd[code]['c'][didx[code][day]] if day in didx[code] else p['entry'])
                for code, p in pos.items()
            )

        def _gc_limit(day: str) -> int:
            return max(max_positions, int(_gc_equity(day) // per_stock))

        for day in sim_dates:
            ym = day[:7]

            # 전일 종가 이후 생성된 주문을 다음 거래일 시가에 체결한다.
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos: continue
                p = pos.pop(code)
                fill = sd[code]['o'][i]
                net_amt, net_pct = _net_profit(
                    p['entry'], fill, p['qty'], p.get('mkt_cap_억', sd[code].get('mkt_cap_억', 500)))
                cash += p['entry'] * p['qty'] + net_amt
                trades.append({'stock_code': code, 'entry_date': p['entry_date'],
                               'exit_date': day, 'entry_price': p['entry'],
                               'exit_price': fill, 'qty': p['qty'],
                               'profit_pct': net_pct, 'profit_amt': net_amt,
                               'hold_days': p.get('hold', 0), 'exit_reason': reason})
                del pending_sells[code]

            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None: continue
                if code not in pos and len(pos) < _gc_limit(day):
                    fill = sd[code]['o'][i]
                    qty = int(min(per_stock, cash) // fill)
                    if qty > 0:
                        cash -= qty * fill
                        entry_mktcap = sd[code].get('mkt_cap_억', 500)
                        if asof_mktcap:
                            entry_shares = _gc_shares_asof(code, day)
                            if entry_shares > 0:
                                entry_mktcap = entry_shares * fill / 1e8
                        pos[code] = {'entry': fill, 'entry_date': day, 'qty': qty,
                                     'hold': 0, 'peak': fill,
                                     'mkt_cap_억': entry_mktcap}
                        nm[ym] = nm.get(ym, 0) + 1
                pending_buys.remove(code)

            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None: continue
                c = sd[code]['c'][i]
                entry = p['entry']
                pct = (c - entry) / entry
                hold = p.get('hold', 0)
                peak = p.get('peak', entry)
                if c > peak: p['peak'] = c; peak = c
                trail = (c - peak) / peak if peak > 0 else 0
                reason = None
                if pct <= stop:
                    reason = '손절'
                elif hold >= 10:
                    cur_trail = trail_big if (pct > big_gate) else trail_pct
                    if pct > 0.05 and trail <= cur_trail:
                        reason = 'Trail'
                # V-GC 데스크로스 테스트: avg5 +50%→-9.6% 급락으로 제거 (trail stop이 더 효과적)
                # 고점 컨플루언스 청산 (2026-07-18 공통모듈)
                if reason is None and chart_confluence and pct >= 0.10:
                    s_ = sd[code]
                    if _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN:
                        reason = 'chart_top'
                if reason is None and hold >= max_hold:
                    reason = '만료'
                p['hold'] = hold + 1
                if reason:
                    pending_sells[code] = reason
                elif (pyramid_gain is not None and not p.get('pyr')
                      and pct >= pyramid_gain and hold >= 10):
                    # 피라미딩: 이기는 포지션에 0.5티켓 추가 (1회, 평균단가 블렌딩)
                    add_qty = int(per_stock * 0.5 / c)
                    if add_qty > 0 and add_qty * c <= cash:
                        cash -= add_qty * c
                        p['entry'] = (entry * p['qty'] + c * add_qty) / (p['qty'] + add_qty)
                        p['qty'] += add_qty
                        p['pyr'] = True
            # 진입 (매월 max_new_per_month건 제한)
            if (
                _market_gate_open(day)
                and len(pos) + len(pending_buys) < _gc_limit(day)
                and nm.get(ym, 0) < max_new_per_month
            ):
                hot_map = _hot_sectors_of(day) if hot_sector_boost else {}
                cands = []
                for code, s in sd.items():
                    if code in pos or code in pending_buys or code in pending_sells: continue
                    i = didx[code].get(day)
                    if i is None or i < 145: continue
                    c = s['c'][i]
                    if c < 1000: continue
                    if asof_mktcap:
                        sh = _gc_shares_asof(code, day)
                        if sh <= 0 or sh * c / 1e8 < min_mktcap:
                            continue
                    ma20 = _ma(s['c'][:i+1], 20)
                    ma60 = _ma(s['c'][:i+1], 60)
                    if not (ma20 and ma60 and ma20 > ma60): continue
                    # 최근 cross_days 내 골든크로스 발생 체크
                    crossed = False
                    for back in range(2, cross_days + 1):
                        if i < back: break
                        m20b = _ma(s['c'][:i-back+1], 20)
                        m60b = _ma(s['c'][:i-back+1], 60)
                        if m20b and m60b and m20b <= m60b:
                            crossed = True; break
                    if not crossed: continue
                    # 거래량 확인
                    v5 = sum(s['v'][max(0, i-4):i+1]) / 5
                    v20 = sum(s['v'][max(0, i-19):i+1]) / 20
                    if v20 <= 0 or v5 < v20 * vol_ratio: continue
                    # RS6M
                    if i < 126: continue
                    prev126 = s['c'][i - 126]
                    if prev126 <= 0: continue
                    k6m = _get_k6m(day)
                    if k6m is None: continue
                    rs6m = (c / prev126 - 1) * 100 - k6m
                    if rs6m < rs6m_min: continue
                    # 과열 회피 (2026-07-13 실증: 60일 +100% 급등 종목의 6개월 -30%하락률 37~41%, 기준율 3.6배)
                    if avoid_overheat is not None and i >= 40:
                        _c40 = s['c'][i - 40]
                        if _c40 > 0 and (c / _c40 - 1) > avoid_overheat:
                            continue
                    # 섹터 보너스: BUY 섹터 종목 우선순위 상승
                    sector_bonus = 10.0 if _get_stock_sector_key(code) else 0.0
                    if sector_bonus > 0 and _is_sector_buy(conn, code, day, threshold=55.0):
                        sector_bonus = 25.0  # BUY 섹터 종목에게 RS6M +25pt 보너스
                    if hot_sector_boost:
                        hr = hot_map.get(sec_of.get(code, ""))
                        if hr == 1:
                            sector_bonus += hot_boost_1
                        elif hr is not None:
                            sector_bonus += hot_boost_2
                    if prog_bonus_pt is not None and _prog_positive(code, day):
                        sector_bonus += prog_bonus_pt
                    if export_bonus_pt is not None and _export_yoy_positive(code, day):
                        sector_bonus += export_bonus_pt
                    if sector_export_bonus_pt is not None and _sector_export_hot(code, day):
                        sector_bonus += sector_export_bonus_pt
                    if gc_flow_bonus_pt is not None and _gc_flow_positive(code, day):
                        sector_bonus += gc_flow_bonus_pt
                    # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                    if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                        continue
                    cands.append((code, c, rs6m + sector_bonus))

                cands.sort(key=lambda x: -x[2])  # RS6M+섹터보너스 내림차순
                available = max(0, _gc_limit(day) - len(pos) - len(pending_buys))
                queued_now = 0
                for code, price, _ in cands:
                    if queued_now >= available: break
                    if nm.get(ym, 0) + queued_now >= max_new_per_month: break
                    pending_buys.append(code)
                    queued_now += 1

            # 일별 에쿼티 마킹 → MDD
            mark = _gc_equity(day)
            if mark > eq_peak:
                eq_peak = mark
            elif eq_peak > 0:
                dd = (mark - eq_peak) / eq_peak * 100
                if dd < max_dd:
                    max_dd = dd

        # 잔존 포지션 청산
        for code, p in pos.items():
            c = sd[code]['c'][-1]
            last_date = sd[code]['d'][-1]
            net_amt, net_pct = _net_profit(p['entry'], c, p['qty'],
                                           p.get('mkt_cap_억', sd[code].get('mkt_cap_억', 500)))
            cash += p['entry'] * p['qty'] + net_amt
            trades.append({
                'stock_code': code,
                'entry_date': p['entry_date'],
                'exit_date': last_date,
                'entry_price': p['entry'],
                'exit_price': c,
                'qty': p['qty'],
                'profit_pct': net_pct,
                'profit_amt': net_amt,
                'hold_days': p.get('hold', 0),
                'exit_reason': '잔존',
            })

        # 성과 집계
        total_cap = per_stock * max_positions
        total_ret_pct = round((cash - total_cap) / total_cap * 100, 2)
        n_trades = len(trades)
        win_trades = sum(1 for t in trades if t['profit_pct'] > 0)
        win_rate = round(win_trades / max(n_trades, 1) * 100, 1)
        avg_pct = round(sum(t['profit_pct'] for t in trades) / max(n_trades, 1), 2)
        exit_reasons = {}
        for t in trades:
            exit_reasons[t['exit_reason']] = exit_reasons.get(t['exit_reason'], 0) + 1
        days = max((datetime.strptime(end_date, '%Y-%m-%d')
                    - datetime.strptime(start_date, '%Y-%m-%d')).days, 1)
        cagr = round(((cash / total_cap) ** (365.0 / days) - 1) * 100, 2)

        summary = (
            f"전략: V-GC 골든크로스 모멘텀  |  기간: {start_date}~{end_date}\n"
            f"진입: MA20골든크로스MA60({cross_days}일내) + 거래량{vol_ratio}배 + RS6M>{rs6m_min:.0f}\n"
            f"매도: Trail{trail_pct*100:.0f}%/Trail{trail_big*100:.0f}%(50%이익이상) + 손절{stop*100:.0f}%\n"
            f"대상: 시총{min_mktcap:.0f}억+ KOSPI/KOSDAQ / 엄격 다음날시가·정수주식·실제현금·동적슬롯\n"
            f"총 거래: {n_trades}건  승률: {win_rate}%  평균: {avg_pct:+.1f}%\n"
            f"총수익률: {total_ret_pct:+.2f}%  CAGR: {cagr:+.2f}%  MDD: {max_dd:.1f}%\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )

        conn.close()
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        # 종목명 매핑
        all_codes = list({t['stock_code'] for t in trades})
        name_map: Dict[str, str] = {}
        for i in range(0, len(all_codes), 100):
            batch = all_codes[i:i+100]
            ph = ','.join('?' * len(batch))
            for sc, sn in conn2.execute(
                f"SELECT stock_code, stock_name FROM stock_universe WHERE stock_code IN ({ph})",
                batch
            ).fetchall():
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        conn2.execute("""
            UPDATE backtest_runs SET
                status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?, strategy=?,
                max_drawdown_pct=?
            WHERE run_id=?
        """, (total_ret_pct, cagr, win_rate, n_trades, win_trades, summary,
              json.dumps(trades, ensure_ascii=False), 'golden_cross',
              round(max_dd, 2), run_id))
        conn2.commit()
        conn2.close()
        _register_execution_artifacts(run_id, total_cap, cash)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=120)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                       (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise



# ─────────────────────────────────────────────────────────────────────
# V-SECTOR: 섹터 로테이션 집중 투자 전략
# 섹터 BUY 신호(점수 >= 65) 발생 시 해당 섹터 급등 후보 TOP3 집중 투자
# 급등점수 = 영업이익YoY(40) + 기관집중도%(30) + 52주저점(20) + 소형주(10)
# ─────────────────────────────────────────────────────────────────────

# 섹터 그룹 정의 (routes/sector_rotation.py 와 동기화)
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

def _sector_score_as_of(conn: sqlite3.Connection, sector_key: str, as_of: str) -> float:
    """백테스트용 — 특정 날짜 기준 섹터 점수 계산 (간소화 버전).
    외인3M + 기관3M 수급 + 영업이익YoY + 섹터 3M 가격 모멘텀을 실제 DB 데이터로 계산.
    """
    info = _SECTOR_GROUPS.get(sector_key, {})
    codes = info.get("codes", [])
    if not codes:
        return 0.0

    d_3m   = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=92)).strftime("%Y-%m-%d")
    d_1y   = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    d_2y   = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=765)).strftime("%Y-%m-%d")
    ph_sql = "({})".format(",".join("?" * len(codes)))

    frn_3m = (conn.execute(
        f"SELECT SUM(CASE WHEN COALESCE(frn_net_buy_amt,0) != 0 "
        f"THEN frn_net_buy_amt/100.0 ELSE COALESCE(frn_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
        f"WHERE stock_code IN {ph_sql} AND date>=? AND date<=? "
        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
        codes + [d_3m, as_of]
    ).fetchone() or (0,))[0] or 0.0

    inst_3m = (conn.execute(
        f"SELECT SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0 "
        f"THEN inst_net_buy_amt/100.0 ELSE COALESCE(inst_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
        f"WHERE stock_code IN {ph_sql} AND date>=? AND date<=? "
        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
        codes + [d_3m, as_of]
    ).fetchone() or (0,))[0] or 0.0

    # 영업이익 YoY (섹터 합산)
    cur_year  = str(int(as_of[:4]))
    prev_year = str(int(as_of[:4]) - 1)
    op_cur = (conn.execute(
        f"SELECT SUM(operating_profit) FROM financial_data "
        f"WHERE stock_code IN {ph_sql} AND is_annual=1 AND year=?",
        codes + [cur_year]
    ).fetchone() or (None,))[0]
    op_prev = (conn.execute(
        f"SELECT SUM(operating_profit) FROM financial_data "
        f"WHERE stock_code IN {ph_sql} AND is_annual=1 AND year=?",
        codes + [prev_year]
    ).fetchone() or (None,))[0]

    op_yoy = 0.0
    if op_cur and op_prev and op_prev != 0:
        op_yoy = (op_cur - op_prev) / abs(op_prev) * 100

    ret3_values = []
    for code in codes:
        p_now = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, as_of),
        ).fetchone()
        p_3m = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, d_3m),
        ).fetchone()
        if p_now and p_3m and p_3m[0]:
            ret3_values.append((float(p_now[0]) / float(p_3m[0]) - 1) * 100)
    sector_ret3 = sorted(ret3_values)[len(ret3_values) // 2] if ret3_values else 0.0

    score = 0.0
    # 외인 (max 40)
    if   frn_3m >= 30000: score += 40
    elif frn_3m >= 10000: score += 35
    elif frn_3m >=  5000: score += 30
    elif frn_3m >=  1500: score += 22
    elif frn_3m >=   300: score += 12
    elif frn_3m < -5000:  score -= 10
    # 기관 (max 35)
    if   inst_3m >= 20000: score += 35
    elif inst_3m >= 10000: score += 28
    elif inst_3m >=  3000: score += 20
    elif inst_3m >=  1000: score += 12
    elif inst_3m >=   200: score += 6
    elif inst_3m < -3000:  score -= 8
    # 영업이익YoY (max 25)
    if   op_yoy >= 100: score += 25
    elif op_yoy >= 50:  score += 18
    elif op_yoy >= 20:  score += 10
    elif op_yoy >= 0:   score += 4
    elif op_yoy < -30:  score -= 8
    # 섹터 장세 자체가 강하면 지수 레벨과 무관하게 들어갈 수 있도록 반영
    if   sector_ret3 >= 30: score += 25
    elif sector_ret3 >= 20: score += 18
    elif sector_ret3 >= 10: score += 10
    elif sector_ret3 >= 5:  score += 5
    elif sector_ret3 < -10: score -= 8

    return round(score, 1)


def run_backtest_sector(
    start_date: str, end_date: str,
    buy_threshold: float = 55.0,    # 섹터 BUY 기준 점수
    exit_threshold: float = 30.0,   # 섹터 EXIT 기준 점수
    rebalance_days: int = 22,        # 월 1회 리밸런싱 (22 영업일)
    per_stock: float = 10_000_000,
    max_positions: int = 9,          # 최대 3섹터 × 3종목
    stop: float = -0.12,
    trail: float = -0.30,  # 2026-07-21 -0.20→-0.30: 연속운용(2020-03~2026-03) 227.89%→245.02%, 승률46.3→46.7%, 거래175→165건(조기청산 감소)
    tp: float = 0.50,
    min_sector_hold_days: int = 44,   # 섹터 점수 재계산 후 하락해도 최소 2개월은 보유
    pick_ta_bonus: float = None,      # 리더 선정 점수에 직전 공시분기 첫 흑자전환 보너스 (예: 20.0, 실험용)
    # 2026-08-09 실험(사용자 제안): 유휴자본 문제를 티켓크기/컴포넌트수로 풀려던 시도가
    # 전부 실패(동점경쟁 불안정성/슬롯감소, ledger 'ticket_pct_reverify_after_normalization_20260809')한 뒤
    # 방향 전환 — 신규 슬롯 경쟁이 아니라 "이미 보유한 포지션의 확신도(섹터점수)가 매수
    # 시점보다 오를 때만" 추가 투입. 슬롯 경쟁 메커니즘 자체를 건드리지 않아 동점
    # 타이브레이크 불안정성과 무관(구조적으로 다른 메커니즘).
    pyramid_score_gain: float = None,     # 예: 15.0 — 진입시점 섹터점수 대비 +N점 오르면 추가매수
    pyramid_add_pct: float = 0.5,         # 추가매수 규모(기존 티켓 대비 비율, 기본 0.5=절반 티켓)
    pyramid_max_adds: int = 2,            # 포지션당 최대 추가매수 횟수(무한 물타기 방지)
    # 2026-08-23: 전체 price_history 스캔에서 214개 거래일·1,267건의 단일일 스파이크(익일
    # 원상복귀) 데이터 아티팩트 발견(2022-01-03 하루 254종목 동시발생 등, 데이터 수집/정합성
    # 문제로 강하게 의심). 매수후보 3M모멘텀 계산 시점 직전에 이런 아티팩트가 있으면 후보에서
    # 제외하는 실험 파라미터 — 기본 False(기존 동작 완전 동일), 실측 검증 후 채택 여부 결정.
    avoid_discontinuity: bool = False,
    strict_exec: bool = True,         # 2026-07-13 기본화 (Codex 계약): D종가 신호 → D+1 시가 체결.
                                      # 검증: same_close avg6 +29.2%(5/6) → next_open +31.4%(5/6) — 전략 유효성 유지.
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-SECTOR: 섹터 로테이션 집중 투자 전략.
    - 월 1회 섹터 스코어 계산 → BUY 섹터 발굴
    - BUY 섹터 내 급등점수 TOP3 종목 집중 매수
    - 손절 -12%, 추적손절 -30%(2026-07-21 -20%→-30%), 익절 +50%
    - 섹터 점수가 EXIT 이하로 하락해도 최소 보유기간 전에는 섹터 청산 보류
    """
    init_backtest_db()
    run_name = run_name or f"V-SECTOR섹터집중 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "sector_focus", "sector_v3_cashledger_20260714",
        {"buy_threshold": buy_threshold, "exit_threshold": exit_threshold,
         "rebalance_days": rebalance_days, "stop": stop, "trail": trail, "tp": tp,
         "min_sector_hold_days": min_sector_hold_days, "strict_exec": strict_exec,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode="asof_approx",  # 2026-08-13: 리더선정 스코어(sel_score)의 기관집중도
        # 계산에 쓰이는 시총을 security_share_history 기반 정확한 as-of 값으로 교체(6기간
        # 재검증 avg6 29.98%→27.62%, 5/6양수 유지 — 소폭변동, 일부기간 오히려 개선).
        # ⚠️ 단, _SECTOR_GROUPS 자체(10업종 70종목 후보군)는 여전히 현재시점 수동선정이라
        # "pit"(완전 PIT) 등급까지는 도달 불가 — approx로 정직하게 표기.
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'sector_focus',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.execute("""
        UPDATE backtest_runs
        SET name=?, strategy='sector_focus', start_date=?, end_date=?, per_stock=?, max_pos=?, status='running'
        WHERE run_id=?
    """, (run_name, start_date, end_date, per_stock, max_positions, run_id))
    conn.commit()

    try:
        # KOSPI 데이터
        kospi_rows = conn.execute(
            "SELECT date, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date"
        ).fetchall()
        k_dates  = [r[0] for r in kospi_rows]
        k_prices = {r[0]: float(r[1]) for r in kospi_rows}

        # 모든 섹터 후보 종목 모음 (가격 데이터 로드용)
        all_codes = list(set(c for info in _SECTOR_GROUPS.values() for c in info["codes"]))

        # 가격 데이터 로드
        price_data: dict = {}  # code → {date: (close, high, low)}
        rows_p = conn.execute(
            "SELECT stock_code, date, close, high, low, open FROM price_history "
            "WHERE stock_code IN ({}) AND date>=? AND date<=? AND close>0 ORDER BY date".format(
                ",".join("?" * len(all_codes))),
            all_codes + [start_date, end_date]
        ).fetchall()
        for r in rows_p:
            c, d, cl, hi, lo, op = r
            if c not in price_data:
                price_data[c] = {}
            price_data[c][d] = (float(cl), float(hi) if hi else float(cl), float(lo) if lo else float(cl),
                                float(op) if op and op > 0 else float(cl))

        # 영업일 목록
        trade_dates = sorted(set(r[1] for r in rows_p if r[1] >= start_date))

        # 시총 맵 (거래비용 슬리피지 티어용)
        mc_map = {r[0]: float(r[1] or 1000) for r in conn.execute(
            "SELECT stock_code, market_cap FROM stock_universe WHERE stock_code IN ({})".format(
                ",".join("?" * len(all_codes))), all_codes).fetchall()}

        # 2026-08-13: 섹터 리더 선정(sel_score)의 기관집중도 계산이 stock_universe.
        # market_cap(현재시총)을 그대로 쓰고 있었음 — _SECTOR_GROUPS 후보군 자체는
        # 여전히 현재시점 수동선정이라 완전한 PIT화는 불가능(2026-07-21/2026-08-12
        # 기존 판정)하지만, 리더 "선정 스코어" 계산에 쓰이는 시총만큼은 정확한
        # as-of 값(security_share_history)으로 교체 가능 — 부분 개선 시도.
        sector_share_intervals: Dict[str, list] = {}
        for code, effective_from, effective_to, shares, quality in conn.execute(
            """SELECT stock_code,effective_from,effective_to,shares_issued,quality
               FROM security_share_history WHERE stock_code IN ({})
               ORDER BY stock_code,effective_from""".format(",".join("?" * len(all_codes))), all_codes
        ):
            sector_share_intervals.setdefault(code, []).append(
                (effective_from, effective_to, float(shares or 0), quality)
            )

        def _shares_asof_sector(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(sector_share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        # 포지션 관리
        # positions: dict[code] → {buy_price, peak, sector, qty}
        positions: dict = {}
        # C1 (2026-07-14, Codex 필수점검): 실현손익 누산기 → 실제 현금원장으로 전환.
        # 1억원 시작, 매수 시 현금 차감(부족 시 주문 거부), 매도 시 원금+순손익(_net_profit:
        # 수수료+거래세+슬리피지 차감) 환입. 수익률 = (최종에쿼티/1억 - 1).
        initial_cash = per_stock * max_positions
        cash = initial_cash
        holding_value = 0.0
        all_trades: list = []
        sector_assignments: dict = {}  # code → sector_key (현재 보유 섹터)

        last_rebalance = ""
        sector_scores_cache: dict = {}  # date → {sector_key: score}
        sector_momentum_cache: dict = {}  # date → {sector_key: {ret1, ret3}}
        sec_pending_sells: list = []  # strict_exec: (code, reason)
        sec_pending_buys: list = []   # strict_exec: (code, sector_key, meta)

        for i, trade_date in enumerate(trade_dates):
            # ── strict_exec: 전일 신호 → 오늘 시가 체결 ──
            if strict_exec:
                _still = []
                for code, reason in sec_pending_sells:
                    if code not in positions:
                        continue
                    pdata = price_data.get(code, {}).get(trade_date)
                    if pdata is None:
                        _still.append((code, reason)); continue
                    px = pdata[3]
                    pos = positions.pop(code)
                    sector_assignments.pop(code, None)
                    _pnl_amt, _net = _net_profit(pos["buy_price"], px, pos.get("qty", 1), mc_map.get(code, 1000))
                    cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                    all_trades.append({"date": trade_date, "code": code, "action": "SELL",
                                       "price": px, "pnl_pct": round(_net, 2), "reason": reason})
                sec_pending_sells = _still
                for code, sector_key, meta in sec_pending_buys:
                    if code in positions or len(positions) >= max_positions:
                        continue
                    pdata = price_data.get(code, {}).get(trade_date)
                    if pdata is None:
                        continue  # 당일 미거래 → 주문 만료
                    px = pdata[3]
                    budget = min(per_stock, cash * 0.99)
                    qty = int(budget / px)
                    if qty < 1 or qty * px > cash:
                        continue  # 현금 부족 → 주문 거부 (현금 음수 금지)
                    cash -= qty * px
                    positions[code] = {"buy_price": px, "peak": px, "qty": qty,
                                       "sector": sector_key, "entry_date": trade_date,
                                       "entry_sector_score": meta.get("sector_score", 0), "pyramid_adds": 0}
                    sector_assignments[code] = sector_key
                    all_trades.append({"date": trade_date, "code": code, "action": "BUY",
                                       "price": px, "sector": sector_key, **meta})
                sec_pending_buys = []
            # ─────── 보유 종목 현재가 업데이트 & 매도 체크 ───────
            to_sell = []
            for code, pos in list(positions.items()):
                pdata = price_data.get(code, {}).get(trade_date)
                if pdata is None:
                    continue
                cur = pdata[0]
                peak = max(pos["peak"], cur)
                positions[code]["peak"] = peak

                ret = cur / pos["buy_price"] - 1
                trail_cur = (peak - pos["buy_price"]) / pos["buy_price"]
                trail_dd  = (cur - peak) / peak

                sell_reason = None
                if ret <= stop:
                    sell_reason = f"손절{ret*100:.1f}%"
                elif trail_cur > 0.05 and trail_dd <= trail:
                    sell_reason = f"추적손절{trail_dd*100:.1f}%"
                elif ret >= tp:
                    sell_reason = f"익절{ret*100:.1f}%"

                if sell_reason:
                    to_sell.append((code, cur, sell_reason))

            if strict_exec:
                _queued = {c for c, _ in sec_pending_sells}
                for code, sell_price, reason in to_sell:
                    if code not in _queued:
                        sec_pending_sells.append((code, reason))
            else:
                for code, sell_price, reason in to_sell:
                    pos = positions.pop(code)
                    sector_assignments.pop(code, None)
                    _pnl_amt, _net = _net_profit(pos["buy_price"], sell_price, pos.get("qty", 1), mc_map.get(code, 1000))
                    cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                    all_trades.append({
                        "date": trade_date, "code": code, "action": "SELL",
                        "price": sell_price, "pnl_pct": round(_net, 2), "reason": reason
                    })

            # ─────── 월 1회 섹터 리밸런싱 ───────
            if i % rebalance_days == 0:
                # 섹터 점수 계산
                scores = {}
                momentum = {}
                for sk in _SECTOR_GROUPS:
                    # 간소화: inst/frn 집계 + op_yoy
                    codes_s = _SECTOR_GROUPS[sk]["codes"]
                    ph_s = "({})".format(",".join("?" * len(codes_s)))
                    d_3m = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=92)).strftime("%Y-%m-%d")

                    frn_s = (conn.execute(
                        f"SELECT SUM(CASE WHEN COALESCE(frn_net_buy_amt,0) != 0 "
                        f"THEN frn_net_buy_amt/100.0 ELSE COALESCE(frn_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
                        f"WHERE stock_code IN {ph_s} AND date>=? AND date<=? "
                        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
                        codes_s + [d_3m, trade_date]
                    ).fetchone() or (0,))[0] or 0.0

                    inst_s = (conn.execute(
                        f"SELECT SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0 "
                        f"THEN inst_net_buy_amt/100.0 ELSE COALESCE(inst_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
                        f"WHERE stock_code IN {ph_s} AND date>=? AND date<=? "
                        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
                        codes_s + [d_3m, trade_date]
                    ).fetchone() or (0,))[0] or 0.0

                    # OP YoY (섹터 내 종목 중위값)
                    cur_yr = str(int(trade_date[:4]))
                    prv_yr = str(int(trade_date[:4]) - 1)
                    op_rows_s = conn.execute(
                        f"SELECT stock_code, operating_profit FROM financial_data "
                        f"WHERE stock_code IN {ph_s} AND year=? AND is_annual=1 AND operating_profit IS NOT NULL",
                        codes_s + [cur_yr]
                    ).fetchall()
                    op_prev_s = {r[0]: r[1] for r in conn.execute(
                        f"SELECT stock_code, operating_profit FROM financial_data "
                        f"WHERE stock_code IN {ph_s} AND year=? AND is_annual=1 AND operating_profit IS NOT NULL",
                        codes_s + [prv_yr]
                    ).fetchall()}
                    yoys = []
                    for code_s, op_c in op_rows_s:
                        op_p = op_prev_s.get(code_s)
                        if op_p and op_p != 0:
                            raw = (op_c - op_p) / abs(op_p) * 100
                            yoys.append(min(max(raw, -200), 2000))
                    med_yoy = sorted(yoys)[len(yoys)//2] if yoys else 0.0

                    ret3_values = []
                    ret1_values = []
                    for code_s in codes_s:
                        p_now_s = price_data.get(code_s, {}).get(trade_date)
                        p_3m_s = None
                        for d_back in range(92, 100):
                            d_try = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=d_back)).strftime("%Y-%m-%d")
                            if d_try in price_data.get(code_s, {}):
                                p_3m_s = price_data[code_s][d_try]
                                break
                        if p_now_s and p_3m_s and p_3m_s[0] > 0:
                            ret3_values.append((p_now_s[0] / p_3m_s[0] - 1) * 100)
                        p_1m_s = None
                        for d_back in range(28, 36):
                            d_try = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=d_back)).strftime("%Y-%m-%d")
                            if d_try in price_data.get(code_s, {}):
                                p_1m_s = price_data[code_s][d_try]
                                break
                        if p_now_s and p_1m_s and p_1m_s[0] > 0:
                            ret1_values.append((p_now_s[0] / p_1m_s[0] - 1) * 100)
                    sector_ret3 = sorted(ret3_values)[len(ret3_values)//2] if ret3_values else 0.0
                    sector_ret1 = sorted(ret1_values)[len(ret1_values)//2] if ret1_values else 0.0
                    momentum[sk] = {"ret1": round(sector_ret1, 1), "ret3": round(sector_ret3, 1)}

                    sc = 0.0
                    if   frn_s >= 30000: sc += 35
                    elif frn_s >= 10000: sc += 30
                    elif frn_s >=  5000: sc += 24
                    elif frn_s >=  1500: sc += 18
                    elif frn_s >=   300: sc += 10
                    elif frn_s <  -5000: sc -= 8
                    if   inst_s >= 20000: sc += 30
                    elif inst_s >= 10000: sc += 24
                    elif inst_s >=  3000: sc += 16
                    elif inst_s >=  1000: sc += 10
                    elif inst_s >=   200: sc += 5
                    elif inst_s <  -3000: sc -= 7
                    if   med_yoy >= 100: sc += 25
                    elif med_yoy >= 50:  sc += 18
                    elif med_yoy >= 20:  sc += 10
                    elif med_yoy >= 0:   sc += 4
                    elif med_yoy < -30:  sc -= 6
                    if   sector_ret3 >= 30: sc += 25
                    elif sector_ret3 >= 20: sc += 18
                    elif sector_ret3 >= 10: sc += 10
                    elif sector_ret3 >= 5:  sc += 5
                    elif sector_ret3 < -10: sc -= 8
                    scores[sk] = round(sc, 1)

                sector_scores_cache[trade_date] = scores
                sector_momentum_cache[trade_date] = momentum

                # BUY 섹터 → 기존 보유 중 EXIT 대상 청산
                for code in list(positions.keys()):
                    sec = sector_assignments.get(code)
                    if sec and scores.get(sec, 0) < exit_threshold:
                        pdata = price_data.get(code, {}).get(trade_date)
                        if pdata:
                            entry_date = positions[code].get("entry_date")
                            hold_days = (
                                datetime.strptime(trade_date, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")
                            ).days if entry_date else 999
                            if hold_days < min_sector_hold_days:
                                continue
                            pos = positions.pop(code)
                            sector_assignments.pop(code, None)
                            sell_p = pdata[0]
                            _pnl_amt, pnl = _net_profit(pos["buy_price"], sell_p, pos.get("qty", 1), mc_map.get(code, 1000))
                            cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                            all_trades.append({
                                "date": trade_date, "code": code, "action": "SECTOR_EXIT",
                                "price": sell_p, "pnl_pct": round(pnl, 2),
                                "reason": f"섹터점수하락{scores.get(sec,0):.0f}→EXIT(보유{hold_days}일)"
                            })

                # ── 확신도 상승 시 추가매수(피라미딩, 사용자 제안 2026-08-09) ──
                # 신규 슬롯 경쟁이 아니라 "이미 보유 중인 포지션"에만 자본을 더 태우므로
                # 동점 타이브레이크 불안정성과 무관 — position_limit/슬롯 수를 전혀 건드리지 않음.
                if pyramid_score_gain is not None:
                    for code, pos in list(positions.items()):
                        sec = sector_assignments.get(code)
                        if not sec:
                            continue
                        cur_score = scores.get(sec, 0)
                        entry_score = pos.get("entry_sector_score", 0)
                        if pos.get("pyramid_adds", 0) >= pyramid_max_adds:
                            continue
                        if cur_score < entry_score + pyramid_score_gain:
                            continue
                        pdata = price_data.get(code, {}).get(trade_date)
                        if not pdata:
                            continue
                        add_px = pdata[0]
                        add_budget = min(per_stock * pyramid_add_pct, cash * 0.99)
                        add_qty = int(add_budget / add_px)
                        if add_qty < 1 or add_qty * add_px > cash:
                            continue  # 현금 부족 → 스킵(음수 금지)
                        cash -= add_qty * add_px
                        old_qty = pos["qty"]
                        new_qty = old_qty + add_qty
                        # 가중평균 단가로 원가 재계산 — 이후 손절/추적손절/익절 판단이 이 기준으로 이뤄짐
                        pos["buy_price"] = (pos["buy_price"] * old_qty + add_px * add_qty) / new_qty
                        pos["qty"] = new_qty
                        pos["entry_sector_score"] = cur_score  # 다음 추가매수는 이 시점 대비 재상승 요구
                        pos["pyramid_adds"] = pos.get("pyramid_adds", 0) + 1
                        all_trades.append({
                            "date": trade_date, "code": code, "action": "PYRAMID_ADD",
                            "price": add_px, "sector": sec,
                            "reason": f"섹터점수상승{entry_score:.0f}→{cur_score:.0f}(+{cur_score-entry_score:.0f}) 추가매수#{pos['pyramid_adds']}",
                        })

                # BUY 섹터 발굴 → RS 리더 선택 (섹터 확정 시 3M 모멘텀 리더 매수)
                buy_sectors = sorted([sk for sk, sc in scores.items() if sc >= buy_threshold],
                                     key=lambda sk: -scores[sk])

                def _price_discontinuity_recent(conn, code, as_of, window=6, threshold=0.40):
                    """2026-08-23: 전체 price_history 스캔에서 확인된 데이터 아티팩트(단일일
                    스파이크 후 익일 원상복귀, 214개 거래일에 걸쳐 1,267건 — 2022-01-03 하루에만
                    254개 종목 동시발생 등 계정/수집 오류로 강하게 의심됨, 2026-08-22 stockeasy
                    _price_discontinuity()와 동일 원리)이 매수후보 선정 시점(as_of) 직전 며칠 내에
                    있으면 해당 종목의 3M모멘텀(rs3m)·기관집중도 계산이 오염됐을 수 있어 후보에서
                    제외한다. 진짜 급등/급락(분할·병합·거래재개 등)과 구분하려 하지 않고 보수적으로
                    스킵 — 매수 기회 손실 위험보다 오염된 신호로 진입하는 위험을 우선 차단."""
                    rows = conn.execute(
                        """
                        WITH p AS (
                          SELECT date, close, LAG(close) OVER(ORDER BY date) prev_close
                          FROM price_history WHERE stock_code=? AND date<=? AND close>0
                        )
                        SELECT close, prev_close FROM p
                        WHERE prev_close IS NOT NULL AND prev_close > 0
                        ORDER BY date DESC LIMIT ?
                        """,
                        (code, as_of, window),
                    ).fetchall()
                    for r in rows:
                        prev_close = float(r[1])
                        close_v = float(r[0])
                        if prev_close and abs(close_v / prev_close - 1) >= threshold:
                            return True
                    return False

                def _sector_rs_picks(conn, sector_key, as_of, top_n=3):
                    """섹터 확정 BUY 시 3개월 RS 리더 선택"""
                    codes_r = _SECTOR_GROUPS[sector_key]["codes"]
                    d3m = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=92)).strftime("%Y-%m-%d")
                    results = []
                    for c in codes_r:
                        p_now = price_data.get(c, {}).get(as_of)
                        p_3m = None
                        for d_back in range(92, 100):
                            d_try = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=d_back)).strftime("%Y-%m-%d")
                            if d_try in price_data.get(c, {}):
                                p_3m = price_data[c][d_try]
                                break
                        if not p_now or not p_3m or p_3m[0] <= 0:
                            continue
                        if avoid_discontinuity and _price_discontinuity_recent(conn, c, as_of):
                            continue
                        rs3m = (p_now[0] / p_3m[0] - 1) * 100
                        # inst_3m 수급
                        inst3m_r = (conn.execute(
                            "SELECT SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0 "
                            "THEN inst_net_buy_amt/100.0 ELSE COALESCE(inst_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
                            "WHERE stock_code=? AND date>=? AND date<=? AND (inst_net_buy_amt!=0 OR frn_net_buy_amt!=0 OR inst_net_buy!=0 OR frn_net_buy!=0)",
                            (c, d3m, as_of)
                        ).fetchone() or (0,))[0] or 0.0
                        _sh_r = _shares_asof_sector(c, as_of)
                        mktcap_r = (_sh_r * p_now[0] / 1e8) if _sh_r > 0 else 1000
                        inst_int_r = inst3m_r / max(1, mktcap_r) * 100
                        # RS 리더 점수 (3M 모멘텀 60% + 기관집중도 40%)
                        sel_score = rs3m * 0.6 + inst_int_r * 40
                        if pick_ta_bonus is not None:
                            # 직전 공시분기 첫 흑자전환 (as-of 표준 공시일정 기준, 룩어헤드 없음)
                            ni_rows = conn.execute("""
                                SELECT net_income FROM financial_data
                                WHERE stock_code=? AND is_annual=0 AND quarter BETWEEN 1 AND 4
                                  AND net_income IS NOT NULL
                                  AND (CASE WHEN quarter=1 THEN printf('%d-05-15', year)
                                            WHEN quarter=2 THEN printf('%d-08-15', year)
                                            WHEN quarter=3 THEN printf('%d-11-15', year)
                                            ELSE printf('%d-02-15', year+1) END) <= ?
                                ORDER BY year DESC, quarter DESC LIMIT 4
                            """, (c, as_of)).fetchall()
                            if (len(ni_rows) >= 2 and float(ni_rows[0][0] or 0) > 0
                                    and any(float(x[0] or 0) < 0 for x in ni_rows[1:])):
                                sel_score += pick_ta_bonus
                        results.append({"code": c, "surge_score": round(sel_score, 1), "rs3m": round(rs3m, 1),
                                        "inst_intensity": round(inst_int_r, 2), "op_yoy": None, "pos_52w": None,
                                        "sector_key": sector_key})
                    results.sort(key=lambda x: -x["surge_score"])
                    return results[:top_n]

                n_slots = max_positions - len(positions)
                for sector_key in buy_sectors[:3]:  # 최대 3섹터
                    if n_slots <= 0:
                        break
                    picks = _sector_rs_picks(conn, sector_key, trade_date, top_n=3)
                    for pk in picks:
                        if n_slots <= 0:
                            break
                        code = pk["code"]
                        if code in positions:
                            continue
                        pdata = price_data.get(code, {}).get(trade_date)
                        if not pdata:
                            continue
                        _meta = {
                            "sector_score": scores.get(sector_key, 0),
                            "sector_ret1": momentum.get(sector_key, {}).get("ret1"),
                            "sector_ret3": momentum.get(sector_key, {}).get("ret3"),
                            "surge_score": pk["surge_score"],
                            "reason": f"섹터BUY{scores.get(sector_key,0):.0f} 급등점수{pk['surge_score']}",
                        }
                        if strict_exec:
                            if code not in [c for c, _, _ in sec_pending_buys]:
                                sec_pending_buys.append((code, sector_key, _meta))
                                n_slots -= 1
                            continue
                        buy_p = pdata[0]
                        budget = min(per_stock, cash * 0.99)
                        qty = int(budget / buy_p)
                        if qty < 1 or qty * buy_p > cash:
                            continue  # 현금 부족 → 주문 거부
                        cash -= qty * buy_p
                        positions[code] = {"buy_price": buy_p, "peak": buy_p, "qty": qty,
                                           "sector": sector_key, "entry_date": trade_date,
                                           "entry_sector_score": _meta.get("sector_score", 0), "pyramid_adds": 0}
                        sector_assignments[code] = sector_key
                        all_trades.append({
                            "date": trade_date, "code": code, "action": "BUY",
                            "price": buy_p, "sector": sector_key, **_meta,
                        })
                        n_slots -= 1

        # 마지막 날 청산 (현금원장 방식)
        last_date = trade_dates[-1] if trade_dates else end_date
        for code, pos in positions.items():
            pdata = price_data.get(code, {}).get(last_date) or price_data.get(code, {})
            if isinstance(pdata, dict):
                last = sorted(pdata.keys())[-1] if pdata else None
                pdata = pdata.get(last) if last else None
            if pdata:
                sell_p = pdata[0]
                _pnl_amt, pnl = _net_profit(pos["buy_price"], sell_p, pos.get("qty", 1), mc_map.get(code, 1000))
                cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                all_trades.append({"date": last_date, "code": code, "action": "FINAL",
                                   "price": sell_p, "pnl_pct": round(pnl, 2), "reason": "종료청산"})
            else:
                # 시세 없음(거래정지 등) → 매수원금 그대로 환입하지 않고 전액 손실 처리 대신
                # 마지막 유효가 부재를 보수적으로 기록 (stale mark 방지: 원금 미환입)
                all_trades.append({"date": last_date, "code": code, "action": "FINAL",
                                   "price": None, "pnl_pct": -100.0, "reason": "시세부재(보수적 전액손실 처리)"})

        # 수익률 계산 (투자원금 기준)
        n_buy = sum(1 for t in all_trades if t["action"] == "BUY")
        n_sell = sum(1 for t in all_trades if t["action"] in ("SELL", "SECTOR_EXIT", "FINAL"))
        sell_trades = [t for t in all_trades if "pnl_pct" in t and t["action"] != "BUY"]
        avg_trade_return = sum(t["pnl_pct"] for t in sell_trades) / max(1, len(sell_trades)) if sell_trades else 0.0
        portfolio_return = (cash - initial_cash) / max(1, initial_cash) * 100  # C1: 최종 현금원장 기준
        win_rate = sum(1 for t in sell_trades if t.get("pnl_pct", 0) > 0) / max(1, len(sell_trades)) * 100

        # KOSPI 비교
        k_start = next((k_prices[d] for d in k_dates if d >= start_date), None)
        k_end   = next((k_prices[d] for d in reversed(k_dates) if d <= end_date), None)
        kospi_ret = (k_end / k_start - 1) * 100 if k_start and k_end else 0.0

        alpha = portfolio_return - kospi_ret
        summary = (f"V-SECTOR {start_date[:7]}~{end_date[:7]} | "
                   f"매수{n_buy}건 매도{n_sell}건 | 자본수익{portfolio_return:.1f}% | "
                   f"평균거래{avg_trade_return:.1f}% | 승률{win_rate:.0f}% | KOSPI대비α{alpha:+.1f}%")

        import json as _json
        conn.execute("""
            UPDATE backtest_runs SET status='done', summary_text=?,
            total_return_pct=?, win_rate=?, total_trades=?, profit_trades=?, trades_json=?
            WHERE run_id=?
        """, (
            summary,
            round(portfolio_return, 2),
            round(win_rate, 1),
            len(sell_trades),
            sum(1 for t in sell_trades if t.get("pnl_pct", 0) > 0),
            _json.dumps({
                "trades": all_trades,
                "avg_trade_return_pct": round(avg_trade_return, 2),
                "portfolio_return_pct": round(portfolio_return, 2),
                "sector_momentum_filter": "none",
            }, ensure_ascii=False),
            run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, initial_cash, cash)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=120)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise



# ══════════════════════════════════════════════════════════════
#  V-RECOVERY: 낙폭과대 반등 전략
#  데이터 근거 (2026-06-29 실증):
#    MA60 -25%+ 하방 종목 → 3배 달성률 69.2% (전체 평균 6.7%의 10배!)
#    MA60 -10~-25% 하방  → 3배 달성률 9.4%
#    52주 저점 0~15% 이내 → 3배 달성률 11.4%
#    기관/외인 강매수     → 3배 달성률 3% (음의 예측력: 이미 알려진 종목)
#  → 현재 전략들이 "MA 위 + 수급 매수" 중심인데 이게 오히려 역효과
# ══════════════════════════════════════════════════════════════

def run_backtest_recovery(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.12,
    trail: float = -0.20,
    trail_big: float = -0.25,       # 50%+ 이익 시 더 넓은 추적손절
    tp: float = 0.80,               # 80%+ 익절 (3배 노림)
    max_hold: int = 240,
    ma60_depth_min: float = -0.20,  # MA60 -20% 이상 낙폭 (80%로 넓히면 하락장 +49.2%→-0.04% 확인)
    ma60_depth_max: float = -0.65,  # 너무 깊은 낙폭 제외 (상장폐지 위험)
    pct_from_low_max: float = 40.0, # 52주 저점 대비 +40% 이내 (하락장 핵심 — 중간반등은 V-DEEP에서 담당)
    vol_ratio: float = 2.0,         # 거래량 반등 확인 (20일 평균 × 2.0배) — 실증 최적값
    hot_sector_boost: bool = False, # 실험용: 주도섹터(as-of ret20 상위) 보너스 — V-GC와 동일 기법
    turnaround_bonus: float = 20.0, # 직전 공시분기 첫 흑자전환 종목 랭킹 보너스 (2026-07-12 채택: avg6 +25.8→+26.9%, 10~30pt 전 구간 개선 강건)
    buyback_bonus: float = None,    # 최근 60일 자사주 취득공시 종목 랭킹 보너스 — 실측 개선 없음(2026-07-12 기각)
    flow_bonus: float = 20.0,       # 기관+외인 5일 순매수 양수 랭킹 보너스 (2026-07-12 채택: avg6 +26.9→+29.5%, 5/10/20pt 전 구간 개선·30pt는 6/6 붕괴)
    asof_mktcap: bool = True,       # 2026-07-13 기본화: as-of 시총 — 룩어헤드 제거. as-of 실측 avg6 +22.4%(5/6), ablation: 무보너스 +15.1/흑자전환만 +18.0/수급만 +19.3 → 두 보너스 모두 진짜 개선
    avoid_overheat: float = None,   # 실험용: 진입일 40일 수익률 +N(1.0=+100%) 초과 급등주 제외 (V-GC 채택 필터 이식)
    strict_exec: bool = True,       # 2026-07-13 기본화 (Codex 계약 §3-2): D종가 신호 → D+1 시가 체결.
                                    # 검증: same_close avg6 +22.4%(5/6) vs next_open +23.0%(5/6) — 전략 유효성 유지.
                                    # 기간분포는 이동(하락 78.5→42.9 / 최근 11.2→56.3 / 최신 15.5→3.6) — 당일종가 편향이 기간 단위론 유의미했음.
    vol_fade_exit: bool = False,    # 실험용(2026-07-17): 437건 실증 — 중앙값 고점76일, 고점후 평균고점수익+93.9%→최종-3.5%(거의 전부반납).
                                    # 진입 근거였던 거래량반등(20일평균×2.0)이 식으면(진입시 거래량의 40% 이하) 조기청산.
    chart_confluence: bool = False, # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    fin_health: bool = False,       # 2026-07-18 실험(사용자 가설): as-of TTM 순이익 흑자 종목만 — "재무 멀쩡한데 이벤트로 폭락한" 종목 한정
    turnaround_rev_filter: bool = False,  # 2026-07-18 실험: 흑자전환 확정분기에 매출YoY>0 조건 추가(리딩시그널 연구 결과 반영)
    avoid_dilution_risk: int = None,  # 2026-07-20 실험: 진입일 트레일링365일 CB/BW/EB/RIGHTS 공시 N건+ 종목 제외
                                       # (turnaround-watch 실증: 4건+ 구간 TTM흑자전환율 lift 0.90x/0.64x,
                                       # 12개월 forward -30%↓ 비율 29%→49.3% — 젬백스형 희석스파이럴 배제 목적)
    ta_score_bonus: float = None,  # 2026-07-20 실험: 종합턴어라운드스코어(0~3, 재도전+매출성장+이익의질)
                                    # 점당 랭킹 보너스 — routes/tenbagger.py comprehensive_score와 동일 정의.
                                    # walk-forward: 0점 lift 0.52~0.59x/1점 0.79~0.91x/2점 1.13~1.23x/
                                    # 3점 1.38x(학습)/1.63x(검증) 단조증가 확인됨(turnaround-watch 탭).
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    낙폭과대 반등 전략 (V-RECOVERY).

    [데이터 기반 핵심 로직]
    실증 데이터(2020-2025, 370만 거래일): MA60 대비 낙폭이 깊을수록 수익률↑
      -20~-25%: 평균 +43.6%  |  -25~-35%: +56.3%  |  -35~-45%: +73.9%  |  -45%↓: +103.5%
    pct_from_low_max=40 유지 이유: 80으로 넓히면 하락장+49.2%→-0.04% 확인(저점근방이 하락장 방어 핵심)
    중간반등 포착은 V-DEEP 전략에서 담당.

    A) 현재가 < MA60 × (1 + ma60_depth_min)  [MA60 대비 깊은 낙폭, ≥-20%]
    B) 현재가 < MA60 × (1 + ma60_depth_max)  [너무 깊으면 제외, ≤-65%]
    C) 52주 저점 대비 pct_from_low_max% 이내  [저점 근방 집중]
    D) 최근 5일 거래량 > 20일 평균 × vol_ratio [반등 신호]
    E) 최근 3일 가격 상승 (바닥 확인)
    F) 최근 5일 종가 > 10일 전 저점 (회복 시작)
    G) 시총 200억+ (불량기업 제외) — stock_universe 기반
    H) KOSPI MA120 붕괴 심각하지 않을 때만 (MA120 × 0.85 이상)

    매도:
    - Trail -20% (이익 달성 후), Trail -25% (50%+ 이익 시)
    - 손절 -12%
    - 최대 보유 240일
    """
    init_backtest_db()
    run_name = run_name or f"V-RECOVERY낙폭반등 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "recovery", "rec_v3_flow_ta_20260713",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "ma60_depth_min": ma60_depth_min, "ma60_depth_max": ma60_depth_max,
         "pct_from_low_max": pct_from_low_max, "vol_ratio": vol_ratio,
         "turnaround_bonus": turnaround_bonus, "flow_bonus": flow_bonus,
         "avoid_dilution_risk": avoid_dilution_risk, "ta_score_bonus": ta_score_bonus,
         "asof_mktcap": asof_mktcap, "avoid_overheat": avoid_overheat,
         "strict_exec": strict_exec, "chart_confluence": chart_confluence,
         "fin_health": fin_health,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'recovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=300)).strftime('%Y-%m-%d')

        # KOSPI (시장 필터용)
        k_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date
        """).fetchall()
        k_dates  = [r[0] for r in k_rows]
        k_prices = [float(r[1]) for r in k_rows]
        k_idx    = {d: i for i, d in enumerate(k_dates)}

        def _k_ma120(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 120: return None
            return sum(k_prices[idx-119:idx+1]) / 120

        # 종목 로드 (시총 200억+, KOSPI/KOSDAQ) — 2026-08-12: SQLite CASE WHEN 정수
        # 트릭이 PostgreSQL boolean 타입과 불일치해 오류 발생하던 것을 Python측
        # 조건분기로 교체(기존 로직과 완전히 동일하게 동작, PostgreSQL 호환성만 수정)
        _rec_mktcap_min = 0 if asof_mktcap else 200
        codes = conn.execute("""
            SELECT DISTINCT p.stock_code, su.market_cap
            FROM price_history p
            JOIN stock_universe su ON p.stock_code=su.stock_code
            WHERE p.date BETWEEN ? AND ? AND p.close>0
              AND su.market_cap >= ?
              AND su.market IN ('KOSPI','KOSDAQ')
              AND LENGTH(p.stock_code)=6
              AND p.stock_code GLOB '[0-9]*'
        """, (start_date, end_date, _rec_mktcap_min)).fetchall()

        # 2026-08-12: 발행주식수를 stock_universe.shares_issued(현재값 고정)로 쓰던
        # 것을 security_share_history 기반 정확한 as-of 값으로 교체(공용 패턴, V-EARNINGS/
        # V-MOONSHOT 등이 이미 쓰던 것과 동일) — 표본검증 결과 300종목 중 173개(57.7%)가
        # 2020-03 시점 실제 발행주식수가 현재값과 2%+ 차이(최대 17배, 분할/대규모증자
        # 미반영) — asof_mktcap=True인데도 실제로는 "근사"라 부르기 민망한 수준이었음.
        rec_share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            _rec_codes = [c for c, _ in codes]
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(_rec_codes))), _rec_codes
            ):
                rec_share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_rec(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(rec_share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0) AS v, COALESCE(high,close) AS h, COALESCE(low,close) AS lo,
                       COALESCE(open, close) AS o
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 90: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 필터
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'h': [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows],
                'o': [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # 주도섹터 부스트 (실험용, V-GC와 동일: as-of sector_large 평균 ret20 상위 2섹터)
        rec_sec_of: Dict[str, str] = {}
        if hot_sector_boost and sd:
            for r in conn.execute(
                "SELECT stock_code, sector_large FROM stock_universe "
                "WHERE stock_code IN ({})".format(",".join("?" * len(sd))),
                list(sd.keys()),
            ).fetchall():
                if r[1] and r[1] != '기타':
                    rec_sec_of[r[0]] = r[1]

        def _rec_hot_sectors(day: str) -> dict:
            agg: Dict[str, list] = {}
            for code2, s2 in sd.items():
                sec2 = rec_sec_of.get(code2)
                if not sec2:
                    continue
                i2 = didx[code2].get(day)
                if i2 is None or i2 < 20:
                    continue
                c0, c20 = s2['c'][i2], s2['c'][i2 - 20]
                if c20 > 0:
                    agg.setdefault(sec2, []).append(c0 / c20 - 1)
            avg2 = {k: sum(v) / len(v) for k, v in agg.items() if len(v) >= 5}
            top = sorted(avg2.items(), key=lambda x: -x[1])[:2]
            return {s3: (i3 + 1) for i3, (s3, a3) in enumerate(top) if a3 >= 0.03}

        # 흑자전환 보너스: 분기 순이익+매출 + 공시가능일(as-of) 로드 — V-TURNAROUND 검증 조건
        # 2026-07-18: 매출도 함께 로드 — 리딩시그널 연구(scratch/turnaround_leading_signal_research.py)
        # walk-forward 검증 결과 "매출YoY성장"만이 학습/검증 양쪽에서 안정적 lift(1.05~1.08x)를
        # 보인 유일한 후보였음(임원매수·적자축소는 오히려 음의 lift, 마진개선은 부호 불안정).
        ta_fins: Dict[str, list] = {}
        if (turnaround_bonus is not None or fin_health or ta_score_bonus is not None) and sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.net_income, f.revenue, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4 AND f.net_income IS NOT NULL
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, avail_date
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                # 튜플: (avail_date, net_income, revenue, year, quarter) — 인덱스[0..2]는 기존 코드 호환
                ta_fins.setdefault(r[0], []).append((r[5], r[1], r[2], r[3], r[4]))

        def _fin_healthy(code: str, day: str) -> bool:
            """day 시점 공시된 최근 4개 분기 순이익 합(TTM) > 0 — '재무가 나쁘지 않은' 종목 판정."""
            fl = ta_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 4:
                return False
            vals = [x[1] for x in avail[-4:] if x[1] is not None]
            return len(vals) == 4 and sum(vals) > 0

        def _is_turnaround(code: str, day: str) -> bool:
            """day 시점 공시된 최신 분기 첫 흑자전환 (직전 NI>0, 이전 1~3분기 중 NI<0 존재)."""
            fl = ta_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 2:
                return False
            if avail[-1][1] is None or avail[-1][1] <= 0:
                return False
            return any(x[1] is not None and x[1] < 0 for x in avail[-4:-1])

        def _is_turnaround_rev_growth(code: str, day: str) -> bool:
            """2026-07-18 실험: _is_turnaround 조건 + 흑자전환 확정분기 매출 YoY>0
            (리딩시그널 연구에서 유일하게 안정적이었던 매출성장 조건을 확정 흑자전환에 추가 필터로 결합)."""
            fl = ta_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5:
                return False
            if avail[-1][1] is None or avail[-1][1] <= 0:
                return False
            if not any(x[1] is not None and x[1] < 0 for x in avail[-4:-1]):
                return False
            rev_now, rev_1y = avail[-1][2], avail[-5][2]
            return bool(rev_now and rev_1y and rev_1y > 0 and rev_now / rev_1y - 1 > 0)

        # 자사주 취득공시 보너스: 종목별 공시일 리스트 (as-of, 취득결정/신탁체결만)
        bb_events: Dict[str, list] = {}
        if buyback_bonus is not None and sd:
            for r in conn.execute("""
                SELECT stock_code, replace(rcept_dt,'.','-') d FROM treasury_buyback
                WHERE event_type IN ('취득결정','acquisition','trust')
                  AND report_nm LIKE '%취득%' AND report_nm NOT LIKE '%해지%' AND report_nm NOT LIKE '%결과%'
                  AND stock_code IN ({})
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                bb_events.setdefault(r[0], []).append(r[1])

        def _has_recent_buyback(code: str, day: str) -> bool:
            evs = bb_events.get(code)
            if not evs:
                return False
            cutoff = (datetime.strptime(day, '%Y-%m-%d') - timedelta(days=60)).strftime('%Y-%m-%d')
            return any(cutoff <= e <= day for e in evs)

        # 기관+외인 수급 보너스: 종목별 일자→순매수 합 (수량 기준)
        flow_map: Dict[str, Dict[str, float]] = {}
        if flow_bonus is not None and sd:
            for r in conn.execute(
                "SELECT stock_code, date, COALESCE(inst_net_buy,0)+COALESCE(frn_net_buy,0) "
                "FROM price_history WHERE date>=? AND date<=? AND close>0 AND stock_code IN ({})".format(
                    ",".join("?" * len(sd))),
                [warmup_start, end_date] + list(sd.keys())).fetchall():
                flow_map.setdefault(r[0], {})[r[1]] = float(r[2] or 0)

        def _flow_positive(code: str, day: str) -> bool:
            m = flow_map.get(code)
            if not m:
                return False
            i0 = didx[code].get(day)
            if i0 is None:
                return False
            days5 = sd[code]['d'][max(0, i0 - 4):i0 + 1]
            vals = [m[d5] for d5 in days5 if d5 in m]
            return bool(vals) and sum(vals) > 0

        # 희석위험 이벤트: 종목별 공시일 리스트 (as-of, CB/BW/EB/유상증자만)
        dilution_map: Dict[str, list] = {}
        if avoid_dilution_risk is not None and sd:
            for r in conn.execute("""
                SELECT stock_code, disclosed_at FROM dilution_events
                WHERE event_type IN ('CB','BW','EB','RIGHTS') AND disclosed_at IS NOT NULL
                  AND stock_code IN ({})
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                dilution_map.setdefault(r[0], []).append(r[1])

        def _dilution_risk_count(code: str, day: str) -> int:
            """day 시점 트레일링 365일 내 CB/BW/EB/RIGHTS 공시 건수."""
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(day, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')
            return sum(1 for e in evs if cutoff <= e <= day)

        # 종합 턴어라운드 스코어(0~3): 재도전이력 + 매출YoY성장 + 이익의질(감가상각/현금흐름)
        # — routes/tenbagger.py get_turnaround_watch()의 comprehensive_score와 동일 정의를
        # as-of(일별 시뮬레이션 시점 기준)로 재구현.
        cf_map: Dict[tuple, dict] = {}
        if ta_score_bonus is not None and sd:
            for r in conn.execute("""
                SELECT stock_code, year, quarter, report_type, depreciation_q, operating_cf_q
                FROM cash_flow_data WHERE is_annual=0 AND stock_code IN ({})
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                cf_map.setdefault((r[0], r[1], r[2]), {})[r[3]] = (r[4], r[5])

        def _ta_score(code: str, day: str) -> int:
            fl = ta_fins.get(code)
            if not fl:
                return 0
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5:
                return 0
            y, q = avail[-1][3], avail[-1][4]
            ni_now = avail[-1][1]
            score = 0
            if any((x[1] or 0) > 0 for x in avail[-5:-1]):  # 재도전: 최근4분기(당분기제외) 흑자 있었음
                score += 1
            rev_now, rev_1y = avail[-1][2], avail[-5][2]
            if rev_1y and rev_1y >= 1e9 and rev_now and rev_now / rev_1y - 1 > 0:  # 매출YoY성장
                score += 1
            variants = cf_map.get((code, y, q))
            if variants and ni_now is not None:  # 이익의질(감가상각주도 또는 영업현금흐름>0)
                dep_q, ocf_q = variants.get("CFS") or next(iter(variants.values()))
                if (dep_q is not None and (ni_now + dep_q) > 0) or (ocf_q is not None and ocf_q > 0):
                    score += 1
            return score

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []

        pending_sells: list = []   # strict_exec: (code, reason) — 익일 시가 체결 대기
        pending_buys: list = []    # strict_exec: code — 익일 시가 체결 대기

        for day in sim_dates:
            # ── strict_exec: 전일 신호 주문을 오늘 시가에 체결 (Codex 계약 §3-2) ──
            if strict_exec:
                _still = []
                for code, reason in pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                pending_sells = _still
                for code in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue  # 당일 미거래 → 주문 만료
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares < 1:
                        continue
                    cash -= shares * px
                    pos[code] = {
                        'entry': px, 'shares': shares, 'buy_date': day,
                        'hold': 0, 'peak': px,
                        'mkt_cap_억': sd[code].get('mkt_cap_억', 300),
                    }
                    trades.append({'code': code, 'buy_date': day, 'entry': px,
                                   'shares': shares, 'action': 'buy'})
                pending_buys = []

            # ── 매도 체크 ───────────────────────────────────
            to_sell = []
            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue

                entry = p['entry']
                peak  = p.get('peak', entry)
                peak  = max(peak, curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1

                ret = (curr - entry) / entry
                # 추적손절
                if ret >= 0.50:
                    tpct = trail_big
                else:
                    tpct = trail
                trail_cond = (curr - peak) / peak < tpct
                # 손절
                stop_cond = ret < stop
                # 익절
                tp_cond = ret >= tp
                # 만료
                expire_cond = p['hold'] >= max_hold
                # 실험(2026-07-17): 거래량 위축 조기청산 — 진입 근거(거래량 반등)가 식으면 청산
                vol_fade_cond = False
                if vol_fade_exit and p['hold'] >= 15 and i >= 20:
                    v_arr = sd[code]['v']
                    v_now2 = v_arr[i]
                    v_avg20_2 = sum(v_arr[max(0, i-20):i]) / max(1, min(20, i))
                    vol_fade_cond = v_avg20_2 > 0 and v_now2 < v_avg20_2 * 0.5 and ret > 0.03
                # 고점 컨플루언스 청산 (2026-07-18 공통모듈): 이익권(+10%)에서 2/3 합의 시 선제 정리
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN

                if stop_cond or trail_cond or tp_cond or expire_cond or vol_fade_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else
                              'trail' if trail_cond else
                              'tp' if tp_cond else
                              'expire' if expire_cond else
                              'vol_fade' if vol_fade_cond else 'chart_top')
                    to_sell.append((code, curr, ret, reason))

            if strict_exec:
                for code, curr, ret, reason in to_sell:
                    if not pos[code].get('pending_exit'):
                        pos[code]['pending_exit'] = reason
                        pending_sells.append((code, reason))
            else:
                for code, curr, ret, reason in to_sell:
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': curr,
                        'pnl_pct': net_pct, 'reason': reason,
                        'pnl': round(pnl, 0),
                    })

            # ── 매수 체크 ───────────────────────────────────
            if len(pos) >= max_positions:
                continue

            # KOSPI MA120 필터 (완전 패닉장 제외)
            kma120 = _k_ma120(day)
            kospi_ok = True
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None:
                    k_curr = k_prices[ki]
                    if k_curr < kma120 * 0.85:  # KOSPI가 MA120 -15% 이하면 스킵
                        kospi_ok = False
            if not kospi_ok:
                continue

            rec_hot_map = _rec_hot_sectors(day) if hot_sector_boost else {}
            candidates = []
            for code, s in sd.items():
                if code in pos: continue
                i = didx[code].get(day)
                if i is None or i < 80: continue
                c = s['c']
                v = s['v']
                lo = s['lo']
                curr = c[i]
                if curr < 500: continue  # 최소 주가
                if asof_mktcap:
                    sh = _shares_asof_rec(code, day)
                    if sh <= 0 or sh * curr / 1e8 < 200:
                        continue

                # [F] MA60 계산
                ma60 = sum(c[max(0,i-59):i+1]) / min(60, i+1)
                if ma60 <= 0: continue

                depth = (curr - ma60) / ma60  # 음수 = 하방
                # [A][B] 낙폭 범위 체크
                if depth > ma60_depth_min or depth < ma60_depth_max:
                    continue

                # [C] 52주 저점 대비 위치
                p252 = lo[max(0,i-251):i+1]
                low52 = min(p252) if p252 else curr
                if low52 <= 0: continue
                pct_from_low = (curr - low52) / low52 * 100
                if pct_from_low > pct_from_low_max:
                    continue

                # [D] 거래량 반등 확인
                v_now = v[i]
                v_avg20 = sum(v[max(0,i-20):i]) / max(1, min(20,i))
                if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * vol_ratio:
                    continue

                # [E] 최근 3일 가격 상승 (바닥 확인: 최근 3일 중 2일 이상 상승)
                if i >= 3:
                    up_days = sum(1 for j in range(i-2, i+1) if j > 0 and c[j] > c[j-1])
                    if up_days < 2:
                        continue

                # [F] 과열 회피 (실험): 40일 +100% 초과 급등(크래시 후 V자 급반등)은 제외
                if avoid_overheat is not None and i >= 40:
                    _c40r = c[i - 40]
                    if _c40r > 0 and (curr / _c40r - 1) > avoid_overheat:
                        continue

                # 복합 점수: 낙폭 깊이(70%) + 저점 반등 위치(30%)
                # 실증: -35~-45% 구간 최강, 저점+30~80% 반등 구간 최강
                depth_score = min(-depth * 100, 50)  # 최대 50점 (depth -0.5 이상 포화)
                # 저점+30~80% 구간에 보너스
                low_bonus = 10.0 if 30 <= pct_from_low <= 80 else (
                    5.0 if pct_from_low < 30 else 0.0)
                score = depth_score * 0.7 + low_bonus
                if turnaround_bonus is not None:
                    _ta_hit = (_is_turnaround_rev_growth(code, day) if turnaround_rev_filter
                              else _is_turnaround(code, day))
                    if _ta_hit:
                        score += turnaround_bonus
                if buyback_bonus is not None and _has_recent_buyback(code, day):
                    score += buyback_bonus
                if flow_bonus is not None and _flow_positive(code, day):
                    score += flow_bonus
                if ta_score_bonus is not None:
                    score += _ta_score(code, day) * ta_score_bonus
                if hot_sector_boost:
                    hr = rec_hot_map.get(rec_sec_of.get(code, ""))
                    if hr == 1:
                        score += 15.0
                    elif hr is not None:
                        score += 5.0
                # 재무건전 필터 (2026-07-18 실험): TTM 흑자 종목만
                if fin_health and not _fin_healthy(code, day):
                    continue
                # 희석위험 제외필터 (2026-07-20 실험): 트레일링365일 CB/BW/EB/RIGHTS N건+ 제외
                if avoid_dilution_risk is not None and _dilution_risk_count(code, day) >= avoid_dilution_risk:
                    continue
                # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈): 2/3 합의 미달 시 진입 보류
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)

            for score, code, curr, i in candidates[:3]:
                if strict_exec:
                    if code not in pos and code not in pending_buys and \
                       len(pos) + len(pending_buys) < max_positions:
                        pending_buys.append(code)
                    continue
                if len(pos) >= max_positions: break
                if cash < curr * 100: continue  # 최소 100주 살 돈
                budget = min(per_stock, cash * 0.99)
                shares = int(budget // curr)
                if shares < 1: continue
                cost = shares * curr
                cash -= cost
                pos[code] = {
                    'entry': curr, 'shares': shares, 'buy_date': day,
                    'hold': 0, 'peak': curr,
                    'mkt_cap_억': sd[code].get('mkt_cap_억', 300),
                }
                trades.append({
                    'code': code, 'buy_date': day, 'entry': curr,
                    'shares': shares, 'action': 'buy',
                })

        # ── 최종 청산 ──
        sell_trades = [t for t in trades if 'sell_date' in t]
        for code, p in pos.items():
            last_date = end_date
            last_price = sd[code]['c'][-1] if sd[code]['c'] else p['entry']
            pnl, net_pct = _net_profit(p['entry'], last_price, p['shares'], p.get('mkt_cap_억', 300))
            sell_trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_date,
                'entry': p['entry'], 'exit': last_price,
                'pnl_pct': net_pct, 'reason': 'end',
                'pnl': round(pnl, 0),
            })
            cash += p['shares'] * p['entry'] + pnl

        init_cap = per_stock * max_positions
        portfolio_return = (cash - init_cap) / init_cap * 100
        win_rate = (sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0) /
                    max(1, len(sell_trades)) * 100)
        avg_trade = (sum(t.get('pnl_pct', 0) for t in sell_trades) /
                     max(1, len(sell_trades)))
        summary = (f"[V-RECOVERY] {start_date[:7]}~{end_date[:7]} "
                   f"수익률={portfolio_return:+.1f}% 승률={win_rate:.0f}% "
                   f"거래={len(sell_trades)}건 avg={avg_trade:+.1f}%")
        print(summary)

        conn.execute("""
            UPDATE backtest_runs SET status='done', summary_text=?, total_return_pct=?,
              win_rate=?, total_trades=?, profit_trades=?, trades_json=?
            WHERE run_id=?
        """, (
            summary, round(portfolio_return, 2), round(win_rate, 1),
            len(sell_trades), sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0),
            json.dumps({'trades': sell_trades, 'avg_trade_return_pct': round(avg_trade, 2),
                        'portfolio_return_pct': round(portfolio_return, 2)}, ensure_ascii=False),
            run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, init_cap, cash)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


# ─── V13 고수익 집중 백테스트 ──────────────────────────────────────────────

def run_backtest_high_profit_compound(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 8,
    stop: float = -0.15,              # 데이터: 손절15%가 avg+22.1% 최고
    trail: float = -0.35,             # 데이터: Trail35%가 3배실현 342건, 5배110건 최고
    trail_big: float = -0.40,         # 이익100%+ 달성 시 Trail40%로 확장
    tp: float = 999.0,                # 익절 없음 — Trail로만 청산 (25배 종목 조기청산 방지)
    max_hold: int = 400,              # 데이터: 고점까지 평균 280~424일 소요
    insider_days: int = 180,          # 임원매수 유효기간
    trail_activate_pct: float = 0.10, # Trail 발동 최소 이익 (데이터: 10%+ 이후 추세 형성)
    min_turnover_m: float = 20.0,     # 최소 일거래대금 (억원)
    sectors: tuple = ('IT', '의료', '경기소비재', '산업재'),
    run_name: str = None,
    run_id: str = None,
) -> str:
    """V13 고수익 집중 — 임원매수+성장섹터+계약/수주 복합 신호. 데이터 기반 최적화.
    진입: ①임원매수(180일) ②IT·의료·경기소비재·산업재 ③MA20 위(반등 확인) ④거래대금20억+ ⑤계약or수주잔고
    매도(데이터 기반): Trail-35%(이익10%+후)/이익100%+시Trail-40% / 손절-15% / 만료400일
    ※ 익절라인 없음 — 실제 3배 종목은 25배까지 가므로 Trail로만 청산
    """
    import uuid as _uuid
    rid = run_id or str(_uuid.uuid4())[:8]
    run_name = run_name or f"[V13] {start_date[:7]}~{end_date[:7]}"

    # 2026-07-18: 이 엔진도 V4와 동일하게 run_spec 기록이 전혀 없어(_record_run_spec 미호출)
    # trg_backtest_done_requires_spec 트리거에 항상 막혀 완료되지 못하던 버그 — 연속운영
    # 실측 시도 중 발견. 실제로는 정수주식이 아닌 금액모델(per_stock 고정배분)이라
    # "레거시" 등급으로 정직하게 기록.
    _record_run_spec(
        rid, "high_profit_compound", "v13_legacy_money_model",
        {"per_stock": per_stock, "max_positions": max_positions, "stop": stop,
         "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="same_close",
        market_cap_mode="current", allocation_rule="fixed_slot",
        universe_version="stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # trg_backtest_insert_done_requires_spec가 status='done' 직접 INSERT를 항상 막으므로
    # (스펙 존재 여부 무관, running으로 먼저 넣고 나중에 done으로 갱신하는 생명주기를 강제)
    # 다른 엔진들과 동일하게 running으로 먼저 등록해야 한다(2026-07-18, V4와 같은 계열 버그).
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'high_profit_compound',?,?,?,?,'running')
    """, (rid, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=280)).strftime('%Y-%m-%d')

    # 전체 거래일 목록
    all_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(date,1,10) FROM price_history WHERE substr(date,1,10) BETWEEN ? AND ? ORDER BY 1",
        (warmup_start, end_date)
    ).fetchall()]
    sim_dates = [d for d in all_dates if d >= start_date]

    sector_ph = ",".join("?" * len(sectors))

    # KOSPI MA60 사전 계산 (하락장 진입 금지 필터)
    _kospi_rows = conn.execute(
        "SELECT date, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date"
    ).fetchall()
    _k_dates_v13  = [r[0] for r in _kospi_rows]
    _k_prices_v13 = [float(r[1]) for r in _kospi_rows]
    _k_idx_v13    = {d: i for i, d in enumerate(_k_dates_v13)}

    def _k_ma60_v13(date: str) -> Optional[float]:
        idx = _k_idx_v13.get(date)
        if idx is None:
            for d in reversed(_k_dates_v13):
                if d <= date: idx = _k_idx_v13[d]; break
        if idx is None or idx < 60: return None
        return sum(_k_prices_v13[idx-59:idx+1]) / 60

    def _k_price_v13(date: str) -> float:
        idx = _k_idx_v13.get(date)
        if idx is None:
            for d in reversed(_k_dates_v13):
                if d <= date: idx = _k_idx_v13[d]; break
        return _k_prices_v13[idx] if idx is not None else 0.0

    # 임원매수 공시 캐시: {date → set of stock_codes with recent insider buy}
    def _insider_buy_codes(as_of: str) -> set:
        cutoff = (datetime.strptime(as_of, '%Y-%m-%d') - timedelta(days=insider_days)).strftime('%Y-%m-%d')
        rows = conn.execute(
            """SELECT DISTINCT stock_code FROM dart_insider_holdings
               WHERE rcept_dt BETWEEN ? AND ?
                 AND COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0) > 0""",
            (cutoff, as_of)
        ).fetchall()
        return {r[0] for r in rows}

    # 계약/수주잔고 보유 종목 캐시 (연도 변동이 적으므로 전체 미리 로드)
    contract_codes = {r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM dart_contracts WHERE signal_strength >= 2"
    ).fetchall()}
    backlog_codes = {r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM order_backlog WHERE COALESCE(backlog_amount,0)>0"
    ).fetchall()}
    catalyst_codes = contract_codes | backlog_codes

    # 섹터 필터
    sector_codes = {r[0] for r in conn.execute(
        f"SELECT stock_code FROM stock_universe WHERE sector_large IN ({sector_ph})"
        " AND market IN ('KOSPI','KOSDAQ','유가증권','코스닥')"
        " AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'",
        sectors
    ).fetchall()}

    # 포지션 관리
    holdings: dict = {}   # code → {entry, peak, entry_date}
    trades: list = []
    capital = per_stock * max_positions
    cash = capital

    def _close_as_of(code: str, date: str) -> float:
        r = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, date)
        ).fetchone()
        return float(r[0]) if r else 0.0

    def _ma_as_of(code: str, date: str, n: int) -> float:
        rows = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT ?",
            (code, date, n)
        ).fetchall()
        if len(rows) < n:
            return 0.0
        return sum(r[0] for r in rows) / n

    _insider_cache: dict = {}

    for date in sim_dates:
        # 임원매수 코드 (7일마다 갱신)
        date_idx = sim_dates.index(date)
        if date_idx % 7 == 0 or not _insider_cache:
            _insider_cache.clear()
            _insider_cache.update({c: True for c in _insider_buy_codes(date)})

        # ── 매도 체크 ──
        for code in list(holdings.keys()):
            curr = _close_as_of(code, date)
            if curr <= 0:
                continue
            h = holdings[code]
            pnl = (curr - h["entry"]) / h["entry"]
            h["peak"] = max(h["peak"], curr)
            peak_pnl = (h["peak"] - h["entry"]) / h["entry"]
            # 데이터 기반: 이익100%+ 달성 후 Trail-40%로 확장 (대박 홀드)
            trail_pct = trail_big if peak_pnl >= 1.00 else trail
            from_peak = (curr - h["peak"]) / h["peak"]
            hold_days = (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(h["entry_date"], "%Y-%m-%d")).days
            reason = None
            if pnl <= stop:
                reason = "stop"
            # Trail은 이익 trail_activate_pct+ 달성 후에만 발동 (데이터: 10%)
            elif from_peak <= trail_pct and peak_pnl >= trail_activate_pct:
                reason = "trail"
            elif pnl >= tp:
                reason = "tp"
            elif hold_days >= max_hold:
                reason = "end"
            if reason:
                pnl_abs = round(per_stock * pnl)
                trades.append({"code": code, "buy_date": h["entry_date"], "sell_date": date,
                                "entry": h["entry"], "exit": curr, "pnl_pct": round(pnl * 100, 2),
                                "reason": reason, "pnl": pnl_abs})
                cash += per_stock + pnl_abs
                del holdings[code]

        # ── 매수 후보 탐색 ──
        if len(holdings) >= max_positions:
            continue

        # 임원매수 × 섹터 × 촉매 교집합
        buy_universe = (set(_insider_cache.keys()) & sector_codes & catalyst_codes) - set(holdings.keys())
        if not buy_universe:
            continue

        # 가격 조건 평가
        ph = ",".join("?" * len(buy_universe))
        # 데이터 기반 진입 조건:
        # - MA20 위(반등 확인): 3배 달성 종목 avg vs_ma20=102.7% vs 손실 avg=98.4%
        # - 52주 저점 대비 30%+ 반등: 저점에서 어느정도 올라온 종목(너무 초기 X)
        # - 거래대금 20억+: 유동성 확보
        # - 52주 고점 80% 조건 제거: 데이터에서 고점근처 vs 낙폭과대 3배달성률 차이없음
        cands = conn.execute(
            f"""SELECT p.stock_code,
                       MAX(CASE WHEN rn=1 THEN p.close END) AS close,
                       AVG(CASE WHEN rn<=20 THEN p.close END) AS ma20,
                       MIN(CASE WHEN rn<=252 THEN p.close END) AS low52,
                       AVG(CASE WHEN rn<=20 THEN COALESCE(NULLIF(p.trade_amount,0), p.close*p.volume) END) AS avg_ta20
                FROM (
                    SELECT stock_code, close, volume, trade_amount,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                    FROM price_history
                    WHERE stock_code IN ({ph}) AND date<=? AND close>0
                ) p
                WHERE rn<=252
                GROUP BY p.stock_code
                HAVING COUNT(*)>=60
                   AND MAX(CASE WHEN rn=1 THEN p.close END) >= AVG(CASE WHEN rn<=20 THEN p.close END)
                   AND MAX(CASE WHEN rn=1 THEN p.close END) >= MIN(CASE WHEN rn<=252 THEN p.close END) * 1.20
                   AND MAX(CASE WHEN rn=1 THEN p.volume END) >= AVG(CASE WHEN rn<=20 THEN p.volume END) * 1.3
                   AND AVG(CASE WHEN rn<=20 THEN COALESCE(NULLIF(p.trade_amount,0), p.close*p.volume) END)
                       >= ?
                ORDER BY AVG(CASE WHEN rn<=20 THEN COALESCE(NULLIF(p.trade_amount,0), p.close*p.volume) END) DESC""",
            list(buy_universe) + [date, min_turnover_m * 1e8]
        ).fetchall()

        # KOSPI 필터: MA60 미만이면 신규진입 금지 (지속 약세장 차단)
        # 실험결과: MA60 avg6=17.3% >> MA120×0.82 baseline avg6=10.2%
        #           MA120×0.90=5.1%, MA20>=MA60(종목)=-69.5%하락 → MA60 KOSPI가 최적
        kospi_k = _k_price_v13(date)
        ki_v13 = _k_idx_v13.get(date)
        if ki_v13 is None:
            for d in reversed(_k_dates_v13):
                if d <= date: ki_v13 = _k_idx_v13[d]; break
        kospi_ma60_v13 = (sum(_k_prices_v13[max(0, ki_v13-59):ki_v13+1]) / 60
                          if ki_v13 is not None and ki_v13 >= 60 else None)
        if kospi_ma60_v13 is not None and kospi_k < kospi_ma60_v13:
            continue  # KOSPI < MA60: 약세장 신규진입 금지

        slots = max_positions - len(holdings)
        for row in cands[:slots]:
            code = row[0]
            entry_price = float(row[1])
            if cash < per_stock * 0.95:
                break
            holdings[code] = {"entry": entry_price, "peak": entry_price, "entry_date": date}
            cash -= per_stock

    # 기간 종료 처리
    for code, h in holdings.items():
        curr = _close_as_of(code, end_date)
        if curr > 0:
            pnl = (curr - h["entry"]) / h["entry"]
            pnl_abs = round(per_stock * pnl)
            trades.append({"code": code, "buy_date": h["entry_date"], "sell_date": end_date,
                           "entry": h["entry"], "exit": curr, "pnl_pct": round(pnl * 100, 2),
                           "reason": "end", "pnl": pnl_abs})

    # 수익률 계산
    total_pnl = sum(t["pnl"] for t in trades)
    total_ret = round(total_pnl / capital * 100, 2) if capital > 0 else 0.0
    profit_cnt = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = round(profit_cnt / len(trades) * 100, 1) if trades else 0.0
    avg_ret = round(sum(t["pnl_pct"] for t in trades) / len(trades), 2) if trades else 0.0

    summary = (f"[V13고수익집중] {start_date[:7]}~{end_date[:7]} "
               f"수익률={total_ret:+.1f}% 승률={win_rate:.0f}% 거래={len(trades)}건 avg={avg_ret:+.1f}%")
    logging.info(summary)

    conn.execute(
        """UPDATE backtest_runs
           SET status='done', name=?, start_date=?, end_date=?, total_return_pct=?,
               profit_trades=?, total_trades=?, trades_json=?, summary_text=?
           WHERE run_id=?""",
        (run_name, start_date, end_date, total_ret, profit_cnt, len(trades),
         json.dumps(trades, ensure_ascii=False), summary, rid)
    )
    conn.commit()
    conn.close()
    return rid


# ─── 섹터 연동 유틸: 특정 날짜/종목의 섹터 BUY 여부 ───────────────────────

def _get_stock_sector_key(code: str) -> Optional[str]:
    """종목 코드 → 섹터 그룹 키"""
    for sk, info in _SECTOR_GROUPS.items():
        if code in info["codes"]:
            return sk
    return None


_sector_score_memo: dict = {}  # (sector_key, ym) → score  캐시


def _is_sector_buy(conn: sqlite3.Connection, code: str, date: str,
                   threshold: float = 50.0) -> bool:
    """V-GC/V11 섹터 필터용 — BUY 섹터 여부 실시간 계산.
    성능 최적화: 월 단위 캐싱.
    """
    sk = _get_stock_sector_key(code)
    if sk is None:
        return True  # 섹터 미등록 종목은 필터 통과 (기존 전략 유지)

    ym = date[:7]
    cache_key = (sk, ym)
    if cache_key not in _sector_score_memo:
        _sector_score_memo[cache_key] = _sector_score_as_of(conn, sk, date)
    return _sector_score_memo[cache_key] >= threshold


# ─── V-DEEP: 깊은낙폭 반등 집중 전략 ─────────────────────────────────────────

def run_backtest_deep_recovery(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.13,
    trail: float = -0.22,
    trail_big: float = -0.30,
    tp: float = 1.00,               # 100%+ 익절 (2배 노림)
    max_hold: int = 300,
    ma60_depth_min: float = -0.25,  # MA60 -25% 이상 낙폭 (데이터 최강구간 시작)
    ma60_depth_max: float = -0.60,  # -60% 이하는 상폐 위험
    pct_from_low_min: float = 10.0, # 저점에서 최소 +10% 반등 확인
    pct_from_low_max: float = 100.0,# 저점 대비 +100% 이내
    vol_ratio: float = 1.5,         # 거래량 1.5x+ (완화)
    asof_mktcap: bool = False,      # 2026-07-17 as-of 재검증: current 대비 악화로 기각 → False 유지 (signal_experiment_ledger: deep_recovery/no_new_signal)
    chart_confluence: bool = False, # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-DEEP: 깊은낙폭 반등 집중 전략.

    [데이터 기반 설계 — 2026-07-02 실증]
    370만 거래일 분석:
      MA60 -25~-35%: 평균 120d +56.3%
      MA60 -35~-45%: 평균 120d +73.9%
      MA60 -45%↓:    평균 120d +103.5%
    → V-RECOVERY(-20~-65%)보다 최강구간(-25~-60%)에 집중

    진입 조건:
    A) MA60 대비 -25% ~ -60% 낙폭 (최강구간 집중)
    B) 52주 저점 대비 +10~100% (저점 탈출 확인 후 포착)
    C) 거래량 1.5x+ (진입 확인)
    D) 최근 5일 중 3일 이상 상승 (반등 지속 확인)
    E) 시총 300억+ (안전 마진)
    F) KOSPI MA120 × 0.80 이상 (패닉장 제외)

    매도: Trail -22%(이익 후) / Trail -30%(50%+ 이익) / 손절 -13% / 만료 300일 / 익절 100%
    """
    init_backtest_db()
    run_name = run_name or f"V-DEEP깊은낙폭 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "deep_recovery", "deep_v2_strict_20260715",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "ma60_depth_min": ma60_depth_min, "ma60_depth_max": ma60_depth_max,
         "pct_from_low_min": pct_from_low_min, "pct_from_low_max": pct_from_low_max,
         "vol_ratio": vol_ratio, "per_stock": per_stock, "max_positions": max_positions,
         "asof_mktcap": asof_mktcap, "chart_confluence": chart_confluence,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'deep_recovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=300)).strftime('%Y-%m-%d')

        k_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date
        """).fetchall()
        k_dates  = [r[0] for r in k_rows]
        k_prices = [float(r[1]) for r in k_rows]
        k_idx    = {d: i for i, d in enumerate(k_dates)}

        def _k_ma120(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 120: return None
            return sum(k_prices[idx-119:idx+1]) / 120

        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= 300
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()

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

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0) AS v,
                       COALESCE(high,close) AS h, COALESCE(low,close) AS lo,
                       COALESCE(open,close) AS o
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 90: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 필터
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'h': [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows],
                'o': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        for day in sim_dates:
            # 전일 종가로 확정된 주문만 다음 거래일 시가에 체결한다.
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos:
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['shares'], p.get('mkt_cap_억', 300))
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                               'entry': p['entry'], 'exit': fill, 'pnl_pct': net_pct,
                               'reason': reason, 'pnl': round(pnl, 0)})
                del pending_sells[code]

            marked_equity = cash + sum(
                p['shares'] * sd[code]['c'][didx[code][day]]
                for code, p in pos.items() if day in didx[code]
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None:
                    continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // fill)
                    if shares > 0:
                        cash -= shares * fill
                        pos[code] = {'entry': fill, 'shares': shares, 'buy_date': day,
                                     'hold': 0, 'peak': fill,
                                     'mkt_cap_억': sd[code].get('mkt_cap_억', 300)}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                pending_buys.remove(code)

            # 매도 체크
            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue
                entry = p['entry']
                peak  = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1
                ret = (curr - entry) / entry
                tpct = trail_big if ret >= 0.50 else trail
                trail_cond = (curr - peak) / peak < tpct
                stop_cond  = ret < stop
                tp_cond    = ret >= tp
                expire_cond = p['hold'] >= max_hold
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN
                if stop_cond or trail_cond or tp_cond or expire_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'tp' if tp_cond else 'chart_top' if chart_top_cond else 'expire')
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue

            # KOSPI 필터
            kma120 = _k_ma120(day)
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None and k_prices[ki] < kma120 * 0.80:
                    continue

            candidates = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                i = didx[code].get(day)
                if i is None or i < 80: continue
                c = s['c']
                v = s['v']
                lo = s['lo']
                curr = c[i]
                if curr < 500: continue

                # [E] 시총 300억+ (as-of): 신호일 기준 주가×상장주식수
                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    if sh <= 0 or sh * curr / 1e8 < 300:
                        continue

                # [A] MA60 대비 낙폭 범위
                ma60 = sum(c[max(0,i-59):i+1]) / min(60, i+1)
                if ma60 <= 0: continue
                depth = (curr - ma60) / ma60
                if depth > ma60_depth_min or depth < ma60_depth_max:
                    continue

                # [B] 52주 저점 대비 위치
                p252 = lo[max(0,i-251):i+1]
                low52 = min(p252) if p252 else curr
                if low52 <= 0: continue
                pct_from_low = (curr - low52) / low52 * 100
                if pct_from_low < pct_from_low_min or pct_from_low > pct_from_low_max:
                    continue

                # [C] 거래량 확인
                v_now  = v[i]
                v_avg20 = sum(v[max(0,i-20):i]) / max(1, min(20,i))
                if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * vol_ratio:
                    continue

                # [D] 최근 5일 중 3일 이상 상승
                if i >= 5:
                    up_days = sum(1 for j in range(i-4, i+1) if j > 0 and c[j] > c[j-1])
                    if up_days < 3:
                        continue

                # 복합 점수: 낙폭 깊이 + 저점반등 최적구간 보너스
                depth_score = min(-depth * 100, 55)
                # 최적 구간 30~80% 반등에 보너스
                low_bonus = 12.0 if 30 <= pct_from_low <= 80 else (
                            6.0 if pct_from_low <= 30 else 2.0)
                score = depth_score + low_bonus
                # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)

            available = max(0, position_limit - len(pos) - len(pending_buys))
            pending_buys.extend(code for _, code, _, _ in candidates[:min(3, available)])

        # 최종 청산
        final_val = cash
        for code, p in pos.items():
            last_c = None
            for d in reversed(sim_dates):
                i = didx[code].get(d)
                if i is not None and sd[code]['c'][i] > 0:
                    last_c = sd[code]['c'][i]; break
            if last_c:
                pnl, net_pct = _net_profit(p['entry'], last_c, p['shares'], p.get('mkt_cap_억', 300))
                final_val += p['shares'] * p['entry'] + pnl
                trades.append({
                    'code': code, 'buy_date': p['buy_date'], 'sell_date': sim_dates[-1],
                    'entry': p['entry'], 'exit': last_c,
                    'pnl_pct': net_pct, 'reason': 'final',
                    'pnl': round(pnl, 0),
                })

        init_cap = per_stock * max_positions
        total_ret = (final_val - init_cap) / init_cap * 100
        closed = [t for t in trades if 'sell_date' in t and t.get('reason') != 'buy']
        n_trades = len(closed)
        win_rate = sum(1 for t in closed if t.get('pnl_pct', 0) > 0) / max(n_trades, 1) * 100
        days_held = (datetime.strptime(end_date, '%Y-%m-%d') -
                     datetime.strptime(start_date, '%Y-%m-%d')).days
        ann_ret = ((1 + total_ret / 100) ** (365 / max(days_held, 1)) - 1) * 100

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?,
                win_rate=?, total_trades=?,
                trades_json=?, summary_text=?
            WHERE run_id=?
        """, (round(total_ret, 2), round(ann_ret, 2),
              round(win_rate, 2), n_trades,
              json.dumps(trades, ensure_ascii=False),
              f"엄격 다음날시가·정수주식·복리 | 총수익 {total_ret:.1f}% | 연환산 {ann_ret:.1f}% | 승률 {win_rate:.0f}% | {n_trades}거래",
              run_id))
        conn.commit()
        _register_execution_artifacts(run_id, init_cap, final_val)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_extreme_dd_volume(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.15,
    trail: float = -0.35,
    trail_big: float = -0.40,        # 이익 100%+ 달성 후 Trail 확장 (V13과 동일 철학)
    tp: float = 999.0,               # 익절 없음 — 실제 3배 종목은 훨씬 더 갈 수 있어 Trail로만 청산
    max_hold: int = 365,
    trail_activate_pct: float = 0.10,
    from_high_max: float = -70.0,    # 52주 고점 대비 낙폭 -70% 이하 (S등급 핵심조건)
    vol_ratio_min: float = 1.5,      # 당일거래량/20일평균(당일포함) 1.5배+
    min_tvol5_억: float = 5.0,       # 5일 평균거래대금 5억원+ (유동성)
    min_mktcap_억: float = 300.0,
    max_mktcap_억: float = 0.0,      # 0=상한없음. 2026-08-08 신 실증등급용(S<1000/A<1500/B<3000)
    asof_mktcap: bool = True,
    require_turn_confirm: bool = True,  # 2026-07-18: 반등확인 없이 진입 시 참패(-82.8%) → 기본 활성화
    require_top_exit: bool = True,   # 2026-08-08 신설: 아래 참조. 진입확인과 분리된 별도 플래그.
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-EXTREME: 초낙폭+거래량 확인 전략 (2026-07-18 신규).

    [데이터 근거] tenbagger_engine.py 실증등급 체계(2026-07-12, strategy_feature_snapshot
    15.5만행 시기분리 walk-forward 검증, 학습 ≤2022/검증 2023+ 완전 분리):
      S등급(낙폭-70%↓ + 당일거래량 20일평균 1.5배+ + 5일평균거래대금 5억+)
      → 12개월 내 3배 달성률: 학습 24.9% / 검증 22.6% (기준율 6.3~8.2% 대비 3.0~3.6배 lift)
    이 조합은 tenbagger 디스커버리(관심종목 스코어링)에만 쓰이고 실제 매매 백테스트로
    옮겨진 적이 없었음 — 이번이 최초 실전 백테스트 구현.

    진입 조건 (tenbagger_engine._fetch_price_data와 동일 정의로 fidelity 유지):
    A) 52주 고점(최근 250거래일, 당일포함) 대비 -70% 이하 낙폭
    B) 당일거래량 / 20일평균거래량(당일포함) >= 1.5배
    C) 5일평균거래대금(당일포함, 억원) >= 5억
    D) 시총 300억+ (as-of)
    E) KOSPI MA120×0.80 이상 (패닉장 신규진입 제외, V-DEEP과 동일)

    ⚠️ 1차 버전(require_turn_confirm 없음) 실측 결과 avg=-82.8%로 참패(사용자 지적으로 원인규명).
    거래량 급증 조건이 "패닉투매 절정"과 "진짜 바닥 확인"을 구분하지 못해, 계속 하락 중인
    종목을 그대로 매수하는 사례 다수 발견(매수직전 5일 상승일수 0/5인 사례 등). 이에 따라
    F) 터닝포인트 확인(require_turn_confirm=True 기본) 추가:
       F-1) 최근 5일 중 3일 이상 상승(반등 모멘텀 확인, deep_recovery와 동일 관행)
       F-2) 당일이 최근 10일 최저가가 아님(저점이 이미 과거에 찍혔는지 확인, 신규 저점 배제)
    → 거래량 급증 즉시 매수가 아니라 "하락이 멈추고 반등이 확인된 이후"에만 진입.

    매도: Trail-35%(이익10%+ 발동) / Trail-40%(이익100%+) / 손절-15% / 만료365일 / 익절없음
    (V13 고수익집중과 동일 "3배~25배 종목은 Trail로만 청산" 철학 — S등급의 목적 자체가
    희귀한 3배 이벤트 포착이므로 익절 상한을 두면 그 이벤트를 스스로 차단하게 됨)
    """
    init_backtest_db()
    run_name = run_name or f"V-EXTREME초낙폭거래량 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "extreme_dd_volume", "extreme_dd_v1_20260718",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "trail_activate_pct": trail_activate_pct, "from_high_max": from_high_max,
         "vol_ratio_min": vol_ratio_min, "min_tvol5_억": min_tvol5_억,
         "min_mktcap_억": min_mktcap_억, "max_mktcap_억": max_mktcap_억,
         "per_stock": per_stock, "max_positions": max_positions,
         "asof_mktcap": asof_mktcap, "require_turn_confirm": require_turn_confirm,
         "require_top_exit": require_top_exit,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'extreme_dd_volume',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=380)).strftime('%Y-%m-%d')

        k_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date
        """).fetchall()
        k_dates  = [r[0] for r in k_rows]
        k_prices = [float(r[1]) for r in k_rows]
        k_idx    = {d: i for i, d in enumerate(k_dates)}

        def _k_ma120(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 120: return None
            return sum(k_prices[idx-119:idx+1]) / 120

        # ── 차트 컨플루언스: module-level 공통 모듈(_chart_*) 위임 (2026-07-18 공통화) ──
        def _bottom_confluence_score(s: dict, i: int) -> int:
            return _chart_bottom_confluence(
                s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i)

        def _top_confluence_score(s: dict, i: int) -> int:
            return _chart_top_confluence(
                s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i)

        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= ?
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date, min_mktcap_억)).fetchall()

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

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0) AS v,
                       COALESCE(open,close) AS o,
                       COALESCE(high,close) AS h, COALESCE(low,close) AS lo
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 90: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 필터 (전일대비 급격한 불연속 배제)
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            # price_history.date에 타임스탬프가 섞인 오염행이 있어 str[:10] 정규화 필수.
            # (미정규화 시 _chart_prep의 strptime('%Y-%m-%d')이 크래시 — 2026-07-20(6)에
            #  run_backtest_megatrend에서 같은 버그를 고쳤으나 이 엔진에는 남아있었음, 2026-08-08 수정)
            d_list = [str(r[0])[:10] for r in rows]
            sd[code] = {
                'd': d_list,
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'o': [float(r[3]) for r in rows],
                'h': [float(r[4]) for r in rows],
                'lo': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap_억,
            }
            # 주봉/월봉 집계 — module-level 공통 헬퍼로 위임 (2026-07-18 공통화)
            sd[code]['chart'] = _chart_prep(d_list, sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        for day in sim_dates:
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos:
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                               'entry': p['entry'], 'exit': fill, 'pnl_pct': net_pct,
                               'reason': reason, 'pnl': round(pnl, 0)})
                del pending_sells[code]

            marked_equity = cash + sum(
                p['shares'] * sd[code]['c'][didx[code][day]]
                for code, p in pos.items() if day in didx[code]
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None:
                    continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // fill)
                    if shares > 0:
                        cash -= shares * fill
                        pos[code] = {'entry': fill, 'shares': shares, 'buy_date': day,
                                     'hold': 0, 'peak': fill,
                                     'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap_억)}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                pending_buys.remove(code)

            # 매도 체크 (V13과 동일: 이익권 확장 Trail, 익절 상한 없음 + 고점 컨플루언스 청산)
            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue
                entry = p['entry']
                peak  = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1
                ret = (curr - entry) / entry
                peak_ret = (peak - entry) / entry
                stop_cond  = ret < stop
                trail_cond = False
                if peak_ret >= trail_activate_pct:
                    tpct = trail_big if peak_ret >= 1.00 else trail
                    trail_cond = (curr - peak) / peak < tpct
                tp_cond    = ret >= tp
                expire_cond = p['hold'] >= max_hold
                # 고점 컨플루언스 청산 (2026-07-18 도입, 2026-08-08 진입확인과 분리).
                # ⚠️ 종전엔 require_turn_confirm 하나가 진입 바닥확인과 이 청산조건을 동시에
                # 켰음 — 진입확인은 검증됐지만(끄면 -82.8%) 이 청산조건은 이 전략에서 독립
                # 검증된 적이 없었다. 실측(2026-08-08): trail_activate_pct=10%만 넘으면
                # 차트신호 2개 합의로 즉시 발동해 승자 206/463건(신A/730일 표본)을 평균
                # 38일·중앙값+16%에서 잘라냄 — 이 신호의 예측지평(24개월)과 청산지평(38일)이
                # 불일치. require_top_exit=False(trail만으로 청산) 대비수치는 아래 __main__
                # 홀드아웃으로 별도 검증(주석에 미검증 수치 기재 금지 원칙).
                top_cond = False
                if require_top_exit and ret >= trail_activate_pct and not trail_cond:
                    top_cond = _top_confluence_score(sd[code], i) >= _CHART_TOP_MIN
                if stop_cond or trail_cond or tp_cond or expire_cond or top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'tp' if tp_cond else 'chart_top' if top_cond else 'expire')
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue

            kma120 = _k_ma120(day)
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None and k_prices[ki] < kma120 * 0.80:
                    continue

            candidates = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                i = didx[code].get(day)
                if i is None or i < 60: continue
                c = s['c']
                v = s['v']
                curr = c[i]
                if curr < 500: continue

                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    if sh <= 0 or sh * curr / 1e8 < min_mktcap_억:
                        continue
                    # 2026-08-08: 신 실증등급은 시총 '상한'이 핵심 판별축(작을수록 lift↑).
                    # label_10x_24m 검증 lift — 시총<1000억 10.3x / <1500억 7.6x / <3000억 4.4x.
                    if max_mktcap_억 and sh * curr / 1e8 > max_mktcap_억:
                        continue

                # [A] 52주 고점(최근 250일, 당일포함) 대비 낙폭
                p250 = c[max(0, i-249):i+1]
                high_52w = max(p250) if p250 else curr
                if high_52w <= 0: continue
                from_high_pct = (curr / high_52w - 1) * 100
                if from_high_pct > from_high_max:
                    continue

                # [B] 거래량 20일평균(당일포함) 대비
                v_window20 = v[max(0, i-19):i+1]
                avg_vol20 = sum(v_window20) / len(v_window20) if v_window20 else 0
                v_now = v[i]
                if avg_vol20 <= 0 or v_now < avg_vol20 * vol_ratio_min:
                    continue

                # [C] 5일평균거래대금(당일포함, 억원)
                v_window5 = v[max(0, i-4):i+1]
                avg_vol5 = sum(v_window5) / len(v_window5) if v_window5 else 0
                avg_tvol5_억 = avg_vol5 * curr / 1e8
                if avg_tvol5_억 < min_tvol5_억:
                    continue

                # [D] 바닥 컨플루언스 확인 (2026-07-18 — 사용자 지시: 일봉추세+주봉구조+캔들패턴을
                # 복합 판단. 거래량 급증만으로는 "패닉투매 중"과 "진짜 바닥"을 구분 못함이 실측으로
                # 확인됨(매수직전 5일이 단 하루도 안 쉬고 계속 하락하는 케이스 다수). 아래 3요소
                # (일봉 MA5>MA10 / 주봉 higher-low / 캔들 반전패턴) 중 최소 2개 이상 합의 요구.
                conf_score = 0
                if require_turn_confirm:
                    conf_score = _bottom_confluence_score(s, i)
                    if conf_score < _CHART_BOTTOM_MIN:
                        continue

                # 낙폭이 깊을수록, 거래량 확인이 강할수록, 컨플루언스가 강할수록 우선 순위
                score = min(-from_high_pct, 95) + min(v_now / max(avg_vol20, 1), 5) * 2 + conf_score * 5
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)
            available = max(0, position_limit - len(pos) - len(pending_buys))
            pending_buys.extend(code for _, code, _, _ in candidates[:min(3, available)])

        final_val = cash
        for code, p in pos.items():
            last_c = None
            for d in reversed(sim_dates):
                i = didx[code].get(d)
                if i is not None and sd[code]['c'][i] > 0:
                    last_c = sd[code]['c'][i]; break
            if last_c:
                pnl, net_pct = _net_profit(p['entry'], last_c, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                final_val += p['shares'] * p['entry'] + pnl
                trades.append({
                    'code': code, 'buy_date': p['buy_date'], 'sell_date': sim_dates[-1],
                    'entry': p['entry'], 'exit': last_c,
                    'pnl_pct': net_pct, 'reason': 'final',
                    'pnl': round(pnl, 0),
                })

        init_cap = per_stock * max_positions
        total_ret = (final_val - init_cap) / init_cap * 100
        closed = [t for t in trades if 'sell_date' in t and t.get('reason') != 'buy']
        n_trades = len(closed)
        win_rate = sum(1 for t in closed if t.get('pnl_pct', 0) > 0) / max(n_trades, 1) * 100
        days_held = (datetime.strptime(end_date, '%Y-%m-%d') -
                     datetime.strptime(start_date, '%Y-%m-%d')).days
        ann_ret = ((1 + total_ret / 100) ** (365 / max(days_held, 1)) - 1) * 100

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?,
                win_rate=?, total_trades=?,
                trades_json=?, summary_text=?
            WHERE run_id=?
        """, (round(total_ret, 2), round(ann_ret, 2),
              round(win_rate, 2), n_trades,
              json.dumps(trades, ensure_ascii=False),
              f"엄격 다음날시가·정수주식·복리 | 총수익 {total_ret:.1f}% | 연환산 {ann_ret:.1f}% | 승률 {win_rate:.0f}% | {n_trades}거래",
              run_id))
        conn.commit()
        _register_execution_artifacts(run_id, init_cap, final_val)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_se_momentum(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.08,             # 2026-07-22 채택: 실제 스탁이지 로그인 세션(모멘텀129건/피크 표본)
                                      # 편출내역 실측 결과 손실거래의 60%가 -8.0~-8.14%에 클러스터링(하드손절 확정).
                                      # 6기간 검증 avg6 +22.4%→+27.0%(4/6기간, -0.10/-0.12보다 우수) 확인 후 채택.
    top_sectors: int = 2,            # 주도섹터 상위 N개에서만 편입
    exit_sector_rank: int = 8,       # 편출 히스테리시스: 상위 N위 밖으로 밀려나야 섹터 편출
                                     # 스윕 실측(연속 2020-03~2026-03): 진입=편출 동일(top2) -64.2% →
                                     # rank5 +10.7 → rank8 +45.0. 진입은 좁게/편출은 넓게가 SE 실동작.
                                     # 단 "모멘텀 음전시만 편출"(rank999)은 -45.7% — 승자 반납 참패.
    ma_exit_buffer: float = 0.96,    # MA 역전 청산 버퍼: ma5 < ma20×0.96 (깊은 이탈만) — 0.985는 휩쏘로 -51%
    trail: float = -0.20,            # 고점대비 -20% 추적손절(이익 5%+ 발동) — +45.0→+54.3 개선 확정
    min_sector_ret20: float = 3.0,   # 섹터 평균 ret20 최소값 (%) — 약세장 무리한 편입 방지
    basket_per_sector: int = 5,      # 섹터당 최대 편입 종목 수 (시총 상위 순)
    min_mktcap_억: float = 500.0,
    asof_mktcap: bool = True,
    chart_confluence: bool = False,  # 공통모듈 옵션 (기본 off — SE 로직 자체가 추세 진입)
    require_earnings_accel: bool = True,  # 2026-07-22 채택: 6기간 검증 avg6 +3.6%→+22.4%(4/6기간 대폭개선,
                                          # 최근/AI랠리 2개 기간만 악화) — 스탁이지 실제 정의(실적가속) 반영
    sector_lookback_days: int = 20,  # 2026-07-22 실험: 스탁이지 실제 sector_rs API(로그인불필요) 스냅샷과
                                      # 대조한 결과 우리 ret20 섹터랭킹 TOP10 겹침 1/10뿐 — 252일이 6/10으로 최유사
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-SE 주도섹터 바스켓 (스탁이지 모멘텀 전략의 백테스트 구현, 2026-07-18 신규).

    [배경] 스탁이지(SE) 모멘텀 전략은 stockeasy_logic_validator.py에서 실보유와
    BUY 94~100% 일치까지 재현됐지만(2026-07-11, 전체 이력 기준), 백테스트 전략으로는
    한 번도 구현된 적 없었음 — 사용자 지시("스탁이지 전략 참고, 보유일수 대신
    차트/추세 기반 매도")로 최초 구현.

    진입 (validator v3 로직 충실 포팅):
    1) SE middle 섹터별 "전체 멤버" 평균 ret20 랭킹 → 상위 top_sectors개 주도섹터
       (평균 ret20 >= min_sector_ret20%; 전체멤버 기준 필수 — 통과종목만으로 랭킹하면
        생존자 편향으로 왜곡됨이 실증됨)
    2) 섹터 내 필터: MA5>MA20 + 주가 >= MA20×0.97 + (기관 or 외인 5일 순매수 양수)
       ※ MA20>=MA60 정배열 요구 없음 — SE는 골든크로스 형성 초기에 진입
    3) 통과 종목 중 as-of 시총 상위 basket_per_sector개 편입 (지주/홀딩스 제외)

    매도 (전부 차트/추세 기반 — 보유일수 조건 없음):
    A) MA5 < MA20 하향 (진입 조건 역전 — SE 실증: 달바글로벌 제외 사유와 동일)
    B) 소속 섹터가 주도섹터 랭킹(상위 top_sectors)에서 이탈 (SE의 섹터 일괄 편출 재현)
    C) 안전망 손절 stop (기본 -12%)

    ⚠️ 한계: stockeasy_sector_membership은 현재 시점 분류를 과거에 적용
    (V-SECTOR와 동일 관행 — 섹터 구성 자체는 안정적이나 신규상장 편입 시차 존재).
    """
    init_backtest_db()
    run_name = run_name or f"V-SE주도섹터 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "se_momentum", "se_momentum_v1_20260718",
        {"stop": stop, "top_sectors": top_sectors, "exit_sector_rank": exit_sector_rank,
         "ma_exit_buffer": ma_exit_buffer, "trail": trail, "min_sector_ret20": min_sector_ret20,
         "basket_per_sector": basket_per_sector, "min_mktcap_억": min_mktcap_억,
         "asof_mktcap": asof_mktcap, "chart_confluence": chart_confluence,
         "require_earnings_accel": require_earnings_accel,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'se_momentum',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_days = max(120, int(sector_lookback_days * 1.6) + 30)
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=warmup_days)).strftime('%Y-%m-%d')

        # SE 섹터 멤버십 (middle level) — 현재 시점 분류 (한계 docstring 참조)
        se_sector: Dict[str, str] = {}
        for r in conn.execute(
            "SELECT DISTINCT stock_code, sector_name FROM stockeasy_sector_membership "
            "WHERE sector_level='middle'"
        ).fetchall():
            se_sector[r[0]] = r[1]

        # 지주/홀딩스 제외용 이름 맵
        name_map: Dict[str, str] = dict(conn.execute(
            "SELECT stock_code, stock_name FROM stock_universe"
        ).fetchall())

        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND LENGTH(p.stock_code)=6 AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= ? AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6 AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date, min_mktcap_억)).fetchall()
        # SE 섹터 분류가 있는 종목만 (전략 유니버스 정의)
        codes = [(c, m) for c, m in codes if c in se_sector]

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality))

        def _shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0),
                       COALESCE(open,close), COALESCE(high,close), COALESCE(low,close),
                       COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 40: continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'o': [float(r[3]) for r in rows],
                'h': [float(r[4]) for r in rows],
                'lo': [float(r[5]) for r in rows],
                'frn': [float(r[6]) for r in rows],
                'inst': [float(r[7]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap_억,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # 실적가속(이익모멘텀) 게이트 — 2026-07-22: STRATEGY_ANALYSIS_GUIDES의 실제 모멘텀Easy
        # 정의("매출/영업이익 YoY·QoQ 가속, 흑자전환, 이익폭발, 수급전환")를 반영한 실험.
        # 기존 se_momentum은 순수 기술적(MA+섹터+수급) 로직뿐이라 실적 요소가 전무했음.
        earn_fins: Dict[str, list] = {}
        if require_earnings_accel and sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.revenue, f.operating_profit, f.net_income, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, avail_date
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                earn_fins.setdefault(r[0], []).append(
                    (r[6], r[1], r[2], r[3], r[4], r[5]))  # (avail_date, rev, op, ni, year, quarter)

        def _earnings_accel_ok(code: str, day: str) -> bool:
            """실적가속: 최신 공시분기 매출YoY>0 AND 영업이익YoY>0(가속) 이거나, 흑자전환(직전 적자→흑자)."""
            fl = earn_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5:
                return False
            cur, prev_y = avail[-1], avail[-5]
            rev_now, op_now, ni_now = cur[1], cur[2], cur[3]
            rev_1y, op_1y = prev_y[1], prev_y[2]
            rev_yoy_ok = bool(rev_now and rev_1y and rev_1y > 0 and rev_now > rev_1y)
            op_yoy_ok = bool(op_now is not None and op_1y is not None and op_1y > 0 and op_now > op_1y)
            if rev_yoy_ok and op_yoy_ok:
                return True
            # 흑자전환: 최신분기 흑자 + 직전 1~3분기 중 적자 존재
            if ni_now is not None and ni_now > 0:
                prior3 = avail[-4:-1]
                if any(x[3] is not None and x[3] < 0 for x in prior3):
                    return True
            return False

        def _ret20(code: str, day: str) -> Optional[float]:
            i = didx[code].get(day)
            if i is None or i < sector_lookback_days: return None
            c = sd[code]['c']
            lb = sector_lookback_days
            return (c[i] - c[i-lb]) / c[i-lb] * 100 if c[i-lb] > 0 else None

        def _sector_ranking(day: str):
            """당일 기준 섹터 랭킹. 반환: (진입가능 상위섹터 set, 섹터→(순위,평균ret_lookback) dict).
            전체 멤버 평균 ret(sector_lookback_days) 랭킹 — 통과종목만으로 계산 시 생존자 편향(주석 참조)."""
            agg: Dict[str, list] = {}
            for code in sd:
                r = _ret20(code, day)
                if r is not None:
                    agg.setdefault(se_sector[code], []).append(r)
            ranked = sorted(
                ((sec, sum(v)/len(v)) for sec, v in agg.items() if len(v) >= 5),
                key=lambda x: -x[1])
            rank_map = {sec: (idx + 1, avg) for idx, (sec, avg) in enumerate(ranked)}
            entry_set = {sec for sec, avg in ranked[:top_sectors] if avg >= min_sector_ret20}
            return entry_set, rank_map

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []
        top_secs_cache: Dict[str, set] = {}

        for day in sim_dates:
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos:
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                               'entry': p['entry'], 'exit': fill, 'pnl_pct': net_pct,
                               'reason': reason, 'pnl': round(pnl, 0)})
                del pending_sells[code]

            marked_equity = cash + sum(
                p['shares'] * sd[code]['c'][didx[code][day]]
                for code, p in pos.items() if day in didx[code]
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None:
                    continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // fill)
                    if shares > 0:
                        cash -= shares * fill
                        pos[code] = {'entry': fill, 'shares': shares, 'buy_date': day,
                                     'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap_억)}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                pending_buys.remove(code)

            top_secs, sec_rank = _sector_ranking(day)
            top_secs_cache[day] = top_secs

            # ── 매도 체크: 전부 차트/추세 기반 (보유일수 조건 없음) ──
            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None or i < 20: continue
                c = sd[code]['c']
                curr = c[i]
                if curr <= 0: continue
                ret = (curr - p['entry']) / p['entry']
                peak = max(p.get('peak', p['entry']), curr)
                p['peak'] = peak
                ma5 = sum(c[i-4:i+1]) / 5
                ma20 = sum(c[i-19:i+1]) / 20
                sec = se_sector.get(code)
                rk, sec_avg = sec_rank.get(sec, (999, -999.0))
                reason = None
                if ret < stop:
                    reason = 'stop'
                elif trail is not None and ret > 0.05 and (curr - peak) / peak < trail:
                    reason = 'trail'           # 고점대비 추적손절 (이익권에서만)
                elif ma5 < ma20 * ma_exit_buffer:
                    reason = 'ma_reverse'      # 진입조건(MA5>MA20) 역전 — 버퍼로 휩쏘 방지
                elif rk > exit_sector_rank or sec_avg < 0:
                    reason = 'sector_exit'     # 주도섹터 확정 이탈(히스테리시스: 상위권 완전 이탈 or 모멘텀 음전)
                if reason:
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue
            if not top_secs:
                continue

            # ── 진입 후보: 주도섹터 내 SE 필터 통과 종목, 시총 상위 순 ──
            cand = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                if se_sector.get(code) not in top_secs: continue
                nm = name_map.get(code, '')
                if '지주' in nm or '홀딩스' in nm: continue
                i = didx[code].get(day)
                if i is None or i < 25: continue
                c = s['c']
                curr = c[i]
                if curr < 500: continue
                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    mc = sh * curr / 1e8
                    if sh <= 0 or mc < min_mktcap_억:
                        continue
                else:
                    mc = s.get('mkt_cap_억', 0)
                ma5 = sum(c[i-4:i+1]) / 5
                ma20 = sum(c[i-19:i+1]) / 20
                if ma5 <= ma20: continue
                if curr < ma20 * 0.97: continue
                frn5 = sum(s['frn'][max(0, i-4):i+1])
                inst5 = sum(s['inst'][max(0, i-4):i+1])
                if frn5 <= 0 and inst5 <= 0: continue
                if require_earnings_accel and not _earnings_accel_ok(code, day): continue
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                cand.append((mc, code))

            # 섹터당 상위 basket_per_sector개 (시총 순)
            cand.sort(reverse=True)
            per_sec_count: Dict[str, int] = {}
            available = max(0, position_limit - len(pos) - len(pending_buys))
            for mc, code in cand:
                if available <= 0: break
                sec = se_sector[code]
                if per_sec_count.get(sec, 0) >= basket_per_sector: continue
                pending_buys.append(code)
                per_sec_count[sec] = per_sec_count.get(sec, 0) + 1
                available -= 1

        final_val = cash
        for code, p in pos.items():
            last_c = None
            for d in reversed(sim_dates):
                i = didx[code].get(d)
                if i is not None and sd[code]['c'][i] > 0:
                    last_c = sd[code]['c'][i]; break
            if last_c:
                pnl, net_pct = _net_profit(p['entry'], last_c, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                final_val += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': sim_dates[-1],
                               'entry': p['entry'], 'exit': last_c,
                               'pnl_pct': net_pct, 'reason': 'final', 'pnl': round(pnl, 0)})

        init_cap = per_stock * max_positions
        total_ret = (final_val - init_cap) / init_cap * 100
        closed = [t for t in trades if 'sell_date' in t and t.get('reason') != 'buy']
        n_trades = len(closed)
        win_rate = sum(1 for t in closed if t.get('pnl_pct', 0) > 0) / max(n_trades, 1) * 100
        days_held = (datetime.strptime(end_date, '%Y-%m-%d') -
                     datetime.strptime(start_date, '%Y-%m-%d')).days
        ann_ret = ((1 + total_ret / 100) ** (365 / max(days_held, 1)) - 1) * 100

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?,
                win_rate=?, total_trades=?, trades_json=?, summary_text=?
            WHERE run_id=?
        """, (round(total_ret, 2), round(ann_ret, 2), round(win_rate, 2), n_trades,
              json.dumps(trades, ensure_ascii=False),
              f"엄격 다음날시가·정수주식 | 총수익 {total_ret:.1f}% | 연환산 {ann_ret:.1f}% | 승률 {win_rate:.0f}% | {n_trades}거래",
              run_id))
        conn.commit()
        _register_execution_artifacts(run_id, init_cap, final_val)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_peak_easy(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.08,             # 2026-07-22 실측 교정: 실제 피크Easy 편출내역 345건 중 손실의 80.8%가
                                      # -8.0%대에 클러스터링(모멘텀Easy와 동일 프레임워크) — 기존 -0.12→-0.08
    trail: float = -0.25,
    top_sectors: int = 2,
    exit_sector_rank: int = 8,
    min_sector_ret20: float = 3.0,
    basket_per_sector: int = 5,
    pct_of_52w_high: float = 0.995,  # 2026-07-22 실측 교정: 사용자 로그인세션으로 확보한 실제 피크Easy
                                      # 편출내역 10건 표본 전수 검증 결과 매수일=정확히 52주 신고가(100.0%)
                                      # — 기존 0.90(근사) 대신 실측치(사실상 그날의 신고가)로 교정
    vol_ratio_min: float = 1.3,      # 거래량 재증가: 5일평균 > 20일평균 x 1.3
    require_rs: bool = True,        # 상대강도: 종목 60일수익률 > KOSPI 60일수익률
    require_earnings_accel: bool = False,  # 실적가속 게이트 (se_momentum과 동일 헬퍼 재사용)
    require_market_uptrend: bool = False,  # 2026-07-22 실험: KOSPI>MA120일 때만 신규진입(약세장 가짜돌파 회피)
    min_mktcap_억: float = 500.0,
    asof_mktcap: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-PEAK -- 스탁이지 "Peak Easy" 전략의 백테스트 재현 (2026-07-22 신규).

    [배경] stockeasy_analyzer.py STRATEGY_ANALYSIS_GUIDES의 실제 정의:
    "52주 신고가/저항 돌파형 전략이다. 신고가권, MA 정배열, 거래량 재증가,
     상대강도, 주도 섹터, 실적 가속을 중심으로 분석" -- 이 6개 요소를 그대로 구현.
    기존 vbr(V8 52W돌파)은 이 중 신고가권·MA정배열·거래량재증가만 있고
    상대강도/주도섹터/실적가속이 빠져 있어 별도 전략으로 신규 구현.

    진입 (전부 충족):
    1) 종목 소속 sector_large가 주도섹터 랭킹(전체멤버 평균ret20) 상위 top_sectors개
    2) 현재가 >= 최근 252일 고점 x pct_of_52w_high (신고가권)
    3) MA20 > MA60 (MA 정배열) & 현재가 > MA20 (추세 위에 위치)
    4) 거래량 재증가: 5일평균 거래량 > 20일평균 x vol_ratio_min
    5) 상대강도: 종목 60일수익률 > KOSPI 60일수익률 (require_rs=True 시)
    6) require_earnings_accel=True 시: 매출/영업이익 YoY 가속 또는 흑자전환 추가 요구

    매도: 손절(stop) / 고점대비 추적손절(trail, 이익5%+ 발동) /
          MA역전(MA20<MA60) / 섹터이탈(히스테리시스 exit_sector_rank)
    -- se_momentum과 동일한 현금원장·D+1 시가체결·as-of 시총 엔진 재사용.
    """
    init_backtest_db()
    run_name = run_name or f"V-PEAK {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "peak_easy", "peak_easy_v1_20260722",
        {"stop": stop, "trail": trail, "top_sectors": top_sectors,
         "exit_sector_rank": exit_sector_rank, "min_sector_ret20": min_sector_ret20,
         "basket_per_sector": basket_per_sector, "pct_of_52w_high": pct_of_52w_high,
         "vol_ratio_min": vol_ratio_min, "require_rs": require_rs,
         "require_earnings_accel": require_earnings_accel,
         "min_mktcap_억": min_mktcap_억, "asof_mktcap": asof_mktcap,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'peak_easy',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')

        sec_map: Dict[str, str] = {}
        name_map: Dict[str, str] = {}
        for r in conn.execute(
            "SELECT stock_code, sector_large, stock_name FROM stock_universe "
            "WHERE sector_large IS NOT NULL AND sector_large!=''"
        ).fetchall():
            sec_map[r[0]] = r[1]
            name_map[r[0]] = r[2]

        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND LENGTH(p.stock_code)=6 AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= ? AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6 AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date, min_mktcap_억)).fetchall()
        codes = [(c, m) for c, m in codes if c in sec_map]

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality))

        def _shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        kospi_rows = conn.execute(
            "SELECT date, close FROM price_history WHERE stock_code='^KS11' "
            "AND date>=? AND date<=? AND close>0 ORDER BY date",
            (warmup_start, end_date)
        ).fetchall()
        kospi_c: Dict[str, float] = {r[0]: float(r[1]) for r in kospi_rows}
        kospi_dates = sorted(kospi_c.keys())

        def _kospi_ret60(day: str) -> Optional[float]:
            idx = [i for i, d in enumerate(kospi_dates) if d <= day]
            if not idx or idx[-1] < 60:
                return None
            i = idx[-1]
            c0, c60 = kospi_c[kospi_dates[i]], kospi_c[kospi_dates[i-60]]
            return (c0 - c60) / c60 * 100 if c60 > 0 else None

        kospi_ma120: Dict[str, float] = {}
        if require_market_uptrend:
            kv = [kospi_c[d] for d in kospi_dates]
            for i in range(119, len(kospi_dates)):
                kospi_ma120[kospi_dates[i]] = sum(kv[i-119:i+1]) / 120

        def _kospi_uptrend(day: str) -> bool:
            idx = [d for d in kospi_dates if d <= day]
            if not idx:
                return True
            last = idx[-1]
            ma = kospi_ma120.get(last)
            return ma is None or kospi_c[last] > ma

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0), COALESCE(open,close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 260: continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows], 'c': c_list,
                'v': [float(r[2]) for r in rows], 'o': [float(r[3]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap_억,
            }

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        earn_fins: Dict[str, list] = {}
        if require_earnings_accel and sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.revenue, f.operating_profit, f.net_income, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, avail_date
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                earn_fins.setdefault(r[0], []).append(
                    (r[6], r[1], r[2], r[3], r[4], r[5]))

        def _earnings_accel_ok(code: str, day: str) -> bool:
            fl = earn_fins.get(code)
            if not fl: return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5: return False
            cur, prev_y = avail[-1], avail[-5]
            rev_now, op_now, ni_now = cur[1], cur[2], cur[3]
            rev_1y, op_1y = prev_y[1], prev_y[2]
            rev_yoy_ok = bool(rev_now and rev_1y and rev_1y > 0 and rev_now > rev_1y)
            op_yoy_ok = bool(op_now is not None and op_1y is not None and op_1y > 0 and op_now > op_1y)
            if rev_yoy_ok and op_yoy_ok:
                return True
            if ni_now is not None and ni_now > 0:
                if any(x[3] is not None and x[3] < 0 for x in avail[-4:-1]):
                    return True
            return False

        def _ret20(code: str, day: str) -> Optional[float]:
            i = didx[code].get(day)
            if i is None or i < 20: return None
            c = sd[code]['c']
            return (c[i] - c[i-20]) / c[i-20] * 100 if c[i-20] > 0 else None

        def _sector_ranking(day: str):
            agg: Dict[str, list] = {}
            for code in sd:
                r = _ret20(code, day)
                if r is not None:
                    agg.setdefault(sec_map[code], []).append(r)
            ranked = sorted(
                ((sec, sum(v)/len(v)) for sec, v in agg.items() if len(v) >= 5),
                key=lambda x: -x[1])
            rank_map = {sec: (idx + 1, avg) for idx, (sec, avg) in enumerate(ranked)}
            entry_set = {sec for sec, avg in ranked[:top_sectors] if avg >= min_sector_ret20}
            return entry_set, rank_map

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        for day in sim_dates:
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos:
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                               'entry': p['entry'], 'exit': fill, 'pnl_pct': net_pct,
                               'reason': reason, 'pnl': round(pnl, 0)})
                del pending_sells[code]

            marked_equity = cash + sum(
                p['shares'] * sd[code]['c'][didx[code][day]]
                for code, p in pos.items() if day in didx[code]
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None:
                    continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // fill)
                    if shares > 0:
                        cash -= shares * fill
                        pos[code] = {'entry': fill, 'shares': shares, 'buy_date': day,
                                     'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap_억)}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                pending_buys.remove(code)

            top_secs, sec_rank = _sector_ranking(day)

            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None or i < 60: continue
                c = sd[code]['c']
                curr = c[i]
                if curr <= 0: continue
                ret = (curr - p['entry']) / p['entry']
                peak = max(p.get('peak', p['entry']), curr)
                p['peak'] = peak
                ma20 = sum(c[i-19:i+1]) / 20
                ma60 = sum(c[i-59:i+1]) / 60
                sec = sec_map.get(code)
                rk, sec_avg = sec_rank.get(sec, (999, -999.0))
                reason = None
                if ret < stop:
                    reason = 'stop'
                elif trail is not None and ret > 0.05 and (curr - peak) / peak < trail:
                    reason = 'trail'
                elif ma20 < ma60:
                    reason = 'ma_reverse'
                elif rk > exit_sector_rank or sec_avg < 0:
                    reason = 'sector_exit'
                if reason:
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue
            if not top_secs:
                continue
            if require_market_uptrend and not _kospi_uptrend(day):
                continue

            cand = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                if sec_map.get(code) not in top_secs: continue
                nm = name_map.get(code, '')
                if '지주' in nm or '홀딩스' in nm: continue
                i = didx[code].get(day)
                if i is None or i < 252: continue
                c = s['c']
                curr = c[i]
                if curr < 500: continue
                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    mc = sh * curr / 1e8
                    if sh <= 0 or mc < min_mktcap_억:
                        continue
                else:
                    mc = s.get('mkt_cap_억', 0)
                ma20 = sum(c[i-19:i+1]) / 20
                ma60 = sum(c[i-59:i+1]) / 60
                if not (ma20 > ma60 and curr > ma20): continue
                high_252 = max(c[i-251:i+1])
                if high_252 <= 0 or curr < high_252 * pct_of_52w_high: continue
                v = s['v']
                v20 = sum(v[i-19:i+1]) / 20
                v5 = sum(v[i-4:i+1]) / 5
                if not (v20 > 0 and v5 > v20 * vol_ratio_min): continue
                if require_rs:
                    ret60 = (curr - c[i-60]) / c[i-60] * 100 if c[i-60] > 0 else None
                    k60 = _kospi_ret60(day)
                    if ret60 is None or k60 is None or ret60 <= k60: continue
                if require_earnings_accel and not _earnings_accel_ok(code, day): continue
                cand.append((mc, code))

            cand.sort(reverse=True)
            per_sec_count: Dict[str, int] = {}
            available = max(0, position_limit - len(pos) - len(pending_buys))
            for mc, code in cand:
                if available <= 0: break
                sec = sec_map[code]
                if per_sec_count.get(sec, 0) >= basket_per_sector: continue
                pending_buys.append(code)
                per_sec_count[sec] = per_sec_count.get(sec, 0) + 1
                available -= 1

        final_val = cash
        for code, p in pos.items():
            last_c = None
            for d in reversed(sim_dates):
                i = didx[code].get(d)
                if i is not None and sd[code]['c'][i] > 0:
                    last_c = sd[code]['c'][i]; break
            if last_c:
                pnl, net_pct = _net_profit(p['entry'], last_c, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                final_val += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': sim_dates[-1],
                               'entry': p['entry'], 'exit': last_c,
                               'pnl_pct': net_pct, 'reason': 'final', 'pnl': round(pnl, 0)})

        init_cap = per_stock * max_positions
        total_ret = (final_val - init_cap) / init_cap * 100
        closed = [t for t in trades if 'sell_date' in t and t.get('reason') != 'buy']
        n_trades = len(closed)
        win_rate = sum(1 for t in closed if t.get('pnl_pct', 0) > 0) / max(n_trades, 1) * 100
        days_held = (datetime.strptime(end_date, '%Y-%m-%d') -
                     datetime.strptime(start_date, '%Y-%m-%d')).days
        ann_ret = ((1 + total_ret / 100) ** (365 / max(days_held, 1)) - 1) * 100

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?,
                win_rate=?, total_trades=?, trades_json=?, summary_text=?
            WHERE run_id=?
        """, (round(total_ret, 2), round(ann_ret, 2), round(win_rate, 2), n_trades,
              json.dumps(trades, ensure_ascii=False),
              f"엄격 다음날시가·정수주식 | 총수익 {total_ret:.1f}% | 연환산 {ann_ret:.1f}% | 승률 {win_rate:.0f}% | {n_trades}거래",
              run_id))
        conn.commit()
        _register_execution_artifacts(run_id, init_cap, final_val)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_low_base_breakout(
    start_date: str, end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    run_name: str = None, run_id: str = None,
    ma60_range_min: float = -0.18,   # MA60 대비 하단 (-18%)
    ma60_range_max: float = +0.10,   # MA60 대비 상단 (+10%, 막 돌파한 경우 포함)
    pct_from_low_max: float = 65.0,  # 52주 저점 대비 +65% 이내 (3배+ 종목 83% 포착)
    ma20_gap_max: float = 0.08,      # MA20이 MA60 대비 -8% 이내 (수렴 중)
    min_up_days: int = 3,            # 최근 5일 중 상승일 (가격 모멘텀 확인)
    stop: float = -0.10,             # 손절 -10%
    trail: float = -0.15,            # 이익 달성 후 추적손절 -15%
    trail_mid: float = -0.20,        # 30%+ 이익 시 -20%
    trail_big: float = -0.25,        # 80%+ 이익 시 -25% (대박 홀드)
    max_hold: int = 270,
    asof_mktcap: bool = False,       # 2026-07-17 as-of 재검증: current 대비 악화로 기각 → False 유지 (signal_experiment_ledger: low_base_breakout/no_new_signal)
    chart_confluence: bool = False,  # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
) -> str:
    """
    저점기반 돌파 전략 (V-LOWBASE).

    [실증 기반 설계 근거]
    3배+ 달성 종목 200건 분석(2022-2025):
    - 86%가 MA60 ±15% 이내 또는 MA60 상단 → 깊은 낙폭 불필요
    - 65%가 52주 저점 +30% 이내, 83%가 +60% 이내 → 저점 근방 집중
    - 44%가 거래량 0.8배 미만 → 낮은 거래량(축적) 상태에서도 가능
    V-GC(avg5=+50%)는 골든크로스 후 진입, V-LOWBASE는 골든크로스 직전/직후 초기 진입.

    진입 조건:
    A) MA60 대비 -18%~+10% 범위 (저점 근방 + 막 돌파)
    B) 52주 저점 대비 +0~65% 이내 (저점 기반 종목)
    C) MA20이 MA60 대비 -8% 이내 (골든크로스 수렴 중)
    D) 최근 5일 중 3일 이상 상승 (가격 모멘텀)
    E) 시총 300억+ (KOSPI/KOSDAQ 한정)

    매도: Trail-15%(이익후)/Trail-20%(30%+)/Trail-25%(80%+) / 손절-10% / 만료 270일
    """
    init_backtest_db()
    run_name = run_name or f"V-LOWBASE저점돌파 {start_date[:7]}~{end_date[:7]}"
    _lowbase_params = {"per_stock": per_stock, "max_positions": max_positions,
                       "start": start_date, "end": end_date}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute(
            "INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status) "
            "VALUES (?,?,'low_base_breakout',?,?,?,?,'running')",
            (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running',strategy='low_base_breakout' WHERE run_id=?",
                     (run_id,))
        conn.commit()

    _record_run_spec(run_id, "low_base_breakout", "lowbase_v2_strict_20260715",
                     {**_lowbase_params, "asof_mktcap": asof_mktcap, "chart_confluence": chart_confluence},
                     signal_timing="close_D", execution_timing="next_open",
                     market_cap_mode=("asof_approx" if asof_mktcap else "current"),
                     allocation_rule="fixed_slot",
                     universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current")
    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=390)).strftime('%Y-%m-%d')

        # 종목 로드: 시총 300억+(as-of 가능 시 security_master_history), KOSPI/KOSDAQ, 6자리
        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code = su.stock_code
                WHERE p.date >= ? AND p.date <= ? AND p.close >= 500
                  AND LENGTH(p.stock_code) = 6
                  AND p.stock_code NOT LIKE '%^%'
                  AND p.stock_code NOT LIKE '%=%'
                  AND p.stock_code NOT LIKE 'GC%' AND p.stock_code NOT LIKE 'CL%'
                  AND p.stock_code NOT LIKE '%-F' AND p.stock_code NOT LIKE 'NQ%'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                INNER JOIN stock_universe su ON p.stock_code = su.stock_code
                WHERE p.date >= ? AND p.date <= ? AND p.close >= 500
                  AND su.market_cap >= 300
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code) = 6
                  AND p.stock_code NOT LIKE '%^%'
                  AND p.stock_code NOT LIKE '%=%'
                  AND p.stock_code NOT LIKE 'GC%' AND p.stock_code NOT LIKE 'CL%'
                  AND p.stock_code NOT LIKE '%-F' AND p.stock_code NOT LIKE 'NQ%'
                ORDER BY su.market_cap DESC
            """, (start_date, end_date)).fetchall()

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

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0), COALESCE(high,close),
                       COALESCE(low,close), COALESCE(open,close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 130: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 필터
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'h': [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows],
                'o': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        for day in sim_dates:
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos: continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['qty'], p['mkt_cap_억'])
                cash += p['invested'] + pnl
                trades.append({'code': code, 'entry': p['entry'], 'exit': fill,
                               'ret': net_pct, 'reason': reason, 'hold': p['hold'],
                               'pnl': pnl, 'entry_date': p['entry_date'], 'exit_date': day})
                del pending_sells[code]

            marked_equity = cash + sum(
                p['qty'] * sd[code]['c'][didx[code][day]]
                for code, p in pos.items() if day in didx[code]
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None: continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    invest = min(per_stock, cash)
                    qty = int(invest // fill)
                    if qty > 0:
                        invested = qty * fill
                        cash -= invested
                        pos[code] = {'entry': fill, 'qty': qty, 'invested': invested,
                                     'peak': fill, 'hold': 0,
                                     'mkt_cap_억': sd[code]['mkt_cap_억'],
                                     'entry_date': day}
                pending_buys.remove(code)

            # 매도 체크
            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue
                entry = p['entry']
                peak = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1
                ret = (curr - entry) / entry
                # 계층형 Trail
                if ret >= 0.80: tpct = trail_big
                elif ret >= 0.30: tpct = trail_mid
                else: tpct = trail
                trail_cond = (curr - peak) / peak < tpct
                stop_cond  = ret < stop
                expire_cond = p['hold'] >= max_hold
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN
                if stop_cond or trail_cond or expire_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'chart_top' if chart_top_cond else 'expire')
                    pending_sells[code] = reason

            # 매수 체크 (포지션 여유 있을 때)
            if len(pos) + len(pending_buys) < position_limit:
                candidates = []
                for code, s in sd.items():
                    if code in pos or code in pending_buys: continue
                    i = didx[code].get(day)
                    if i is None or i < 260: continue
                    c_arr = s['c']
                    v_arr = s['v']
                    lo_arr = s['lo']
                    curr = c_arr[i]
                    if curr <= 0: continue

                    # 조건 E: 시총 300억+ (as-of)
                    if asof_mktcap:
                        sh = _shares_asof(code, day)
                        if sh <= 0 or sh * curr / 1e8 < 300:
                            continue

                    # MA20, MA60
                    ma20 = sum(c_arr[i-19:i+1]) / 20
                    ma60 = sum(c_arr[i-59:i+1]) / 60
                    if ma60 <= 0: continue

                    # 조건 A: MA60 대비 -18%~+10%
                    ma60_depth = (curr - ma60) / ma60
                    if ma60_depth < ma60_range_min or ma60_depth > ma60_range_max:
                        continue

                    # 조건 B: 52주 저점 대비 +0~65%
                    lo52 = min(lo_arr[max(0, i-260):i+1]) if i >= 260 else min(lo_arr[:i+1])
                    if lo52 <= 0: continue
                    pct_from_low = (curr - lo52) / lo52 * 100
                    if pct_from_low > pct_from_low_max: continue

                    # 조건 C: MA20이 MA60 대비 -8% 이내 (수렴 중)
                    ma20_gap = (ma20 - ma60) / ma60
                    if ma20_gap < -ma20_gap_max: continue

                    # 조건 D: 최근 5일 중 3일 이상 상승
                    if i < 4: continue
                    recent5 = c_arr[i-4:i+1]
                    up_days = sum(1 for j in range(1, 5) if recent5[j] > recent5[j-1])
                    if up_days < min_up_days: continue

                    # 스코어 계산 (정렬용)
                    # 저점에 가까울수록 높은 점수
                    low_score  = max(0, 65 - pct_from_low)   # 0~65 → 역방향
                    # MA60 수렴도 (가까울수록 좋음)
                    gap_score  = max(0, 8 - abs(ma20_gap * 100))  # 0~8
                    # 스코어 합산
                    score = low_score + gap_score
                    # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                    if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                        continue
                    candidates.append((score, code, curr))

                candidates.sort(reverse=True)
                buy_count = max(0, position_limit - len(pos) - len(pending_buys))
                pending_buys.extend(code for _, code, _ in candidates[:buy_count])

        # 미청산 포지션 강제 청산
        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0: curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['qty'], p['mkt_cap_억'])
            cash += p['invested'] + pnl
            trades.append({'code': code, 'entry': p['entry'], 'exit': curr,
                           'ret': net_pct, 'reason': 'force_close',
                           'hold': p['hold'], 'pnl': pnl,
                           'entry_date': p['entry_date'], 'exit_date': last_day})
            pos.pop(code, None)

        # 결과 집계
        initial = per_stock * max_positions
        if not trades:
            total_return = 0.0; win_rate = 0.0; trade_cnt = 0
            final = initial
        else:
            final   = cash
            total_return = (final - initial) / initial * 100
            wins = [t for t in trades if t['pnl'] > 0]
            win_rate = len(wins) / len(trades) * 100
            trade_cnt = len(trades)

        avg_ret = sum(t['ret'] for t in trades) / len(trades) if trades else 0
        days_held = max((datetime.strptime(end_date, '%Y-%m-%d') -
                         datetime.strptime(start_date, '%Y-%m-%d')).days, 1)
        ann_return = ((1 + total_return / 100) ** (365 / days_held) - 1) * 100 if total_return > -100 else -100
        summary = (f"V-LOWBASE 엄격 다음날시가·정수주식·복리 | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{trade_cnt}건 | 평균:{avg_ret:.1f}%")

        conn.execute(
            """
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
            """,
            (
                round(total_return, 2),
                round(ann_return, 2),
                round(win_rate, 2),
                trade_cnt,
                len([t for t in trades if t.get('pnl', 0) > 0]),
                summary,
                __import__('json').dumps(trades[-50:]),
                run_id,
            ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, initial, final)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


# ─── V-TURNAROUND 흑자전환 특화 전략 ────────────────────────────────────────

def run_backtest_turnaround(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.13,            # v3: -0.13 최적화 (avg5=20.6%, ALL기간 양수)
    trail: float = -0.25,           # 기본 추적손절
    trail_big: float = -0.30,       # 50%+ 이익 시 더 넓게
    tp: float = 999.0,              # 익절 없음 — 흑자전환 후 폭발적 상승 기대
    max_hold: int = 300,
    hi52_drop_min: float = -0.30,   # 52주 고가 대비 최소 낙폭 (BQ: 70.5% 출발점)
    hi52_drop_max: float = -0.65,   # 너무 깊으면 상장폐지 위험
    max_pbr: float = 1.5,           # PBR 저평가 필터
    min_mktcap: int = 200,          # 시총 200억+ (억원 단위)
    vol_ratio: float = 1.3,         # 거래량 확인 (관심 시작 신호)
    strict_exec: bool = True,       # 2026-07-13 (Codex 계약): D종가 신호 → D+1 시가 체결
    asof_mktcap: bool = True,       # 2026-07-17 기본화: 시총 필터를 as-of(security_master_history)로 적용
    chart_confluence: bool = False, # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    흑자전환 특화 전략 (V-TURNAROUND).

    [데이터 기반 핵심 인사이트] BigQuery 1,324종목 전수 분석:
      흑자전환 종목 평균 수익률: 6.14x (우량성장주 3.48x의 1.77배!)
      40.5%가 매수 시점에 적자 상태 → 흑자전환 시 대형 모멘텀 발생

    [진입 조건] v2 (2026-07-04 TTM 필터)
    A) 흑자전환: 최근 공시 분기 NI > 0 + 직전 3분기 중 1분기 이상 NI < 0
       + TTM(최근 4분기 NI 합계) > 0 (임시 반등 아닌 실질 흑자전환)
    B) 낙폭과대: 52주 고가 대비 -30~-65% (BQ 실증: 이 구간에서 70.5% 출발)
    C) PBR < 1.5 (저평가 — 이미 가격에 부정적 기대 반영)
    D) 거래량 > 20일 평균 × 1.3 (관심 증가 확인)
    E) 시총 200억+ (불량기업 제외)
    F) KOSPI MA120 × 0.85 이상 (완전 패닉장 제외 — 흑자전환은 패닉장 제외 허용)

    [매도 조건]
    - Trail -25% (이익 발생 후)  /  Trail -30% (50%+ 이익 시 더 넓게)
    - 손절 -12% (v2: -15%→-12%, stop-out 빈도 감소)
    - 최대 보유 300일
    """
    init_backtest_db()
    run_name = run_name or f"V-TURNAROUND흑자전환 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "turnaround", "ta_v4_strict_20260714",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "max_hold": max_hold,
         "hi52_drop_min": hi52_drop_min, "hi52_drop_max": hi52_drop_max,
         "max_pbr": max_pbr, "min_mktcap": min_mktcap, "vol_ratio": vol_ratio,
         "strict_exec": strict_exec, "asof_mktcap": asof_mktcap,
         "chart_confluence": chart_confluence,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'turnaround',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        _load_disc_dates(conn)
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=400)).strftime('%Y-%m-%d')

        # KOSPI 시장 필터
        k_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date
        """).fetchall()
        k_dates  = [r[0] for r in k_rows]
        k_prices = [float(r[1]) for r in k_rows]
        k_idx    = {d: i for i, d in enumerate(k_dates)}

        def _k_ma120(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 120: return None
            return sum(k_prices[idx-119:idx+1]) / 120

        def _k_ma60(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 60: return None
            return sum(k_prices[idx-59:idx+1]) / 60

        # 재무 데이터 로드 (분기 + 연간, 공시일 포함)
        # 컬럼: year,quarter,rev,op,eps,bps,equity,net_inc,roe,is_annual,avail_date
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
                          ELSE printf('%d-02-15', f.year+1) END
                   ) as avail_date
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d ON
                d.stock_code = f.stock_code AND d.year = f.year
                AND d.quarter = CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                AND d.is_annual = CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
            WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 3)
               OR (f.is_annual=1)
            ORDER BY f.stock_code, f.year, f.quarter
        """).fetchall():
            sc = r[0]
            fin_all.setdefault(sc, []).append(r[1:])

        # 분기 NI 이력 사전 구성 (종목별, 시간순 정렬)
        # ni_hist[code] = [(avail_date, year, quarter, net_income), ...]  ASC by (year, quarter)
        ni_hist: Dict[str, list] = {}
        for sc, rows in fin_all.items():
            q_rows = []
            for row in rows:
                is_ann = row[9]
                if is_ann:  # 연간 제외 (분기만)
                    continue
                y, q, ni = row[0], row[1], row[7]
                if ni is None:
                    continue
                avail = row[10] if (len(row) > 10 and row[10]) else _release_date(y, q, False)
                q_rows.append((avail, y, q, ni))
            q_rows.sort(key=lambda x: (x[1], x[2]))  # year, quarter 오름차순
            if q_rows:
                ni_hist[sc] = q_rows

        def _get_turnaround(code: str, target_date: str):
            """
            Returns (latest_ni, neg_count) if turnaround detected, else None.
            - latest_ni: 최근 공시 분기 NI (양수)
            - neg_count: 직전 3분기 중 음수 분기 수
            TTM NI 합계 > 0: 실질적 흑자전환 (임시 반등 제외), 시장국면 무관 균일 적용
            """
            rows = ni_hist.get(code)
            if not rows:
                return None
            available = [(y, q, ni) for avail, y, q, ni in rows if avail <= target_date]
            if len(available) < 2:
                return None
            available.sort(key=lambda x: (x[0], x[1]), reverse=True)
            latest_ni = available[0][2]
            if latest_ni is None or latest_ni <= 0:
                return None
            prev_quarters = available[1:4]  # 직전 3분기
            neg_count = sum(1 for _, _, ni in prev_quarters if ni is not None and ni < 0)
            if neg_count == 0:
                return None  # 적자 이력 없음 → 흑자전환 아님
            # TTM NI 합계가 양수여야 함 (실질적 흑자전환, 임시 반등 제외)
            ttm_nis = [ni for _, _, ni in available[:4] if ni is not None]
            if not ttm_nis or sum(ttm_nis) <= 0:
                return None
            return (latest_ni, neg_count)

        # 종목 로드 (시총 200억+(as-of 가능 시 security_master_history), 분할 필터)
        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= ?
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date, min_mktcap)).fetchall()

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _ta_shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0), COALESCE(high,close), COALESCE(low,close),
                       COALESCE(open, close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 100: continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd':  [r[0]  for r in rows],
                'c':  c_list,
                'v':  [float(r[2]) for r in rows],
                'h':  [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows],
                'o':  [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # valuation_history 기반 시점별 PBR 로드
        pbr_hist: Dict[str, list[tuple[str, float]]] = {}
        if sd:
            ph = ','.join('?' * len(sd))
            for r in conn.execute(
                f"""
                SELECT stock_code, period_end, pbr
                FROM valuation_history
                WHERE stock_code IN ({ph})
                  AND pbr IS NOT NULL
                  AND pbr > 0
                ORDER BY stock_code, period_end
                """,
                list(sd.keys())
            ).fetchall():
                pbr_hist.setdefault(r[0], []).append((r[1], float(r[2])))

        fallback_pbr_map: Dict[str, float] = {}
        if sd:
            ph = ','.join('?' * len(sd))
            for r in conn.execute(
                f"SELECT stock_code, COALESCE(pbr, 9.9) FROM stock_universe WHERE stock_code IN ({ph})",
                list(sd.keys())
            ).fetchall():
                fallback_pbr_map[r[0]] = float(r[1]) if r[1] is not None else 9.9

        def _pbr_as_of(code: str, target_date: str) -> float:
            hist = pbr_hist.get(code)
            if hist:
                dates = [d for d, _ in hist]
                idx = bisect_right(dates, target_date) - 1
                if idx >= 0:
                    return hist[idx][1]
            return fallback_pbr_map.get(code, 9.9)

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        # 흑자전환 감지 캐시 (동일 종목을 매일 재검사 비용 절감)
        # 캐시 키: (code, month) — 같은 달은 동일 결과로 가정
        ta_cache: Dict[tuple, object] = {}

        ta_pending_sells: list = []
        ta_pending_buys: list = []

        for day in sim_dates:
            # ── strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약) ──
            if strict_exec:
                _still = []
                for code, reason in ta_pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', min_mktcap))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                ta_pending_sells = _still
                for code in ta_pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares < 1:
                        continue
                    cash -= shares * px
                    pos[code] = {
                        'entry': px, 'shares': shares, 'buy_date': day,
                        'hold': 0, 'peak': px,
                        'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap),
                    }
                    trades.append({'code': code, 'buy_date': day, 'entry': px,
                                   'shares': shares, 'action': 'buy'})
                ta_pending_buys = []

            # ── 매도 체크 ───────────────────────────────────────────
            to_sell = []
            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue

                entry = p['entry']
                peak  = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1

                ret = (curr - entry) / entry
                tpct = trail_big if ret >= 0.50 else trail
                trail_cond  = (curr - peak) / peak < tpct
                stop_cond   = ret < stop
                tp_cond     = ret >= tp
                expire_cond = p['hold'] >= max_hold
                # 손실 타임아웃: 120일 후에도 -8% 이하 손실 지속 → 스토리 미발현
                momentum_timeout = p['hold'] >= 120 and ret < -0.08
                # 고점 컨플루언스 청산 (2026-07-18 공통모듈)
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN

                if stop_cond or trail_cond or tp_cond or expire_cond or momentum_timeout or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond
                              else 'tp' if tp_cond else
                              'momentum_timeout' if momentum_timeout else
                              'chart_top' if chart_top_cond else 'expire')
                    to_sell.append((code, curr, ret, reason))

            if strict_exec:
                for code, curr, ret, reason in to_sell:
                    if not pos[code].get('pending_exit'):
                        pos[code]['pending_exit'] = reason
                        ta_pending_sells.append((code, reason))
            else:
                for code, curr, ret, reason in to_sell:
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', min_mktcap))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': curr,
                        'pnl_pct': net_pct, 'reason': reason,
                        'pnl': round(pnl, 0),
                    })

            if len(pos) >= max_positions:
                continue

            # KOSPI 필터 (패닉장 제외 — MA120×0.85, 흑자전환은 패닉장 제외 나머지는 허용)
            kma120 = _k_ma120(day)
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None and k_prices[ki] < kma120 * 0.85:
                    continue

            # ── 매수 후보 탐색 ──────────────────────────────────────
            candidates = []
            day_month = day[:7]  # YYYY-MM

            for code, s in sd.items():
                if code in pos: continue
                i = didx[code].get(day)
                if i is None or i < 90: continue

                c   = s['c']
                v   = s['v']
                h   = s['h']
                curr = c[i]
                if curr < 500: continue

                # [E] 시총 200억+ (as-of): 신호일 기준 주가×상장주식수
                if asof_mktcap:
                    _sh = _ta_shares_asof(code, day)
                    if _sh <= 0 or _sh * curr / 1e8 < min_mktcap:
                        continue

                # [B] 52주 고가 대비 낙폭 체크
                hi_252 = s['h'][max(0, i-251):i+1]
                high52 = max(hi_252) if hi_252 else curr
                if high52 <= 0: continue
                hi_drop = (curr - high52) / high52  # 음수
                if hi_drop > hi52_drop_min or hi_drop < hi52_drop_max:
                    continue

                # [D] PBR 필터 (과거 시점 valuation_history 우선)
                pbr = _pbr_as_of(code, day)
                if pbr > max_pbr or pbr <= 0:
                    continue

                # [E] 거래량 체크
                v_now  = v[i]
                v_avg20 = sum(v[max(0, i-20):i]) / max(1, min(20, i))
                if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * vol_ratio:
                    continue

                # [A] 흑자전환 체크 (캐시 활용)
                cache_key = (code, day_month)
                ta_result = ta_cache.get(cache_key, 'MISS')
                if ta_result == 'MISS':
                    ta_result = _get_turnaround(code, day)
                    ta_cache[cache_key] = ta_result
                if ta_result is None:
                    continue

                latest_ni, neg_count = ta_result

                # 복합 점수 산출
                # 낙폭 깊이 (BQ 실증: 깊을수록 좋음)
                depth_score = min(-hi_drop * 100, 55.0)  # 최대 55점
                # 적자 분기 수 보너스 (더 오래 적자일수록 반전 모멘텀 ↑)
                neg_bonus = min(neg_count * 10.0, 30.0)  # 최대 30점 (3분기×10)
                # PBR 저평가 보너스
                pbr_bonus = max(0.0, (max_pbr - pbr) / max_pbr * 15.0)  # 최대 15점
                score = depth_score + neg_bonus + pbr_bonus

                # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)

            for score, code, curr, i in candidates[:3]:
                if strict_exec:
                    if code not in pos and code not in ta_pending_buys and \
                       len(pos) + len(ta_pending_buys) < max_positions:
                        ta_pending_buys.append(code)
                    continue
                if len(pos) >= max_positions: break
                if cash < curr * 100: continue
                budget = min(per_stock, cash * 0.99)
                shares = int(budget // curr)
                if shares < 1: continue
                cost   = shares * curr
                cash  -= cost
                pos[code] = {
                    'entry': curr, 'shares': shares, 'buy_date': day,
                    'hold': 0, 'peak': curr,
                    'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap),
                }
                trades.append({
                    'code': code, 'buy_date': day, 'entry': curr,
                    'shares': shares, 'action': 'buy',
                })

        # ── 최종 청산 ────────────────────────────────────────────────
        sell_trades = [t for t in trades if 'sell_date' in t]
        for code, p in pos.items():
            last_price = sd[code]['c'][-1] if sd.get(code, {}).get('c') else p['entry']
            pnl, net_pct = _net_profit(p['entry'], last_price, p['shares'], p.get('mkt_cap_억', min_mktcap))
            sell_trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': end_date,
                'entry': p['entry'], 'exit': last_price,
                'pnl_pct': net_pct, 'reason': 'end',
                'pnl': round(pnl, 0),
            })
            cash += p['shares'] * p['entry'] + pnl

        init_cap = per_stock * max_positions
        portfolio_return = (cash - init_cap) / init_cap * 100
        win_rate = (sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0) /
                    max(1, len(sell_trades)) * 100)
        avg_trade = (sum(t.get('pnl_pct', 0) for t in sell_trades) /
                     max(1, len(sell_trades)))

        summary = (f"[V-TURNAROUND] {start_date[:7]}~{end_date[:7]} "
                   f"수익률={portfolio_return:+.1f}% 승률={win_rate:.0f}% "
                   f"거래={len(sell_trades)}건 avg={avg_trade:+.1f}%")
        print(summary)

        conn.execute("""
            UPDATE backtest_runs SET status='done', summary_text=?, total_return_pct=?,
              win_rate=?, total_trades=?, profit_trades=?, trades_json=?
            WHERE run_id=?
        """, (
            summary, round(portfolio_return, 2), round(win_rate, 1),
            len(sell_trades), sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0),
            json.dumps({'trades': sell_trades, 'avg_trade_return_pct': round(avg_trade, 2),
                        'portfolio_return_pct': round(portfolio_return, 2)}, ensure_ascii=False),
            run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, init_cap, cash)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


# ─── V-MEGATREND: 구조적 테마 추종(분산 바스켓 + 손절규율) ────────────────────

def run_backtest_megatrend(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 30,
    ret6m_min: float = 1.0,       # 6개월 수익률 +100%↑ (2026-07-20 검증: scratch/megatrend_sector_confirm_test.py)
    dist_high_max: float = -0.15,  # 52주 고점 대비 -15% 이내
    stop_loss: float = -0.20,      # 손절 -20% (손실 상한 고정 — 승자 무제한 보유의 전제조건)
    max_hold: int = 252,           # 최대 보유 12개월(거래일 기준) — 검증 시 사용한 forward 윈도우와 동일
    trend_break_ma: int = None,    # 2026-07-20 1차 채택 후 트레일링스탑에 밀려 철회(아래 trail_pct 참조).
                                    # MA120 하향이탈 시 조기청산(이익권 한정) — 시간청산(+77.2%)보다는
                                    # 나았으나(+96.7%) 트레일링스탑(+123.2%)에 못 미쳐 기본값에서 제외.
                                    # None이면 미사용, 값 지정 시 trail_pct와 동시 적용(더 먼저 걸리는 쪽이 발동).
    trail_pct: float = -0.30,      # ★2026-07-20 종합 매도로직 최적화 채택: 고점대비 추적손절(이익권 한정).
                                    # 시간청산/MA이탈/차트컨플루언스/복합조건 12종 전수비교(연속운용
                                    # 2020-03~2026-07-20 기준 총수익률) — trail30 +123.2%(최고) > trail25
                                    # +120.4% > trail35 +117.6% > ma120+trail30 +114.2% > MA200 +99.8% >
                                    # MA120 +96.7% > MA60 +96.2% > 시간청산(원래 기본값) +76.5% > 차트컨플루언스
                                    # 계열 4종 전부 최하위(+59~63%, 승률은 59%로 최고이나 거래빈도 644건으로
                                    # 3배 급증해 승자를 조기 매도 — 추세추종 전략엔 역효과, 신규 확인). 6기간
                                    # 매트릭스도 trail25 avg6=+8.22%(3/6, 트레일 계열 중 최고 일관성)로 개선 확인.
    chart_confluence: bool = False,  # 2026-07-20 실험 후 기각(차트컨플루언스는 추세추종에 역효과, 위 주석 참조).
                                    # 기본 비활성 유지 — 재실험 전 반드시 signal_experiment_ledger 확인.
    universe: str = "all_market",  # ★2026-07-21 채택: 사용자 지시(반도체 외 전력기기/조선/화장품ODM 등
                                    # 2024년 메가랠리도 포착 필요)로 "semiconductor"(151종목 한정)에서
                                    # 확장. 순수 전체시장(2,165종목)은 노이즈 급등 유입으로 오히려 악화
                                    # (연속운용 -6.1%) — sector_filter+sector_confirm_min 조합으로 해결.
    min_mktcap_억: float = 300,     # all_market 유니버스 전용 시총 하한(억원) — 유동성 필터
    sector_confirm_min: int = 3,    # ★2026-07-21 채택: 같은 sector_large 내 동시 조건충족 종목이 3개
                                    # 이상일 때만 매수 — "개별 급등 노이즈"와 "진짜 섹터 재평가"를 구분.
                                    # sector2 +63.9%, sector3 +125.1%(최종채택), sector4 -35.5%(불안정)
                                    # — 3이 안정적 최적점(연속운용 2020-03~2026-07-20, sector_filter 적용시).
    sector_filter: tuple = ("IT", "산업재", "필수소비재"),  # ★2026-07-21 채택: 반도체(IT)+전력기기/
                                    # 조선(산업재)+화장품ODM(필수소비재) — 실제 구조적 메가테마가 확인된
                                    # 3개 섹터로 한정(1,040종목). 순수 전체시장(2,165종목, 필터없음)보다
                                    # 안정적(연속운용 +125.1% vs -6.1%, 6기간 avg +6.68%(3/6) vs 반도체
                                    # 단독 +7.8%(2/6)와 대등하거나 더 일관적). None=전체시장(비권장).
    min_price: float = 50000,      # 2026-07-22 실측 진단+채택: 24.6~25.5구간 99건 중 71건 손절(승률22%)
                                    # 원인 분석 결과 진입가 10만원+ 승률45%/+15.2% vs 10만원미만 승률12~27%/
                                    # -8~-16% — sector_large가 방산·조선(대형주)과 저가 투기성 급등주를
                                    # 구분 못해 동일 섹터로 대량 혼입시킨 것이 확인됨. 6기간 검증: avg6는
                                    # 거의 그대로(6.68%→6.32%)지만 하락장 리스크가 극적으로 감소
                                    # (-35.2%→-7.1%) — 완전한 해결책은 아니고 "다운사이드 방어" 트레이드오프.
    require_earnings_accel: bool = False,  # 2026-07-22 실험: 사용자 제안 — 가격 대신/추가로 매출·영업이익
                                    # YoY 가속 또는 흑자전환(as-of) 요구. se_momentum/V-PEAK와 동일 헬퍼 재사용.
    smart_money_min_score: int = 0,  # 2026-07-22 실험: case-control walk-forward(반도체제외 300%+ 종목 vs
                                    # 대조군, 학습~2022/검증2023~)에서 유일하게 학습·검증 방향이 일치하고
                                    # 검증기에서 더 강화된 신호. 점수=[신용잔고비율<3%]+[기관+외인20일수급 강한매수(10억+)].
                                    # 0(미적용)/1/2로 게이트. 라벨판별력(2점: 학습1.40x/검증2.39x)이 실전
                                    # 백테스트 수익률 개선으로 이어지는지 검증 중 — 기본값 0(미적용).
    exclude_quality_risk: bool = False,  # 2026-07-26 실험: Codex가 신규 구축한 inventory_sales_signals/
                                    # cash_conversion_signals(재고build_up·현금전환불량 risk_score>=4)를
                                    # PIT 제외필터로 적용. Codex 자체 event-study에서 exclude_quality_risk는
                                    # 소폭 개선(avg12 +73.68 vs 기준 +73.33, PF 339.85 vs 288.20)이었으나,
                                    # 이 신호를 "랭킹 가점"으로 쓴 monthly_top20 실행백테스트는 대폭 악화
                                    # (Overlay Top10 +30.38% vs Model Top10 +173.85%, 라벨스윕이 실현가능
                                    # 수익률을 과대평가) — 랭킹가점이 아닌 "이미 선정된 진입신호를 거부만
                                    # 하는" 순수 제외필터로 V-MEGATREND에서 별도 검증 필요(기본값 False,
                                    # scratch/megatrend_quality_risk_exclude_test_20260726.py 참조).
    asof_mktcap: bool = True,      # 2026-07-27: universe="all_market"일 때 min_mktcap_억 유니버스 필터가
                                    # stock_universe.market_cap(현재시총) 정적 컷오프였음(진짜 룩어헤드) —
                                    # security_master_history/security_share_history 기반 as-of 시총으로
                                    # 전환. universe="semiconductor"(hand-curated 테이블)에는 영향 없음.
    market_regime_gate_min: float = None,  # 2026-08-23 실험: signal_experiment_ledger id=72~73과
                                    # 동일한 신호(신용잔고비율<3%+기관외인20일동반매수 종목비중)를
                                    # 종목별 필터(id=73, V-MEGATREND 자체게이트로 실패 — 오버샘플링
                                    # 고갈, 5/6구간 거래0건)가 아니라 "시장 전체" 일별 레짐 점수로
                                    # 재구성(quant_market_regime_signal.regime_score, rolling
                                    # z-score 합산, scratch/build_credit_flow_regime.py). 종목 풀을
                                    # 줄이지 않고 레짐이 나쁜 날에만 신규매수 전체를 하루 건너뛰는
                                    # V10 bear_gate와 동일 구조. None=미적용(기본값, 라벨검증은
                                    # scratch/validate_regime_score_forward_return.py — 학습기
                                    # 상관계수0.40/검증기0.19, 3분위 전부 학습·검증 방향 일치).
                                    # ⚠️ 데이터 소스(kiwoom_credit_balance)가 2026-07-07 이후 갱신
                                    # 중단(레거시 테이블, margin_balance_daily로 대체됨) — 이 파라미터는
                                    # 연구/백테스트 전용이며 라이브 게이트로 쓰려면 소스 교체 필요.
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-MEGATREND — 구조적 테마(반도체/전력기기/조선/화장품ODM 등) 추종 전략.

    [배경] 2026-07-20 사용자 지시: V-GC/V-SECTOR가 전공정장비·전력기기 메가랠리를
    트레일링손절(-20~25%)·섹터점수하락 즉시청산 때문에 대부분 놓쳤음이 실거래 검증으로
    확인됨(원익IPS/피에스케이/테스/유진테크 대부분 미보유, 심텍은 -12%손절 직후 +569%
    폭등). "몇 년짜리 구조적 테마를 끝까지 타는" 전략이 없다는 공백을 메우기 위해 신규 설계.
    2026-07-21 사용자 지시로 반도체(151종목) 한정에서 확장: 전력기기/조선/화장품ODM 등
    타업종 메가랠리(2024년 HD현대일렉트릭+861%, 효성중공업+1538%, 2025년 한국콜마/코스메카코리아
    ODM랠리)도 포착 필요. 순수 전체시장(2,165종목) 확장은 노이즈 급등 유입으로 오히려 악화
    (연속운용 -6.1%) — sector_filter(IT+산업재+필수소비재)+sector_confirm_min(3)으로 해결,
    반도체 단독과 대등하거나 더 나은 성과(+125.1%) 확인.

    [데이터 기반 설계] scratch/megatrend_sector_confirm_test.py,
    scratch/megatrend_fundamental_confirm_test.py (walk-forward, n=232건,
    2020-06~2025-06 월별체크포인트, 반도체밸류스트림 151종목 기준 최초 검증):
    - 개별 종목 승률은 19~31%로 낮음(섹터동반강세·매출/영업이익YoY가속 추가해도 무개선,
      기존 avoid_overheat 근거와 일치 — "이미 급등"만으로 다음 승자를 못 고름).
    - 단, 손실을 -20%로 고정하고 승자를 트레일링스탑(-30%)으로 최대한 오래 보유하면
      건당 기대값이 플러스로 재현 — "몇 개 대박이 다수의 소손실을 상쇄" 전형적 fat-tail 구조.
      개별종목 확신이 아니라 분산 바스켓+엄격한 손절 규율이 핵심 전제.

    매수: sector_filter 섹터(기본 IT/산업재/필수소비재) 중 6개월 수익률≥ret6m_min &
         52주고점대비≥dist_high_max & 같은 섹터 내 동시충족 종목≥sector_confirm_min
    매도: 손실≤stop_loss(하드손절) 또는 고점대비 추적손절 trail_pct(기본 -30%, 이익권 한정)
         또는 보유기간≥max_hold(안전망) — 승자를 중간 조정에 흔들리지 않고 최대한 오래 보유.
    """
    init_backtest_db()
    run_name = run_name or f"V-MEGATREND {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _asof_active = bool(asof_mktcap and universe == "all_market")
    _record_run_spec(
        run_id, "megatrend", "megatrend_v4_multisector_20260721",
        {"ret6m_min": ret6m_min, "dist_high_max": dist_high_max, "stop_loss": stop_loss,
         "max_hold": max_hold, "trend_break_ma": trend_break_ma, "trail_pct": trail_pct,
         "chart_confluence": chart_confluence, "max_positions": max_positions,
         "universe": universe, "min_mktcap_억": min_mktcap_억, "asof_mktcap": asof_mktcap,
         "exclude_quality_risk": exclude_quality_risk, "market_regime_gate_min": market_regime_gate_min,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if _asof_active else "not_applicable"),
        allocation_rule="diversified_basket",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'megatrend',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / max_positions, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')

        share_intervals: Dict[str, list] = {}
        def _shares_asof_mt(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        if universe == "all_market":
            # 2026-07-21: 전체 KOSPI/KOSDAQ 보통주(우선주 제외 — collect_dart_cashflow_batch.py에서
            # 발견한 것과 동일한 우선주 명명패턴 필터 재사용), 시총 min_mktcap_억 이상.
            # 2026-07-27: 시총 컷오프는 진짜 룩어헤드였음(현재 market_cap으로 소급 편입) — 종목
            # 유니버스 자체는 시총 무관하게 넓게 잡고(섹터만 필터), 실제 min_mktcap_억 임계값은
            # 아래 매수후보 스캔 루프에서 _shares_asof_mt()로 진입일 as-of 시총으로 매일 재확인.
            _pref_pat = re.compile(r"\d?우[A-Z]?$")
            _sector_clause = ""
            _sector_params: list = []
            if sector_filter:
                _sector_clause = " AND sector_large IN ({})".format(",".join("?" * len(sector_filter)))
                _sector_params = list(sector_filter)
            _mktcap_gate = "" if _asof_active else "AND COALESCE(market_cap, 0) >= ?"
            _mktcap_param = [] if _asof_active else [min_mktcap_억]
            all_rows = conn.execute(f"""
                SELECT stock_code, stock_name, market_cap FROM stock_universe
                WHERE market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
                  {_mktcap_gate}
                  AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  {_sector_clause}
            """, _mktcap_param + _sector_params).fetchall()
            codes = [r[0] for r in all_rows if not (r[1] and _pref_pat.search(r[1]))]
            mktcap_map = {r[0]: (r[2] or 300) for r in all_rows}
            sector_map: Dict[str, str] = {}
            if sector_confirm_min is not None:
                sector_map = {r[0]: r[1] for r in conn.execute(
                    "SELECT stock_code, sector_large FROM stock_universe WHERE stock_code IN ({})".format(
                        ",".join("?" * len(codes))), codes).fetchall()}
            if _asof_active:
                for code, effective_from, effective_to, shares, quality in conn.execute(
                    """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                       FROM security_share_history WHERE stock_code IN ({})
                       ORDER BY stock_code,effective_from""".format(",".join("?" * len(codes))), codes
                ):
                    share_intervals.setdefault(code, []).append(
                        (effective_from, effective_to, float(shares or 0), quality)
                    )
        else:
            sector_map = {}
            codes = [r[0] for r in conn.execute(
                "SELECT DISTINCT stock_code FROM semiconductor_valuestream WHERE stock_code IS NOT NULL"
            ).fetchall()]
            mktcap_map = {r[0]: (r[1] or 300) for r in conn.execute(
                "SELECT stock_code, market_cap FROM stock_universe WHERE stock_code IN ({})".format(
                    ",".join("?" * len(codes))), codes).fetchall()} if codes else {}

        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o,
                       COALESCE(high, close) AS h, COALESCE(low, close) AS lo
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 260:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            d_list = [str(r[0])[:10] for r in rows]  # 드물게 섞인 타임스탬프 오염 방어(_chart_prep strptime 대비)
            lo_list = [float(r[4]) if r[4] and r[4] > 0 else c_list[idx] for idx, r in enumerate(rows)]
            sd[code] = {
                'd': d_list,
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'h': [float(r[3]) if r[3] and r[3] > 0 else c_list[idx] for idx, r in enumerate(rows)],
                'lo': lo_list,
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(d_list, lo_list, c_list)

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))

        regime_score_dates: list = []
        regime_score_vals: list = []
        if market_regime_gate_min is not None:
            _rg_rows = conn.execute(
                "SELECT trade_date, regime_score FROM quant_market_regime_signal "
                "WHERE regime_score IS NOT NULL ORDER BY trade_date"
            ).fetchall()
            regime_score_dates = [r[0] for r in _rg_rows]
            regime_score_vals = [r[1] for r in _rg_rows]

        def _regime_gate_ok(day: str) -> bool:
            """market_regime_gate_min 미만이면 그날 신규매수 전체를 건너뜀(종목풀 축소 아님).
            데이터가 없는 날(과거 구간 밖·소스 미갱신)은 판단불가로 통과시킨다."""
            if market_regime_gate_min is None or not regime_score_dates:
                return True
            idx = bisect.bisect_right(regime_score_dates, day) - 1
            if idx < 0:
                return True
            return regime_score_vals[idx] >= market_regime_gate_min
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        earn_fins: Dict[str, list] = {}
        if require_earnings_accel and sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.revenue, f.operating_profit, f.net_income, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, avail_date
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                earn_fins.setdefault(r[0], []).append(
                    (r[6], r[1], r[2], r[3], r[4], r[5]))

        def _earnings_accel_ok(code: str, day: str) -> bool:
            fl = earn_fins.get(code)
            if not fl: return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5: return False
            cur, prev_y = avail[-1], avail[-5]
            rev_now, op_now, ni_now = cur[1], cur[2], cur[3]
            rev_1y, op_1y = prev_y[1], prev_y[2]
            rev_yoy_ok = bool(rev_now and rev_1y and rev_1y > 0 and rev_now > rev_1y)
            op_yoy_ok = bool(op_now is not None and op_1y is not None and op_1y > 0 and op_now > op_1y)
            if rev_yoy_ok and op_yoy_ok:
                return True
            if ni_now is not None and ni_now > 0:
                if any(x[3] is not None and x[3] < 0 for x in avail[-4:-1]):
                    return True
            return False

        # 2026-07-22: 신용잔고비율+기관외인수급 결합 스코어 (case-control walk-forward 검증됨)
        credit_hist: Dict[str, list] = {}
        flow_hist: Dict[str, list] = {}
        if smart_money_min_score > 0 and sd:
            for r in conn.execute("""
                SELECT stock_code, dt, credit_ratio FROM kiwoom_credit_balance
                WHERE stock_code IN ({}) AND credit_ratio IS NOT NULL
                ORDER BY stock_code, dt
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                credit_hist.setdefault(r[0], []).append((str(r[1])[:10], r[2]))
            for code in sd:
                flow_hist[code] = conn.execute("""
                    SELECT date, COALESCE(inst_net_buy_amt,0), COALESCE(frn_net_buy_amt,0)
                    FROM price_history WHERE stock_code=? ORDER BY date
                """, (code,)).fetchall()

        def _smart_money_score(code: str, day: str) -> int:
            score = 0
            cl = credit_hist.get(code)
            if cl:
                avail = [x for x in cl if x[0] <= day]
                if avail and avail[-1][1] is not None and avail[-1][1] < 3:
                    score += 1
            fl = flow_hist.get(code)
            if fl:
                idx = [i for i, r in enumerate(fl) if r[0] <= day]
                if idx:
                    i = idx[-1]
                    window = fl[max(0, i-19):i+1]
                    if sum(r[1] + r[2] for r in window) >= 1000:
                        score += 1
            return score

        # 2026-07-26: Codex 신규 재고/현금전환 품질리스크 PIT 제외필터 (분기말+60일 지연,
        # Codex의 research_new_quality_factor_validation.py와 동일한 가용시점 계산식 재사용).
        # quality_risk_count(=inventory_risk + cash_risk)와 동일하게 두 테이블을 독립적으로
        # "as-of 최신 1건"만 조회해 OR — 두 신호를 한 리스트에 섞으면 서로 다른 분기 캘린더가
        # 뒤섞여 "직전 2건"류의 근사가 부정확해지므로 반드시 테이블별로 분리 추적한다.
        inv_risk_hist: Dict[str, list] = {}
        cash_risk_hist: Dict[str, list] = {}
        if exclude_quality_risk and sd:
            def _avail_date(year: int, quarter: int) -> str:
                month = quarter * 3
                day = 31 if month in (3, 12) else 30
                base = datetime(int(year), month, day)
                return (base + timedelta(days=60)).strftime("%Y-%m-%d")
            for tbl, dest in (("inventory_sales_signals", inv_risk_hist),
                              ("cash_conversion_signals", cash_risk_hist)):
                for r in conn.execute(f"""
                    SELECT stock_code, fiscal_year, fiscal_quarter, risk_score
                    FROM {tbl} WHERE stock_code IN ({",".join("?" * len(sd))})
                """, list(sd.keys())).fetchall():
                    if r[3] is None:
                        continue
                    dest.setdefault(r[0], []).append((_avail_date(r[1], r[2]), int(r[3]) >= 4))
            for dest in (inv_risk_hist, cash_risk_hist):
                for code in dest:
                    dest[code].sort(key=lambda x: x[0])

        def _latest_risk(hist: Dict[str, list], code: str, day: str) -> bool:
            points = hist.get(code)
            if not points:
                return False
            avail = [x for x in points if x[0] <= day]
            return bool(avail) and avail[-1][1]

        def _quality_risk(code: str, day: str) -> bool:
            return _latest_risk(inv_risk_hist, code, day) or _latest_risk(cash_risk_hist, code, day)

        per_stock = total_capital / max_positions
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        for day in sim_dates:
            if strict_exec:
                _still = []
                for code, reason in pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                pending_sells = _still
                for code in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}
                pending_buys = []

            # 매도 체크: 손절 또는 보유기간 만료
            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                stop_cond = ret <= stop_loss
                expire_cond = p['hold'] >= max_hold
                trend_break_cond = False
                if trend_break_ma is not None and ret > 0 and i >= trend_break_ma:
                    ma = sum(sd[code]['c'][i - trend_break_ma + 1:i + 1]) / trend_break_ma
                    trend_break_cond = curr < ma
                trail_cond = False
                if trail_pct is not None and ret > 0:
                    trail_cond = (curr - p['peak']) / p['peak'] < trail_pct
                chart_top_cond = False
                if chart_confluence and ret > 0.05 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN
                if stop_cond or expire_cond or trend_break_cond or trail_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'trend_break' if trend_break_cond else
                              'chart_top' if chart_top_cond else 'expire')
                    if strict_exec:
                        if code not in [c for c, _ in pending_sells]:
                            pending_sells.append((code, reason))
                    else:
                        pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                        cash += p['shares'] * p['entry'] + pnl
                        trades.append({
                            'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                            'entry': p['entry'], 'exit': curr,
                            'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                        })
                        pos.pop(code, None)

            # 매수 후보 스캔
            if len(pos) + len(pending_buys) < max_positions and _regime_gate_ok(day):
                candidates = []
                for code, s in sd.items():
                    if code in pos or code in pending_buys:
                        continue
                    i = didx[code].get(day)
                    if i is None or i < 126:
                        continue
                    c_arr = s['c']
                    curr = c_arr[i]
                    if curr <= 0 or curr < min_price:
                        continue
                    if _asof_active:
                        _sh = _shares_asof_mt(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    ret6m = curr / c_arr[i - 126] - 1 if c_arr[i - 126] > 0 else -1
                    if ret6m < ret6m_min:
                        continue
                    hi252 = max(c_arr[max(0, i - 251):i + 1])
                    if hi252 <= 0:
                        continue
                    dist = curr / hi252 - 1
                    if dist < dist_high_max:
                        continue
                    if require_earnings_accel and not _earnings_accel_ok(code, day):
                        continue
                    if smart_money_min_score > 0 and _smart_money_score(code, day) < smart_money_min_score:
                        continue
                    if exclude_quality_risk and _quality_risk(code, day):
                        continue
                    candidates.append((ret6m, code))
                if sector_confirm_min is not None and candidates:
                    sec_count: Dict[str, int] = {}
                    for _, code in candidates:
                        sec = sector_map.get(code)
                        if sec:
                            sec_count[sec] = sec_count.get(sec, 0) + 1
                    candidates = [(r, c) for r, c in candidates
                                  if sector_map.get(c) and sec_count.get(sector_map[c], 0) >= sector_confirm_min]
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_buys)
                if strict_exec:
                    pending_buys.extend(code for _, code in candidates[:slots])
                else:
                    for _, code in candidates[:slots]:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        budget = min(per_stock, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'mkt_cap_억': sd[code]['mkt_cap_억']}

        # 미청산 포지션 강제 청산
        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                'reason': 'final', 'pnl': round(pnl, 0),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100) if trades else 0.0
        avg_ret = sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0.0
        summary = (f"V-MEGATREND 구조테마추종(반도체+전력기기+조선+화장품ODM) | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{len(trades)}건 | 평균:{avg_ret:.1f}%")

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades),
            len([t for t in trades if t['pnl'] > 0]), summary,
            __import__('json').dumps(trades), run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=_asof_active)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_earnings_conviction(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 10,      # 2026-07-22(2차) 사용자 지시로 20→10 축소: "비중조절보다 확실한
                                   # 시그널에 더 집중". 종목 수를 줄이고 대신 각 종목의 배분폭을 키움.
    target_slots: int = 18,       # base_ticket = total_capital/target_slots(555만원). 2026-07-22(2차)
                                   # 1차 시도(target_slots=10, weight_cap=6)는 최고점수 종목 단 2개가
                                   # 1억 전액을 흡수해(60M+40M) 나머지 8슬롯이 전부 굶는 사고 실측
                                   # (당일 최고득점자가 이후 최고수익자와 무관 — 결국 순위 1~2위만
                                   # 반영되는 '이진 베팅'으로 변질, avg -0.37%). target_slots를 넓혀
                                   # base_ticket을 낮추고 weight_cap도 낮춰 5~6개 포지션이 동시에
                                   # 자금을 받을 수 있게 재조정 — "균등화는 아니지만 다수 고득점
                                   # 종목이 함께 집중배분 받는" 중간 지점.
    entry_score_min: float = 0.20,  # 진입(자격) 최소 이익 또는 매출 YoY 가속(+20%) — 성장 자체는
                                     # 퍼센트로 확인(순수 규모만으로는 "가속"인지 알 수 없음).
    weight_cap: float = 3.0,       # 랭킹/가중치는 아래 절대증가액 기준(weight_scale_억) 사용, 이 값은
                                   # 그 결과의 상한 배수.
    weight_scale_억: float = 5000,  # 2026-07-22(3차) 핵심 버그수정: 랭킹·가중치를 %성장이 아니라
                                    # **절대 이익/매출 증가액(억원)**으로 전환. %만 쓰면 20억→80억
                                    # (+300%)이 SK하이닉스 4.55조원 증가(+157%)보다 항상 높게 랭크되는
                                    # 근본 결함이 있었음(실측: 아무리 절대이익 하한을 500억→2000억으로
                                    # 올려도 소형주 %폭발이 계속 최상위권 독점 — 경동도시가스 매출이
                                    # 379,440%로 랭킹1위였던 사례). 절대증가액 랭킹으로 전환한 결과
                                    # SK하이닉스가 54개 후보 중 정확히 1위(4조5,505억원 증가)로 확인.
                                    # weight = 1+min(abs_increase_억/weight_scale_억, weight_cap-1) —
                                    # 5,000억원 증가당 1배씩 가중, 최대 weight_cap배에서 상한.
    min_op_profit_억: float = 500,  # 절대 영업이익 규모 하한(억원) — 초소형 기저효과(20억→80억,+300%)가
                                    # SK하이닉스급 진짜 대형가속을 밀어내는 착시 방지(실측 확인 완료).
    min_revenue_억: float = 500,   # 2026-07-22(2차) 신규: 매출 단독 급증 경로의 절대 매출 하한(억원).
                                    # 사용자 지시 "매출도 급격한 증가는 매수 대상" — 이익이 아직 적자/
                                    # 박한 성장초기 기업(고매출성장 SaaS·바이오임상 등)도 매출YoY 자체가
                                    # 강하면 진입 가능하도록 별도 경로 추가.
    revenue_score_min: float = 0.40,  # 매출단독 경로 진입 최소 매출YoY(+40%, 이익경로 20%보다 높게
                                       # 잡음 — 매출만으로는 신호가 약해 더 강한 확인 필요).
    min_mktcap_억: float = 300,
    stop_loss: float = -0.20,
    trail_pct: float = -0.30,
    max_hold: int = 252,
    deteriorate_exit: bool = True,  # 최신 분기 영업이익 YoY가 역성장으로 전환되면 조기청산
                                     # (비중확대의 대칭 원리 — 실적이 나빠지면 확신도 거둬들임).
    asof_mktcap: bool = True,      # 2026-07-27: min_mktcap_억 유니버스 필터가 stock_universe.market_cap
                                    # (현재시총) 정적 컷오프였음(룩어헤드) — as-of 시총으로 전환.
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-EARNINGS — 실적가속 집중배분 전략.

    [배경] 2026-07-22 사용자 지적 2건: ①"삼성전자/SK하이닉스가 역대급 이익을 내고 있는데 왜
    편입이 늦는가? 이익의 질이 좋아지는 기업은 비중을 늘려야 하지 않나?" ②"산술평균이 아니라
    수익 극대화가 목표. 점수가 높고 시그널이 확실한 종목에 더 집중해야 한다. 이익뿐 아니라
    매출 급증도 매수 대상." — V1(균등1/N 배분)에서 V2(소수 고확신 종목 집중배분+매출단독 경로)로
    재설계. V1은 6기간 KOSPI/KOSDAQ avg6와 거의 동률(+18.44% vs +18.90%)이었으나 이는 "평균"
    프레이밍일 뿐 — 실제 목표는 절대수익 극대화이므로 배치 내 정규화를 폐지하고 점수 자체의
    절대 크기를 가중치에 그대로 반영, 포지션 수도 20→10으로 줄여 집중도를 높임.

    매수: 가격조건 없이 ①분기 영업이익 YoY 가속(entry_score_min 이상, 매출YoY도 양수, 절대영업이익
         min_op_profit_억 이상) 또는 ②매출 YoY 단독 급증(revenue_score_min 이상, 절대매출
         min_revenue_억 이상 — 이익이 아직 안 나는 고성장 초기기업 포착) 중 더 강한 신호로 진입.
         가중치 = 1+min(score, weight_cap-1) — 정규화 없이 점수가 클수록 계속 커짐(상한 weight_cap).
    매도: 손절(-20%) / 추적손절(-30%, 이익권 한정) / 실적악화청산(최신분기 YoY 역성장 전환 시) /
         보유만료(252일).
    """
    init_backtest_db()
    run_name = run_name or f"V-EARNINGS {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "earnings_conviction", "earnings_conviction_v2_concentrated_20260722",
        {"entry_score_min": entry_score_min, "weight_cap": weight_cap,
         "stop_loss": stop_loss, "trail_pct": trail_pct, "max_hold": max_hold,
         "max_positions": max_positions, "target_slots": target_slots,
         "min_op_profit_억": min_op_profit_억, "min_revenue_억": min_revenue_억,
         "revenue_score_min": revenue_score_min,
         "min_mktcap_억": min_mktcap_억, "deteriorate_exit": deteriorate_exit,
         "asof_mktcap": asof_mktcap,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="conviction_weighted_concentrated",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'earnings_conviction',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / target_slots, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')
        _pref_pat = re.compile(r"\d?우[A-Z]?$")
        # 2026-07-27: as-of 모드에선 유니버스 자체는 시총 무관하게 넓게 잡고(전 KOSPI/KOSDAQ),
        # min_mktcap_억 컷오프는 아래 매수후보 스캔에서 진입일 as-of 시총으로 매일 재확인.
        _mktcap_gate = "" if asof_mktcap else "AND COALESCE(market_cap, 0) >= ?"
        _mktcap_param = [] if asof_mktcap else [min_mktcap_억]
        all_rows = conn.execute(f"""
            SELECT stock_code, stock_name, market_cap FROM stock_universe
            WHERE market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
              {_mktcap_gate}
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        """, _mktcap_param).fetchall()
        codes = [r[0] for r in all_rows if not (r[1] and _pref_pat.search(r[1]))]
        mktcap_map = {r[0]: (r[2] or 300) for r in all_rows}
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(codes))), codes
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_ec(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows],
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # 분기 실적 이벤트: (avail_date, op_yoy_growth) 리스트. report_type='CFS' 우선(개별종목
        # override는 여기서는 무시 — 매출·영업이익 대표성은 항상 CFS가 낫다는 2026-07-19 확립 원칙 재사용).
        earn_events: Dict[str, list] = {}
        if sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.revenue, f.operating_profit, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4 AND f.report_type='CFS'
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, f.year, f.quarter
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                earn_events.setdefault(r[0], []).append(
                    {"avail": r[5], "rev": r[1], "op": r[2]})

        def _earn_score(code: str, day: str):
            """가장 최근 as-of 분기의 진입자격+랭킹기준. ①영업이익 YoY가속(매출도 동반양수, 절대
            영업이익 하한) ②매출 YoY 단독급증(절대매출 하한, 이익 요건 없음 — 성장초기 고매출성장
            기업 포착) 중 조건 충족 시, **절대 증가액(억원)**을 랭킹/가중치 기준으로 반환(2026-07-22
            3차: %기준이었을 때 SK하이닉스급 대형 가속이 초소형 %폭발에 랭킹에서 밀리는 근본결함
            발견 — 절대금액 기준으로 전환). 두 경로 모두 충족 시 절대증가액이 더 큰 쪽 채택.
            반환: None(자격없음) 또는 (rank_abs_억, pct, path)."""
            evs = earn_events.get(code)
            if not evs or len(evs) < 5:
                return None
            avail = [e for e in evs if e["avail"] <= day]
            if len(avail) < 5:
                return None
            cur, prev_y = avail[-1], avail[-5]
            op_now, op_1y = cur["op"], prev_y["op"]
            rev_now, rev_1y = cur["rev"], prev_y["rev"]

            cands = []
            if (op_now is not None and op_1y is not None and op_1y > 0
                    and op_now >= min_op_profit_억 * 1e8
                    and rev_now is not None and rev_1y is not None and rev_1y > 0 and rev_now > rev_1y):
                pct = op_now / op_1y - 1
                if pct >= entry_score_min:
                    cands.append(((op_now - op_1y) / 1e8, pct, "profit"))

            if (rev_now is not None and rev_1y is not None and rev_1y > 0
                    and rev_now >= min_revenue_억 * 1e8):
                pct = rev_now / rev_1y - 1
                if pct >= revenue_score_min:
                    cands.append(((rev_now - rev_1y) / 1e8, pct, "revenue"))

            if not cands:
                return None
            return max(cands, key=lambda x: x[0])

        def _weight_mult(rank_abs_억: float) -> float:
            """연속 가중치 — 절대 증가액(억원) 기준. weight_scale_억마다 1배씩 커지다 weight_cap에서
            상한(2026-07-22 3차: %기준 가중치도 함께 절대금액 기준으로 통일)."""
            return 1.0 + min(max(rank_abs_억, 0.0) / weight_scale_억, weight_cap - 1.0)

        base_ticket = total_capital / target_slots
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        for day in sim_dates:
            if strict_exec:
                _still = []
                for code, reason in pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                        'weight_mult': p.get('weight_mult', 1.0),
                    })
                pending_sells = _still
                for code, wmult in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(base_ticket * wmult, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억'], 'weight_mult': wmult}
                pending_buys = []

            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                stop_cond = ret <= stop_loss
                expire_cond = p['hold'] >= max_hold
                trail_cond = trail_pct is not None and ret > 0 and (curr - p['peak']) / p['peak'] < trail_pct
                deter_cond = False
                if deteriorate_exit and ret > 0:
                    sc = _earn_score(code, day)
                    if sc is not None and sc[1] < 0:
                        deter_cond = True
                if stop_cond or expire_cond or trail_cond or deter_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'earnings_deteriorate' if deter_cond else 'expire')
                    if strict_exec:
                        if code not in [c for c, _ in pending_sells]:
                            pending_sells.append((code, reason))
                    else:
                        pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                        cash += p['shares'] * p['entry'] + pnl
                        trades.append({
                            'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                            'entry': p['entry'], 'exit': curr,
                            'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                            'weight_mult': p.get('weight_mult', 1.0),
                        })
                        pos.pop(code, None)

            if len(pos) + len(pending_buys) < max_positions:
                candidates = []
                pending_codes = {c for c, _ in pending_buys} if strict_exec else set()
                for code in sd:
                    if code in pos or code in pending_codes:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    curr = sd[code]['c'][i]
                    if curr <= 0:
                        continue
                    if asof_mktcap:
                        _sh = _shares_asof_ec(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    r = _earn_score(code, day)
                    if r is None:
                        continue
                    candidates.append((r[0], code))  # r[0] = 절대증가액(억원) — 랭킹/가중치 기준
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_codes)
                picked = candidates[:slots]
                # 2026-07-22(3차) 사용자 지시 반영: "비중조절보다 확실한 시그널에 더 집중" —
                # 배치 내 평균=1.0 정규화(1차 버전)를 폐지하고, **절대 증가액(억원)** 기준 연속
                # 가중치를 그대로 사용(_weight_mult, 1~weight_cap배) — %기준이면 초소형 %폭발이
                # SK하이닉스급 대형가속을 항상 이겨버리는 근본결함이 있어 절대금액으로 전환.
                if strict_exec:
                    for score, code in picked:
                        pending_buys.append((code, _weight_mult(score)))
                else:
                    for score, code in picked:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        wmult = _weight_mult(score)
                        budget = min(base_ticket * wmult, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억'], 'weight_mult': wmult}

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                'reason': 'final', 'pnl': round(pnl, 0), 'weight_mult': p.get('weight_mult', 1.0),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100) if trades else 0.0
        avg_ret = sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0.0
        summary = (f"V-EARNINGS 실적가속확신비중 | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{len(trades)}건 | 평균:{avg_ret:.1f}%")

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades),
            len([t for t in trades if t['pnl'] > 0]), summary,
            __import__('json').dumps(trades), run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=asof_mktcap)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_moonshot_turnaround(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 30,       # 2026-07-23 20→30 상향: 사용자 지시("1000% 상승 종목을 찾는게
                                    # 중요, 꼭 다 안먹어도 어깨에 나와도 상관없다")에 따라 "총수익 극대화"
                                    # 보다 "발굴 개수 극대화" 우선 — 실측 20→30 비교: 6기간 avg6
                                    # 18.94%→22.39%(3/6→4/6양수, 개선), 연속운용(2020-03~2026-03)
                                    # 100%+ 대박종목 21건→28건으로 증가(예스24+336.7%·인화정공+254.6%·
                                    # 코스맥스+214.8% 등 추가 포착), 단 연속운용 총수익은 190.31%→
                                    # 160.97%로 하락(자본이 더 얇게 분산돼 개별 대박의 기여도 희석) —
                                    # "총수익"과 "발굴개수"의 트레이드오프이며 사용자 지시대로 후자 우선.
                                    # 분산 바스켓(집중 아님) — "어느 종목이 1000%될지" 사전에 알 수
                                    # 없으므로 넓게 분산해 소수 대박이 다수 소손실을 상쇄하는
                                    # fat-tail 구조를 노림(megatrend/turnaround와 동일 철학).
    entry_score_min: int = 2,      # /api/tenbagger/turnaround-watch comprehensive_score 채택기준
                                    # (walk-forward 검증: 0점 0.52~0.59x/1점 0.79~0.91x/2점 1.13~1.23x/
                                    # 3점 1.38x·검증1.63x). 기본 2점(재도전/매출성장/이익의질 중 2개+).
    dilution_max: int = 3,         # 희석위험(CB/BW/EB/RIGHTS 트레일링365일 공시건수) 배제 상한.
                                    # 검증: 4건+부터 lift 0.90x/0.64x·1년내-30%↓비율 42.6%/49.3%로
                                    # 급격 악화(젬백스 사례) — 3건 이하만 허용.
    min_mktcap_억: float = 300,
    stop_loss: float = -0.35,      # 심한 낙폭과대/적자 모집단 특성상 변동성이 커 표준(-20%)보다
                                    # 넓게 설정 — 조기손절로 진짜 턴어라운드를 놓치는 것을 방지.
    trail_pct: float = -0.35,      # 추적손절도 넓게 — 1000%대 승자가 중간조정에 흔들리지 않고
                                    # 최대한 오래 보유되도록(megatrend와 동일 설계).
    max_hold: int = 500,           # 최대 보유 500거래일(~2년) — 턴어라운드가 무르익어 대박으로
                                    # 발전하기까지 megatrend(252일)보다 긴 호흡이 필요하다는 전제.
    asof_mktcap: bool = True,      # 2026-07-27: min_mktcap_억 유니버스 필터가 stock_universe.market_cap
                                    # (현재시총) 정적 컷오프였음(룩어헤드 — 부실기업이 턴어라운드로 몸집이
                                    # 커진 뒤에야 "300억+"로 소급 편입되는 등) — as-of 시총으로 전환.
    panic_stop_loss: float = None,  # 2026-08-09: KOSPI 패닉장(MA120×0.85 미만) 중 -20% 터치 이벤트
                                     # 176건 사후분석 — 패닉장 중 낙폭은 반등확률 30.0%(평균최종-9.8%)
                                     # vs 정상/강세장 중 낙폭은 7.7~20.0%(평균최종-19.8~-28.6%). 시장전체
                                     # 패닉이 원인인 낙폭은 시스템리스크(재평가) 회복 여지가 크고, 강세장
                                     # 중 개별종목만 하락하면 펀더멘털 악재일 가능성이 커 계속 하락하는
                                     # 것으로 추정. None이면 stop_loss 단일값(기존 동작) 유지 — 국면별
                                     # 손절폭을 분리하려면 panic_stop_loss(완화, 예: -0.45)를 지정.
    panic_ma_ratio: float = 0.85,   # 패닉장 판정 임계(KOSPI/MA120)
    include_profitable: bool = False,  # 2026-08-10: 기본 False(기존 동작=TTM적자 모집단만).
                                     # True면 TTM 흑자/분기흑자 종목도 포함 — comprehensive_score
                                     # (재도전+매출YoY성장+이익의질)조건은 그대로 유지하되 population을
                                     # 흑자기업까지 확장. 사용자 지시(2026-08-10): "매수/매도가 워낙 많은
                                     # 범용전략보다 텐버거 전용 로직(익절없음+넓은손절+긴만기)을 흑자
                                     # 모집단에도 넓혀보자" — 실증(capture_rate_analysis) 결과 이 매도
                                     # 설계(손절-35%/trail-35%/만기500일/익절없음)가 v4/v2류(익절있음)
                                     # 대비 텐버거 거래 평균실현수익 3.6~17.5%→75.0%로 압도적 우위였음.
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-MOONSHOT — 턴어라운드 종합스코어 기반 대박발굴 전략.

    [배경] 2026-07-22 사용자 지시 "기존 전략을 그대로 두더라도 핵심종목 1000%씩 오르는 종목의
    발굴에 집중하는것도 괜찮다고 생각합니다" — V-EARNINGS(메가캡 이익가속 집중배분)와는 반대 극단:
    현재는 작고 실적이 안 좋은(TTM 적자) 종목 중에서, 이미 walk-forward로 검증된 3개 독립 리딩
    시그널(재도전 턴어라운드/매출YoY성장/감가상각주도 이익의질, 2026-07-19 turnaround-watch 구축)의
    조합점수(0~3)가 높은 종목을 광범위하게 분산 매수 — 어느 한 종목이 1000%가 될지는 사전에 알 수
    없으므로 집중이 아니라 분산+장기보유로 fat-tail을 노림. 희석위험(CB/BW/EB) 높은 종목은 검증된
    리스크 신호로 배제.

    매수: TTM 순이익≤0(적자 모집단) + comprehensive_score(재도전+매출YoY+이익의질) ≥ entry_score_min
         + 희석위험(트레일링365일 CB/BW/EB건수) ≤ dilution_max — 최대 20종목 균등분산.
    매도: 손절 -35%(하드, 변동성 큰 모집단 감안 확대) / 추적손절 -35%(이익권) / 만료 500거래일(~2년).
    """
    init_backtest_db()
    run_name = run_name or f"V-MOONSHOT {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "moonshot_turnaround", "moonshot_turnaround_v1_20260723",
        {"entry_score_min": entry_score_min, "dilution_max": dilution_max,
         "min_mktcap_억": min_mktcap_억, "stop_loss": stop_loss, "trail_pct": trail_pct,
         "max_hold": max_hold, "max_positions": max_positions, "asof_mktcap": asof_mktcap,
         "panic_stop_loss": panic_stop_loss, "panic_ma_ratio": panic_ma_ratio,
         "include_profitable": include_profitable,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="diversified_basket",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'moonshot_turnaround',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / max_positions, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')
        _pref_pat = re.compile(r"\d?우[A-Z]?$")

        # KOSPI 국면(패닉장 판정용, panic_stop_loss 사용 시에만 의미 — 2026-08-09
        # 사후분석: 원안 완료거래 253건 중 -20%터치 176건 재구성 결과, KOSPI<MA120×0.85
        # 패닉장 중 -20%낙폭 반등확률 30.0%(평균최종-9.8%) vs 정상/강세장 중 낙폭 7.7~20.0%
        # (평균최종-19.8~-28.6%) — 시장전체 패닉발 낙폭은 시스템리스크 회복 여지가 크고,
        # 강세장 중 개별종목만 하락하면 펀더멘털 악재일 가능성이 커 계속 하락하는 것으로 추정)
        _k_rows_ms = conn.execute("SELECT date, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date").fetchall()
        _k_dates_ms = [r[0] for r in _k_rows_ms]
        _k_prices_ms = [float(r[1]) for r in _k_rows_ms]
        _k_idx_ms = {d: i for i, d in enumerate(_k_dates_ms)}

        def _is_panic_ms(date: str) -> bool:
            if panic_stop_loss is None:
                return False
            idx = _k_idx_ms.get(date)
            if idx is None:
                for d in reversed(_k_dates_ms):
                    if d <= date:
                        idx = _k_idx_ms[d]
                        break
            if idx is None or idx < 120:
                return False
            ma = sum(_k_prices_ms[idx - 119:idx + 1]) / 120
            if ma <= 0:
                return False
            return (_k_prices_ms[idx] / ma) < panic_ma_ratio

        # 2026-07-27: as-of 모드에선 유니버스 자체는 시총 무관하게 넓게 잡고(전 KOSPI/KOSDAQ),
        # min_mktcap_억 컷오프는 아래 매수후보 스캔에서 진입일 as-of 시총으로 매일 재확인.
        _mktcap_gate = "" if asof_mktcap else "AND COALESCE(market_cap, 0) >= ?"
        _mktcap_param = [] if asof_mktcap else [min_mktcap_억]
        all_rows = conn.execute(f"""
            SELECT stock_code, stock_name, market_cap FROM stock_universe
            WHERE market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
              {_mktcap_gate}
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        """, _mktcap_param).fetchall()
        codes = [r[0] for r in all_rows if not (r[1] and _pref_pat.search(r[1]))]
        mktcap_map = {r[0]: (r[2] or 300) for r in all_rows}
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(codes))), codes
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_ms(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows],
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        if not sd:
            raise RuntimeError("유니버스가 비어있음(가격이력 부족)")

        overrides = {r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config "
            "WHERE config_key='preferred_report_type'")}
        raw_rows = conn.execute("""
            SELECT stock_code, year, quarter, report_type, net_income, revenue
            FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL
              AND stock_code IN ({})
            ORDER BY stock_code, year, quarter
        """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall()
        by_quarter: Dict[tuple, dict] = {}
        for r in raw_rows:
            key = (r[0], r[1], r[2])
            by_quarter.setdefault(key, {})[r[3]] = r
        panel: Dict[str, list] = {}
        for (code, y, q), variants in by_quarter.items():
            pref = overrides.get(code, "CFS")
            r_ni = variants.get(pref) or next(iter(variants.values()))
            r_rev = variants.get("CFS") or r_ni
            panel.setdefault(code, []).append((y, q, r_ni[4], r_rev[5]))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        cf_map: Dict[tuple, dict] = {}
        for r in conn.execute("""
            SELECT stock_code, year, quarter, report_type, depreciation_q, operating_cf_q
            FROM cash_flow_data WHERE is_annual=0 AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())):
            key = (r[0], r[1], r[2])
            cf_map.setdefault(key, {})[r[3]] = r

        dilution_map: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT stock_code, disclosed_at FROM dilution_events
            WHERE event_type IN ('CB','BW','EB','RIGHTS') AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())):
            if r[1]:
                dilution_map.setdefault(r[0], []).append(str(r[1])[:10])
        for c in dilution_map:
            dilution_map[c].sort()

        def _avail_date(y: int, q: int) -> str:
            if q == 1: return f"{y}-05-15"
            if q == 2: return f"{y}-08-15"
            if q == 3: return f"{y}-11-15"
            return f"{y+1}-02-15"

        def _dilution_risk(code: str, avail: str) -> int:
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(avail, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return sum(1 for d in evs if cutoff <= d <= avail)

        # 종목별 (avail_date, score) 이벤트 리스트를 사전 계산 — turnaround-watch의
        # comprehensive_score(B/D/E 3신호 합산) 로직을 그대로 이식, as-of 판단용으로 재구성.
        score_events: Dict[str, list] = {}
        for code, qs in panel.items():
            n = len(qs)
            if n < 8:
                continue
            for i in range(4, n):
                y, q, ni, rev = qs[i]
                avail = _avail_date(y, q)
                ttm_now = sum(x[2] or 0 for x in qs[max(0, i-3):i+1])
                if not include_profitable:
                    if ttm_now > 0:
                        continue  # TTM 흑자면 모집단 밖(V-EARNINGS 등 다른 전략의 영역)
                    if ni is not None and ni > 0:
                        continue  # 이번 분기 자체는 흑자(단일분기 흑자전환) — comprehensive_score 모집단은
                                  # "이번 분기도 적자"인 경우만(turnaround-watch B/D/E와 동일 조건)
                rev_yoy = None
                if i - 4 >= 0 and qs[i-4][3] and qs[i-4][3] >= 1e9 and rev:
                    raw = (rev / qs[i-4][3] - 1) * 100
                    rev_yoy = raw if abs(raw) <= 500 else None
                last_flip = any((qs[j][2] or 0) > 0 for j in range(max(0, i-4), i))
                variants = cf_map.get((code, y, q))
                dep_driven = cash_positive = False
                if variants:
                    r = variants.get("CFS") or next(iter(variants.values()))
                    dep_q, ocf_q = r[4], r[5]
                    dep_driven = bool(dep_q is not None and ni is not None and (ni + dep_q) > 0)
                    cash_positive = bool(ocf_q is not None and ocf_q > 0)
                score = int(last_flip) + int(bool(rev_yoy is not None and rev_yoy > 0)) + int(dep_driven or cash_positive)
                if score >= 1:
                    # 2026-07-24 조사(에이엘티 사례): 동점(같은 comprehensive_score) 시
                    # candidates.sort(reverse=True)가 (score,code) 튜플을 그대로 내림차순 정렬해
                    # 종목코드 문자열이 큰 코드를 기계적으로 우선시하는 편향을 발견 — rev_yoy를
                    # 2차 정렬기준으로 바꿔봤으나 실측 결과 연속운용 수익률이 오히려 대폭 악화
                    # (2020-03~2026-07-24 기준 137.46%→81.83%, -55.6%p, run_id 비교로 확인).
                    # 추정 원인: 소형주 기저효과로 인한 노이즈성 매출YoY 극단치가 "성숙한" 우선순위
                    # 신호가 되어버려, 기존의 (사실상 무작위에 가까운) 코드순 동점처리보다 못한
                    # 선택을 반복 유발. 검증된 대안이 없어 원래 동작으로 되돌림 — 동점 처리는
                    # 여전히 종목코드 기준이며 투자근거상 의미는 없으나, 이 신호(comprehensive_score)
                    # 자체는 turnaround-watch 발굴용으로만 쓰고 실전 편입 여부는 자본경쟁(30슬롯)
                    # 결과라는 점을 감안할 것 — 에이엘티가 미편입된 것은 편향 때문이 아니라 정상적인
                    # 슬롯 경쟁 결과로 판단됨(CLAUDE.md 참조).
                    score_events.setdefault(code, []).append((avail, score))
        for code in score_events:
            score_events[code].sort()

        def _current_score(code: str, day: str):
            evs = score_events.get(code)
            if not evs:
                return None
            avail = [e for e in evs if e[0] <= day]
            if not avail:
                return None
            return avail[-1]  # (avail_date, score) — 가장 최근 것

        per_stock = total_capital / max_positions
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        for day in sim_dates:
            if strict_exec:
                _still = []
                for code, reason in pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                pending_sells = _still
                for code in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}
                pending_buys = []

            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                _eff_stop = panic_stop_loss if (panic_stop_loss is not None and _is_panic_ms(day)) else stop_loss
                stop_cond = ret <= _eff_stop
                expire_cond = p['hold'] >= max_hold
                trail_cond = trail_pct is not None and ret > 0 and (curr - p['peak']) / p['peak'] < trail_pct
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
                    if strict_exec:
                        if code not in [c for c, _ in pending_sells]:
                            pending_sells.append((code, reason))
                    else:
                        pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                        cash += p['shares'] * p['entry'] + pnl
                        trades.append({
                            'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                            'entry': p['entry'], 'exit': curr,
                            'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                        })
                        pos.pop(code, None)

            if len(pos) + len(pending_buys) < max_positions:
                candidates = []
                pending_codes = set(pending_buys) if strict_exec else set()
                for code in sd:
                    if code in pos or code in pending_codes:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    curr = sd[code]['c'][i]
                    if curr <= 0:
                        continue
                    if asof_mktcap:
                        _sh = _shares_asof_ms(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    r = _current_score(code, day)
                    if r is None or r[1] < entry_score_min:
                        continue
                    if _dilution_risk(code, day) > dilution_max:
                        continue
                    candidates.append((r[1], code))
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_codes)
                picked = candidates[:slots]
                if strict_exec:
                    for _, code in picked:
                        pending_buys.append(code)
                else:
                    for _, code in picked:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        budget = min(per_stock, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                'reason': 'final', 'pnl': round(pnl, 0),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100) if trades else 0.0
        avg_ret = sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0.0
        summary = (f"V-MOONSHOT 턴어라운드종합스코어 대박발굴 | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{len(trades)}건 | 평균:{avg_ret:.1f}%")

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades),
            len([t for t in trades if t['pnl'] > 0]), summary,
            __import__('json').dumps(trades), run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=asof_mktcap)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_contract_momentum(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 10,
    per_stock: float = 10_000_000,
    min_ratio: float = 10.0,        # 계약금액/매출 비율(%) 하한
    overseas_only: bool = True,     # 해외수주만(국내계약은 2026-07-24 홀드아웃에서 신호품질 열위 확인)
    min_ai: float = 0.0,
    pos52_max: float = 1.0,         # 52주 내 상대위치 상한(과열 회피, 1.0=제한없음)
    min_ma20: float = 0.0,          # 종가/MA20-1 하한(추세 확인)
    min_quarterly_impact: float = None,  # 2026-08-10: 계약비율(%)÷계약기간(분기수) 하한.
                                     # None=비활성(기존 동작 동일). 사용자 지시("10버거 종목을 확대하되
                                     # 아닌 종목을 걸러낼수 있는 지표")로 실증: 실제거래(102건 중 68건
                                     # 매칭) success(trail/expire)그룹 평균13.74 vs stop그룹 평균5.79로
                                     # 2.4배 차이 — 단순 ratio는 오히려 역방향(success15.8%<stop26.0%,
                                     # 대형 단발계약이 후속 모멘텀 없이 소진되는 경향 추정).
                                     # ⚠️ 학습기(2020-03~2023-12) 최적값(qi=8)을 검증기(2024-01+)에
                                     # 얼려적용 시 대폭악화(방향뒤집힘, 과최적화 확정) — 기본값 None 유지.
    max_mom60: float = None,        # 2026-08-10: 진입시점 60일 모멘텀(%) 상한. None=비활성.
    stop: float = -0.08,
    trail: float = -0.25,
    trail_activate_pct: float = 0.10,
    max_hold: int = 400,  # 2026-08-10: 240→400 변경. 텐버거(10배+) 583종목 저점~고점 실제 페이스
                           # (3년이내 달성군 중위 483일=1.3년, p90 886일) 대비 원안(240일)이 짧다는
                           # 문제의식으로 스윕(240/400/500/700/999) — 연속운용(2020-03~2026-03)
                           # 204.24%→222.10%(+17.9%p), 350~450 전구간 견고(knife-edge 아님).
                           # 홀드아웃(학습<2024-01-01/검증>=2024-01-01) 재검증: baseline 학습45.78%/
                           # 검증162.8% vs 400 학습39.09%/검증187.92%(+25.1%p) — 검증기(미래데이터)
                           # 에서 개선, 방향 일치. signal_experiment_ledger: contract_momentum/
                           # max_hold_240_to_400_holdout_20260810.
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-CONTRACT-MOMENTUM — 해외 대형수주 공시 모멘텀 전략.

    [배경] 2026-08-09 사용자 지시로 3년내 10배 종목 251개를 상승계기별 카테고리화한
    결과, "대형수주"(36개, 14.3%) 카테고리의 최초 공시일 중 42%(15/36)가 저점 이전에
    발생 — 즉 선행지표로 쓸 수 있는 비중이 상당함을 확인. 이 신호는 이미 2026-07-23~24
    Codex가 독립 스크립트(scratch/codex_research_contract_momentum_20260723.py)로
    발굴·검증했으나(학습기 최적파라미터를 검증기에 얼려서 적용, +154.3%/145건/
    승률24.8%/PF2.51/MDD-27.9% — 붕괴 없음 확인) 정식 backtest.py 함수로 이식되지
    않아 실전(가상매매/콤보)에 연결된 적이 없었음. 이번에 원본 로직을 최대한 그대로
    유지하되(파라미터·필터·매도규칙 동일), 다른 전략과 같은 프레임워크(as-of 없음—
    원본에 시총필터 자체가 없었음, D+1 시가체결, run_spec 기록)로 이식.

    매수: dart_contracts 중 "단일판매/공급계약"류 공시(해지·거래정지·유동성공급·
         [첨부추가] 제외) + contract_ratio_pct>=min_ratio + (해외한정 옵션) +
         52주 내 상대위치<=pos52_max + 종가>=MA20×(1+min_ma20) + 20일평균거래대금>=20억.
         공시 다음 거래일 시가 매수, 동일일 복수신호는 ratio·ai_score 내림차순 우선.
    매도: 손절-8% / 추적손절-25%(이익10%+ 발동) / 만기400거래일(2026-08-10 240→400, 홀드아웃 검증 채택).
    """
    init_backtest_db()
    run_name = run_name or f"V-CONTRACT-MOMENTUM {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "contract_momentum", "contract_momentum_v1_20260809",
        {"min_ratio": min_ratio, "overseas_only": overseas_only, "min_ai": min_ai,
         "pos52_max": pos52_max, "min_ma20": min_ma20, "min_quarterly_impact": min_quarterly_impact,
         "max_mom60": max_mom60,
         "stop": stop, "trail": trail,
         "max_hold": max_hold, "max_positions": max_positions, "per_stock": per_stock,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'contract_momentum',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        def _is_clean_contract_report(report_name: str) -> bool:
            name = (report_name or "").replace(" ", "")
            if not name:
                return False
            if "단일판매" not in name and "공급계약" not in name:
                return False
            blocked = ("계약해지", "주권매매거래정지", "유동성공급", "[첨부추가]")
            return not any(token in name for token in blocked)

        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=500)).strftime('%Y-%m-%d')
        raw = conn.execute("""
            SELECT rcept_no, stock_code, disclosed_at, COALESCE(report_nm,''),
                   COALESCE(contract_ratio_pct,0), COALESCE(is_overseas,0), COALESCE(ai_score,0),
                   COALESCE(contract_amount_krw,0), contract_start, contract_end
            FROM dart_contracts
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' AND contract_ratio_pct IS NOT NULL
        """).fetchall()

        def _duration_months(s, e):
            # 2026-08-10: 계약기간 정규화 지표(2026-07-25 이론적 제안 → 오늘 실증) —
            # 단순 contract_ratio_pct는 성공/손절 분포에서 오히려 역방향(성공15.8%<손절26.0%,
            # 대형 단발성 계약이 후속 모멘텀 없이 소진되는 경향으로 추정), 계약기간으로 나눈
            # quarterly_impact(=ratio/duration_quarters)는 성공13.74 vs 손절5.79로 2.4배 판별력.
            if not s or not e:
                return None
            try:
                sy, sm = int(str(s)[:4]), int(str(s)[5:7])
                ey, em = int(str(e)[:4]), int(str(e)[5:7])
                return max(0.25, (ey - sy) * 12 + (em - sm))
            except Exception:
                return None

        seen = set()
        events_raw = []
        for rcept_no, code, dt, report_name, ratio, overseas, ai_score, amount_krw, c_start, c_end in raw:
            digits = "".join(ch for ch in str(dt or "") if ch.isdigit())
            if len(digits) < 8:
                continue
            iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            if not (warmup_start <= iso <= end_date):
                continue
            if not _is_clean_contract_report(report_name):
                continue
            # dedup key는 원본 스크립트(codex_research_contract_momentum_20260723.py)와
            # 동일하게 금액도 포함 — 같은 종목·날짜·비율이라도 계약금액이 다르면 별개 이벤트.
            key = (code, iso, round(float(amount_krw or 0) / 1_000_000), round(float(ratio or 0), 2))
            if key in seen:
                continue
            seen.add(key)
            dur_m = _duration_months(c_start, c_end)
            q_impact = (float(ratio or 0) / (dur_m / 3)) if (dur_m and ratio) else None
            events_raw.append((code, iso, float(ratio or 0), int(overseas or 0), float(ai_score or 0), q_impact))

        codes = sorted({e[0] for e in events_raw})
        sd: Dict[str, dict] = {}
        for code in codes:
            # 2026-08-09: trade_amount(KRX ACC_TRDVAL)가 2026-07~08 전종목 0으로 채워지던
            # 인프라버그를 발견·수정(scheduler.py _job_krx_daily) + 결측분 백필했으나,
            # signal_engine.py/screener.py처럼 close×volume 폴백도 방어적으로 추가해
            # 향후 유사 회귀에도 이 전략만 취약해지지 않도록 함.
            rows = conn.execute("""
                SELECT date, close, COALESCE(open,close) AS o, COALESCE(high,close) AS h,
                       COALESCE(low,close) AS lo, COALESCE(trade_amount,0) AS amt, COALESCE(volume,0) AS vol
                FROM price_history WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 260:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            amt_list = [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) * float(r[6] or 0) for r in rows]
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows], 'c': c_list,
                'o': [float(r[2]) for r in rows], 'h': [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows], 'amt': amt_list,
            }
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # 이벤트별 진입일(entry_date=신호일 다음 거래일) 및 필터 지표(52주위치/MA20/20일평균거래대금) 계산
        buy_pool: Dict[str, list] = {}
        for code, sig_date, ratio, overseas, ai_score, q_impact in events_raw:
            s = sd.get(code)
            if not s or code not in didx:
                continue
            pos = None
            for i, d in enumerate(s['d']):
                if d > sig_date:
                    pos = i; break
            if pos is None or pos < 260:
                continue
            i0 = pos - 1  # 신호 확인 시점(공시일 당일 종가 기준)
            ma20 = sum(s['c'][i0-19:i0+1]) / 20
            avg20_amt = sum(s['amt'][i0-19:i0+1]) / 20
            if ma20 <= 0 or avg20_amt < 2_000_000_000:
                continue
            hi252 = max(s['h'][i0-251:i0+1]); lo252 = min(s['lo'][i0-251:i0+1])
            if hi252 <= lo252:
                continue
            pos52 = (s['c'][i0] - lo252) / (hi252 - lo252)
            close_ma20 = s['c'][i0] / ma20 - 1
            # 2026-08-10: 진입시점 60일 모멘텀 — 교차전략(golden_cross/earnings_conviction/
            # contract_momentum/moonshot_turnaround) 384건 실측: success그룹 평균17.56% <
            # stop그룹 평균21.69% (반직관적, 과열회피 원칙과 일치 — 너무 급하게 오른 뒤
            # 진입하면 오히려 실패 확률 높음). max_mom60=None이면 비활성(기존동작 동일).
            mom60 = (s['c'][i0] / s['c'][i0 - 60] - 1) * 100 if i0 >= 60 and s['c'][i0 - 60] > 0 else None
            if ratio < min_ratio: continue
            if overseas_only and not overseas: continue
            if ai_score < min_ai: continue
            if pos52 > pos52_max: continue
            if close_ma20 < min_ma20: continue
            if min_quarterly_impact is not None and (q_impact is None or q_impact < min_quarterly_impact):
                continue
            if max_mom60 is not None and (mom60 is None or mom60 > max_mom60):
                continue
            entry_date = s['d'][pos]
            if entry_date < start_date or entry_date > end_date:
                continue
            buy_pool.setdefault(entry_date, []).append((ratio, ai_score, code))
        for d in buy_pool:
            buy_pool[d].sort(reverse=True)

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))

        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []
        equity_curve: list = []  # 2026-08-13: MDD/Sharpe 계산용 일별 자산평가(현금+보유포지션 시가평가)

        for day in sim_dates:
            _still = []
            for code, reason in pending_sells:
                if code not in pos:
                    continue
                i = didx[code].get(day)
                if i is None:
                    _still.append((code, reason)); continue
                px = sd[code]['o'][i]
                if px <= 0:
                    _still.append((code, reason)); continue
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], px, p['shares'], 300)
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                                'entry': p['entry'], 'exit': px, 'pnl_pct': net_pct,
                                'reason': reason, 'pnl': round(pnl, 0)})
            pending_sells = _still
            for code in pending_buys:
                if code in pos or len(pos) >= max_positions:
                    continue
                i = didx[code].get(day)
                if i is None:
                    continue
                px = sd[code]['o'][i]
                if px <= 0 or cash < px * 10:
                    continue
                budget = min(per_stock, cash * 0.99)
                shares = int(budget // px)
                if shares <= 0:
                    continue
                cash -= shares * px
                pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0, 'peak': px}
            pending_buys = []

            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                stop_cond = ret <= stop
                expire_cond = p['hold'] >= max_hold
                trail_cond = ret > trail_activate_pct and (curr - p['peak']) / p['peak'] <= trail
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
                    if code not in [c for c, _ in pending_sells]:
                        pending_sells.append((code, reason))

            pending_codes = set(pending_buys)
            slots = max_positions - len(pos) - len(pending_codes)
            if slots > 0:
                for ratio, ai_score, code in buy_pool.get(day, []):
                    if slots <= 0:
                        break
                    if code in pos or code in pending_codes:
                        continue
                    pending_buys.append(code)
                    pending_codes.add(code)
                    slots -= 1

            _mkval = cash
            for _c, _p in pos.items():
                _i = didx[_c].get(day)
                if _i is not None and sd[_c]['c'][_i] > 0:
                    _mkval += _p['shares'] * sd[_c]['c'][_i]
                else:
                    _mkval += _p['shares'] * _p['entry']
            equity_curve.append({'date': day, 'equity': _mkval})

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], 300)
            cash += p['shares'] * p['entry'] + pnl
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                            'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                            'reason': 'final', 'pnl': round(pnl, 0)})

        total_return = (cash - total_capital) / total_capital * 100
        completed = [t for t in trades if 'pnl_pct' in t]
        win_rate = (sum(1 for t in completed if t['pnl_pct'] > 0) / len(completed) * 100) if completed else 0

        # 2026-08-13(사용자 지시): MDD/샤프/손익비 계산 파이프라인 신규 추가.
        # _calc_metrics()(L1163)와 동일한 산식(에쿼티커브 peak대비 낙폭, 일별수익률
        # 표준편차 기반 샤프, 승/패 평균금액비 손익비)을 이 함수 구조에 맞춰 인라인 적용.
        peak = total_capital
        max_dd = 0.0
        for e in equity_curve:
            eq = e['equity']
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (eq - peak) / peak * 100
                max_dd = min(max_dd, dd)
        sharpe = 0.0
        if len(equity_curve) > 5:
            eq_vals = [e['equity'] for e in equity_curve]
            daily_r = [(eq_vals[i] - eq_vals[i - 1]) / eq_vals[i - 1]
                       for i in range(1, len(eq_vals)) if eq_vals[i - 1] > 0]
            if len(daily_r) > 5:
                rf = 0.03 / 252
                mean_r = sum(daily_r) / len(daily_r)
                std_r = (sum((r - mean_r) ** 2 for r in daily_r) / len(daily_r)) ** 0.5
                sharpe = round((mean_r - rf) / std_r * (252 ** 0.5), 2) if std_r > 0 else 0.0
        win_t = [t for t in completed if t['pnl'] > 0]
        loss_t = [t for t in completed if t['pnl'] <= 0]
        avg_win = sum(t['pnl'] for t in win_t) / len(win_t) if win_t else 0
        avg_loss = abs(sum(t['pnl'] for t in loss_t)) / len(loss_t) if loss_t else 1
        pl_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, total_trades=?, win_rate=?,
                max_drawdown_pct=?, trades_json=?
            WHERE run_id=?
        """, (round(total_return, 2), len(completed), round(win_rate, 1),
              round(max_dd, 2),
              json.dumps({"trades": trades, "sharpe": sharpe, "pl_ratio": pl_ratio,
                          "max_drawdown_pct": round(max_dd, 2)}), run_id))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=False)
        return run_id
    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_patent_catalyst(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 10,
    per_stock: float = 10_000_000,
    dilution_max: int = 3,          # 희석위험(CB/BW/EB/RIGHTS 트레일링365일) 배제 상한 — V-MOONSHOT과 동일 검증된 임계값 재사용
    min_mktcap_억: float = 100,
    stop: float = -0.25,
    trail: float = -0.30,
    trail_activate_pct: float = 0.10,
    max_hold: int = 365,
    asof_mktcap: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-PATENT-CATALYST — 적자기업 특허/기술이전/R&D계약/라이선스 공시 촉매 전략.

    [배경] 2026-08-09 사용자 지시로 251개 10배 종목 중 특허기술이전 카테고리(27개,
    10.8%)를 조사한 결과 이미 81.5% 커버돼 있어 우선순위를 낮췄으나, "완전히 검증되고
    수익률 향상 값을 찾을 때까지 계속하라"는 지시에 따라 라벨 레벨 검증(2026-07-19
    dream_catalyst, TTM 적자모집단 12개월 forward 50%+급등 달성률 학습17.2%/검증25.0%
    vs 무촉매14.9%/14.3%)이 실전 백테스트로 옮겨진 적이 없음을 확인해 이번에 최초 구현.
    독립 재검증(다른 방법론, 반기간격 무작위대조군): 적자모집단 50%+달성률 학습42.3%
    vs 대조35.9%(lift1.18x), 검증41.9% vs 34.8%(lift1.20x) — 학습·검증 방향 일치·재현.
    ⚠️ V-CONTRACT-MOMENTUM(lift 훨씬 강함)보다 약한 신호 — 실전 수익성은 미검증, 이하
    walk-forward로 확인 필요.

    매수: dart_rd_patent_signals(signal_type 4종 patent/tech_transfer/rd_contract/license
         반드시 합산 사용 — 2026-07-19 검증: 유형별 분리는 학습·검증 부호가 뒤집힘)
         공시 + TTM 순이익≤0(적자모집단, as-of) + 희석위험≤dilution_max + 시총≥min_mktcap_억(as-of).
         공시 다음거래일 시가매수, 동일일 복수신호는 시총 내림차순(대형 적자 소형 아님 우선순위 없음 — 균등).
    매도: 손절-25%/추적손절-30%(이익10%+발동)/만기365일.
    """
    init_backtest_db()
    run_name = run_name or f"V-PATENT-CATALYST {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "patent_catalyst", "patent_catalyst_v1_20260809",
        {"dilution_max": dilution_max, "min_mktcap_억": min_mktcap_억, "stop": stop,
         "trail": trail, "max_hold": max_hold, "max_positions": max_positions,
         "per_stock": per_stock, "asof_mktcap": asof_mktcap,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'patent_catalyst',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=500)).strftime('%Y-%m-%d')

        events_raw = conn.execute("""
            SELECT stock_code, rcept_dt FROM dart_rd_patent_signals
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """).fetchall()

        codes = sorted({e[0] for e in events_raw})
        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open,close) AS o
                FROM price_history WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            sd[code] = {'d': [str(r[0])[:10] for r in rows], 'c': c_list,
                        'o': [float(r[2]) for r in rows]}
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # as-of TTM 순이익(적자모집단 필터) — financial_data 분기 net_income 누적
        overrides = {r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config WHERE config_key='preferred_report_type'")}
        raw_fin = conn.execute("""
            SELECT stock_code, year, quarter, report_type, net_income FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL
              AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall() if sd else []
        by_quarter: Dict[tuple, dict] = {}
        for r in raw_fin:
            by_quarter.setdefault((r[0], r[1], r[2]), {})[r[3]] = r[4]
        panel: Dict[str, list] = {}
        for (code, y, q), variants in by_quarter.items():
            pref = overrides.get(code, "CFS")
            ni = variants.get(pref) if pref in variants else next(iter(variants.values()))
            panel.setdefault(code, []).append((y, q, ni))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        def _avail_date(y: int, q: int) -> str:
            if q == 1: return f"{y}-05-15"
            if q == 2: return f"{y}-08-15"
            if q == 3: return f"{y}-11-15"
            return f"{y+1}-02-15"

        def _ttm_negative(code: str, asof: str) -> bool:
            qs = panel.get(code)
            if not qs or len(qs) < 4:
                return False
            idx = None
            for i, (y, q, ni) in enumerate(qs):
                if _avail_date(y, q) <= asof:
                    idx = i
            if idx is None or idx < 3:
                return False
            ttm = sum((qs[j][2] or 0) for j in range(idx - 3, idx + 1))
            return ttm <= 0

        dilution_map: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT stock_code, disclosed_at FROM dilution_events
            WHERE event_type IN ('CB','BW','EB','RIGHTS') AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())) if sd else []:
            if r[1]:
                dilution_map.setdefault(r[0], []).append(str(r[1])[:10])
        for c in dilution_map:
            dilution_map[c].sort()

        def _dilution_risk(code: str, asof: str) -> int:
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(asof, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return sum(1 for d in evs if cutoff <= d <= asof)

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, ef, et, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(sd))), list(sd.keys())
            ) if sd else []:
                share_intervals.setdefault(code, []).append((ef, et, float(shares or 0), quality))

        def _shares_asof_pc(code: str, day: str) -> float:
            for ef, et, shares, _q in reversed(share_intervals.get(code, [])):
                if ef <= day and (et is None or day < et):
                    return shares
            return 0.0

        buy_pool: Dict[str, list] = {}
        for code, rd in events_raw:
            s = sd.get(code)
            if not s or code not in didx:
                continue
            sig_date = str(rd)[:10]
            pos = None
            for i, d in enumerate(s['d']):
                if d > sig_date:
                    pos = i; break
            if pos is None or pos < 60:
                continue
            avail = sig_date
            if not _ttm_negative(code, avail):
                continue
            if _dilution_risk(code, avail) > dilution_max:
                continue
            entry_date = s['d'][pos]
            if entry_date < start_date or entry_date > end_date:
                continue
            if asof_mktcap:
                mc = _shares_asof_pc(code, entry_date)
                if mc <= 0:
                    continue
            buy_pool.setdefault(entry_date, []).append(code)
        for d in buy_pool:
            buy_pool[d] = sorted(set(buy_pool[d]))

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))

        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        for day in sim_dates:
            _still = []
            for code, reason in pending_sells:
                if code not in pos:
                    continue
                i = didx[code].get(day)
                if i is None:
                    _still.append((code, reason)); continue
                px = sd[code]['o'][i]
                if px <= 0:
                    _still.append((code, reason)); continue
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], px, p['shares'], 300)
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                                'entry': p['entry'], 'exit': px, 'pnl_pct': net_pct,
                                'reason': reason, 'pnl': round(pnl, 0)})
            pending_sells = _still
            for code in pending_buys:
                if code in pos or len(pos) >= max_positions:
                    continue
                i = didx[code].get(day)
                if i is None:
                    continue
                px = sd[code]['o'][i]
                if px <= 0 or cash < px * 10:
                    continue
                budget = min(per_stock, cash * 0.99)
                shares = int(budget // px)
                if shares <= 0:
                    continue
                cash -= shares * px
                pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0, 'peak': px}
            pending_buys = []

            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                stop_cond = ret <= stop
                expire_cond = p['hold'] >= max_hold
                trail_cond = ret > trail_activate_pct and (curr - p['peak']) / p['peak'] <= trail
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
                    if code not in [c for c, _ in pending_sells]:
                        pending_sells.append((code, reason))

            pending_codes = set(pending_buys)
            slots = max_positions - len(pos) - len(pending_codes)
            if slots > 0:
                for code in buy_pool.get(day, []):
                    if slots <= 0:
                        break
                    if code in pos or code in pending_codes:
                        continue
                    pending_buys.append(code)
                    pending_codes.add(code)
                    slots -= 1

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], 300)
            cash += p['shares'] * p['entry'] + pnl
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                            'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                            'reason': 'final', 'pnl': round(pnl, 0)})

        total_return = (cash - total_capital) / total_capital * 100
        completed = [t for t in trades if 'pnl_pct' in t]
        win_rate = (sum(1 for t in completed if t['pnl_pct'] > 0) / len(completed) * 100) if completed else 0
        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, total_trades=?, win_rate=?, trades_json=?
            WHERE run_id=?
        """, (round(total_return, 2), len(completed), round(win_rate, 1),
              json.dumps({"trades": trades}), run_id))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=asof_mktcap)
        return run_id
    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


def run_backtest_earnings_supply_discovery(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 25,
    op_growth_min: float = 1.0,       # 분기 영업이익 YoY 성장 하한(1.0=100%). Codex 2026-08-11 PIT
                                       # walk-forward 발견(생존편향 제거 데이터셋, 학습/검증 양쪽 lift>1):
                                       # supply_20d_억>=10 & op_growth>=100% -> 10x 2.13%/3x 16.31%/
                                       # 5x 8.51%(3배 기준 목표15% 이미 초과). 개별예측 정밀도가 아니라
                                       # V-MOONSHOT과 동일한 분산+익절없음+넓은손절 포트폴리오로 실전
                                       # 백테스트해 실제 운용수익률을 확인하기 위해 이식.
    supply_min_억: float = 10.0,      # 20일 기관+외국인 순매수 합계 하한(억원)
    min_mktcap_억: float = 300,
    stop_loss: float = -0.35,         # V-MOONSHOT과 동일 설계(변동성 큰 모집단, 조기손절 방지)
    trail_pct: float = -0.35,
    max_hold: int = 500,              # ~2년, 텐버거 중위 도달기간(1.3~1.7년) 고려
    asof_mktcap: bool = True,
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-DISCOVERY — Codex PIT(생존편향 제거) walk-forward 발굴 신호 실전 백테스트.

    [배경] 2026-08-11 Codex가 상장폐지 종목 214개(과거가격 200,586건) 포함한 point-in-time
    데이터셋(strategy_feature_snapshot_pit_v2, 187,543행/2,691종목)으로 재검증한 결과,
    기존 heuristic_score>=55 로직이 모든 검증구간에서 역신호(lift<1.0)로 확인되어 폐기됨.
    대신 발굴된 5개 신호 중 최강(`earnings_demand`: 20일 순매수 10억+ & 영업이익 100%+ 성장)이
    학습/검증 양쪽 lift>1을 유지했으나, "10배 단독 예측 정밀도"(2.13%)는 목표(15%) 미달로
    Codex는 실전 승격을 보류함(research_candidate_only). 단 "3배 기준"으로는 이미 목표 초과
    (16.31%>15%) — 개별 예측기가 아니라 V-MOONSHOT과 같은 분산 포트폴리오(익절없음+넓은손절+
    긴만기)로 운용하면 실제 수익이 날 수 있는지 별도 검증 필요.

    매수: 분기 영업이익 YoY 성장(as-of 공시일 기준) >= op_growth_min
         + 20일 기관+외국인 순매수 합계 >= supply_min_억 — 최대 max_positions종목 분산.
    매도: 손절 stop_loss(하드) / 추적손절 trail_pct(이익권) / 만료 max_hold거래일.
    """
    init_backtest_db()
    run_name = run_name or f"V-DISCOVERY {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "earnings_supply_discovery", "earnings_supply_discovery_v1_20260811",
        {"op_growth_min": op_growth_min, "supply_min_억": supply_min_억,
         "min_mktcap_억": min_mktcap_억, "stop_loss": stop_loss, "trail_pct": trail_pct,
         "max_hold": max_hold, "max_positions": max_positions, "asof_mktcap": asof_mktcap,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="diversified_basket",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'earnings_supply_discovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / max_positions, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')
        _pref_pat = re.compile(r"\d?우[A-Z]?$")

        _mktcap_gate = "" if asof_mktcap else "AND COALESCE(market_cap, 0) >= ?"
        _mktcap_param = [] if asof_mktcap else [min_mktcap_억]
        all_rows = conn.execute(f"""
            SELECT stock_code, stock_name, market_cap FROM stock_universe
            WHERE market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
              {_mktcap_gate}
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        """, _mktcap_param).fetchall()
        codes = [r[0] for r in all_rows if not (r[1] and _pref_pat.search(r[1]))]
        mktcap_map = {r[0]: (r[2] or 300) for r in all_rows}
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(codes))), codes
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_ed(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o,
                       (COALESCE(inst_net_buy_amt,0) + COALESCE(frn_net_buy_amt,0)) / 100.0 AS supply_억
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            supply_list = [float(r[3] or 0) for r in rows]
            # 20일 롤링 합계(prefix-sum, O(1) 조회용)
            prefix = [0.0]
            for v in supply_list:
                prefix.append(prefix[-1] + v)
            supply_20d = [
                prefix[i + 1] - prefix[max(0, i - 19)]
                for i in range(len(supply_list))
            ]
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows],
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'supply_20d': supply_20d,
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        if not sd:
            raise RuntimeError("유니버스가 비어있음(가격이력 부족)")

        # 분기 영업이익 YoY 성장(as-of 공시일 기준) — Codex PIT 연구와 동일 정의
        overrides = {r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config "
            "WHERE config_key='preferred_report_type'")}
        raw_rows = conn.execute("""
            SELECT stock_code, year, quarter, report_type, operating_profit
            FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND operating_profit IS NOT NULL
              AND stock_code IN ({})
            ORDER BY stock_code, year, quarter
        """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall()
        by_quarter: Dict[tuple, dict] = {}
        for r in raw_rows:
            key = (r[0], r[1], r[2])
            by_quarter.setdefault(key, {})[r[3]] = r
        panel: Dict[str, list] = {}
        for (code, y, q), variants in by_quarter.items():
            pref = overrides.get(code, "CFS")
            r_op = variants.get(pref) or next(iter(variants.values()))
            panel.setdefault(code, []).append((y, q, r_op[4]))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        def _avail_date(y: int, q: int) -> str:
            if q == 1: return f"{y}-05-15"
            if q == 2: return f"{y}-08-15"
            if q == 3: return f"{y}-11-15"
            return f"{y+1}-02-15"

        # 종목별 (avail_date, op_growth) 이벤트 리스트
        growth_events: Dict[str, list] = {}
        for code, qs in panel.items():
            n = len(qs)
            for i in range(4, n):
                y, q, op = qs[i]
                op_prev = qs[i - 4][2]
                if op is None or op_prev is None or op_prev <= 0:
                    continue
                growth = op / op_prev - 1.0
                if not (-5 <= growth <= 10):  # PIT 연구와 동일 이상치 제외
                    continue
                avail = _avail_date(y, q)
                growth_events.setdefault(code, []).append((avail, growth))
        for code in growth_events:
            growth_events[code].sort()

        def _current_growth(code: str, day: str):
            evs = growth_events.get(code)
            if not evs:
                return None
            avail = [e for e in evs if e[0] <= day]
            if not avail:
                return None
            return avail[-1][1]

        per_stock = total_capital / max_positions
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        for day in sim_dates:
            if strict_exec:
                _still = []
                for code, reason in pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                pending_sells = _still
                for code in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}
                pending_buys = []

            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                stop_cond = ret <= stop_loss
                expire_cond = p['hold'] >= max_hold
                trail_cond = trail_pct is not None and ret > 0 and (curr - p['peak']) / p['peak'] < trail_pct
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
                    if strict_exec:
                        if code not in [c for c, _ in pending_sells]:
                            pending_sells.append((code, reason))
                    else:
                        pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                        cash += p['shares'] * p['entry'] + pnl
                        trades.append({
                            'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                            'entry': p['entry'], 'exit': curr,
                            'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                        })
                        pos.pop(code, None)

            if len(pos) + len(pending_buys) < max_positions:
                candidates = []
                pending_codes = set(pending_buys) if strict_exec else set()
                for code in sd:
                    if code in pos or code in pending_codes:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    curr = sd[code]['c'][i]
                    if curr <= 0:
                        continue
                    if asof_mktcap:
                        _sh = _shares_asof_ed(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    growth = _current_growth(code, day)
                    if growth is None or growth < op_growth_min:
                        continue
                    supply_now = sd[code]['supply_20d'][i]
                    if supply_now < supply_min_억:
                        continue
                    candidates.append((growth, code))
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_codes)
                picked = candidates[:slots]
                if strict_exec:
                    for _, code in picked:
                        pending_buys.append(code)
                else:
                    for _, code in picked:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        budget = min(per_stock, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': curr,
                'pnl_pct': net_pct, 'reason': 'final', 'pnl': round(pnl, 0),
            })

        name_map = {}
        all_codes = list({t['code'] for t in trades})
        for i in range(0, len(all_codes), 400):
            batch = all_codes[i:i + 400]
            ph = ",".join("?" * len(batch))
            for sc, sn in conn.execute(
                f"SELECT stock_code, stock_name FROM stock_universe WHERE stock_code IN ({ph})", batch
            ):
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['code'], t['code'])

        total_return = (cash - total_capital) / total_capital * 100
        win_trades = sum(1 for t in trades if t['pnl'] > 0)
        win_rate = (win_trades / len(trades) * 100) if trades else 0.0
        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"★ V-DISCOVERY: op_growth>={op_growth_min*100:.0f}% + supply20d>={supply_min_억}억 / "
            f"손절{stop_loss*100:.0f}% / trail{trail_pct*100:.0f}% / 만기{max_hold}거래일\n"
            f"총 거래: {len(trades)}건  승률: {win_rate:.1f}%  총수익률: {total_return:.2f}%"
        )
        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?, total_trades=?,
                profit_trades=?, trades_json=?, summary_text=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades), win_trades,
            json.dumps({"trades": trades}, ensure_ascii=False), summary_text, run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=asof_mktcap)
        return run_id
    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--start',     default='2023-04-01')
    parser.add_argument('--end',       default='2025-12-31')
    parser.add_argument('--per-stock', type=float, default=10_000_000)
    parser.add_argument('--max-pos',   type=int,   default=10)
    parser.add_argument('--version',   default='V4', choices=['V4','V8','V10','V11','V12'])

    print(f"백테스트 시작 ({args.version}): {args.start} ~ {args.end}  "
          f"(종목당 {args.per_stock:,.0f}원, 최대 {args.max_pos}종목)")

    fn_map = {'V4': run_backtest, 'V8': run_backtest_v8,
              'V10': run_backtest_v10, 'V11': run_backtest_v11,
              'V12': run_backtest_v12}
    rid = fn_map[args.version](args.start, args.end,
                                per_stock=args.per_stock, max_positions=args.max_pos)
    print(f"완료! run_id={rid}")
    conn = sqlite3.connect(DB_PATH, timeout=120)
    row  = conn.execute("SELECT summary_text FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
    conn.close()
    if row: print(row[0])
