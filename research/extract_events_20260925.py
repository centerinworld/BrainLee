#!/usr/bin/env python3
"""Extract disclosure-event tables to data_cache/research/events.parquet (prod venv; read-only) for the event study."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402


def dt(s):
    s = str(s or "")[:10].replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else None


def main() -> None:
    conn = connect_primary_db(timeout=600)
    rows = []
    for code, d, cls in conn.execute("SELECT stock_code,rcept_dt,event_class FROM treasury_buyback WHERE event_class IN ('취득결정','신탁체결','소각','처분결정')").fetchall():
        rows.append((code, dt(d), {"취득결정": "buyback_acquire_decision", "신탁체결": "buyback_trust", "소각": "buyback_cancel", "처분결정": "buyback_disposal"}[cls]))
    for code, d, ins in conn.execute("SELECT stock_code,rcept_dt,instrument_type FROM dart_dilution_events").fetchall():
        rows.append((code, dt(d), f"dilution_{ins}"))
    for code, d in conn.execute("SELECT stock_code,disclosed_at FROM dart_contracts WHERE contract_ratio_pct>=10").fetchall():
        rows.append((code, dt(d), "contract_ratio_ge10"))
    for code, d, st in conn.execute("SELECT stock_code,rcept_dt,signal_type FROM dart_rd_patent_signals WHERE exclude_reason IS NULL").fetchall():
        rows.append((code, dt(d), f"rd_{st}"))
    df = pd.DataFrame(rows, columns=["code", "date", "kind"]).dropna()
    df = df[df.code.str.match(r"^\d{6}$")]
    out = ROOT / "data_cache" / "research" / "events.parquet"
    df.to_parquet(out)
    print(df.kind.value_counts().to_dict())


if __name__ == "__main__":
    main()
