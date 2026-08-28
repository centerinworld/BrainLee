"""
collectors/krx_isu_base_info.py — KRX 종목기본정보 + 일별 스냅샷/변동 추적

호출하는 KRX OpenAPI:
  - sto/stk_isu_base_info  (KOSPI 종목기본정보)
  - sto/ksq_isu_base_info  (KOSDAQ 종목기본정보)

저장:
  - stock_universe (isin_code, eng_name, secugrp_nm, kind_stkcert_nm 등 보강)
  - stock_base_info_history (일별 스냅샷)
  - stock_base_info_changes (전일 대비 변경분)

호출량: 일 2회 (KRX 한도 500건 중 0.4%)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import requests

import config
from api_rate_limiter import api_limiter

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"


def _fetch(path: str, bas_dd: str) -> list[dict]:
    """KRX API 한 번 호출 (rate limit + 차단 감지 적용)."""
    if not api_limiter.wait("KRX"):
        logger.warning(f"[KRX기본정보] {path} 쿼터 소진 — 스킵")
        return []
    headers = {"AUTH_KEY": config.KRX_API_KEY}
    try:
        r = requests.get(
            f"{BASE_URL}/{path}",
            params={"basDd": bas_dd},
            headers=headers,
            timeout=20,
        )
        if r.status_code == 429:
            api_limiter.report_block("KRX", cooldown=3600)
            return []
        if r.status_code == 200:
            return r.json().get("OutBlock_1", [])
        logger.warning(f"[KRX기본정보] {path} HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"[KRX기본정보] {path} 오류: {e}")
    return []


def _norm_date(yyyymmdd: str) -> str:
    """'YYYYMMDD' → 'YYYY-MM-DD' (빈값/이상값은 그대로)."""
    s = (yyyymmdd or "").strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return yyyymmdd or ""


def _to_int(v) -> int | None:
    """KRX는 숫자도 ',' 포함 문자열로 옴."""
    if v in (None, "", "-"):
        return None
    try:
        return int(str(v).replace(",", ""))
    except Exception:
        return None


def _diff_and_record(
    cur: sqlite3.Cursor,
    code: str,
    today: str,
    new_row: dict,
) -> int:
    """
    오늘 데이터를 어제 스냅샷과 비교해서 변경된 항목을 stock_base_info_changes에 기록.

    추적 대상:
      - shares_issued (무상증자/감자 등)
      - sector_type (관리종목 지정/해제 등)
      - secugrp_nm (증권 구분 변경)
      - stock_name (종목명 변경)
      - market_cap : 추적 안 함 (매일 변하므로 의미 없음)

    Returns: 기록한 변경 건수
    """
    yesterday = (date.fromisoformat(today) - timedelta(days=14)).isoformat()
    prev = cur.execute("""
        SELECT shares_issued, sector_type, secugrp_nm, stock_name
        FROM stock_base_info_history
        WHERE stock_code = ?
          AND snapshot_date < ?
          AND snapshot_date >= ?
        ORDER BY snapshot_date DESC
        LIMIT 1
    """, (code, today, yesterday)).fetchone()
    if not prev:
        return 0

    fields = [
        ("shares_issued", "상장주식수", lambda v: f"{v:,}주" if v else "-"),
        ("sector_type",   "소속부",     lambda v: v or "-"),
        ("secugrp_nm",    "증권구분",   lambda v: v or "-"),
        ("stock_name",    "종목명",     lambda v: v or "-"),
    ]
    cnt = 0
    for i, (key, label, fmt) in enumerate(fields):
        old_v, new_v = prev[i], new_row.get(key)
        if old_v != new_v and (old_v or new_v):
            desc = f"{label} 변경: {fmt(old_v)} → {fmt(new_v)}"
            cur.execute("""
                INSERT INTO stock_base_info_changes
                    (stock_code, change_date, change_type, old_value, new_value, description)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (code, today, key, str(old_v) if old_v is not None else None,
                  str(new_v) if new_v is not None else None, desc))
            cnt += 1
    return cnt


def collect_base_info(today_str: str | None = None) -> dict:
    """
    KOSPI/KOSDAQ 종목기본정보를 KRX API에서 가져와 저장 + 변동 추적.

    Returns:
        {"updated": N, "history": M, "changes": K, "skipped": S}
    """
    today_str = today_str or date.today().isoformat()
    bas_dd = today_str.replace("-", "")

    rows_kospi  = _fetch("sto/stk_isu_base_info", bas_dd)
    rows_kosdaq = _fetch("sto/ksq_isu_base_info", bas_dd)
    rows = rows_kospi + rows_kosdaq

    if not rows:
        logger.warning("[KRX기본정보] 응답 없음 — KRX 휴장/차단 가능")
        return {"updated": 0, "history": 0, "changes": 0, "skipped": 0}

    conn = sqlite3.connect(DB_PATH, timeout=60)
    cur = conn.cursor()
    updated = history = changes = skipped = 0

    for r in rows:
        code = str(r.get("ISU_SRT_CD") or r.get("ISU_CD") or "").strip()[-6:]
        if not code or len(code) != 6 or not code.isdigit():
            skipped += 1
            continue

        isin       = str(r.get("ISU_CD") or "").strip()
        full_name  = str(r.get("ISU_NM") or "").strip()
        abbrv      = str(r.get("ISU_ABBRV") or "").strip()
        eng_name   = str(r.get("ISU_ENG_NM") or "").strip()
        list_dd    = _norm_date(str(r.get("LIST_DD") or "").strip())
        market     = str(r.get("MKT_TP_NM") or "").strip()
        secugrp    = str(r.get("SECUGRP_NM") or "").strip()
        sector_tp  = str(r.get("SECT_TP_NM") or "").strip()
        kind_stk   = str(r.get("KIND_STKCERT_TP_NM") or "").strip()
        parval     = _to_int(r.get("PARVAL"))
        list_shrs  = _to_int(r.get("LIST_SHRS"))

        # 1) stock_universe 보강 (이미 존재하는 종목만)
        cur.execute("""
            UPDATE stock_universe SET
                isin_code            = ?,
                eng_name             = ?,
                secugrp_nm           = ?,
                kind_stkcert_nm      = ?,
                isu_full_name        = ?,
                stock_name           = COALESCE(NULLIF(?, ''), stock_name),
                market               = COALESCE(NULLIF(?, ''), market),
                listed_date          = COALESCE(NULLIF(?, ''), listed_date),
                face_value           = COALESCE(?, face_value),
                shares_issued        = COALESCE(?, shares_issued),
                sector_type          = COALESCE(NULLIF(?, ''), sector_type),
                base_info_updated_at = CURRENT_TIMESTAMP
            WHERE stock_code = ?
        """, (isin, eng_name, secugrp, kind_stk, full_name,
              abbrv, market, list_dd, parval, list_shrs, sector_tp, code))
        if cur.rowcount > 0:
            updated += 1

        # 2) 일별 스냅샷 — 변동 비교용. market_cap은 price_history에서 매일 갱신되므로
        #    여기서는 LIST_SHRS 기준만 저장 (시가 × 상장주식수로 계산 가능).
        cur.execute("""
            INSERT OR REPLACE INTO stock_base_info_history
                (stock_code, snapshot_date, shares_issued, sector_type, secugrp_nm, stock_name)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (code, today_str, list_shrs, sector_tp, secugrp, abbrv or full_name))
        history += 1

        # 3) 변동 추적
        changes += _diff_and_record(cur, code, today_str, {
            "shares_issued": list_shrs,
            "sector_type":   sector_tp,
            "secugrp_nm":    secugrp,
            "stock_name":    abbrv or full_name,
        })

    conn.commit()
    conn.close()
    logger.info(
        f"[KRX기본정보] {today_str} 갱신 {updated} / 스냅 {history} / 변경 {changes} / 스킵 {skipped}"
    )
    return {
        "updated": updated, "history": history,
        "changes": changes, "skipped": skipped,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    res = collect_base_info()
    print(res)
