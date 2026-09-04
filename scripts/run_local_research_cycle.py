#!/usr/bin/env python3
"""Run the local-first research cycle without BigQuery.

This is the default research runner for the current project scale.  It refreshes
point-in-time trigger tables, local insights, macro-sector validation and the
data-quality audits that guard trading signals.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "research_outputs"


def run_step(name: str, args: list[str], timeout: int = 900) -> dict:
    started = datetime.now()
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = (datetime.now() - started).total_seconds()
    return {
        "name": name,
        "args": args,
        "returncode": proc.returncode,
        "elapsed_sec": round(elapsed, 2),
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "ok": proc.returncode == 0,
    }


def main() -> None:
    steps = [
        ("dilution_quality", ["scripts/classify_dilution_event_quality.py"], 300),
        ("segment_dilution_audit", ["scripts/audit_segment_dilution_coverage.py"], 300),
        ("macro_indicator_backtest", ["scripts/ops/backtest_macro_indicator_candidates.py", "--promote"], 900),
        ("trigger_discovery_build", ["scripts/build_trigger_discovery_lab.py"], 1200),
        ("trigger_discovery_insights", ["scripts/generate_trigger_discovery_insights.py"], 600),
        ("external_provider_sample_audit", ["scripts/audit_external_provider_samples.py"], 300),
    ]
    results = []
    for name, args, timeout in steps:
        print(json.dumps({"event": "step_start", "name": name, "args": args}, ensure_ascii=False), flush=True)
        result = run_step(name, args, timeout)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result["ok"]:
            break

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "local_sqlite_no_bigquery",
        "all_ok": all(r["ok"] for r in results),
        "steps": results,
    }
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = OUT_DIR / f"local_research_cycle_{stamp}.json"
    md_path = OUT_DIR / f"local_research_cycle_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Local Research Cycle — {stamp}",
        "",
        f"- mode: {payload['mode']}",
        f"- all_ok: {payload['all_ok']}",
        "",
        "|step|ok|elapsed_sec|",
        "|---|---:|---:|",
    ]
    for r in results:
        lines.append(f"|{r['name']}|{r['ok']}|{r['elapsed_sec']}|")
    lines += [
        "",
        "BigQuery는 호출하지 않는다. 대규모 전기간×전종목×수백팩터 그리드서치가 필요할 때만 별도 수동 실행한다.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "all_ok": payload["all_ok"]}, ensure_ascii=False))
    if not payload["all_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
