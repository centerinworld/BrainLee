"""가상매매(paper) 공통 진입·청산 가드 — 2026-09-24 수익률 개선안 적용.

근거: peak_holding 청산 307건(승률 24%, 평균 -3.4%) 분석 (docs/SYSTEM_REVIEW_20260924.md §C, 2026-09-24 재현·기간분할 검증).
  * 진입일 KOSPI가 MA60 아래: 평균 -6.2%/승률 16% vs 위: -0.6%/32% (7/31 이전만 봐도 -9.4% vs -0.6%)
  * 이익 +10% 도달 후 본전스톱: 평균 -3.36% → -2.23%, 9개 전략 중 8개 개선(value +23%p, ai_combo +6%p)
  * 동일 종목 다중 전략 중복(삼성전자 6포지션 등) → 종목/섹터 노출 한도
  * 진입일 등락 하락일 -8.4% / 0~3% -5.6% / 8~15% +2.9% — 국면과 교락 가능성이 있어 shadow(로그만)로 시작

훅(routes/trend.py):
  * `_paper_buy_gate()` 진입부 → `check_entry()` (국면·노출 한도: 차단 / 진입확인: shadow)
  * `_auto_hardstop_all_strategies()` → `breakeven_exit()` (+10% 도달 후 본전 이탈 시 청산)

환경변수(기본값 = 적용):
  VT_REGIME_FILTER=1        KOSPI<MA60이면 모멘텀/돌파 계열 신규 진입 차단 (REGIME_EXEMPT 제외)
  VT_EXPOSURE_LIMIT=1       종목당 동시 보유 상한(VT_MAX_POS_PER_STOCK, 기본 2) · 섹터 비중 상한(VT_SECTOR_CAP_PCT, 기본 35%)
  VT_BREAKEVEN_STOP=1       +VT_BREAKEVEN_ARM_PCT(기본 10)% 도달 후 매입가 이하로 내려오면 청산
  VT_ENTRY_CONFIRM=shadow   off | shadow(로그만) | enforce — 당일 등락 <+3% 진입을 기록/차단
  VT_MIN_MCAP_EOK=0         (R3, 기본 꺼짐) 시총이 이 값(억원) 미만이면 신규 진입 차단 — 슬리피지 0.4~0.8%/편도 구간 회피. 연구 권고 1000
  VT_REENTRY_COOLDOWN_DAYS=0 (R3, 기본 꺼짐) 같은 종목을 청산한 뒤 이 일수(달력일) 이내 재진입 차단. 연구 권고 28(≈20거래일)
  VT_ACCOUNT_DD_GUARD=shadow (R6) off | shadow(기록만, 기본) | enforce — 가상 계좌 총평가액이 60일 고점 대비 -10%면 신규 진입 격번(50% 축소),
                            -15%면 중단(KOSPI>MA60 & 낙폭 -5% 이내에서만 해제). 상태는 `virtual_account_equity_daily`(scripts/compute_virtual_account_equity_20260925.py).
                            연구(account_dd_rule_20260925.md) 결과가 일관되지 않아(MDD 개선 불명확) enforce는 사용자 결정.
  VT_SHADOW_STRATEGIES=     쉼표 구분 전략 키 — 해당 전략의 **신규 진입만** 기록 전용(진입하지 않고 `virtual_guard_log`에
                            guard='shadow_strategy', decision='shadow_would_block', 가격·KOSPI 포함 기록 → 사후 5/20/60일 수익 추적).
                            기존 보유분 청산·실주문 경로는 건드리지 않는다. 2026-09-25 사용자 승인(R1 A안): momentum,peak. 기본값 빈 값.
"""
from __future__ import annotations

import logging
import os
import zlib
from datetime import datetime

logger = logging.getLogger(__name__)


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "off", "no", "")


REGIME_FILTER = lambda: _flag("VT_REGIME_FILTER")
EXPOSURE_LIMIT = lambda: _flag("VT_EXPOSURE_LIMIT")
BREAKEVEN_STOP = lambda: _flag("VT_BREAKEVEN_STOP")
MAX_POS_PER_STOCK = lambda: int(os.getenv("VT_MAX_POS_PER_STOCK", "2"))
SECTOR_CAP_PCT = lambda: float(os.getenv("VT_SECTOR_CAP_PCT", "35"))
BREAKEVEN_ARM_PCT = lambda: float(os.getenv("VT_BREAKEVEN_ARM_PCT", "10"))
MIN_MCAP_EOK = lambda: float(os.getenv("VT_MIN_MCAP_EOK", "0") or 0)
REENTRY_COOLDOWN_DAYS = lambda: int(float(os.getenv("VT_REENTRY_COOLDOWN_DAYS", "0") or 0))
ENTRY_CONFIRM_MIN_PCT = 3.0

# 역발상(낙폭반등·저평가) 계열은 약세장이 정상 진입 구간 → 국면 필터 제외.
REGIME_EXEMPT = {"v_recovery", "value"}
# 공통 손절 제외(자체 청산 로직 보유, 기존 _auto_hardstop_all_strategies 정책과 동일)
BREAKEVEN_EXEMPT = {"gpt_v18"}


def shadow_strategies() -> set[str]:
    return {x.strip() for x in os.getenv("VT_SHADOW_STRATEGIES", "").split(",") if x.strip()}


def shadow_entry(conn, code: str, strategy: str, price: float | None) -> bool:
    """True면 이 전략의 신규 진입은 기록 전용(shadow)이다 — 호출자는 진입하지 않는다. 기록은 여기서 남긴다(하루 1건 중복 제거)."""
    strategy = str(strategy or "")
    if strategy not in shadow_strategies():
        return False
    _log(conn, str(code), strategy, "shadow_strategy", "shadow_would_block",
         f"shadow_strategy: {strategy} 신규 진입 기록 전용 (R1 채택 기준 미달, 2026-09-25 승인)",
         price=price, kospi=_kospi_snapshot(conn))
    return True


def _account_dd_mode() -> str:
    m = os.getenv("VT_ACCOUNT_DD_GUARD", "shadow").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "shadow"


def account_dd_state(conn) -> tuple[str, float | None]:
    """최신 가상 계좌 낙폭 상태 ('normal'|'half'|'stop', dd%). 테이블·데이터가 없으면 ('normal', None) — fail-open."""
    try:
        r = conn.execute("SELECT state, dd_pct FROM virtual_account_equity_daily ORDER BY trade_date DESC LIMIT 1").fetchone()
        return (str(r[0]), float(r[1])) if r else ("normal", None)
    except Exception:
        return "normal", None


def _entry_confirm_mode() -> str:
    m = os.getenv("VT_ENTRY_CONFIRM", "shadow").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "shadow"


_LOG_EXTRA_COLUMNS = (
    # 2026-09-25 (§11 S3): 차단 시점 기준가·시장 상태와 사후 성과 — "막은 진입"의 이후 수익을 비교하기 위한 컬럼
    ("price_at_block", "DOUBLE PRECISION DEFAULT NULL"),
    ("kospi_close", "DOUBLE PRECISION DEFAULT NULL"),
    ("kospi_ma60", "DOUBLE PRECISION DEFAULT NULL"),
    ("ret_5d", "DOUBLE PRECISION DEFAULT NULL"),
    ("ret_20d", "DOUBLE PRECISION DEFAULT NULL"),
    ("ret_60d", "DOUBLE PRECISION DEFAULT NULL"),
    ("kospi_ret_5d", "DOUBLE PRECISION DEFAULT NULL"),
    ("kospi_ret_20d", "DOUBLE PRECISION DEFAULT NULL"),
    ("kospi_ret_60d", "DOUBLE PRECISION DEFAULT NULL"),
)


def _ensure_log(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS virtual_guard_log ("
        "id SERIAL PRIMARY KEY, logged_at TIMESTAMP DEFAULT now(), stock_code TEXT, strategy TEXT, "
        "guard TEXT, decision TEXT, detail TEXT)"
    )
    for col, ddl in _LOG_EXTRA_COLUMNS:
        conn.execute(f"ALTER TABLE virtual_guard_log ADD COLUMN IF NOT EXISTS {col} {ddl}")


def _log(conn, code: str, strategy: str, guard: str, decision: str, detail: str,
         price: float | None = None, kospi: tuple[float, float] | None = None) -> None:
    """가드 판정 기록. 같은 (종목, 전략, 가드, 판정)은 하루 1건만 남긴다(분 단위 재시도 중복 방지)."""
    try:
        _ensure_log(conn)
        dup = conn.execute(
            "SELECT 1 FROM virtual_guard_log WHERE stock_code=? AND strategy=? AND guard=? AND decision=? "
            "AND CAST(logged_at AS DATE)=CURRENT_DATE LIMIT 1",
            (code, strategy, guard, decision),
        ).fetchone()
        if dup:
            return
        conn.execute(
            "INSERT INTO virtual_guard_log (stock_code, strategy, guard, decision, detail, price_at_block, kospi_close, kospi_ma60) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (code, strategy, guard, decision, detail[:500],
             float(price) if price else None, kospi[0] if kospi else None, kospi[1] if kospi else None),
        )
        conn.commit()
    except Exception as e:  # 로그 실패가 매매를 막지 않도록
        logger.warning(f"[가드로그] 기록 실패: {e}")


def _kospi_snapshot(conn) -> tuple[float, float] | None:
    try:
        _above, cur, ma = kospi_above_ma60(conn)
        return (cur, ma) if cur and ma else None
    except Exception:
        return None


def _dedup_closes(rows) -> list[float]:
    seen, out = set(), []
    for d, c in rows:
        k = str(d)[:10]
        if k not in seen:
            seen.add(k)
            out.append(float(c or 0))
    return out


def kospi_above_ma60(conn) -> tuple[bool, float, float]:
    """KOSPI 일별 종가 vs MA60. 데이터가 부족하면 (True, 0, 0) — 필터가 fail-open."""
    rows = conn.execute(
        "SELECT date, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 90"
    ).fetchall()
    closes = _dedup_closes(rows)
    if len(closes) < 60:
        return True, 0.0, 0.0
    ma60 = sum(closes[:60]) / 60
    return closes[0] > ma60, closes[0], ma60


def _sector_of(conn, code: str) -> str | None:
    r = conn.execute("SELECT sector_large FROM stock_universe WHERE stock_code=? LIMIT 1", (code,)).fetchone()
    return (r[0] or None) if r else None


def check_entry(conn, code: str, strategy: str, qty: int, price: float) -> dict:
    """신규 가상매수 허용 여부. {'allowed': bool, 'reasons': [...]}"""
    reasons: list[str] = []
    code, strategy = str(code), str(strategy or "")

    # 0) 전략 shadow(R1 승인): 신규 진입 기록 전용. 다른 가드 판정·로그는 건너뛴다(중복 방지).
    if shadow_entry(conn, code, strategy, price):
        return {"allowed": False, "reasons": [f"shadow_strategy: {strategy} 신규 진입은 기록 전용"], "shadow": True}

    # 1) 시장국면 필터
    if REGIME_FILTER() and strategy not in REGIME_EXEMPT:
        try:
            above, cur, ma = kospi_above_ma60(conn)
            if not above:
                reasons.append(f"regime_filter: KOSPI {cur:,.0f} < MA60 {ma:,.0f} — 모멘텀/돌파 계열 신규 진입 중단")
        except Exception as e:
            logger.warning(f"[가드] 국면 판정 실패(fail-open): {e}")

    # 2) 노출 한도 (전략 합산)
    if EXPOSURE_LIMIT():
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM peak_holding WHERE stock_code=? AND is_active=1", (code,)
            ).fetchone()[0]
            if n >= MAX_POS_PER_STOCK():
                reasons.append(f"exposure_stock: {code} 이미 {n}개 전략이 보유 (상한 {MAX_POS_PER_STOCK()})")
            sector = _sector_of(conn, code)
            if sector:
                tot, in_sec = conn.execute(
                    "SELECT COALESCE(SUM(ph.buy_price*ph.quantity),0), "
                    "COALESCE(SUM(CASE WHEN su.sector_large=? THEN ph.buy_price*ph.quantity END),0) "
                    "FROM peak_holding ph LEFT JOIN stock_universe su ON su.stock_code=ph.stock_code "
                    "WHERE ph.is_active=1", (sector,)
                ).fetchone()
                tot, in_sec = float(tot or 0), float(in_sec or 0)
                new_amt = float(price) * int(qty)
                # 포트폴리오가 충분히 커진 뒤(≥2천만원)에만 비중 한도를 적용(초기 1~2종목 오탐 방지)
                if tot >= 20_000_000 and (in_sec + new_amt) / (tot + new_amt) * 100 > SECTOR_CAP_PCT():
                    reasons.append(
                        f"exposure_sector: {sector} 비중 {(in_sec + new_amt) / (tot + new_amt) * 100:.0f}% > 상한 {SECTOR_CAP_PCT():.0f}%")
        except Exception as e:
            logger.warning(f"[가드] 노출 한도 판정 실패(fail-open): {e}")

    # 2b) 회전율 규칙(R3, 기본 꺼짐): 시총 하한 · 재진입 쿨다운 — research_outputs/turnover_rules_20260925.md
    if MIN_MCAP_EOK() > 0:
        try:
            r = conn.execute("SELECT market_cap FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1", (code,)).fetchone()
            if r and r[0] is not None and float(r[0]) < MIN_MCAP_EOK():
                reasons.append(f"min_mcap: 시총 {float(r[0]):,.0f}억 < 하한 {MIN_MCAP_EOK():,.0f}억")
        except Exception as e:
            logger.warning(f"[가드] 시총 하한 판정 실패(fail-open): {e}")
    if REENTRY_COOLDOWN_DAYS() > 0:
        try:
            r = conn.execute("SELECT MAX(sold_at) FROM peak_holding WHERE stock_code=? AND is_active=0", (code,)).fetchone()
            if r and r[0]:
                last = datetime.fromisoformat(str(r[0])[:19].replace("T", " "))
                days = (datetime.now() - last).days
                if days < REENTRY_COOLDOWN_DAYS():
                    reasons.append(f"reentry_cooldown: 마지막 청산 {days}일 전 < {REENTRY_COOLDOWN_DAYS()}일")
        except Exception as e:
            logger.warning(f"[가드] 재진입 쿨다운 판정 실패(fail-open): {e}")

    # 2c) 계좌 낙폭 규칙(R6): shadow는 기록만(기본), enforce는 half=격번 차단·stop=차단
    dd_mode = _account_dd_mode()
    if dd_mode != "off":
        state, dd = account_dd_state(conn)
        if state in ("half", "stop"):
            msg = f"account_dd_{state}: 가상 계좌 낙폭 {dd:+.1f}% — 신규 진입 {'50% 축소' if state == 'half' else '중단'}"
            skip = state == "stop" or (zlib.crc32(f"{code}{datetime.now():%Y%m%d}".encode()) % 2 == 0)
            if dd_mode == "enforce" and skip:
                reasons.append(msg)
            elif dd_mode == "shadow" and skip:
                _log(conn, code, strategy, f"account_dd_{state}", "shadow_would_block", msg, price=price, kospi=_kospi_snapshot(conn))

    # 3) 진입 확인(당일 등락 <+3%): shadow는 기록만, enforce는 차단
    mode = _entry_confirm_mode()
    if mode != "off" and strategy not in REGIME_EXEMPT:
        try:
            rows = conn.execute(
                "SELECT date, close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 4", (code,)
            ).fetchall()
            cl = _dedup_closes(rows)
            if len(cl) >= 2 and cl[1] > 0:
                chg = (cl[0] / cl[1] - 1) * 100
                if chg < ENTRY_CONFIRM_MIN_PCT:
                    msg = f"entry_confirm: 당일 등락 {chg:+.1f}% < +{ENTRY_CONFIRM_MIN_PCT:.0f}%"
                    if mode == "enforce":
                        reasons.append(msg)
                    else:
                        _log(conn, code, strategy, "entry_confirm", "shadow_would_block", msg,
                             price=price, kospi=_kospi_snapshot(conn))
        except Exception as e:
            logger.warning(f"[가드] 진입확인 판정 실패(무시): {e}")

    if reasons:
        # 사유마다 실제 가드명(regime_filter / exposure_stock / exposure_sector / entry_confirm)으로 한 행씩 기록
        snap = _kospi_snapshot(conn)
        for reason in reasons:
            _log(conn, code, strategy, reason.split(":", 1)[0].strip() or "entry", "blocked", reason,
                 price=price, kospi=snap)
    return {"allowed": not reasons, "reasons": reasons}


def breakeven_exit(conn, code: str, strategy: str, entry_date, buy_price: float, cur: float) -> bool:
    """+ARM% 도달 이력이 있고 현재가가 매입가 이하로 내려오면 True(청산 대상)."""
    if not BREAKEVEN_STOP() or strategy in BREAKEVEN_EXEMPT or buy_price <= 0 or cur <= 0:
        return False
    if cur > buy_price:
        return False
    try:
        r = conn.execute(
            "SELECT MAX(close) FROM price_history WHERE stock_code=? AND close>0 AND date >= ?",
            (code, str(entry_date)[:10]),
        ).fetchone()
        peak = float(r[0] or 0) if r else 0.0
    except Exception:
        return False
    return peak / buy_price - 1 >= BREAKEVEN_ARM_PCT() / 100.0
