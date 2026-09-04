"""
ETF_check K-ETF 기준 일자 재수집 헬퍼.

용도:
- 특정 거래일의 ETF 데이터를 offset/limit chunk로 순차 재수집
- 오래 남은 running collection_log를 먼저 error로 정리
- 이미 일부만 채워진 날짜도 이어서 계속 수집 가능

예시:
  /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python ETF_check/recollect_ketf_day.py --date 2026-08-14 --start-offset 1500
"""
from __future__ import annotations

import argparse
from datetime import date

from collector import _interrupt_stale_collection_logs, run_collection


def main() -> int:
    parser = argparse.ArgumentParser(description="ETF K-ETF 일자 재수집")
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--end-offset", type=int, default=2693)
    parser.add_argument("--chunk-size", type=int, default=250)
    args = parser.parse_args()

    _interrupt_stale_collection_logs(max_age_hours=0)

    offset = max(0, args.start_offset)
    end_offset = max(offset, args.end_offset)
    chunk = max(1, args.chunk_size)

    while offset < end_offset:
        current_limit = min(chunk, end_offset - offset)
        print(f"[RUN] trade_date={args.date} offset={offset} limit={current_limit}")
        run_collection(trade_date=args.date, limit=current_limit, offset=offset)
        offset += current_limit

    print(f"[DONE] trade_date={args.date} processed offsets {args.start_offset}..{end_offset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
