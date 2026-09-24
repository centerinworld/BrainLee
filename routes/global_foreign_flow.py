"""
글로벌 외국인 자금 흐름 (Global Foreign Flow)

routes/global_macro.py의 범용 global_macro_data 저장소를 그대로 재사용하되,
"어느 시장에 외부 주식자금이 들어오고 빠지는가"를 비교하도록 세 축을 분리한다:

1. 국가별 자국 시장 외국인 순매수(COUNTRIES) — "그 나라 시장에 외국인 돈이 들어왔는가"
   (KRX/NSDL 등 각국 거래소 실측치, 일별)
2. TIC(미 재무부) 국가별 대미 주식 양자간 자금흐름(TIC_COUNTRIES) — "그 나라 투자자가
   미국 주식을 얼마나 순매수했는가" (FRED가 미러링하는 공식 통계, 월별)
3. ECB 유로존 포트폴리오 주식·펀드지분 부채 거래 — "유로존으로 외부 자금이 들어왔는가"

서로 다른 통계의 출발 투자자를 연결할 수 없으므로 특정 지역에서 빠진 동일 자금이
다른 지역으로 이동했다고 단정하지 않는다. 공통 월 구간의 목적지별 유입 압력을
동시에 비교하되 범위와 커버리지를 항상 함께 반환한다.

데이터 신뢰도(confidence)는 실제 수집기 구현/접근성 조사 결과를 그대로 반영한다
(2026-09-08 조사, 상세는 CLAUDE.md 변경이력 참조):
  HIGH    — 실제 공식 소스에서 정기 수집 중
  PENDING — 코드는 있으나 설정 대기 또는 미구현
  BLOCKED — 소스 접근 자체가 막힘
가짜 값으로 채우지 않고, 데이터 없는 국가는 값 없이 상태만 정직하게 반환한다.
"""
from db_compat import connect_primary_db
import sqlite3 as _sl
from datetime import datetime, timedelta

from fastapi import APIRouter

router = APIRouter()

# ── 1. 자국 시장 외국인 순매수 (일/주별, 각국 거래소 실측) ──────────────────────
COUNTRIES = [
    {"code": "KR", "label": "한국", "flag": "🇰🇷", "indicator": "KR_FOREIGN_FLOW_USD",
     "freq": "DAILY", "confidence": "HIGH", "note": "KRX/KIS 실측 — price_history 외국인 순매수 집계"},
    {"code": "TW", "label": "대만", "flag": "🇹🇼", "indicator": "TW_FOREIGN_FLOW_USD",
     "freq": "DAILY", "confidence": "BLOCKED", "note": "TWSE 접속 차단(WAF 307) — 대체 소스 확보 전까지 수집 불가"},
    {"code": "JP", "label": "일본", "flag": "🇯🇵", "indicator": "JP_FOREIGN_FLOW_USD",
     "freq": "WEEKLY", "confidence": "HIGH", "note": "MOF(재무성) 공개 주간 CSV 실측 — 대내증권투자 주식 순매수"},
    {"code": "CN", "label": "중국(북향자금)", "flag": "🇨🇳", "indicator": "CN_NORTHBOUND_FLOW_USD",
     "freq": "DAILY", "confidence": "PENDING", "note": "HKEX 순매수 데이터 엔드포인트 미확정 — 수집기 미구현"},
    {"code": "IN", "label": "인도", "flag": "🇮🇳", "indicator": "IN_FPI_FLOW_USD",
     "freq": "DAILY", "confidence": "HIGH", "note": "NSDL 공개 리포트, 주식(Equity) 순투자 기준"},
    {"code": "HK", "label": "홍콩(남향자금)", "flag": "🇭🇰", "indicator": "HK_MAINLAND_SOUTHBOUND_FLOW_USD",
     "freq": "DAILY", "confidence": "HIGH",
     "note": "HKEX 공식 SH+SZ Stock Connect — 중국 본토 투자자의 홍콩주식 순매수 프록시(전체 외국인 수급 아님)"},
]

# ── 2. TIC 국가별 대미 주식 양자간 자금흐름 ────────────────────────────────
# WFE/SIFMA의 대형 주식시장과 한국 투자 판단 관련성을 함께 반영한 20개국이다.
# 미국은 목적지이므로 국가 목록에서 제외하고 TIC_US_FLOW_ALL 합계 카드로 표시한다.
TIC_COUNTRIES = [
    {"code": "CN", "label": "중국(본토)", "flag": "🇨🇳", "region": "ASIA", "indicator": "TIC_US_FLOW_CN"},
    {"code": "JP", "label": "일본",       "flag": "🇯🇵", "region": "ASIA", "indicator": "TIC_US_FLOW_JP"},
    {"code": "HK", "label": "홍콩",       "flag": "🇭🇰", "region": "ASIA", "indicator": "TIC_US_FLOW_HK"},
    {"code": "IN", "label": "인도",       "flag": "🇮🇳", "region": "ASIA", "indicator": "TIC_US_FLOW_IN"},
    {"code": "GB", "label": "영국",       "flag": "🇬🇧", "region": "EUROPE", "indicator": "TIC_US_FLOW_GB"},
    {"code": "CA", "label": "캐나다",     "flag": "🇨🇦", "region": "AMERICAS", "indicator": "TIC_US_FLOW_CA"},
    {"code": "SA", "label": "사우디아라비아", "flag": "🇸🇦", "region": "MEA", "indicator": "TIC_US_FLOW_SA"},
    {"code": "FR", "label": "프랑스",     "flag": "🇫🇷", "region": "EUROPE", "indicator": "TIC_US_FLOW_FR"},
    {"code": "DE", "label": "독일",       "flag": "🇩🇪", "region": "EUROPE", "indicator": "TIC_US_FLOW_DE"},
    {"code": "NL", "label": "네덜란드",   "flag": "🇳🇱", "region": "EUROPE", "indicator": "TIC_US_FLOW_NL"},
    {"code": "ES", "label": "스페인",     "flag": "🇪🇸", "region": "EUROPE", "indicator": "TIC_US_FLOW_ES"},
    {"code": "SE", "label": "스웨덴",     "flag": "🇸🇪", "region": "EUROPE", "indicator": "TIC_US_FLOW_SE"},
    {"code": "CH", "label": "스위스",     "flag": "🇨🇭", "region": "EUROPE", "indicator": "TIC_US_FLOW_CH"},
    {"code": "AU", "label": "호주",       "flag": "🇦🇺", "region": "ASIA_PAC", "indicator": "TIC_US_FLOW_AU"},
    {"code": "KR", "label": "한국",       "flag": "🇰🇷", "region": "ASIA", "indicator": "TIC_US_FLOW_KR"},
    {"code": "TW", "label": "대만",       "flag": "🇹🇼", "region": "ASIA", "indicator": "TIC_US_FLOW_TW"},
    {"code": "IT", "label": "이탈리아",   "flag": "🇮🇹", "region": "EUROPE", "indicator": "TIC_US_FLOW_IT"},
    {"code": "ZA", "label": "남아공",     "flag": "🇿🇦", "region": "MEA", "indicator": "TIC_US_FLOW_ZA"},
    {"code": "AE", "label": "UAE",        "flag": "🇦🇪", "region": "MEA", "indicator": "TIC_US_FLOW_AE"},
    {"code": "BR", "label": "브라질",     "flag": "🇧🇷", "region": "AMERICAS", "indicator": "TIC_US_FLOW_BR"},
]


def _conn():
    c = connect_primary_db(timeout=30)
    c.row_factory = _sl.Row
    return c


def _series_since(conn, indicator: str, since: str):
    rows = conn.execute(
        "SELECT date, value FROM global_macro_data WHERE indicator_code=? AND date>=? ORDER BY date",
        (indicator, since),
    ).fetchall()
    return [{"date": str(r["date"])[:10], "value": float(r["value"])} for r in rows if r["value"] is not None]


def _latest(conn, indicator: str):
    row = conn.execute(
        "SELECT date, value FROM global_macro_data WHERE indicator_code=? ORDER BY date DESC LIMIT 1",
        (indicator,),
    ).fetchone()
    if not row or row["value"] is None:
        return None, None
    return str(row["date"])[:10], float(row["value"])


def _last_n_months(conn, indicator: str, n: int = 3):
    rows = conn.execute(
        "SELECT date, value FROM global_macro_data WHERE indicator_code=? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (indicator, n),
    ).fetchall()
    return [{"date": str(r["date"])[:10], "value": float(r["value"])} for r in reversed(rows)]


def _month_window(end_month: str, count: int = 3) -> list[str]:
    year, month = map(int, end_month[:7].split("-"))
    result = []
    for offset in range(count - 1, -1, -1):
        index = year * 12 + month - 1 - offset
        result.append(f"{index // 12:04d}-{index % 12 + 1:02d}")
    return result


def _monthly_totals(conn, indicator: str, months: list[str]) -> dict[str, float]:
    if not months:
        return {}
    rows = _series_since(conn, indicator, f"{months[0]}-01")
    allowed = set(months)
    totals: dict[str, float] = {}
    for row in rows:
        month = row["date"][:7]
        if month in allowed:
            totals[month] = totals.get(month, 0.0) + row["value"]
    return totals


def _destination_snapshot(conn, us_latest_date: str | None, europe_latest_date: str | None) -> dict:
    available_ends = [d[:7] for d in (us_latest_date, europe_latest_date) if d]
    if len(available_ends) < 2:
        return {"common_period": None, "destinations": [], "warning": "미국·유럽 공통 월 데이터가 부족합니다."}

    months = _month_window(min(available_ends), 3)
    us_values = _monthly_totals(conn, "TIC_US_FLOW_ALL", months)
    europe_values = _monthly_totals(conn, "EURO_AREA_EQUITY_FUND_INFLOW_USD", months)

    asia_total = 0.0
    asia_included = []
    asia_partial = []
    for country in COUNTRIES:
        values = _monthly_totals(conn, country["indicator"], months)
        if all(month in values for month in months):
            asia_total += sum(values[month] for month in months)
            asia_included.append(country["code"])
        elif values:
            asia_partial.append(country["code"])

    def complete_sum(values: dict[str, float]) -> float | None:
        return round(sum(values[m] for m in months), 1) if all(m in values for m in months) else None

    coverage = len(asia_included) / len(COUNTRIES) if COUNTRIES else 0.0
    return {
        "common_period": f"{months[0]}~{months[-1]}",
        "comparison_grade": "PARTIAL" if coverage < 0.8 else "COMPARABLE",
        "destinations": [
            {
                "code": "ASIA_SAMPLE", "label": "아시아 표본시장", "flag": "🌏",
                "value_usd_million": round(asia_total, 1) if asia_included else None,
                "confidence": "PARTIAL", "coverage": f"{len(asia_included)}/{len(COUNTRIES)}개국 완전 포함",
                "included": asia_included, "partial": asia_partial,
                "definition": "공식 자국시장 외국인·국경간 순매수 중 공통 3개월이 모두 있는 국가만 합산",
            },
            {
                "code": "US", "label": "미국시장", "flag": "🇺🇸",
                "value_usd_million": complete_sum(us_values), "confidence": "HIGH", "coverage": "전세계",
                "definition": "미 재무부 TIC: 외국인의 미국 주식 순매수",
            },
            {
                "code": "EUROPE", "label": "유로존", "flag": "🇪🇺",
                "value_usd_million": complete_sum(europe_values), "confidence": "HIGH", "coverage": "유로존 21개국",
                "definition": "ECB: 비거주자의 유로존 주식·투자펀드 지분 순취득(부채 거래)",
            },
        ],
        "warning": "지역별 유입 압력의 동시 비교이며, 동일 자금의 출발지→도착지 이동 경로를 증명하지 않습니다.",
    }


@router.get("/summary")
def get_summary():
    conn = _conn()
    try:
        as_of = datetime.now().strftime("%Y-%m-%d")
        d30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        d90 = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        # ── 1. 자국 시장 외국인 순매수 ──
        countries = []
        cum30_have_data = []
        for c in COUNTRIES:
            latest_date, latest_value = _latest(conn, c["indicator"])
            s30 = _series_since(conn, c["indicator"], d30)
            s90 = _series_since(conn, c["indicator"], d90)
            cum30 = round(sum(p["value"] for p in s30), 1) if s30 else None
            cum90 = round(sum(p["value"] for p in s90), 1) if s90 else None
            has_data = latest_value is not None
            countries.append({
                **{k: v for k, v in c.items() if k != "indicator"},
                "has_data": has_data,
                "latest_date": latest_date,
                "latest_flow_usd_million": round(latest_value, 1) if has_data else None,
                "cum_30d_usd_million": cum30,
                "cum_90d_usd_million": cum90,
            })
            if has_data and cum30 is not None:
                cum30_have_data.append(cum30)

        asia_total_cum_30d = round(sum(cum30_have_data), 1) if cum30_have_data else None
        countries_with_data = sum(1 for c in countries if c["has_data"])

        # ── 2. TIC 대미 양자간 흐름 ──
        tic_all_date, tic_all_latest = _latest(conn, "TIC_US_FLOW_ALL")
        euro_date, _euro_latest = _latest(conn, "EURO_AREA_EQUITY_FUND_INFLOW_USD")
        tic_asia_date, tic_asia_latest = _latest(conn, "TIC_US_FLOW_ASIA_TOTAL")
        tic_europe_date, tic_europe_latest = _latest(conn, "TIC_US_FLOW_EUROPE_TOTAL")
        tic_asia_3m = _last_n_months(conn, "TIC_US_FLOW_ASIA_TOTAL", 3)
        tic_europe_3m = _last_n_months(conn, "TIC_US_FLOW_EUROPE_TOTAL", 3)
        tic_asia_3m_sum = round(sum(p["value"] for p in tic_asia_3m), 1) if tic_asia_3m else None
        tic_europe_3m_sum = round(sum(p["value"] for p in tic_europe_3m), 1) if tic_europe_3m else None

        tic_by_country = []
        for c in TIC_COUNTRIES:
            latest_date, latest_value = _latest(conn, c["indicator"])
            m3 = _last_n_months(conn, c["indicator"], 3)
            m3_sum = round(sum(p["value"] for p in m3), 1) if m3 else None
            tic_by_country.append({
                "code": c["code"], "label": c["label"], "flag": c["flag"], "region": c["region"],
                "has_data": latest_value is not None,
                "latest_date": latest_date,
                "latest_usd_million": round(latest_value, 1) if latest_value is not None else None,
                "sum_3m_usd_million": m3_sum,
            })
        tic_by_country.sort(
            key=lambda x: x["sum_3m_usd_million"] if x["sum_3m_usd_million"] is not None else float("-inf"),
            reverse=True,
        )

        # 서로 다른 통계의 기간을 공통 3개월로 맞춘 목적지별 동시 비교다.
        destination_snapshot = _destination_snapshot(conn, tic_all_date, euro_date)
        observations = []
        destinations = {item["code"]: item for item in destination_snapshot["destinations"]}
        asia_value = destinations.get("ASIA_SAMPLE", {}).get("value_usd_million")
        us_value = destinations.get("US", {}).get("value_usd_million")
        eu_value = destinations.get("EUROPE", {}).get("value_usd_million")
        if all(value is not None for value in (asia_value, us_value, eu_value)):
            observations.append(
                f"공통 기간 {destination_snapshot['common_period']}에 아시아 표본 ${asia_value:,.0f}백만, "
                f"미국 ${us_value:,.0f}백만, 유로존 ${eu_value:,.0f}백만의 외부 주식자금 흐름이 동시 관측됐습니다. "
                "이는 목적지별 방향 비교이며 아시아 자금이 미국 또는 유럽으로 이동했다는 경로 증명은 아닙니다."
            )

        # ── 맥락: 달러 강세/VIX/미국채10년 ──
        dxy_date, dxy_now = _latest(conn, "US_DXY")
        dxy_series = _series_since(conn, "US_DXY", d30)
        dxy_chg_30d = None
        if dxy_series and dxy_now is not None and dxy_series[0]["value"]:
            dxy_chg_30d = round((dxy_now - dxy_series[0]["value"]) / dxy_series[0]["value"] * 100, 2)
        _, vix_now = _latest(conn, "US_VIX")
        _, ust10y_now = _latest(conn, "US_10Y_YIELD_YH")
        if ust10y_now is None:
            _, ust10y_now = _latest(conn, "US_10Y_YIELD")

        return {
            "as_of": as_of,
            "countries": countries,
            "countries_with_data": countries_with_data,
            "countries_total": len(countries),
            "asia_total_cum_30d_usd_million": asia_total_cum_30d,
            "asia_outflow": (asia_total_cum_30d is not None and asia_total_cum_30d < 0),
            "destination_snapshot": destination_snapshot,
            "us_inbound": {
                "note": "미 재무부 TIC(FRED 미러링), 월별, 공식발표 특성상 약 2~3개월 지연",
                "market_scope": "미국을 제외한 대형 주식시장 20개국",
                "market_scope_note": "미국은 목적지이며 전세계→미국 합계로 별도 표시; 거래소 시총과 한국시장 관련성을 기준으로 구성",
                "as_of_month": tic_all_date,
                "all_countries_usd_million": round(tic_all_latest, 1) if tic_all_latest is not None else None,
                "asia_total": {
                    "latest_month": tic_asia_date,
                    "latest_usd_million": round(tic_asia_latest, 1) if tic_asia_latest is not None else None,
                    "sum_3m_usd_million": tic_asia_3m_sum,
                },
                "europe_total": {
                    "latest_month": tic_europe_date,
                    "latest_usd_million": round(tic_europe_latest, 1) if tic_europe_latest is not None else None,
                    "sum_3m_usd_million": tic_europe_3m_sum,
                },
                "by_country": tic_by_country,
            },
            "observations": observations,
            "context": {
                "dxy_date": dxy_date,
                "dxy_value": round(dxy_now, 2) if dxy_now is not None else None,
                "dxy_chg_30d_pct": dxy_chg_30d,
                "vix_value": round(vix_now, 2) if vix_now is not None else None,
                "ust10y_value": round(ust10y_now, 2) if ust10y_now is not None else None,
            },
        }
    finally:
        conn.close()


@router.get("/history")
def get_history(days: int = 180):
    conn = _conn()
    try:
        since = (datetime.now() - timedelta(days=max(30, min(days, 1095)))).strftime("%Y-%m-%d")
        series = {}
        for c in COUNTRIES:
            series[c["code"]] = _series_since(conn, c["indicator"], since)
        return {"since": since, "series": series, "countries": COUNTRIES}
    finally:
        conn.close()


@router.get("/us-inbound-history")
def get_us_inbound_history(months: int = 24):
    """TIC 국가별 대미 자금흐름 월별 시계열(라인/스택 차트용)."""
    conn = _conn()
    try:
        since = (datetime.now() - timedelta(days=max(90, min(months, 60)) * 31)).strftime("%Y-%m-%d")
        series = {
            "ALL": _series_since(conn, "TIC_US_FLOW_ALL", since),
            "ASIA_TOTAL": _series_since(conn, "TIC_US_FLOW_ASIA_TOTAL", since),
            "EUROPE_TOTAL": _series_since(conn, "TIC_US_FLOW_EUROPE_TOTAL", since),
        }
        for c in TIC_COUNTRIES:
            series[c["code"]] = _series_since(conn, c["indicator"], since)
        return {"since": since, "series": series, "countries": TIC_COUNTRIES}
    finally:
        conn.close()
