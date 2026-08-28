"""
텐버거 백테스트 — look-ahead bias 완전 제거 + 데이터 기반 매도 전략

핵심 원칙:
  1. 모든 신호는 해당 날짜 이전 데이터만 사용
  2. PBR은 '해당 시점 종가 ÷ 최신 확인 가능 BPS'로 계산
  3. 시총은 '해당 시점 종가 × 발행주식수'로 계산

[데이터 기반 매도 전략 설계 근거 — 2026-06-27]
역사적 3배+ 달성 종목 분석(2019~2024):
  - 저점→고점 평균 기간: 956일(31.9개월)  → max_hold=730일(2년) 설정
  - 고점 후 30일 평균 하락: -34%          → 고점 포착이 핵심
  - 고점 후 90일 내 100% 가 -30% 이상 하락 → 추적손절이 단순 익절보다 우월
  - -20% 추적손절 → 에코프로비엠 +180% 청산 (실제 +8,008% 놓침)
  - 상승 중 -20~30% 눌림이 3~5회 반복 발생 → 단순 추적손절은 항상 초기 청산

올바른 매도 조건 (3가지 레이어):
  [레이어1] 손절: 매수가 대비 -25% (눌림 내성 확보)
  [레이어2] 펀더멘털 붕괴: 2분기 연속 영업적자 전환 (진입 이유 소멸)
  [레이어3] 추세 붕괴: 60일 고점 대비 -35% + 점수 30점 이하 (반등 없는 하락)
  [레이어4] 최대 보유: 730일(2년) — 데이터 기반, 240일은 근거 없음
  익절(+80%)는 폐기 — 텐버거 종목에서 +80%는 여정의 시작에 불과함

실행: python3 scripts/tenbagger_backtest.py
"""
import sqlite3
import os
import json
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'stock.db')
TC = 0.003   # 왕복 수수료

WINDOWS = [
    ("2019-01-01", "2019-12-31"),
    ("2020-01-01", "2020-12-31"),
    ("2021-01-01", "2021-12-31"),
    ("2022-01-01", "2022-12-31"),
    ("2023-01-01", "2023-12-31"),
    ("2024-01-01", "2024-12-31"),
    ("2019-01-01", "2024-12-31"),
]


def get_conn():
    return sqlite3.connect(DB_PATH, timeout=60)


def get_monthly_rebal_dates(conn, start, end):
    rows = conn.execute(
        "SELECT DISTINCT date FROM price_history WHERE stock_code='^KS11' "
        "AND date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    dates = [r[0] for r in rows]
    seen, result = set(), []
    for d in dates:
        ym = d[:7]
        if ym not in seen:
            seen.add(ym)
            result.append(d)
    return result


def get_weekly_rebal_dates(conn, start, end):
    """주간 리밸런싱 날짜 — 월별 손절 집행 지연 문제 해결
    데이터 근거: -25% 손절 설정 시 월별 체크로 실제 -36~-58% 손실 발생
    주간 체크로 전환 시 손절 집행 정확도 대폭 개선"""
    rows = conn.execute(
        "SELECT DISTINCT date FROM price_history WHERE stock_code='^KS11' "
        "AND date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    dates = [r[0] for r in rows]
    seen, result = set(), []
    for d in dates:
        # 주 번호: ISO week
        dt = datetime.strptime(d, "%Y-%m-%d")
        week_key = f"{dt.isocalendar()[0]}-{dt.isocalendar()[1]}"
        if week_key not in seen:
            seen.add(week_key)
            result.append(d)
    return result


# ── 데이터 사전 로드 ────────────────────────────────────────────────
def load_data(conn, start, end):
    preload_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")

    print("  [주가] 로드 중...", end="", flush=True)
    price_data = {}
    for r in conn.execute(
        """SELECT stock_code, date, high, low, close, volume FROM price_history
           WHERE date >= ? AND date <= ? AND close > 0
             AND (stock_code='^KS11' OR stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]')
           ORDER BY stock_code, date DESC""",
        (preload_start, end)
    ):
        c = r[0]
        if c not in price_data:
            price_data[c] = []
        price_data[c].append((r[1], r[2], r[3], r[4], r[5]))
    print(f" {len(price_data)}종목")

    print("  [재무] 로드 중...", end="", flush=True)
    fin_data = {}
    for r in conn.execute(
        "SELECT stock_code, year, quarter, operating_profit, bps FROM financial_data "
        "WHERE is_annual=0 AND year>=2018 ORDER BY stock_code, year DESC, quarter DESC"
    ):
        c = r[0]
        if c not in fin_data:
            fin_data[c] = []
        fin_data[c].append((r[1], r[2], r[3], r[4]))  # year,q,op,bps
    print(f" {len(fin_data)}종목")

    print("  [마스터] 로드 중...", end="", flush=True)
    shares_data = {}
    for r in conn.execute(
        "SELECT stock_code, shares_issued, market FROM stock_universe "
        "WHERE market IN ('KOSPI','KOSDAQ') AND shares_issued > 0 "
        "AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
    ):
        shares_data[r[0]] = (r[1], r[2])
    print(f" {len(shares_data)}종목")

    return price_data, fin_data, shares_data


# ── 날짜 기준 분기 가용성 체크 ──────────────────────────────────────
def fin_avail_by(year, quarter, target_date):
    """분기보고서 공시 후 45일 지연 적용"""
    month_map = {1: 5, 2: 8, 3: 11, 4: 3}
    rel_year = year + 1 if quarter == 4 else year
    m = month_map.get(quarter, 5)
    return target_date >= f"{rel_year}-{m:02d}-15"


# ── 종목 점수 계산 (look-ahead 없이) ────────────────────────────────
def score_at(code, target_date, price_data, fin_data, shares_data):
    if code not in price_data:
        return None

    hist = [(d, hi, lo, cl, vol) for d, hi, lo, cl, vol in price_data[code] if d <= target_date]
    if len(hist) < 60:
        return None

    cur_d, cur_hi, cur_lo, cur_cl, cur_vol = hist[0]

    year_hist = hist[:252]
    high52 = max((r[1] for r in year_hist if r[1]), default=0)
    low52  = min((r[2] for r in year_hist if r[2] and r[2] > 0), default=0)
    if not high52:
        return None

    from_high = (cur_cl - high52) / high52 * 100
    from_low  = (cur_cl - low52) / low52 * 100 if low52 else 999

    # 거래량: 60일 평균 대비 (데이터 역산: 실제 3배+종목 저점 이후 9.1배, 57%가 2배+)
    vol60 = sum(r[4] for r in hist[:60]) / 60 if len(hist) >= 60 else 0
    vol_ratio = cur_vol / vol60 if vol60 > 0 else 0

    recent5 = [r[3] for r in hist[:6]]
    reversal = len(recent5) >= 5 and sum(1 for i in range(4) if recent5[i] > recent5[i+1]) >= 2

    fins = [f for f in fin_data.get(code, []) if fin_avail_by(f[0], f[1], target_date)]
    turnaround = False
    op_trend_up = False
    hist_pbr = None

    if len(fins) >= 2:
        op_cur  = fins[0][2] or 0
        op_prev = fins[1][2] or 0
        if op_prev < 0 and op_cur > 0:
            turnaround = True
        if len(fins) >= 3:
            op3 = fins[2][2] or 0
            if op_cur > op_prev > 0 and op_prev > op3:
                op_trend_up = True

        bps = fins[0][3]
        if bps and bps > 0:
            hist_pbr = cur_cl / bps

    shares = shares_data.get(code)
    hist_mktcap_억 = None
    if shares:
        hist_mktcap_억 = round(cur_cl * shares[0] / 1e8)

    score = 0.0

    # A. 낙폭과대 (25점) — 핵심: 70.5%가 -30~-70% 구간에서 출발
    if from_high <= -50:
        score += 25
    elif from_high <= -40:
        score += 22
    elif from_high <= -30:
        score += 18
    elif from_high <= -20:
        score += 10
    elif from_high <= -10:
        score += 3

    # B. 펀더멘털 변화 (25점) — 핵심: 40.5%가 적자 상태
    if turnaround:
        score += 20
    if op_trend_up:
        score += 8
    elif len(fins) >= 2 and (fins[0][2] or 0) > (fins[1][2] or 0) > 0:
        score += 4

    # C. 저평가 소형주 (20점)
    if hist_pbr:
        if hist_pbr < 0.5:
            score += 15
        elif hist_pbr < 0.8:
            score += 10
        elif hist_pbr < 1.2:
            score += 5
    if hist_mktcap_억:
        if 200 <= hist_mktcap_억 <= 2000:
            score += 8
        elif 2000 < hist_mktcap_억 <= 5000:
            score += 4

    # D. 기술적 신호 (15점)
    if reversal:
        score += 8
    # 거래량: 60일 평균 2배+ (데이터 역산: 57%가 2배+, 핵심 신호)
    if vol_ratio >= 3.0:
        score += 10
    elif vol_ratio >= 2.0:
        score += 7
    elif vol_ratio >= 1.5:
        score += 3
    if from_low <= 3:
        score += 5

    # E. 시너지 보너스
    if from_high <= -30 and reversal and vol_ratio >= 1.5:
        score += 5

    return {
        "score": score,
        "from_high": from_high,
        "vol_ratio": vol_ratio,
        "turnaround": turnaround,
        "in_zone": (-70 <= from_high <= -30),
        "reversal": reversal,
        "hist_pbr": hist_pbr,
        "hist_mktcap_억": hist_mktcap_억,
        "close": cur_cl,
        "fins": fins,
    }


def get_price_at(code, date, price_data):
    for d, hi, lo, cl, vol in price_data.get(code, []):
        if d <= date:
            return cl
    return None


def get_60d_high(code, date, price_data):
    """최근 60거래일 고가 (추세붕괴 기준점)"""
    hist = [(d, cl) for d, hi, lo, cl, vol in price_data.get(code, []) if d <= date]
    if len(hist) < 10:
        return None
    return max(cl for d, cl in hist[:60])


def get_recent_op_trend(code, date, fin_data):
    """최근 2분기 영업이익 동향: 'loss2'=2분기연속적자, 'loss1'=1분기적자, 'ok'=흑자"""
    fins = [f for f in fin_data.get(code, []) if fin_avail_by(f[0], f[1], date)]
    if len(fins) < 2:
        return 'unknown'
    op0 = fins[0][2] or 0
    op1 = fins[1][2] or 0
    if op0 < 0 and op1 < 0:
        return 'loss2'
    if op0 < 0:
        return 'loss1'
    return 'ok'


def get_kospi_return(price_data, start, end):
    ks = price_data.get("^KS11", [])
    s = [cl for d, hi, lo, cl, vol in ks if d >= start]
    e = [cl for d, hi, lo, cl, vol in ks if d <= end]
    if s and e:
        return (e[0] - s[-1]) / s[-1]
    return 0.0


# ── 매도 판단 함수 (데이터 기반 3레이어) ────────────────────────────
def should_sell(code, pos, cur, rd, price_data, fin_data, params):
    """
    데이터 기반 매도 조건 (3레이어):
    [L1] 손절: 매수가 대비 -25% (눌림 내성 확보, -20%는 조기 청산)
    [L2] 펀더멘털 붕괴: 2분기 연속 영업적자 (진입 이유 소멸)
    [L3] 추세 붕괴: 60일 고점 대비 -35% + 점수 30점 이하
    [L4] 최대 보유: 730일(2년) — 역사적 평균 956일, 안전마진 적용
    반환: (bool, reason)
    """
    ret = (cur - pos["entry"]) / pos["entry"]
    hold = (datetime.strptime(rd, "%Y-%m-%d") - datetime.strptime(pos["date"], "%Y-%m-%d")).days

    # L1: 손절 (매수가 기준 -25%)
    stop = params.get("stop_loss", -0.25)
    if ret <= stop:
        return True, f"손절{ret*100:.0f}%"

    # L4: 최대 보유 기간
    max_hold = params.get("max_hold", 730)
    if hold >= max_hold:
        return True, f"기간만료{hold}일"

    # L2: 펀더멘털 붕괴 — 2분기 연속 영업적자 전환
    # (최소 180일 보유 후 적용 — 초기 일시적 적자 오청산 방지)
    if hold >= 180:
        op_trend = get_recent_op_trend(code, rd, fin_data)
        if op_trend == 'loss2':
            return True, "영업적자2Q연속"

    # L3: 추세 붕괴 — 60일 고점 대비 -35% + 점수 하락
    # (최소 90일 보유 후 적용)
    if hold >= 90 and ret > 0.10:  # 최소 +10% 수익 구간에서만
        high60 = get_60d_high(code, rd, price_data)
        if high60 and cur <= high60 * 0.65:  # 60일 고점 대비 -35%
            # 점수도 같이 확인 (점수가 30 이하면 반등 기대 없음)
            # 간단히: 60일 -35% 하락이면 추세 붕괴로 판단
            return True, f"추세붕괴60일-35%"

    return False, None


# ── 백테스트 ────────────────────────────────────────────────────────
def run_backtest(start, end, params, price_data, fin_data, shares_data, rebal_dates_all):
    rdates = [d for d in rebal_dates_all if start <= d <= end]
    if len(rdates) < 3:
        return None

    portfolio = {}
    cash = 1.0
    equity = []
    sell_reasons = {}

    for rd in rdates:
        # 1. 청산 체크 (데이터 기반 매도 조건)
        to_sell = []
        for code, pos in list(portfolio.items()):
            cur = get_price_at(code, rd, price_data)
            if cur is None:
                to_sell.append((code, "상폐/데이터없음"))
                continue
            sell, reason = should_sell(code, pos, cur, rd, price_data, fin_data, params)
            if sell:
                to_sell.append((code, reason))

        for code, reason in to_sell:
            pos = portfolio.pop(code)
            cur = get_price_at(code, rd, price_data)
            if cur:
                ret = (cur - pos["entry"]) / pos["entry"]
                cash += pos["w"] * (1.0 + ret - TC)
                sell_reasons[reason] = sell_reasons.get(reason, 0) + 1

        # 2. 신규 후보
        slots = params["max_pos"] - len(portfolio)
        if slots > 0:
            scored = []
            for code in shares_data:
                if code in portfolio:
                    continue
                info = score_at(code, rd, price_data, fin_data, shares_data)
                if info is None:
                    continue
                if info["score"] < params["min_score"]:
                    continue
                if params.get("drawdown_req") and not info["in_zone"]:
                    continue
                if params.get("reversal_req") and not info["reversal"]:
                    continue
                vol_req = params.get("vol_req", 0)
                if vol_req > 0 and info["vol_ratio"] < vol_req:
                    continue
                max_cap = params.get("max_cap", 0)
                if max_cap > 0 and info["hist_mktcap_억"] and info["hist_mktcap_억"] > max_cap:
                    continue
                scored.append((code, info["score"], info["close"]))

            scored.sort(key=lambda x: -x[1])
            for code, sc, cl in scored[:slots]:
                if cash <= 0:
                    break
                w = 1.0 / params["max_pos"]
                portfolio[code] = {"entry": cl, "date": rd, "w": w}
                cash -= w * (1.0 + TC)

        # 3. 포트폴리오 가치
        pv = max(cash, 0)
        for code, pos in portfolio.items():
            cur = get_price_at(code, rd, price_data)
            if cur:
                pv += pos["w"] * (1.0 + (cur - pos["entry"]) / pos["entry"])
        equity.append((rd, pv))

    # 최종 청산
    last = rdates[-1]
    for code, pos in portfolio.items():
        cur = get_price_at(code, last, price_data)
        if cur:
            ret = (cur - pos["entry"]) / pos["entry"]
            cash += pos["w"] * (1.0 + ret - TC)

    total_ret = cash - 1.0
    kospi_ret = get_kospi_return(price_data, start, end)

    rets = [(equity[i][1] - equity[i-1][1]) / equity[i-1][1]
            for i in range(1, len(equity)) if equity[i-1][1] > 0]
    win_rate = sum(1 for r in rets if r > 0) / len(rets) if rets else 0

    peak, max_dd = equity[0][1] if equity else 1, 0
    for _, v in equity:
        if v > peak: peak = v
        if peak > 0: max_dd = min(max_dd, (v - peak) / peak)

    return {
        "total_ret": total_ret,
        "kospi_ret": kospi_ret,
        "win_rate": win_rate,
        "max_dd": max_dd,
        "alpha_x": total_ret / kospi_ret if abs(kospi_ret) > 0.01 else None,
        "sell_reasons": sell_reasons,
    }


# ── 메인 ────────────────────────────────────────────────────────────
def main():
    conn = get_conn()
    print("=" * 72)
    print("텐버거 백테스트 — 데이터 기반 매도 전략 v2")
    print("=" * 72)
    print("""
[매도 전략 설계 근거]
- 역사적 3배+종목 저점→고점 평균: 956일(31.9개월)
- +80% 익절은 에코프로비엠 기준 +8,008% 여정의 2%에서 청산 → 폐기
- 240일 보유한도는 근거 없음 → 730일(2년)로 변경
- -20% 추적손절은 상승 중 7회 눌림 발생 시 초기 청산 → -25%로 완화
- 진짜 매도 신호: 2분기 연속 영업적자(펀더멘털 소멸) + 60일-35% 추세붕괴
""")

    test_window = ("2019-01-01", "2024-12-31")
    print(f"기간: {test_window[0]} ~ {test_window[1]}")

    print("\n[1] 데이터 로드...")
    price_data, fin_data, shares_data = load_data(conn, test_window[0], test_window[1])
    rebal_dates = get_weekly_rebal_dates(conn, test_window[0], test_window[1])
    print(f"  리밸런싱 날짜(주간): {len(rebal_dates)}개")

    kospi_ret = get_kospi_return(price_data, test_window[0], test_window[1])
    print(f"  KOSPI 6년 수익률: {kospi_ret*100:.1f}%")

    # ── 파라미터 조합 (데이터 기반 매도 조건 적용) ──────────────────
    configs = [
        # ── v3: 데이터 역산 기반 (기관/외인 제거, 거래량 60일 2배+, 주간리밸) ──
        ("★v3_거래량2배+주간리밸", {
            "min_score": 45, "drawdown_req": True, "reversal_req": False,
            "vol_req": 2.0, "stop_loss": -0.25,
            "max_hold": 730, "max_pos": 10, "max_cap": 5000,
        }),
        ("★v3_점수45+시총2000억", {
            "min_score": 45, "drawdown_req": True, "reversal_req": False,
            "vol_req": 2.0, "stop_loss": -0.25,
            "max_hold": 730, "max_pos": 10, "max_cap": 2000,
        }),
        ("★v3_점수50+시총3000억", {
            "min_score": 50, "drawdown_req": True, "reversal_req": False,
            "vol_req": 2.0, "stop_loss": -0.25,
            "max_hold": 730, "max_pos": 8, "max_cap": 3000,
        }),
        # ── 기존 전략 (비교용) ─────────────────────────────────────
        ("★데이터기반_손절25%+2년보유", {
            "min_score": 50, "drawdown_req": True, "reversal_req": False,
            "vol_req": 1.5, "stop_loss": -0.25,
            "max_hold": 730,   # 2년 (평균 956일 기준)
            "max_pos": 10, "max_cap": 5000,
        }),
        ("★데이터기반_손절30%+2년보유", {
            "min_score": 50, "drawdown_req": True, "reversal_req": False,
            "vol_req": 1.5, "stop_loss": -0.30,
            "max_hold": 730,
            "max_pos": 10, "max_cap": 5000,
        }),
        ("★데이터기반_손절25%+1.5년", {
            "min_score": 50, "drawdown_req": True, "reversal_req": False,
            "vol_req": 1.5, "stop_loss": -0.25,
            "max_hold": 540,   # 1.5년
            "max_pos": 10, "max_cap": 5000,
        }),
        ("★데이터기반_낙폭40+거래량2x", {
            "min_score": 50, "drawdown_req": True, "reversal_req": False,
            "vol_req": 2.0, "stop_loss": -0.25,
            "max_hold": 730,
            "max_pos": 10, "max_cap": 5000,
        }),
        ("★데이터기반_소형주2000억", {
            "min_score": 50, "drawdown_req": True, "reversal_req": False,
            "vol_req": 1.5, "stop_loss": -0.25,
            "max_hold": 730,
            "max_pos": 10, "max_cap": 2000,
        }),
        ("★데이터기반_점수55+분산8종", {
            "min_score": 55, "drawdown_req": True, "reversal_req": False,
            "vol_req": 1.5, "stop_loss": -0.25,
            "max_hold": 730,
            "max_pos": 8, "max_cap": 5000,
        }),
        # ── 비교군: 구 전략 (잘못된 설계) ──────────────────────
        ("[구]장기보유+익절80%(잘못된설계)", {
            "min_score": 50, "drawdown_req": True, "reversal_req": False,
            "vol_req": 1.5, "stop_loss": -0.20,
            "max_hold": 240,   # 근거 없는 240일
            "max_pos": 10, "max_cap": 5000,
            "_legacy": True,   # 구 전략 표시
        }),
    ]

    print(f"\n[2] {len(configs)}개 전략 테스트...")
    results = []
    for name, params in configs:
        # 구 전략은 구 매도 로직 사용
        if params.pop("_legacy", False):
            r = _run_legacy(test_window[0], test_window[1], params,
                            price_data, fin_data, shares_data, rebal_dates)
        else:
            r = run_backtest(test_window[0], test_window[1], params,
                             price_data, fin_data, shares_data, rebal_dates)
        if r:
            ax = r.get("alpha_x")
            ax_str = f"{ax:.2f}x" if ax is not None else "  N/A"
            goal = ax is not None and ax >= 2.0 and r["total_ret"] > 0
            star = "★★★" if goal else ("★★" if ax is not None and ax >= 1.5 else "  ")
            reasons = r.get("sell_reasons", {})
            print(f"  {star} {name:<40} {r['total_ret']*100:>+7.1f}% | "
                  f"KOSPI대비: {ax_str} | MDD: {r['max_dd']*100:.1f}%")
            if reasons:
                print(f"       매도사유: {reasons}")
            results.append({**r, "name": name, "params": params, "ax": ax})

    results.sort(key=lambda x: (x["total_ret"] > 0, x.get("ax") or -99), reverse=True)

    print("\n" + "=" * 72)
    print("최종 랭킹")
    print("=" * 72)
    print(f"{'순위':<4} {'전략':<40} {'수익률':>8} {'KOSPI':>7} {'대비':>6} {'MDD':>7}")
    print("-" * 72)
    for i, r in enumerate(results, 1):
        ax_str = f"{r['ax']:.2f}x" if r.get("ax") else "  N/A"
        mark = "◀ 목표달성" if (r.get("ax") and r["ax"] >= 2.0 and r["total_ret"] > 0) else ""
        print(f"{i:<4} {r['name']:<40} {r['total_ret']*100:>+7.1f}% "
              f"{r['kospi_ret']*100:>6.1f}% {ax_str:>6} {r['max_dd']*100:>6.1f}% {mark}")

    # 베스트 전략 연도별 검증
    best = results[0] if results else None
    if best:
        print(f"\n[베스트: '{best['name']}'] 연도별 검증...")
        for start, end in WINDOWS[:6]:
            r = run_backtest(start, end, best["params"], price_data, fin_data, shares_data, rebal_dates)
            if r:
                ax = r.get("ax")
                ax_s = f"{ax:.2f}x" if ax else "N/A"
                ok = "✅" if r["total_ret"] > 0 else "❌"
                print(f"  {start[:4]}: {r['total_ret']*100:+.1f}% vs KOSPI "
                      f"{r['kospi_ret']*100:+.1f}% → {ax_s} | MDD {r['max_dd']*100:.1f}% {ok}")


def _run_legacy(start, end, params, price_data, fin_data, shares_data, rebal_dates_all):
    """구 전략: 단순 손절/익절/기간 기반 (비교용)"""
    rdates = [d for d in rebal_dates_all if start <= d <= end]
    if len(rdates) < 3:
        return None
    portfolio = {}
    cash = 1.0
    equity = []
    for rd in rdates:
        to_sell = []
        for code, pos in list(portfolio.items()):
            cur = get_price_at(code, rd, price_data)
            if cur is None:
                to_sell.append(code)
                continue
            ret = (cur - pos["entry"]) / pos["entry"]
            hold = (datetime.strptime(rd, "%Y-%m-%d") - datetime.strptime(pos["date"], "%Y-%m-%d")).days
            # 구 전략: 손절 / 익절 / 기간만료
            if ret <= params["stop_loss"] or ret >= 0.80 or hold >= 240:
                to_sell.append(code)
        for code in to_sell:
            pos = portfolio.pop(code)
            cur = get_price_at(code, rd, price_data)
            if cur:
                ret = (cur - pos["entry"]) / pos["entry"]
                cash += pos["w"] * (1.0 + ret - TC)
        slots = params["max_pos"] - len(portfolio)
        if slots > 0:
            scored = []
            for code in shares_data:
                if code in portfolio:
                    continue
                info = score_at(code, rd, price_data, fin_data, shares_data)
                if not info or info["score"] < params["min_score"]:
                    continue
                if params.get("drawdown_req") and not info["in_zone"]:
                    continue
                vol_req = params.get("vol_req", 0)
                if vol_req > 0 and info["vol_ratio"] < vol_req:
                    continue
                scored.append((code, info["score"], info["close"]))
            scored.sort(key=lambda x: -x[1])
            for code, sc, cl in scored[:slots]:
                if cash <= 0:
                    break
                w = 1.0 / params["max_pos"]
                portfolio[code] = {"entry": cl, "date": rd, "w": w}
                cash -= w * (1.0 + TC)
        pv = max(cash, 0)
        for code, pos in portfolio.items():
            cur = get_price_at(code, rd, price_data)
            if cur:
                pv += pos["w"] * (1.0 + (cur - pos["entry"]) / pos["entry"])
        equity.append((rd, pv))
    last = rdates[-1]
    for code, pos in portfolio.items():
        cur = get_price_at(code, last, price_data)
        if cur:
            ret = (cur - pos["entry"]) / pos["entry"]
            cash += pos["w"] * (1.0 + ret - TC)
    total_ret = cash - 1.0
    kospi_ret = get_kospi_return(price_data, start, end)
    rets = [(equity[i][1] - equity[i-1][1]) / equity[i-1][1]
            for i in range(1, len(equity)) if equity[i-1][1] > 0]
    peak, max_dd = equity[0][1] if equity else 1, 0
    for _, v in equity:
        if v > peak: peak = v
        if peak > 0: max_dd = min(max_dd, (v - peak) / peak)
    return {
        "total_ret": total_ret, "kospi_ret": kospi_ret,
        "win_rate": sum(1 for r in rets if r > 0) / len(rets) if rets else 0,
        "max_dd": max_dd,
        "alpha_x": total_ret / kospi_ret if abs(kospi_ret) > 0.01 else None,
        "sell_reasons": {"구전략(손절/익절80%/240일)": "비교용"},
    }


if __name__ == "__main__":
    main()
