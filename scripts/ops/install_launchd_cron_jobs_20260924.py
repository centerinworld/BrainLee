#!/usr/bin/env python3
"""Migrate the project's crontab jobs to per-user LaunchAgents (2026-09-24).

Why: macOS blocks /usr/sbin/cron from opening files on the external volume ("Operation not permitted" in
/var/mail/brainlee), so since 2026-08-28/29 almost every cron job failed before it started. LaunchAgents run in the
user session like com.stock-dashboard.local (which already works from the same paths).

Each job -> ~/Library/LaunchAgents/com.stock-dashboard.cron.<name>.plist (bash -c "<command>", StartCalendarInterval),
bootstrapped into gui/<uid>. The matching crontab line is commented out with '#MIGRATED-TO-LAUNCHD ' (crontab backup is
written first). Weekday: cron 1-5 = launchd 1..5, cron 2-6 = 2..6 (0 = Sunday in both). Dry-run default; --apply writes.
The unrelated '@reboot ... ceo-briefing' line is left untouched."""
import os, plistlib, subprocess, sys
from pathlib import Path

R = "/Volumes/Realtek_NVME/stock_dashboard/runtime"
PY = f"{R}/venv/bin/python3"
BOOT = f"PYTHONPATH={R}/runtime_pg_bootstrap"
# name, marker substring identifying the crontab line, shell command, list of calendar dicts
JOBS = [
    ("public_data_company", "public_data_collector.py --company-only", f"cd {R} && {BOOT} {PY} {R}/public_data_collector.py --company-only >> {R}/public_data.log 2>&1", [dict(Weekday=1, Hour=7, Minute=0)]),
    ("telegram_collector", "telegram_collector.py", f"cd {R} && {BOOT} {PY} {R}/telegram_collector.py >> {R}/telegram_collector.log 2>&1", [dict(Hour=8, Minute=30)]),
    ("telegram_monitor", "telegram_monitor.py", f"cd {R} && {BOOT} {PY} {R}/telegram_monitor.py >> {R}/telegram_monitor.log 2>&1", [dict(Hour=9, Minute=0), dict(Hour=21, Minute=0)]),
    ("cron_3am", "cron_3am.sh", f"{R}/cron_3am.sh >> {R}/cron_3am.log 2>&1", [dict(Weekday=w, Hour=3, Minute=0) for w in range(1, 6)]),
    ("hs_daily_refresh", "hs_trade_lab && ", f"cd {R}/hs_trade_lab && {R}/venv/bin/python scripts/daily_refresh.py >> {R}/hs_trade_lab/data/daily_refresh_cron.log 2>&1", [dict(Hour=4, Minute=30)]),
    ("weekly_revalidation_a", "weekly_revalidation.py --phase A", f"cd {R} && {BOOT} {PY} scratch/weekly_revalidation.py --phase A >> scratch/phase_a_cron.log 2>&1", [dict(Hour=4, Minute=0)]),
    ("quant_daily", "quant_indicators_cron.py --mode daily", f"cd {R} && {PY} scripts/ops/quant_indicators_cron.py --mode daily >> logs/quant_daily.log 2>&1", [dict(Weekday=w, Hour=19, Minute=30) for w in range(1, 6)]),
    ("quant_weekly", "quant_indicators_cron.py --mode weekly", f"cd {R} && {PY} scripts/ops/quant_indicators_cron.py --mode weekly >> logs/quant_weekly.log 2>&1", [dict(Weekday=1, Hour=8, Minute=0)]),
    ("quant_monthly", "quant_indicators_cron.py --mode monthly", f"cd {R} && {PY} scripts/ops/quant_indicators_cron.py --mode monthly >> logs/quant_monthly.log 2>&1", [dict(Day=12, Hour=5, Minute=0)]),
    ("quant_annual", "quant_indicators_cron.py --mode annual", f"cd {R} && {PY} scripts/ops/quant_indicators_cron.py --mode annual >> logs/quant_annual.log 2>&1", [dict(Month=1, Day=20, Hour=5, Minute=0)]),
    ("detailed_analysis_refresh", "refresh_detailed_analysis_multi_channel.py", f"cd {R} && OPENAI_API_KEY= {BOOT} {PY} scripts/ops/refresh_detailed_analysis_multi_channel.py --limit 999 >> {R}/logs/detailed_analysis_refresh.log 2>&1", [dict(Hour=9, Minute=30), dict(Hour=21, Minute=30)]),
    ("etf_retry_full_pdf", "retry_etf_full_pdf_v7.sh", f"{R}/scripts/retry_etf_full_pdf_v7.sh", [dict(Weekday=w, Hour=2, Minute=15) for w in range(2, 7)]),
    ("etf_retry_issuer", "retry_etf_issuer_fallback.sh", f"{R}/scripts/retry_etf_issuer_fallback.sh", [dict(Weekday=w, Hour=3, Minute=0) for w in range(2, 7)]),
    ("etf_retry_scale", "retry_etf_scale_collection.sh", f"{R}/scripts/retry_etf_scale_collection.sh", [dict(Weekday=w, Hour=2, Minute=45) for w in range(2, 7)]),
    ("gemini_gems_worker", "gemini_gems_worker.py", f"cd {R} && PYTHONPATH={R}/runtime_pg_bootstrap:{R} {PY} gemini_gems_worker.py >> {R}/logs/gems_daily.log 2>&1", [dict(Hour=6, Minute=0)]),
]

def main(apply):
    uid = os.getuid(); agents = Path.home() / "Library/LaunchAgents"
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines()
    print("crontab lines:", len(cron))
    new_cron = list(cron); report = []
    for name, marker, cmd, cal in JOBS:
        idx = [i for i, l in enumerate(cron) if marker in l and (not l.lstrip().startswith("#") or l.startswith("#MIGRATED-TO-LAUNCHD "))]
        if len(idx) != 1:
            report.append((name, f"cron line matches: {len(idx)} (skipped)")); continue
        label = f"com.stock-dashboard.cron.{name}"
        plist = dict(Label=label, ProgramArguments=["/bin/zsh", "-c", cmd], StartCalendarInterval=cal,
                     EnvironmentVariables={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}, RunAtLoad=False, ProcessType="Background",
                     StandardErrorPath=f"/tmp/launchd_cron_{name}.err")  # launchd itself cannot open files on the external volume (EX_CONFIG 78)
        report.append((name, "ok"))
        if apply:
            path = agents / f"{label}.plist"; path.write_bytes(plistlib.dumps(plist))
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
            r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], capture_output=True, text=True)
            if r.returncode != 0:
                report[-1] = (name, "bootstrap failed: " + r.stderr.strip()[:120]); continue
            new_cron[idx[0]] = cron[idx[0]] if cron[idx[0]].startswith("#MIGRATED-TO-LAUNCHD ") else "#MIGRATED-TO-LAUNCHD " + cron[idx[0]]
    for r in report: print(r)
    if apply:
        bk = Path(f"{R}/backup_crontab_20260924.txt")
        if not bk.exists(): bk.write_text("\n".join(cron) + "\n")
        subprocess.run(["crontab", "-"], input="\n".join(new_cron) + "\n", text=True, check=True)
        print("crontab updated; backup at", f"{R}/backup_crontab_20260924.txt")

if __name__ == "__main__":
    main("--apply" in sys.argv)
