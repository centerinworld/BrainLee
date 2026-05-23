from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = (year * 12 + month - 1) + delta
    return month_index // 12, (month_index % 12) + 1


def main() -> None:
    now = datetime.now()
    current_year = now.year
    start_year = current_year - 1
    provisional_start_year, provisional_start_month = add_months(now.year, now.month, -1)
    provisional_start_ym = f"{provisional_start_year}{provisional_start_month:02d}"
    provisional_end_ym = f"{now.year}{now.month:02d}"
    download_cmd = [
        str(ROOT_DIR.parent / "venv" / "bin" / "python"),
        str(ROOT_DIR / "scripts" / "download_customs_data.py"),
        "--start-year",
        str(start_year),
        "--end-year",
        str(current_year),
        "--endpoints",
        "itemtrade",
        "sidoitemtrade",
        "sidotempertrade",
        "nationtrade",
        "idfytempertrade",
        "sidotrade",
        "--force",
    ]
    ingest_cmd = [
        str(ROOT_DIR.parent / "venv" / "bin" / "python"),
        str(ROOT_DIR / "scripts" / "ingest_customs_data.py"),
    ]
    backfill_cmd = [
        str(ROOT_DIR.parent / "venv" / "bin" / "python"),
        str(ROOT_DIR / "scripts" / "backfill_telegram_posts.py"),
    ]
    rebuild_cache_cmd = [
        str(ROOT_DIR.parent / "venv" / "bin" / "python"),
        str(ROOT_DIR / "scripts" / "rebuild_analysis2_cache.py"),
    ]
    rebuild_flow_mapping_cmd = [
        str(ROOT_DIR.parent / "venv" / "bin" / "python"),
        str(ROOT_DIR / "scripts" / "rebuild_telegram_flow_mappings.py"),
    ]
    build_trade_cards_cmd = [
        str(ROOT_DIR.parent / "venv" / "bin" / "python"),
        str(ROOT_DIR / "scripts" / "build_telegram_trade_cards.py"),
    ]
    provisional_cmd = [
        str(ROOT_DIR.parent / "venv" / "bin" / "python"),
        str(ROOT_DIR / "scripts" / "collect_provisional_10day.py"),
        "--start-ym",
        provisional_start_ym,
        "--end-ym",
        provisional_end_ym,
        "--export-csv",
    ]
    subprocess.run(download_cmd, cwd=ROOT_DIR, check=True)
    ingest = subprocess.run(ingest_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    backfill = subprocess.run(backfill_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    flow_map = subprocess.run(rebuild_flow_mapping_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    rebuild = subprocess.run(rebuild_cache_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    cards = subprocess.run(build_trade_cards_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    provisional = subprocess.run(provisional_cmd, cwd=ROOT_DIR, check=False, capture_output=True, text=True)
    provisional_result = {}
    if provisional.returncode == 0:
        provisional_result = json.loads(provisional.stdout or "{}")
    else:
        provisional_result = {
            "status": "failed_nonfatal",
            "returncode": provisional.returncode,
            "stderr": provisional.stderr[-2000:],
        }
    summary = {
        "ran_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "download_years": [start_year, current_year],
        "provisional_10day_months": [provisional_start_ym, provisional_end_ym],
        "ingest_result": json.loads(ingest.stdout or "{}"),
        "telegram_backfill_result": json.loads(backfill.stdout or "{}"),
        "telegram_flow_map_result": json.loads(flow_map.stdout or "{}"),
        "analysis2_cache_result": json.loads(rebuild.stdout or "{}"),
        "telegram_trade_card_result": json.loads(cards.stdout or "{}"),
        "provisional_10day_result": provisional_result,
    }
    (ROOT_DIR / "data" / "daily_refresh_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
