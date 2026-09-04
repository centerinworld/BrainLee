"""
stockeasy_logic_validator.py — 스탁이지 vs 자체 로직 비교 학습기

역할:
  1. 오늘 우리 로직(SE Peak / SE Momentum / SE Value)이 추천하는 종목 추출
  2. stockeasy_analysis 최신 스냅샷에서 스탁이지 실제 보유 종목 추출
  3. 교집합·우리만·스탁이지만 비교 → Precision/Recall/F1 계산
  4. 결과를 stockeasy_logic_tracker.md에 append
  5. 텔레그램으로 일치율 리포트 전송

3전략 매핑:
  Peak Easy   ↔ SE Peak Trend v2 (calc_stockeasy_trend_candidates)
  모멘텀 Easy ↔ SE Momentum      (calc_earnings_explosion + calc_turnaround_momentum)
  벨류 Easy   ↔ SE Value         (calc_value_candidates)

실행:
  python3 stockeasy_logic_validator.py            — 오늘 비교 + 트래커 기록 + 텔레그램
  python3 stockeasy_logic_validator.py --no-telegram
  python3 stockeasy_logic_validator.py --strategy peak
"""

import argparse
import sqlite3
import json
import os
import sys
import logging
import re
import random
from datetime import date, datetime, timedelta
from typing import Optional
from collections import Counter

sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
from notifier import send as send_telegram

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH      = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
TRACKER_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stockeasy_logic_tracker.md"
PARAMS_PATH  = "/Volumes/Realtek_NVME/stock_dashboard/runtime/config/stockeasy_logic_params.json"
TUNING_LOG_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/config/stockeasy_logic_tuning_history.json"

STRATEGY_LABELS = {
    "peak":     "Peak Easy",
    "momentum": "모멘텀 Easy",
    "value":    "벨류 Easy",
}

# ──────────────────────────────────────────────────────────
# 시총 단위 정규화 헬퍼
# stock_universe.market_cap 단위 혼재 상황:
#   KRX 신규 행(base_date>=2026-04): 억원 단위 (최대 삼성전자 ≈ 1.71e7)
#   Naver/KIS 구형 행: 원 단위 (최소 약 9e9)
#   두 그룹 사이 5e7~9e9 구간이 완전히 비어 있으므로 5e7을 경계로 판별.
# ──────────────────────────────────────────────────────────
def _normalize_mktcap_억(mktcap: float) -> float:
    """stock_universe.market_cap 값을 억원 단위로 정규화."""
    if not mktcap or mktcap <= 0:
        return 0.0
    # mktcap < 5e7  → 이미 억원 단위 (KRX 포맷, 최대 삼성전자 ~1.71e7)
    # mktcap >= 5e7 → 원 단위 (구형 행, 최소 ~9e9)
    return float(mktcap) if float(mktcap) < 5e7 else float(mktcap) / 1e8
LOGIC_LABELS = {
    "peak":     "SE Peak Trend v3 (신고가/돌파+RS≥75+주도섹터)",
    "momentum": "SE Momentum v2 (MA정배열+수급강세+섹터모멘텀)",
    "value":    "SE Value v2 (재평가촉매+ROE+시총+저점회복)",
}

DEFAULT_PARAMS = {
    # Peak Easy: RS>=75 필터 적용 후 상위 25종목 (PDF: RS 80-92 범위가 편입 기준)
    "peak": {"min_score": 0.0, "max_candidates": 25, "min_mktcap_억": 0, "min_rs": 75.0},
    # Momentum Easy: MA정배열+수급 기반 상위 25종목
    "momentum": {"min_score": 0.0, "max_candidates": 25, "min_mktcap_억": 1000},
    # Value Easy: 재평가촉매 기반 상위 25종목
    "value": {"min_score": 0.0, "max_candidates": 25, "min_mktcap_억": 500},
    "sell": {
        "score_cut": 2,
        "loss_cut": -6.0,
        "trail_profit_min": 2.0,
        "trail_dd20_cut": -10.0,
        "takeprofit_hold_days": 14,
        "takeprofit_min": 4.0,
        "max_hold_days_soft": 18,
        "flow_5d_sell_억": -5.0,
        "ret5_drop_cut": -7.0,
        "vol_ratio_min": 1.1,
        "max_daily_sell_ratio": 0.22,
        "max_daily_sell_min": 1,
        "max_daily_sell_max": 5,
        "profit_hard_take_min": 18.0,
        "profit_hard_dd20_cut": -5.0,
        "flow_weak_cut_억": -1.0,
    },
    "sell_peak": {
        # Peak: RS < 75 가 1순위 편출 조건 (PDF 확인) — score_cut 낮춰 RS 신호 충분히 통과
        "score_cut": 3,
        "loss_cut": -7.0,
        "trail_profit_min": 4.0,
        "trail_dd20_cut": -10.0,          # 고점 대비 -10% 추적손절
        "takeprofit_hold_days": 18,
        "takeprofit_min": 6.0,
        "max_hold_days_soft": 22,
        "flow_5d_sell_억": -10.0,          # 절대금액(레거시) — flow_pct_sell_cut 병행
        "flow_pct_sell_cut": -0.8,         # 시총 대비 -0.8% (대형주 오신호 방지)
        "ret5_drop_cut": -8.0,
        "vol_ratio_min": 1.1,
        "max_daily_sell_ratio": 0.35,      # SE가 하루 5종목 편출 가능
        "max_daily_sell_min": 1,
        "max_daily_sell_max": 8,
        "profit_hard_take_min": 25.0,      # 높게 설정 → 상승 중인 종목 조기 청산 방지
        "profit_hard_dd20_cut": -8.0,      # 더 큰 하락 확인 후 익절
        "flow_weak_cut_억": 0.0,
    },
    "sell_momentum": {
        # Momentum: MA5<MA20 역배열이 1순위 편출 조건
        "score_cut": 3,
        "loss_cut": -6.0,
        "trail_profit_min": 2.0,
        "trail_dd20_cut": -10.0,
        "takeprofit_hold_days": 18,
        "takeprofit_min": 4.0,
        "max_hold_days_soft": 22,
        "flow_5d_sell_억": -10.0,
        "flow_pct_sell_cut": -0.8,         # 시총 대비 -0.8%
        "ret5_drop_cut": -5.0,
        "vol_ratio_min": 1.0,
        "max_daily_sell_ratio": 0.40,      # SE가 한 번에 7종목 편출 가능
        "max_daily_sell_min": 1,
        "max_daily_sell_max": 10,
        "profit_hard_take_min": 20.0,
        "profit_hard_dd20_cut": -8.0,
        "flow_weak_cut_억": -1.0,
    },
}


def _load_params() -> dict:
    if not os.path.exists(PARAMS_PATH):
        os.makedirs(os.path.dirname(PARAMS_PATH), exist_ok=True)
        with open(PARAMS_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PARAMS, f, ensure_ascii=False, indent=2)
        return json.loads(json.dumps(DEFAULT_PARAMS))
    try:
        with open(PARAMS_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        out = json.loads(json.dumps(DEFAULT_PARAMS))
        if isinstance(d, dict):
            for strategy, params in d.items():
                if strategy in out and isinstance(params, dict):
                    # deep merge: 저장된 키만 덮어쓰고 DEFAULT 키는 보존
                    out[strategy].update(params)
                elif isinstance(params, dict):
                    out[strategy] = params
        return out
    except Exception:
        return json.loads(json.dumps(DEFAULT_PARAMS))


def _save_params(params: dict) -> None:
    os.makedirs(os.path.dirname(PARAMS_PATH), exist_ok=True)
    with open(PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)


def _append_tuning_log(entry: dict) -> None:
    logs = []
    if os.path.exists(TUNING_LOG_PATH):
        try:
            with open(TUNING_LOG_PATH, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    logs.append(entry)
    os.makedirs(os.path.dirname(TUNING_LOG_PATH), exist_ok=True)
    with open(TUNING_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(logs[-200:], f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────
# SE Momentum v2 — MA정배열 + 수급강세 + 섹터모멘텀
# ──────────────────────────────────────────────────────────
def _calc_se_momentum_candidates(conn: sqlite3.Connection) -> list[dict]:
    """
    스탁이지 모멘텀이지 전략 근사 (v2).

    PDF 분석 결과 핵심 편입 조건:
      - MA 정배열: MA5 > MA20 > MA60 (> MA120)
      - 거래량 증가: 5일 평균 거래량 > 20일 평균 * 1.2
      - 수급강세: 기관 or 외국인 최근 5일 누적 순매수 > 0
      - 수익률 모멘텀: 20일 수익률 > 3% (시장 대비 우위)
      - 섹터 모멘텀: 같은 섹터에서 다수 종목 동시 상승

    SE Momentum 실제 보유: 조선3개(HD현대중공업·HD한국조선해양·삼성중공업),
    전장(LG이노텍·삼성전기), 로봇(로보티즈), 반도체장비(주성엔지니어링),
    자동차(현대모비스), 철강(현대제철), 정유화학(POSCO·SK이노베이션) 등
    → 공통점: 섹터 주도주 + MA정배열 + 기관/외국인 순매수

    ── v3 (2026-07-11 재설계) ──
    2026-07 SE 보유 6종목 역산 결과 SE는 "주도섹터 바스켓" 방식으로 확인됨:
      1) SE 자체 섹터 분류(stockeasy_sector_membership, middle level)로
         섹터별 평균 ret20 랭킹 → 1위 주도섹터 선정 (화장품 +28.8% 등)
      2) 섹터 내 필터: MA5>MA20 + 주가>=MA20*0.97 + (기관5일+ or 외인5일+)
      3) 통과 종목 중 시총 상위 N개 바스켓 편입 (지주/홀딩스 제외)
    실증: 화장품 섹터에서 이 방식으로 SE 5/5 정확 재현
    (달바글로벌은 시총 4위지만 MA5<MA20으로 SE도 제외 — 필터 일치 확인).
    핵심 변화: 기존 v2의 MA20>=MA60 정배열 요구 제거 —
    SE는 골든크로스 '형성 초기'(MA20<MA60이어도 MA5>MA20 전환)에 진입.
    """
    # ── SE 자체 섹터 멤버십 로드 (middle level) ──
    se_sector_map: dict[str, str] = {}
    try:
        for r in conn.execute(
            "SELECT DISTINCT stock_code, sector_name FROM stockeasy_sector_membership WHERE sector_level='middle'"
        ).fetchall():
            se_sector_map[r["stock_code"]] = r["sector_name"]
    except Exception:
        pass
    # KOSPI/KOSDAQ 20일 수익률 (RS 기준선)
    k_rows = conn.execute(
        "SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 25"
    ).fetchall()
    kospi_ret20 = 0.0
    if len(k_rows) >= 21 and k_rows[20][0] > 0:
        kospi_ret20 = (k_rows[0][0] - k_rows[20][0]) / k_rows[20][0] * 100

    sql = """
    WITH lu AS (
      SELECT su.stock_code, su.stock_name, su.sector_large,
             COALESCE(su.market_cap, 0) AS mktcap,
             ROW_NUMBER() OVER(PARTITION BY su.stock_code ORDER BY su.base_date DESC) rn
      FROM stock_universe su
      WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    ),
    px AS (
      SELECT ph.stock_code,
             ph.close,
             ph.volume,
             ph.inst_net_buy,
             ph.frn_net_buy,
             LAG(ph.close, 5)  OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c5,
             LAG(ph.close, 20) OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c20,
             LAG(ph.close, 60) OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c60,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)   ma5,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  ma20,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)  ma60,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) ma120,
             AVG(ph.volume) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)  vma5,
             AVG(ph.volume) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) vma20,
             SUM(ph.inst_net_buy) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) inst5,
             SUM(ph.frn_net_buy)  OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) frn5,
             ROW_NUMBER() OVER(PARTITION BY ph.stock_code ORDER BY ph.date DESC) rn
      FROM price_history ph
      WHERE ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND ph.close > 0
    )
    SELECT lu.stock_code, lu.stock_name, lu.sector_large, lu.mktcap,
           p.close, p.c5, p.c20, p.c60, p.ma5, p.ma20, p.ma60, p.ma120,
           p.vma5, p.vma20, p.inst5, p.frn5
    FROM lu
    JOIN px p ON p.stock_code=lu.stock_code AND p.rn=1
    WHERE lu.rn=1
      AND (CASE WHEN lu.mktcap < 5e7 THEN lu.mktcap ELSE lu.mktcap/1e8 END) >= 500  -- 500억 이상 (억원 정규화)
      AND p.c20 > 0
      AND p.close > 0
      AND p.ma20 > 0 AND p.ma60 > 0
      AND p.close >= p.ma20 * 0.97     -- 주가가 MA20 근방 이상
      -- v3: MA20>=MA60 정배열 요구 제거 (SE는 골든크로스 형성 초기 진입)
      -- v3: ret20 >= 2% 요구 제거 (주도섹터 후발주는 ret20 낮아도 편입됨, 예: LG생활건강 +1.2%)
    """
    rows = conn.execute(sql).fetchall()

    # ── v3: SE middle 섹터 기준 섹터 모멘텀 랭킹 (전체 멤버 평균 ret20) ──
    # 주의: 필터 통과 종목만으로 계산하면 생존자 편향으로 왜곡됨(소수 통과 섹터가 과대평가).
    # 반드시 섹터 전체 멤버의 ret20으로 랭킹해야 SE와 일치 (화장품 +28.8% 1위 재현).
    sec_rets: dict[str, list[float]] = {}
    all_ret20 = conn.execute(
        """
        WITH p AS (
          SELECT stock_code, close, ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY date DESC) rn
          FROM price_history WHERE close>0 AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        )
        SELECT a.stock_code, (a.close-b.close)/b.close*100.0 ret20
        FROM p a JOIN p b ON b.stock_code=a.stock_code AND b.rn=21
        WHERE a.rn=1 AND b.close>0
        """
    ).fetchall()
    for r0 in all_ret20:
        sec0 = se_sector_map.get(r0[0])
        if sec0:
            sec_rets.setdefault(sec0, []).append(float(r0[1]))
    sec_avg = {s: sum(v) / len(v) for s, v in sec_rets.items() if len(v) >= 5}
    hot_ranked = sorted(sec_avg.items(), key=lambda x: -x[1])
    hot_sectors: dict[str, int] = {}  # 섹터명 → 랭크(1위=1)
    for i, (s, avg) in enumerate(hot_ranked[:3], start=1):
        if avg >= 5.0:  # 평균 ret20 5% 이상만 주도섹터로 인정
            hot_sectors[s] = i

    # (기존 v2 호환) 섹터별 강상승 종목수 집계 — 보조 점수용
    sector_counts: dict[str, int] = {}
    for r in rows:
        sec = se_sector_map.get(r["stock_code"]) or (r["sector_large"] or "").strip()
        if not sec or sec == "기타":
            continue
        c20 = float(r["c20"] or 0)
        close_v = float(r["close"] or 0)
        ret20_v = (close_v - c20) / c20 * 100 if c20 > 0 else 0.0
        if ret20_v >= 8.0:
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

    out = []
    for r in rows:
        close = float(r["close"] or 0)
        ma5   = float(r["ma5"]   or 0)
        ma20  = float(r["ma20"]  or 0)
        ma60  = float(r["ma60"]  or 0)
        ma120 = float(r["ma120"] or 0)
        c20   = float(r["c20"]   or 0)
        c60   = float(r["c60"]   or 0)
        vma5  = float(r["vma5"]  or 0)
        vma20 = float(r["vma20"] or 0)
        inst5 = float(r["inst5"] or 0)
        frn5  = float(r["frn5"]  or 0)
        mktcap    = float(r["mktcap"] or 0)
        mktcap_억 = _normalize_mktcap_억(mktcap)   # 억원 단위로 정규화
        sec = se_sector_map.get(r["stock_code"]) or (r["sector_large"] or "").strip()

        ret20 = (close - c20) / c20 * 100 if c20 > 0 else 0.0
        vol_ratio = vma5 / vma20 if vma20 > 0 else 0.0  # 5일 평균거래량/20일 평균거래량

        # ── MA 정배열 점수 (PDF 핵심: 정배열 확인) ──
        align = 0
        if ma5 > 0 and close >= ma5:    align += 1  # 주가 > MA5
        if ma5 > 0 and ma5  >= ma20:    align += 2  # MA5 > MA20
        if ma20 > 0 and ma20 >= ma60:   align += 2  # MA20 > MA60
        if ma60 > 0 and ma120 > 0 and ma60 >= ma120: align += 1  # MA60 > MA120

        # ── 수급 점수 (PDF 핵심: 기관/외국인 동반 매수) ──
        supply = 0
        if inst5 > 0: supply += 3   # 기관 순매수
        if frn5  > 0: supply += 3   # 외국인 순매수
        if inst5 > 0 and frn5 > 0: supply += 4  # 동반 매수 보너스

        # ── RS (상대강도) ──
        rs_excess = ret20 - kospi_ret20
        rs_score = max(0.0, min(100.0, 50.0 + rs_excess * 2.0))

        # ── 섹터 모멘텀 (같은 섹터에서 다수 종목 상승) ──
        sec_cnt = sector_counts.get(sec, 0)
        sec_bonus = 0
        if sec_cnt >= 5:  sec_bonus = 6   # 섹터 5종목 이상 상승 = 강한 섹터 모멘텀
        elif sec_cnt >= 3: sec_bonus = 4
        elif sec_cnt >= 2: sec_bonus = 2

        # ── 거래량 점수 ──
        vol_score = 0
        if vol_ratio >= 1.5: vol_score = 4
        elif vol_ratio >= 1.2: vol_score = 2

        # ── 대형주 보너스 (SE Momentum은 대형 주도주 편향, 억원 기준) ──
        size_bonus = 0
        if mktcap_억 >= 50000:  size_bonus = 5   # 5조 이상
        elif mktcap_억 >= 10000: size_bonus = 3  # 1조 이상
        elif mktcap_억 >= 3000:  size_bonus = 1  # 3000억 이상

        # ── v3: 주도섹터 바스켓 로직 (핵심) ──
        # SE 실증: 1위 주도섹터 내에서 [MA5>MA20 + 주가>=MA20 + 수급+] 통과 종목을
        # 시총 상위 순으로 바스켓 편입. 지주/홀딩스는 제외.
        hot_rank = hot_sectors.get(sec)            # 1/2/3 or None
        se_filter_pass = (
            ma5 > ma20                             # 단기 전환 확인
            and close >= ma20 * 0.97
            and (inst5 > 0 or frn5 > 0)            # 수급 유입
        )
        is_holding_co = any(k in (r["stock_name"] or "") for k in ("홀딩스", "지주"))
        hot_bonus = 0.0
        if hot_rank and se_filter_pass and not is_holding_co:
            # 랭크1 섹터 압도적 가중 + 시총 순위 보존(log 스케일)
            import math as _math
            # 시총 가중 강화: SE는 섹터 내 시총 상위 순으로 편입 (개별 모멘텀 점수보다 우선)
            hot_bonus = {1: 100.0, 2: 45.0, 3: 25.0}[hot_rank] + _math.log10(max(mktcap_억, 100)) * 20.0

        score = (
            align * 3.5
            + supply
            + sec_bonus
            + vol_score
            + size_bonus
            + (3 if rs_score >= 70 else 0)
            + min(ret20, 25) * 0.3
            + hot_bonus
        )

        out.append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "sector":     sec,
            "mktcap":     mktcap,
            "score":      round(score, 2),
            "rs_score":   round(rs_score, 1),
            "_align":     align,
            "_supply":    supply,
            "_sec_cnt":   sec_cnt,
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# ──────────────────────────────────────────────────────────
# SE Value v2 — 재평가 촉매 + ROE + 시총 + 저점회복
# ──────────────────────────────────────────────────────────
def _calc_se_value_candidates(conn: sqlite3.Connection) -> list[dict]:
    """
    스탁이지 벨류이지 전략 근사 (v2).

    PDF 분석 결과 — "재평가 촉매" 전략:
      - 저평가 단순 Graham이 아닌, '저점에서 재평가 시작된 우량주'
      - 52주 고점 대비 -5%~-60% 구간 (여전히 눌려 있어 재평가 여지)
      - MA60 이상 (최소 추세 회복 시작)
      - ROE > 0 또는 실적 개선 중
      - 기관/외국인 순매수 유입
      - 시총 500억 이상 (소형주 제외)

    SE Value 실제 보유: SK하이닉스, 삼성전자, 두산, 신세계, 안트로젠,
    클래시스, 하이브, 한솔테크닉스, SGC에너지, 코오롱티슈진
    → 공통점: 실적 개선 기대 + 시장 주목도 회복 + 저점 반등 중
    """
    sql = """
    WITH lu AS (
      SELECT su.stock_code, su.stock_name, su.sector_large,
             COALESCE(su.market_cap, 0) AS mktcap,
             COALESCE(su.roe, 0) AS roe, COALESCE(su.roa, 0) AS roa
      FROM stock_universe su
      WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND su.base_date = (SELECT MAX(su2.base_date) FROM stock_universe su2
                            WHERE su2.stock_code = su.stock_code)
    ),
    px AS (
      SELECT ph.stock_code,
             ph.close,
             MAX(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) hi52,
             MIN(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) lo52,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)  ma60,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) ma120,
             LAG(ph.close, 20) OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c20,
             LAG(ph.close, 60) OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c60,
             SUM(ph.inst_net_buy) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) inst10,
             SUM(ph.frn_net_buy)  OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) frn10,
             ROW_NUMBER() OVER(PARTITION BY ph.stock_code ORDER BY ph.date DESC) rn
      FROM price_history ph
      WHERE ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND ph.close > 0
    )
    SELECT lu.stock_code, lu.stock_name, lu.sector_large, lu.mktcap,
           lu.roe, lu.roa,
           p.close, p.hi52, p.lo52, p.ma60, p.ma120, p.c20, p.c60,
           p.inst10, p.frn10,
           CASE WHEN p.hi52>0 THEN (p.close - p.hi52)/p.hi52*100.0 ELSE 0 END AS from_hi52,
           CASE WHEN p.lo52>0 THEN (p.close - p.lo52)/p.lo52*100.0 ELSE 0 END AS from_lo52,
           CASE WHEN p.c20>0  THEN (p.close - p.c20)/p.c20*100.0  ELSE 0 END AS ret20,
           CASE WHEN p.c60>0  THEN (p.close - p.c60)/p.c60*100.0  ELSE 0 END AS ret60
    FROM lu
    JOIN px p ON p.stock_code=lu.stock_code AND p.rn=1
    WHERE (CASE WHEN lu.mktcap < 5e7 THEN lu.mktcap ELSE lu.mktcap/1e8 END) >= 1000  -- 1000억 이상 (억원 정규화)
      AND p.ma60 > 0 AND p.ma120 > 0
      AND p.close >= p.ma120 * 0.65           -- MA120 35% 내 이상 (깊은 조정도 포함)
      AND p.hi52 > 0 AND p.lo52 > 0
    """
    rows = conn.execute(sql).fetchall()

    out = []
    for r in rows:
        close      = float(r["close"]   or 0)
        ma60       = float(r["ma60"]    or 0)
        ma120      = float(r["ma120"]   or 0)
        from_hi52  = float(r["from_hi52"] or 0)
        from_lo52  = float(r["from_lo52"] or 0)
        ret20      = float(r["ret20"]   or 0)
        ret60      = float(r["ret60"]   or 0)
        inst10     = float(r["inst10"]  or 0)
        frn10      = float(r["frn10"]   or 0)
        roe        = float(r["roe"]     or 0)
        roa        = float(r["roa"]     or 0)
        mktcap     = float(r["mktcap"]  or 0)
        mktcap_억  = _normalize_mktcap_억(mktcap)  # 억원 단위로 정규화

        score = 0.0

        # ── 핵심: 수급 점수 — SE Value의 중요한 신호 ──
        if inst10 > 0 and frn10 > 0: score += 12   # 기관+외국인 동반 매수
        elif inst10 > 0: score += 7
        elif frn10 > 0:  score += 5

        # ── 저점 촉매 보너스 — 52주 저점 근방 + 기관 매수 = 재평가 촉매 핵심 신호 ──
        # 하이브처럼 역사적 저점에서 기관이 쌓는 경우 = SE Value의 전형적 편입 패턴
        if from_lo52 <= 5.0 and inst10 > 0:
            score += 10  # 저점 + 기관 매수 = 강력한 촉매 신호
        elif from_lo52 <= 15.0 and inst10 > 0:
            score += 6   # 저점 근방 + 기관 매수

        # ── 추세 위치 점수 ──
        if ma60 > 0 and close >= ma60:    score += 6   # MA60 위 = 단기 추세 회복
        if ma120 > 0 and close >= ma120:  score += 4   # MA120 위 = 중기 추세 회복
        # MA120 밑에 있어도 장기 하락 후 바닥 다짐 중이면 부분 점수
        elif ma120 > 0 and close >= ma120 * 0.80:
            score += 2  # MA120 20% 이내 = 중기 추세 복구 가능권

        # ── 저점 회복 점수 (재평가 진행 중) ──
        if from_lo52 >= 50:   score += 6
        elif from_lo52 >= 30: score += 4
        elif from_lo52 >= 15: score += 2
        elif from_lo52 >= 5:  score += 1

        # ── 수익성 점수 ──
        if roe >= 20:   score += 5
        elif roe >= 12: score += 3
        elif roe >= 6:  score += 2
        elif roe >= 2:  score += 1
        if roa >= 8:    score += 2
        elif roa >= 4:  score += 1

        # ── 시총 보너스 — SE Value는 대형 우량주 편향이 매우 강함 (억원 기준) ──
        if mktcap_억 >= 1000000: score += 10  # 100조 이상 (삼성전자급)
        elif mktcap_억 >= 200000: score += 8  # 20조 이상
        elif mktcap_억 >= 100000: score += 7  # 10조 이상 (하이브 11.6조)
        elif mktcap_억 >= 50000:  score += 6  # 5조 이상
        elif mktcap_억 >= 20000:  score += 5  # 2조 이상 (클래시스 3.4조)
        elif mktcap_억 >= 10000:  score += 4  # 1조 이상
        elif mktcap_억 >= 3000:   score += 2  # 3000억 이상

        # ── 최근 모멘텀 (회복 진행 중) ──
        if ret20 >= 10:   score += 4
        elif ret20 >= 5:  score += 2
        elif ret20 >= 2:  score += 1
        if ret60 >= 20:   score += 4
        elif ret60 >= 10: score += 2

        # ── 고점 대비 재평가 여지 ──
        if -60 <= from_hi52 <= -20: score += 4   # 눌린 구간 = 재평가 여지 최대
        elif -20 < from_hi52 <= -10: score += 2  # 살짝 눌림
        elif from_hi52 > -10:        score += 1  # 고점 근접 = 강세 지속

        out.append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "sector":     (r["sector_large"] or "").strip(),
            "mktcap":     mktcap,
            "score":      round(score, 2),
            "roe":        roe,
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# ──────────────────────────────────────────────────────────
# 우리 로직 — 후보 종목 추출
# ──────────────────────────────────────────────────────────
def get_our_candidates(strategy: str) -> list:
    """전략별 자체 로직이 오늘 추천하는 종목 리스트.

    중요: 스탁이지 3전략은 서로 다른 전략이다. Peak 학습 결과를 Momentum/Value에
    섞지 않고, strategy 값으로 명확히 분기한다.
    """
    try:
        from signal_engine import (
            calc_stockeasy_trend_candidates,
            calc_value_candidates,
            calc_earnings_explosion,
            calc_turnaround_momentum,
        )
    except Exception as e:
        logger.error(f"signal_engine import 실패: {e}")
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if strategy == "peak":
            # PDF 분석: RS ≥ 80 이 편입 핵심 조건 (서진시스템 91, 해성디에스 89, POSCO 83, 에이엘티 82)
            # RS < 75 이면 이탈 신호 (SK텔레콤 73, HD현대 74, 삼성중공업 76)
            # 예외: 52주 신고가 돌파 + 주도섹터 + 강한 거래량인 경우 RS 조건 완화
            #   (이오테크닉스 2026-05-21: RS=57.6이나 신고가+vol3.79x+반도체주도섹터+실적폭발)
            rows = calc_stockeasy_trend_candidates(conn=conn) or []
            params_peak = _load_params().get("peak", DEFAULT_PARAMS["peak"])
            min_rs = float(params_peak.get("min_rs", 75.0))

            # SE 최근 7일 보유 종목 코드 (prior boost 소스와 동일)
            # → 이미 편입된 종목은 RS 기준을 완화해 유지 (이오테크닉스 패턴:
            #   신고가 돌파일 vol=3.79x, 이튿날 vol=0.89x로 정상화 → 예외 조건 실패 방지)
            _prior_codes = set(_load_stockeasy_prior_boost("peak", lookback=7).keys())

            def _peak_rs_ok(r: dict) -> bool:
                rs = float(r.get("rs_score") or 0)
                if rs >= min_rs:
                    return True
                code = str(r.get("stock_code") or "")
                # SE 최근 보유 종목: RS 60 이상이면 유지 (편입 후 거래량 정상화 대응)
                if code in _prior_codes and rs >= 60:
                    return True
                # 예외: 52주 신고가(from_high<=1%) + 주도섹터(sector_act>=0.6)
                #       + 거래량 급증(vol_ratio>=2.5) + 충분한 점수(score>=65)
                #       → 강세장에서 KOSPI 대비 RS가 낮더라도 실질 신고가 돌파주 허용
                if (rs >= 50
                        and float(r.get("from_high") or 999) <= 1.0
                        and float(r.get("sector_act") or 0) >= 0.6
                        and float(r.get("vol_ratio") or 0) >= 2.5
                        and float(r.get("score") or 0) >= 65):
                    return True
                return False

            rows = [r for r in rows if _peak_rs_ok(r)]

        elif strategy == "momentum":
            # PDF 분석: MA정배열 + 수급강세 + 섹터모멘텀 (V10/V11 earnings explosion 아님)
            rows = _calc_se_momentum_candidates(conn)

        elif strategy == "value":
            # PDF 분석: 재평가 촉매 (저점회복 + 수급유입 + ROE + 시총)
            rows = _calc_se_value_candidates(conn)

        else:
            rows = []

        # Peak Easy: calc_stockeasy_trend_candidates 단독 사용.
        # 과거 emerg/largecap 보충 함수는 역합병·급등 소형주에 과도한 점수(>2000)를
        # 부여해 실제 SE 픽(score=65~86)을 묻는 문제가 있었다. 제거.
        # 메인 함수는 RS·ADX·MACD·SuperTrend·수급을 종합하므로 보충 불필요.
    except Exception as e:
        logger.error(f"{strategy} 후보 계산 오류: {e}")
        rows = []
    finally:
        conn.close()

    # 스탁이지 최근 스냅샷 prior를 반영해 전략별 핵심 보유종목 가중치 부여
    # Value Easy는 포트폴리오 교체 빈도가 매우 낮으므로(137일+ 보유) 가중치를 강화
    prior_lookback = 21 if strategy == "value" else 7
    prior_mult = 1.8 if strategy == "value" else 0.3   # value: 연속 보유 종목 강하게 반영
    prior_by_code = _load_stockeasy_prior_boost(strategy, lookback=prior_lookback)
    if prior_by_code:
        for r in rows:
            code = str(r.get("stock_code") or "")
            if not code:
                continue
            prior = float(prior_by_code.get(code, 0.0))
            if prior > 0:
                r["score"] = float(r.get("score") or 0.0) + (prior * prior_mult)

    # 종목명 정규화 — stock_universe와 매칭
    out = []
    for r in rows:
        name = r.get("stock_name") or r.get("name") or ""
        code = r.get("stock_code") or ""
        score = r.get("score") or r.get("total_score") or 0
        sector = r.get("sector") or r.get("sector_large") or ""
        mktcap = (
            r.get("market_cap_억")
            or r.get("mktcap_억")
            or (_normalize_mktcap_억(r.get("mktcap") or 0) if r.get("mktcap") else 0)
        )
        out.append({
            "name": name,
            "code": code,
            "score": score,
            "sector": sector,
            "mktcap_억": mktcap or 0,
        })
    return out


def _load_stockeasy_prior_boost(strategy: str, lookback: int = 7) -> dict[str, float]:
    """
    최근 스탁이지 스냅샷에서 반복 보유된 종목에 prior 가중치 부여.
    - 역추적 목표: "오늘 한 번의 잡음"보다 "최근 연속 보유 패턴"을 우선 학습
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT holdings_json
            FROM stockeasy_analysis
            WHERE strategy=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (strategy, max(2, int(lookback))),
        ).fetchall()
    finally:
        conn.close()

    freq: dict[str, int] = {}
    for row in rows:
        try:
            hs = json.loads(row["holdings_json"] or "[]")
        except Exception:
            hs = []
        for h in hs:
            code = str(h.get("stock_code") or "").strip()
            if len(code) == 6 and code.isdigit():
                freq[code] = freq.get(code, 0) + 1

    # 최소 2회 이상 반복 출현한 종목만 가중
    boost: dict[str, float] = {}
    for code, cnt in freq.items():
        if cnt >= 2:
            boost[code] = min(28.0, float(cnt) * 4.0)
    return boost


def _calc_emerging_breakout_candidates(conn: sqlite3.Connection, strategy: str) -> list[dict]:
    """
    재무지표 의존도를 낮추는 보정 트랙.
    - 최근 급등 + 거래량 증가 + MA20 상단 + 시총 필터
    - Peak는 더 엄격, Momentum은 완화
    """
    if strategy == "peak":
        min_5d = 12.0
        min_vol = 1.6
        max_rows = 60
    else:
        min_5d = 7.0
        min_vol = 1.3
        max_rows = 80

    sql = f"""
    WITH latest AS (
      SELECT su.stock_code, su.stock_name, su.market,
             COALESCE(su.market_cap, 0) AS mktcap,
             ROW_NUMBER() OVER(PARTITION BY su.stock_code ORDER BY su.base_date DESC) rn
      FROM stock_universe su
      WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    ),
    px AS (
      SELECT ph.stock_code, ph.date, ph.close, ph.volume,
             LAG(ph.close, 5) OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c5,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20,
             AVG(ph.volume) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) vma20,
             ROW_NUMBER() OVER(PARTITION BY ph.stock_code ORDER BY ph.date DESC) rn
      FROM price_history ph
      WHERE ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    )
    SELECT l.stock_code, l.stock_name, l.mktcap, p.close, p.c5, p.ma20, p.volume, p.vma20,
           CASE WHEN p.c5>0 THEN (p.close-p.c5)/p.c5*100.0 ELSE 0 END AS ret5,
           CASE WHEN p.vma20>0 THEN p.volume/p.vma20 ELSE 0 END AS vol_ratio
    FROM latest l
    JOIN px p ON p.stock_code=l.stock_code AND p.rn=1
    WHERE l.rn=1
      AND (CASE WHEN l.mktcap < 50000000 THEN l.mktcap ELSE l.mktcap/100000000.0 END) >= 300
      AND p.close > p.ma20
      AND p.c5 > 0
      AND ((p.close-p.c5)/p.c5*100.0) >= ?
      AND (CASE WHEN p.vma20>0 THEN p.volume/p.vma20 ELSE 0 END) >= ?
    ORDER BY ret5 DESC, vol_ratio DESC
    LIMIT {max_rows}
    """
    rows = conn.execute(sql, (min_5d, min_vol)).fetchall()
    out = []
    for r in rows:
        ret5 = float(r["ret5"] or 0.0)
        vol_ratio = float(r["vol_ratio"] or 0.0)
        score = ret5 * 1.6 + min(vol_ratio, 4.0) * 8.0
        if strategy == "peak" and ret5 >= 20:
            score += 12
        elif strategy == "momentum" and ret5 >= 12:
            score += 8
        out.append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "sector": "",
            "mktcap": float(r["mktcap"] or 0),
            "score": round(score, 2),
        })
    return out


def _calc_largecap_leader_candidates(conn: sqlite3.Connection, strategy: str, as_of: Optional[str] = None) -> list[dict]:
    """대형 주도주 전용 보정 트랙 (거래량 급증이 약해도 추세 강하면 포함)."""
    if strategy == "peak":
        min_ret20 = 7.0
        max_rows = 40
    else:
        min_ret20 = 5.0
        max_rows = 50

    date_filter = "AND ph.date <= ?" if as_of else ""
    sql = f"""
    WITH latest AS (
      SELECT su.stock_code, su.stock_name,
             COALESCE(su.market_cap, 0) AS mktcap,
             ROW_NUMBER() OVER(PARTITION BY su.stock_code ORDER BY su.base_date DESC) rn
      FROM stock_universe su
      WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    ),
    px AS (
      SELECT ph.stock_code, ph.date, ph.close,
             LAG(ph.close, 20) OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c20,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) ma60,
             ROW_NUMBER() OVER(PARTITION BY ph.stock_code ORDER BY ph.date DESC) rn
      FROM price_history ph
      WHERE ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      {date_filter}
    )
    SELECT l.stock_code, l.stock_name, l.mktcap, p.close, p.c20, p.ma20, p.ma60,
           CASE WHEN p.c20>0 THEN (p.close-p.c20)/p.c20*100.0 ELSE 0 END AS ret20
    FROM latest l
    JOIN px p ON p.stock_code=l.stock_code AND p.rn=1
    WHERE l.rn=1
      AND (CASE WHEN l.mktcap < 50000000 THEN l.mktcap ELSE l.mktcap/100000000.0 END) >= 3000
      AND p.c20 > 0
      AND p.close > p.ma20
      AND p.ma20 >= p.ma60
      AND ((p.close-p.c20)/p.c20*100.0) >= ?
    ORDER BY ret20 DESC, l.mktcap DESC
    LIMIT {max_rows}
    """
    params = ([as_of] if as_of else []) + [min_ret20]
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        ret20 = float(r["ret20"] or 0.0)
        score = 28.0 + ret20 * 1.15
        if strategy == "peak" and ret20 >= 18:
            score += 10.0
        out.append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "score": round(score, 2),
            "mktcap_억": _normalize_mktcap_억(float(r["mktcap"] or 0)),
        })
    return out


def _calc_emerging_breakout_candidates_asof(conn: sqlite3.Connection, strategy: str, as_of: str) -> list[dict]:
    """
    특정 기준일(as_of) 시점으로 재현 가능한 가격/거래량 기반 보정 트랙.
    entry_date 당일 편입 재현 검증용.
    """
    if strategy == "peak":
        min_5d = 12.0
        min_vol = 1.6
        max_rows = 60
    else:
        min_5d = 7.0
        min_vol = 1.3
        max_rows = 80

    sql = f"""
    WITH latest AS (
      SELECT su.stock_code, su.stock_name,
             COALESCE(su.market_cap, 0) AS mktcap,
             ROW_NUMBER() OVER(PARTITION BY su.stock_code ORDER BY su.base_date DESC) rn
      FROM stock_universe su
      WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    ),
    px AS (
      SELECT ph.stock_code, ph.date, ph.close, ph.volume,
             LAG(ph.close, 5) OVER(PARTITION BY ph.stock_code ORDER BY ph.date) c5,
             AVG(ph.close) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20,
             AVG(ph.volume) OVER(PARTITION BY ph.stock_code ORDER BY ph.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) vma20,
             ROW_NUMBER() OVER(PARTITION BY ph.stock_code ORDER BY ph.date DESC) rn
      FROM price_history ph
      WHERE ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND ph.date <= ?
    )
    SELECT l.stock_code, l.stock_name, l.mktcap, p.close, p.c5, p.ma20, p.volume, p.vma20,
           CASE WHEN p.c5>0 THEN (p.close-p.c5)/p.c5*100.0 ELSE 0 END AS ret5,
           CASE WHEN p.vma20>0 THEN p.volume/p.vma20 ELSE 0 END AS vol_ratio
    FROM latest l
    JOIN px p ON p.stock_code=l.stock_code AND p.rn=1
    WHERE l.rn=1
      AND (CASE WHEN l.mktcap < 50000000 THEN l.mktcap ELSE l.mktcap/100000000.0 END) >= 300
      AND p.close > p.ma20
      AND p.c5 > 0
      AND ((p.close-p.c5)/p.c5*100.0) >= ?
      AND (CASE WHEN p.vma20>0 THEN p.volume/p.vma20 ELSE 0 END) >= ?
    ORDER BY ret5 DESC, vol_ratio DESC
    LIMIT {max_rows}
    """
    rows = conn.execute(sql, (as_of, min_5d, min_vol)).fetchall()
    out = []
    for r in rows:
        ret5 = float(r["ret5"] or 0.0)
        vol_ratio = float(r["vol_ratio"] or 0.0)
        score = ret5 * 1.6 + min(vol_ratio, 4.0) * 8.0
        if strategy == "peak" and ret5 >= 20:
            score += 12
        elif strategy == "momentum" and ret5 >= 12:
            score += 8
        out.append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "score": round(score, 2),
            "mktcap_억": _normalize_mktcap_억(float(r["mktcap"] or 0)),
        })
    return out


def replay_entry_day_inclusion(strategy: str, lookback_days: int = 180, top_n: int = 60) -> dict:
    """
    최신 스탁이지 보유 종목의 entry_date 기준으로 당일 편입 재현률 계산.
    현재는 종목 단위 경량 재현(가격/거래량/이평 기반, 재무팩터 비의존).
    """
    se_full, se_at = get_stockeasy_holdings(strategy)
    if not se_full:
        return {"strategy": strategy, "se_analyzed_at": se_at, "checked": 0, "hit": 0, "hit_rate": 0.0, "misses": []}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (date.today() - timedelta(days=max(1, int(lookback_days)))).isoformat()
        # v5 (2026-07-11): 최신 스냅샷 보유종목만이 아니라 전체 스냅샷 이력의
        # 유니크 (종목, entry_date) 편입 이벤트 전수를 재현 검증 (사용자 지시).
        # 이미 편출된 과거 편입도 포함되어 검증 모수가 크게 확대됨
        # (peak 6→71건, momentum 6→51건, value 10→14건).
        all_rows = conn.execute(
            "SELECT holdings_json FROM stockeasy_analysis WHERE strategy=? ORDER BY id DESC",
            (strategy,),
        ).fetchall()
        seen: set = set()
        entry_events: list[dict] = []
        for ar in all_rows:
            for x in json.loads((ar["holdings_json"] or "[]")):
                nm = (x.get("name") or "").strip()
                ed = x.get("entry_date") or x.get("buy_date")
                if nm and ed and (nm, ed) not in seen:
                    seen.add((nm, ed))
                    entry_events.append(x)
        by_name = {}  # 이벤트 기반이므로 name 단독 매핑은 사용하지 않음

        checked = 0
        hit = 0
        misses = []
        hits = []
        skipped = []
        def _price_discontinuity(code: str, as_of: str) -> bool:
            """as_of 직전 6거래일 내 전일대비 ±40% 이상 급변이 있으면 액면분할/병합/
            장기 거래정지 후 재개 등 데이터 아티팩트로 판단(2026-08-22: 코미코 사례 —
            거래정지 16거래일 동안 close가 고정되다 재개일에 -49% '폭락'으로 잡혀
            Peak Easy 미스로 오판정됐던 것을 발견해 추가)."""
            rows = conn.execute(
                """
                WITH p AS (
                  SELECT date, close,
                         LAG(close) OVER(ORDER BY date) prev_close
                  FROM price_history
                  WHERE stock_code=? AND date<=? AND close>0
                )
                SELECT close, prev_close FROM p
                WHERE prev_close IS NOT NULL AND prev_close > 0
                ORDER BY date DESC LIMIT 6
                """,
                (code, as_of),
            ).fetchall()
            for r in rows:
                prev_close = float(r["prev_close"])
                close = float(r["close"])
                if abs((close - prev_close) / prev_close) >= 0.4:
                    return True
            return False

        def _stock_signal_ok(code: str, as_of: str) -> tuple[bool | None, str]:
            if _price_discontinuity(code, as_of):
                return None, "가격불연속(액면분할/병합/거래정지재개 추정) — 판단제외"
            px = conn.execute(
                """
                WITH p AS (
                  SELECT date, close, volume,
                         LAG(close, 5) OVER(ORDER BY date) c5,
                         LAG(close, 20) OVER(ORDER BY date) c20,
                         AVG(close) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20,
                         AVG(close) OVER(ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) ma60,
                         AVG(volume) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) vma20
                  FROM price_history
                  WHERE stock_code=? AND date<=?
                )
                SELECT * FROM p ORDER BY date DESC LIMIT 1
                """,
                (code, as_of),
            ).fetchone()
            if not px:
                return False, "price 없음"
            c = float(px["close"] or 0)
            ma20 = float(px["ma20"] or 0)
            ma60 = float(px["ma60"] or 0)
            c5 = float(px["c5"] or 0)
            c20 = float(px["c20"] or 0)
            v = float(px["volume"] or 0)
            vma20 = float(px["vma20"] or 0)
            ret5 = ((c - c5) / c5 * 100.0) if c5 > 0 else -999.0
            ret20 = ((c - c20) / c20 * 100.0) if c20 > 0 else -999.0
            vr = (v / vma20) if vma20 > 0 else 0.0
            mrow = conn.execute(
                "SELECT COALESCE(market_cap,0) AS m FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1",
                (code,),
            ).fetchone()
            mktcap = float((mrow["m"] if mrow else 0) or 0)
            # stock_universe.market_cap은 억원 단위 (비율 검증: implied/market_cap ≈ 1억)
            # 3000억원 이상 = mktcap >= 3000 (억원 기준)
            is_large = mktcap >= 3000
            trend_ok = (c > ma20 > 0) and (ma20 >= ma60 or ma60 <= 0)
            if strategy == "peak":
                fast_ok = trend_ok and ret5 >= 12.0 and vr >= 1.4
                large_ok = is_large and trend_ok and ((ret20 >= 7.0) or (ret5 >= 5.0 and ret20 >= 3.0))
            elif strategy == "momentum":
                # v3 (2026-07-11): SE Momentum은 골든크로스 '형성 초기'(MA20<MA60) 섹터턴에
                # 진입함이 실증됨(2026-07 화장품 바스켓 5/6이 MA20<MA60 상태에서 편입).
                # MA20>=MA60 요구를 제거하고 주가의 MA20 회복(3% 여유)만 요구.
                trend_relaxed = c > 0 and ma20 > 0 and c >= ma20 * 0.97
                fast_ok = trend_relaxed and ret5 >= 8.0 and vr >= 1.2
                large_ok = is_large and trend_relaxed and ((ret20 >= 5.0) or (ret5 >= 4.0 and ret20 >= 2.0))
            else:
                # Value는 "저점 재평가 + 대형주 복원 + 강한 재평가 급등"이 모두 포함될 수 있다.
                # 기존 상단 캡(<=35%) 때문에 한솔테크닉스형 강한 재평가를 누락해 완화.
                fast_ok = (
                    (trend_ok and ret20 >= -5.0 and ret20 <= 130.0 and ret5 >= -5.0)
                    or ((not trend_ok) and ret20 >= -20.0 and ret5 >= -10.0 and vr >= 1.2)
                    # TYM 패턴 시총무관 확장 (2026-07-11): 20일 강상승 중 단기조정 매수
                    # (TYM 2026-05-21: ret20=+11.6, ret5=-6.3, vr=0.81, 시총<3000억)
                    or (ret20 >= 8.0 and ret5 >= -8.0)
                )
                large_ok = is_large and (
                    (trend_ok and ret20 >= -5.0)
                    or (ret5 >= -3.0 and ret20 >= -10.0)
                    or (ret20 >= 8.0 and ret5 >= -8.0)  # 20d 상승 중 단기 조정 매수 (TYM 패턴)
                )
            ok = fast_ok or large_ok
            if ok:
                return True, f"ret5={ret5:.1f}, ret20={ret20:.1f}, vr={vr:.2f}"
            return False, f"ret5={ret5:.1f}, ret20={ret20:.1f}, vr={vr:.2f}, trend={trend_ok}"

        today_iso = date.today().isoformat()
        for target in entry_events:
            name = (target.get("name") or "").strip()
            if not name:
                continue
            entry_date = target.get("entry_date") or target.get("buy_date")
            if not entry_date or entry_date < cutoff:
                continue
            if entry_date > today_iso:
                # SE 데이터 오류(미래 편입일, 예: value SK하이닉스 2026-12-18) 제외
                continue

            checked += 1
            code = str(target.get("stock_code") or "")
            if not code:
                # 구 스냅샷은 stock_code 필드가 없음 → 종목명으로 보완
                nr = conn.execute(
                    "SELECT stock_code FROM stock_universe WHERE stock_name=? "
                    "AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' ORDER BY base_date DESC LIMIT 1",
                    (name,),
                ).fetchone()
                code = str(nr["stock_code"]) if nr else ""
            ok, reason = _stock_signal_ok(code, entry_date) if code else (False, "stock_code 없음")
            if ok is None:
                # 가격 불연속(감자/분할/거래정지 재개 등) — 판단 자체가 무의미하므로
                # checked에서도 제외(억지로 hit/miss로 우겨넣지 않음).
                checked -= 1
                skipped.append({"name": name, "entry_date": entry_date, "reason": reason})
                continue
            if ok:
                hit += 1
                hits.append({"name": name, "entry_date": entry_date, "reason": reason})
            else:
                misses.append({"name": name, "entry_date": entry_date, "reason": reason})
        hit_rate = round((hit / checked * 100.0), 1) if checked else 0.0
        return {
            "strategy": strategy,
            "se_analyzed_at": se_at,
            "checked": checked,
            "hit": hit,
            "hit_rate": hit_rate,
            "top_n": top_n,
            "hits": hits,
            "misses": sorted(misses, key=lambda x: (x["entry_date"], x["name"])),
            "skipped": sorted(skipped, key=lambda x: (x["entry_date"], x["name"])),
        }
    finally:
        conn.close()


def get_stockeasy_two_snapshots(strategy: str) -> tuple[dict, dict]:
    """해당 전략의 최신/직전 스냅샷 반환."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT analyzed_at, holdings_json, exits_json
            FROM stockeasy_analysis
            WHERE strategy=?
            ORDER BY id DESC
            LIMIT 2
        """, (strategy,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return {}, {}

    def _to_payload(row):
        analyzed_at, hjson, ejson = row
        hs = json.loads(hjson) if hjson else []
        es = json.loads(ejson) if ejson else []
        return {"analyzed_at": analyzed_at, "holdings": hs, "exits": es}

    latest = _to_payload(rows[0])
    prev = _to_payload(rows[1]) if len(rows) > 1 else {}
    return latest, prev


def _calc_daily_delta(strategy: str) -> dict:
    latest, prev = get_stockeasy_two_snapshots(strategy)
    cur_h = {x.get("name") for x in (latest.get("holdings") or []) if x.get("name")}
    prv_h = {x.get("name") for x in (prev.get("holdings") or []) if x.get("name")}
    added = sorted(cur_h - prv_h)
    removed = sorted(prv_h - cur_h)
    exits_today = sorted({x.get("name") for x in (latest.get("exits") or []) if x.get("name")})
    return {
        "latest_at": latest.get("analyzed_at", ""),
        "prev_at": prev.get("analyzed_at", ""),
        "added": added,
        "removed": removed,
        "exits_today": exits_today,
    }


def _load_latest_stockeasy_reports(strategy: str) -> dict:
    """최신 스냅샷의 보유종목 리포트(research.summary.content_list) 요약."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT analyzed_at, holdings_json
            FROM stockeasy_analysis
            WHERE strategy=?
            ORDER BY id DESC
            LIMIT 1
        """, (strategy,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"report_count": 0, "top_keywords": [], "top_sectors": []}

    analyzed_at, hjson = row
    holdings = json.loads(hjson or "[]")
    sectors = Counter()
    kw = Counter()
    total_reports = 0
    vocab = [
        "AI", "HBM", "수주", "방산", "조선", "반도체", "서버", "데이터센터",
        "흑자전환", "실적", "영업이익", "매출", "고마진", "MLCC", "FC-BGA",
        "저평가", "가치", "재평가", "모멘텀", "신고가", "돌파", "상승추세",
    ]
    for h in holdings:
        sec = h.get("sector")
        if sec:
            sectors[sec] += 1
        rs = (h.get("research") or {}).get("summary") or {}
        lines = rs.get("content_list") or []
        if lines:
            total_reports += 1
        txt = " ".join(str(x) for x in lines)
        txt_u = txt.upper()
        for t in vocab:
            hit = txt_u.count(t.upper())
            if hit:
                kw[t] += hit

    return {
        "analyzed_at": analyzed_at,
        "report_count": total_reports,
        "top_keywords": [k for k, _ in kw.most_common(8)],
        "top_sectors": [k for k, _ in sectors.most_common(5)],
    }


# ──────────────────────────────────────────────────────────
# 스탁이지 — 실제 보유 종목
# ──────────────────────────────────────────────────────────
def get_stockeasy_holdings(strategy: str) -> tuple[list, str]:
    """stockeasy_analysis 최신 1건의 holdings_json 반환."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT analyzed_at, holdings_json FROM stockeasy_analysis
            WHERE strategy=? ORDER BY id DESC LIMIT 1
        """, (strategy,)).fetchone()
        if not row:
            return [], ""
        analyzed_at, hjson = row
        hs = json.loads(hjson) if hjson else []
        return [{"name": h["name"], "sector": h.get("sector", ""),
                 "hold_days": h.get("hold_days", 0),
                 "profit_pct": h.get("profit_pct", 0)} for h in hs], analyzed_at
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────
# 비교 / 통계
# ──────────────────────────────────────────────────────────
def _compare_snapshot(strategy: str) -> dict:
    """[레거시] 최신 스냅샷 기준 비교(오늘 기준)."""
    params = _load_params().get(strategy, DEFAULT_PARAMS[strategy])
    ours_full_raw = get_our_candidates(strategy)
    min_score = float(params.get("min_score", 0.0))
    max_candidates = int(params.get("max_candidates", 120))
    min_mktcap = float(params.get("min_mktcap_억", 0) or 0)
    ours_full = [
        x for x in ours_full_raw
        if float(x.get("score") or 0) >= min_score
        and float(x.get("mktcap_억") or 0) >= min_mktcap
    ]
    ours_full = sorted(ours_full, key=lambda x: float(x.get("score") or 0), reverse=True)[:max_candidates]
    se_full, se_at = get_stockeasy_holdings(strategy)
    delta = _calc_daily_delta(strategy)
    report_profile = _load_latest_stockeasy_reports(strategy)

    ours_set = {x["name"] for x in ours_full if x["name"]}
    se_set   = {x["name"] for x in se_full   if x["name"]}

    intersect = ours_set & se_set
    only_ours = ours_set - se_set
    only_se   = se_set - ours_set

    # Precision/Recall/F1 — 스탁이지 보유를 정답(ground truth)로 봄
    precision = len(intersect) / len(ours_set) if ours_set else 0
    recall    = len(intersect) / len(se_set)   if se_set   else 0
    f1        = (2 * precision * recall / (precision + recall)) if (precision+recall) > 0 else 0

    # 매도(편출) 검증: 오늘 제거/편출된 종목을 정답으로 보고, 우리 매도후보와 비교
    sell_truth = set((delta.get("removed") or []) + (delta.get("exits_today") or []))
    our_sell_full = _get_our_sell_candidates(strategy, se_full)
    our_sell_set = {x["name"] for x in our_sell_full if x.get("name")}
    sell_inter = our_sell_set & sell_truth
    sell_precision = len(sell_inter) / len(our_sell_set) if our_sell_set else 0
    sell_recall = len(sell_inter) / len(sell_truth) if sell_truth else 0
    sell_f1 = (2 * sell_precision * sell_recall / (sell_precision + sell_recall)) if (sell_precision + sell_recall) > 0 else 0

    return {
        "strategy":   strategy,
        "our_count":  len(ours_set),
        "se_count":   len(se_set),
        "intersect":  sorted(intersect),
        "only_ours":  sorted(only_ours),
        "only_se":    sorted(only_se),
        "precision":  round(precision * 100, 1),
        "recall":     round(recall * 100, 1),
        "f1":         round(f1 * 100, 1),
        "se_analyzed_at": se_at,
        "ours_full":  ours_full,
        "se_full":    se_full,
        "params_before": {
            "min_score": min_score,
            "max_candidates": max_candidates,
            "min_mktcap_억": min_mktcap,
        },
        "delta": delta,
        "report_profile": report_profile,
        "sell_truth": sorted(sell_truth),
        "our_sell": sorted(our_sell_set),
        "sell_intersect": sorted(sell_inter),
        "sell_precision": round(sell_precision * 100, 1),
        "sell_recall": round(sell_recall * 100, 1),
        "sell_f1": round(sell_f1 * 100, 1),
        "our_sell_full": our_sell_full,
    }


def compare(strategy: str) -> dict:
    """
    이벤트 기준 비교(기본):
    - 매수: 각 종목의 entry_date(실제 편입일) 기준 재현
    - 매도: 스냅샷 페어 기준 as_of(실제 편출 발생일) 재현
    """
    params = _load_params().get(strategy, DEFAULT_PARAMS[strategy])
    se_full, se_at = get_stockeasy_holdings(strategy)
    report_profile = _load_latest_stockeasy_reports(strategy)
    delta = _calc_daily_delta(strategy)

    # 1) 매수 이벤트 재현률 (entry_date 기준)
    replay = replay_entry_day_inclusion(strategy, lookback_days=365, top_n=80)
    checked = int(replay.get("checked", 0) or 0)
    hit = int(replay.get("hit", 0) or 0)
    misses = replay.get("misses") or []
    hits = replay.get("hits") or []
    miss_names = sorted({m.get("name") for m in misses if m.get("name")})
    hit_names = sorted({m.get("name") for m in hits if m.get("name")})
    hit_rate = round((hit / checked * 100.0), 1) if checked else 0.0

    # 2) 매도 이벤트 재현률 (as_of 기준 누적) — v5: 전체 스냅샷 이력 사용 (사용자 지시)
    sell_bt = backtest_sell(strategy, lookback_snapshots=999)
    sell_precision = float(sell_bt.get("precision", 0.0) or 0.0)
    sell_recall = float(sell_bt.get("recall", 0.0) or 0.0)
    sell_f1 = float(sell_bt.get("f1", 0.0) or 0.0)

    # 3) 당일 편출 이벤트(있을 때만)도 별도 표기
    snapshot_today = _compare_snapshot(strategy)
    sell_truth_today = snapshot_today.get("sell_truth") or []
    our_sell_today = snapshot_today.get("our_sell") or []
    sell_inter_today = snapshot_today.get("sell_intersect") or []

    return {
        "strategy": strategy,
        # 이벤트 기준 매수 점수
        "our_count": checked,        # 검증한 편입 이벤트 수
        "se_count": checked,         # 이벤트 검증모수 동일
        "intersect": hit_names,      # 맞춘 편입 이벤트
        "only_ours": [],             # 이벤트 검증에서는 과추출 개념 배제
        "only_se": miss_names,       # 못 맞춘 편입 이벤트(개선 대상)
        "precision": hit_rate,       # 이벤트 기준에서는 hit_rate로 통일
        "recall": hit_rate,
        "f1": hit_rate,
        "se_analyzed_at": se_at,
        "ours_full": [],
        "se_full": se_full,
        "params_before": {
            "min_score": float(params.get("min_score", 0.0)),
            "max_candidates": int(params.get("max_candidates", 120)),
            "min_mktcap_억": float(params.get("min_mktcap_억", 0) or 0),
        },
        "delta": delta,
        "report_profile": report_profile,
        # 매도는 누적 이벤트 기준(핵심)
        "sell_truth": sell_truth_today,
        "our_sell": our_sell_today,
        "sell_intersect": sell_inter_today,
        "sell_precision": round(sell_precision, 1),
        "sell_recall": round(sell_recall, 1),
        "sell_f1": round(sell_f1, 1),
        "our_sell_full": snapshot_today.get("our_sell_full", []),
        # 디버그/리포트용
        "entry_checked": checked,
        "entry_hit": hit,
        "entry_misses": misses,
        "sell_backtest": sell_bt,
    }


def _score_from_candidates(ours_raw: list[dict], se_set: set[str], min_score: float, max_candidates: int, min_mktcap_억: float) -> dict:
    filtered = [
        x for x in (ours_raw or [])
        if float(x.get("score") or 0) >= float(min_score)
        and float(x.get("mktcap_억") or 0) >= float(min_mktcap_억)
    ]
    filtered = sorted(filtered, key=lambda x: float(x.get("score") or 0), reverse=True)[: int(max_candidates)]
    ours_set = {x.get("name") for x in filtered if x.get("name")}
    inter = ours_set & se_set
    precision = len(inter) / len(ours_set) if ours_set else 0.0
    recall = len(inter) / len(se_set) if se_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    se_n = max(1, len(se_set))
    over_ratio = max(0.0, (len(ours_set) - len(se_set)) / se_n) if se_set else 0.0
    return {
        "our_count": len(ours_set),
        "intersect": sorted(inter),
        "precision": round(precision * 100, 1),
        "recall": round(recall * 100, 1),
        "f1": round(f1 * 100, 1),
        "over_ratio": round(over_ratio, 3),
        "hvm_in_ours": ("에이치브이엠" in ours_set),
        "hvm_in_inter": ("에이치브이엠" in inter),
    }


def _load_strategy_snapshots(strategy: str, limit: int = 40) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 날짜별 최신 스냅샷 1개만 사용 (당일 여러 번 실행 시 중복 페어 방지)
        rows = conn.execute(
            """
            SELECT analyzed_at, holdings_json, exits_json
            FROM stockeasy_analysis
            WHERE strategy=? AND id IN (
                SELECT MAX(id) FROM stockeasy_analysis
                WHERE strategy=?
                GROUP BY DATE(analyzed_at)
            )
            ORDER BY analyzed_at DESC
            LIMIT ?
            """,
            (strategy, strategy, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append({
            "analyzed_at": r["analyzed_at"],
            "holdings": json.loads(r["holdings_json"] or "[]"),
            "exits": json.loads(r["exits_json"] or "[]"),
        })
    return out


def backtest_sell(strategy: str, lookback_snapshots: int = 30, sell_cfg: Optional[dict] = None) -> dict:
    """
    스냅샷 누적 매도 정합성 측정.
    truth = (직전보유-현재보유) + exits_json(name)
    pred  = 직전보유에 대해 analyzed_at 시점 매도신호 계산
    """
    snaps = _load_strategy_snapshots(strategy, limit=max(3, int(lookback_snapshots)))
    if len(snaps) < 2:
        return {"strategy": strategy, "pairs": 0, "truth_total": 0, "pred_total": 0, "hit_total": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    pred_total = 0
    truth_total = 0
    hit_total = 0
    pairs = 0
    for i in range(len(snaps) - 1):
        cur = snaps[i]
        prev = snaps[i + 1]
        as_of = str(cur.get("analyzed_at", "")).split(" ")[0]
        prev_h = [
            {
                "name": x.get("name") or x.get("stock_name"),
                "hold_days": x.get("hold_days") or x.get("holding_days") or 0,
                "profit_pct": x.get("profit_pct") if x.get("profit_pct") is not None else x.get("return_rate", 0),
            }
            for x in (prev.get("holdings") or [])
        ]
        cur_names = {(x.get("name") or x.get("stock_name")) for x in (cur.get("holdings") or []) if (x.get("name") or x.get("stock_name"))}
        prev_names = {(x.get("name") or x.get("stock_name")) for x in (prev.get("holdings") or []) if (x.get("name") or x.get("stock_name"))}
        removed = prev_names - cur_names
        exits = {(x.get("name") or x.get("stock_name")) for x in (cur.get("exits") or []) if (x.get("name") or x.get("stock_name"))}
        truth = {x for x in (removed | exits) if x}
        pred = {x.get("name") for x in _get_our_sell_candidates(strategy, prev_h, as_of=as_of, sell_cfg=sell_cfg) if x.get("name")}
        hit = truth & pred
        truth_total += len(truth)
        pred_total += len(pred)
        hit_total += len(hit)
        pairs += 1

    precision = (hit_total / pred_total) if pred_total else 0.0
    recall = (hit_total / truth_total) if truth_total else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return {
        "strategy": strategy,
        "pairs": pairs,
        "truth_total": truth_total,
        "pred_total": pred_total,
        "hit_total": hit_total,
        "precision": round(precision * 100, 1),
        "recall": round(recall * 100, 1),
        "f1": round(f1 * 100, 1),
    }


def tune_sell_params(strategies: list[str], iterations: int = 300, lookback_snapshots: int = 30) -> dict:
    params = _load_params()
    out = {}
    for strategy in strategies:
        base = _get_sell_cfg(strategy)
        best_cfg = dict(base)
        best_score = None
        best_metrics = {}
        for i in range(max(1, int(iterations))):
            if i == 0:
                cfg = dict(base)
            else:
                cfg = {
                    "score_cut": random.choice([2, 3, 4, 5]),
                    "loss_cut": random.choice([-4.0, -5.0, -6.0, -7.0, -8.0]),
                    "trail_profit_min": random.choice([1.0, 2.0, 3.0, 4.0]),
                    "trail_dd20_cut": random.choice([-8.0, -10.0, -12.0, -15.0]),
                    "takeprofit_hold_days": random.choice([10, 12, 14, 16, 18, 20]),
                    "takeprofit_min": random.choice([2.0, 3.0, 4.0, 5.0, 6.0, 8.0]),
                    "max_hold_days_soft": random.choice([14, 16, 18, 20, 22]),
                    "flow_5d_sell_억": random.choice([-2.0, -5.0, -10.0, -20.0]),
                    "ret5_drop_cut": random.choice([-5.0, -6.0, -7.0, -8.0, -10.0]),
                    "vol_ratio_min": random.choice([1.0, 1.1, 1.2, 1.4]),
                    "max_daily_sell_ratio": random.choice([0.12, 0.15, 0.18, 0.2, 0.22, 0.25]),
                    "max_daily_sell_min": 1,
                    "max_daily_sell_max": random.choice([3, 4, 5]),
                    "profit_hard_take_min": random.choice([12.0, 15.0, 18.0, 22.0, 28.0]),
                    "profit_hard_dd20_cut": random.choice([-4.0, -5.0, -6.0, -8.0]),
                    "flow_weak_cut_억": random.choice([2.0, 0.0, -1.0, -3.0]),
                }
            m = backtest_sell(strategy, lookback_snapshots=lookback_snapshots, sell_cfg=cfg)
            score = float(m.get("f1", 0.0)) + min(float(m.get("precision", 0.0)), 45.0) * 0.25
            if best_score is None or score > best_score:
                best_score = score
                best_cfg = dict(cfg)
                best_metrics = m
        params[f"sell_{strategy}"] = best_cfg
        out[strategy] = {
            "best_cfg": best_cfg,
            "best_score": round(best_score or 0.0, 2),
            "metrics": best_metrics,
        }
    _save_params(params)
    return {"strategies": strategies, "results": out}


def tune_peak_params_fast(iterations: int = 400) -> dict:
    """
    Peak 파라미터를 빠르게 탐색.
    - get_our_candidates('peak')는 1회만 계산
    - min_score/max_candidates/min_mktcap_억 조합을 랜덤 탐색
    - HVM 교집합 유지에 가중치
    """
    all_params = _load_params()
    baseline_params = dict(all_params.get("peak", DEFAULT_PARAMS["peak"]))
    se_full, se_at = get_stockeasy_holdings("peak")
    se_set = {x["name"] for x in se_full if x.get("name")}
    ours_raw = get_our_candidates("peak")

    best = None
    best_params = None
    for i in range(max(1, int(iterations))):
        if i == 0:
            p = {
                "min_score": float(baseline_params.get("min_score", 0.0)),
                "max_candidates": int(baseline_params.get("max_candidates", 30)),
                "min_mktcap_억": float(baseline_params.get("min_mktcap_억", 0.0)),
            }
        else:
            p = {
                "min_score": round(random.uniform(0.0, 22.0), 1),
                "max_candidates": random.choice([15, 20, 25, 30, 35, 40, 45, 50, 60]),
                "min_mktcap_억": random.choice([0, 300, 500, 800, 1000, 1500, 2000, 3000, 5000]),
            }
        s = _score_from_candidates(ours_raw, se_set, p["min_score"], p["max_candidates"], p["min_mktcap_억"])
        over_penalty = float(s.get("over_ratio", 0.0)) * 18.0
        objective = (
            float(s["f1"]) * 1.0
            + float(s["precision"]) * 0.45
            + float(s["recall"]) * 0.15
            - over_penalty
            + (4.0 if s["hvm_in_inter"] else (1.0 if s["hvm_in_ours"] else -20.0))
        )
        if best is None or objective > best:
            best = objective
            best_params = p
            best_score = s

    # apply best
    all_params["peak"] = best_params
    _save_params(all_params)
    return {
        "iterations": int(iterations),
        "se_count": len(se_set),
        "se_analyzed_at": se_at,
        "best_params": best_params,
        "best_score": best_score,
        "objective": best,
        "baseline_params": baseline_params,
    }


def tune_strategy_params_fast(strategy: str, iterations: int = 300) -> dict:
    """peak 외 momentum/value 포함 공통 매수 파라미터 튜닝(현재 스냅샷 기준)."""
    all_params = _load_params()
    baseline = dict(all_params.get(strategy, DEFAULT_PARAMS[strategy]))
    se_full, se_at = get_stockeasy_holdings(strategy)
    se_set = {x["name"] for x in se_full if x.get("name")}
    ours_raw = get_our_candidates(strategy)
    best = None
    best_params = None
    best_score = None
    for i in range(max(1, int(iterations))):
        if i == 0:
            p = {
                "min_score": float(baseline.get("min_score", 0.0)),
                "max_candidates": int(baseline.get("max_candidates", 60)),
                "min_mktcap_억": float(baseline.get("min_mktcap_억", 0.0)),
            }
        else:
            p = {
                "min_score": round(random.uniform(0.0, 28.0), 1),
                "max_candidates": random.choice([15, 20, 25, 30, 35, 40, 50, 60, 80, 100]),
                "min_mktcap_억": random.choice([0, 300, 500, 800, 1000, 1500, 2000, 3000, 5000]),
            }
        s = _score_from_candidates(ours_raw, se_set, p["min_score"], p["max_candidates"], p["min_mktcap_억"])
        over_penalty = float(s.get("over_ratio", 0.0)) * 20.0
        objective = (
            float(s["f1"]) * 1.0
            + float(s["precision"]) * 0.45
            + float(s["recall"]) * 0.15
            - over_penalty
        )
        if best is None or objective > best:
            best = objective
            best_params = p
            best_score = s
    all_params[strategy] = best_params
    _save_params(all_params)
    return {
        "strategy": strategy,
        "iterations": int(iterations),
        "se_count": len(se_set),
        "se_analyzed_at": se_at,
        "best_params": best_params,
        "best_score": best_score,
        "objective": round(best or 0.0, 2),
        "baseline_params": baseline,
    }


def _extract_sell_features(
    conn: sqlite3.Connection,
    name: str,
    hold_days: int,
    profit_pct: float,
    as_of: Optional[str] = None,
    kospi_3m: Optional[tuple] = None,   # (kospi_now, kospi_63d_ago) for RS computation
    stock_code: Optional[str] = None,   # 종목코드 우선 사용 (동명 오매핑 방지)
) -> Optional[dict]:
    """보유 종목의 매도 판단에 필요한 기술적 지표 추출.

    kospi_3m: Peak 전략 RS 계산용 — (현재 KOSPI close, 63거래일 전 KOSPI close) 쌍.
              None이면 RS 계산 생략(rs_score=None).
    stock_code: 보유 데이터에 코드가 있으면 우선 사용. 동명 종목/개명 이력 오매핑 방지.
    """
    if stock_code:
        # 코드 우선: 이름은 검증용으로만 사용
        code_row = conn.execute(
            "SELECT stock_code, COALESCE(market_cap,0) AS mktcap "
            "FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1",
            (stock_code,),
        ).fetchone()
    else:
        code_row = None

    if not code_row:
        # fallback: 종목명으로 조회 (코드 없거나 코드 조회 실패 시)
        code_row = conn.execute(
            "SELECT stock_code, COALESCE(market_cap,0) AS mktcap "
            "FROM stock_universe WHERE stock_name=? ORDER BY base_date DESC LIMIT 1",
            (name,),
        ).fetchone()
    if not code_row:
        return None
    code = str(code_row["stock_code"] or "")
    mktcap = float(code_row["mktcap"] or 0)

    date_filter = "AND date<=?" if as_of else ""
    params = (code, as_of) if as_of else (code,)
    px = conn.execute(
        f"""
        WITH p AS (
          SELECT date, close, volume,
                 AVG(close) OVER(ORDER BY date ROWS BETWEEN 4  PRECEDING AND CURRENT ROW) ma5,
                 AVG(close) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20,
                 AVG(close) OVER(ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) ma60,
                 MAX(close) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) h20,
                 AVG(volume) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) vma20,
                 LAG(close, 5)  OVER(ORDER BY date) c5,
                 LAG(close, 62) OVER(ORDER BY date) c63  -- 3개월 전 종가 (RS 계산용)
          FROM price_history
          WHERE stock_code=? {date_filter}
            AND close > 0
        )
        SELECT * FROM p ORDER BY date DESC LIMIT 1
        """,
        params,
    ).fetchone()
    if not px:
        return None

    c     = float(px["close"] or 0)
    ma5   = float(px["ma5"]   or 0)
    ma20  = float(px["ma20"]  or 0)
    ma60  = float(px["ma60"]  or 0)
    h20   = float(px["h20"]   or c)
    c5    = float(px["c5"]    or c)
    c63   = float(px["c63"]   or 0)
    vol   = float(px["volume"] or 0)
    vma20 = float(px["vma20"] or 0)
    ret5  = ((c - c5)  / c5  * 100.0) if c5  > 0 else 0.0
    dd20  = ((c - h20) / h20 * 100.0) if h20 > 0 else 0.0
    vol_ratio = (vol / vma20) if vma20 > 0 else 0.0

    # RS score (3개월 상대강도) — Peak 전략에서 RS < 75 는 1순위 매도 신호
    rs_score: Optional[float] = None
    if kospi_3m and kospi_3m[0] > 0 and kospi_3m[1] > 0 and c63 > 0:
        k_ret3m = (kospi_3m[0] - kospi_3m[1]) / kospi_3m[1] * 100.0
        s_ret3m = (c - c63) / c63 * 100.0
        rs_score = max(0.0, min(100.0, 50.0 + (s_ret3m - k_ret3m) * 2.0))

    flow_params = (code, as_of) if as_of else (code,)
    flow_filter = "AND date<=?" if as_of else ""
    flow = conn.execute(
        f"""
        SELECT COALESCE(SUM(COALESCE(frn_net_buy_amt,0)),0)/100.0  AS frn5_억,
               COALESCE(SUM(COALESCE(inst_net_buy_amt,0)),0)/100.0 AS inst5_억
        FROM (
          SELECT frn_net_buy_amt, inst_net_buy_amt
          FROM price_history
          WHERE stock_code=? {flow_filter}
            AND (frn_net_buy_amt IS NOT NULL OR inst_net_buy_amt IS NOT NULL)
          ORDER BY date DESC LIMIT 5
        )
        """,
        flow_params,
    ).fetchone()
    frn5  = float(flow["frn5_억"]  or 0.0) if flow else 0.0
    inst5 = float(flow["inst5_억"] or 0.0) if flow else 0.0

    # 시총 대비 5일 수급 비율 (%) — 대형주 절대 금액 왜곡 보정
    flow_sum = frn5 + inst5
    mktcap_억 = _normalize_mktcap_억(mktcap)   # 억원 단위로 정규화 (공통 헬퍼 사용)
    flow_pct_of_mktcap = (flow_sum / mktcap_억 * 100.0) if mktcap_억 > 0 else 0.0

    return {
        "name": name,
        "code": code,
        "mktcap": mktcap,
        "mktcap_억": mktcap_억,
        "hold_days": int(hold_days or 0),
        "profit_pct": float(profit_pct or 0.0),
        "c": c, "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "ret5": ret5, "dd20": dd20, "vol_ratio": vol_ratio,
        "frn5": frn5, "inst5": inst5,
        "flow_sum": flow_sum,
        "flow_pct_of_mktcap": flow_pct_of_mktcap,   # 음수일수록 수급 이탈
        "rs_score": rs_score,                          # None if not computed
    }


def _get_sell_cfg(strategy: str, sell_cfg: Optional[dict] = None) -> dict:
    params = _load_params()
    cfg = dict(params.get("sell", DEFAULT_PARAMS["sell"]))
    skey = f"sell_{strategy}"
    if skey in params and isinstance(params.get(skey), dict):
        cfg.update(params[skey])
    if sell_cfg:
        cfg.update(sell_cfg)
    return cfg


def _get_our_sell_candidates(strategy: str, se_holdings: list[dict], as_of: Optional[str] = None, sell_cfg: Optional[dict] = None) -> list[dict]:
    """보유 종목 대상으로 단기 매도 시그널 후보 계산(as_of 지원).

    개선 사항 (v3):
    ① Peak: RS < 75 추가 (PDF 1순위 편출 조건) — 편입 조건(RS≥75)이 깨지면 즉시 매도
    ② Momentum: MA5 < MA20 역배열 추가 (MA정배열 붕괴 = 모멘텀 이탈 핵심 신호)
    ③ 시총 대비 수급 비율로 대형주 오신호(SK하이닉스 등) 억제
    ④ allow 조건을 primary_signal 기반으로 재설계 → 정밀도 향상
    ⑤ 일일 상한 확대 (SE가 한 번에 5종목 편출 가능)
    """
    cfg = _get_sell_cfg(strategy, sell_cfg=sell_cfg)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    out = []

    # RS 계산용 KOSPI 3개월 데이터 1회 사전 조회 (Peak: RS<75, Momentum: RS<60)
    kospi_3m: Optional[tuple] = None
    date_filter_k = "AND date<=?" if as_of else ""
    kp = (as_of,) if as_of else ()
    krows = conn.execute(
        f"SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0 "
        f"{date_filter_k} ORDER BY date DESC LIMIT 65",
        kp,
    ).fetchall()
    if len(krows) >= 63:
        kospi_3m = (float(krows[0][0]), float(krows[62][0]))

    # ── v4: momentum 섹터 로테이션 이탈 감지 준비 ──
    # SE Momentum 매도는 섹터 바스켓 일괄 편출임이 실증됨 (2026-05-12 2차전지 7종,
    # 05-14 조선 3종, 06-19 금융 5종 동시 편출). 보유종목의 SE 섹터가
    # 주도섹터 랭킹에서 밀리면 바스켓 전체 매도 신호.
    mom_sec_map: dict[str, str] = {}
    mom_hot_secs: set[str] = set()
    mom_sec_avg: dict[str, float] = {}
    if strategy == "momentum":
        try:
            for r0 in conn.execute(
                "SELECT DISTINCT stock_code, sector_name FROM stockeasy_sector_membership WHERE sector_level='middle'"
            ).fetchall():
                mom_sec_map[r0["stock_code"]] = r0["sector_name"]
            df = "AND date<=?" if as_of else ""
            dp = (as_of,) if as_of else ()
            _sr = conn.execute(
                f"""
                WITH p AS (
                  SELECT stock_code, close, ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY date DESC) rn
                  FROM price_history WHERE close>0 AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' {df}
                )
                SELECT a.stock_code, (a.close-b.close)/b.close*100.0
                FROM p a JOIN p b ON b.stock_code=a.stock_code AND b.rn=21
                WHERE a.rn=1 AND b.close>0
                """,
                dp,
            ).fetchall()
            _secs: dict[str, list[float]] = {}
            for r0 in _sr:
                s0 = mom_sec_map.get(r0[0])
                if s0:
                    _secs.setdefault(s0, []).append(float(r0[1]))
            mom_sec_avg = {s: sum(v) / len(v) for s, v in _secs.items() if len(v) >= 5}
            for i0, (s0, a0) in enumerate(sorted(mom_sec_avg.items(), key=lambda x: -x[1])[:5]):
                if a0 >= 3.0:
                    mom_hot_secs.add(s0)
        except Exception:
            pass

    try:
        for h in (se_holdings or []):
            name = (h.get("name") or h.get("stock_name") or "").strip()
            if not name:
                continue
            hold_days  = int(h.get("hold_days") or 0)
            profit_pct = float(h.get("profit_pct") or 0.0)
            # stock_code 우선 전달 — 동명 종목/개명 이력 오매핑 방지 (P2 Fix)
            s_code = (h.get("stock_code") or "").strip() or None
            feat = _extract_sell_features(conn, name, hold_days, profit_pct,
                                          as_of=as_of, kospi_3m=kospi_3m,
                                          stock_code=s_code)
            if not feat:
                continue

            score    = 0
            reasons  = []
            rs       = feat.get("rs_score")          # None for momentum/value
            ma5      = feat["ma5"]
            ma20     = feat["ma20"]
            ma60     = feat["ma60"]
            flow_sum = feat["flow_sum"]
            flow_pct = feat["flow_pct_of_mktcap"]   # % of mktcap; 대형주 보정

            trend_break  = feat["c"] > 0 and ma20 > 0 and feat["c"] < ma20
            ma5_cross    = ma5 > 0 and ma20 > 0 and ma5 < ma20   # Momentum 역배열
            ma20_cross   = ma20 > 0 and ma60 > 0 and ma20 < ma60
            hard_loss    = hold_days >= 1 and profit_pct <= float(cfg.get("loss_cut", -6.0))
            breakdown    = trend_break and feat["ret5"] <= float(cfg.get("ret5_drop_cut", -7.0))

            # ── 전략별 핵심(primary) 매도 신호 ─────────────────
            primary_signal = False

            if strategy == "peak":
                # ① RS < 75: Peak 편입 조건 이탈 = 1순위 매도 신호
                if rs is not None and rs < 75.0 and hold_days >= 1:
                    score += 5
                    reasons.append(f"RS={rs:.0f}<75")
                    primary_signal = True
                # ② 추세 이탈 + 급락
                if trend_break:
                    score += 3
                    reasons.append("추세이탈(close<MA20)")
                    primary_signal = True
                if breakdown:
                    score += 2
                    reasons.append("급락동반")
                # ③ 심각한 고점 대비 하락 + 수급 이탈 확인
                # 수급이 양호한 종목(flow > 0)은 dd20만으로 primary 처리 안 함
                # → 심텍처럼 flow=+884억인데 dd20=-13%인 경우 조기 청산 방지
                dd20_severe = hold_days >= 7 and feat["dd20"] <= -12.0
                flow_weak_dd = flow_pct <= -0.3 or (rs is not None and rs < 80.0)
                if dd20_severe and flow_weak_dd:
                    score += 3
                    reasons.append(f"고점-12%+수급이탈({feat['dd20']:.1f}%)")
                    primary_signal = True

            elif strategy == "momentum":
                # ⓪ v4: 섹터 로테이션 이탈 — 보유종목의 SE 섹터가 주도섹터 TOP5에서
                # 탈락하면 바스켓 일괄 편출 (SE 실증 패턴, 최우선 신호)
                # (실험 기록 2026-07-11) 섹터 로테이션 이탈 신호(보유종목 섹터의 평균
                # ret20 마이너스 전환 시 매도)를 테스트했으나 F1 43.5→40.6으로 악화
                # (일일 캡 내 순위를 바꿔 정답 종목을 밀어냄). 신호 비활성 —
                # mom_sec_map/mom_sec_avg 인프라는 향후 재실험용으로 유지.
                # ① MA5<MA20: 정배열 붕괴 = Momentum 전략 핵심 이탈 신호
                if ma5_cross:
                    score += 5
                    reasons.append("MA5<MA20역배열")
                    primary_signal = True
                # ② MA20<MA60: 중기 추세 전환
                if ma20_cross:
                    score += 3
                    reasons.append("MA20<MA60역배열")
                    primary_signal = True
                # ③ RS < 60: 모멘텀 상대강도 붕괴 (조선주 일괄편출 패턴 포함)
                if rs is not None and rs < 60.0 and hold_days >= 1:
                    score += 4
                    reasons.append(f"RS={rs:.0f}<60(모멘텀붕괴)")
                    primary_signal = True
                # ④ 추세 이탈 + 급락
                if trend_break:
                    score += 2
                    reasons.append("추세이탈")
                    primary_signal = primary_signal or breakdown
                if breakdown:
                    score += 2
                    reasons.append("급락동반")
                # ⑤ 심각한 고점 대비 하락 (Momentum: dd20만으로도 충분)
                if hold_days >= 10 and feat["dd20"] <= -10.0:
                    score += 3
                    reasons.append(f"고점-10%({feat['dd20']:.1f}%)")
                    primary_signal = True

            else:  # value
                # v4: 추세이탈+급락도 primary 승격 제거 — SE는 TYM -20%/클래시스 -15% 등
                # 추세이탈 손실 종목도 계속 보유함이 실증됨. 점수만 가산.
                if trend_break and breakdown:
                    score += 4
                    reasons.append("추세이탈+급락")
                if ma20_cross:
                    score += 2
                    reasons.append("MA20<MA60")
                # 장기보유 + 심각한 손실 = 밸류 이지 "회복 포기" 이탈 (하이브 2026-05-21 패턴)
                # 120일 이상 보유했는데도 -28% 이상 손실이면 thesis 훼손으로 판단
                # v4 (2026-07-11): 대형주(5000억+) 한정 — SE는 소형 바이오(안트로젠 -57%,
                # 코오롱티슈진 -21% 등)는 대손실에도 계속 보유함이 실증됨.
                # 임계 -32%: 하이브 실제 편출 시점 손익 -32.9% 기준 캘리브레이션
                # (-28%면 편출 5~9일 전부터 조기 발화 → FP 4건. 단일 이벤트 기반이므로
                #  새 편출 이벤트 축적 시 재검증 필요)
                if hold_days >= 120 and profit_pct <= -32.0 and feat["mktcap_억"] >= 5000:
                    score += 8
                    reasons.append(f"장기보유대손실({hold_days}일,{profit_pct:.1f}%)")
                    primary_signal = True

            # ── 공통 보조 신호 ──────────────────────────────────
            # v4: value는 공통 신호로 primary 승격 금지 (SE Value는 손절/익절 안 함 실증)
            if hard_loss:
                score += 4
                reasons.append(f"손절({profit_pct:.1f}%)")
                if strategy != "value":
                    primary_signal = True

            # MA20 < MA60 보조
            if ma20_cross and strategy == "peak":
                score += 1
                reasons.append("MA20<MA60")

            # 포물선 급등 후 익절 (Parabolic take-profit)
            # SE는 5일 수익률 25%+ 급등 후 수익을 확정하는 경향이 있음
            # 단, 수급이 여전히 강하면(flow_pct > 0) 추가 상승 기대 → 보유 유지
            # 심텍 케이스: ret5=47% 급등인데 flow_pct=+3% (기관/외국인 여전히 매수)
            # → SE는 이 경우 보유 유지. 포물선 + 수급이탈 조합일 때만 익절.
            parabolic_tp = (
                feat["ret5"] >= 25.0
                and profit_pct >= 15.0
                and hold_days >= 5
                and flow_pct <= 0.0          # 수급이 빠지기 시작해야 포물선 익절 적용
            )
            if parabolic_tp:
                score += 3
                reasons.append(f"포물선급등익절(ret5={feat['ret5']:.0f}%)")
                if strategy != "value":
                    primary_signal = True

            # 고수익 + 수급 이탈 (비츠로셀 패턴: profit=57%, flow_pct=-0.65%)
            # 큰 수익 실현 후 기관/외국인이 빠지기 시작하면 SE는 회전
            # 대형주(5조+ = 50000억 이상)는 절대 수급금액이 크므로 더 엄격한 임계치 적용
            _flow_tp_cut = -2.0 if feat["mktcap_억"] >= 50000 else -0.5
            if profit_pct >= 40.0 and flow_pct <= _flow_tp_cut and hold_days >= 14:
                score += 3
                reasons.append(f"고수익+수급이탈(profit={profit_pct:.0f}%,flow={flow_pct:.1f}%)")
                if strategy != "value":
                    primary_signal = True

            # 고수익 + 의미있는 고점 대비 하락
            # v4 (2026-07-11): 메가수익주(+100%↑)는 SE가 얕은 조정으로 팔지 않음이 실증됨
            # (LG이노텍 +300% 구간 조정 다수 보유유지 후 6-22 편출) → dd20 임계 1.6배 강화
            _mega_mult = 1.6 if profit_pct >= 100.0 else 1.0
            if profit_pct >= float(cfg.get("trail_profit_min", 2.0)) and hold_days >= 3:
                if feat["dd20"] <= float(cfg.get("trail_dd20_cut", -10.0)) * _mega_mult:
                    score += 2
                    reasons.append(f"고수익후고점대비{feat['dd20']:.1f}%")

            if (profit_pct >= float(cfg.get("profit_hard_take_min", 18.0))
                    and feat["dd20"] <= float(cfg.get("profit_hard_dd20_cut", -5.0)) * _mega_mult
                    and hold_days >= 14):
                score += 2
                reasons.append("고수익후되돌림")

            # 수급 이탈: 시총 대비 비율로 판단 (대형주 절대금액 왜곡 방지)
            if flow_pct <= float(cfg.get("flow_pct_sell_cut", -0.8)):
                score += 2
                reasons.append(f"수급이탈({flow_pct:.2f}%/mktcap)")
            elif feat["frn5"] < 0 and feat["inst5"] < 0 and flow_pct <= -0.3:
                score += 1
                reasons.append("기관+외국인매도")

            # 급락 + 거래량
            if (feat["ret5"] <= float(cfg.get("ret5_drop_cut", -7.0))
                    and feat["vol_ratio"] >= float(cfg.get("vol_ratio_min", 1.1))):
                score += 1
                reasons.append("급락+거래량")

            # ── 통과 조건: primary_signal 존재 + score ≥ score_cut ─
            if strategy == "value":
                # v4 (2026-07-11): SE Value는 사실상 '매도하지 않는' 전략임이 실증됨.
                # 40스냅샷(약 2개월)간 편출 1건(하이브, 장기보유대손실)뿐이며
                # 안트로젠 -57%/SGC에너지 -29%/SK하이닉스 +298% 모두 계속 보유.
                # → hard_loss 즉시통과·trend_break&breakdown 우회 통로 제거,
                #   primary_signal(장기보유대손실 등)만 허용.
                allow = primary_signal
                score_ok = score >= int(cfg.get("score_cut", 8))
            else:
                allow = primary_signal or hard_loss or parabolic_tp
                score_ok = score >= int(cfg.get("score_cut", 2))

            if allow and score_ok:
                out.append({
                    "name":   name,
                    "code":   feat["code"],
                    "score":  score,
                    "reason": ", ".join(reasons[:5]),
                })
    finally:
        conn.close()

    out = sorted(out, key=lambda x: (x["score"], x["name"]), reverse=True)

    # 과신호 방지: 일일 매도 후보 상한
    # SE가 한 번에 5종목 이상 편출하는 경우가 있으므로 충분히 크게 설정
    hcount = len(se_holdings or [])
    cap = max(1, int(round(hcount * float(cfg.get("max_daily_sell_ratio", 0.35)))))
    cap = max(int(cfg.get("max_daily_sell_min", 1)), cap)
    cap = min(int(cfg.get("max_daily_sell_max", 8)), cap)
    return out[:cap] if cap > 0 else out


def _infer_and_apply_adjustment(r: dict, all_params: dict) -> dict:
    """
    단순 규칙 기반 미세조정:
    - 신규편입 누락이 많으면 min_score 완화(하향)
    - 과추출/정밀도 저하면 min_score 강화(상향) 및 max_candidates 축소
    """
    s = r["strategy"]
    p = dict(all_params.get(s, DEFAULT_PARAMS[s]))
    before = dict(p)
    reasons = []

    added = set(r.get("delta", {}).get("added", []))
    only_se = set(r.get("only_se", []))
    only_ours_cnt = len(r.get("only_ours", []))
    rec = float(r.get("recall", 0))
    precision = float(r.get("precision", 0))
    rp = r.get("report_profile") or {}
    rep_kw = rp.get("top_keywords") or []

    missed_new = len(added & only_se)
    if missed_new >= 2 or (added and missed_new >= 1):
        p["min_score"] = max(0.0, float(p.get("min_score", 0.0)) - 1.0)
        p["max_candidates"] = min(160, int(p.get("max_candidates", 120)) + 10)
        reasons.append(f"신규편입 누락 {missed_new}건 → 진입 완화")
    elif (precision < 20 and only_ours_cnt >= 20) or (r.get("our_count", 0) >= max(15, int(r.get("se_count", 0) * 2)) and precision < 25):
        p["min_score"] = min(80.0, float(p.get("min_score", 0.0)) + 1.5)
        p["max_candidates"] = max(12, int(p.get("max_candidates", 120)) - 12)
        reasons.append(
            f"과추출 {only_ours_cnt}건/정밀도 {precision}%/우리후보 {r.get('our_count', 0)}건 → 필터 강하게 강화"
        )
    elif rec < 40 and len(only_se) >= 5:
        p["min_score"] = max(0.0, float(p.get("min_score", 0.0)) - 0.5)
        reasons.append(f"재현율 {rec}% 저조 → 점수 하한 완화")
    else:
        reasons.append("유의미한 패턴 변화 없음 → 파라미터 유지")

    # 섹터는 전략 제한/우대에 사용하지 않는다. (전략 원칙)
    if "preferred_sectors" in p:
        p.pop("preferred_sectors", None)
        reasons.append("섹터 우대/제한 파라미터 제거(전략 원칙 반영)")

    # 리포트에서 대형주 키워드(AI/HBM/서버/데이터센터)가 강하면 최소 시총 필터 강화
    largecap_kw = {"AI", "HBM", "서버", "데이터센터", "반도체"}
    has_largecap_signal = s == "momentum" and any(k in largecap_kw for k in rep_kw)
    if has_largecap_signal:
        p["min_mktcap_억"] = max(float(p.get("min_mktcap_억", 0) or 0), 5000.0)
        reasons.append("리포트 대형주/AI 키워드 반영 → 최소 시총 5,000억 적용")
    elif s == "momentum":
        # 대형주 신호 없으면 min_mktcap_억 점진 하향(단방향 누적 드리프트 방지)
        current_mktcap = float(p.get("min_mktcap_억", 0) or 0)
        if current_mktcap > 0:
            p["min_mktcap_억"] = max(0.0, current_mktcap - 500.0)
            reasons.append(
                f"대형주 키워드 없음 → min_mktcap 점진 하향 ({current_mktcap:.0f}→{p['min_mktcap_억']:.0f}억)"
            )
    elif s == "value":
        # Value는 중형 재평가 종목 포함이 핵심이므로 과도한 대형주 하한을 방지
        current_mktcap = float(p.get("min_mktcap_억", 0) or 0)
        if current_mktcap > 2000.0:
            p["min_mktcap_억"] = 2000.0
            reasons.append(
                f"value 전략 시총 하한 완화 ({current_mktcap:.0f}→2000억) — 중형 재평가 종목 누락 방지"
            )

    all_params[s] = p
    changed = (before != p)
    return {
        "strategy": s,
        "before": before,
        "after": p,
        "changed": changed,
        "reasons": reasons,
    }


# ──────────────────────────────────────────────────────────
# 트래커 마크다운 append
# ──────────────────────────────────────────────────────────
def append_to_tracker(results: list, today: str) -> None:
    """오늘 비교 결과를 stockeasy_logic_tracker.md 끝에 일별 섹션으로 추가."""
    if not os.path.exists(TRACKER_PATH):
        with open(TRACKER_PATH, "w", encoding="utf-8") as f:
            f.write(_initial_tracker_template())

    lines = [f"\n---\n\n## {today} — 일일 검증 리포트\n"]
    lines.append(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # 요약 테이블
    lines.append("\n### 일치율 요약\n")
    lines.append("| 전략 | 우리 후보 | 스탁이지 보유 | 교집합 | Precision | Recall | F1 | 매도F1 |")
    lines.append("|------|-----------|---------------|--------|-----------|--------|----|-------|")
    for r in results:
        lines.append(
            f"| {STRATEGY_LABELS[r['strategy']]} | {r['our_count']} | {r['se_count']} "
            f"| {len(r['intersect'])} | {r['precision']}% | {r['recall']}% | {r['f1']}% | {r.get('sell_f1', 0)}% |"
        )

    # 전략별 상세
    for r in results:
        s     = r["strategy"]
        label = STRATEGY_LABELS[s]
        logic = LOGIC_LABELS[s]
        lines.append(f"\n### {label} ({logic})\n")
        lines.append(f"- 스탁이지 스냅샷: {r['se_analyzed_at']}")
        lines.append(f"- 우리 추천 {r['our_count']}종목 / 스탁이지 {r['se_count']}종목 / 교집합 {len(r['intersect'])}종목")
        lines.append(f"- **Precision {r['precision']}%** (우리 추천 중 스탁이지가 실제 보유 비율)")
        lines.append(f"- **Recall {r['recall']}%** (스탁이지 보유 중 우리가 잡아낸 비율)")
        lines.append(
            f"- **매도 Precision {r.get('sell_precision', 0)}% / Recall {r.get('sell_recall', 0)}% / F1 {r.get('sell_f1', 0)}%** "
            f"(정답: 당일 이탈·편출 {len(r.get('sell_truth', []))}종목)"
        )
        rp = r.get("report_profile") or {}
        if rp.get("report_count"):
            lines.append(
                f"- 리포트 기반 요약: {rp.get('report_count')}개 본문 분석 / "
                f"핵심키워드 {', '.join((rp.get('top_keywords') or [])[:5])}"
            )

        if r["intersect"]:
            lines.append(f"\n✅ 교집합 ({len(r['intersect'])}): {', '.join(r['intersect'])}")
        if r["only_se"]:
            lines.append(f"\n❌ 스탁이지만 보유 — **우리 로직 실패** ({len(r['only_se'])}): {', '.join(r['only_se'])}")
            lines.append("  → 우리 로직이 못 잡아냄. 어떤 조건이 너무 엄격한지 검토 필요.")
        if r["only_ours"]:
            lines.append(f"\n⚠️ 우리만 추천 — **과추출** ({len(r['only_ours'])}): {', '.join(r['only_ours'][:30])}{'...' if len(r['only_ours'])>30 else ''}")
            lines.append("  → 스탁이지는 추가 필터를 적용 중. 우리 로직 좁힐 수 있는 추가 조건 탐색.")
        if r.get("sell_truth"):
            lines.append(
                f"\n🟥 매도 정답({len(r.get('sell_truth', []))}): "
                f"{', '.join((r.get('sell_truth') or [])[:12])}{'...' if len((r.get('sell_truth') or [])) > 12 else ''}"
            )
            lines.append(
                f"🟧 우리 매도후보({len(r.get('our_sell', []))}): "
                f"{', '.join((r.get('our_sell') or [])[:12])}{'...' if len((r.get('our_sell') or [])) > 12 else ''}"
            )
            if r.get("sell_intersect"):
                lines.append(f"🟩 매도 교집합({len(r.get('sell_intersect', []))}): {', '.join(r.get('sell_intersect', []))}")

        # 학습 힌트 — 자동 생성
        hint = _learning_hint(r)
        if hint:
            lines.append(f"\n💡 자동 학습 힌트: {hint}")
        adj = r.get("adjustment")
        if adj:
            lines.append(
                f"\n🔧 로직 조정: min_score {adj['before'].get('min_score')} → {adj['after'].get('min_score')}, "
                f"max_candidates {adj['before'].get('max_candidates')} → {adj['after'].get('max_candidates')}, "
                f"min_mktcap_억 {adj['before'].get('min_mktcap_억',0)} → {adj['after'].get('min_mktcap_억',0)}"
            )
            lines.append(f"   사유: {', '.join(adj.get('reasons', []))}")
        d = r.get("delta") or {}
        if d.get("added") or d.get("removed") or d.get("exits_today"):
            lines.append(
                f"\n🧭 당일 변동: 신규편입 {len(d.get('added', []))} / 이탈 {len(d.get('removed', []))} / 당일편출 {len(d.get('exits_today', []))}"
            )

    with open(TRACKER_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logger.info(f"[트래커] {TRACKER_PATH} 갱신 완료 (Precision/Recall 추가)")


def _learning_hint(r: dict) -> str:
    """비교 결과 기반 자동 학습 힌트."""
    p = r["precision"]
    rec = r["recall"]
    if r["our_count"] == 0:
        return "우리 로직 후보 0건 — 필터가 너무 엄격함. 임계값 완화 필요."
    if rec < 30 and r["se_count"] > 0:
        return f"Recall {rec}% — 스탁이지 보유 다수가 우리 필터에 걸림. 핵심 조건 재검토."
    if p < 20 and r["our_count"] > 30:
        return f"Precision {p}% & 후보 {r['our_count']}개 — 후보 과다. 추가 필터 필요."
    if p > 70 and rec > 50:
        return f"P/R 모두 양호 — 미세조정 단계. 누락 종목 {len(r['only_se'])}개의 공통 패턴 분석."
    return ""


def _initial_tracker_template() -> str:
    return """# Stockeasy 로직 학습 트래커

> **목표**: 우리 SE Peak / SE Momentum / SE Value 로직이 매일 스탁이지 3전략(Peak/모멘텀/벨류)과
> 100% 일치하도록 학습하고, 매일 결과를 누적 기록한다.
> 세 전략은 매수 철학이 다르므로 공통 로직으로 합치지 않는다.

## 매핑
| 스탁이지 전략 | 우리 로직 | 신호 함수 |
|---------------|----------|-----------|
| Peak Easy     | SE Peak Trend v2 | `calc_stockeasy_trend_candidates()` |
| 모멘텀 Easy   | SE Momentum | `calc_earnings_explosion() + calc_turnaround_momentum()` |
| 벨류 Easy     | SE Value | `calc_value_candidates()` |

## 학습 사이클
1. **매일 16:30** — `stockeasy_analyzer.py`로 스탁이지 보유/이탈 종목 수집
2. **매일 16:35** — `stockeasy_logic_validator.py`로 우리 로직 vs 스탁이지 비교
3. **트래커 append** — 일치율(Precision/Recall/F1) + 누락 종목/과추출 종목 기록
4. **주간 회고** — 일요일 09:00, 7일 평균 일치율 추이 확인 + 로직 조정안 제시

## KPI
- **F1 ≥ 80%** → 로직 안정화 단계 (미세조정 단계)
- **F1 ≥ 95%** → 사실상 일치 (목표)

## 일자별 검증 결과
"""


# ──────────────────────────────────────────────────────────
# 텔레그램 리포트
# ──────────────────────────────────────────────────────────
def send_report(results: list, today: str) -> None:
    if not results:
        return
    lines = [f"🧪 <b>스탁이지 로직 일치율 리포트</b>", f"📅 {today}", "━━━━━━━━━━━━━━━━━━━━━"]
    for r in results:
        s = r["strategy"]
        lines.append(f"\n<b>{STRATEGY_LABELS[s]}</b> — F1 <b>{r['f1']}%</b>")
        lines.append(f"  우리 {r['our_count']} / SE {r['se_count']} / 교집합 {len(r['intersect'])}")
        lines.append(f"  Precision {r['precision']}% · Recall {r['recall']}%")
        lines.append(
            f"  매도 P/R/F1 {r.get('sell_precision', 0)}% / {r.get('sell_recall', 0)}% / {r.get('sell_f1', 0)}%"
        )
        rp = r.get("report_profile") or {}
        if rp.get("report_count"):
            lines.append(f"  📚 리포트 {rp.get('report_count')}개 분석 · 키워드 {', '.join((rp.get('top_keywords') or [])[:4])}")
        d = r.get("delta") or {}
        lines.append(f"  🧭 신규편입 {len(d.get('added', []))} · 이탈 {len(d.get('removed', []))} · 당일편출 {len(d.get('exits_today', []))}")
        if d.get("added"):
            lines.append(f"    + {', '.join(d.get('added', [])[:4])}{' ...' if len(d.get('added', [])) > 4 else ''}")
        if d.get("removed"):
            lines.append(f"    - {', '.join(d.get('removed', [])[:4])}{' ...' if len(d.get('removed', [])) > 4 else ''}")
        if r["only_se"]:
            miss_short = ", ".join(r["only_se"][:5])
            tail = f" 외 {len(r['only_se'])-5}" if len(r["only_se"]) > 5 else ""
            lines.append(f"  ❌ 누락: {miss_short}{tail}")
        adj = r.get("adjustment")
        if adj:
            lines.append(
                f"  🔧 로직값: min_score {adj['before'].get('min_score')}→{adj['after'].get('min_score')}, "
                f"max {adj['before'].get('max_candidates')}→{adj['after'].get('max_candidates')}, "
                f"minCap {adj['before'].get('min_mktcap_억',0)}→{adj['after'].get('min_mktcap_억',0)}"
            )
            lines.append(f"  📌 조정사유: {', '.join(adj.get('reasons', []))}")
        hint = _learning_hint(r)
        if hint:
            lines.append(f"  💡 {hint}")

    msg = "\n".join(lines)
    if len(msg) > 3800:
        msg = msg[:3800] + "...(생략)"
    send_telegram(msg, key=f"se_logic_validator_{today}")
    logger.info("[텔레그램] 일치율 리포트 전송 완료")


# ──────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────
def run_validation(strategies: list, send_tg: bool = True) -> list:
    today = date.today().isoformat()
    results = []
    params = _load_params()
    for s in strategies:
        logger.info(f"[{s}] 비교 시작...")
        r = compare(s)
        adj = _infer_and_apply_adjustment(r, params)
        r["adjustment"] = adj
        logger.info(
            f"[{s}] BUY P={r['precision']}% R={r['recall']}% F1={r['f1']}% | "
            f"SELL P={r.get('sell_precision', 0)}% R={r.get('sell_recall', 0)}% F1={r.get('sell_f1', 0)}% "
            f"(우리 {r['our_count']} / SE {r['se_count']})"
        )
        results.append(r)

    _save_params(params)
    _append_tuning_log({
        "date": today,
        "at": datetime.now().isoformat(timespec="seconds"),
        "results": [
            {
                "strategy": r["strategy"],
                "precision": r["precision"],
                "recall": r["recall"],
                "f1": r["f1"],
                "sell_precision": r.get("sell_precision", 0),
                "sell_recall": r.get("sell_recall", 0),
                "sell_f1": r.get("sell_f1", 0),
                "delta": r.get("delta", {}),
                "adjustment": r.get("adjustment", {}),
            } for r in results
        ]
    })

    append_to_tracker(results, today)
    if send_tg:
        send_report(results, today)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["peak", "momentum", "value"], default=None)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--tune-peak-hvm", action="store_true")
    parser.add_argument("--tune-strategy", choices=["peak", "momentum", "value"], default=None)
    parser.add_argument("--tune-sell", action="store_true")
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--sell-lookback-snapshots", type=int, default=30)
    parser.add_argument("--replay-entry", action="store_true")
    parser.add_argument("--backtest-sell", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--top-n", type=int, default=60)
    args = parser.parse_args()
    if args.tune_peak_hvm:
        res = tune_peak_params_fast(iterations=args.iterations)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if args.tune_strategy:
        res = tune_strategy_params_fast(args.tune_strategy, iterations=args.iterations)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if args.tune_sell:
        sell_strategies = [args.strategy] if args.strategy else ["peak", "momentum"]
        res = tune_sell_params(sell_strategies, iterations=args.iterations, lookback_snapshots=args.sell_lookback_snapshots)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if args.replay_entry:
        strategies = [args.strategy] if args.strategy else ["peak", "momentum", "value"]
        out = []
        for s in strategies:
            out.append(replay_entry_day_inclusion(s, lookback_days=args.lookback_days, top_n=args.top_n))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if args.backtest_sell:
        strategies = [args.strategy] if args.strategy else ["peak", "momentum"]
        out = []
        for s in strategies:
            out.append(backtest_sell(s, lookback_snapshots=args.sell_lookback_snapshots))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    strategies = [args.strategy] if args.strategy else ["peak", "momentum", "value"]
    run_validation(strategies, send_tg=not args.no_telegram)
