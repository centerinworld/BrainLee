#!/usr/bin/env python3
"""Fetch a fixed DART sample and verify the current backlog parser read-only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from collectors.dart_backlog_collector import (
    PARSER_VERSION,
    _extract_backlog,
    _fetch_document_with_key_rotation,
)


CASES = (
    ("009540", "HD한국조선해양 정상 회귀", "20260515001799"),
    ("084370", "유진테크 증감표 정상 회귀", "20260319000837"),
    ("003030", "세아제강 각주 오염 회귀", "20260515001160"),
    ("010420", "3열 수주잔고 표 정상 회귀", "20260312000618"),
    ("065130", "시장 전체 배터리 수주잔고 혼입", "20200330003103"),
    ("078890", "파생상품 계약잔액 혼입", "20170814000492"),
    ("340930", "억원 축약형", "20240719000309"),
    ("015590", "공사손실충당부채 표 혼입", "20201116000409"),
    ("073010", "수주상황 단위 경계", "20250814001731"),
    ("267250", "원유선도 계약잔액 혼입", "20230515002586"),
)


def main() -> int:
    results = []
    for stock_code, label, rcept_no in CASES:
        text = _fetch_document_with_key_rotation(rcept_no)
        if not text:
            results.append({
                "stock_code": stock_code,
                "label": label,
                "rcept_no": rcept_no,
                "status": "fetch_failed",
            })
            continue
        metric = _extract_backlog(text)
        results.append({
            "stock_code": stock_code,
            "label": label,
            "rcept_no": rcept_no,
            "status": "parsed" if metric.backlog_amount_krw is not None else "no_metric",
            "amount_krw": metric.backlog_amount_krw,
            "unit": metric.backlog_unit,
            "confidence": metric.backlog_confidence,
            "excerpt": metric.source_excerpt[:500],
        })
    print(json.dumps({"parser_version": PARSER_VERSION, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
