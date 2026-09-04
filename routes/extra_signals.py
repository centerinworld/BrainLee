"""
routes/extra_signals.py — 개별종목 추가 시그널 API
  고용(1/3/6개월) / 수출계약(월별추이+트렌드설명) / 섹터(인덱스+섹터내종목평균)
  / 수급(5/10/30일) / ETF비중추이(전일/5일대비) / ETF편입여부
"""
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from fastapi import APIRouter

logger = logging.getLogger(__name__)
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

        # current_workers 우선 확보: employment_company(bizr_no 기반) → wlb_monthly 최신값
        def _get_current_workers():
            ec = conn.execute(
                "SELECT worker_count FROM employment_company "
                "WHERE stock_code=? AND worker_count>0 ORDER BY ym DESC LIMIT 1",
                (code,),
            ).fetchone()
            if ec:
                return ec[0]
            wlb = conn.execute(
                "SELECT total_workers FROM wlb_monthly "
                "WHERE stock_code=? AND total_workers>0 ORDER BY data_ym DESC LIMIT 1",
                (code,),
            ).fetchone()
            return wlb[0] if wlb else None

        rows = conn.execute(
            "SELECT data_ym, new_hires, terminations, net_change "
            "FROM nps_monthly WHERE stock_code=? ORDER BY data_ym DESC LIMIT 13",
            (code,),
        ).fetchall()

        # NPS가 전부 0이면 신뢰하지 않고 WLB/EC 경로로 전환
        nps_all_zero = rows and all((r[3] or 0) == 0 for r in rows)

        if not rows or nps_all_zero:
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

            # 이상값 필터링: 인접 행 간 변동이 기준값의 50% 초과이면 사업장 매칭 오류로 제거
            def _is_wlb_outlier(prev_w, curr_w):
                if prev_w is None or curr_w is None or prev_w == 0:
                    return False
                return abs(curr_w - prev_w) / prev_w > 0.5

            filtered_asc = [wasc[0]]
            for i in range(1, len(wasc)):
                pw = filtered_asc[-1]["total_workers"]
                cw = wasc[i]["total_workers"]
                if not _is_wlb_outlier(pw, cw):
                    filtered_asc.append(wasc[i])
            wasc = filtered_asc
            wdesc = list(reversed(wasc))

            if len(wdesc) < 2:
                return {"signal": "gray", "label": "데이터 없음", "detail": None}

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
            "current_workers": _get_current_workers(),  # EC/WLB에서 실제 인원수 보완
            "detail": {
                "history":    data,
                "net_1m":     net_1m,
                "net_3m":     net_3m,
                "net_6m":     net_6m,
                "prev_3m":    prev_3m,
            },
        }
    except Exception as _e:
        logger.warning("고용 시그널 조회 실패 [%s]: %s", code if 'code' in dir() else '?', _e)
        return {"signal": "gray", "label": "데이터 없음", "detail": None,
                "net_1m": None, "net_3m": None, "net_6m": None, "current_workers": None}
    finally:
        try: conn.close()
        except Exception: pass


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

        # 최신월 HS코드별 수출 상세(복수 HS코드 전부 노출용)
        latest_ym = rows[0]["period_ym"]
        hs_rows = c.execute(
            """SELECT hs_code, hs_name, flow_type, mapping_status,
                      SUM(export_value) AS export_val,
                      SUM(import_value) AS import_val
               FROM analysis2_company_hs_monthly_cache
               WHERE stock_code=? AND period_ym=?
               GROUP BY hs_code, hs_name, flow_type, mapping_status
               ORDER BY export_val DESC, hs_code""",
            (code, latest_ym),
        ).fetchall()

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
        except Exception as _e:
            logger.debug("HS 섹터 필터링 실패, 전체 반환: %s", _e)
            shared = [{"code": sc, "name": all_shared[sc]} for sc in all_shared_codes][:10]
            shared_hs_cnt = len(shared)
        finally:
            try: mc.close()
            except Exception: pass

        monthly = list(reversed([dict(r) for r in rows]))  # oldest→newest
        vals = [r["export_val"] or 0 for r in monthly]
        trend_desc = _make_trend_desc(vals)
        latest = monthly[-1]
        prev   = monthly[-2] if len(monthly) >= 2 else None
        mom_pct = round((latest["export_val"] / prev["export_val"] - 1) * 100, 1) \
                  if prev and prev["export_val"] else None
        hs_items = []
        for r in hs_rows:
            hs_items.append({
                "hs_code": r["hs_code"],
                "hs_name": r["hs_name"],
                "flow_type": r["flow_type"] or "export",
                "export_val": r["export_val"] or 0,
                "import_val": r["import_val"] or 0,
                # ⚠️ 2026-08-23: HS코드-기업 매핑은 exact(확정)/composite/provisional(잠정)
                # 신뢰도가 있음(hs_code_company_map.mapping_status) — 이 필드가 없으면
                # 잠정 매핑도 확정처럼 보여 오인될 수 있어 그대로 전달한다.
                "mapping_status": r["mapping_status"],
            })

        return {
            "latest_ym":      latest["period_ym"],
            "latest_val":     latest["export_val"],
            "mom_pct":        mom_pct,
            "trend_desc":     trend_desc,
            "monthly":        monthly,
            "hs_items":       hs_items,
            "shared_stocks":  shared,          # 공동 매핑 종목 목록
            "shared_hs_cnt":  shared_hs_cnt,   # 공유 HS 코드 수
        }
    except Exception as _e:
        logger.warning("HS 수출 정보 조회 실패 [%s]: %s", code if 'code' in dir() else '?', _e)
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
    except Exception as _e:
        logger.warning("수주공시 조회 실패 [%s]: %s", code if 'code' in dir() else '?', _e)
    finally:
        try: conn.close()
        except Exception: pass

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
                # ROW_NUMBER()가 필요한 건 종목당 최근 순위 1/5/10/30위뿐인데
                # date 하한이 없어 종목당 전체 이력(수년치)을 윈도우 함수로 훑고
                # 있었음(pg_stat_statements 실측: 호출당 평균 3.3초, 이 파일 최대
                # 부하 쿼리). 90일이면 거래일 30위까지 항상 포함되므로 안전하게 제한.
                ranked = conn.execute(
                    f"""WITH ranked AS (
                          SELECT stock_code, close, date,
                                 ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                          FROM price_history
                          WHERE stock_code IN ({placeholders}) AND close > 0
                            AND date >= date('now', '-90 days')
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
    except Exception as _e:
        logger.warning("섹터 시그널 조회 실패 [%s]: %s", code if 'code' in dir() else '?', _e)
        return {"signal_5d": "gray", "signal_10d": "gray", "signal_30d": "gray",
                "label": "오류", "detail": None}
    finally:
        try: conn.close()
        except Exception: pass


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

        # ⚠️ 2026-08-23: 정수(0자리)로 반올림하면 소형주(일일 수급이 억원
        # 단위 미만)는 거의 전부 0/±1억으로 뭉개져, 같은 원본 데이터를 쓰는
        # 상단 패널(백만원 단위 표시)과 값이 달라 보이는 오해를 유발했다.
        # 1자리 소수로 정밀도를 보존한다.
        def to억(v): return round(v / 100.0, 1) if v else 0

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
    except Exception as _e:
        logger.warning("수급 시그널 조회 실패 [%s]: %s", code if 'code' in dir() else '?', _e)
        return {k: "gray" for k in
                ["signal_frn_5d","signal_frn_10d","signal_frn_30d",
                 "signal_inst_5d","signal_inst_10d","signal_inst_30d"]} | \
               {"label": "오류", "detail": None}
    finally:
        try: conn.close()
        except Exception: pass


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
    except Exception as _e:
        logger.warning("ETF 비중 시그널 조회 실패 [%s]: %s", code if 'code' in dir() else '?', _e)
        return {"signal": "gray", "label": "데이터 없음", "detail": None}
    finally:
        try: conn.close()
        except Exception: pass


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
    except Exception as _e:
        logger.warning("ETF 편입 시그널 조회 실패 [%s]: %s", code if 'code' in dir() else '?', _e)
        return {"signal": "gray", "label": "데이터 없음", "etf_count": 0, "detail": None}
    finally:
        try: conn.close()
        except Exception: pass


# ──────────────────────────────────────────────
# 메인 엔드포인트
# ──────────────────────────────────────────────
@router.get("/extra-signals/{code}")
def get_extra_signals(code: str):
    if not code or not code.strip():
        return {}
    # 6개 하위 시그널은 서로 독립적(각기 다른 DB/도메인)인데 순차 실행하면
    # 대기시간이 누적돼 이 엔드포인트 하나가 2~3초 이상 걸리는 게 확인됨
    # (2026-08-14, 개별종목 페이지 로딩 지연의 최대 원인). 병렬 실행으로 단축.
    tasks = {
        "employment":    _get_employment_signal,
        "exports":       _get_exports_signal,
        "sector_trend":  _get_sector_trend_signal,
        "supply":        _get_supply_signal,
        "etf_ratio":     _get_etf_ratio_signal,
        "etf_inclusion": _get_etf_inclusion_signal,
    }
    result: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(fn, code): key for key, fn in tasks.items()}
        for fut, key in futures.items():
            try:
                result[key] = fut.result()
            except Exception as e:
                logger.warning("%s 시그널 조회 실패 [%s]: %s", key, code, e)
                result[key] = None
    return result


# ──────────────────────────────────────────────
# 차트 시그널 v2 (2026-07-18 재설계) — 추세/수급/차트 3섹션 분리, 직관적 문장형
#   사용자 피드백: "추세·수급·차트를 완전 분리, '이탈했다/지지중/돌파했다' 같은
#   직관적 표현, 점수는 각 지표 이해 후 마지막에" — 전문가 관점 보정:
#   · RSI 과매수/과매도는 수급 섹션에 배치(사용자 멘탈모델 기준)하되
#     "기술적 침체/과열"로 정확히 명명
#   · "평균 매수값 대비"는 20일 거래량가중평균가(VWAP20)로 구현 —
#     최근 1개월 시장 참여자의 실질 평균 매수단가 대비 현재가 위치
#   · 진짜 수급(외국인/기관 순매수 5·20일)을 함께 표시
#   순수 로직(AI 미사용), 백테스트 검증된 backtest.py 컨플루언스 모듈 재사용.
# ──────────────────────────────────────────────
@router.get("/chart/{code}")
def get_chart_signals(code: str):
    import backtest as _bt
    conn = _main_conn()
    try:
        rows = conn.execute(
            """SELECT date, close, COALESCE(open,close), COALESCE(high,close),
                      COALESCE(low,close), COALESCE(volume,0),
                      COALESCE(frn_net_buy_amt,0), COALESCE(inst_net_buy_amt,0)
               FROM price_history
               WHERE stock_code=? AND close>0
               ORDER BY date DESC LIMIT 400""",
            (code,)
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 30:
        return {"ok": False, "reason": "가격 데이터 부족"}

    rows = list(reversed(rows))
    dates  = [r[0][:10] for r in rows]
    closes = [float(r[1]) for r in rows]
    opens  = [float(r[2]) for r in rows]
    highs  = [float(r[3]) for r in rows]
    lows   = [float(r[4]) for r in rows]
    vols   = [float(r[5]) for r in rows]
    frn_amt  = [float(r[6]) for r in rows]   # 백만원
    inst_amt = [float(r[7]) for r in rows]   # 백만원
    i = len(closes) - 1
    curr = closes[i]

    def _ma_at(n, idx=None):
        idx = i if idx is None else idx
        if idx + 1 < n:
            return None
        return sum(closes[idx - n + 1: idx + 1]) / n

    ma5, ma10, ma20, ma60, ma120 = _ma_at(5), _ma_at(10), _ma_at(20), _ma_at(60), _ma_at(120)
    chart = _bt._chart_prep(dates, lows, closes)

    # ── 이평선별 상태 판정: 돌파(최근 5일 내 상향교차) / 이탈(하향교차) / 지지중 / 하회중 ──
    def _ma_state(n):
        m_now = _ma_at(n)
        if m_now is None:
            return None
        for back in range(0, 5):
            idx = i - back
            m1, m0 = _ma_at(n, idx), _ma_at(n, idx - 1)
            if m1 is None or m0 is None or idx < 1:
                break
            if closes[idx-1] <= m0 and closes[idx] > m1:
                return {"state": "breakout", "days_ago": back, "ma": m_now}
            if closes[idx-1] >= m0 and closes[idx] < m1:
                return {"state": "breakdown", "days_ago": back, "ma": m_now}
        gap = (curr - m_now) / m_now * 100
        return {"state": "above" if curr >= m_now else "below", "gap_pct": round(gap, 1), "ma": m_now}

    def _ma_statement(n, st):
        if st is None:
            return None
        nm = f"MA{n} 이동평균선"
        if st["state"] == "breakout":
            ago = "오늘" if st["days_ago"] == 0 else f"{st['days_ago']}일 전"
            return {"state": "up", "label": f"{nm}", "statement": f"⚡ {ago} {nm}({round(st['ma']):,}원)을 상향 돌파했다"}
        if st["state"] == "breakdown":
            ago = "오늘" if st["days_ago"] == 0 else f"{st['days_ago']}일 전"
            return {"state": "down", "label": f"{nm}", "statement": f"⚠ {ago} {nm}({round(st['ma']):,}원)을 하향 이탈했다"}
        if st["state"] == "above":
            return {"state": "up", "label": f"{nm}", "statement": f"{nm}({round(st['ma']):,}원) 위에서 지지받는 중 (+{st['gap_pct']}%)"}
        return {"state": "down", "label": f"{nm}", "statement": f"{nm}({round(st['ma']):,}원) 아래에 머무는 중 ({st['gap_pct']}%)"}

    st20, st60, st120 = _ma_state(20), _ma_state(60), _ma_state(120)

    # ── 골든/데드크로스 (MA20 vs MA60, 최근 20일) ──
    cross = None
    if len(closes) >= 80:
        for back in range(0, 20):
            idx = i - back
            a20, a60 = _ma_at(20, idx), _ma_at(60, idx)
            p20, p60 = _ma_at(20, idx - 1), _ma_at(60, idx - 1)
            if None in (a20, a60, p20, p60):
                break
            if p20 <= p60 and a20 > a60:
                cross = {"type": "golden", "days_ago": back}
                break
            if p20 >= p60 and a20 < a60:
                cross = {"type": "dead", "days_ago": back}
                break

    # ══ ① 추세 섹션 ══════════════════════════════════════
    aligned_up = bool(ma20 and ma60 and ma120 and ma20 > ma60 > ma120)
    aligned_down = bool(ma20 and ma60 and ma120 and ma20 < ma60 < ma120)
    short_up = bool(ma5 and ma10 and ma5 > ma10)
    n_wk = chart["i2wk"][i] if i < len(chart["i2wk"]) else 0
    wk_higher_low = bool(n_wk >= 5 and chart["wk_low"][n_wk-1] > min(chart["wk_low"][max(0, n_wk-5):n_wk-1] or [1e18]))
    wk_lower_high = bool(n_wk >= 5 and chart["wk_close"][n_wk-1] < max(chart["wk_close"][max(0, n_wk-5):n_wk-1] or [-1e18]))
    n_mo = chart["i2mo"][i] if i < len(chart["i2mo"]) else 0
    mo_up = bool(n_mo >= 4 and (chart["mo_low"][n_mo-1] > min(chart["mo_low"][max(0, n_mo-4):n_mo-1])
                               or chart["mo_close"][n_mo-1] > chart["mo_close"][n_mo-2]))
    mo_down = bool(n_mo >= 4 and chart["mo_close"][n_mo-1] < chart["mo_close"][n_mo-2])

    # 추세 헤드라인 (중기 기준 우선 — 실전에서 매매 판단의 축)
    if aligned_up and curr > (ma20 or 0):
        trend_headline, trend_state = "상승 추세 진행 중이다", "up"
    elif aligned_up and st20 and st20["state"] in ("breakdown", "below"):
        trend_headline, trend_state = "상승 추세였으나 단기 이탈했다 (조정 진입)", "warn"
    elif aligned_down and curr < (ma20 or 1e18):
        trend_headline, trend_state = "하락 추세가 지속되고 있다", "down"
    elif aligned_down and st20 and st20["state"] in ("breakout", "above"):
        trend_headline, trend_state = "하락 추세에서 탈출을 시도 중이다", "warn"
    elif cross and cross["type"] == "golden":
        trend_headline, trend_state = f"골든크로스 발생({cross['days_ago']}일 전) — 상승 전환 시도 중이다", "up"
    elif cross and cross["type"] == "dead":
        trend_headline, trend_state = f"데드크로스 발생({cross['days_ago']}일 전) — 하락 전환 위험이 있다", "down"
    else:
        trend_headline, trend_state = "뚜렷한 추세 없이 횡보 중이다", "flat"

    trend_items = [
        {"state": "up" if short_up else "down", "label": "단기 (5·10일선)",
         "statement": "단기 반등이 진행 중이다 (5일선 > 10일선)" if short_up else "단기 조정이 진행 중이다 (5일선 < 10일선)"},
        {"state": "up" if aligned_up else ("down" if aligned_down else "flat"), "label": "중기 (20·60·120일선)",
         "statement": ("이평선 정배열 — 중장기 상승 구조다" if aligned_up else
                       "이평선 역배열 — 중장기 하락 구조다" if aligned_down else
                       "이평선 혼조 — 방향 탐색 구간이다")},
        {"state": ("up" if cross and cross["type"] == "golden" else "down" if cross else "flat"),
         "label": "골든/데드크로스",
         "statement": (f"⚡ 골든크로스가 {cross['days_ago']}일 전 발생했다 (20일선이 60일선 상향 돌파)" if cross and cross["type"] == "golden" else
                       f"☠ 데드크로스가 {cross['days_ago']}일 전 발생했다 (20일선이 60일선 하향 이탈)" if cross else
                       "최근 20일 내 교차 없음")},
        {"state": "up" if wk_higher_low else ("down" if wk_lower_high else "flat"), "label": "주봉 (큰 흐름)",
         "statement": ("주 단위 저점이 높아지고 있다 — 바닥 다지기" if wk_higher_low else
                       "주 단위 고점이 낮아지고 있다 — 상단 무거움" if wk_lower_high else "주봉상 뚜렷한 구조 없음")},
        {"state": "up" if mo_up else ("down" if mo_down else "flat"), "label": "월봉 (장기 흐름)",
         "statement": ("월 단위로 저점 상승/반등 중이다" if mo_up else
                       "월 단위로 꺾여 있다 (전월 대비 하락 마감)" if mo_down else "판단 불가 (데이터 부족)")},
    ]

    # ══ ② 수급 섹션 ══════════════════════════════════════
    rsi_now  = _bt._chart_rsi14(closes, i)
    rsi_prev = _bt._chart_rsi14(closes, i - 3) if i >= 17 else None
    rsi_turn_up = bool(rsi_now is not None and rsi_prev is not None and rsi_now > rsi_prev)
    if rsi_now is None:
        rsi_item = {"state": "flat", "label": "과매수/과매도 (RSI)", "statement": "판단 불가 (데이터 부족)"}
    elif rsi_now <= 30:
        rsi_item = {"state": "up" if rsi_turn_up else "down", "label": f"과매수/과매도 (RSI {rsi_now:.0f})",
                    "statement": ("과매도 구간이다 — 낙폭 과대, 반등 전환 시작" if rsi_turn_up else
                                  "과매도 구간이다 — 침체 지속 중, 반전 확인 필요")}
    elif rsi_now >= 70:
        rsi_item = {"state": "down", "label": f"과매수/과매도 (RSI {rsi_now:.0f})",
                    "statement": "과매수 구간이다 — 단기 과열, 추격 매수 주의"}
    else:
        rsi_item = {"state": "flat", "label": f"과매수/과매도 (RSI {rsi_now:.0f})",
                    "statement": "과열도 침체도 아닌 중립 구간이다"}

    # 시장 평균 매수가 (VWAP20) 대비
    v20 = vols[max(0, i-19):i+1]
    c20w = closes[max(0, i-19):i+1]
    vwap20 = (sum(c * v for c, v in zip(c20w, v20)) / sum(v20)) if sum(v20) > 0 else None
    if vwap20:
        vgap = (curr - vwap20) / vwap20 * 100
        if vgap >= 3:
            vwap_item = {"state": "up", "label": "평균 매수가 대비",
                         "statement": f"최근 1개월 평균 매수가({round(vwap20):,}원)보다 +{vgap:.1f}% 위 — 최근 매수자 대부분 이익 구간(매물 부담 낮음)"}
        elif vgap <= -3:
            vwap_item = {"state": "down", "label": "평균 매수가 대비",
                         "statement": f"최근 1개월 평균 매수가({round(vwap20):,}원)보다 {vgap:.1f}% 아래 — 최근 매수자 대부분 손실 구간(반등 시 본전 매물 주의)"}
        else:
            vwap_item = {"state": "flat", "label": "평균 매수가 대비",
                         "statement": f"최근 1개월 평균 매수가({round(vwap20):,}원) 부근 — 손익 공방 구간"}
    else:
        vwap_item = {"state": "flat", "label": "평균 매수가 대비", "statement": "판단 불가 (거래량 데이터 부족)"}

    def _flow_item(amts, who):
        # 백만원 → 억원 (÷100)
        s5 = sum(amts[max(0, i-4):i+1]) / 100.0
        s20 = sum(amts[max(0, i-19):i+1]) / 100.0
        if s5 == 0 and s20 == 0:
            return {"state": "flat", "label": f"{who} 수급", "statement": "수급 데이터 없음"}
        f5 = f"{s5:+,.0f}억"
        f20 = f"{s20:+,.0f}억"
        if s5 > 0 and s20 > 0:
            return {"state": "up", "label": f"{who} 수급", "statement": f"꾸준히 사들이고 있다 (5일 {f5} · 20일 {f20})"}
        if s5 > 0:
            return {"state": "up", "label": f"{who} 수급", "statement": f"최근 매수 전환했다 (5일 {f5}, 20일 {f20})"}
        if s5 < 0 and s20 < 0:
            return {"state": "down", "label": f"{who} 수급", "statement": f"계속 팔고 있다 (5일 {f5} · 20일 {f20})"}
        return {"state": "down", "label": f"{who} 수급", "statement": f"최근 매도 전환했다 (5일 {f5}, 20일 {f20})"}

    frn_item = _flow_item(frn_amt, "외국인")
    inst_item = _flow_item(inst_amt, "기관")

    # 수급 헤드라인
    if rsi_now is not None and rsi_now <= 30:
        supply_headline, supply_state = "과매도 구간이다" + (" (반등 전환 시작)" if rsi_turn_up else ""), ("up" if rsi_turn_up else "down")
    elif rsi_now is not None and rsi_now >= 70:
        supply_headline, supply_state = "과매수(과열) 구간이다", "down"
    elif frn_item["state"] == "up" and inst_item["state"] == "up":
        supply_headline, supply_state = "외국인·기관이 함께 사고 있다", "up"
    elif frn_item["state"] == "down" and inst_item["state"] == "down":
        supply_headline, supply_state = "외국인·기관이 함께 팔고 있다", "down"
    else:
        supply_headline, supply_state = "수급 중립 (뚜렷한 쏠림 없음)", "flat"

    supply_items = [rsi_item, vwap_item, frn_item, inst_item]

    # ══ ③ 차트 섹션 ══════════════════════════════════════
    resistance_20d = max(closes[max(0, i-20):i]) if i >= 5 else None
    support_20d    = min(closes[max(0, i-20):i]) if i >= 5 else None
    breakout  = bool(resistance_20d and curr > resistance_20d)
    breakdown = bool(support_20d and curr < support_20d)
    candle_bull = _bt._chart_bullish_candle(opens, highs, lows, closes, i)
    candle_bear = _bt._chart_bearish_candle(opens, highs, lows, closes, i)

    p252 = closes[max(0, i-251):i+1]
    hi52, lo52 = max(p252), min(p252)
    pos_52w = (curr - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50.0
    from_high_pct = (curr / hi52 - 1) * 100 if hi52 else 0.0
    from_low_pct  = (curr / lo52 - 1) * 100 if lo52 else 0.0

    if breakdown:
        chart_headline, chart_state = f"20거래일 저점 지지선({round(support_20d):,}원)을 이탈했다 — 추가 하락 위험", "down"
    elif breakout:
        chart_headline, chart_state = f"20거래일 고점 저항선({round(resistance_20d):,}원)을 돌파했다 — 상승 여력 열림", "up"
    elif st20 and st20["state"] == "breakdown":
        chart_headline, chart_state = "20일선을 이탈했다", "down"
    elif st20 and st20["state"] == "breakout":
        chart_headline, chart_state = "20일선을 회복했다", "up"
    elif st20 and st20["state"] == "above":
        chart_headline, chart_state = "20일선 위에서 지지받는 중이다", "up"
    else:
        chart_headline, chart_state = "이평선 아래에 머물러 있다", "down"

    chart_items = [x for x in [
        _ma_statement(20, st20),
        _ma_statement(60, st60),
        _ma_statement(120, st120),
        {"state": "down" if breakdown else "up", "label": "20거래일 저점 지지선 (MA20 아님)",
         "statement": (f"⚠ 최근 20거래일 저점 {round(support_20d):,}원을 하향 이탈했다" if breakdown else
                       f"최근 20거래일 저점 {round(support_20d):,}원 위에서 버티는 중 (현재가 +{(curr-support_20d)/support_20d*100:.1f}%)")
         } if support_20d else None,
        {"state": "up" if breakout else "flat", "label": "20거래일 고점 저항선 (MA20 아님)",
         "statement": (f"⚡ 최근 20거래일 고점 {round(resistance_20d):,}원을 상향 돌파했다" if breakout else
                       f"최근 20거래일 고점 {round(resistance_20d):,}원 아래 ({(curr-resistance_20d)/resistance_20d*100:.1f}%) — 돌파 시 추세 가속 가능")
         } if resistance_20d else None,
        {"state": "up" if candle_bull else ("down" if candle_bear else "flat"), "label": "캔들 패턴 (최근 3일)",
         "statement": ("망치형/상승장악형 출현 — 바닥권 매수세 유입 신호다" if candle_bull else
                       "유성형/하락장악형 출현 — 고점권 매도세 출현 신호다" if candle_bear else "특이 반전 패턴 없음")},
        {"state": "down" if pos_52w >= 80 else ("up" if pos_52w <= 25 else "flat"), "label": "52주 위치",
         "statement": f"52주 고점 대비 {from_high_pct:.1f}% · 저점 대비 +{from_low_pct:.1f}% (구간 내 {pos_52w:.0f}% 지점)"},
    ] if x]

    # ══ ④ 종합 점수 (각 지표 이해 후 마지막에) ══════════════
    bottom_core3 = sum([short_up, wk_higher_low, candle_bull])
    top_core3    = sum([not short_up and bool(ma5 and ma10), wk_lower_high, candle_bear])
    if bottom_core3 >= 2 and bottom_core3 > top_core3:
        verdict, verdict_color = "🟢 바닥/반등 신호 우세", "green"
    elif top_core3 >= 2 and top_core3 > bottom_core3:
        verdict, verdict_color = "🔴 고점/하락 신호 우세", "red"
    else:
        verdict, verdict_color = "🟡 중립 (신호 혼재/부족)", "yellow"

    return {
        "ok": True, "base_date": dates[i], "close": curr,
        "metrics": {
            "ma20": ma20,
            "ma60": ma60,
            "ma120": ma120,
            "support_20d": support_20d,
            "resistance_20d": resistance_20d,
        },
        "sections": [
            {"key": "trend", "title": "📈 추세", "headline": trend_headline, "state": trend_state, "items": trend_items},
            {"key": "supply", "title": "💰 수급/체력", "headline": supply_headline, "state": supply_state, "items": supply_items},
            {"key": "chart", "title": "📊 차트 (지지/저항)", "headline": chart_headline, "state": chart_state, "items": chart_items},
        ],
        "score": {
            "bottom": bottom_core3, "top": top_core3, "max": 3,
            "verdict": verdict, "verdict_color": verdict_color,
            "note": "종합 판정은 백테스트로 검증된 3요소(단기추세·주봉구조·캔들패턴) 2/3 합의 기준. "
                    "위 각 섹션의 개별 신호를 먼저 이해한 뒤 참고용으로 활용하세요.",
        },
    }
