#!/usr/bin/env python3
"""무상증자 권리락 계수를 OpenDART 구조화 API(fricDecsn, 무상증자 결정)로 확정한다 — 2026-09-28.

배경: 무상증자 이벤트 다수가 이사회 결의일(공시일)로만 기록되고 배정비율이 비어
review_required로 남아, 실제 가격 단절(권리락일)을 보유한 백테스트가 가격 감사에서
막혔다. 텍스트 파싱 대신 DART 구조화 응답의 1주당 배정주식수·신주배정기준일을 쓴다.

확정 규칙(모두 만족해야 함):
  1. 권리락일 = 신주배정기준일의 직전 한국 거래일(T+2 결제, 수집기와 같은 규칙)
  2. 계수 = 1 / (1 + 1주당 신주배정 보통주 수)
  3. price_history의 권리락일 종가/직전 거래일 종가(r)를 계수로 나눈 잔여 변동이
     가격제한폭(0.70~1.30) 안이고, |log r - log 계수| < |log r| (무보정보다 계수 쪽에 가까움)
     — 이미 수정주가로 이어진 구간이면 계수를 넣지 않는다(이중 보정 방지)
  4. 같은 종목의 다른 날짜(권리락일 초과 60일 이내)에 같은 계수(±2%)로 확정된 행은
     'superseded_by_ex_date'로 내린다 — 로더가 60일 내 같은 계수 중 늦은 건만 남기므로
     안 내리면 상장일 기준 행이 권리락일 행을 대신 살아남는다.
변경 전 행은 corporate_action_events_backup_bonus_dart_api_<run>에 백업, data_fix_log에 1행 기록.

    PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/confirm_bonus_issue_factors_dart_api.py [--apply] [--codes 001,002]
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.dart_equity_issue_collector import _prev_kr_trading_day  # noqa: E402
from dart_key_manager import get_dart_api_keys, is_quota_error  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

DART = "https://opendart.fss.or.kr/api"
OUT = ROOT / "research_outputs" / "bonus_issue_dart_api_confirmation_latest.json"
CACHE = ROOT / "data_cache" / "dart_corp_codes.json"


def _corp_codes(keys: list[str]) -> dict:
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < 7 * 86400:
        return json.loads(CACHE.read_text())
    resp = requests.get(f"{DART}/corpCode.xml", params={"crtfc_key": keys[0]}, timeout=60)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))
    out = {}
    for item in root:
        sc = (item.findtext("stock_code") or "").strip()
        if sc:
            out[sc] = (item.findtext("corp_code") or "").strip()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out))
    return out


def _num(v) -> float | None:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "-") else None
    except ValueError:
        return None


def _kdate(v) -> str | None:
    digits = "".join(ch for ch in str(v or "") if ch.isdigit())
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None


def _fetch_decisions(keys: list[str], corp_code: str, bgn: str, end: str) -> list[dict]:
    for key in keys:
        r = requests.get(f"{DART}/fricDecsn.json", params={
            "crtfc_key": key, "corp_code": corp_code,
            "bgn_de": bgn.replace("-", ""), "end_de": end.replace("-", "")}, timeout=30)
        data = r.json()
        if is_quota_error(data):
            continue
        if data.get("status") == "013":  # 조회 결과 없음
            return []
        if data.get("status") != "000":
            raise RuntimeError(f"DART fricDecsn {corp_code}: {data.get('status')} {data.get('message')}")
        return data.get("list") or []
    raise RuntimeError("all DART keys exhausted")


def _close_ratio(conn, code: str, ex_date: str) -> tuple[float | None, str | None]:
    rows = conn.execute(
        """SELECT substr(date,1,10), close FROM price_history
           WHERE stock_code=? AND substr(date,1,10)<=? AND close>0 ORDER BY date DESC LIMIT 2""",
        (code, ex_date),
    ).fetchall()
    if len(rows) < 2 or rows[0][0] != ex_date:
        return None, None
    return float(rows[0][1]) / float(rows[1][1]), rows[1][0]


def candidates(conn, only: set[str]) -> list[tuple[str, str, str]]:
    """(종목, 조회 시작일, 조회 종료일): 확인대기 무상증자 + 무상증자 공시 인접 가격단절."""
    out = set()
    for code, edate in conn.execute(
        """SELECT stock_code, event_date FROM corporate_action_events
           WHERE event_type='bonus_issue' AND adjustment_status='review_required'"""
    ).fetchall():
        d = date.fromisoformat(str(edate)[:10])
        out.add((code, (d - timedelta(days=10)).isoformat(), (d + timedelta(days=10)).isoformat()))
    for code, edate in conn.execute(
        """SELECT stock_code, event_date FROM price_jump_audit
           WHERE return_usable=0 AND (matched_report_name LIKE '%무상증자%'
                 OR (classification='raw_source_confirmed_jump_review' AND price_ratio < 0.7))"""
    ).fetchall():
        d = date.fromisoformat(str(edate)[:10])
        out.add((code, (d - timedelta(days=90)).isoformat(), d.isoformat()))
    return sorted(c for c in out if not only or c[0] in only)


def run(apply: bool, only: set[str]) -> dict:
    keys = get_dart_api_keys()
    corp = _corp_codes(keys)
    conn = connect_primary_db(timeout=120)
    run_id = f"bonus_dart_api_{datetime.now():%Y%m%d_%H%M%S}"
    seen_rcept = set()
    confirmed, rejected, errors = [], [], []
    for code, bgn, end in candidates(conn, only):
        cc = corp.get(code)
        if not cc:
            errors.append([code, "no_corp_code"])
            continue
        try:
            decisions = _fetch_decisions(keys, cc, bgn, end)
        except Exception as exc:  # noqa: BLE001
            errors.append([code, str(exc)[:200]])
            continue
        for dec in decisions:
            rcept = dec.get("rcept_no")
            if not rcept or rcept in seen_rcept:
                continue
            seen_rcept.add(rcept)
            per_share = _num(dec.get("nstk_ascnt_ps_ostk"))
            record_date = _kdate(dec.get("nstk_asstd"))
            before = _num(dec.get("bfic_tisstk_ostk"))
            if not per_share or per_share <= 0 or not record_date:
                rejected.append([code, rcept, "missing_ratio_or_record_date"])
                continue
            # 배정기준일이 휴장일(연말 폐장 12/31 등)이면 실효 기준일은 그 직전 거래일이고,
            # 권리락일은 다시 그 직전 거래일이다(181710: 기준일 12/31 → 권리락 12/29).
            effective_record = _prev_kr_trading_day(
                (date.fromisoformat(record_date) + timedelta(days=1)).isoformat())
            ex_date = _prev_kr_trading_day(effective_record)
            factor = 1.0 / (1.0 + per_share)
            ratio, prev_day = _close_ratio(conn, code, ex_date)
            if ratio is None:
                rejected.append([code, rcept, f"no_price_on_ex_date:{ex_date}"])
                continue
            if factor > 0.95:
                # 5% 미만 무상증자는 일상 변동과 구분이 안 된다 — 자동 확정하지 않는다
                # (build_corporate_action_adjustment_engine의 minor_change 규칙과 동일).
                rejected.append([code, rcept, f"minor_bonus factor {factor:.4f} > 0.95 (manual review)"])
                continue
            residual = ratio / factor
            closer = abs(math.log(ratio) - math.log(factor)) < abs(math.log(ratio))
            if not (0.70 <= residual <= 1.30 and closer):
                rejected.append([code, rcept, f"price_ratio {ratio:.4f} inconsistent with factor {factor:.4f} at {ex_date}"])
                continue
            confirmed.append({
                "stock_code": code, "ex_date": ex_date, "record_date": record_date, "rcept_no": rcept,
                "per_share": per_share, "factor": factor, "price_ratio": ratio, "residual_move": residual,
                "old_shares": before, "new_shares": before * (1 + per_share) if before else None,
            })
    superseded = []
    if apply and confirmed:
        conn.execute(f"CREATE TABLE IF NOT EXISTS corporate_action_events_backup_{run_id} AS "
                     "SELECT * FROM corporate_action_events WHERE 1=0")
        now = datetime.now().isoformat(timespec="seconds")
        for c in confirmed:
            end60 = (date.fromisoformat(c["ex_date"]) + timedelta(days=60)).isoformat()
            conn.execute(
                f"""INSERT INTO corporate_action_events_backup_{run_id}
                    SELECT * FROM corporate_action_events WHERE stock_code=?
                      AND (event_date=? OR (event_date>? AND event_date<=?) OR evidence_rcept_no=?)""",
                (c["stock_code"], c["ex_date"], c["ex_date"], end60, c["rcept_no"]),
            )
            note = (f"DART fricDecsn {c['rcept_no']}: 1주당 {c['per_share']}주, 배정기준일 {c['record_date']} "
                    f"→ 권리락일 {c['ex_date']}, 계수 {c['factor']:.6f}; 권리락일 종가비 {c['price_ratio']:.4f} "
                    f"(잔여변동 {c['residual_move']:.3f}, 제한폭 이내) [{run_id}]")
            conn.execute(
                """INSERT INTO corporate_action_events
                     (stock_code,event_date,event_type,old_shares,new_shares,share_ratio,backward_price_factor,
                      evidence_report_name,evidence_rcept_no,evidence_url,source,confidence,adjustment_status,note,
                      created_at,updated_at)
                   VALUES (?,?,'bonus_issue',?,?,?,?,'주요사항보고서(무상증자결정)',?,?,'DART_fricDecsn_api',0.95,
                           'factor_confirmed',?,?,?)
                   ON CONFLICT(stock_code,event_date,event_type) DO UPDATE SET
                     old_shares=excluded.old_shares,new_shares=excluded.new_shares,share_ratio=excluded.share_ratio,
                     backward_price_factor=excluded.backward_price_factor,evidence_report_name=excluded.evidence_report_name,
                     evidence_rcept_no=excluded.evidence_rcept_no,evidence_url=excluded.evidence_url,
                     source=excluded.source,confidence=excluded.confidence,
                     adjustment_status='factor_confirmed',note=excluded.note,updated_at=excluded.updated_at""",
                (c["stock_code"], c["ex_date"], c["old_shares"], c["new_shares"], 1 + c["per_share"], c["factor"],
                 c["rcept_no"], f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={c['rcept_no']}", note, now, now),
            )
            for rid, edate, f in conn.execute(
                """SELECT id, event_date, backward_price_factor FROM corporate_action_events
                   WHERE stock_code=? AND adjustment_status='factor_confirmed' AND backward_price_factor IS NOT NULL
                     AND event_date>? AND event_date<=?""",
                (c["stock_code"], c["ex_date"], end60),
            ).fetchall():
                if abs(float(f) - c["factor"]) <= 0.02 * c["factor"]:
                    conn.execute(
                        """UPDATE corporate_action_events SET adjustment_status='superseded_by_ex_date',
                             note=COALESCE(note,'') || ?, updated_at=? WHERE id=?""",
                        (f" | superseded by ex-date {c['ex_date']} ({run_id})", now, rid),
                    )
                    superseded.append([c["stock_code"], str(edate)[:10], float(f)])
            conn.execute(
                """UPDATE corporate_action_events SET adjustment_status='superseded_by_ex_date',
                     note=COALESCE(note,'') || ?, updated_at=?
                   WHERE stock_code=? AND evidence_rcept_no=? AND event_type='bonus_issue'
                     AND adjustment_status='review_required' AND event_date<>?""",
                (f" | decision-date row; ex-date {c['ex_date']} confirmed ({run_id})", now,
                 c["stock_code"], c["rcept_no"], c["ex_date"]),
            )
        conn.execute(
            """INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                   new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
            (now, "corporate_action_events", "bonus_issue ex-date factors", len(confirmed),
             "DART fricDecsn ratio -> ex-date(prev trading day of record date) factor 1/(1+x); price ratio residual within ±30% limit; same-factor rows within 60d superseded",
             f"backup table corporate_action_events_backup_{run_id}; superseded {len(superseded)}",
             f"factor_confirmed {len(confirmed)} (source=DART_fricDecsn_api)",
             "OpenDART fricDecsn.json", run_id),
        )
        conn.commit()
    conn.close()
    result = {"run_id": run_id, "applied": apply, "confirmed": confirmed, "superseded": superseded,
              "rejected": rejected, "errors": errors}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--codes", default="")
    a = ap.parse_args()
    res = run(a.apply, {c.strip() for c in a.codes.split(",") if c.strip()})
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}, ensure_ascii=False))
