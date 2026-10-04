#!/usr/bin/env python3
"""판정이 남은 가격 행을 위한 제3 원주가 소스: KIS 일봉 원주가(FID_ORG_ADJ_PRC=1) 원문 저장(2026-10-04, 읽기 전용 수집).

대상(종목별 필요한 기간만):
  - price_raw_basis_audit_20261004/mismatch_rows.csv 의 남은 불일치 행(공식≠marcap 보류, 기업행위 경계 B·C, 소액 차이 D 등)
  - fractional_rows_classified.csv 의 ETF 소수점 행(marcap 미포함 종목)
저장: /Volumes/Realtek_NVME/stock_dashboard/data_raw/kis_raw_daily/<code>.json  ({date: {open,high,low,close,volume}})
이미 저장된 날짜는 다시 받지 않는다. 호출 간격 config.KIS_RATE_LIMIT_SECS.
"""
import asyncio
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from collectors.kis_collector import KISCollector  # noqa: E402
from kis_client import KISClient  # noqa: E402

D = ROOT / "research_outputs" / "price_raw_basis_audit_20261004"
RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/kis_raw_daily")


def targets():
    m = pd.read_csv(D / "mismatch_rows.csv", dtype={"code": str})[["code", "date"]]
    f = pd.read_csv(D / "fractional_rows_classified.csv", dtype={"code": str})
    f = f[f.cls == "marcap에 종목 없음"][["code", "date"]]
    t = pd.concat([m, f]).drop_duplicates()
    return {c: (g.date.min(), g.date.max(), set(g.date)) for c, g in t.groupby("code")}


async def main():
    RAW.mkdir(parents=True, exist_ok=True)
    tg = targets()
    col = KISCollector(kis_client=KISClient())
    print(f"대상 {len(tg)}종목", flush=True)
    for i, (code, (d0, d1, need)) in enumerate(sorted(tg.items()), 1):
        p = RAW / f"{code}.json"
        have = json.loads(p.read_text()) if p.exists() else {}
        if need <= set(have):
            continue
        rows = await col.fetch_period_ohlcv(code, d0.replace("-", ""), d1.replace("-", ""), adj_price="1")
        for r in rows:
            have[str(r["date"])[:10]] = {k: r[k] for k in ("open", "high", "low", "close", "volume")}
        p.write_text(json.dumps(have, ensure_ascii=False))
        if i % 20 == 0:
            print(f"  {i}/{len(tg)} {code} {len(rows)}행", flush=True)
    print("완료", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
