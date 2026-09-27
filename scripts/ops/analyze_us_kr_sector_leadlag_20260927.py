"""
scripts/ops/analyze_us_kr_sector_leadlag_20260927.py

"미국 시장이 국내 시장을 선행한다"는 사용자 가설을 실제로 검증한다 — 미국 섹터 ETF의
당일 등락이 "다음 국내 거래일" 해당 섹터 바스켓 등락과 같은 방향인지 과거 데이터로 확인.
결과(적중률·상관계수)를 routes/us_sector_rotation.py의 US_TO_KR_LEADLAG에 반영한다.

방법: 미국 ETF 종가일 date_us < 국내 거래일 date_kr인 가장 가까운 date_us를 찾아
(US 당일수익률, KR date_kr 당일수익률) 쌍을 만든다 — 기존 market_radar.py
get_sector_us_overnight_signals와 같은 "미국 마감(새벽) → 국내 다음 개장" 정렬.
전체기간을 앞 60%(학습)/뒤 40%(검증)로 나눠 룩어헤드 없이 방향일치율을 계산한다.

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/ops/analyze_us_kr_sector_leadlag_20260927.py
"""
from __future__ import annotations

import json
from datetime import datetime

from db_compat import connect_primary_db

# KR sector_rotation.py의 SECTOR_GROUPS와 동일 코드(간접 의존 피하려 여기 복사 — 원본 바뀌면 같이 갱신할 것)
KR_CODES = {
    "반도체":        ["005930","000660","042700","166090","240810","058470","009150","357780","089030","039030"],
    "IT/하드웨어":    ["034220","108320","272290","011070"],
    "기판패키지":     ["222800","353200","095340","007810","007660","195870"],
    "통신/플랫폼":    ["035420","035720","017670","030200","259960","036570"],
    "금융/지주":      ["105560","055550","086790","138040","000810","039490"],
    "바이오":         ["207940","068270","000100","326030","302440","196170","298380","141080"],
    "의료기기/미용":  ["214150","214450","278470","336570","149980","145020"],
    "자동차":         ["005380","000270","012330","204320","161390","073240"],
    "화장품/뷰티":    ["241710","051900","090430","078520","161890","027050","003350"],
    "산업재/건설":    ["028050","000720","047040","006360","375500","241560"],
    "방산":           ["047810","012450","272210","064350","000880","079550"],
    "조선":           ["009540","329180","010140","042660"],
    "해운":           ["011200","028670","005880","086280","003280"],
    "소재/화학":      ["011170","051910","009830","011780","298020","361610","006650"],
    "철강/비철금속":  ["005490","004020","103140","010130"],
    "전력기기":       ["267260","010120","298040","017040","103590","033100","000500","001440"],
}

# (US ETF, KR 섹터키) 후보쌍 — US 섹터 하나가 여러 KR 섹터에 대응될 수 있음
CANDIDATE_PAIRS = [
    ("XLK", "반도체"), ("XLK", "IT/하드웨어"), ("XLK", "기판패키지"),
    ("XLC", "통신/플랫폼"),
    ("XLF", "금융/지주"),
    ("XLV", "바이오"), ("XLV", "의료기기/미용"),
    ("XLY", "자동차"),
    ("XLP", "화장품/뷰티"),
    ("XLI", "산업재/건설"), ("XLI", "방산"), ("XLI", "조선"), ("XLI", "해운"),
    ("XLB", "소재/화학"), ("XLB", "철강/비철금속"),
    ("XLU", "전력기기"),
]


def _kr_daily_returns(conn, codes):
    ph = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""SELECT date, stock_code, close FROM price_history
            WHERE stock_code IN ({ph}) AND close > 0
            ORDER BY date""",
        codes,
    ).fetchall()
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], {})[r["stock_code"]] = r["close"]
    dates = sorted(by_date.keys())
    rets = {}  # date -> equal-weight avg return vs prior date
    prev = {}
    for d in dates:
        day = by_date[d]
        day_rets = []
        for code, px in day.items():
            if code in prev and prev[code] > 0:
                day_rets.append((px - prev[code]) / prev[code])
        if day_rets:
            rets[d] = sum(day_rets) / len(day_rets)
        prev.update(day)
    return rets  # {date_str: pct_return}


def _us_daily_returns(conn, ticker):
    rows = conn.execute(
        "SELECT date, close FROM us_price_history WHERE ticker=? AND close>0 ORDER BY date",
        (ticker,),
    ).fetchall()
    rets = {}
    prev = None
    for r in rows:
        if prev is not None and prev > 0:
            rets[r["date"]] = (r["close"] - prev) / prev
        prev = r["close"]
    return rets


def _hit_rate(pairs):
    if not pairs:
        return None
    same = sum(1 for u, k in pairs if (u > 0) == (k > 0))
    return round(same / len(pairs) * 100, 1)


def main():
    conn = connect_primary_db(timeout=30)
    us_cache = {}
    results = []
    try:
        for etf, kr_key in CANDIDATE_PAIRS:
            if etf not in us_cache:
                us_cache[etf] = _us_daily_returns(conn, etf)
            us_rets = us_cache[etf]
            kr_rets = _kr_daily_returns(conn, KR_CODES[kr_key])

            us_dates = sorted(us_rets.keys())
            kr_dates = sorted(kr_rets.keys())
            if not us_dates or not kr_dates:
                continue

            # 각 국내 거래일에 대해 그 직전(작은) 미국 날짜를 찾는다(정렬된 US 날짜에서 이분탐색)
            import bisect
            pairs = []
            for d_kr in kr_dates:
                idx = bisect.bisect_left(us_dates, d_kr) - 1
                if idx < 0:
                    continue
                d_us = us_dates[idx]
                # 너무 먼 과거(연휴 등으로 3일 초과 간격)는 룩어헤드/왜곡 방지로 제외
                if (datetime.strptime(d_kr, "%Y-%m-%d") - datetime.strptime(d_us, "%Y-%m-%d")).days > 4:
                    continue
                pairs.append((us_rets[d_us], kr_rets[d_kr], d_kr))

            if len(pairs) < 30:
                results.append({"etf": etf, "kr_sector": kr_key, "n": len(pairs), "note": "샘플 부족"})
                continue

            pairs.sort(key=lambda x: x[2])
            split = int(len(pairs) * 0.6)
            train, test = pairs[:split], pairs[split:]
            train_hit = _hit_rate([(u, k) for u, k, _ in train])
            test_hit = _hit_rate([(u, k) for u, k, _ in test])

            # Spearman 근사(순위상관 대신 부호일치 기반 — scipy 없이 간단 피어슨으로 대체)
            n = len(pairs)
            us_vals = [u for u, k, _ in pairs]
            kr_vals = [k for u, k, _ in pairs]
            mu, mk = sum(us_vals) / n, sum(kr_vals) / n
            cov = sum((u - mu) * (k - mk) for u, k, _ in pairs)
            su = (sum((u - mu) ** 2 for u in us_vals)) ** 0.5
            sk = (sum((k - mk) ** 2 for k in kr_vals)) ** 0.5
            corr = round(cov / (su * sk), 3) if su > 0 and sk > 0 else None

            results.append({
                "etf": etf, "kr_sector": kr_key, "n": n,
                "corr": corr, "train_hit_pct": train_hit, "test_hit_pct": test_hit,
                "date_range": f"{pairs[0][2]}~{pairs[-1][2]}",
            })
    finally:
        conn.close()

    results.sort(key=lambda r: -(r.get("test_hit_pct") or 0))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    with open("docs/us_kr_sector_leadlag_backtest_20260927.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
