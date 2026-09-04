"""Production KRX full-PDF collector with sample-publication protection."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import full_pdf_collector as collector
import full_pdf_collector_v3 as v3


_collect = collector.collect


def remove_sample_publication(db_path: Path, day: str, run_id: int | None = None) -> None:
    conn = collector.connect(db_path)
    with conn:
        conn.execute("DELETE FROM etf_pdf_full_publication WHERE base_date=?", (day,))
        if run_id is not None:
            conn.execute(
                """
                UPDATE etf_pdf_full_run
                SET status='sample_complete',is_complete=0
                WHERE run_id=?
                """,
                (run_id,),
            )
    conn.close()


def safe_collect(
    day: str,
    db_path: Path = collector.DB_PATH,
    raw_root: Path = collector.RAW_ROOT,
    delay: float = 0.35,
    retries: int = 3,
    limit: int | None = None,
    force: bool = False,
    headless: bool = True,
) -> dict[str, Any]:
    result = _collect(day, db_path, raw_root, delay, retries, limit, force, headless)
    if limit is not None:
        remove_sample_publication(db_path, day, result.get("run_id"))
        result["complete"] = False
        result["sample_only"] = True
    return result


collector.KRXSession = v3.CurrentKRXSession
collector.collect = safe_collect


if __name__ == "__main__":
    collector.main()
