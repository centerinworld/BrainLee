from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

# 실행 가능한 스텝 목록 (순서 유지)
ALL_STEPS = ["download", "ingest", "backfill", "flow_map", "promote_mappings", "rebuild_cache", "trade_cards", "provisional"]


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = (year * 12 + month - 1) + delta
    return month_index // 12, (month_index % 12) + 1


def _log(msg: str) -> None:
    print(f"[daily_refresh] {datetime.now().strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def _run_step(name: str, cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    _log(f"▶ {name} 시작")
    t0 = time.time()
    result = subprocess.run(cmd, cwd=ROOT_DIR, check=check, capture_output=capture, text=True)
    elapsed = time.time() - t0
    _log(f"✔ {name} 완료 ({elapsed:.1f}s, returncode={result.returncode})")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HS Trade Lab 일별 갱신 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"스텝 목록: {', '.join(ALL_STEPS)}",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=ALL_STEPS,
        metavar="STEP",
        default=None,
        help="실행할 스텝만 지정 (기본: 전체). 예: --steps backfill rebuild_cache",
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        choices=ALL_STEPS,
        metavar="STEP",
        default=[],
        help="건너뛸 스텝 지정. 예: --skip download provisional",
    )
    args = parser.parse_args()

    active_steps: list[str] = args.steps if args.steps else ALL_STEPS
    active_steps = [s for s in active_steps if s not in args.skip]

    _log(f"실행 스텝: {active_steps}")

    now = datetime.now()
    current_year = now.year
    start_year = current_year - 1
    provisional_start_year, provisional_start_month = add_months(now.year, now.month, -1)
    provisional_start_ym = f"{provisional_start_year}{provisional_start_month:02d}"
    provisional_end_ym = f"{now.year}{now.month:02d}"

    venv_python = str(ROOT_DIR.parent / "venv" / "bin" / "python")

    # ── 스텝별 커맨드 정의 ──────────────────────────────────────────
    cmds: dict[str, list[str]] = {
        "download": [
            venv_python,
            str(ROOT_DIR / "scripts" / "download_customs_data.py"),
            "--start-year", str(start_year),
            "--end-year", str(current_year),
            "--endpoints",
            "itemtrade", "sidoitemtrade", "sidotempertrade",
            "nationtrade", "idfytempertrade", "sidotrade",
            "--force",
        ],
        "ingest": [venv_python, str(ROOT_DIR / "scripts" / "ingest_customs_data.py")],
        "backfill": [venv_python, str(ROOT_DIR / "scripts" / "backfill_telegram_posts.py")],
        "flow_map": [venv_python, str(ROOT_DIR / "scripts" / "rebuild_telegram_flow_mappings.py")],
        "promote_mappings": [venv_python, str(ROOT_DIR / "scripts" / "promote_verified_sector_mappings.py")],
        "rebuild_cache": [venv_python, str(ROOT_DIR / "scripts" / "rebuild_analysis2_cache.py")],
        "trade_cards": [venv_python, str(ROOT_DIR / "scripts" / "build_telegram_trade_cards.py")],
        "provisional": [
            venv_python,
            str(ROOT_DIR / "scripts" / "collect_provisional_10day.py"),
            "--start-ym", provisional_start_ym,
            "--end-ym", provisional_end_ym,
            "--export-csv",
        ],
    }

    # ── 실행 ────────────────────────────────────────────────────────
    results: dict[str, subprocess.CompletedProcess | None] = {s: None for s in ALL_STEPS}

    if "download" in active_steps:
        _run_step("download", cmds["download"], check=True, capture=False)

    for step in ["ingest", "backfill", "flow_map", "promote_mappings", "rebuild_cache", "trade_cards"]:
        if step in active_steps:
            results[step] = _run_step(step, cmds[step], check=True, capture=True)

    if "provisional" in active_steps:
        results["provisional"] = _run_step("provisional", cmds["provisional"], check=False, capture=True)

    # ── 요약 JSON 생성 ───────────────────────────────────────────────
    def _parse(r: subprocess.CompletedProcess | None) -> object:
        if r is None:
            return {"status": "skipped"}
        if r.returncode != 0:
            return {"status": "failed", "returncode": r.returncode, "stderr": (r.stderr or "")[-2000:]}
        try:
            return json.loads(r.stdout or "{}")
        except Exception:
            return {"status": "ok", "stdout_snippet": (r.stdout or "")[:500]}

    provisional_result = _parse(results["provisional"])

    summary = {
        "ran_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "active_steps": active_steps,
        "download_years": [start_year, current_year],
        "provisional_10day_months": [provisional_start_ym, provisional_end_ym],
        "ingest_result": _parse(results["ingest"]),
        "telegram_backfill_result": _parse(results["backfill"]),
        "telegram_flow_map_result": _parse(results["flow_map"]),
        "promote_mappings_result": _parse(results["promote_mappings"]),
        "analysis2_cache_result": _parse(results["rebuild_cache"]),
        "telegram_trade_card_result": _parse(results["trade_cards"]),
        "provisional_10day_result": provisional_result,
    }
    (ROOT_DIR / "data" / "daily_refresh_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
