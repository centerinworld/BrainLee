import re

with open('/Applications/stock_dashboard/signal_engine.py', 'r') as f:
    content = f.read()

changes = 0

# 1. _check_ma_align: MA200 기반으로 변경
idx = content.find("def _check_ma_align(conn, stock_code: str) -> bool:")
if idx >= 0:
    end_idx = content.find("\n\n\n", idx)
    old_block = content[idx:end_idx]
    new_block = '''def _check_ma_align(conn, stock_code: str) -> bool:
    """MA200+MA60 위 = 장기 상승추세 (글로벌 펀드 기준)."""
    rows = conn.execute("""
        SELECT close FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 210
    """, (stock_code,)).fetchall()
    if len(rows) < 60: return False
    closes = [r[0] for r in rows]
    ma60  = sum(closes[:60])  / 60
    ma200 = sum(closes[:200]) / 200 if len(closes) >= 200 else ma60
    return closes[0] > ma60 and closes[0] > ma200'''
    content = content.replace(old_block, new_block)
    changes += 1
    print("1. MA align OK")
else:
    print("1. MA align NOT FOUND")

# 2. ma_align signal 계산 수정
idx2 = content.find("    elif name == 'ma_align':")
if idx2 >= 0:
    end_idx2 = content.find("\n    elif name ==", idx2+5)
    old_sig = content[idx2:end_idx2]
    new_sig = """    elif name == 'ma_align':
        rows = conn.execute(\"\"\"
            SELECT close FROM price_history WHERE stock_code=? AND close>0
            ORDER BY date DESC LIMIT 210
        \"\"\", (stock_code,)).fetchall()
        if len(rows) < 60: return 'gray', None, '데이터 부족'
        closes = [r[0] for r in rows]
        curr  = closes[0]
        ma60  = sum(closes[:60]) / 60
        ma200 = sum(closes[:200]) / 200 if len(closes) >= 200 else ma60
        ma200_prev = sum(closes[10:210]) / 200 if len(closes) >= 210 else ma200
        above_ma200 = curr > ma200
        breakout = curr > ma200 and closes[10] <= ma200_prev
        pct = round(curr/ma200*100-100, 2)
        if breakout:
            return 'green', pct, f"MA200 상향 돌파! 장기 추세 전환 ({pct:+.1f}%)"
        elif above_ma200 and curr > ma60:
            return 'green', pct, f"MA200+MA60 위 장기 상승추세 ({pct:+.1f}%)"
        elif above_ma200:
            return 'yellow', pct, f"MA200 위 MA60 아래 단기 조정 중"
        elif curr > ma60:
            return 'yellow', pct, f"MA200 아래 MA60 위 장기 추세 미확인"
        else:
            return 'red', pct, f"MA200+MA60 아래 하락추세 ({pct:+.1f}%)"

"""
    content = content.replace(old_sig, new_sig)
    changes += 1
    print("2. MA signal OK")
else:
    print("2. MA signal NOT FOUND")

# 3. 외국인 수급: 5일 연속 + 환율 결합
idx3 = content.find("    elif name == 'frn_supply':")
if idx3 >= 0:
    end_idx3 = content.find("\n    elif name ==", idx3+5)
    old_frn = content[idx3:end_idx3]
    new_frn = """    elif name == 'frn_supply':
        if not stock_code: return 'gray', None, '종목 필요'
        rows = conn.execute(\"\"\"
            SELECT frn_net_buy, frn_net_buy_amt FROM price_history
            WHERE stock_code=? AND frn_net_buy IS NOT NULL
            ORDER BY date DESC LIMIT 7
        \"\"\", (stock_code,)).fetchall()
        if not rows: return 'gray', None, '수급 데이터 없음'
        qty_list = [r[0] or 0 for r in rows]
        amt_sum  = sum(r[1] or 0 for r in rows[:5])
        qty_sum5 = sum(qty_list[:5])
        if stock_code not in ('^KS11','^KQ11'):
            amt_sum = amt_sum / 100
        consec_buy  = all(q > 0 for q in qty_list[:5])
        consec_sell = all(q < 0 for q in qty_list[:5])
        krw_rows = conn.execute(
            "SELECT close FROM price_history WHERE stock_code='USDKRW=X' AND close>0 ORDER BY date DESC LIMIT 10"
        ).fetchall()
        usd_falling = False
        if len(krw_rows) >= 10:
            usd_ma5  = sum(r[0] for r in krw_rows[:5])  / 5
            usd_ma10 = sum(r[0] for r in krw_rows[:10]) / 10
            usd_falling = usd_ma5 < usd_ma10
        if consec_buy and usd_falling:
            return 'green', round(qty_sum5), f"외국인 5일 연속 매수+원화강세 최적 환경 ({amt_sum:+,.0f}억원)"
        elif consec_buy:
            return 'green', round(qty_sum5), f"외국인 5일 연속 순매수 ({amt_sum:+,.0f}억원)"
        elif qty_sum5 > 0:
            return 'yellow', round(qty_sum5), f"외국인 5일 누적 매수 ({amt_sum:+,.0f}억원) 연속성 미확인"
        elif consec_sell:
            return 'red', round(qty_sum5), f"외국인 5일 연속 순매도 ({amt_sum:+,.0f}억원)"
        else:
            return 'red', round(qty_sum5), f"외국인 5일 누적 매도 ({amt_sum:+,.0f}억원)"

"""
    content = content.replace(old_frn, new_frn)
    changes += 1
    print("3. frn_supply OK")
else:
    print("3. frn_supply NOT FOUND")

# 4. RSI 매도 기준 75로 상향 (과열 판단 강화)
content = content.replace(
    'elif rsi >= 70:\n            sig    = \'red\'\n            detail = f"과열 구간 ({rsi}) — 수익 실현 고려"',
    'elif rsi >= 75:\n            sig    = \'red\'\n            detail = f"과열 구간 RSI{rsi} — 매도 경계 (PBR 상투 동반 확인)"'
)

with open('/Applications/stock_dashboard/signal_engine.py', 'w') as f:
    f.write(content)
print(f"총 {changes}개 수정 완료")
