"""
signal_engine.py — Smart Score 100 퀀트 시그널 엔진 v2

[가중치 구성 - 종목 시그널 합계 100점]
  외국인 수급 퀀트    : 15점  (강도+이상매도 탐지)
  기관 수급          : 10점
  재무건전성 퀀트     : 15점  (흑자+YoY+부채비율+마진)
  가치지표 퀀트       : 10점  (PBR+PER 저평가)
  대차잔고 퀀트       : 10점  (연속증가+숏커버링)
  수급에너지(OBV)     : 10점  (거래량×가격)
  52주 추세추종       : 10점
  MACD               : 10점
  RSI                :  5점
  이평선 정배열       :  5점
  합계               : 100점
"""

import json, sqlite3, logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)
DB_PATH = "/Applications/stock_dashboard/stock.db"


# ═══════════════════════════════════════════════════════════════
#  DB 초기화
# ═══════════════════════════════════════════════════════════════
def init_signal_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS signal_config (
        id           INTEGER PRIMARY KEY,
        scope        TEXT NOT NULL,
        name         TEXT NOT NULL UNIQUE,
        label        TEXT NOT NULL,
        description  TEXT DEFAULT '',
        logic_type   TEXT NOT NULL,
        params       TEXT DEFAULT '{}',
        weight       REAL  DEFAULT 0,
        is_active    INTEGER DEFAULT 1,
        sort_order   INTEGER DEFAULT 0,
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS signal_result (
        id          INTEGER PRIMARY KEY,
        config_id   INTEGER,
        stock_code  TEXT DEFAULT '',
        signal      TEXT NOT NULL,
        score       REAL DEFAULT 0,
        value       REAL,
        description TEXT DEFAULT '',
        calc_date   TEXT NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(config_id, stock_code, calc_date)
    );
    """)
    cnt = conn.execute("SELECT COUNT(*) FROM signal_config").fetchone()[0]
    if cnt == 0:
        _insert_defaults(conn)
    conn.commit()
    conn.close()


def _insert_defaults(conn):
    defaults = [
        # scope, name, label, description, logic_type, params_json, weight, sort_order
        # ── 시장 시그널 ──────────────────────────────────────────
        ('market','market_supply','시장 수급','KOSPI 기관+외국인 3일 동반 매수/매도',
         'market_supply','{"days":3,"kospi_code":"^KS11"}',0,1),
        ('market','vix','VIX 공포지수','20이하=안정 / 30이상=공포',
         'threshold','{"symbol":"^VIX","green_below":20,"yellow_below":30,"red_above":30}',0,2),
        ('market','usdkrw','원/달러 환율','1300이하=안정 / 1400이상=위험',
         'threshold','{"symbol":"USDKRW=X","green_below":1300,"yellow_below":1400,"red_above":1400}',0,3),
        ('market','nasdaq','나스닥 추세','MA5/MA20 대비 위치',
         'ma_trend','{"symbol":"^IXIC","ma_short":5,"ma_long":20}',0,4),
        ('market','sp500','S&P500 추세','MA5/MA20 대비 위치',
         'ma_trend','{"symbol":"^GSPC","ma_short":5,"ma_long":20}',0,5),
        ('market','fear_greed','Fear&Greed','CNN 공포탐욕지수 (수동 입력)',
         'manual','{"green_above":55,"red_below":30,"default":50}',0,6),

        # ── 종목 시그널 (합계 100점) ──────────────────────────────
        ('stock','frn_supply','외국인 수급',
         '3일 매수 강도+이상매도 탐지 (발행주식 0.5% 이상 매도=경고)',
         'frn_supply_quant',
         '{"days_accum":3,"ma_days":20,"spike_threshold":3.0,"sell_warning_pct":0.5}',
         15,1),
        ('stock','inst_supply','기관 수급','5일 기관 순매수 누적 추세',
         'supply_trend','{"days":5,"target":"inst"}',10,2),
        ('stock','financials','재무건전성',
         '영업이익 흑자+YoY 매출 성장+부채비율+마진율 하락 탐지',
         'financial_quant',
         '{"growth_good":10,"growth_great":20,"debt_warn":150,"debt_danger":300}',
         15,3),
        ('stock','value','가치지표',
         'PBR<1.0 + PER<12 → 저평가 우량주 배지',
         'value_quant',
         '{"pbr_good":1.5,"pbr_great":1.0,"per_good":20,"per_great":12}',
         10,4),
        ('stock','short_sell','대차잔고',
         '5일 연속 증가=잠재 매도 폭탄 / 급감+주가상승=숏커버링',
         'short_quant',
         '{"danger_ratio":1.2,"warn_days":3,"shortcover_signal":true}',
         10,5),
        ('stock','vol_price','수급에너지',
         'OBV 기울기+거래량×가격 상승 일치 여부 (거래량 없는 상승은 가짜)',
         'vol_price_quant',
         '{"spike_ratio":2.0,"price_up_pct":3.0,"obv_days":10}',
         10,6),
        ('stock','trend52w','52주 추세',
         '신고가 80% 돌파=추세추종 / 신저가 20% 이하=접근금지',
         'trend52w_quant',
         '{"buy_threshold":0.8,"danger_threshold":0.2,"high_window":260}',
         10,7),
        ('stock','macd_signal','MACD',
         '골든크로스(0선 위)=강한 매수 / 데드크로스(0선 아래)=강한 매도',
         'technical',
         '{"fast":12,"slow":26,"signal":9,"type":"macd"}',
         10,8),
        ('stock','rsi_signal','RSI',
         'RSI 30이하 탈출=반등 / 50이상 상승=추세 / 70이상=과열',
         'technical',
         '{"period":14,"green_below":30,"red_above":70,"type":"rsi"}',
         5,9),
        ('stock','ma_align','이평선 정배열',
         '5>20>60 완전 정배열=강세 / 역배열=약세',
         'technical',
         '{"type":"ma_align","ma1":5,"ma2":20,"ma3":60}',
         5,10),
    ]
    conn.executemany("""
        INSERT OR IGNORE INTO signal_config
        (scope,name,label,description,logic_type,params,weight,sort_order)
        VALUES (?,?,?,?,?,?,?,?)
    """, defaults)


# ═══════════════════════════════════════════════════════════════
#  Smart Score 판정
# ═══════════════════════════════════════════════════════════════
def _get_verdict(score, flags):
    frn   = flags.get('foreign_surge', False)
    short = flags.get('short_surge', False)
    if score >= 85 and frn:   return "🚀 적극매수", "green"
    elif score >= 70:          return "👍 매수",     "green"
    elif score >= 50:          return "✋ 보유",     "yellow"
    elif short or score < 40:  return "⚠ 주의",     "red"
    elif score < 25:           return "🚫 접근금지", "red"
    else:                      return "⏳ 관망",     "yellow"


def _gen_one_liner(score, results, flags):
    s = {r['name']: r.get('signal','gray') for r in results}
    frn = s.get('frn_supply','gray'); inst = s.get('inst_supply','gray')
    fin = s.get('financials','gray'); short = s.get('short_sell','gray')
    trend = s.get('trend52w','gray'); macd = s.get('macd_signal','gray')
    val = s.get('value','gray')

    if flags.get('short_surge') and frn == 'red':
        return "⚠ 외국인 이탈 + 대차잔고 급증 — 하락 압력이 강합니다. 신규 진입 자제."
    if flags.get('short_surge') and frn == 'green':
        return "⚠ 외국인이 매수 중이나 대차잔고도 함께 늘어 단기 변동성에 주의하세요."
    if trend == 'red':
        return "🚫 52주 신저가 근접 — 바닥 밑에 지하실이 있을 수 있습니다. 접근 금지."
    if flags.get('foreign_surge') and macd == 'green' and fin == 'green':
        return "🚀 외국인 강한 매수 + MACD 골든크로스 + 실적 양호 — 강력한 매수 신호입니다."
    if frn == 'green' and inst == 'green' and trend == 'green':
        return "👍 외국인·기관 동반 매수 + 52주 신고가 돌파 — 추세추종 매수 구간입니다."
    if fin == 'red':
        return "⚠ 영업적자 또는 매출 감소 — 실적 개선 확인 후 진입을 권장합니다."
    if val == 'green' and frn != 'red':
        return "💎 저평가 우량주 구간 — PBR/PER 매력적. 중장기 분할 매수 고려."
    if score >= 75:
        return f"긍정 지표 우세 (Smart Score {score}점) — 단기 모멘텀 양호합니다."
    elif score >= 50:
        return f"혼조세 (Smart Score {score}점) — 추가 확인 후 진입을 권장합니다."
    return f"부정 지표 우세 (Smart Score {score}점) — 관망 또는 리스크 관리 필요."


# ═══════════════════════════════════════════════════════════════
#  메인 계산 함수
# ═══════════════════════════════════════════════════════════════
def calc_stock_signals(stock_code, conn=None):
    _conn = conn or sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
    configs = _conn.execute("""
        SELECT id,name,label,description,logic_type,params,weight
        FROM signal_config WHERE scope='stock' AND is_active=1 ORDER BY sort_order
    """).fetchall()

    results = []; total_score = 0.0; total_weight = 0.0
    flags = {'is_boutique':False,'short_surge':False,'foreign_surge':False}

    for cfg_id, name, label, desc, logic_type, params_json, weight in configs:
        try:
            params = json.loads(params_json or '{}')
            sig, val, detail, partial = _calc_stock_signal(
                _conn, name, logic_type, params, stock_code, weight, flags)
            results.append({"id":cfg_id,"name":name,"label":label,"description":desc,
                "signal":sig,"score":round(partial,1),"max_score":weight,"value":val,"detail":detail})
            if sig != 'gray':
                total_score += partial; total_weight += weight
            _conn.execute("""
                INSERT OR REPLACE INTO signal_result
                (config_id,stock_code,signal,score,value,description,calc_date)
                VALUES (?,?,?,?,?,?,?)
            """, (cfg_id, stock_code, sig, partial, val, detail, today))
        except Exception as e:
            logger.debug(f"[시그널] {name}/{stock_code}: {e}")
            results.append({"id":cfg_id,"name":name,"label":label,"description":desc,
                "signal":"gray","score":0,"max_score":weight,"value":None,"detail":"데이터 부족"})

    smart_score = round(total_score/total_weight*100) if total_weight > 0 else 0
    verdict, verdict_color = _get_verdict(smart_score, flags)
    one_liner = _gen_one_liner(smart_score, results, flags)

    if conn is None:
        _conn.commit(); _conn.close()

    return {"smart_score":smart_score,"verdict":verdict,"verdict_color":verdict_color,
            "one_liner":one_liner,"signals":results,"flags":flags}


def calc_market_signals(conn=None):
    _conn = conn or sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
    configs = _conn.execute("""
        SELECT id,name,label,description,logic_type,params
        FROM signal_config WHERE scope='market' AND is_active=1 ORDER BY sort_order
    """).fetchall()

    results = []
    for cfg_id, name, label, desc, logic_type, params_json in configs:
        try:
            params = json.loads(params_json or '{}')
            sig, val, detail = _calc_market_signal(_conn, name, logic_type, params)
            results.append({"id":cfg_id,"name":name,"label":label,
                "description":desc,"signal":sig,"value":val,"detail":detail})
            _conn.execute("""
                INSERT OR REPLACE INTO signal_result
                (config_id,stock_code,signal,score,value,description,calc_date)
                VALUES (?,?,?,?,?,?,?)
            """, (cfg_id,'',sig,0,val,detail,today))
        except Exception as e:
            logger.debug(f"[시그널/시장] {name}: {e}")
            results.append({"id":cfg_id,"name":name,"label":label,
                "description":desc,"signal":"gray","value":None,"detail":"계산 불가"})

    if conn is None:
        _conn.commit(); _conn.close()
    return results


# ═══════════════════════════════════════════════════════════════
#  종목 개별 로직
# ═══════════════════════════════════════════════════════════════
def _calc_stock_signal(conn, name, logic_type, params, stock_code, weight, flags):
    if logic_type == 'frn_supply_quant':
        return _frn_quant(conn, params, stock_code, weight, flags)
    elif logic_type == 'supply_trend':
        return _supply_simple(conn, params, stock_code, weight)
    elif logic_type == 'financial_quant':
        return _financial_quant(conn, params, stock_code, weight)
    elif logic_type == 'value_quant':
        return _value_quant(conn, params, stock_code, weight)
    elif logic_type == 'short_quant':
        return _short_quant(conn, params, stock_code, weight, flags)
    elif logic_type == 'vol_price_quant':
        return _volprice_quant(conn, params, stock_code, weight)
    elif logic_type == 'trend52w_quant':
        return _trend52w(conn, params, stock_code, weight)
    elif logic_type == 'technical':
        sig, val, detail = _technical(conn, params, stock_code)
        score = weight if sig=='green' else weight*0.5 if sig=='yellow' else 0
        return sig, val, detail, score
    return 'gray', None, '미구현', 0


def _frn_quant(conn, params, stock_code, weight, flags):
    days=params.get('days_accum',3); ma_days=params.get('ma_days',20)
    spike=params.get('spike_threshold',3.0); sell_pct=params.get('sell_warning_pct',0.5)
    rows = conn.execute("""
        SELECT frn_net_buy FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT ?
    """, (stock_code, ma_days+5)).fetchall()
    if len(rows) < days+3: return 'gray',None,'수급 데이터 부족',0
    recent=sum(r[0] or 0 for r in rows[:days]); avg20=sum(r[0] or 0 for r in rows[:ma_days])/ma_days
    listed=conn.execute("SELECT list_stock_cnt FROM listed_company_info WHERE stock_code=? LIMIT 1",(stock_code,)).fetchone()
    total=listed[0] if listed and listed[0] else 1e8
    sell_warn = recent < 0 and abs(recent) > total*(sell_pct/100)
    strong_buy = avg20 > 0 and recent > avg20*spike
    if sell_warn:
        flags['foreign_surge']=False
        return 'red',recent,f"이상 매도 탐지 {round(recent):,}주 (발행주식 {sell_pct}% 이상)",0
    if strong_buy:
        flags['foreign_surge']=True
        return 'green',recent,f"강한 매수 {round(recent):,}주 (평균 {spike}배 이상)",weight
    if recent > 0 and recent > avg20:
        return 'green',recent,f"순매수 {round(recent):,}주 ({days}일)",weight*0.7
    if recent > 0:
        return 'yellow',recent,f"소폭 순매수 {round(recent):,}주",weight*0.4
    if recent < 0:
        return 'red',recent,f"순매도 {round(recent):,}주 ({days}일)",0
    return 'yellow',0,'수급 중립',weight*0.3


def _supply_simple(conn, params, stock_code, weight):
    days=params.get('days',5); target=params.get('target','inst')
    col='inst_net_buy' if target=='inst' else 'frn_net_buy'
    rows=conn.execute(f"SELECT {col} FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",(stock_code,days)).fetchall()
    if not rows: return 'gray',None,'데이터 없음',0
    vals=[r[0] or 0 for r in rows]; accum=sum(vals)
    pos_days=sum(1 for v in vals if v>0); lbl='기관' if target=='inst' else '외국인'
    if accum>0 and pos_days>=days*0.6:
        return 'green',accum,f"{lbl} {days}일 중 {pos_days}일 순매수 ({round(accum):,}주)",weight
    elif accum>0: return 'yellow',accum,f"소폭 순매수 ({round(accum):,}주)",weight*0.5
    elif accum<0: return 'red',accum,f"순매도 누적 ({round(accum):,}주)",0
    return 'yellow',0,'중립',weight*0.3


def _financial_quant(conn, params, stock_code, weight):
    g_good=params.get('growth_good',10); g_great=params.get('growth_great',20)
    d_warn=params.get('debt_warn',150); d_danger=params.get('debt_danger',300)
    rows=conn.execute("""
        SELECT year,quarter,revenue,op_profit,liabilities,equity
        FROM financial_data WHERE stock_code=? AND is_annual=0
        ORDER BY year DESC,quarter DESC LIMIT 8
    """,(stock_code,)).fetchall()
    if not rows: return 'gray',None,'재무 데이터 없음',0
    op=rows[0][3] or 0; liab=rows[0][4] or 0; eq=rows[0][5] or 1
    debt=round(liab/eq*100,1) if eq>0 else 999
    rev_now=rows[0][2] or 0; rev_yoy=rows[4][2] if len(rows)>=5 else None
    yoy=round((rev_now-rev_yoy)/abs(rev_yoy)*100,1) if rev_yoy and rev_yoy!=0 else 0
    margin_drop=False
    if len(rows)>=5:
        prev_op=rows[4][3] or 0; prev_rev=rows[4][2] or 1
        cm=op/rev_now*100 if rev_now>0 else 0; pm=prev_op/prev_rev*100 if prev_rev>0 else 0
        margin_drop=cm<pm-3
    score=0; pos=[]; iss=[]
    if op>0: score+=weight*0.4; pos.append("흑자")
    else: iss.append(f"영업적자")
    if yoy>=g_great: score+=weight*0.4; pos.append(f"매출 +{yoy}% YoY")
    elif yoy>=g_good: score+=weight*0.2; pos.append(f"매출 +{yoy}% YoY")
    elif yoy<0: iss.append(f"매출 {yoy}% YoY")
    if debt<d_warn: score+=weight*0.2; pos.append(f"부채비율 {debt}%")
    elif debt>d_danger: iss.append(f"부채 {debt}% 위험")
    if margin_drop: iss.append("마진율 하락"); score=max(0,score-weight*0.2)
    sig='green' if score>=weight*0.7 else 'red' if score<weight*0.2 else 'yellow'
    parts=pos+[f"⚠{i}" for i in iss]
    return sig,yoy," / ".join(parts[:3]) or "재무 중립",round(score,1)


def _value_quant(conn, params, stock_code, weight):
    pbr_good=params.get('pbr_good',1.5); pbr_great=params.get('pbr_great',1.0)
    per_good=params.get('per_good',20); per_great=params.get('per_great',12)
    row=conn.execute("""
        SELECT pbr,per FROM financial_data WHERE stock_code=? AND pbr IS NOT NULL
        ORDER BY year DESC,quarter DESC LIMIT 1
    """,(stock_code,)).fetchone()
    if not row: return 'gray',None,'밸류 데이터 없음',0
    pbr=row[0]; per=row[1]; score=0; tags=[]
    if pbr is not None:
        if pbr<pbr_great: score+=weight*0.5; tags.append(f"PBR {pbr}x 저평가")
        elif pbr<pbr_good: score+=weight*0.3; tags.append(f"PBR {pbr}x 적정")
        else: tags.append(f"PBR {pbr}x 고평가")
    if per is not None and per>0:
        if per<per_great: score+=weight*0.5; tags.append(f"PER {per}x 저평가")
        elif per<per_good: score+=weight*0.3; tags.append(f"PER {per}x 적정")
        else: tags.append(f"PER {per}x 고평가")
    sig='green' if score>=weight*0.7 else 'red' if score<weight*0.2 else 'yellow'
    if sig=='green': tags.insert(0,'💎 저평가 우량주')
    return sig,pbr," / ".join(tags) or "밸류 중립",round(score,1)


def _short_quant(conn, params, stock_code, weight, flags):
    danger=params.get('danger_ratio',1.2); warn_d=params.get('warn_days',3)
    sc=params.get('shortcover_signal',True)
    rows=conn.execute("SELECT lend_bal_rt,lend_bal FROM stock_lending WHERE stock_code=? ORDER BY bas_dt DESC LIMIT 20",(stock_code,)).fetchall()
    if not rows:
        rows2=conn.execute("SELECT shrt_slng_qty FROM short_sell_daily WHERE stock_code=? ORDER BY bas_dt DESC LIMIT 10",(stock_code,)).fetchall()
        if not rows2: return 'gray',None,'대차 데이터 없음',weight*0.3
        vals=[r[0] or 0 for r in rows2]; avg=sum(vals[1:])/len(vals[1:]) if len(vals)>1 else 1
        ratio=vals[0]/avg if avg>0 else 1
        rising=sum(1 for i in range(min(warn_d,len(vals)-1)) if vals[i]>vals[i+1])
        if rising>=warn_d: flags['short_surge']=True; return 'red',ratio,f'공매도 {warn_d}일 연속 증가 (x{ratio:.1f})',0
        return 'yellow',ratio,f'공매도 주의 (평균 대비 x{ratio:.1f})',weight*0.5
    rates=[r[0] or 0 for r in rows]
    curr=rates[0]; avg=sum(rates[1:10])/min(9,len(rates)-1) if len(rates)>1 else curr
    consec=sum(1 for i in range(min(warn_d+1,len(rates)-1)) if rates[i]>rates[i+1])
    if sc and len(rates)>=5:
        drop=(rates[4]-rates[0])/max(rates[4],0.01)*100
        pr=conn.execute("SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 5",(stock_code,)).fetchall()
        if pr and drop>20:
            prices=[r[0] for r in pr]
            if prices[0]>prices[-1]: return 'green',curr,f'숏커버링 신호 — 대차잔고 -{drop:.0f}%+주가상승',weight
    ratio_avg=curr/avg if avg>0 else 1
    if consec>=warn_d and ratio_avg>=danger: flags['short_surge']=True; return 'red',curr,f'대차잔고 {consec}일 연속증가 ({curr:.1f}%)',0
    elif consec>=warn_d: return 'yellow',curr,f'대차잔고 {consec}일 연속증가',weight*0.3
    elif curr<2.0: return 'green',curr,f'대차잔고 낮음 ({curr:.1f}%)',weight
    elif curr<5.0: return 'yellow',curr,f'대차잔고 주의 ({curr:.1f}%)',weight*0.5
    flags['short_surge']=True; return 'red',curr,f'대차잔고 높음 ({curr:.1f}%)',0


def _volprice_quant(conn, params, stock_code, weight):
    spike=params.get('spike_ratio',2.0); pup=params.get('price_up_pct',3.0)
    obv_d=params.get('obv_days',10)
    rows=conn.execute("SELECT close,volume FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 30",(stock_code,)).fetchall()
    if len(rows)<5: return 'gray',None,'데이터 부족',0
    closes=[r[0] for r in rows]; vols=[r[1] or 0 for r in rows]
    closes_a=list(reversed(closes)); vols_a=list(reversed(vols))
    obv=0; obv_list=[]
    for i in range(len(closes_a)):
        if i>0: obv+=vols_a[i] if closes_a[i]>closes_a[i-1] else -vols_a[i] if closes_a[i]<closes_a[i-1] else 0
        obv_list.append(obv)
    obv_slope=obv_list[-1]-obv_list[max(0,len(obv_list)-obv_d)]
    avg_vol=sum(vols[1:21])/min(20,len(vols)-1) if len(vols)>1 else 1
    vol_ratio=round(vols[0]/avg_vol,2) if avg_vol>0 else 1
    pchg=(closes[0]-closes[1])/closes[1]*100 if closes[1]>0 else 0
    if pchg>=pup and vol_ratio>=spike and obv_slope>0:
        return 'green',vol_ratio,f"강한 매수 에너지 (거래량 {vol_ratio}배+가격 +{pchg:.1f}%)",weight
    elif pchg<=-pup and vol_ratio>=spike:
        return 'red',vol_ratio,f"세력 탈출 신호 (거래량 {vol_ratio}배+가격 {pchg:.1f}%)",0
    elif obv_slope>0 and vol_ratio>=1.3:
        return 'green',vol_ratio,f"수급 에너지 양호 (OBV↑ 거래량 {vol_ratio}배)",weight*0.7
    elif not obv_slope>0 and vol_ratio<0.7:
        return 'yellow',vol_ratio,f"거래 위축 (평균 대비 {vol_ratio}배)",weight*0.3
    return 'yellow',vol_ratio,f"거래량 평균 수준 ({vol_ratio}배)",weight*0.5


def _trend52w(conn, params, stock_code, weight):
    buy_th=params.get('buy_threshold',0.8); danger_th=params.get('danger_threshold',0.2)
    window=params.get('high_window',260)
    rows=conn.execute("SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",(stock_code,window)).fetchall()
    if len(rows)<60: return 'gray',None,'데이터 부족',0
    closes=[r[0] for r in rows]; curr=closes[0]
    high52=max(closes); low52=min(closes); rng=high52-low52
    pos=round((curr-low52)/rng,3) if rng>0 else 0.5
    from_h=round((curr-high52)/high52*100,1)
    if pos>=buy_th: return 'green',pos,f"52주 신고가 {abs(from_h):.0f}% 이내 — 추세추종 구간 (위치 {pos*100:.0f}%)",weight
    elif pos>=0.5: return 'green',pos,f"중간 이상 위치 (52주 범위 {pos*100:.0f}%)",weight*0.6
    elif pos<=danger_th: return 'red',pos,f"52주 신저가 근접 — 접근 금지 (위치 {pos*100:.0f}%)",0
    return 'yellow',pos,f"중하위 위치 (52주 범위 {pos*100:.0f}%)",weight*0.3


# ═══════════════════════════════════════════════════════════════
#  시장 시그널 로직
# ═══════════════════════════════════════════════════════════════
def _calc_market_signal(conn, name, logic_type, params):
    if logic_type == 'market_supply':
        days=params.get('days',3); sym=params.get('kospi_code','^KS11')
        rows=conn.execute("SELECT inst_net_buy,frn_net_buy FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",(sym,days)).fetchall()
        if not rows: return 'gray',None,'데이터 없음'
        inst=sum(r[0] or 0 for r in rows); frn=sum(r[1] or 0 for r in rows)
        sig='green' if inst>0 and frn>0 else 'red' if inst<0 and frn<0 else 'yellow'
        return sig,inst+frn,f"기관 {'+'if inst>=0 else ''}{round(inst/100):,}억 / 외국인 {'+'if frn>=0 else ''}{round(frn/100):,}억 ({days}일)"
    elif logic_type == 'threshold':
        sym=params.get('symbol'); gb=params.get('green_below'); ra=params.get('red_above')
        if not sym: return 'gray',None,'심볼 없음'
        row=conn.execute("SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",(sym,)).fetchone()
        if not row: return 'gray',None,'데이터 없음'
        val=row[0]; sig='green' if gb and val<gb else 'red' if ra and val>ra else 'yellow'
        nm={'^VIX':'VIX','USDKRW=X':'원달러'}.get(sym,sym)
        return sig,val,f"{nm} {val:,.1f} ({'안정' if sig=='green' else '위험' if sig=='red' else '주의'})"
    elif logic_type == 'ma_trend':
        sym=params.get('symbol'); ms=params.get('ma_short',5); ml=params.get('ma_long',20)
        if not sym: return 'gray',None,'심볼 없음'
        rows=conn.execute("SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",(sym,ml+5)).fetchall()
        closes=[r[0] for r in rows]
        if len(closes)<ml: return 'gray',None,'데이터 부족'
        c=closes[0]; v5=sum(closes[:ms])/ms; v20=sum(closes[:ml])/ml
        a5=c>v5; a20=c>v20
        sig='green' if a5 and a20 else 'red' if not a5 and not a20 else 'yellow'
        nm={'^IXIC':'나스닥','^GSPC':'S&P500'}.get(sym,sym)
        return sig,c,f"{nm} MA{ms}{'위' if a5 else '아래'}/MA{ml}{'위' if a20 else '아래'}"
    elif logic_type == 'manual':
        row=conn.execute("SELECT signal,value,description FROM signal_result WHERE config_id=(SELECT id FROM signal_config WHERE name=? LIMIT 1) AND stock_code='' ORDER BY calc_date DESC LIMIT 1",(name,)).fetchone()
        if row: return row[0],row[1],row[2] or f"수동입력: {row[1]}"
        d=params.get('default',50); g=params.get('green_above',55); r=params.get('red_below',30)
        sig='green' if d>g else 'red' if d<r else 'yellow'
        return sig,d,f"기본값 {d} (수동 입력 필요)"
    return 'gray',None,'미구현'


# ═══════════════════════════════════════════════════════════════
#  기술적 지표
# ═══════════════════════════════════════════════════════════════
def _technical(conn, params, stock_code):
    typ=params.get('type','rsi')
    rows=conn.execute("SELECT close,volume FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 120",(stock_code,)).fetchall()
    if len(rows)<30: return 'gray',None,'데이터 부족'
    closes=list(reversed([r[0] for r in rows]))
    vols=list(reversed([r[1] or 0 for r in rows]))

    if typ=='rsi':
        p=params.get('period',14); gb=params.get('green_below',30); ra=params.get('red_above',70)
        if len(closes)<p+1: return 'gray',None,'데이터 부족'
        gs=[max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
        ls=[max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
        ag=sum(gs[:p])/p; al=sum(ls[:p])/p
        for i in range(p,len(gs)): ag=(ag*(p-1)+gs[i])/p; al=(al*(p-1)+ls[i])/p
        rsi=round(100-100/(1+(ag/al if al>0 else 100)),1)
        sig='green' if rsi<=gb else 'red' if rsi>=ra else ('green' if rsi>=50 else 'yellow')
        note=f'과매도 반등기대({rsi})' if rsi<=gb else f'과열 수익실현({rsi})' if rsi>=ra else f"{'상승'if rsi>=50 else '하락'}추세({rsi})"
        return sig,rsi,note

    elif typ=='macd':
        fast=params.get('fast',12); slow=params.get('slow',26); sp=params.get('signal',9)
        def ema(d,n):
            k=2/(n+1); e=d[0]; r=[e]
            for v in d[1:]: e=v*k+e*(1-k); r.append(e)
            return r
        if len(closes)<slow+sp: return 'gray',None,'데이터 부족'
        ef=ema(closes,fast); es=ema(closes,slow); ml=[f-s for f,s in zip(ef,es)]; sl=ema(ml[slow-1:],sp)
        cm=ml[-1]; cs=sl[-1]; pm=ml[-2]; ps=sl[-2] if len(sl)>=2 else cs
        golden=pm<=ps and cm>cs; dead=pm>=ps and cm<cs; ab=cm>0
        if golden and ab: return 'green',round(cm,4),'골든크로스(0선 위) — 강한 상승추세'
        elif golden: return 'green',round(cm,4),'골든크로스(0선 아래) — 반등 시작'
        elif dead and not ab: return 'red',round(cm,4),'데드크로스(0선 아래) — 하락추세'
        elif dead: return 'yellow',round(cm,4),'데드크로스(0선 위) — 주의'
        elif ab and cm>cs: return 'green',round(cm,4),'상승추세 유지'
        elif not ab and cm<cs: return 'red',round(cm,4),'하락추세 지속'
        return 'yellow',round(cm,4),f'중립(MACD {cm:+.2f})'

    elif typ=='ma_align':
        m1=params.get('ma1',5); m2=params.get('ma2',20); m3=params.get('ma3',60)
        if len(closes)<m3: return 'gray',None,'데이터 부족'
        c=closes[-1]; v1=sum(closes[-m1:])/m1; v2=sum(closes[-m2:])/m2; v3=sum(closes[-m3:])/m3
        if c>v1>v2>v3: return 'green',c,'완전 정배열 — 강세'
        elif c<v1<v2<v3: return 'red',c,'완전 역배열 — 약세'
        elif c>v1 and c>v2 and c>v3: return 'green',c,'전 이평선 위'
        elif c<v2 and c<v3: return 'red',c,'장기 이평선 아래'
        return 'yellow',c,f'혼조 (MA{m1}:{v1:,.0f}/MA{m2}:{v2:,.0f})'

    return 'gray',None,f'미구현({typ})'


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    init_signal_db()
    print("✅ Smart Score 100 시그널 DB 초기화 완료\n")
    conn = sqlite3.connect(DB_PATH)
    print("=== 시장 시그널 ===")
    for r in calc_market_signals(conn):
        e={'green':'🟢','yellow':'🟡','red':'🔴','gray':'⚪'}.get(r['signal'],'⚪')
        print(f"  {e} {r['label']:12s}: {r['detail']}")
    TEST='005930'
    print(f"\n=== {TEST} Smart Score ===")
    res=calc_stock_signals(TEST,conn)
    print(f"  Smart Score: {res['smart_score']}점")
    print(f"  판정: {res['verdict']}")
    print(f"  한줄평: {res['one_liner']}")
    for r in res['signals']:
        e={'green':'🟢','yellow':'🟡','red':'🔴','gray':'⚪'}.get(r['signal'],'⚪')
        print(f"  {e} {r['label']:12s} {r['score']:4.0f}/{r['max_score']:2.0f}점: {r['detail']}")
    conn.commit(); conn.close()
