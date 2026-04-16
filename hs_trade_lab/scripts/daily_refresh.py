from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    current_year = datetime.now().year
    start_year = current_year - 1
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
    subprocess.run(download_cmd, cwd=ROOT_DIR, check=True)
    ingest = subprocess.run(ingest_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    backfill = subprocess.run(backfill_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    rebuild = subprocess.run(rebuild_cache_cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True)
    summary = {
        "ran_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "download_years": [start_year, current_year],
        "ingest_result": json.loads(ingest.stdout or "{}"),
        "telegram_backfill_result": json.loads(backfill.stdout or "{}"),
        "analysis2_cache_result": json.loads(rebuild.stdout or "{}"),
    }
    (ROOT_DIR / "data" / "daily_refresh_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
