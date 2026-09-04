#!/usr/bin/env python3
"""Classify dilution events into usable risk buckets.

The raw `dilution_events` table intentionally mixes confirmed issuance decisions,
equity issues, bonus issues, amendments, results, self-CB transactions and legacy
disclosure parses.  Trading logic should not treat all rows as equal.  This
script adds stable classification columns and keeps a compact audit trail.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


def compact(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dilution_events)").fetchall()}
    ddl = {
        "risk_amount_status": "ALTER TABLE dilution_events ADD COLUMN risk_amount_status TEXT",
        "risk_event_bucket": "ALTER TABLE dilution_events ADD COLUMN risk_event_bucket TEXT",
        "risk_use_note": "ALTER TABLE dilution_events ADD COLUMN risk_use_note TEXT",
        "risk_classified_at": "ALTER TABLE dilution_events ADD COLUMN risk_classified_at TEXT",
    }
    for col, sql in ddl.items():
        if col not in cols:
            conn.execute(sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_de_risk_bucket ON dilution_events(risk_event_bucket, disclosed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_de_amount_status ON dilution_events(risk_amount_status, disclosed_at)")


def classify(row: sqlite3.Row) -> tuple[str, str, str]:
    event_type = (row["event_type"] or "").upper()
    report = compact(row["report_nm"])
    source = row["data_source"] or ""
    amount = row["issue_amount"]
    dilution = row["dilution_pct"]

    is_amount_confirmed = amount is not None and float(amount or 0) > 0
    is_dilution_confirmed = dilution is not None and float(dilution or 0) > 0

    if event_type == "BONUS":
        return (
            "not_amount_applicable",
            "capital_action_bonus",
            "무상증자는 조달금액이 아니라 주식수/가격조정 이벤트로 해석",
        )
    if event_type == "RIGHTS_RESULT":
        return (
            "not_amount_applicable",
            "equity_issue_result",
            "유상증자 결과/청약 결과는 중복 금액 집계 대상에서 제외하고 이벤트 플래그로만 사용",
        )

    non_issuance_keywords = (
        "만기전사채취득",
        "자기전환사채",
        "자기사채",
        "사채취득",
        "자기신주인수권",
        "소각",
        "매도결정",
        "행사가액조정",
        "전환가액조정",
        "발행가액확정",
        "권리락",
        "종속회사",
        "자회사",
        "철회",
    )
    if any(k in report for k in non_issuance_keywords):
        return (
            "not_amount_applicable",
            "legacy_non_issuance_event",
            "만기전취득/자기사채/가격조정/종속회사 등은 신규 희석 금액으로 해석하지 않음",
        )

    if event_type in {"CB", "BW", "EB"}:
        if is_amount_confirmed:
            return (
                "amount_confirmed",
                "mezzanine_issue",
                "CB/BW/EB 발행금액 확인: 금액 기반 희석/풋옵션 리스크 계산 가능",
            )
        return (
            "amount_missing_event_usable",
            "mezzanine_issue_amount_missing",
            "CB/BW/EB 이벤트는 유효하지만 금액 기반 리스크에는 사용 금지",
        )

    if event_type in {"RIGHTS", "RIGHTS_BONUS"}:
        if is_amount_confirmed:
            return (
                "amount_confirmed",
                "equity_issue",
                "유상/유무상증자 조달금액 확인: 금액 기반 희석 리스크 계산 가능",
            )
        if is_dilution_confirmed:
            return (
                "amount_missing_event_usable",
                "equity_issue_amount_missing",
                "금액은 없지만 신주/희석률은 있어 건수/희석률 리스크로 사용 가능",
            )
        return (
            "amount_missing_event_usable",
            "equity_issue_amount_missing",
            "유상/유무상증자 이벤트는 유효하지만 금액 기반 리스크에는 사용 금지",
        )

    if source == "dart_disclosure_parse":
        return (
            "legacy_unclassified",
            "legacy_parse_review",
            "레거시 공시 파싱 행: 방어적 이벤트 플래그로만 사용하고 금액 기반 리스크 제외",
        )

    return (
        "review_required",
        "unknown_dilution_event",
        "분류 규칙 외 이벤트: 전략 반영 전 원문 확인 필요",
    )


def main() -> None:
    conn = sqlite3.connect(DB, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    ensure_columns(conn)

    now = datetime.now().isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT id, event_type, report_nm, data_source, issue_amount, dilution_pct
        FROM dilution_events
        """
    ).fetchall()
    status_counts: Counter[str] = Counter()
    bucket_counts: Counter[str] = Counter()
    changed = 0
    for row in rows:
        status, bucket, note = classify(row)
        conn.execute(
            """
            UPDATE dilution_events
            SET risk_amount_status=?,
                risk_event_bucket=?,
                risk_use_note=?,
                risk_classified_at=?
            WHERE id=?
            """,
            (status, bucket, note, now, row["id"]),
        )
        status_counts[status] += 1
        bucket_counts[bucket] += 1
        changed += 1
    conn.commit()

    summary = {
        "generated_at": now,
        "rows_classified": changed,
        "risk_amount_status": dict(status_counts.most_common()),
        "risk_event_bucket": dict(bucket_counts.most_common()),
    }
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = OUT_DIR / f"dilution_event_quality_{stamp}.json"
    md_path = OUT_DIR / f"dilution_event_quality_{stamp}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Dilution Event Quality — {stamp}",
        "",
        f"- rows classified: {changed:,}",
        "",
        "## Amount Status",
        "|status|rows|",
        "|---|---:|",
    ]
    for k, v in status_counts.most_common():
        lines.append(f"|{k}|{v:,}|")
    lines += ["", "## Event Bucket", "|bucket|rows|", "|---|---:|"]
    for k, v in bucket_counts.most_common():
        lines.append(f"|{k}|{v:,}|")
    lines += [
        "",
        "## Usage Rule",
        "- `amount_confirmed`: 금액 기반 리스크/시총대비 조달규모/풋옵션 현금부족 계산 가능",
        "- `amount_missing_event_usable`: 건수·희석률·경고 플래그만 사용, 금액 기반 계산 제외",
        "- `not_amount_applicable`: 중복/비조달/가격조정 이벤트로 금액 결측이 정상",
        "- `legacy_unclassified`/`review_required`: 자동매매 핵심 필터에는 보수적으로 감점 또는 검토 필요 처리",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
