"""
collectors/dart_insider_collector.py — 대량보유 + 임원지분변동 DART 수집기

OpenDART API:
  - major_shareholders(stock_code)       대량보유상황보고서 (5%+ 신고)
  - major_shareholders_exec(stock_code)  임원·주요주주 특정증권등 소유상황보고서

저장:
  - dart_major_holders     (5%+ 보고)
  - dart_insider_holdings  (임원·주요주주 변동)

호출량 전략:
  매일 _job_disclosure_check (03:30) 에서 호출되어 오늘 신규 공시 종목만 갱신.
  list API로 어제~오늘 'D'(지분공시) 종목을 추려서 그 종목만 호출.
  일 호출 추정: 100~300건 (DART 한도 36,000건 / 1% 미만)

CEO 식별: isu_exctv_ofcps에 '대표이사' 포함 여부.
주요 변동: 변동 주식수 × 종가가 1억원 이상이거나 비율 변동 ≥ 0.1%.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import config
from api_rate_limiter import api_limiter

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"
SIGNIFICANT_AMOUNT_KRW = 100_000_000   # 1억원 이상 변동
SIGNIFICANT_RATE_PCT   = 0.1           # 비율 0.1%p 이상 변동


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def _to_int(v) -> int | None:
    if v in (None, "", "-"):
        return None
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except Exception:
        return None


def _to_float(v) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except Exception:
        return None


def _norm_date(s: str) -> str:
    s = (s or "").strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s or ""


def _today_close(cur, code: str) -> float | None:
    """가장 최근 종가 — change_amount 추정용."""
    row = cur.execute(
        "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
        (code,),
    ).fetchone()
    return float(row[0]) if row else None


def _list_disclosed_codes(dart, since: str, until: str) -> set[str]:
    """
    어제~오늘 'D' (지분공시) 신규 공시 종목코드 집합.
    호출량: list API 1~2건 (페이지 분할 시).
    """
    codes: set[str] = set()
    if not api_limiter.wait("DART"):
        logger.warning("[DART지분] 쿼터 소진 — list 스킵")
        return codes
    try:
        df = dart.list(since, until, kind="D")
        if df is None or df.empty:
            return codes
        if "stock_code" in df.columns:
            for c in df["stock_code"].dropna().unique():
                cs = str(c).strip().zfill(6)
                if len(cs) == 6 and cs.isdigit():
                    codes.add(cs)
    except Exception as e:
        logger.warning(f"[DART지분] list 오류: {e}")
    return codes


def _save_major_holders(cur, df, code: str, corp_name: str) -> int:
    """대량보유 보고 저장. df는 DataFrame 또는 dict list."""
    if df is None:
        return 0
    rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    saved = 0
    for r in rows:
        rcept_no = str(r.get("rcept_no") or "").strip()
        if not rcept_no:
            continue
        rcept_dt = _norm_date(str(r.get("rcept_dt") or ""))
        repror   = str(r.get("repror") or "").strip()
        stkqy    = _to_int(r.get("stkqy"))
        stkrt    = _to_float(r.get("stkrt"))
        ctr_stkqy = _to_int(r.get("ctr_stkqy"))
        ctr_stkrt = _to_float(r.get("ctr_stkrt"))
        report_tp = str(r.get("report_tp") or "").strip()

        stk_diff = (stkqy - ctr_stkqy) if (stkqy is not None and ctr_stkqy is not None) else None
        rt_diff  = (stkrt - ctr_stkrt) if (stkrt is not None and ctr_stkrt is not None) else None

        try:
            cur.execute("""
                INSERT OR IGNORE INTO dart_major_holders
                  (rcept_no, rcept_dt, stock_code, corp_code, corp_name,
                   repror, stkqy, stkrt, ctr_stkqy, ctr_stkrt, report_tp,
                   stk_diff, rt_diff, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rcept_no, rcept_dt, code,
                  str(r.get("corp_code") or ""), corp_name,
                  repror, stkqy, stkrt, ctr_stkqy, ctr_stkrt, report_tp,
                  stk_diff, rt_diff, json.dumps(r, ensure_ascii=False, default=str)))
            if cur.rowcount > 0:
                saved += 1
        except Exception as e:
            logger.debug(f"[DART지분] major insert {code} {rcept_no}: {e}")
    return saved


def _save_insider_holdings(cur, df, code: str, corp_name: str) -> int:
    """임원·주요주주 보고 저장. CEO 여부 + 주요 변동 플래그 자동 판정."""
    if df is None:
        return 0
    rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    if not rows:
        return 0

    close_price = _today_close(cur, code)
    saved = 0
    for r in rows:
        rcept_no = str(r.get("rcept_no") or "").strip()
        if not rcept_no:
            continue
        rcept_dt = _norm_date(str(r.get("rcept_dt") or ""))
        repror   = str(r.get("repror") or "").strip()
        rgist    = str(r.get("isu_exctv_rgist_at") or "").strip()
        ofcps    = str(r.get("isu_exctv_ofcps") or "").strip()
        shrhldr  = str(r.get("isu_main_shrholdr") or "").strip()
        cnt      = _to_int(r.get("sp_stock_lmp_cnt"))
        irds_cnt = _to_int(r.get("sp_stock_lmp_irds_cnt"))
        irds_rt  = _to_float(r.get("sp_stock_lmp_irds_rate"))

        # CEO 여부
        is_ceo = 1 if "대표이사" in ofcps else 0

        # 변동 금액(원)
        change_amt = None
        if irds_cnt is not None and close_price:
            change_amt = float(irds_cnt) * close_price

        # 주요 변동 판정
        is_significant = 0
        if change_amt is not None and abs(change_amt) >= SIGNIFICANT_AMOUNT_KRW:
            is_significant = 1
        if irds_rt is not None and abs(irds_rt) >= SIGNIFICANT_RATE_PCT:
            is_significant = 1

        try:
            cur.execute("""
                INSERT OR IGNORE INTO dart_insider_holdings
                  (rcept_no, rcept_dt, stock_code, corp_code, corp_name,
                   repror, isu_exctv_rgist, isu_exctv_ofcps, isu_main_shrholdr,
                   sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate,
                   is_ceo, is_significant, change_amount, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rcept_no, rcept_dt, code,
                  str(r.get("corp_code") or ""), corp_name,
                  repror, rgist, ofcps, shrhldr,
                  cnt, irds_cnt, irds_rt,
                  is_ceo, is_significant, change_amt,
                  json.dumps(r, ensure_ascii=False, default=str)))
            if cur.rowcount > 0:
                saved += 1
        except Exception as e:
            logger.debug(f"[DART지분] insider insert {code} {rcept_no}: {e}")
    return saved


def collect_for_codes(codes: list[str], dart=None) -> dict:
    """
    주어진 종목코드들에 대해 대량보유 + 임원지분 보고 수집.

    Args:
        codes: 종목코드 리스트
        dart : OpenDartReader 인스턴스 (None이면 새로 생성)

    Returns:
        {"major": M, "insider": I, "skipped": S, "errors": E}
    """
    if not codes:
        return {"major": 0, "insider": 0, "skipped": 0, "errors": 0}

    if dart is None:
        import OpenDartReader
        dart = OpenDartReader(config.DART_API_KEY)

    conn = _conn()
    cur = conn.cursor()

    major_total = insider_total = errors = skipped = 0

    for code in codes:
        # 종목명 조회 (저장용)
        nm_row = cur.execute(
            "SELECT stock_name FROM stock_universe WHERE stock_code=?", (code,)
        ).fetchone()
        corp_name = nm_row[0] if nm_row else ""

        # 1) 대량보유 (5%+)
        if not api_limiter.wait("DART"):
            logger.warning("[DART지분] 쿼터 소진 — 중단")
            break
        try:
            df_major = dart.major_shareholders(code)
            major_total += _save_major_holders(cur, df_major, code, corp_name)
        except Exception as e:
            errors += 1
            logger.debug(f"[DART지분] major {code}: {e}")

        # 2) 임원·주요주주
        if not api_limiter.wait("DART"):
            logger.warning("[DART지분] 쿼터 소진 — 중단")
            break
        try:
            df_exec = dart.major_shareholders_exec(code)
            insider_total += _save_insider_holdings(cur, df_exec, code, corp_name)
        except Exception as e:
            errors += 1
            logger.debug(f"[DART지분] insider {code}: {e}")

        if (major_total + insider_total) and (major_total + insider_total) % 50 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    logger.info(
        f"[DART지분] 처리 {len(codes)}종목 / 신규 major {major_total} / 신규 insider {insider_total} / 오류 {errors}"
    )
    return {
        "major": major_total, "insider": insider_total,
        "skipped": skipped, "errors": errors,
    }


def collect_recent_disclosures(days: int = 1) -> dict:
    """
    최근 N일 (D) 지분공시 종목들의 대량보유 + 임원지분 변동을 수집.

    스케줄러 _job_disclosure_check에서 매일 호출.
    """
    import OpenDartReader
    dart = OpenDartReader(config.DART_API_KEY)

    until = date.today().strftime("%Y%m%d")
    since = (date.today() - timedelta(days=days)).strftime("%Y%m%d")

    codes = _list_disclosed_codes(dart, since, until)
    logger.info(f"[DART지분] {since}~{until} 지분공시 {len(codes)}종목 발견")

    if not codes:
        return {"major": 0, "insider": 0, "skipped": 0, "errors": 0}

    return collect_for_codes(sorted(codes), dart=dart)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    res = collect_recent_disclosures(days=2)
    print(res)
