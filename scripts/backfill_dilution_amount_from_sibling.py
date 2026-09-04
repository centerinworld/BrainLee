"""무비용(API 재호출 없음) 희석 이벤트 금액 백필 — 같은 이벤트의 자매 공시에서 금액 복사.

2026-07-29 발견: `scripts/backfill_dilution_issue_amounts.py`가 DART 문서를 재파싱해도
`[첨부정정]`(첨부파일만 정정) 유형 공시는 DART document.xml 응답이 18자 스텁뿐이라 절대
파싱 성공할 수 없음(첨부파일 자체를 봐야 함, 텍스트 API로는 원천적으로 불가능) — 그런데
같은 종목·같은 event_type(CB/BW/EB/RIGHTS)의 `[기재정정]`이나 원본 공시가 근처 날짜에
이미 금액을 갖고 있는 경우가 많음(같은 사채/증자 건에 대한 정정 사이클). 새 DART API 호출
없이 이미 수집된 dilution_events 내에서 자매 행 매칭만으로 복구 — 완전 무료.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "stock.db"

# 2026-07-29(2차) 재발방지: 1차 실행 때 report_nm 필터 없이 event_type만 보고 자매금액을
# 채워 넣었다가, "만기전사채취득"(상환)/"자기전환사채매도"(재매각)/"최종발행가액확정"(가격
# 확정)/"권리락"/"종속회사" 같은 비발행성 공시 1,130건에 issue_amount가 잘못 채워지는
# 사고가 있었음(risk_amount_status='not_amount_applicable'로 이미 올바르게 분류돼 있었는데
# amount만 의미없이 채워짐 — 리스크게이트/텐버거는 status로 필터링해 기능상 영향은 없었지만
# 데이터 정합성 문제라 전량 NULL로 원복함). 재발 방지를 위해 대상(target) 선정 시에도
# classify_dilution_event_quality.py와 동일한 배제 키워드를 적용.
_SKIP_NAME_RE = re.compile(
    r"만기전.*취득|자기전환사채|자기사채|매도결정|매수선택권행사자지정|"
    r"발행가액확정|권리락|종속회사|자회사|청약결과|철회|취득후재매각"
)


def _is_issuance_target(report_nm: str | None) -> bool:
    name = (report_nm or "").replace(" ", "")
    if not name:
        return False
    return not _SKIP_NAME_RE.search(name)


def backfill(window_days: int = 120, dry_run: bool = True) -> dict:
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row

    donors = conn.execute("""
        SELECT stock_code, event_type, disclosed_at, issue_amount
        FROM dilution_events
        WHERE issue_amount IS NOT NULL AND issue_amount > 0 AND disclosed_at IS NOT NULL
    """).fetchall()
    donor_map: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for d in donors:
        key = (d["stock_code"], d["event_type"])
        donor_map.setdefault(key, []).append((d["disclosed_at"][:10], d["issue_amount"]))

    targets_all = conn.execute("""
        SELECT id, rcept_no, stock_code, event_type, disclosed_at, report_nm
        FROM dilution_events
        WHERE (issue_amount IS NULL OR issue_amount <= 0)
          AND event_type IN ('CB','BW','EB','RIGHTS')
          AND disclosed_at IS NOT NULL
    """).fetchall()
    targets = [t for t in targets_all if _is_issuance_target(t["report_nm"])]

    updated = 0
    no_donor = 0
    examples = []
    for t in targets:
        key = (t["stock_code"], t["event_type"])
        cands = donor_map.get(key)
        if not cands:
            no_donor += 1
            continue
        t_date = date.fromisoformat(t["disclosed_at"][:10])
        best = None
        best_gap = None
        for d_date_s, amount in cands:
            d_date = date.fromisoformat(d_date_s)
            gap = abs((d_date - t_date).days)
            if gap <= window_days and (best_gap is None or gap < best_gap):
                best, best_gap = amount, gap
        if best is None:
            no_donor += 1
            continue
        if not dry_run:
            conn.execute(
                "UPDATE dilution_events SET issue_amount=? WHERE id=?",
                (best, t["id"]),
            )
        updated += 1
        if len(examples) < 15:
            examples.append({
                "rcept_no": t["rcept_no"], "stock_code": t["stock_code"],
                "event_type": t["event_type"], "date": t["disclosed_at"],
                "report_nm": t["report_nm"], "amount_억": round(best / 1e8, 2),
                "gap_days": best_gap,
            })
    if not dry_run:
        conn.commit()
    conn.close()
    return {
        "window_days": window_days, "dry_run": dry_run,
        "scanned_all": len(targets_all), "skipped_non_issuance": len(targets_all) - len(targets),
        "scanned": len(targets), "updated": updated, "no_donor": no_donor,
        "examples": examples,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=120)
    ap.add_argument("--apply", action="store_true", help="실제 UPDATE 수행(기본은 dry-run)")
    args = ap.parse_args()
    result = backfill(window_days=args.window_days, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
