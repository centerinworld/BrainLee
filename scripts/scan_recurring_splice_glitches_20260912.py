#!/usr/bin/env python3
"""Comprehensive scan for recurring, scattered price_history/naver splice glitches.

Background: the known 2018-12-24~28 batch splice (already repaired) was a single
contiguous 4-day corrupted window. Investigating the "2022 cluster" mentioned in
docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md revealed a DIFFERENT,
larger-scope pattern: individual stocks intermittently show a stable divergence
ratio from naver on ISOLATED, SCATTERED single-or-two-day episodes throughout
their history (e.g. 005440 disagrees with naver by a constant ~1.5183x factor on
2022-01-26, 02-08, 02-09, 02-22, 02-23, 02-28, 03-10, 03-15, 03-17 - each time
reverting to an EXACT match the very next trading day). A per-day "how many
stocks glitch simultaneously" threshold only caught days where >=8 stocks
happened to glitch together and completely missed lower-multiplicity days like
2022-03-17 for this same stock - so the true scope must be found per-stock
across each stock's ENTIRE history, not by day-level clustering.

This reuses the exact same conservative candidate criteria already validated
against the confirmed 003490/005440 2018-12-24 cases and the Naver-verified
splice_repair_candidates_20260911.json pipeline, applied per-stock across full
history using the complete Naver snapshots collect_krx_history's sibling
Codex tooling already fetched into
research_outputs/price_snapshot_repair/20260911T200447/{code}.json.gz
(fetch(code, '20100101', today) - full history, no new network calls needed).

Candidate criteria per contiguous run of "episode" (ratio outside +/-2%) dates:
  - the run touches at least one date - runs are found across the WHOLE history
  - episode length <= 10 trading days
  - >= 5 consecutive exactly-matching (within 2%) trading days immediately
    before the episode starts (an established track record)
  - stable ratio within the episode (all days within 15% of the episode median)
  - median ratio is not close to a clean corporate-action fraction (2x, 3x, ...,
    1/2, 1/3, ... within 3%) - those need separate, dedicated investigation
  - reconverges to naver (average ratio within 5% of 1.0) over the first 5
    trading days after the episode ends
  - no corporate_action_events row overlapping the episode +/- 3 days

Writes a candidates JSON (dry-run artifact) but does not touch price_history.
Run scripts/apply_recurring_splice_repair_20260912.py --apply afterward.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

SNAP_DIR = ROOT / "research_outputs" / "price_snapshot_repair" / "20260911T200447"
OUT_DIR = ROOT / "research_outputs" / "price_integrity_remediation_20260909"

CLEAN_FRACTIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 25, 50, 100]
CLEAN_FRACTIONS = CLEAN_FRACTIONS + [1 / f for f in CLEAN_FRACTIONS if f != 1]
PRICE_ADJUSTING_REPORT_TERMS = ("분할", "병합", "감자", "증자", "합병")


def is_clean_fraction(ratio: float) -> bool:
    if ratio <= 0:
        return False
    return any(abs(ratio / f - 1) < 0.03 for f in CLEAN_FRACTIONS)


def overlaps_corporate_action_window(run_dates: list[str], ca_dates: set[str], days: int = 3) -> bool:
    """Return true when an event is within ``days`` calendar days of an episode."""
    if not run_dates or not ca_dates:
        return False
    start = date.fromisoformat(min(run_dates)) - timedelta(days=days)
    end = date.fromisoformat(max(run_dates)) + timedelta(days=days)
    return any(start <= date.fromisoformat(event_date) <= end for event_date in ca_dates)


def find_candidates_for_stock(code: str, ph: dict, nv: dict, ca_dates: set[str]) -> list[dict]:
    all_dates = sorted(set(ph) & set(nv))
    if len(all_dates) < 20:
        return []
    ratios: dict[str, float] = {}
    for d in all_dates:
        o, h, l, pc, v = ph[d]
        nc = nv[d][3]
        if v == 0 and o == 0 and h == 0 and l == 0:
            continue  # supplied suspension marker (price_integrity.invalid_ohlcv convention) -
            # close carries an indicative/no-trade value, not comparable to naver's real close
        if pc and nc and pc > 0 and nc > 0:
            ratios[d] = pc / nc
    idx = {d: i for i, d in enumerate(all_dates)}
    episode_dates = sorted(d for d in ratios if abs(ratios[d] - 1) > 0.02)
    if not episode_dates:
        return []
    runs: list[list[str]] = []
    cur = [episode_dates[0]]
    for d in episode_dates[1:]:
        if idx[d] == idx[cur[-1]] + 1:
            cur.append(d)
        else:
            runs.append(cur)
            cur = [d]
    runs.append(cur)

    out = []
    for run in runs:
        if len(run) > 10:
            continue
        run_ratios = [ratios[d] for d in run]
        med = sorted(run_ratios)[len(run_ratios) // 2]
        if med == 0 or not all(abs(r / med - 1) < 0.15 for r in run_ratios):
            continue
        if is_clean_fraction(med):
            continue
        start_i = idx[run[0]]
        prior_run = 0
        for i in range(start_i - 1, -1, -1):
            d = all_dates[i]
            if d in ratios and abs(ratios[d] - 1) <= 0.02:
                prior_run += 1
            else:
                break
        if prior_run < 5:
            continue
        end_i = idx[run[-1]]
        after = [all_dates[i] for i in range(end_i + 1, min(len(all_dates), end_i + 6))]
        after_ratios = [ratios[d] for d in after if d in ratios]
        if not after_ratios or abs(sum(after_ratios) / len(after_ratios) - 1) > 0.05:
            continue
        if overlaps_corporate_action_window(run, ca_dates):
            continue
        out.append({
            "stock_code": code,
            "run_dates": run,
            "median_ratio": med,
            "prior_matching_days": prior_run,
            "after_ratio_sample": after_ratios,
            "days": [
                {
                    "date": d,
                    "ph_open": ph[d][0], "ph_high": ph[d][1], "ph_low": ph[d][2],
                    "ph_close": ph[d][3], "ph_volume": ph[d][4],
                    "nv_open": nv[d][0], "nv_high": nv[d][1], "nv_low": nv[d][2],
                    "nv_close": nv[d][3], "nv_volume": nv[d][4],
                }
                for d in run
            ],
        })
    return out


def main() -> None:
    conn = connect_primary_db(timeout=180)
    conn.execute("SET default_transaction_read_only=on")
    conn.execute("SET statement_timeout='170s'")

    codes = sorted(p.stem.replace(".json", "") for p in SNAP_DIR.glob("*.json.gz"))
    print(f"scanning {len(codes)} stocks with cached naver snapshots", flush=True)

    ca_rows = conn.execute(
        "SELECT stock_code, event_date::text FROM corporate_action_events"
    ).fetchall()
    ca_by_code: dict[str, set[str]] = defaultdict(set)
    for code, ev_date in ca_rows:
        ca_by_code[code].add(ev_date)
    # corporate_action_events is incomplete by design while events await
    # normalization. DART notices are an additional veto, never positive proof.
    disclosure_rows = conn.execute(
        "SELECT stock_code, CAST(rcept_dt AS TEXT), report_nm FROM dart_disclosures"
    ).fetchall()
    for code, raw_date, report_name in disclosure_rows:
        if not any(term in str(report_name or "") for term in PRICE_ADJUSTING_REPORT_TERMS):
            continue
        compact = str(raw_date or "").replace("-", "")[:8]
        if len(compact) == 8 and compact.isdigit():
            ca_by_code[code].add(f"{compact[:4]}-{compact[4:6]}-{compact[6:]}")

    all_candidates = []
    batch_size = 200
    for start in range(0, len(codes), batch_size):
        batch = codes[start:start + batch_size]
        placeholders = ",".join(["%s"] * len(batch))
        rows = conn.execute(
            f"""SELECT stock_code, date::text, open, high, low, close, volume
                FROM price_history WHERE stock_code IN ({placeholders}) AND close IS NOT NULL""",
            tuple(batch),
        ).fetchall()
        ph_by_code: dict[str, dict] = defaultdict(dict)
        for code, d, o, h, l, c, v in rows:
            ph_by_code[code][d] = (o, h, l, c, v)

        for code in batch:
            snap_path = SNAP_DIR / f"{code}.json.gz"
            if not snap_path.exists():
                continue
            nv_rows = json.loads(gzip.decompress(snap_path.read_bytes()))
            nv = {r[1]: (r[2], r[3], r[4], r[5], r[6]) for r in nv_rows}
            ph = ph_by_code.get(code, {})
            if not ph:
                continue
            cands = find_candidates_for_stock(code, ph, nv, ca_by_code.get(code, set()))
            all_candidates.extend(cands)
        print(f"  processed {min(start + batch_size, len(codes))}/{len(codes)} - "
              f"candidates so far: {len(all_candidates)}", flush=True)

    # Preserve every run. The original implementation overwrote its 471-episode
    # pre-repair evidence with a 239-episode post-repair scan.
    out_file = OUT_DIR / f"recurring_splice_candidates_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(all_candidates, ensure_ascii=False, indent=None))
    total_rows = sum(len(c["days"]) for c in all_candidates)
    print(json.dumps({
        "stocks_scanned": len(codes),
        "candidate_episodes": len(all_candidates),
        "candidate_stocks": len(set(c["stock_code"] for c in all_candidates)),
        "candidate_rows": total_rows,
        "output": str(out_file),
    }, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
