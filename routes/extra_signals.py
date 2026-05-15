"""
routes/extra_signals.py — 개별종목 추가 시그널 API
  고용(1/3/6개월) / 수출계약(월별추이+트렌드설명) / 섹터(인덱스+섹터내종목평균)
  / 수급(5/10/30일) / ETF비중추이(전일/5일대비) / ETF편입여부
"""
import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter

router = APIRouter()

MAIN_DB = "stock.db"
EMP_DB  = "employment_monitor/employment.db"
ETF_DB  = "ETF_check/etf_check.db"
HS_DB   = "hs_trade_lab/data/hs_trade_lab.db"

SECTOR_MAP = {
    "IT": "정보기술", "경기소비재": "경기소비재", "금융": "금융",
    "산업재": "산업재", "소재": "소재", "에너지": "에너지/화학",
    "의료": "헬스케어", "통신서비스": "커뮤니케이션서비스",
    "필수소비재": "생활소비재", "유틸리티": "유틸리티",
    "중공업": "중공업", "건설": "건설",
}


def _main_conn():
    c = sqlite3.connect(MAIN_DB, timeout=10); c.row_factory = sqlite3.Row; return c

def _emp_conn():
    c = sqlite3.connect(EMP_DB, timeout=10);  c.row_factory = sqlite3.Row; return c

def _etf_conn():
    c = sqlite3.connect(ETF_DB, timeout=10);  c.row_factory = sqlite3.Row; return c

def _sig(val):
    if val is None: return "gray"
    return "green" if val > 0 else ("red" if val < 0 else "yellow")


def _employment_grade(net_3m: int | float | None) -> tuple[str, str]:
    """고용 시그널 공통 판정 (NPS/WLB 3개월 합산 기준)."""
    if net_3m is None:
        return "gray", "데이터 없음"
    if net_3m >= 1000:
        return "green", "고용 빠르게 증가"
    if net_3m >= 300:
        return "green", "고용 증가"
    if net_3m >= 0:
        return "yellow", "고용 소폭 증가"
    if net_3m >= -300:
        return "yellow", "고용 소폭 감소"
    return "red", "고용 감소"


def _employment_grade_annual(net_1y: int | float | None) -> tuple[str, str]:
    """고용 시그널 판정 — 연간 데이터만 있을 때 적용."""
    if net_1y is None:
        return "gray", "데이터 없음"
    if net_1y >= 3000:
        return "green", "고용 빠르게 증가"
    if net_1y >= 1000:
        return "green", "고용 증가"
    if net_1y >= 0:
        return "yellow", "고용 소폭 증가"
    if net_1y >= -1000:
        return "yellow", "고용 소폭 감소"
    return "red", "고용 감소"


def _ym_month_gap(ym_a: str, ym_b: str) -> int:
    """두 YYYYMM 문자열 사이의 월 차이 (양수)."""
    ya, ma = int(ym_a[:4]), int(ym_a[4:6])
    yb, mb = int(ym_b[:4]), int(ym_b[4:6])
    return abs((ya * 12 + ma) - (yb * 12 + mb))


# ──────────────────────────────────────────────
# 고용 시그널 (1/3/6개월 집계)
# ──────────────────────────────────────────────
def _get_employment_signal(code: str) -> dict:
    try:
        conn = _emp_conn()
        rows = conn.execute(
            "SELECT data_ym, new_hires, terminations, net_change "
            "FROM nps_monthly WHERE stock_code=? ORDER BY data_ym DESC LIMIT 13",
            (code,),
        ).fetchall()

        if not rows:
            # ── employment_company (bizr_no 직접조회, 정확도 우선) ─────────
            ec_rows = conn.execute(
                "SELECT ym, worker_count FROM employment_company "
                "WHERE stock_code=? AND worker_count>0 ORDER BY ym DESC LIMIT 13",
                (code,),
            ).fetchall()

            if len(ec_rows) >= 2:
                # YYYY-MM → YYYYMM 변환 후 wlb_monthly와 동일한 로직 적용
                ec_data = [{"data_ym": r[0].replace("-", ""), "total_workers": r[1]} for r in ec_rows]
                ec_asc  = list(reversed(ec_data))

                gaps = [_ym_month_gap(ec_asc[i-1]["data_ym"], ec_asc[i]["data_ym"]) for i in range(1, len(ec_asc))]
                monthly_valid = all(g <= 2 for g in gaps)

                latest = ec_data[0]; oldest = ec_data[-1]
                diff = (latest["total_workers"] or 0) - (oldest["total_workers"] or 0)
                total_gap = _ym_month_gap(oldest["data_ym"], latest["data_ym"])
                pct = diff / max(oldest["total_workers"] or 1, 1) * 100

                if monthly_valid:
                    monthly_net = [(ec_asc[i]["total_workers"] or 0) - (ec_asc[i-1]["total_workers"] or 0) for i in range(1, len(ec_asc))]
                    net_1m = monthly_net[-1]; net_3m = sum(monthly_net[-3:])
                    net_6m = sum(monthly_net[-6:]) if len(monthly_net) >= 6 else None
                    net_1y = sum(monthly_net[-12:]) if len(monthly_net) >= 12 else None
                    signal, label = _employment_grade(net_3m)
                    prev_3m = sum(monthly_net[-6:-3]) if len(monthly_net) >= 6 else None
                    return {
                        "signal": signal, "label": label, "source": "ec",
                        "net_1m": net_1m, "net_3m": net_3m, "net_6m": net_6m, "net_1y": net_1y,
                        "current_workers": latest["total_workers"],
                        "detail": {"history": ec_asc, "diff": diff, "pct": pct,
                                   "net_1m": net_1m, "net_3m": net_3m, "net_6m": net_6m, "prev_3m": prev_3m},
                    }
                else:
                    # 가장 최신 2개 레코드로 비교 (전체 기간 대신 직전 스냅샷과 비교)
                    prev_rec = ec_asc[-2]  # 두 번째 최신 = 직전 데이터
                    latest_rec = ec_asc[-1]
                    adj_diff = (latest_rec["total_workers"] or 0) - (prev_rec["total_workers"] or 0)
                    adj_gap  = _ym_month_gap(prev_rec["data_ym"], latest_rec["data_ym"])
                    # 24개월 이내 비교면 유효
                    net_1y_val = adj_diff if adj_gap <= 24 else None
                    signal, label = _employment_grade_annual(net_1y_val)
                    return {
                        "signal": signal, "label": label, "source": "ec_annual",
                        "net_1m": None, "net_3m": None, "net_6m": None,
                        "net_1y": net_1y_val,
                        "current_workers": latest["total_workers"],
                        "detail": {"history": ec_asc, "diff": diff, "pct": pct,
                                   "net_1m": None, "net_3m": None, "net_6m": None, "prev_3m": None},
                    }

            # ── wlb_monthly (사업장명 매칭, 보조 fallback) ──────────────────
            wrows = conn.execute(
                "SELECT data_ym, total_workers FROM wlb_monthly "
                "WHERE stock_code=? ORDER BY data_ym DESC LIMIT 13",
                (code,),
            ).fetchall()
            if len(wrows) < 2:
                return {"signal": "gray", "label": "데이터 없음", "detail": None}

            wdesc = [dict(r) for r in wrows]  # latest → old
            wasc = list(reversed(wdesc))       # old → latest

            # 인접 데이터 간 월 간격 확인 — 간격이 2개월 초과이면 연간 비교로만 처리
            gaps = [
                _ym_month_gap(wasc[i - 1]["data_ym"], wasc[i]["data_ym"])
                for i in range(1, len(wasc))
            ]
            monthly_data_valid = all(g <= 2 for g in gaps)

            latest = wdesc[0]
            oldest = wdesc[-1]
            diff = (latest["total_workers"] or 0) - (oldest["total_workers"] or 0)
            total_gap = _ym_month_gap(oldest["data_ym"], latest["data_ym"])
            pct = diff / max(oldest["total_workers"] or 1, 1) * 100

            if monthly_data_valid:
                # 연속 월별 데이터: 1/3/6개월 순증 계산
                monthly_net = [
                    (wasc[i]["total_workers"] or 0) - (wasc[i - 1]["total_workers"] or 0)
                    for i in range(1, len(wasc))
                ]
                net_1m = monthly_net[-1]
                net_3m = sum(monthly_net[-3:])
                net_6m = sum(monthly_net[-6:]) if len(monthly_net) >= 6 else None
                net_1y = sum(monthly_net[-12:]) if len(monthly_net) >= 12 else None
                signal, label = _employment_grade(net_3m)
                prev_3m = sum(monthly_net[-6:-3]) if len(monthly_net) >= 6 else None
                return {
                    "signal": signal, "label": label, "source": "wlb",
                    "net_1m": net_1m, "net_3m": net_3m,
                    "net_6m": net_6m, "net_1y": net_1y,
                    "current_workers": latest["total_workers"],
                    "detail": {
                        "history": [dict(r) for r in wasc],
                        "diff": diff, "pct": pct,
                        "net_1m": net_1m, "net_3m": net_3m,
                        "net_6m": net_6m, "prev_3m": prev_3m,
                    },
                }
            else:
                # 데이터 간격이 넓음(연간 스냅샷) — 연간 변화만 유효
                signal, label = _employment_grade_annual(diff if total_gap <= 15 else None)
                return {
                    "signal": signal, "label": label, "source": "wlb_annual",
                    "net_1m": None, "net_3m": None, "net_6m": None,
                    "net_1y": diff if total_gap <= 15 else None,
                    "current_workers": latest["total_workers"],
                    "detail": {
                        "history": [dict(r) for r in wasc],
                        "diff": diff, "pct": pct,
                        "net_1m": None, "net_3m": None,
                        "net_6m": None, "prev_3m": None,
                    },
                }

        data = [dict(r) for r in reversed(rows)]
        net_1m = sum((r["net_change"] or 0) for r in data[-1:])
        net_3m = sum((r["net_change"] or 0) for r in data[-3:])
        net_6m = sum((r["net_change"] or 0) for r in data[-6:]) if len(data) >= 6 else None

        signal, label = _employment_grade(net_3m)

        prev_3m = sum((r["net_change"] or 0) for r in data[-6:-3]) if len(data) >= 6 else None

        return {
            "signal": signal, "label": label, "source": "nps",
            "net_1m": net_1m, "net_3m": net_3m, "net_6m": net_6m,
            "current_workers": None,  # NPS는 총 인원수 데이터 없음
            "detail": {
                "history":    data,
                "net_1m":     net_1m,
                "net_3m":     net_3m,
                "net_6m":     net_6m,
                "prev_3m":    prev_3m,
            },
        }
    except Exception:
        return {"signal": "gray", "label": "데이터 없음", "detail": None,
                "net_1m": None, "net_3m": None, "net_6m": None, "current_workers": None}
    finally:
        try: conn.close()
        except: pass


# ──────────────────────────────────────────────
# 수출입(해외계약) 시그널 — 월별 추이 + 트렌드 설명
# ──────────────────────────────────────────────
def _make_trend_desc(monthly_cnts: list) -> str:
    """월별 건수 리스트(오래된→최신)에서 트렌드 설명 생성."""
    if len(monthly_cnts) < 2:
        return ""
    increases = 0; decreases = 0; streak_type = None
    for i in range(len(monthly_cnts) - 1, 0, -1):
        cur = monthly_cnts[i]; prev = monthly_cnts[i-1]
        if cur > prev:
            if streak_type in (None, "up"):
                increases += 1; streak_type = "up"
            else:
                break
        elif cur < prev:
            if streak_type in (None, "down"):
                decreases += 1; streak_type = "down"
            else:
                break
        else:
            break

    if streak_type == "up"   and increases >= 2: return f"{increases}개월 연속 증가"
    if streak_type == "down" and decreases >= 2: return f"{decreases}개월 연속 감소"

    # 혼합 패턴: 최근 N개 증가 후 M개 감소 or vice versa
    up_streak = 0; dn_streak = 0; phase = None
    for i in range(len(monthly_cnts) - 1, 0, -1):
        cur = monthly_cnts[i]; prev = monthly_cnts[i-1]
        diff = 1 if cur > prev else (-1 if cur < prev else 0)
        if phase is None:
            if diff == 1: phase = "up"; up_streak = 1
            elif diff == -1: phase = "down"; dn_streak = 1
        elif phase == "up":
            if diff == 1: up_streak += 1
            elif diff == -1: phase = "down_after_up"; dn_streak = 1
            else: break
        elif phase == "down":
            if diff == -1: dn_streak += 1
            elif diff == 1: phase = "up_after_down"; up_streak = 1
            else: break
        else:
            break

    if phase == "down_after_up" and up_streak >= 2:
        return f"{up_streak}개월 증가 후 {dn_streak}개월 감소"
    if phase == "up_after_down" and dn_streak >= 2:
        return f"{dn_streak}개월 감소 후 {up_streak}개월 증가"
    return ""


def _get_hs_export_info(code: str) -> dict | None:
    """hs_trade_lab DB에서 종목 월별 수출실적 + 공동 매핑 종목 조회."""
    try:
        c = sqlite3.connect(HS_DB, timeout=5); c.row_factory = sqlite3.Row

        # 월별 수출 합산
        rows = c.execute(
            """SELECT period_ym, SUM(export_value) AS export_val
               FROM analysis2_company_monthly_cache
               WHERE stock_code=?
               GROUP BY period_ym ORDER BY period_ym DESC LIMIT 13""",
            (code,),
        ).fetchall()
        if not rows:
            c.close(); return None

        # 공동 매핑 종목: 동일 HS 코드(수출)를 함께 사용하는 다른 종목 (최대 20개 후보)
        shared_rows = c.execute(
            """SELECT DISTINCT m2.stock_code, m2.stock_name
               FROM hs_code_company_map m1
               JOIN hs_code_company_map m2 ON m1.hs_code = m2.hs_code
               WHERE m1.stock_code=? AND m2.stock_code!=?
                 AND m1.flow_type='export' AND m2.flow_type='export'
               ORDER BY m2.stock_name LIMIT 20""",
            (code, code),
        ).fetchall()
        all_shared_codes = [r["stock_code"] for r in shared_rows]
        all_shared = {r["stock_code"]: r["stock_name"] for r in shared_rows}

        c.close()

        # sector_large 조회 (조회 종목 기준) 후 동일 섹터 종목만 공동 표시
        try:
            mc = _main_conn()
            own_sector = mc.execute(
                "SELECT sector_large FROM stock_universe WHERE stock_code=?", (code,)
            ).fetchone()
            own_sector = own_sector["sector_large"] if own_sector else None

            if own_sector and all_shared_codes:
                placeholders = ",".join("?" * len(all_shared_codes))
                same_sector = mc.execute(
                    f"SELECT stock_code FROM stock_universe "
                    f"WHERE stock_code IN ({placeholders}) AND sector_large=?",
                    all_shared_codes + [own_sector],
                ).fetchall()
                same_sector_codes = {r["stock_code"] for r in same_sector}
                shared = [{"code": sc, "name": all_shared[sc]}
                          for sc in all_shared_codes if sc in same_sector_codes][:10]
            else:
                shared = [{"code": sc, "name": all_shared[sc]} for sc in all_shared_codes][:10]
            shared_hs_cnt = len(shared)
        except Exception:
            shared = [{"code": sc, "name": all_shared[sc]} for sc in all_shared_codes][:10]
            shared_hs_cnt = len(shared)
        finally:
            try: mc.close()
            except: pass

        monthly = list(reversed([dict(r) for r in rows]))  # oldest→newest
        vals = [r["export_val"] or 0 for r in monthly]
        trend_desc = _make_trend_desc(vals)
        latest = monthly[-1]
        prev   = monthly[-2] if len(monthly) >= 2 else None
        mom_pct = round((latest["export_val"] / prev["export_val"] - 1) * 100, 1) \
                  if prev and prev["export_val"] else None
        return {
            "latest_ym":      latest["period_ym"],
            "latest_val":     latest["export_val"],
            "mom_pct":        mom_pct,
            "trend_desc":     trend_desc,
            "monthly":        monthly,
            "shared_stocks":  shared,          # 공동 매핑 종목 목록
            "shared_hs_cnt":  shared_hs_cnt,   # 공유 HS 코드 수
        }
    except Exception:
        return None


def _get_exports_signal(code: str) -> dict:
    export_info   = _get_hs_export_info(code)
    contract_info = None

    try:
        conn  = _main_conn()
        today = date.today()
        since = today - timedelta(days=365)
        rows  = conn.execute(
            """SELECT disclosed_at, contract_type, contract_amount_krw,
                      counterparty, counterparty_country, is_overseas, ai_summary
               FROM dart_contracts
               WHERE stock_code=? AND disclosed_at >= ?
               ORDER BY disclosed_at DESC""",
            (code, since.isoformat()),
        ).fetchall()
        if rows:
            total_amt = sum((r["contract_amount_krw"] or 0) for r in rows)
            contract_info = {
                "count":         len(rows),
                "total_amt_억":  round(total_amt / 1e8),
                "items": [{"date":        r["disclosed_at"],
                           "type":        r["contract_type"],
                           "amt_억":      round((r["contract_amount_krw"] or 0) / 1e8),
                           "counterparty": r["counterparty"],
                           "is_overseas": r["is_overseas"],
                           "summary":     r["ai_summary"]}
                          for r in rows[:20]],
            }
    except Exception:
        pass
    finally:
        try: conn.close()
        except: pass

    # 대표 시그널: 수출 트렌드 우선
    if export_info:
        vals = [r["export_val"] or 0 for r in export_info["monthly"]]
        if len(vals) >= 3 and vals[-1] > vals[-2] >= vals[-3]: signal = "green"
        elif len(vals) >= 2 and vals[-1] > vals[-2]:           signal = "green"
        elif len(vals) >= 3 and vals[-1] < vals[-2] <= vals[-3]: signal = "red"
        elif len(vals) >= 2 and vals[-1] < vals[-2]:           signal = "red"
        else:                                                   signal = "yellow"
        if export_info.get("trend_desc"):
            label = export_info["trend_desc"]
        else:
            label = "수출 증가" if signal == "green" else ("수출 감소" if signal == "red" else "수출 보합")
    elif contract_info:
        signal = "green" if contract_info["count"] >= 3 else "yellow"
        label  = f"계약 {contract_info['count']}건"
    else:
        signal, label = "gray", "데이터 없음"

    return {
        "signal":    signal,
        "label":     label,
        "export":    export_info,
        "contracts": contract_info,
        "detail":    {"export": export_info, "contracts": contract_info},
    }


# ──────────────────────────────────────────────
# 섹터 트렌드 시그널 — 섹터 인덱스 + 섹터 내 종목 평균 수익률
# ──────────────────────────────────────────────
def _get_sector_trend_signal(code: str) -> dict:
    try:
        conn = _main_conn()
        row = conn.execute(
            "SELECT sector_large, sector_mid, market FROM stock_universe WHERE stock_code=?",
            (code,),
        ).fetchone()
        if not row or not row["sector_large"]:
            return {"signal_5d": "gray", "signal_10d": "gray", "signal_30d": "gray",
                    "label": "섹터 정보 없음", "detail": None}

        sector_key = row["sector_large"]
        sector_mid = row["sector_mid"]
        market     = row["market"] or "유가증권"
        sid_sector = SECTOR_MAP.get(sector_key)
        mkt = "KOSPI" if "유가" in market or market == "KOSPI" else "KOSDAQ"

        # ── 1. 섹터 인덱스 (sector_index_daily) ─────────────────
        idx_hist = []
        if sid_sector:
            idx_hist = conn.execute(
                "SELECT date, close, change_rate FROM sector_index_daily "
                "WHERE sector=? AND market=? ORDER BY date DESC LIMIT 35",
                (sid_sector, mkt),
            ).fetchall()

        # ── 2. 섹터 내 종목 평균 수익률 (sector_large 기반) ──────
        # sector_mid 대신 sector_large 기준으로 전체 종목 일관 분류
        avg_returns = {"5d": None, "10d": None, "30d": None}
        sector_stock_cnt = 0
        if sector_key:
            codes_rows = conn.execute(
                "SELECT stock_code FROM stock_universe "
                "WHERE sector_large=? AND market IN ('유가증권','코스닥','KOSPI','KOSDAQ') LIMIT 300",
                (sector_key,),
            ).fetchall()
            sector_codes = [r["stock_code"] for r in codes_rows]
            sector_stock_cnt = len(sector_codes)

            if sector_codes:
                placeholders = ",".join("?" * len(sector_codes))
                ranked = conn.execute(
                    f"""WITH ranked AS (
                          SELECT stock_code, close, date,
                                 ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                          FROM price_history
                          WHERE stock_code IN ({placeholders}) AND close > 0
                        )
                        SELECT rn, AVG(close) AS avg_close
                        FROM ranked WHERE rn IN (1,5,10,30)
                        GROUP BY rn ORDER BY rn""",
                    sector_codes,
                ).fetchall()

                rn_map = {r["rn"]: r["avg_close"] for r in ranked}
                c1 = rn_map.get(1)
                if c1:
                    def _pct(n):
                        cn = rn_map.get(n)
                        return round((c1 - cn) / cn * 100, 2) if cn else None
                    avg_returns = {"5d": _pct(5), "10d": _pct(10), "30d": _pct(30)}

        def sig_chg(c):
            if c is None: return "gray"
            if c >= 2: return "green"
            if c >= -2: return "yellow"
            return "red"

        # 섹터 인덱스 변화율
        from datetime import datetime as _dt
        idx_c5 = idx_c10 = idx_c30 = None
        latest_date = None
        if idx_hist and len(idx_hist) >= 2:
            latest_dt = _dt.strptime(idx_hist[0]["date"], "%Y-%m-%d").date()
            latest_date = idx_hist[0]["date"]

            def _idx_chg(n, max_cal):
                if len(idx_hist) <= n: return None
                base_row = idx_hist[n]
                base_dt  = _dt.strptime(base_row["date"], "%Y-%m-%d").date()
                if (latest_dt - base_dt).days > max_cal: return None
                base = base_row["close"]
                return round((idx_hist[0]["close"] - base) / base * 100, 2) if base else None

            idx_c5  = _idx_chg(5, 20)
            idx_c10 = _idx_chg(10, 30)
            idx_c30 = _idx_chg(30, 60)

        # 대표 신호: 섹터 내 종목 평균 우선, 없으면 인덱스
        c5  = avg_returns["5d"]  if avg_returns["5d"]  is not None else idx_c5
        c10 = avg_returns["10d"] if avg_returns["10d"] is not None else idx_c10
        c30 = avg_returns["30d"] if avg_returns["30d"] is not None else idx_c30

        return {
            "signal_5d":  sig_chg(c5),
            "signal_10d": sig_chg(c10),
            "signal_30d": sig_chg(c30),
            "label":      sid_sector or sector_key,
            "sector_key": sector_key,
            "sector_mid": sector_mid,
            "latest_date": latest_date,
            "chg_5d":  c5,  "chg_10d": c10, "chg_30d": c30,
            "idx_5d":  idx_c5,  "idx_10d": idx_c10, "idx_30d": idx_c30,
            "avg_5d":  avg_returns["5d"], "avg_10d": avg_returns["10d"],
            "avg_30d": avg_returns["30d"],
            "sector_stock_cnt": sector_stock_cnt,
            "detail": {
                "sector_name":       sid_sector or sector_key,
                "sector_mid":        sector_mid,
                "market":            mkt,
                "sector_stock_cnt":  sector_stock_cnt,
                "idx_chg": {"5d": idx_c5, "10d": idx_c10, "30d": idx_c30},
                "avg_chg": avg_returns,
                "history": [{"date": r["date"], "close": r["close"],
                             "change_rate": r["change_rate"]}
                            for r in reversed(idx_hist)],
            },
        }
    except Exception:
        return {"signal_5d": "gray", "signal_10d": "gray", "signal_30d": "gray",
                "label": "오류", "detail": None}
    finally:
        try: conn.close()
        except: pass


# ──────────────────────────────────────────────
# 외국인/기관 수급 시그널
# ──────────────────────────────────────────────
def _get_supply_signal(code: str) -> dict:
    try:
        conn = _main_conn()
        rows = conn.execute(
            "SELECT date, close, frn_net_buy, inst_net_buy, frn_net_buy_amt, inst_net_buy_amt "
            "FROM price_history WHERE stock_code=? AND close>0 "
            "ORDER BY date DESC LIMIT 32",
            (code,),
        ).fetchall()

        if not rows:
            return {k: "gray" for k in
                    ["signal_frn_5d","signal_frn_10d","signal_frn_30d",
                     "signal_inst_5d","signal_inst_10d","signal_inst_30d"]} | \
                   {"label": "데이터 없음", "detail": None}

        rows_list = list(rows)

        def agg(lst, fa, qa):
            return sum((r[fa] or 0) for r in lst), sum((r[qa] or 0) for r in lst)

        r5, r10, r30 = rows_list[:5], rows_list[:10], rows_list[:30]
        frn_a5,  _  = agg(r5,  "frn_net_buy_amt",  "frn_net_buy")
        frn_a10, _  = agg(r10, "frn_net_buy_amt",  "frn_net_buy")
        frn_a30, _  = agg(r30, "frn_net_buy_amt",  "frn_net_buy")
        ins_a5,  _  = agg(r5,  "inst_net_buy_amt", "inst_net_buy")
        ins_a10, _  = agg(r10, "inst_net_buy_amt", "inst_net_buy")
        ins_a30, _  = agg(r30, "inst_net_buy_amt", "inst_net_buy")

        def to억(v): return round(v / 100.0) if v else 0

        return {
            "signal_frn_5d":  _sig(frn_a5),  "signal_frn_10d": _sig(frn_a10),
            "signal_frn_30d": _sig(frn_a30),
            "signal_inst_5d": _sig(ins_a5),  "signal_inst_10d": _sig(ins_a10),
            "signal_inst_30d": _sig(ins_a30),
            "frn_amt_5d":  to억(frn_a5),  "frn_amt_10d": to억(frn_a10),
            "frn_amt_30d": to억(frn_a30),
            "inst_amt_5d": to억(ins_a5),  "inst_amt_10d": to억(ins_a10),
            "inst_amt_30d": to억(ins_a30),
            "label": "수급 데이터",
            "detail": {
                "history": [{"date": r["date"],
                             "frn_amt":  to억(r["frn_net_buy_amt"] or 0),
                             "inst_amt": to억(r["inst_net_buy_amt"] or 0)}
                            for r in reversed(rows[:30])],
            },
        }
    except Exception:
        return {k: "gray" for k in
                ["signal_frn_5d","signal_frn_10d","signal_frn_30d",
                 "signal_inst_5d","signal_inst_10d","signal_inst_30d"]} | \
               {"label": "오류", "detail": None}
    finally:
        try: conn.close()
        except: pass


# ──────────────────────────────────────────────
# ETF 시총대비 편입비율 추이 (6.5) — 전일/5일전 대비
# ──────────────────────────────────────────────
def _get_etf_ratio_signal(code: str) -> dict:
    try:
        conn = _etf_conn()
        rows = conn.execute(
            "SELECT trade_date, etf_amount, market_cap, mktcap_ratio, etf_count "
            "FROM etf_inclusion_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 15",
            (code,),
        ).fetchall()

        if not rows:
            return {"signal": "gray", "label": "데이터 없음", "detail": None}

        # etf_count=0 AND etf_amount=0 인 날은 수집 실패로 간주하고 건너뜀
        valid_rows = [r for r in rows if (r["etf_count"] or 0) > 0 or (r["etf_amount"] or 0) > 0]
        if not valid_rows:
            return {"signal": "gray", "label": "데이터 없음", "detail": None}

        latest = dict(valid_rows[0])
        history = [dict(r) for r in reversed(valid_rows)]

        ratio_now  = latest["mktcap_ratio"] or 0
        ratio_prev = (dict(valid_rows[1])["mktcap_ratio"] or 0) if len(valid_rows) >= 2 else ratio_now
        ratio_5d   = (dict(valid_rows[min(4, len(valid_rows)-1)])["mktcap_ratio"] or 0)

        diff_1d = round(ratio_now - ratio_prev, 3)
        diff_5d = round(ratio_now - ratio_5d,   3)

        # 신규 편입 감지: 이전에 0이었다가 현재 >0
        newly_included = (ratio_prev == 0 and ratio_now > 0)

        if   diff_1d >= 0.1:  signal, label = "green",  f"ETF 비중 증가 ({ratio_now:.2f}%)"
        elif diff_1d >= 0:    signal, label = "yellow", f"ETF 비중 유지 ({ratio_now:.2f}%)"
        elif diff_1d >= -0.1: signal, label = "yellow", f"ETF 비중 소폭 감소 ({ratio_now:.2f}%)"
        else:                 signal, label = "red",    f"ETF 비중 감소 ({ratio_now:.2f}%)"

        if newly_included:
            signal = "green"; label = f"★ ETF 신규 편입 ({ratio_now:.2f}%)"

        return {
            "signal":        signal,
            "label":         label,
            "ratio_now":     round(ratio_now, 2),
            "diff_1d":       diff_1d,
            "diff_5d":       diff_5d,
            "newly_included": newly_included,
            "detail": {"history": history, "latest_date": latest["trade_date"]},
        }
    except Exception:
        return {"signal": "gray", "label": "데이터 없음", "detail": None}
    finally:
        try: conn.close()
        except: pass


# ──────────────────────────────────────────────
# ETF 편입 여부 시그널 (6.6)
# ──────────────────────────────────────────────
def _get_etf_inclusion_signal(code: str) -> dict:
    try:
        conn = _etf_conn()
        # etf_count=0 AND etf_amount=0 인 날(수집 실패)을 제외하고 가장 최근 유효 데이터 사용
        rows = conn.execute(
            "SELECT trade_date, etf_count, etf_amount, mktcap_ratio "
            "FROM etf_inclusion_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 10",
            (code,),
        ).fetchall()

        if not rows:
            return {"signal": "gray", "label": "데이터 없음",
                    "etf_count": 0, "detail": None}

        valid = next(
            (r for r in rows if (r["etf_count"] or 0) > 0 or (r["etf_amount"] or 0) > 0),
            None,
        )
        row = dict(valid if valid else rows[0])
        etf_count = row["etf_count"] or 0
        ratio     = row["mktcap_ratio"] or 0
        # etf_amount은 억원 단위로 저장됨 (원 단위 아님)
        amt_억    = round(row["etf_amount"] or 0)

        signal = "green" if etf_count > 0 else "gray"
        label  = f"ETF {etf_count}개 편입" if etf_count > 0 else "ETF 미편입"

        return {
            "signal":     signal, "label": label,
            "etf_count":  etf_count,
            "ratio":      round(ratio, 2),
            "amt_억":     amt_억,
            "trade_date": row["trade_date"],
            "detail": {
                "etf_count": etf_count, "ratio": round(ratio, 2),
                "amt_억":    amt_억,     "trade_date": row["trade_date"],
            },
        }
    except Exception:
        return {"signal": "gray", "label": "데이터 없음", "etf_count": 0, "detail": None}
    finally:
        try: conn.close()
        except: pass


# ──────────────────────────────────────────────
# 메인 엔드포인트
# ──────────────────────────────────────────────
@router.get("/extra-signals/{code}")
def get_extra_signals(code: str):
    if not code or not code.strip():
        return {}
    return {
        "employment":    _get_employment_signal(code),
        "exports":       _get_exports_signal(code),
        "sector_trend":  _get_sector_trend_signal(code),
        "supply":        _get_supply_signal(code),
        "etf_ratio":     _get_etf_ratio_signal(code),
        "etf_inclusion": _get_etf_inclusion_signal(code),
    }
