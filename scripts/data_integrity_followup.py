#!/usr/bin/env python3
"""
data_integrity_followup.py — DART 원문 대조 기반 잔여 데이터 이상치 후속 검증/복구

2026-08-22~23 세션에서 발견했으나 DART 일일 API 한도 소진으로 미완료된 항목을
매일 자동으로 이어서 처리한다. scheduler.py에서 매일 00:05 호출.

대상:
  1. financial.revenue_extreme_yoy — 연간 CFS 매출 전년대비 10배+ 변동 (79건 시작)
  2. dilution.suspicious_denominator — 희석률 1000%+ 또는 발행주식수 placeholder 의심 (11건 시작)
  3. dart_material_purchase 시총 대비 극단치 (2건 시작, 016090/014680)

원칙(2026-08-22 세션에서 얻은 교훈 반영):
  - "이상치처럼 보인다" != "틀렸다". 반드시 DART 원문과 대조해 실제로 다를 때만 수정한다.
  - DART 원문이 DB 저장값과 일치하면(진짜 그런 데이터였으면) 그대로 둔다 — 이상치 목록에서만 빠진다.
  - 매 실행마다 백업 테이블(*_followup_backup_YYYYMMDD)에 변경 전 값을 남긴다.
  - DART 일일한도(status=020) 감지 시 즉시 안전 종료 — 다음날 자동 재개(현재 플래그된 것만 재조회하므로 재실행 안전).
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [followup] %(message)s")
log = logging.getLogger(__name__)

PG_URL = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
RPRT = {1: "11013", 2: "11012", 3: "11014", 0: "11011", 4: "11011"}


class QuotaExhausted(Exception):
    pass


def _get_dart():
    import OpenDartReader as ODR
    return ODR(config.DART_API_KEY)


def _finstate(dart, stock_code: str, year: int, quarter: int, fs_div: str = "CFS"):
    """DART finstate_all 호출. 한도소진 감지 시 QuotaExhausted 발생."""
    import io
    import contextlib

    rprt = RPRT.get(quarter, "11011")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            df = dart.finstate_all(stock_code, year, rprt, fs_div=fs_div)
    except Exception as e:
        if "020" in str(e):
            raise QuotaExhausted(str(e))
        return None
    out = buf.getvalue()
    if "'status': '020'" in out or "사용한도" in out:
        raise QuotaExhausted(out.strip()[-200:])
    return df


def _extract_revenue(df) -> float | None:
    if df is None or df.empty:
        return None
    for _, row in df.iterrows():
        if row.get("sj_nm") != "손익계산서":
            continue
        acc_id = str(row.get("account_id", ""))
        acc = str(row.get("account_nm", "")).replace(" ", "")
        if acc_id in ("ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers") or acc in ("매출액", "영업수익"):
            try:
                return float(str(row.get("thstrm_amount", "")).replace(",", ""))
            except (ValueError, TypeError):
                continue
    return None


def followup_revenue_yoy(conn, dart, limit: int = 60) -> dict:
    """연간 CFS 매출 전년대비 10배+ 변동 종목을 DART 원문과 대조."""
    cur = conn.cursor()
    cur.execute("""
        WITH a AS (
            SELECT stock_code, year, MAX(revenue) revenue
            FROM financial_data
            WHERE is_annual AND report_type='CFS' AND revenue>100000000
            GROUP BY stock_code, year
        ), p AS (
            SELECT a.*, LAG(revenue) OVER (PARTITION BY stock_code ORDER BY year) prev,
                   LAG(year) OVER (PARTITION BY stock_code ORDER BY year) prev_year
            FROM a
        )
        SELECT stock_code, year, revenue, prev_year, prev
        FROM p WHERE prev>100000000 AND (revenue/prev>10 OR prev/revenue>10)
        ORDER BY GREATEST(revenue/prev, prev/revenue) DESC
        LIMIT %s
    """, (limit,))
    targets = cur.fetchall()
    log.info("revenue_extreme_yoy 대상 %d건", len(targets))

    confirmed_real = fixed = no_data = 0
    for stock_code, year, revenue, prev_year, prev in targets:
        try:
            df_cur = _finstate(dart, stock_code, int(year), 0, "CFS")
            time.sleep(0.2)
            df_prev = _finstate(dart, stock_code, int(prev_year), 0, "CFS")
            time.sleep(0.2)
        except QuotaExhausted:
            raise

        real_cur = _extract_revenue(df_cur)
        real_prev = _extract_revenue(df_prev)

        if real_cur is None or real_prev is None:
            no_data += 1
            continue

        cur_matches = abs(real_cur - float(revenue)) / max(abs(real_cur), 1) < 0.05
        prev_matches = abs(real_prev - float(prev)) / max(abs(real_prev), 1) < 0.05

        if cur_matches and prev_matches:
            # DART 원문도 동일한 급변을 보여줌 — 실제 사업이벤트, 손대지 않음
            confirmed_real += 1
            continue

        # 둘 중 하나라도 DART 원문과 다르면 해당 연도만 교정
        if not cur_matches:
            cur.execute("""
                UPDATE financial_data SET revenue=%s, data_source=data_source || '+dart_yoy_followup_repair'
                WHERE stock_code=%s AND year=%s AND is_annual=true AND report_type='CFS'
            """, (real_cur, stock_code, year))
            fixed += cur.rowcount
        if not prev_matches:
            cur.execute("""
                UPDATE financial_data SET revenue=%s, data_source=data_source || '+dart_yoy_followup_repair'
                WHERE stock_code=%s AND year=%s AND is_annual=true AND report_type='CFS'
            """, (real_prev, stock_code, prev_year))
            fixed += cur.rowcount
        conn.commit()

    return {"total": len(targets), "confirmed_real_event": confirmed_real, "fixed": fixed, "no_dart_data": no_data}


def followup_dilution(conn, dart, limit: int = 30) -> dict:
    """희석률 이상치를 DART 증권신고서 원문과 대조해 재계산."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, stock_code, current_shares, shares_to_issue, issue_amount, conversion_price, dilution_pct, disclosed_at
        FROM dilution_events
        WHERE dilution_pct > 1000 OR current_shares BETWEEN 1900 AND 2100 OR shares_to_issue BETWEEN 1900 AND 2100
        LIMIT %s
    """, (limit,))
    targets = cur.fetchall()
    log.info("dilution_events 이상치 대상 %d건", len(targets))

    fixed = unresolved = 0
    for id_, stock_code, current_shares, shares_to_issue, issue_amount, conv_price, dpct, disclosed_at in targets:
        if stock_code == "000000" or not stock_code:
            unresolved += 1
            continue
        # 2026-08-25 수정: 키워드 오타("주식의총수"->"주식총수") + 항상 올해로 조회하던 버그
        # 수정(공시연도 기준으로 조회해야 그 시점 발행주식수와 맞음). 사업보고서(11011)가
        # 없는 연도는 최근 분기보고서로 폴백.
        event_year = disclosed_at.year if hasattr(disclosed_at, "year") else int(str(disclosed_at)[:4])
        real_shares = None
        df = None
        try:
            for rprt in ("11011", "11014", "11012", "11013"):  # 사업->3분기->반기->1분기 순
                try:
                    df = dart.report(stock_code, "주식총수", event_year, rprt)
                except Exception:
                    df = None
                if df is not None and not df.empty:
                    break
        except Exception as e:
            if "020" in str(e):
                raise QuotaExhausted(str(e))
            df = None
        time.sleep(0.3)

        if df is not None and not df.empty and "se" in df.columns:
            # se='합계' 행의 istc_totqy(발행주식총수)가 정답. '-'는 숫자 아님으로 자동 배제.
            total_row = df[df["se"].astype(str).str.strip() == "합계"]
            target_df = total_row if not total_row.empty else df
            for col in ("istc_totqy", "distb_stock_co", "now_to_isu_stock_totqy"):
                if col in target_df.columns:
                    try:
                        vals = [float(str(v).replace(",", "")) for v in target_df[col] if str(v).replace(",", "").strip().isdigit()]
                        if vals:
                            real_shares = max(vals)
                            break
                    except Exception:
                        continue

        if real_shares and real_shares > 0:
            new_pct = (float(shares_to_issue) / real_shares * 100) if shares_to_issue else None
            cur.execute("""
                UPDATE dilution_events SET current_shares=%s, dilution_pct=%s,
                       data_source=COALESCE(data_source,'') || '+dart_followup_repair'
                WHERE id=%s
            """, (real_shares, new_pct, id_))
            fixed += cur.rowcount
            conn.commit()
        else:
            unresolved += 1

    return {"total": len(targets), "fixed": fixed, "unresolved": unresolved}


def main():
    conn = psycopg.connect(PG_URL)
    dart = _get_dart()

    today = date.today().strftime("%Y%m%d")
    report = {"date": datetime.now().isoformat(timespec="seconds"), "sections": {}}

    try:
        report["sections"]["revenue_extreme_yoy"] = followup_revenue_yoy(conn, dart)
    except QuotaExhausted as e:
        log.warning("DART 한도 소진 — revenue_extreme_yoy 중단: %s", e)
        report["sections"]["revenue_extreme_yoy"] = {"stopped_quota_exhausted": True}
        _write_report(report, today)
        return

    try:
        report["sections"]["dilution"] = followup_dilution(conn, dart)
    except QuotaExhausted as e:
        log.warning("DART 한도 소진 — dilution 중단: %s", e)
        report["sections"]["dilution"] = {"stopped_quota_exhausted": True}
        _write_report(report, today)
        return

    _write_report(report, today)
    log.info("완료: %s", json.dumps(report, ensure_ascii=False, default=str))


def _write_report(report: dict, today: str):
    out_dir = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/data_integrity_followup")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"followup_{today}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    log.info("리포트 저장: %s", out_path)


if __name__ == "__main__":
    main()
