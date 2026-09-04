# ──────────────────────────────────────────────────────────────
# main.py 하단에 추가할 텔레그램 종목 언급 API
# ──────────────────────────────────────────────────────────────

# 아래 코드를 /Volumes/Realtek_NVME/stock_dashboard/runtime/main.py 하단 (export default App 위)에 추가하세요

"""
@app.get("/api/telegram/mentions/daily")
def get_telegram_daily_mentions(db: Session = Depends(get_db)):
    \"\"\"
    최근 7일간 일별 TOP 20 종목 언급 횟수 반환
    반환: { dates: [...], rows: [{stock_name, market, daily: {날짜: count}}] }
    \"\"\"
    import sqlite3
    from datetime import date, timedelta

    conn   = sqlite3.connect(DB_PATH)
    cur    = conn.cursor()

    # 최근 7일 날짜 목록
    today  = date.today()
    dates  = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]

    # 7일 내 전체 언급 데이터
    cur.execute(\"\"\"
        SELECT mention_date, stock_name, market, mention_count
        FROM tg_daily_mentions
        WHERE mention_date >= ?
        ORDER BY mention_date, mention_count DESC
    \"\"\", (dates[0],))
    rows = cur.fetchall()

    # 종목별 집계
    stock_totals = {}
    stock_daily  = {}
    stock_market = {}
    for mention_date, stock_name, market, count in rows:
        if stock_name not in stock_totals:
            stock_totals[stock_name] = 0
            stock_daily[stock_name]  = {}
            stock_market[stock_name] = market
        stock_totals[stock_name]             += count
        stock_daily[stock_name][mention_date] = count

    # 7일 합산 TOP 20
    top20 = sorted(stock_totals.items(), key=lambda x: -x[1])[:20]

    result = []
    for stock_name, total in top20:
        result.append({
            "stock_name": stock_name,
            "market":     stock_market.get(stock_name, "미확인"),
            "total":      total,
            "daily":      {d: stock_daily[stock_name].get(d, 0) for d in dates},
        })

    conn.close()
    return {"dates": dates, "stocks": result}


@app.get("/api/telegram/mentions/weekly")
def get_telegram_weekly_mentions():
    \"\"\"주간 TOP 20 (최근 완성된 주 기준)\"\"\"
    import sqlite3
    from datetime import date, timedelta

    conn  = sqlite3.connect(DB_PATH)
    cur   = conn.cursor()
    today = date.today()
    # 이번주 월요일
    monday      = today - timedelta(days=today.weekday())
    week_start  = monday.isoformat()

    cur.execute(\"\"\"
        SELECT stock_name, market, SUM(mention_count) as total
        FROM tg_daily_mentions
        WHERE mention_date >= ?
        GROUP BY stock_name
        ORDER BY total DESC
        LIMIT 20
    \"\"\", (week_start,))
    rows = cur.fetchall()
    conn.close()
    return [{"stock_name": r[0], "market": r[1], "count": r[2]} for r in rows]


@app.get("/api/telegram/mentions/monthly")
def get_telegram_monthly_mentions():
    \"\"\"월간 TOP 20 (이번달 기준)\"\"\"
    import sqlite3
    from datetime import date

    conn        = sqlite3.connect(DB_PATH)
    cur         = conn.cursor()
    today       = date.today()
    month_start = today.replace(day=1).isoformat()

    cur.execute(\"\"\"
        SELECT stock_name, market, SUM(mention_count) as total
        FROM tg_daily_mentions
        WHERE mention_date >= ?
        GROUP BY stock_name
        ORDER BY total DESC
        LIMIT 20
    \"\"\", (month_start,))
    rows = cur.fetchall()
    conn.close()
    return [{"stock_name": r[0], "market": r[1], "count": r[2]} for r in rows]
"""
