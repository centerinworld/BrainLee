"""
backfill_financials.py — DART API 한도 내 전종목 재무제표 자동 수집

설계 원칙:
  · DART API 일일 한도 10,000회 → 안전 한도 8,000회 사용
  · 하루 최대 1,000종목 처리 (종목당 평균 8회 호출)
  · 매일 자정 00:05 자동 실행 → 4~5일이면 전종목 완료
  · 이미 수집된 분기는 스킵 (중복 없음)
  · 진행 상황 파일(backfill_progress.json)로 저장 → 재시작 시 이어서

실행:
  python3 backfill_financials.py             # 오늘 한도만큼 수집
  python3 backfill_financials.py --stats     # 현황만 출력
  python3 backfill_financials.py --setup-cron  # crontab 자동 등록
  python3 backfill_financials.py --codes 005930 000660  # 특정 종목만
  python3 backfill_financials.py --reset     # 처음부터 다시
"""

import sys, os, time, json, logging, argparse, sqlite3
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, "/Applications/stock_dashboard")

# ── stdout 필터 (OpenDartReader 내부 출력 억제) ───────────────────
_real_stdout = sys.__stdout__

class _FilteredStdout:
    _SKIP = ("reprt_code=", "조회된 데이타가 없습니다", "fs_div=",
             "status", "message", "{\'status\'", "사용한도")
    def write(self, s):
        if not any(kw in s for kw in self._SKIP): _real_stdout.write(s)
    def flush(self): _real_stdout.flush()
    def fileno(self): return _real_stdout.fileno()

sys.stdout = _FilteredStdout()

for _lg in ("OpenDartReader","dart","urllib3","requests","httpx","httpcore"):
    logging.getLogger(_lg).setLevel(logging.CRITICAL)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(_real_stdout),
        logging.FileHandler("/Applications/stock_dashboard/backfill_financials.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── 설정 ─────────────────────────────────────────────────────────
DB_PATH         = Path("/Applications/stock_dashboard/stock.db")
PROGRESS_FILE   = Path("/Applications/stock_dashboard/backfill_progress.json")
DAILY_API_LIMIT = 8000   # 안전 한도
MAX_STOCKS_DAY  = 1000   # 하루 최대 종목
SLEEP_DART      = 0.8    # API 간격(초)
SLEEP_STOCK     = 0.1    # 종목 간 간격
_DEFAULT_START_YEAR = 2015

# ── DART 기업목록 캐시 ───────────────────────────────────────────
_dart_registered = None

def _load_dart_corps(dart):
    global _dart_registered
    if _dart_registered is not None: return _dart_registered
    try:
        df = dart.corp_codes
        if df is not None and not df.empty:
            for col in ("stock_code","종목코드"):
                if col in df.columns:
                    codes = set(str(c).zfill(6) for c in df[col].dropna()
                                if str(c).strip().isdigit() and len(str(c).strip())<=6
                                and str(c).strip()!="000000")
                    _dart_registered = codes
                    logger.info(f"DART 기업목록: {len(codes):,}개")
                    return codes
    except Exception as e:
        logger.warning(f"DART 기업목록 로드 실패: {e}")
    _dart_registered = set()
    return _dart_registered

# ── 진행 상황 ────────────────────────────────────────────────────
def load_progress():
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE) as f: return json.load(f)
    except: pass
    return {"completed_codes":[],"last_run_date":"","total_new":0,"total_api_calls":0}

def save_progress(p):
    try:
        with open(PROGRESS_FILE,"w") as f: json.dump(p, f, ensure_ascii=False, indent=2)
    except Exception as e: logger.warning(f"진행 저장 실패: {e}")

# ── DB 헬퍼 ─────────────────────────────────────────────────────
def _fin_exists(conn, code, year, qnum):
    return conn.execute("SELECT 1 FROM financial_data WHERE stock_code=? AND year=? AND quarter=? LIMIT 1",
                        (code,year,qnum)).fetchone() is not None

def _has_any(conn, code):
    return conn.execute("SELECT COUNT(*) FROM financial_data WHERE stock_code=?",
                        (code,)).fetchone()[0] > 0

def _save_fin(conn, code, year, qnum, is_annual, m):
    conn.execute("""INSERT OR REPLACE INTO financial_data (
        stock_code,year,quarter,is_annual,revenue,operating_profit,net_income,
        total_assets,total_liabilities,total_equity,capital_stock,
        eps,bps,dps,cash,total_shares,depreciation_amortization,created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
    (code,year,qnum,int(is_annual),m.get("revenue"),m.get("operating_profit"),
     m.get("net_income"),m.get("total_assets"),m.get("total_liabilities"),
     m.get("total_equity"),m.get("capital_stock"),m.get("eps"),m.get("bps"),
     m.get("dps"),m.get("cash"),m.get("total_shares"),m.get("depreciation_amortization")))
    conn.commit()

def get_codes(conn):
    codes = set()
    for tbl, col, cond in [
        ("watchlist","stock_code",""),
        ("portfolio","stock_code","WHERE quantity>0"),
        ("stock_universe","stock_code","WHERE base_date=(SELECT MAX(base_date) FROM stock_universe)")]:
        try:
            for row in conn.execute(f"SELECT {col} FROM {tbl} {cond}"):
                c=str(row[0]).strip().zfill(6)
                if c.isdigit() and len(c)==6: codes.add(c)
        except: pass
    return sorted(codes)

def _latest_quarter():
    m,y=date.today().month,date.today().year
    if m>=11: return (y,3)
    elif m>=8: return (y,2)
    elif m>=5: return (y,1)
    elif m>=3: return (y-1,4)
    else: return (y-1,3)

# ── 단일 종목 수집 ───────────────────────────────────────────────
def collect_one(dart, conn, code, used_api, limit):
    import pandas as pd
    r={"new":0,"skip":0,"fail":0,"api":0}
    reg=_load_dart_corps(dart)
    if reg and len(reg)>100 and code not in reg:
        r["skip"]=1; return r
    ly,lq=_latest_quarter()
    qmap={"11013":1,"11012":2,"11014":3,"11011":4}
    years=list(range(ly,_DEFAULT_START_YEAR-1,-1))
    consec=0
    for year in years:
        yn,yf=0,0
        for rprt,qnum in qmap.items():
            if year>ly or (year==ly and qnum>lq): continue
            if _fin_exists(conn,code,year,qnum): r["skip"]+=1; continue
            if used_api+r["api"]>=limit:
                logger.warning(f"API 한도({limit})도달 — {code} 중단")
                return r
            fn=None
            for fs in ["CFS","OFS"]:
                try:
                    df=dart.finstate_all(code,year,rprt,fs_div=fs)
                    r["api"]+=1
                    if df is not None and not df.empty: fn=df; break
                except: r["api"]+=1
                time.sleep(SLEEP_DART)
            if fn is None or fn.empty:
                try: fn=dart.finstate(code,year,rprt); r["api"]+=1
                except: r["api"]+=1
                time.sleep(SLEEP_DART)
            if fn is None or fn.empty: r["fail"]+=1; yf+=1; continue
            m={k:None for k in["revenue","operating_profit","net_income","total_assets",
               "total_liabilities","total_equity","capital_stock","eps","bps","dps",
               "cash","total_shares","depreciation_amortization"]}
            for _,row in fn.iterrows():
                acc=str(row.get("account_nm","")).replace(" ","")
                vc="thstrm_amount"
                if not acc or vc not in row or pd.isna(row[vc]): continue
                try: val=float(str(row[vc]).replace(",",""))
                except: continue
                if "매출액" in acc or "영업수익" in acc: m["revenue"]=val
                elif "영업이익" in acc: m["operating_profit"]=val
                elif "당기순이익" in acc and "주당" not in acc: m["net_income"]=val
                elif "자산총계" in acc: m["total_assets"]=val
                elif "부채총계" in acc: m["total_liabilities"]=val
                elif "자본총계" in acc: m["total_equity"]=val
                elif "자본금" in acc: m["capital_stock"]=val
                elif any(k in acc for k in["기본주당순이익","주당순이익","기본EPS"]): m["eps"]=val
                elif any(k in acc for k in["주당순자산","1주당순자산가액"]): m["bps"]=val
                elif any(k in acc for k in["주당배당금","주당현금배당금"]): m["dps"]=val
                elif any(k in acc for k in["현금및현금성자산","현금성자산"]): m["cash"]=val
                elif any(k in acc for k in["발행주식수","유통주식수"]): m["total_shares"]=val
                elif "감가상각비" in acc: m["depreciation_amortization"]=val
            try: _save_fin(conn,code,year,qnum,rprt=="11011",m); r["new"]+=1; yn+=1
            except Exception as e: logger.warning(f"[저장]{code} {year}Q{qnum}: {e}"); r["fail"]+=1
        if yn==0 and yf>=len(qmap): consec+=1
        if consec>=2: break
        else:
            if yn>0: consec=0
    return r

# ── 통계 ─────────────────────────────────────────────────────────
def print_stats(conn, codes, progress):
    total=len(codes)
    has=sum(1 for c in codes if _has_any(conn,c))
    ly,lq=_latest_quarter()
    latest=conn.execute(f"SELECT COUNT(DISTINCT stock_code) FROM financial_data WHERE year={ly} AND quarter={lq}").fetchone()[0]
    recs=conn.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0]
    done=len(progress.get("completed_codes",[]))
    remaining=total-has
    print()
    print("━"*50)
    print("📊 재무제표 수집 현황")
    print("━"*50)
    print(f"  전체 대상    : {total:,}개")
    print(f"  재무 있음    : {has:,}개  ({has/total*100:.1f}%)")
    print(f"  재무 없음    : {remaining:,}개")
    print(f"  최신분기     : {latest:,}개 ({ly}Q{lq} 기준)")
    print(f"  총 DB 레코드 : {recs:,}건")
    print(f"  누적 API 호출: {progress.get('total_api_calls',0):,}회")
    print(f"  마지막 실행  : {progress.get('last_run_date','없음')}")
    print(f"  예상 완료    : 약 {max(1,remaining//MAX_STOCKS_DAY)}일 후")
    print("━"*50)

# ── cron 등록 ────────────────────────────────────────────────────
def setup_cron():
    import subprocess
    py  = "/Applications/stock_dashboard/venv/bin/python3"
    scr = "/Applications/stock_dashboard/backfill_financials.py"
    log = "/Applications/stock_dashboard/backfill_financials.log"
    line = f"5 0 * * * cd /Applications/stock_dashboard && {py} {scr} >> {log} 2>&1"
    res = subprocess.run(["crontab","-l"], capture_output=True, text=True)
    existing = res.stdout if res.returncode==0 else ""
    if "backfill_financials" in existing:
        print("✅ cron 이미 등록됨")
        return
    new_cron = existing.rstrip() + "\n" + line + "\n"
    proc = subprocess.run(["crontab","-"], input=new_cron, text=True)
    if proc.returncode==0:
        print("✅ cron 등록 완료!")
        print(f"   {line}")
        print("   매일 00:05 자동 실행")
    else:
        print("❌ 수동 등록 필요. crontab -e 후 아래 줄 추가:")
        print(f"   {line}")

# ── 메인 ─────────────────────────────────────────────────────────
def main():
    parser=argparse.ArgumentParser(description="DART 재무제표 자동 수집")
    parser.add_argument("--stats",      action="store_true")
    parser.add_argument("--setup-cron", action="store_true")
    parser.add_argument("--codes",      nargs="+")
    parser.add_argument("--max-stocks", type=int, default=MAX_STOCKS_DAY)
    parser.add_argument("--reset",      action="store_true")
    parser.add_argument("--missing-only", action="store_true")
    args=parser.parse_args()

    if args.setup_cron: setup_cron(); return
    if not DB_PATH.exists(): print(f"❌ DB 없음: {DB_PATH}"); sys.exit(1)

    conn=sqlite3.connect(str(DB_PATH))
    progress={} if args.reset else load_progress()
    if args.reset:
        PROGRESS_FILE.unlink(missing_ok=True)
        progress={"completed_codes":[],"last_run_date":"","total_new":0,"total_api_calls":0}

    all_codes=([c.zfill(6) for c in args.codes] if args.codes else get_codes(conn))

    if args.stats:
        print_stats(conn,all_codes,progress); conn.close(); return

    completed_set=set(progress.get("completed_codes",[]))
    pending=[c for c in all_codes if not _has_any(conn,c)]
    today_batch=pending[:args.max_stocks]
    today_str=date.today().isoformat()

    logger.info(f"=== backfill 시작 ({today_str}) ===")
    logger.info(f"전체:{len(all_codes):,} 미완료:{len(pending):,} 오늘:{len(today_batch):,}")

    if not today_batch:
        logger.info("✅ 모든 종목 수집 완료!")
        print_stats(conn,all_codes,progress); conn.close(); return

    try:
        import config as _cfg, OpenDartReader
        dart=OpenDartReader(_cfg.DART_API_KEY)
        logger.info(f"DART 초기화 (API키: {_cfg.DART_API_KEY[:8]}...)")
    except Exception as e:
        logger.error(f"DART 초기화 실패: {e}"); conn.close(); sys.exit(1)

    reg=_load_dart_corps(dart)
    t0=time.time()
    tn=ts=tf=ta=0
    newly_done=[]

    for i,code in enumerate(today_batch,1):
        el=time.time()-t0
        per=el/i if i>1 else 0
        eta=per*(len(today_batch)-i)
        eta_s=f"{int(eta//60)}분{int(eta%60)}초" if eta>0 else "-"
        print(f"[{i:4d}/{len(today_batch):4d}] {code}  경과 {int(el//60)}분  ETA {eta_s}  "
              f"API {ta:,}/{DAILY_API_LIMIT}  (신규:{tn} 스킵:{ts} 실패:{tf})", flush=True)
        if ta>=DAILY_API_LIMIT:
            logger.warning(f"오늘 API 한도 도달 — 중단"); break
        try:
            r=collect_one(dart,conn,code,ta,DAILY_API_LIMIT)
            tn+=r["new"]; ts+=r["skip"]; tf+=r["fail"]; ta+=r["api"]
            if r["new"]>0: logger.info(f"  {code}: 신규 {r['new']}건 API {r['api']}회")
            if _has_any(conn,code): newly_done.append(code)
        except Exception as e:
            logger.error(f"  {code}: {e}"); tf+=1
        time.sleep(SLEEP_STOCK)

    progress["completed_codes"]=list(set(completed_set)|set(newly_done))
    progress["last_run_date"]=today_str
    progress["total_new"]=progress.get("total_new",0)+tn
    progress["total_api_calls"]=progress.get("total_api_calls",0)+ta
    save_progress(progress)

    elapsed=time.time()-t0
    print()
    print("━"*60)
    print(f"✅ 오늘 수집 완료!")
    print(f"   소요:{int(elapsed//60)}분{int(elapsed%60)}초  신규:{tn:,}건  API:{ta:,}/{DAILY_API_LIMIT:,}회")
    remaining=len([c for c in all_codes if not _has_any(conn,c)])
    print(f"   남은 종목: {remaining:,}개 (약 {max(1,remaining//MAX_STOCKS_DAY)}일 후 완료)")
    print("━"*60)
    print_stats(conn,all_codes,progress)
    conn.close()

if __name__=="__main__":
    main()
