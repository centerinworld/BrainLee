"""
stockeasy_analyzer.py — 스탁이지 3전략 AI 분석기

기능:
  1. 스탁이지 3전략(Peak/모멘텀/벨류) 현재 보유 종목 수집
  2. 각 종목의 DB 데이터(주가·수급·재무·기술지표) 추출
  3. OpenAI Claude(또는 GPT)로 매수 기준 역추론 분석
  4. 텔레그램으로 분석 리포트 전송

실행 방식:
  python3 stockeasy_analyzer.py              — 분석 리포트 생성 + 텔레그램
  python3 stockeasy_analyzer.py --no-telegram — 콘솔 출력만
  python3 stockeasy_analyzer.py --strategy peak — 특정 전략만
"""

import argparse
import sqlite3
import json
import os
import sys
import re
import math
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, "/Applications/stock_dashboard")
from notifier import send as send_telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DB_PATH   = "/Applications/stock_dashboard/stock.db"
EMP_DB    = "/Applications/stock_dashboard/employment_monitor/employment.db"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

STRATEGY_URLS = {
    "peak":     "https://stockeasy.intellio.kr/strategy-room/peak",
    "momentum": "https://stockeasy.intellio.kr/strategy-room/momentum",
    "value":    "https://stockeasy.intellio.kr/strategy-room/value",
}
STRATEGY_LABELS = {
    "peak": "Peak Easy",
    "momentum": "모멘텀 Easy",
    "value": "벨류 Easy",
}


# ──────────────────────────────────────────────────────────────
# 스크래핑
# ──────────────────────────────────────────────────────────────
def fetch_strategy_page(strategy: str) -> str | None:
    url = STRATEGY_URLS.get(strategy)
    if not url:
        return None
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    })
    try:
        r = session.get(url, timeout=20)
        if r.ok:
            return r.text
    except Exception as e:
        logger.error(f"Fetch error {strategy}: {e}")
    return None


def _num(txt: str) -> float:
    t = str(txt).strip().replace(",", "").replace("%", "").replace("원", "")
    try:
        return float(t)
    except Exception:
        return 0.0


def _parse_entry_date(txt: str) -> str:
    t = str(txt).strip()
    if "/" in t:
        parts = t.split("/")
        if len(parts) == 2:
            try:
                mm, dd = int(parts[0]), int(parts[1])
                today  = date.today()
                year   = today.year
                if today.month == 12 and mm <= 3:
                    year += 1
                return f"{year}-{mm:02d}-{dd:02d}"
            except Exception:
                pass
    return t


def parse_strategy_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    result = {"holdings": [], "exits": []}

    def _parse_table(table, is_holding):
        rows_data = []
        for row in table.select("tr"):
            cells = [c.get_text(strip=True) for c in row.select("td")]
            if not cells or len(cells) < 5:
                continue
            if cells[0] in ("섹터", "종목명"):
                continue
            if len(cells) >= 7:
                sector_raw = cells[0]
                stock_name = cells[1]
                buy_price  = _num(cells[2])
                price2     = _num(cells[3])
                entry_raw  = cells[4]
                hold_raw   = cells[5]
                profit_raw = cells[6]
            elif len(cells) == 6:
                sector_raw = ""
                stock_name = cells[0]
                buy_price  = _num(cells[1])
                price2     = _num(cells[2])
                entry_raw  = cells[3]
                hold_raw   = cells[4]
                profit_raw = cells[5]
            else:
                continue

            try:
                float(stock_name.replace(",", ""))
                continue
            except ValueError:
                pass
            if not stock_name or stock_name == "종목명":
                continue

            # 섹터 파싱 (예: "원자력96" → ("원자력", 96))
            m = re.match(r'^(.+?)(\d{1,3})$', str(sector_raw).strip())
            sector       = m.group(1).strip() if m else sector_raw.strip()
            sector_score = int(m.group(2)) if m else 0

            # hold_days 파싱
            h = str(hold_raw).strip()
            NEW_KEYWORDS = ("진입", "진입중", "당일", "신규", "편입", "신규편입", "New", "new", "TODAY", "today", "-", "")
            if h in NEW_KEYWORDS:
                hold_days = -1
            else:
                n = re.sub(r"[^0-9]", "", h)
                hold_days = int(n) if n else 0

            entry_date = _parse_entry_date(entry_raw)
            profit_pct = _num(profit_raw)

            row_d = {
                "name": stock_name,
                "sector": sector,
                "sector_score": sector_score,
                "buy_price": buy_price,
                "entry_date": entry_date,
                "hold_days": hold_days,
                "profit_pct": profit_pct,
            }
            if is_holding:
                row_d["current_price"] = price2
            else:
                row_d["sell_price"] = price2

            rows_data.append(row_d)
        return rows_data

    for table in soup.select("table"):
        headers = " ".join(th.get_text(strip=True) for th in table.select("th"))
        if "현재가" in headers:
            result["holdings"] = _parse_table(table, True)
        elif "매도가" in headers:
            result["exits"] = _parse_table(table, False)

    return result


# ──────────────────────────────────────────────────────────────
# DB 데이터 수집
# ──────────────────────────────────────────────────────────────
def get_stock_data(stock_name: str, entry_date: str) -> dict:
    """종목명으로 DB에서 기술·수급·재무 데이터 수집 (매수 시점 기준)"""
    conn = sqlite3.connect(DB_PATH)
    result = {
        "name": stock_name,
        "entry_date": entry_date,
        "stock_code": None,
        "market": None,
        "sector": None,
        "mktcap_억": None,
        # 기술지표 (매수 시점)
        "price_entry": None,
        "ma20": None, "ma60": None, "ma120": None, "ma200": None,
        "rsi14": None, "vol_ratio": None, "high52_pct": None,
        # 수급 (매수 전 5일)
        "frn_5d": None, "inst_5d": None,
        "frn_5d_amt": None, "inst_5d_amt": None,
        # 재무 (매수 시점 공시 기준)
        "per": None, "pbr": None, "roe": None, "eps": None, "bps": None,
        "rev_yoy": None, "op_yoy": None,
        # 최근 성과
        "profit_pct": None,
    }

    try:
        # 종목 코드 조회
        row = conn.execute(
            "SELECT stock_code, market, sector_small, market_cap FROM stock_universe WHERE stock_name=? LIMIT 1",
            (stock_name,)
        ).fetchone()
        if not row:
            conn.close()
            return result

        stock_code = row[0]
        result["stock_code"] = stock_code
        result["market"]     = row[1]
        result["sector"]     = row[2]
        result["mktcap_억"]  = round(row[3] / 1e8) if row[3] else None

        # 주가 데이터 (매수일 이전 300일)
        price_rows = conn.execute("""
            SELECT date, close, volume, frn_net_buy, inst_net_buy, frn_net_buy_amt, inst_net_buy_amt
            FROM price_history
            WHERE stock_code=? AND close>0 AND date<=?
            ORDER BY date DESC LIMIT 300
        """, (stock_code, entry_date)).fetchall()

        if not price_rows:
            conn.close()
            return result

        dates   = [r[0] for r in reversed(price_rows)]
        prices  = [float(r[1]) for r in reversed(price_rows)]
        volumes = [float(r[2] or 0) for r in reversed(price_rows)]
        frn_qty = [float(r[3] or 0) for r in reversed(price_rows)]
        inst_qty= [float(r[4] or 0) for r in reversed(price_rows)]
        frn_amt = [float(r[5] or 0) for r in reversed(price_rows)]
        inst_amt= [float(r[6] or 0) for r in reversed(price_rows)]

        n = len(prices)
        curr = prices[-1]
        result["price_entry"] = curr

        def ma(k):
            if n < k: return None
            return round(sum(prices[-k:]) / k)

        result["ma20"]  = ma(20)
        result["ma60"]  = ma(60)
        result["ma120"] = ma(120)
        result["ma200"] = ma(200)

        # RSI(14)
        if n >= 15:
            deltas = [prices[i] - prices[i-1] for i in range(n-14, n)]
            gains  = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]
            ag = sum(gains) / 14
            al = sum(losses) / 14
            result["rsi14"] = round(100 - 100/(1 + ag/al), 1) if al > 0 else 100.0

        # 거래량 비율 (당일 vs 20일 평균)
        if n >= 21:
            avg20v = sum(volumes[-21:-1]) / 20
            result["vol_ratio"] = round(volumes[-1] / avg20v, 2) if avg20v > 0 else None

        # 52주 고점 대비
        if n >= 2:
            window = prices[max(0, n-252):]
            high52 = max(window)
            result["high52_pct"] = round((curr - high52) / high52 * 100, 1) if high52 > 0 else None

        # 수급 5일 (매수 직전)
        result["frn_5d"]     = round(sum(frn_qty[-5:]))
        result["inst_5d"]    = round(sum(inst_qty[-5:]))
        result["frn_5d_amt"] = round(sum(frn_amt[-5:]) / 100)   # 억원
        result["inst_5d_amt"]= round(sum(inst_amt[-5:]) / 100)  # 억원

        # 재무 (최신 공시된 것)
        fin = conn.execute("""
            SELECT year, quarter, eps, bps, roe, revenue, operating_profit, is_annual
            FROM financial_data
            WHERE stock_code=? AND eps IS NOT NULL
            ORDER BY year DESC, quarter DESC LIMIT 1
        """, (stock_code,)).fetchone()

        if fin:
            y, q, eps, bps, roe, rev, op, is_ann = fin
            result["eps"] = eps
            result["bps"] = bps
            result["roe"] = roe
            if eps and bps and curr:
                result["per"] = round(curr / eps, 1) if eps > 0 else None
                result["pbr"] = round(curr / bps, 2) if bps > 0 else None

            # YoY 성장률
            prev = conn.execute("""
                SELECT revenue, operating_profit FROM financial_data
                WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?
            """, (stock_code, y-1, q, is_ann)).fetchone()
            if prev and prev[0] and rev:
                result["rev_yoy"] = round((rev - prev[0]) / abs(prev[0]) * 100, 1)
            if prev and prev[1] and op:
                result["op_yoy"] = round((op - prev[1]) / abs(prev[1]) * 100, 1)

        # 종목 PBR/PER from stock_universe
        su = conn.execute("SELECT per, pbr FROM stock_universe WHERE stock_code=?", (stock_code,)).fetchone()
        if su:
            if result["per"] is None and su[0]: result["per"] = su[0]
            if result["pbr"] is None and su[1]: result["pbr"] = su[1]

    except Exception as e:
        logger.error(f"DB error for {stock_name}: {e}")
    finally:
        conn.close()

    return result


# ──────────────────────────────────────────────────────────────
# AI 분석
# ──────────────────────────────────────────────────────────────
def analyze_strategy_with_ai(strategy: str, data: dict) -> str:
    """
    수집한 종목 데이터를 기반으로 매수 패턴 역추론.
    OpenAI API 사용. 없으면 규칙 기반 분석 반환.
    """
    if not OPENAI_API_KEY:
        return _rule_based_analysis(strategy, data)

    holdings = data.get("holdings", [])
    if not holdings:
        return f"{STRATEGY_LABELS[strategy]}: 보유 종목 없음"

    # 프롬프트 구성
    stock_summary = []
    for h in holdings[:15]:  # 최대 15종목
        s = h.get("stock_data", {})
        line = (
            f"- {h['name']} ({h.get('sector','?')} {h.get('sector_score',0)}점) "
            f"매수일:{h.get('entry_date','?')} 보유{h.get('hold_days',0)}일 "
            f"수익:{h.get('profit_pct',0):+.1f}%\n"
            f"  기술: MA정배열={'O' if (s.get('ma20') and s.get('ma60') and s.get('ma120') and s.get('price_entry') and s['price_entry']>s['ma120']) else 'X'} "
            f"RSI={s.get('rsi14','?')} 거래량배율={s.get('vol_ratio','?')} 52주고점={s.get('high52_pct','?')}%\n"
            f"  수급: 기관5일={s.get('inst_5d_amt','?')}억원 외국인5일={s.get('frn_5d_amt','?')}억원\n"
            f"  밸류: PER={s.get('per','?')} PBR={s.get('pbr','?')} ROE={s.get('roe','?')}% "
            f"매출YoY={s.get('rev_yoy','?')}% 영업이익YoY={s.get('op_yoy','?')}%\n"
            f"  시총={s.get('mktcap_억','?')}억원"
        )
        stock_summary.append(line)

    prompt = f"""당신은 한국 주식 투자 전략 분석가입니다.
스탁이지 '{STRATEGY_LABELS[strategy]}' 전략의 현재 보유 종목 데이터입니다.
⚠️ 중요: 아래 모든 지표는 각 종목의 【편입일(매수일) 당시】 데이터입니다.
현재 시점이 아닌 매수 결정 시점의 조건을 분석하므로 전략의 매수 기준을 정확히 역추론할 수 있습니다.
아래 종목들의 매수 시점 기술/수급/밸류 지표를 보고, 이 전략의 매수 기준을 역추론해 주세요.

보유 종목 ({len(holdings)}개):
{chr(10).join(stock_summary)}

분석 요청:
1. 기술적 패턴: 이 전략이 중시하는 이평선·RSI·거래량 조건은?
2. 수급 패턴: 기관/외국인 수급이 중요한 역할을 하는가?
3. 밸류 패턴: PER/PBR/ROE 등 가치 기준이 있는가?
4. 섹터 패턴: 특정 섹터에 집중하는가?
5. 추정 전략: 3줄로 요약

응답은 한국어로, 2000자 이내로 간결하게."""

    try:
        import openai
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.3,
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return _rule_based_analysis(strategy, data)


def _rule_based_analysis(strategy: str, data: dict) -> str:
    """OpenAI 없을 때의 규칙 기반 분석. 모든 지표는 편입일 당시 기준."""
    holdings = data.get("holdings", [])
    exits     = data.get("exits", [])

    if not holdings:
        return f"{STRATEGY_LABELS[strategy]}: 보유 종목 없음"

    lines = [f"📊 [{STRATEGY_LABELS[strategy]}] 규칙기반 분석 (⚠️ 모든 지표 = 편입일 당시)"]
    lines.append(f"보유 {len(holdings)}종목 | 최근이탈 {len(exits)}종목")

    # 섹터 집계
    sector_cnt = {}
    for h in holdings:
        s = h.get("sector", "기타")
        sector_cnt[s] = sector_cnt.get(s, 0) + 1
    top_sectors = sorted(sector_cnt.items(), key=lambda x: -x[1])[:5]
    lines.append(f"\n📌 주요 섹터: " + ", ".join(f"{s}({n})" for s, n in top_sectors))

    # 기술지표 평균
    def avg(lst, key, exclude_none=True):
        vals = [h.get("stock_data", {}).get(key) for h in lst if h.get("stock_data",{}).get(key) is not None]
        return round(sum(vals)/len(vals), 1) if vals else None

    avg_rsi       = avg(holdings, "rsi14")
    avg_vol_ratio = avg(holdings, "vol_ratio")
    avg_high52    = avg(holdings, "high52_pct")
    avg_inst_amt  = avg(holdings, "inst_5d_amt")
    avg_frn_amt   = avg(holdings, "frn_5d_amt")
    avg_per       = avg(holdings, "per")
    avg_pbr       = avg(holdings, "pbr")
    avg_mktcap    = avg(holdings, "mktcap_억")

    # MA 정배열 비율
    ma_ok = sum(1 for h in holdings if (
        h.get("stock_data", {}).get("price_entry") and
        h.get("stock_data", {}).get("ma120") and
        h["stock_data"]["price_entry"] > h["stock_data"]["ma120"]
    ))
    ma_pct = round(ma_ok / len(holdings) * 100) if holdings else 0

    lines.append(f"\n📈 기술적 지표 (평균):")
    lines.append(f"  MA120 이상: {ma_pct}%의 종목")
    if avg_rsi: lines.append(f"  RSI: {avg_rsi}")
    if avg_vol_ratio: lines.append(f"  거래량배율: {avg_vol_ratio}x")
    if avg_high52: lines.append(f"  52주고점 대비: {avg_high52}%")

    lines.append(f"\n💰 수급 (5일 누적 평균):")
    if avg_inst_amt is not None: lines.append(f"  기관: {avg_inst_amt:+.0f}억원")
    if avg_frn_amt  is not None: lines.append(f"  외국인: {avg_frn_amt:+.0f}억원")

    lines.append(f"\n📊 밸류에이션 (평균):")
    if avg_per: lines.append(f"  PER: {avg_per}배")
    if avg_pbr: lines.append(f"  PBR: {avg_pbr}배")
    if avg_mktcap: lines.append(f"  시총: {avg_mktcap:.0f}억원")

    # 수익률 분포
    profits = [h.get("profit_pct", 0) for h in holdings]
    pos = sum(1 for p in profits if p > 0)
    lines.append(f"\n🎯 현재 수익 상황:")
    lines.append(f"  수익{pos}/{len(profits)} | 평균{round(sum(profits)/len(profits),1):+.1f}%")
    top3 = sorted(holdings, key=lambda x: x.get("profit_pct", 0), reverse=True)[:3]
    for t in top3:
        lines.append(f"  ★ {t['name']} {t.get('profit_pct',0):+.1f}%")

    # 최근 이탈 종목 — 편입 vs 편출 시점 비교
    if exits:
        lines.append(f"\n🚪 최근 이탈 종목 (편입→편출 변화):")
        for e in exits[:5]:
            pp   = e.get("profit_pct", 0)
            hd   = e.get("hold_days", 0)
            esd  = e.get("entry_stock_data", {})
            exsd = e.get("exit_stock_data", {})

            # 편입 시 RSI vs 편출 시 RSI
            rsi_entry = esd.get("rsi14")
            rsi_exit  = exsd.get("rsi14")
            rsi_str = ""
            if rsi_entry and rsi_exit:
                rsi_str = f" RSI:{rsi_entry}→{rsi_exit}"

            # 편입 시 수급 vs 편출 시 수급
            inst_entry = esd.get("inst_5d_amt")
            inst_exit  = exsd.get("inst_5d_amt")
            inst_str = ""
            if inst_entry is not None and inst_exit is not None:
                inst_str = f" 기관:{inst_entry:+.0f}→{inst_exit:+.0f}억"

            icon = '🟢' if pp > 0 else '🔴'
            lines.append(
                f"  {icon} {e['name']} {pp:+.1f}% "
                f"({e.get('data_as_of_entry','?')}편입 → {e.get('data_as_of_exit','?')}편출, {hd}일)"
                + rsi_str + inst_str
            )

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 메인 분석 실행
# ──────────────────────────────────────────────────────────────
def run_analysis(strategies: list, send_tg: bool = True, verbose: bool = False) -> None:
    today = date.today().isoformat()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    all_reports = []

    for strategy in strategies:
        label = STRATEGY_LABELS.get(strategy, strategy)
        logger.info(f"[{label}] 분석 시작...")

        # 1. 페이지 스크래핑
        html = fetch_strategy_page(strategy)
        if not html:
            logger.warning(f"[{label}] 페이지 취득 실패")
            continue

        data = parse_strategy_html(html)
        holdings = data.get("holdings", [])
        exits    = data.get("exits", [])

        logger.info(f"[{label}] 보유={len(holdings)} 이탈={len(exits)}")

        # 2. 각 종목 DB 데이터 수집 ─ 반드시 편입/편출일 기준으로 조회
        for h in holdings:
            entry_date = h.get("entry_date", today)
            h["stock_data"] = get_stock_data(h["name"], entry_date)
            # 레이블: 편입일 당시 데이터임을 명시
            h["data_as_of"] = entry_date

        # 편출 종목도 편출일(또는 편입일) 기준 데이터 수집
        for e in exits:
            # 편출일은 페이지에서 제공되지 않아 → 오늘 날짜를 사용 (최근 이탈이므로)
            # entry_date는 파싱된 값 사용, 편출 시점은 오늘 기준
            entry_date = e.get("entry_date", today)
            exit_date  = today  # 페이지에서 편출된 날 = 오늘
            e["entry_stock_data"] = get_stock_data(e["name"], entry_date)  # 편입 시점
            e["exit_stock_data"]  = get_stock_data(e["name"], exit_date)   # 편출 시점
            e["data_as_of_entry"] = entry_date
            e["data_as_of_exit"]  = exit_date

        data["strategy"] = strategy
        data["fetched_at"] = now_str

        # 3. AI 분석
        analysis = analyze_strategy_with_ai(strategy, data)

        if verbose:
            print(f"\n{'='*60}")
            print(analysis)

        all_reports.append({
            "strategy": strategy,
            "label": label,
            "holdings_count": len(holdings),
            "exits_count": len(exits),
            "analysis": analysis,
            "holdings": holdings,
            "exits": exits,
        })

    if not all_reports:
        return

    # 4. 텔레그램 전송
    if send_tg:
        _send_telegram_reports(all_reports, now_str)

    # 5. DB에 분석 결과 저장
    _save_analysis_to_db(all_reports)


def _send_telegram_reports(reports: list, now_str: str) -> None:
    """전략별 분석 리포트를 텔레그램으로 전송."""
    header = f"🔍 <b>스탁이지 전략 분석 리포트</b>\n📅 {now_str}\n"
    send_telegram(header, key=f"se_header_{date.today().isoformat()}")

    for r in reports:
        label   = r["label"]
        n       = r["holdings_count"]
        n_exit  = r["exits_count"]
        analysis = r["analysis"]

        # 텔레그램 메시지 분리 (4000자 제한)
        msg = f"━━━━━━━━━━━━━━━━━━━━━\n<b>[{label}]</b> 보유 {n}종목\n\n{analysis}"
        if len(msg) > 3800:
            msg = msg[:3800] + "...(생략)"

        send_telegram(msg, key=f"se_analysis_{r['strategy']}_{date.today().isoformat()}")

        # 보유 종목 리스트
        holdings = r.get("holdings", [])
        if holdings:
            lines = [f"📋 <b>[{label}] 현재 보유 종목</b>"]
            for h in holdings:
                pp = h.get("profit_pct", 0)
                hd = h.get("hold_days", 0)
                sector = h.get("sector", "")
                lines.append(f"{'🟢' if pp>0 else '🔴' if pp<0 else '⚪'} {h['name']}"
                              f" {pp:+.1f}% | {sector} | {hd}일")
            hold_msg = "\n".join(lines)
            if len(hold_msg) > 3800:
                hold_msg = hold_msg[:3800] + "\n..."
            send_telegram(hold_msg, key=f"se_holdings_{r['strategy']}_{date.today().isoformat()}")


def _save_analysis_to_db(reports: list) -> None:
    """분석 결과를 DB에 저장 (stockeasy_analysis 테이블)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stockeasy_analysis (
                id          INTEGER PRIMARY KEY,
                strategy    TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                holdings_cnt INTEGER,
                exits_cnt    INTEGER,
                analysis_text TEXT,
                holdings_json TEXT,
                exits_json    TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in reports:
            # holdings에서 stock_data 제거 (DB 용량 절약)
            h_slim = [{k: v for k, v in h.items() if k != "stock_data"} for h in r.get("holdings", [])]
            conn.execute("""
                INSERT INTO stockeasy_analysis
                    (strategy, analyzed_at, holdings_cnt, exits_cnt, analysis_text, holdings_json, exits_json)
                VALUES (?,?,?,?,?,?,?)
            """, (
                r["strategy"], now,
                r["holdings_count"], r["exits_count"],
                r["analysis"],
                json.dumps(h_slim, ensure_ascii=False),
                json.dumps(r.get("exits", []), ensure_ascii=False),
            ))
        conn.commit()
        conn.close()
        logger.info(f"[DB] 분석 결과 저장 완료 ({len(reports)}건)")
    except Exception as e:
        logger.error(f"[DB] 저장 실패: {e}")


# ──────────────────────────────────────────────────────────────
# 주간 요약 (DB 누적 분석 결과 기반)
# ──────────────────────────────────────────────────────────────
def run_weekly_summary() -> None:
    """매주 일요일 09:00 — 지난 7일 분석 DB에서 패턴 종합 + 텔레그램."""
    try:
        conn = sqlite3.connect(DB_PATH)
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        rows = conn.execute("""
            SELECT strategy, analyzed_at, holdings_cnt, exits_cnt, analysis_text
            FROM stockeasy_analysis
            WHERE analyzed_at >= ?
            ORDER BY strategy, analyzed_at
        """, (week_ago,)).fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"[주간요약] DB 오류: {e}")
        return

    if not rows:
        send_telegram("📊 스탁이지 주간 요약: 지난 7일 분석 데이터 없음")
        return

    # 전략별 통계 집계
    from collections import defaultdict
    stat = defaultdict(list)
    for r in rows:
        stat[r[0]].append({"at": r[1], "holdings": r[2], "exits": r[3], "text": r[4]})

    now_str = datetime.now().strftime("%Y-%m-%d")
    msg_lines = [
        f"📊 <b>스탁이지 주간 전략 리포트</b>",
        f"📅 {week_ago} ~ {now_str}",
        f"━━━━━━━━━━━━━━━━━━━━━",
    ]

    for strategy, label in STRATEGY_LABELS.items():
        if strategy not in stat:
            continue
        entries = stat[strategy]
        avg_hold = round(sum(e["holdings"] for e in entries) / len(entries))
        total_exit = sum(e["exits"] for e in entries)
        latest = entries[-1]

        msg_lines.append(f"\n<b>[{label}]</b>")
        msg_lines.append(f"  분석 {len(entries)}회 | 평균 보유 {avg_hold}종목 | 이탈 누적 {total_exit}종목")
        msg_lines.append(f"  최신 분석 ({latest['at'][:10]}):")
        # 최신 분석 텍스트 앞 300자만
        short_text = latest["text"][:300].replace("\n", " ")
        msg_lines.append(f"  {short_text}...")

    msg = "\n".join(msg_lines)
    if len(msg) > 3800:
        msg = msg[:3800] + "\n...(생략)"

    key = f"se_weekly_{date.today().isoformat()}"
    send_telegram(msg, key=key)
    logger.info("[스탁이지] 주간 요약 전송 완료")


# ──────────────────────────────────────────────────────────────
# 스케줄러에서 호출 가능한 래퍼
# ──────────────────────────────────────────────────────────────
def run_daily_analysis():
    """매일 장 마감 후 호출 — 3전략 전체 분석."""
    run_analysis(list(STRATEGY_URLS.keys()), send_tg=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="스탁이지 전략 AI 분석기")
    parser.add_argument("--strategy", choices=["peak", "momentum", "value"], default=None)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    strategies = [args.strategy] if args.strategy else list(STRATEGY_URLS.keys())
    run_analysis(strategies, send_tg=not args.no_telegram, verbose=args.verbose)
