"""
섹터 로테이션 조기 포착 시스템
주도섹터를 RS + 거래량확장 + 기관수급 + 수출선행지표로 감지
"""
from fastapi import APIRouter
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from trading_calendar import is_kr_trading_day

router = APIRouter()
DB = "stock.db"

def _conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _ensure_cache_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sector_rotation_cache (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            as_of TEXT NOT NULL,
            market_status TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )


def _market_status_now():
    now = datetime.now()
    if not is_kr_trading_day(now.date()):
        return "closed"
    hm = now.hour * 100 + now.minute
    if 900 <= hm < 1540:
        return "intraday"
    return "closed"


def _cache_meta(as_of, market_status):
    return {
        "cached": True,
        "as_of": as_of,
        "market_status": market_status,
        "market_status_label": "장중 1시간 갱신" if market_status == "intraday" else "장마감 기준",
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _read_cache(conn, cache_key):
    _ensure_cache_table(conn)
    row = conn.execute(
        "SELECT payload_json FROM sector_rotation_cache WHERE cache_key=?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def _write_cache(conn, cache_key, payload, as_of, market_status):
    _ensure_cache_table(conn)
    conn.execute(
        """
        INSERT INTO sector_rotation_cache
            (cache_key, payload_json, as_of, market_status, computed_at, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(cache_key) DO UPDATE SET
            payload_json=excluded.payload_json,
            as_of=excluded.as_of,
            market_status=excluded.market_status,
            computed_at=excluded.computed_at,
            updated_at=datetime('now')
        """,
        (
            cache_key,
            json.dumps(payload, ensure_ascii=False),
            as_of,
            market_status,
            payload.get("meta", {}).get("computed_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def refresh_sector_rotation_cache(force: bool = True):
    """섹터 로테이션 화면용 값을 한 번에 계산해 캐시한다."""
    conn = _conn()
    try:
        _ensure_cache_table(conn)
        as_of = _last_trade_date(conn)
        market_status = _market_status_now()
        meta = _cache_meta(as_of, market_status)

        scores = _compute_sector_scores(conn, as_of)
        leadership = _compute_sector_leadership(conn, as_of=as_of, months=36, top_n=3)
        rotation_map = _compute_rotation_map(conn, as_of=as_of)
        for payload in (scores, leadership, rotation_map):
            payload["meta"] = dict(meta)

        _write_cache(conn, "scores", scores, as_of, market_status)
        _write_cache(conn, "leadership", leadership, as_of, market_status)
        _write_cache(conn, "rotation-map", rotation_map, as_of, market_status)
        conn.commit()
        return {
            "ok": True,
            "as_of": as_of,
            "market_status": market_status,
            "computed_at": meta["computed_at"],
            "counts": {
                "scores": len(scores.get("sectors", [])),
                "leadership": len(leadership.get("sectors", [])),
                "rotation_map": len(rotation_map.get("sectors", [])),
            },
        }
    finally:
        conn.close()

# ── 섹터 종목 그룹 ─────────────────────────────────────────────────────
# 검증 기준: stock_universe.sector_large 확인, 우선주/_skip 제외
# 수출 HS코드 실증: 변압기(8504) 2023 YoY+20~44% → 주가급등 1~3개월 전 선행 신호 확인
SECTOR_GROUPS = {
    "전력기기": {
        # HD현대일렉트릭(산업재), LS ELECTRIC(산업재), 효성중공업(산업재),
        # 일진전기(산업재), 제룡전기(산업재), 광명전기(산업재)
        # 가온전선(산업재), 대한전선(산업재)
        # StockEasy 2026-06-28 기준 제거: 두산에너빌리티(034020)=원자력
        "codes": ["267260","010120","298040","017040","103590","033100","000500","001440"],
        # HS8504=변압기/전력변환기, HS8535/8537=개폐기/차단기, HS8544=전선·케이블
        "hs_codes": ["8504","8535","8537","8544"],
        "hs_keys": [],
        "label": "⚡ 전력기기",
        "color": "#3b82f6",
    },
    "원자력": {
        # StockEasy middle=원자력 기준
        "codes": ["034020","052690","051600","105840","032820","046120","094820","457550","126720"],
        "hs_keys": [],
        "label": "☢ 원자력",
        "color": "#10b981",
    },
    "화장품/뷰티": {
        # 코스메카코리아(필수소비재), LG생활건강(필수소비재), 아모레퍼시픽(필수소비재)
        # 에이블씨엔씨(필수소비재), 한국콜마(필수소비재), 코리아나(필수소비재)
        # StockEasy 2026-06-28 기준 제거: 클래시스(214150)=의료기기
        "codes": ["241710","051900","090430","078520","161890","027050","003350"],
        "hs_keys": ["public:23:9"],   # 화장품 HS3304
        "label": "🧴 화장품/뷰티",
        "color": "#a855f7",
    },
    "의료기기/미용": {
        # StockEasy middle=의료기기/미용기기 기준
        "codes": ["214150","214450","278470","336570","149980","145020"],
        "hs_keys": [],
        "label": "🩺 의료기기/미용",
        "color": "#ec4899",
    },
    "반도체": {
        # 삼성전자, SK하이닉스, 한미반도체, 하나머티리얼즈, 원익IPS,
        # 리노공업, 삼성전기, 솔브레인, 테크윙, 이오테크닉스
        # StockEasy 2026-06-28 기준 교체: 솔브레인홀딩스(036830)=지주사 → 솔브레인(357780)=반도체소재
        "codes": ["005930","000660","042700","166090","240810","058470","009150","357780","089030","039030"],
        "hs_keys": ["public:23:4","public:23:5"],  # 메모리+시스템반도체 HS8542
        "label": "🔬 반도체",
        "color": "#f59e0b",
    },
    "기판패키지": {
        # HBM·첨단패키지 기판 수혜주: 심텍(IT), 대덕전자(IT), ISC(IT), 코리아써키트(IT)
        # 이수페타시스(반도체소재), 해성디에스(IT=리드프레임)
        # 2024~2025 HBM 호황 직접 수혜, 반도체 섹터와 함께 움직이나 별도 포착 필요
        # StockEasy 2026-06-28 기준 교체: 인터플렉스(051370)=SW/AI → 이수페타시스(007660)=반도체소재
        "codes": ["222800","353200","095340","007810","007660","195870"],
        "hs_keys": ["public:23:4"],  # HS8542 반도체 수출 공동 선행지표
        "label": "🔌 기판/패키지",
        "color": "#8b5cf6",
    },
    "2차전지": {
        # StockEasy 2026-06-28 기준: 양극재/배터리셀 중심
        # 제거: LG화학(051910)=정유/화학, SK이노베이션(096770)=정유/화학
        "codes": ["247540","006400","373220","086520","066970","003670"],
        "hs_keys": ["public:23:3"],   # 이차전지 HS850760
        "label": "🔋 2차전지",
        "color": "#22c55e",
    },
    "방산": {
        # 한국항공우주(산업재), 한화에어로스페이스(산업재), 한화시스템(산업재)
        # 현대로템(산업재), 한화(산업재), LIG디펜스앤에어로스페이스(산업재)
        "codes": ["047810","012450","272210","064350","000880","079550"],
        "hs_keys": [],
        "label": "🛡 방산",
        "color": "#ef4444",
    },
    "조선": {
        # HD한국조선해양, HD현대중공업, 삼성중공업, 한화오션
        # 제거: 현대건설(000720, 건설)
        "codes": ["009540","329180","010140","042660"],
        "hs_keys": ["public:23:7"],   # 선박 HS8901
        "label": "🚢 조선",
        "color": "#06b6d4",
    },
    "바이오": {
        # 삼성바이오로직스, 셀트리온, 유한양행, SK바이오팜, SK바이오사이언스
        # 알테오젠, 에이비엘바이오, 리가켐바이오
        # StockEasy 2026-06-28 기준 제거: 091990(미관측/상폐성), 263750=펄어비스(게임), 휴젤(145020)=의료기기
        "codes": ["207940","068270","000100","326030","302440","196170","298380","141080"],
        "hs_keys": ["public:23:16"],  # 바이오의약품 HS3002
        "label": "🧬 바이오",
        "color": "#f472b6",
    },
}

def _valid_codes(codes):
    return [c for c in codes if not c.endswith("_skip") and len(c) == 6]

def _get_price_returns(conn, codes, date_from, date_to):
    """기간 내 종목별 수익률 → 섹터 평균"""
    if not codes:
        return None
    ph = conn.execute(
        f"""SELECT stock_code, date, close FROM price_history
        WHERE stock_code IN ({','.join('?'*len(codes))})
        AND date >= ? AND date <= ? AND close > 0
        ORDER BY stock_code, date""",
        codes + [date_from, date_to]
    ).fetchall()
    by_code = defaultdict(list)
    for r in ph:
        by_code[r[0]].append((r[1], r[2]))
    rets = []
    for code, data in by_code.items():
        if len(data) >= 3:
            rets.append((data[-1][1] - data[0][1]) / data[0][1] * 100)
    return sum(rets)/len(rets) if rets else None

def _get_sector_volume_ratio(conn, codes, as_of):
    """현재 10일 평균 거래량 / 60일 평균 거래량"""
    if not codes:
        return 1.0
    rows = conn.execute(
        f"""SELECT stock_code, date, volume FROM price_history
        WHERE stock_code IN ({','.join('?'*len(codes))})
        AND date <= ? AND date >= date(?, '-90 days')
        AND volume > 0 ORDER BY stock_code, date""",
        codes + [as_of, as_of]
    ).fetchall()
    by_code = defaultdict(list)
    for r in rows:
        by_code[r[0]].append(r[2])
    ratios = []
    for code, vols in by_code.items():
        if len(vols) >= 20:
            recent10 = sum(vols[-10:]) / 10
            base60 = sum(vols[:60]) / min(len(vols), 60)
            if base60 > 0:
                ratios.append(recent10 / base60)
    return sum(ratios)/len(ratios) if ratios else 1.0

def _get_sector_breadth(conn, codes, as_of):
    """섹터 내 52주 신고가 대비 5% 이내 종목 비율"""
    if not codes:
        return 0.0
    rows = conn.execute(
        f"""SELECT stock_code, date, close, high FROM price_history
        WHERE stock_code IN ({','.join('?'*len(codes))})
        AND date <= ? AND date >= date(?, '-365 days')
        AND close > 0 ORDER BY stock_code, date""",
        codes + [as_of, as_of]
    ).fetchall()
    by_code = defaultdict(list)
    for r in rows:
        by_code[r[0]].append((r[1], r[2], r[3]))
    near_high = 0
    total = 0
    for code, data in by_code.items():
        if len(data) < 60:
            continue
        cur = data[-1][1]
        high52 = max(r[2] or r[1] for r in data)
        if high52 > 0:
            total += 1
            if cur >= high52 * 0.90:
                near_high += 1
    return near_high / total if total > 0 else 0.0

def _get_sector_investor_flow(conn, codes, as_of, days=90):
    """섹터 3개월 기관+외국인 순매수 (억원) — price_history 기반

    kiwoom_investor_daily는 매수금액(buy-only) 버그(trde_tp='1')로 순매수 보완 불가했음
    (2026-07-21 trde_tp='0'으로 수정+재수집 완료 — 소형주 보완용으로 향후 활용 가능,
    이 함수는 price_history를 1순위 소스로 유지).
    price_history에 실값이 없는 종목은 0으로 집계됨.
    반환: (frn_순매수합_억원, inst_순매수합_억원)
    """
    if not codes:
        return 0.0, 0.0
    d_from = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")

    # price_history: 금액 실값 우선, 2020~2022 보강 구간은 수량*종가로 환산
    rows = conn.execute(
        f"""SELECT
            SUM(CASE WHEN COALESCE(frn_net_buy_amt,0) != 0
                THEN frn_net_buy_amt/100.0
                ELSE COALESCE(frn_net_buy,0) * COALESCE(close,0) / 100000000.0 END),
            SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0
                THEN inst_net_buy_amt/100.0
                ELSE COALESCE(inst_net_buy,0) * COALESCE(close,0) / 100000000.0 END)
        FROM price_history
        WHERE stock_code IN ({','.join('?'*len(codes))})
        AND date >= ? AND date <= ?
        AND (inst_net_buy_amt != 0 OR frn_net_buy_amt != 0 OR inst_net_buy != 0 OR frn_net_buy != 0)""",
        codes + [d_from, as_of]
    ).fetchone()

    frn_sum = float(rows[0] or 0)
    inst_sum = float(rows[1] or 0)
    return frn_sum, inst_sum


def _get_hs_export_yoy(sector_info, as_of):
    """수출 YoY — quant_major_indicator_series 또는 hs_trade_lab DB 직접 조회
    전력기기는 hs_codes(8504/8535/8544)로 hs_trade_lab DB에서 직접 조회
    """
    hs_direct = sector_info.get("hs_codes", [])
    hs_keys = sector_info.get("hs_keys", [])
    as_of_ym = as_of[:7]  # 'YYYY-MM'
    prev_ym = f"{int(as_of_ym[:4])-1}{as_of_ym[4:]}"

    if hs_direct:
        try:
            hs_conn = sqlite3.connect("hs_trade_lab/data/hs_trade_lab.db")
            placeholders = " OR ".join(f"hs_code LIKE '{c}%'" for c in hs_direct)
            # 최근 수출값이 있는 월 기준으로 YoY 계산 (당월 미수집 0값 제외)
            latest = hs_conn.execute(
                f"""
                SELECT MAX(period_ym)
                FROM customs_monthly_record
                WHERE ({placeholders})
                  AND period_ym LIKE '____-__'
                  AND export_value > 0
                """
            ).fetchone()[0]
            if not latest:
                hs_conn.close()
                return None
            # 최근 3개월 합산 vs 전년동기 3개월 합산
            y = int(latest[:4])
            m = int(latest[5:7])
            cur_months = [f"{y if m-i>=1 else y-1}-{(m-i) if m-i>=1 else m-i+12:02d}" for i in range(3)]
            prv_months = [f"{y-1 if m-i>=1 else y-2}-{(m-i) if m-i>=1 else m-i+12:02d}" for i in range(3)]
            cur = sum(hs_conn.execute(
                f"SELECT COALESCE(SUM(export_value),0) FROM customs_monthly_record WHERE ({placeholders}) AND period_ym IN ({','.join('?'*3)})",
                cur_months
            ).fetchone()[0] or 0 for _ in [1])
            prv = sum(hs_conn.execute(
                f"SELECT COALESCE(SUM(export_value),0) FROM customs_monthly_record WHERE ({placeholders}) AND period_ym IN ({','.join('?'*3)})",
                prv_months
            ).fetchone()[0] or 0 for _ in [1])
            hs_conn.close()
            if prv > 0:
                return (cur - prv) / prv * 100
        except Exception:
            pass
        return None

    if not hs_keys:
        return None

    # quant_major_indicator_series 사용: 최근 유효 3개월 평균 vs 전년 동일 3개월 평균
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        f"""SELECT period, SUM(value) as val FROM quant_major_indicator_series
        WHERE indicator_key IN ({','.join('?'*len(hs_keys))})
        AND period <= ? GROUP BY period ORDER BY period DESC LIMIT 24""",
        hs_keys + [as_of_ym]
    ).fetchall()
    conn.close()
    lookup = {r[0]: float(r[1] or 0) for r in rows if r[0] and len(r[0]) == 7}
    latest = next((r[0] for r in rows if float(r[1] or 0) > 0), None)
    if not latest:
        return None
    y = int(latest[:4])
    m = int(latest[5:7])
    cur_months = [f"{y if m-i>=1 else y-1}-{(m-i) if m-i>=1 else m-i+12:02d}" for i in range(3)]
    prv_months = [f"{y-1 if m-i>=1 else y-2}-{(m-i) if m-i>=1 else m-i+12:02d}" for i in range(3)]
    pairs = [(lookup.get(c, 0), lookup.get(p, 0)) for c, p in zip(cur_months, prv_months)]
    pairs = [(cur, prv) for cur, prv in pairs if cur > 0 and prv > 0]
    if not pairs:
        return None
    cur_avg = sum(cur for cur, _ in pairs) / len(pairs)
    prev_avg = sum(prv for _, prv in pairs) / len(pairs)
    return (cur_avg - prev_avg) / prev_avg * 100

def _get_sector_earnings_yoy(conn, codes, as_of):
    """최근 공시 가능한 분기 영업이익 YoY (공시 지연 45일 감안)"""
    dt = datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=45)
    avail_year = dt.year
    avail_q = (dt.month - 1) // 3
    if avail_q == 0:
        avail_q = 4
        avail_year -= 1

    best_yoy = None
    for offset in [0, 1]:
        q = avail_q - offset
        y = avail_year
        if q <= 0:
            q += 4
            y -= 1
        cur = conn.execute(
            f"""SELECT AVG(operating_profit) FROM financial_data
            WHERE stock_code IN ({','.join('?'*len(codes))})
            AND year=? AND quarter=? AND is_annual=0
            AND operating_profit IS NOT NULL AND operating_profit > 0""",
            codes + [y, q]
        ).fetchone()[0]
        prv = conn.execute(
            f"""SELECT AVG(operating_profit) FROM financial_data
            WHERE stock_code IN ({','.join('?'*len(codes))})
            AND year=? AND quarter=? AND is_annual=0
            AND operating_profit IS NOT NULL AND operating_profit > 0""",
            codes + [y - 1, q]
        ).fetchone()[0]
        if cur and prv and prv > 0:
            yoy = (cur - prv) / prv * 100
            if best_yoy is None or yoy > best_yoy:
                best_yoy = yoy
    return best_yoy


def _score_sector(conn, sect_key, sector_info, as_of):
    """섹터 종합 스코어 계산 (0~100점)

    데이터 기반 선행 신호 중심 (검증된 3개 섹터 실증 분석 반영):
    - 화장품: 기관 선행 매수 2~3개월 전
    - 반도체: 외국인 대규모 선행 매수
    - 전력기기: 실적 YoY 반전 + 거래량 선행
    """
    codes = _valid_codes(sector_info["codes"])
    score = 0
    detail = {}
    dt = datetime.strptime(as_of, "%Y-%m-%d")

    # ── 1+2. 수급 3개월 (외국인 35점 + 기관 30점) ──────────────────────
    # price_history 실값 우선, 소형주는 kiwoom_investor_daily 보완
    frn_3m, inst_3m = _get_sector_investor_flow(conn, codes, as_of, days=90)
    detail["frn_3m_억"] = round(frn_3m)
    detail["inst_3m_억"] = round(inst_3m)

    # 외국인 점수 (최대 40점 — 규모 구간 세분화)
    # 실증 데이터: 2024-01 반도체 외인 +66,404억 → 이후 HBM 폭등 선행신호였음
    if frn_3m > 30000:   score += 40   # 초대규모 (삼성전자/SK하이닉스급)
    elif frn_3m > 10000: score += 35
    elif frn_3m > 5000:  score += 30
    elif frn_3m > 1500:  score += 22
    elif frn_3m > 500:   score += 14
    elif frn_3m > 100:   score += 7
    elif frn_3m > 0:     score += 3
    elif frn_3m < -5000: score -= 10
    elif frn_3m < -1000: score -= 5

    # 기관 점수 (최대 35점 — 규모 구간 세분화)
    # 실증: 2025-03 반도체 기관 +2,067억, 2025-10 기관 +32,751억
    if inst_3m > 20000:  score += 35
    elif inst_3m > 8000: score += 30
    elif inst_3m > 2000: score += 22
    elif inst_3m > 700:  score += 15
    elif inst_3m > 200:  score += 8
    elif inst_3m > 0:    score += 3
    elif inst_3m < -5000: score -= 10
    elif inst_3m < -500:  score -= 5

    # 스마트머니 역행 매수 패턴 보너스 (최대 +10점)
    # 역행패턴 = 한 주체가 대규모 순매수하는 반면 다른 주체는 매도 → 선행 신호
    if frn_3m > 3000 and inst_3m < -500:
        detail["pattern"] = "외국인선행★"
        score += 10   # 외인 대규모 선취 매수 (더 강한 선행 신호)
    elif frn_3m > 500 and inst_3m < -500:
        detail["pattern"] = "외국인선행★"
        score += 5
    elif inst_3m > 1000 and frn_3m < -300:
        detail["pattern"] = "기관선행★"
        score += 10   # 기관 대규모 선취 매수
    elif inst_3m > 300 and frn_3m < -300:
        detail["pattern"] = "기관선행★"
        score += 5

    # ── 3. 수출 YoY (15점) + 영업이익 YoY 반전 (10점) ───────────────────
    hs_yoy = _get_hs_export_yoy(sector_info, as_of)
    detail["hs_export_yoy"] = round(hs_yoy, 0) if hs_yoy is not None else None
    if hs_yoy is not None:
        if hs_yoy > 30:    score += 15
        elif hs_yoy > 15:  score += 10
        elif hs_yoy > 5:   score += 5
        elif hs_yoy < -15: score -= 5

    earnings_yoy = _get_sector_earnings_yoy(conn, codes, as_of)
    detail["op_yoy"] = round(earnings_yoy, 0) if earnings_yoy is not None else None
    if earnings_yoy is not None:
        if earnings_yoy > 100:   score += 10
        elif earnings_yoy > 50:  score += 7
        elif earnings_yoy > 20:  score += 4
        elif earnings_yoy > 0:   score += 1
        elif earnings_yoy < -30: score -= 5

    # ── 4. 거래량 확장 (15점) — 소형섹터 선행 신호 ─────────────────────
    vol_ratio = _get_sector_volume_ratio(conn, codes, as_of)
    detail["vol_ratio"] = round(vol_ratio, 2)
    if vol_ratio > 2.0:   score += 15
    elif vol_ratio > 1.5: score += 10
    elif vol_ratio > 1.2: score += 5

    # ── 5. RS 보조/위험감점 — 가격 확인 없이는 BUY로 승격하지 않음 ───────
    kospi_4w = _get_price_returns(conn, ['^KS11'],
        (dt - timedelta(weeks=4)).strftime("%Y-%m-%d"), as_of) or 0
    kospi_12w = _get_price_returns(conn, ['^KS11'],
        (dt - timedelta(weeks=12)).strftime("%Y-%m-%d"), as_of) or 0
    rs4 = _get_price_returns(conn, codes,
        (dt - timedelta(weeks=4)).strftime("%Y-%m-%d"), as_of)
    rs12 = _get_price_returns(conn, codes,
        (dt - timedelta(weeks=12)).strftime("%Y-%m-%d"), as_of)
    if rs4 is not None:
        rs4_ex = rs4 - kospi_4w
        detail["rs4w_excess"] = round(rs4_ex, 1)
        if rs4_ex > 10:   score += 5
        elif rs4_ex > 0:  score += 2
        elif rs4_ex < -5: score -= 5
        elif rs4_ex < 0:  score -= 2
    if rs12 is not None:
        rs12_ex = rs12 - kospi_12w
        detail["rs12w_excess"] = round(rs12_ex, 1)
        if rs12_ex < -20:   score -= 15
        elif rs12_ex < -10: score -= 10
        elif rs12_ex < -3:  score -= 5

    rs4_risk = detail.get("rs4w_excess")
    rs12_risk = detail.get("rs12w_excess")

    # 신호 등급: 선행지표가 좋아도 4주/12주 가격 약세가 겹치면 BUY 금지.
    if (
        rs4_risk is not None and rs4_risk < 0
        and rs12_risk is not None and rs12_risk < -20
    ):
        signal = "NEUTRAL"
    elif score >= 55:
        signal = "BUY"
    elif score >= 35:
        signal = "WATCH"
    else:
        signal = "NEUTRAL"

    return {
        "sector": sect_key,
        "label": sector_info["label"],
        "color": sector_info["color"],
        "score": min(score, 100),
        "signal": signal,
        "detail": detail,
        "codes": codes,
    }


def _last_trade_date(conn, as_of=None):
    """전종목 가격이 충분히 적재된 마지막 거래일.

    장중/부분 백필 행이 섞이면 일부 섹터만 당일가를 쓰게 되어 RS와 진입판정이
    왜곡된다. 최소 2,000종목 이상 유효 종가가 있는 날짜만 기준일로 인정한다.
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        """
        SELECT date
        FROM price_history
        WHERE date <= ? AND close > 0
        GROUP BY date
        HAVING COUNT(DISTINCT stock_code) >= 2000
        ORDER BY date DESC
        LIMIT 1
        """,
        (as_of,)
    ).fetchone()
    return row[0][:10] if row and row[0] else as_of


def _rotation_phase_for_sector(conn, codes, as_of):
    dt = datetime.strptime(as_of, "%Y-%m-%d")
    kospi_4w = _get_price_returns(conn, ['^KS11'], (dt - timedelta(weeks=4)).strftime("%Y-%m-%d"), as_of) or 0
    kospi_12w = _get_price_returns(conn, ['^KS11'], (dt - timedelta(weeks=12)).strftime("%Y-%m-%d"), as_of) or 0
    rs4 = _get_price_returns(conn, codes, (dt - timedelta(weeks=4)).strftime("%Y-%m-%d"), as_of)
    rs12 = _get_price_returns(conn, codes, (dt - timedelta(weeks=12)).strftime("%Y-%m-%d"), as_of)
    rs4_ex = (rs4 - kospi_4w) if rs4 is not None else None
    rs12_ex = (rs12 - kospi_12w) if rs12 is not None else None
    if rs4_ex is None or rs12_ex is None:
        phase = "Lagging"
    elif rs4_ex > 0 and rs12_ex > 0:
        phase = "Leading"
    elif rs4_ex > 0:
        phase = "Improving"
    elif rs12_ex > 0:
        phase = "Weakening"
    else:
        phase = "Lagging"
    return {
        "phase": phase,
        "rs4w": round(rs4_ex, 1) if rs4_ex is not None else None,
        "rs12w": round(rs12_ex, 1) if rs12_ex is not None else None,
    }


def _entry_reasons(score_row, rotation):
    d = score_row.get("detail") or {}
    risks = []
    reasons = []
    if rotation.get("rs12w") is not None and rotation["rs12w"] <= -5:
        risks.append(f"가격확인 필요: 12주 RS {rotation['rs12w']:.1f}%")
    if rotation.get("rs4w") is not None and rotation["rs4w"] < 0:
        risks.append(f"단기 약세: 4주 RS {rotation['rs4w']:.1f}%")
    frn = d.get("frn_3m_억") or 0
    inst = d.get("inst_3m_억") or 0
    if frn >= 500:
        reasons.append(f"외국인 3M +{frn:,}억")
    if inst >= 200:
        reasons.append(f"기관 3M +{inst:,}억")
    if d.get("pattern"):
        reasons.append(d["pattern"])
    if d.get("hs_export_yoy") is not None and d["hs_export_yoy"] >= 15:
        reasons.append(f"수출 YoY +{d['hs_export_yoy']:.0f}%")
    if d.get("op_yoy") is not None and d["op_yoy"] >= 20:
        reasons.append(f"영업이익 YoY +{d['op_yoy']:.0f}%")
    if d.get("vol_ratio") is not None and d["vol_ratio"] >= 1.5:
        reasons.append(f"거래량 {d['vol_ratio']:.1f}배")
    if rotation.get("rs4w") is not None and rotation["rs4w"] >= 3:
        reasons.append(f"4주 RS +{rotation['rs4w']:.1f}%")
    if not reasons:
        reasons.append("선행 신호 부족")
    return (risks + reasons)[:4]


def _entry_stage(score_row, rotation, leaders):
    score = score_row.get("score") or 0
    phase = rotation.get("phase")
    rs4 = rotation.get("rs4w")
    rs12 = rotation.get("rs12w")
    top_leader = max((p.get("surge_score") or 0 for p in leaders), default=0)

    short_weak = rs4 is not None and rs4 < 0
    medium_weak = rs12 is not None and rs12 < -5
    deeply_weak = rs12 is not None and rs12 < -20

    if deeply_weak and short_weak:
        return {"stage": "AVOID", "label": "회피", "priority": 5}
    if medium_weak and short_weak:
        return {"stage": "EARLY_WATCH", "label": "초기 관찰", "priority": 2}

    if score >= 65 and phase == "Leading":
        return {"stage": "ENTRY_NOW", "label": "진입", "priority": 1}
    if score >= 65 and phase == "Improving" and (rs4 or 0) >= 3 and (rs12 is None or rs12 > -8):
        return {"stage": "ENTRY_NOW", "label": "진입", "priority": 1}
    if score >= 55 and phase == "Leading":
        return {"stage": "ENTRY_NOW", "label": "진입", "priority": 1}
    if score >= 55 and phase == "Improving" and (rs4 or 0) >= 3 and (rs12 is None or rs12 > -8):
        return {"stage": "ENTRY_NOW", "label": "진입", "priority": 1}
    if score >= 60 and top_leader >= 70 and phase in ("Improving", "Leading") and not medium_weak:
        return {"stage": "ENTRY_NOW", "label": "진입", "priority": 1}
    if score >= 45 or phase == "Improving":
        return {"stage": "EARLY_WATCH", "label": "초기 관찰", "priority": 2}
    if phase == "Leading":
        return {"stage": "HOLD_LEADER", "label": "보유/추세", "priority": 3}
    if score <= 25 and phase in ("Weakening", "Lagging"):
        return {"stage": "AVOID", "label": "회피", "priority": 5}
    return {"stage": "WAIT", "label": "대기", "priority": 4}


def _leader_picks_for_sector(conn, sector_key, as_of, top_n=3):
    """섹터 안에서 지금 주도주 후보를 압축 산출."""
    info = SECTOR_GROUPS[sector_key]
    codes = _valid_codes(info["codes"])
    d_3m = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    d_1y = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    picks = []

    for code in codes:
        nm = conn.execute(
            "SELECT stock_name, market_cap FROM stock_universe WHERE stock_code=?",
            (code,)
        ).fetchone()
        if not nm:
            continue
        mc = float(nm[1] or 0)
        fi = conn.execute(
            """SELECT
                SUM(CASE WHEN COALESCE(frn_net_buy_amt,0) != 0
                    THEN frn_net_buy_amt/100.0
                    ELSE COALESCE(frn_net_buy,0) * COALESCE(close,0) / 100000000.0 END),
                SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0
                    THEN inst_net_buy_amt/100.0
                    ELSE COALESCE(inst_net_buy,0) * COALESCE(close,0) / 100000000.0 END)
            FROM price_history
            WHERE stock_code=? AND date>=? AND date<=?
              AND (inst_net_buy_amt!=0 OR frn_net_buy_amt!=0 OR inst_net_buy!=0 OR frn_net_buy!=0)""",
            (code, d_3m, as_of)
        ).fetchone()
        frn = float(fi[0] or 0)
        inst = float(fi[1] or 0)
        frn_intensity = frn / mc * 100 if mc > 0 else 0
        inst_intensity = inst / mc * 100 if mc > 0 else 0

        p_now = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, as_of)
        ).fetchone()
        p_3m = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date>=? AND date<=? AND close>0 ORDER BY date LIMIT 1",
            (code, d_3m, as_of)
        ).fetchone()
        prices_1y = conn.execute(
            "SELECT high, low FROM price_history WHERE stock_code=? AND date>=? AND date<=? "
            "AND close>0 AND high IS NOT NULL AND low IS NOT NULL",
            (code, d_1y, as_of)
        ).fetchall()
        cur_price = float(p_now[0]) if p_now else 0
        ret_3m = (cur_price / float(p_3m[0]) - 1) * 100 if p_3m and cur_price else None
        high_52w = max(float(r[0]) for r in prices_1y) if prices_1y else cur_price
        low_52w = min(float(r[1]) for r in prices_1y) if prices_1y else cur_price
        rng = high_52w - low_52w if high_52w > low_52w else 1
        pos_52w = (cur_price - low_52w) / rng * 100 if cur_price else 50

        latest_year = conn.execute(
            "SELECT MAX(year) FROM financial_data WHERE stock_code=? AND is_annual=1 AND operating_profit IS NOT NULL",
            (code,)
        ).fetchone()[0]
        op_yoy = None
        if latest_year:
            op_cur = conn.execute(
                """SELECT operating_profit FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1
                   ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),
                            (CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END),
                            (CASE WHEN data_source='dart' THEN 0 ELSE 1 END),
                            id DESC
                   LIMIT 1""",
                (code, latest_year)
            ).fetchone()
            op_prv = conn.execute(
                """SELECT operating_profit FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1
                   ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),
                            (CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END),
                            (CASE WHEN data_source='dart' THEN 0 ELSE 1 END),
                            id DESC
                   LIMIT 1""",
                (code, latest_year - 1)
            ).fetchone()
            if op_cur and op_prv and op_prv[0] and op_prv[0] != 0:
                op_yoy = (float(op_cur[0]) - float(op_prv[0])) / abs(float(op_prv[0])) * 100

        surge_score = 0
        reasons = []
        if op_yoy is not None:
            if op_yoy > 300:
                surge_score += 40; reasons.append(f"OP YoY +{op_yoy:.0f}%")
            elif op_yoy > 100:
                surge_score += 30; reasons.append(f"OP YoY +{op_yoy:.0f}%")
            elif op_yoy > 50:
                surge_score += 22; reasons.append(f"OP YoY +{op_yoy:.0f}%")
            elif op_yoy > 20:
                surge_score += 15; reasons.append(f"OP YoY +{op_yoy:.0f}%")
            elif op_yoy < -50:
                surge_score -= 10
        if inst_intensity > 2.0:
            surge_score += 30; reasons.append(f"기관집중 {inst_intensity:.2f}%")
        elif inst_intensity > 0.8:
            surge_score += 22; reasons.append(f"기관집중 {inst_intensity:.2f}%")
        elif inst_intensity > 0.3:
            surge_score += 15; reasons.append(f"기관집중 {inst_intensity:.2f}%")
        elif inst_intensity > 0.1:
            surge_score += 8
        if frn_intensity > 1.0:
            surge_score += 10; reasons.append(f"외인집중 {frn_intensity:.2f}%")
        elif frn_intensity > 0.3:
            surge_score += 6
        if pos_52w < 30:
            surge_score += 20; reasons.append(f"52주 위치 {pos_52w:.0f}%")
        elif pos_52w < 50:
            surge_score += 12
        elif pos_52w < 70:
            surge_score += 5
        elif pos_52w > 85:
            surge_score -= 5
        if ret_3m is not None and ret_3m > 15:
            surge_score += 8; reasons.append(f"3M +{ret_3m:.0f}%")
        elif ret_3m is not None and ret_3m < -10:
            surge_score -= 5
        if 0 < mc < 5000:
            surge_score += 5

        picks.append({
            "code": code,
            "name": nm[0],
            "market_cap_억": round(mc),
            "surge_score": round(surge_score),
            "ret_3m": round(ret_3m, 1) if ret_3m is not None else None,
            "op_yoy": round(op_yoy, 1) if op_yoy is not None else None,
            "inst_intensity_pct": round(inst_intensity, 2),
            "frn_intensity_pct": round(frn_intensity, 2),
            "pos_52w_pct": round(pos_52w, 1),
            "reasons": reasons[:3] or ["데이터 확인 필요"],
        })

    picks.sort(key=lambda x: -x["surge_score"])
    return picks[:top_n]


def _monthly_signal_history(conn, sector_key, info, as_of, months=36):
    end = datetime.strptime(as_of, "%Y-%m-%d")
    cur = (end - timedelta(days=months * 31)).replace(day=1)
    history = []
    while cur <= end:
        month_end = (cur + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        month_end = min(month_end, end)
        month_end_str = month_end.strftime("%Y-%m-%d")
        scored = _score_sector(conn, sector_key, info, month_end_str)
        history.append({
            "month": cur.strftime("%Y-%m"),
            "score": scored["score"],
            "signal": scored["signal"],
        })
        cur = (cur + timedelta(days=32)).replace(day=1)
    peak = max(history, key=lambda x: x["score"]) if history else None
    latest_buy = next((h for h in reversed(history) if h["signal"] == "BUY"), None)
    return {
        "items": history,
        "peak": peak,
        "latest_buy": latest_buy,
    }


# ── API 엔드포인트 ────────────────────────────────────────────────────

def _compute_sector_scores(conn, as_of: str):
    results = []
    for sect_key, info in SECTOR_GROUPS.items():
        r = _score_sector(conn, sect_key, info, as_of)
        results.append(r)
    results.sort(key=lambda x: -x["score"])
    return {"as_of": as_of, "sectors": results}


def _compute_sector_leadership(conn, as_of: str, months: int = 36, top_n: int = 3):
    as_of = _last_trade_date(conn, as_of)
    months = max(12, min(int(months or 36), 48))
    top_n = max(1, min(int(top_n or 3), 5))

    sectors = []
    for sect_key, info in SECTOR_GROUPS.items():
        codes = _valid_codes(info["codes"])
        score_row = _score_sector(conn, sect_key, info, as_of)
        rotation = _rotation_phase_for_sector(conn, codes, as_of)
        leaders = _leader_picks_for_sector(conn, sect_key, as_of, top_n=top_n)
        stage = _entry_stage(score_row, rotation, leaders)
        signal_history = _monthly_signal_history(conn, sect_key, info, as_of, months=months)
        sectors.append({
            "sector": sect_key,
            "label": info["label"],
            "color": info["color"],
            "as_of": as_of,
            "score": score_row["score"],
            "signal": score_row["signal"],
            "stage": stage["stage"],
            "stage_label": stage["label"],
            "stage_priority": stage["priority"],
            "phase": rotation["phase"],
            "rs4w": rotation["rs4w"],
            "rs12w": rotation["rs12w"],
            "detail": score_row["detail"],
            "entry_reasons": _entry_reasons(score_row, rotation),
            "leaders": leaders,
            "history_recent": signal_history["items"][-12:],
            "peak_signal": signal_history["peak"],
            "latest_buy_signal": signal_history["latest_buy"],
        })

    sectors.sort(key=lambda x: (x["stage_priority"], -x["score"], -(x["rs4w"] or -999)))
    return {
        "as_of": as_of,
        "summary": {
            "entry_now": sum(1 for s in sectors if s["stage"] == "ENTRY_NOW"),
            "watch": sum(1 for s in sectors if s["stage"] == "EARLY_WATCH"),
            "leading": sum(1 for s in sectors if s["phase"] == "Leading"),
            "sectors": len(sectors),
        },
        "sectors": sectors,
    }


def _compute_rotation_map(conn, as_of: str):
    as_dt = datetime.strptime(as_of, "%Y-%m-%d")
    results = []
    kospi_4w = _get_price_returns(conn, ['^KS11'],
        (as_dt - timedelta(weeks=4)).strftime("%Y-%m-%d"), as_of) or 0
    kospi_12w = _get_price_returns(conn, ['^KS11'],
        (as_dt - timedelta(weeks=12)).strftime("%Y-%m-%d"), as_of) or 0

    for sect_key, info in SECTOR_GROUPS.items():
        codes = _valid_codes(info["codes"])
        rs4 = _get_price_returns(conn, codes,
            (as_dt - timedelta(weeks=4)).strftime("%Y-%m-%d"), as_of)
        rs12 = _get_price_returns(conn, codes,
            (as_dt - timedelta(weeks=12)).strftime("%Y-%m-%d"), as_of)
        if rs4 is None or rs12 is None:
            continue
        rs4_ex = rs4 - kospi_4w
        rs12_ex = rs12 - kospi_12w
        results.append({
            "sector": sect_key,
            "label": info["label"],
            "color": info["color"],
            "rs4w": round(rs4_ex, 1),
            "rs12w": round(rs12_ex, 1),
            "phase": (
                "Leading" if rs4_ex > 0 and rs12_ex > 0 else
                "Improving" if rs4_ex > 0 else
                "Weakening" if rs12_ex > 0 else "Lagging"
            ),
        })
    return {"as_of": as_of, "sectors": results}


def _cached_or_compute(cache_key: str, compute_fn, as_of: str = None, **kwargs):
    conn = _conn()
    try:
        if as_of:
            resolved_as_of = _last_trade_date(conn, as_of)
            payload = compute_fn(conn, resolved_as_of, **kwargs)
            payload["meta"] = {
                **_cache_meta(resolved_as_of, "manual"),
                "cached": False,
                "market_status_label": "수동 기준일 계산",
            }
            return payload

        cached = _read_cache(conn, cache_key)
        if cached:
            return cached

        refresh_sector_rotation_cache()
        cached = _read_cache(conn, cache_key)
        if cached:
            return cached

        resolved_as_of = _last_trade_date(conn)
        payload = compute_fn(conn, resolved_as_of, **kwargs)
        payload["meta"] = {**_cache_meta(resolved_as_of, _market_status_now()), "cached": False}
        return payload
    finally:
        conn.close()


@router.get("/scores")
def get_sector_scores(as_of: str = None):
    """캐시된 섹터 스코어. 장중 1시간 갱신, 장후 장마감 기준."""
    return _cached_or_compute("scores", _compute_sector_scores, as_of=as_of)


@router.get("/leadership")
def get_sector_leadership(as_of: str = None, months: int = 36, top_n: int = 3):
    """캐시된 주도섹터·주도주·진입 타이밍 통합 신호."""
    if as_of or int(months or 36) != 36 or int(top_n or 3) != 3:
        return _cached_or_compute(
            "leadership",
            _compute_sector_leadership,
            as_of=as_of or datetime.now().strftime("%Y-%m-%d"),
            months=months,
            top_n=top_n,
        )
    return _cached_or_compute("leadership", _compute_sector_leadership, as_of=as_of, months=36, top_n=3)


@router.get("/history/{sector_key}")
def get_sector_history(sector_key: str, months: int = 36):
    """섹터별 월별 RS 히스토리 (시그널 발생 시점 추적)"""
    if sector_key not in SECTOR_GROUPS:
        return {"error": "unknown sector"}

    conn = _conn()
    try:
        info = SECTOR_GROUPS[sector_key]
        codes = _valid_codes(info["codes"])

        end = datetime.now()
        start = end - timedelta(days=months * 30)

        # 월별 RS 계산
        history = []
        cur = start
        while cur < end:
            ym = cur.strftime("%Y-%m-%d")
            month_end = (cur + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            month_end_str = min(month_end, end).strftime("%Y-%m-%d")

            sect_ret = _get_price_returns(conn, codes, ym, month_end_str)
            kospi_ret = _get_price_returns(conn, ['^KS11'], ym, month_end_str)

            if sect_ret is not None:
                history.append({
                    "month": cur.strftime("%Y-%m"),
                    "sect_ret": round(sect_ret, 1),
                    "kospi_ret": round(kospi_ret, 1) if kospi_ret else 0,
                    "excess": round(sect_ret - (kospi_ret or 0), 1),
                })
            cur = (cur + timedelta(days=32)).replace(day=1)

        return {"sector": sector_key, "label": info["label"], "history": history}
    finally:
        conn.close()


@router.get("/rotation-map")
def get_rotation_map():
    """캐시된 섹터 RS 매트릭스 — 4주/12주 RS 기반 로테이션 위치."""
    return _cached_or_compute("rotation-map", _compute_rotation_map)


@router.post("/refresh-cache")
def refresh_rotation_cache():
    """섹터 로테이션 캐시 수동 재계산."""
    return refresh_sector_rotation_cache(force=True)


@router.get("/top-picks/{sector_key}")
def get_sector_top_picks(sector_key: str, top_n: int = 8):
    """섹터 내 급등 후보 종목 발굴 — 실증 데이터 기반 3대 드라이버 분석

    급등 종목 특징 (데이터 실증):
    ① 영업이익 YoY 폭발  — 주가의 핵심 드라이버 (SK하이닉스 +404% → +290%, 한화에어로스페이스 +49% → +115%)
    ② 기관 집중도 (시총 대비 %) — 소형주에서 집중 시 폭등 (원익IPS 0.43% 집중 → +524%)
    ③ 52주 저점 근처 — 아직 덜 오른 종목이 더 오름
    """
    if sector_key not in SECTOR_GROUPS:
        return {"error": "unknown sector"}
    conn = _conn()
    try:
        as_of = datetime.now().strftime("%Y-%m-%d")
        info = SECTOR_GROUPS[sector_key]
        codes = _valid_codes(info["codes"])
        d_3m = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        d_1y = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        # 섹터 평균 PBR (저평가 비교 기준)
        sector_pbrs = []
        for code in codes:
            pb = conn.execute("SELECT pbr FROM stock_universe WHERE stock_code=? AND pbr>0 AND pbr<50", (code,)).fetchone()
            if pb: sector_pbrs.append(float(pb[0]))
        sector_avg_pbr = sum(sector_pbrs)/len(sector_pbrs) if sector_pbrs else 2.0

        picks = []
        for code in codes:
            nm = conn.execute(
                "SELECT stock_name, market_cap, per, pbr FROM stock_universe WHERE stock_code=?", (code,)
            ).fetchone()
            if not nm: continue
            mc = float(nm[1] or 0)  # 억원

            # ── 수급 ──
            fi = conn.execute(
                """SELECT
                    SUM(CASE WHEN COALESCE(frn_net_buy_amt,0) != 0
                        THEN frn_net_buy_amt/100.0
                        ELSE COALESCE(frn_net_buy,0) * COALESCE(close,0) / 100000000.0 END),
                    SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0
                        THEN inst_net_buy_amt/100.0
                        ELSE COALESCE(inst_net_buy,0) * COALESCE(close,0) / 100000000.0 END)
                FROM price_history WHERE stock_code=? AND date>=? AND date<=?
                AND (inst_net_buy_amt!=0 OR frn_net_buy_amt!=0 OR inst_net_buy!=0 OR frn_net_buy!=0)""",
                (code, d_3m, as_of)
            ).fetchone()
            frn = float(fi[0] or 0)
            inst = float(fi[1] or 0)

            # 집중도 = 3M 순매수(억) / 시총(억) × 100 (%)
            frn_intensity = frn / mc * 100 if mc > 0 else 0
            inst_intensity = inst / mc * 100 if mc > 0 else 0

            # ── 가격 데이터 ──
            p_now = conn.execute("SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            p_3m = conn.execute("SELECT close FROM price_history WHERE stock_code=? AND date>=? AND close>0 ORDER BY date LIMIT 1", (code, d_3m)).fetchone()
            prices_1y = conn.execute(
                "SELECT high, low FROM price_history WHERE stock_code=? AND date>=? "
                "AND close>0 AND high IS NOT NULL AND low IS NOT NULL",
                (code, d_1y)
            ).fetchall()

            cur_price = float(p_now[0]) if p_now else 0
            ret_3m = (cur_price / float(p_3m[0]) - 1) * 100 if p_3m and cur_price else None
            high_52w = max(float(r[0]) for r in prices_1y) if prices_1y else cur_price
            low_52w = min(float(r[1]) for r in prices_1y) if prices_1y else cur_price
            rng = high_52w - low_52w if high_52w > low_52w else 1
            pos_52w = (cur_price - low_52w) / rng * 100 if cur_price else 50  # 0~100%

            # ── 영업이익 YoY ──
            latest_year = conn.execute(
                "SELECT MAX(year) FROM financial_data WHERE stock_code=? AND is_annual=1 AND operating_profit IS NOT NULL", (code,)
            ).fetchone()[0]
            op_yoy = None
            if latest_year:
                _op_order = """ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),
                                        (CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END),
                                        (CASE WHEN data_source='dart' THEN 0 ELSE 1 END),
                                        id DESC LIMIT 1"""
                op_cur = conn.execute(f"SELECT operating_profit FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1 {_op_order}", (code, latest_year)).fetchone()
                op_prv = conn.execute(f"SELECT operating_profit FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1 {_op_order}", (code, latest_year-1)).fetchone()
                if op_cur and op_prv and op_prv[0] and op_prv[0] != 0:
                    op_yoy = (float(op_cur[0]) - float(op_prv[0])) / abs(float(op_prv[0])) * 100

            # ── 최근 분기 QoQ 영업이익 추세 (실시간 모멘텀) ──
            q_rows = conn.execute(
                """SELECT year, quarter, operating_profit FROM financial_data
                WHERE stock_code=? AND is_annual=0 AND operating_profit IS NOT NULL
                ORDER BY year DESC, quarter DESC LIMIT 4""", (code,)
            ).fetchall()
            op_qoq = None
            if len(q_rows) >= 2 and q_rows[1][2] and q_rows[1][2] != 0:
                op_qoq = (float(q_rows[0][2]) - float(q_rows[1][2])) / abs(float(q_rows[1][2])) * 100

            # ──────────────────────────────────────────────
            # 급등 종목 선별 점수 (100점 만점)
            # 실증: 영업이익YoY > 기관집중도 > 52주위치 순서로 중요
            # ──────────────────────────────────────────────
            surge_score = 0
            score_detail = []

            # [A] 영업이익 YoY (40점) — 주가의 핵심 드라이버
            if op_yoy is not None:
                if op_yoy > 300:   surge_score += 40; score_detail.append(f"영업이익YoY{op_yoy:+.0f}%★★★")
                elif op_yoy > 100: surge_score += 30; score_detail.append(f"영업이익YoY{op_yoy:+.0f}%★★")
                elif op_yoy > 50:  surge_score += 22; score_detail.append(f"영업이익YoY{op_yoy:+.0f}%★")
                elif op_yoy > 20:  surge_score += 15; score_detail.append(f"영업이익YoY{op_yoy:+.0f}%")
                elif op_yoy > 0:   surge_score += 8;  score_detail.append(f"영업이익YoY{op_yoy:+.0f}%")
                elif op_yoy < -50: surge_score -= 10; score_detail.append(f"영업이익감소{op_yoy:.0f}%")
                else:              surge_score -= 3

            # 흑자전환 보너스 (2023→흑자, 데이터 실증 최고 신호)
            if q_rows and len(q_rows) >= 2:
                if float(q_rows[0][2] or 0) > 0 and float(q_rows[1][2] or 0) < 0:
                    surge_score += 10; score_detail.append("흑자전환★")

            # [B] 기관 집중도 (30점) — 시총 대비 %, 소형주 프리미엄
            # 실증: 원익IPS 집중도 0.43% → +524% / 하나머티리얼즈 1.65% → +95%
            if inst_intensity > 2.0:   surge_score += 30; score_detail.append(f"기관집중도{inst_intensity:.2f}%★★★")
            elif inst_intensity > 0.8: surge_score += 22; score_detail.append(f"기관집중도{inst_intensity:.2f}%★★")
            elif inst_intensity > 0.3: surge_score += 15; score_detail.append(f"기관집중도{inst_intensity:.2f}%★")
            elif inst_intensity > 0.1: surge_score += 8;  score_detail.append(f"기관집중도{inst_intensity:.2f}%")
            elif inst_intensity > 0:   surge_score += 3
            elif inst_intensity < -1:  surge_score -= 10; score_detail.append(f"기관이탈{inst_intensity:.2f}%")
            elif inst_intensity < -0.3: surge_score -= 5

            # 외인 집중도 보너스 (10점)
            if frn_intensity > 1.0:   surge_score += 10; score_detail.append(f"외인집중{frn_intensity:.2f}%")
            elif frn_intensity > 0.3: surge_score += 6
            elif frn_intensity > 0.1: surge_score += 3
            elif frn_intensity < -0.5: surge_score -= 5

            # [C] 52주 저점 근처 (20점) — 덜 오른 종목이 더 오름
            # 실증: 대부분 급등주는 52주 저점 근처에서 시그널 발생
            if pos_52w < 30:    surge_score += 20; score_detail.append(f"52주저점근처({pos_52w:.0f}%)★★")
            elif pos_52w < 50:  surge_score += 12; score_detail.append(f"52주중간({pos_52w:.0f}%)")
            elif pos_52w < 70:  surge_score += 5
            elif pos_52w > 85:  surge_score -= 5;  score_detail.append(f"52주고점({pos_52w:.0f}%)")

            # [D] 소형주 레버리지 프리미엄 (소형일수록 섹터 상승 시 더 오름)
            if 0 < mc < 5000:    surge_score += 5; score_detail.append("소형주프리미엄")
            elif 5000 < mc < 20000: surge_score += 2

            pbr = float(nm[3]) if nm[3] else None
            picks.append({
                "code": code,
                "name": nm[0],
                "market_cap_억": round(mc),
                "per": round(float(nm[2]), 1) if nm[2] else None,
                "pbr": round(pbr, 2) if pbr else None,
                "frn_3m_억": round(frn),
                "inst_3m_억": round(inst),
                "frn_intensity_pct": round(frn_intensity, 2),
                "inst_intensity_pct": round(inst_intensity, 2),
                "op_yoy": round(op_yoy, 1) if op_yoy is not None else None,
                "op_qoq": round(op_qoq, 1) if op_qoq is not None else None,
                "op_latest_year": latest_year,
                "pos_52w_pct": round(pos_52w, 1),
                "high_52w": round(high_52w),
                "low_52w": round(low_52w),
                "ret_3m": round(ret_3m, 1) if ret_3m is not None else None,
                "surge_score": surge_score,
                "score_detail": " | ".join(score_detail) if score_detail else "데이터부족",
            })

        picks.sort(key=lambda x: -x["surge_score"])
        return {
            "sector": sector_key,
            "label": info["label"],
            "as_of": as_of,
            "sector_avg_pbr": round(sector_avg_pbr, 2),
            "scoring_guide": {
                "A_op_yoy": "영업이익YoY 40점 — 주가의 핵심 드라이버(SK하이닉스+404%→주가+290%)",
                "B_inst_intensity": "기관집중도(시총대비%) 30점 — 소형주 집중 시 폭등(원익IPS 0.43%집중→+524%)",
                "C_52w_position": "52주 저점 근처 20점 — 덜 오른 종목이 더 오름",
                "D_size_leverage": "소형주 레버리지 5점 — 시총5000억 미만",
            },
            "picks": picks[:top_n],
        }
    finally:
        conn.close()
