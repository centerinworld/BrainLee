#!/usr/bin/env python3
"""Fill coverage_gap events where marcap (KRX-sourced) supplies EVERY missing trading day, including long gaps
(>30 days) that fill_coverage_gaps_20260924.py skipped. Those days are trading suspensions: marcap carries the last
close with O=H=L=0 and volume=0 — the marker shape `price_integrity.invalid_ohlcv()` blesses ("supplied suspension
marker, never a tradable candle"). NOTE: `price_history` has no `quality_status` column; the canonical read view
`price_history_quality_v` derives `quality_status='suspended'` for rows with `volume=0 AND high=0 AND low=0`, which is
what keeps those rows out of return calculations.

GATE (fail-closed, per row — 2026-09-24 revision)
-------------------------------------------------
A long gap mixes two row kinds and only one of them may be inserted on trust:

* **suspension marker** (`volume=0 and open=high=low=0`, `close>0`) - marcap carries the last close through a halt.
  Allowed by default (unchanged). The canonical view `price_history_quality_v` classifies inserted rows as
  `quality_status='suspended'` (from the OHLCV pattern - `price_history` itself stores no such column), so they never
  reach return calculations. A marker whose close does not carry the previous available close forward is still ALLOWED
  but queued as an advisory (`carry_forward_ratio`), because a large deviation is a marcap-basis-splice fingerprint a
  human should see. That advisory (over `MARKER_CARRY_FORWARD_ADVISORY`) is a carry-forward heuristic, NOT the price
  limit - it never blocks an insert.
* **trading row** (anything else, incl. `volume>0` and zero-volume limit-locked quotes) - a real quote. A long gap can
  also be a marcap *segment splice*: two segments of the same code stored on different bases (008800 2016-03-04 x0.13
  and 2018-04-20 x14.35 are mutual inverses - a single day cannot move that far, and no disclosure exists for it).
  Such a row is inserted ONLY when one of these holds:
    (A) every step it forms with a neighbour in the reconstructed series is a legal one-day move under the
        DATE-SENSITIVE KRX price limit (`price_integrity.price_band`: ±15% before 2015-06-15, ±30% on/after).
        `fdr_band_ok` applies it per direction - the step to the previous close is limited by the candidate
        day's band, the step to the next close by the NEXT trading day's band (inverted), so an ordinary
        limit-down is legitimate and a segment splice is not, or
    (B) `corporate_action_events` holds a `factor_confirmed` row within +/-25 days whose `backward_price_factor`
        (or its inverse) reproduces the observed close/previous-close ratio inside one day's limit, or
    (C) DART published the ex-rights notice (`report_nm LIKE '%권리락%'`, -5..+3 days) AND a price-adjusting capital
        action is registered in `corporate_action_events` within +/-25 days.
  Anything else is REFUSED, and because refusing one row would leave the surrounding markers carrying a close we would
  not insert as a price, the whole EVENT is held back (`event_blocked=1`). Nothing unverified is written.
  A refusal is never a judgement call left implicit: refused rows, blocked events, advisory markers,
  provenance-invalid rows (fractional/NaN/OHLC-shape) and refused rows that are ALREADY in price_history (i.e. written
  by a pre-gate run - the rollback candidates) all go to the queue CSV
  (`<runtime>/.verification/suspension_gap_gate_queue.csv` by default), which is the human review list.

Inserted with app.price_basis_checked=1, logged in price_history_fix_backup (old_*=NULL; rollback = delete rows of
that run_id). `--apply` to write; the default is a read-only dry run that still writes the queue CSV.
"""
from __future__ import annotations

import bisect
import importlib.util
import json
import math
import sys
from collections import Counter
from datetime import date as _date
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from marcap_client import ensure_year  # noqa: E402

_spec = importlib.util.spec_from_file_location("fill_coverage_gaps", Path(__file__).with_name("fill_coverage_gaps_20260924.py"))
_mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_mod)
valid_rows = _mod.valid_rows
gap_days = _mod.gap_days
fdr_band_ok = _mod.fdr_band_ok
price_band = _mod.price_band          # the ONE date-sensitive KRX limit policy (price_integrity), not a copy

# ── gate constants ────────────────────────────────────────────────────────────────────────────────────────────
CA_SETTLE_WINDOW_DAYS = 25                # corporate_action_events may be registered weeks after the ex-date
EX_RIGHTS_WINDOW = (-5, 3)                # DART 권리락 notice lands 1 day before the ex-date in practice
MARKER_CARRY_FORWARD_ADVISORY = 0.10      # marker close vs previous available close; beyond this = advisory (allowed)
PRICE_ADJUSTING_EVENT_TYPES = frozenset({
    "bonus_issue", "rights_issue", "rights_or_other_issue", "share_increase_unclassified",
    "split", "reverse_split", "share_consolidation", "capital_reduction", "merger",
})
QUEUE_PATH = ROOT / ".verification" / "suspension_gap_gate_queue.csv"

# SQL below must stay pure ASCII: psycopg's parameter splitter decodes the query with the client encoding, so a
# multibyte literal (Korean) in a query that also carries bind parameters raises UnicodeDecodeError. Korean match
# patterns are therefore passed as *parameters*.
CA_SQL = ("SELECT stock_code,event_date,event_type,adjustment_status,backward_price_factor,evidence_report_name "
          "FROM corporate_action_events ORDER BY stock_code,event_date")
EX_RIGHTS_SQL = ("SELECT stock_code,replace(rcept_dt,'-',''),report_nm FROM dart_disclosures "
                 "WHERE report_nm LIKE ? ORDER BY stock_code,rcept_dt")
EX_RIGHTS_PATTERN = "%권리락%"
EXISTING_SQL = "SELECT date FROM price_history WHERE stock_code=? AND date>? AND date<?"
POINT_CLOSE_SQL = "SELECT close FROM price_history WHERE stock_code=? AND date=?"

QUEUE_COLUMNS = ["decision", "evidence", "advisory", "code", "date", "previous_date", "event_date", "already_present",
                 "event_blocked", "open", "high", "low", "close", "volume", "prev_neighbor", "next_neighbor",
                 "ratio_prev", "ratio_next", "carry_forward_ratio", "row_kind"]


# ── pure gate ─────────────────────────────────────────────────────────────────────────────────────────────────
def _positive(value) -> float | None:
    """Float that can act as a price anchor, else None. NaN never anchors anything (PG NaN > every real number)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _day_gap(left: str, right: str) -> int | None:
    """Signed day distance right -> left, tolerant of YYYYMMDD / YYYY-MM-DD mixing. None when unparsable."""
    try:
        a = _date.fromisoformat(str(left)[:10].replace("/", "-") if "-" in str(left) else
                                f"{str(left)[:4]}-{str(left)[4:6]}-{str(left)[6:8]}")
        b = _date.fromisoformat(str(right)[:10] if "-" in str(right) else
                                f"{str(right)[:4]}-{str(right)[4:6]}-{str(right)[6:8]}")
    except (TypeError, ValueError):
        return None
    return (a - b).days


def is_suspension_marker(row) -> bool:
    """The DB's suspension convention: O=H=L=0 with volume=0 and close>0 (carried)."""
    return (float(row["volume"]) == 0 and float(row["open"]) == 0
            and float(row["high"]) == 0 and float(row["low"]) == 0)


def row_kind(row) -> str:
    return "suspension_marker" if is_suspension_marker(row) else "trading_row"


def _ca_fields(raw) -> tuple[str, str | None, str | None, float | None, str | None]:
    values = (list(raw) + [None] * 5)[:5]
    return (str(values[0])[:10], values[1], values[2], _positive(values[3]), values[4])


def match_corporate_action(code, date_str, close, prev_close, ca_events=(), ex_rights=()) -> str | None:
    """Evidence string when a *confirmed* corporate action explains an out-of-band move, else None.

    Evidence classes (fail-closed: no evidence, no insert):
      (B) factor_confirmed backward_price_factor - or its inverse - reproduces the observed ratio inside one
          day's price limit, dated within +/-25 days of the row. Mirrors the audit's ratio-match rule
          (scripts/audit_price_jumps_and_build_canonical.py) with a residual window of one KRX limit instead of
          a fixed 5%, because the ex-date quote also carries that day's market move.
      (C) a DART ex-rights notice (권리락) within EX_RIGHTS_WINDOW days AND a price-adjusting capital action
          registered for the code within +/-25 days. The disclosure alone is context, not authority.
    """
    ratio_close, ratio_prev = _positive(close), _positive(prev_close)
    if ratio_close is None or ratio_prev is None:
        return None
    ratio = ratio_close / ratio_prev

    best = None
    for raw in ca_events:
        ev_date, etype, status, bpf, report = _ca_fields(raw)
        if status != "factor_confirmed" or bpf is None:
            continue
        gap = _day_gap(ev_date, date_str)
        if gap is None or abs(gap) > CA_SETTLE_WINDOW_DAYS:
            continue
        for candidate in (bpf, 1.0 / bpf):
            band_lo, band_hi = price_band(date_str)      # the limit in force ON the ex-date, not a global ±30%
            if band_lo - 1e-9 <= ratio / candidate <= band_hi + 1e-9:
                detail = (f"factor_confirmed {etype} {ev_date} bpf={bpf:g} reproduces ratio {ratio:.4f}"
                          + (f" ({report})" if report else "") + f" [{gap:+d}d]")
                if best is None or abs(gap) < best[0]:
                    best = (abs(gap), detail)
                break
    if best is not None:
        return best[1]

    notice = None
    for raw in ex_rights:
        values = (list(raw) + [None, None])[:2]
        rcept, report = values[0], str(values[1] or "")
        if "권리락" not in report:
            continue
        gap = _day_gap(rcept, date_str)
        if gap is None or not (EX_RIGHTS_WINDOW[0] <= gap <= EX_RIGHTS_WINDOW[1]):
            continue
        notice = (gap, str(rcept), report)
        break
    if notice is None:
        return None
    for raw in ca_events:
        ev_date, etype, status, _bpf, _report = _ca_fields(raw)
        if etype not in PRICE_ADJUSTING_EVENT_TYPES:
            continue
        gap = _day_gap(ev_date, date_str)
        if gap is not None and abs(gap) <= CA_SETTLE_WINDOW_DAYS:
            return (f"DART '{notice[2]}' {notice[1]} ({notice[0]:+d}d) + registered {etype} {ev_date} "
                    f"[{status}]")
    return None


def gate_event(rows: pd.DataFrame, prev_anchor, next_anchor, ca_events=(), ex_rights=(),
               *, next_anchor_date) -> pd.DataFrame:
    """Attach a per-row gate decision to one event's reconstructed interior series.

    ``rows`` = marcap rows for the days strictly between previous_date and event_date (any order). Neighbours are the
    previous/next close *in the series this run would create* (earlier/later row, else the previous_date/event_date
    close from price_history). A trading row is accepted on band (A) or corporate-action evidence (B/C); a suspension
    marker is accepted by convention. Nothing here writes or guesses: a marker keeps the source close verbatim and a
    refusal never falls back to an interpolated value.

    ``next_anchor_date`` is the date of the closing next-anchor row (= the audit's event_date) and is REQUIRED, because
    the price limit is date-sensitive: the step to the next close is judged by the NEXT trading day's band
    (`price_integrity.price_band`), which for the last interior row is the event date's band. An interior row's next
    neighbour is the following row, whose date is already in the frame. The previous side never needs the previous
    anchor's date: the step C[day]/C[day-1] is limited by the candidate day's own band.
    """
    out = rows.sort_values("date").reset_index(drop=True).copy()
    if out.empty:
        for col in ("prev_neighbor", "next_neighbor", "ratio_prev", "ratio_next", "carry_forward_ratio",
                    "band", "band_day", "next_band_day", "decision", "evidence", "advisory", "row_kind"):
            out[col] = []
        return out

    closes = [_positive(v) for v in out["close"]]
    prevs = [closes[i - 1] if i else _positive(prev_anchor) for i in range(len(out))]
    nexts = [closes[i + 1] if i < len(out) - 1 else _positive(next_anchor) for i in range(len(out))]
    days = [str(v)[:10] for v in out["date"]]
    next_days = [days[i + 1] if i < len(out) - 1 else str(next_anchor_date)[:10] for i in range(len(out))]

    decisions, evidences, advisories, kinds = [], [], [], []
    for (_, row), prev_close, next_close, day, next_day in zip(out.iterrows(), prevs, nexts, days, next_days):
        close = float(row["close"])
        kind = row_kind(row)
        kinds.append(kind)
        if kind == "suspension_marker":
            carry = close / prev_close if prev_close else None
            advisory = ""
            if carry is None:
                advisory = "marker carry-forward unverifiable (no previous anchor)"
            elif abs(carry - 1.0) > MARKER_CARRY_FORWARD_ADVISORY:
                advisory = f"marker close {close:g} is {carry:.4f}x the previous available close {prev_close:g}"
            decisions.append("suspension_marker")
            evidences.append("suspension marker (O=H=L=0, volume=0): carried close, no trade")
            advisories.append(advisory)
            continue

        # A trading row needs BOTH neighbours: an anchor-less edge cannot be verified, so it is refused (fail-closed)
        if prev_close is None or next_close is None:
            decisions.append("rejected_unverified_basis")
            evidences.append("no previous/next close anchor in or around the gap - basis cannot be verified")
            advisories.append("")
            continue
        if fdr_band_ok(close, prev_close, next_close, day, next_day=next_day):
            band_lo, band_hi = price_band(day)
            decisions.append("band_ok")
            evidences.append(f"legal one-day steps under the {band_lo:g}..{band_hi:g} limit of {day} "
                             f"(prev {prev_close:g}, next {next_close:g} under {next_day}'s band)")
            advisories.append("")
            continue
        evidence = match_corporate_action(row["code"], str(row["date"]), close, prev_close, ca_events, ex_rights)
        if evidence:
            decisions.append("corporate_action_confirmed")
            evidences.append(evidence)
            advisories.append("")
        else:
            band_lo, band_hi = price_band(day)
            next_lo, next_hi = price_band(next_day)
            decisions.append("rejected_unverified_basis")
            evidences.append(f"out of band (close {close:g} vs prev {prev_close:g} x{close / prev_close:.4f}, "
                             f"vs next {next_close:g} x{close / next_close:.4f}; allowed {band_lo:g}..{band_hi:g} "
                             f"on {day} and inverse of {next_lo:g}..{next_hi:g} on {next_day}) and no confirmed "
                             f"corporate-action evidence - possible marcap segment splice")
            advisories.append("")

    out["row_kind"] = kinds
    out["prev_neighbor"] = prevs
    out["next_neighbor"] = nexts
    out["ratio_prev"] = [round(float(r["close"]) / p, 6) if p else None for (_, r), p in zip(out.iterrows(), prevs)]
    out["ratio_next"] = [round(float(r["close"]) / n, 6) if n else None for (_, r), n in zip(out.iterrows(), nexts)]
    out["carry_forward_ratio"] = [round(float(r["close"]) / p, 6) if (p and row_kind(r) == "suspension_marker")
                                  else None for (_, r), p in zip(out.iterrows(), prevs)]
    out["band_day"] = days
    out["next_band_day"] = next_days
    out["band"] = [f"{price_band(d)[0]:g}..{price_band(d)[1]:g}" for d in days]
    out["decision"] = decisions
    out["evidence"] = evidences
    out["advisory"] = advisories
    return out


def event_blocked(gated: pd.DataFrame) -> bool:
    """One refused row poisons the whole event: the surrounding markers would carry a close we refuse as a price."""
    return bool((gated["decision"] == "rejected_unverified_basis").any())


def invalid_reason(row) -> str | None:
    """Why `valid_rows` would drop this provenance row, or None when it survives. Mirrors valid_rows' predicates."""
    reasons = []
    ohlc = [float(row[c]) for c in ("open", "high", "low", "close")]
    if not all(math.isfinite(v) and v == round(v) for v in ohlc) or not math.isfinite(float(row["volume"])):
        reasons.append("fractional_or_nan_price(adjusted_basis)")
    if not math.isfinite(float(row["close"])) or float(row["close"]) <= 0:
        reasons.append("nonpositive_close")
    if float(row["volume"]) < 0:
        reasons.append("negative_volume")
    marker = is_suspension_marker(row)
    shape_ok = (float(row["open"]) > 0 and float(row["low"]) > 0
                and float(row["high"]) >= max(float(row["open"]), float(row["low"]), float(row["close"]))
                and float(row["low"]) <= min(float(row["open"]), float(row["high"]), float(row["close"])))
    if not marker and not shape_ok:
        reasons.append("ohlc_shape_violation")
    return "; ".join(reasons) if reasons else None


# ── DB helpers ────────────────────────────────────────────────────────────────────────────────────────────────
def _point_close(conn, code: str, day: str) -> float | None:
    row = conn.execute(POINT_CLOSE_SQL, (code, day)).fetchone()
    return _positive(row[0]) if row else None


def load_corporate_action_evidence(conn) -> tuple[dict, dict]:
    """Bulk-load the two evidence sources once per run, keyed by stock code.

    Both are small tables (corporate_action_events ~11.7k rows; DART ex-rights notices a few thousand), so one scan
    beats a per-event query. A lookup failure yields empty evidence, and no evidence means no insert (fail-closed).
    """
    ca: dict[str, list] = {}
    try:
        for row in conn.execute(CA_SQL).fetchall():
            values = list(row)
            ca.setdefault(str(values[0]), []).append(tuple(values[1:]))
    except Exception as exc:  # noqa: BLE001 - fail closed, and say so
        print(f"[gate] corporate_action_events unavailable: {repr(exc)[:120]}", flush=True)
    ex_rights: dict[str, list] = {}
    try:
        for row in conn.execute(EX_RIGHTS_SQL, (EX_RIGHTS_PATTERN,)).fetchall():
            values = list(row)
            ex_rights.setdefault(str(values[0]), []).append(tuple(values[1:]))
    except Exception as exc:  # noqa: BLE001
        print(f"[gate] dart_disclosures unavailable: {repr(exc)[:120]}", flush=True)
    return ca, ex_rights


def write_queue(path: Path, rows: list[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=QUEUE_COLUMNS)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ── run ───────────────────────────────────────────────────────────────────────────────────────────────────────
def main(apply: bool, queue_path: Path | None = None) -> dict:
    conn = connect_primary_db(timeout=900)
    ev = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,event_date,previous_date FROM price_jump_audit WHERE classification='coverage_gap'").fetchall()],
        columns=["code", "d", "p"])
    cal = [r[0] for r in conn.execute("SELECT date FROM price_trading_calendar ORDER BY date").fetchall()]
    m = pd.concat([pd.read_parquet(ensure_year(y), columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
                   for y in range(2010, 2027)], ignore_index=True)
    m["Date"] = m["Date"].astype(str).str[:10]
    m = m.rename(columns={"Code": "code", "Date": "date", "Open": "open", "High": "high", "Low": "low",
                          "Close": "close", "Volume": "volume"}).dropna()
    have = set(zip(m.code, m.date))
    by_code = {c: g for c, g in m.groupby("code")}
    ca_by_code, ex_rights_by_code = load_corporate_action_evidence(conn)

    queue: list[dict] = []
    insertable: list = []          # rows of CLEAN events that are still missing from price_history
    counts: Counter = Counter()
    covered_events = blocked_events = 0

    def queue_row(row, decision, evidence, advisory, prev_date, event_date, present, blocked, kind=None) -> dict:
        return {"decision": decision, "evidence": evidence, "advisory": advisory,
                "code": row["code"], "date": row["date"], "previous_date": prev_date, "event_date": event_date,
                "already_present": int(present), "event_blocked": int(blocked),
                "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
                "volume": row["volume"], "prev_neighbor": row.get("prev_neighbor"),
                "next_neighbor": row.get("next_neighbor"), "ratio_prev": row.get("ratio_prev"),
                "ratio_next": row.get("ratio_next"), "carry_forward_ratio": row.get("carry_forward_ratio"),
                "row_kind": kind if kind is not None else row.get("row_kind")}

    for code, event_date, prev_date in zip(ev.code, ev.d, ev.p):
        days = gap_days(cal, prev_date, event_date)
        if not days or not all((code, x) in have for x in days):
            continue
        covered_events += 1
        candidates = by_code[code]
        candidates = candidates[candidates.date.isin(set(days))].sort_values("date")
        accepted = valid_rows(candidates)
        invalid = candidates[~candidates.index.isin(accepted.index)]
        for _, bad in invalid.iterrows():
            counts["invalid_provenance_rows"] += 1
            reason = invalid_reason(bad) or "rejected by valid_rows"
            queue.append(queue_row(bad, "rejected_invalid_row", reason, "", prev_date, event_date, False, True))

        present = {r[0] for r in conn.execute(EXISTING_SQL, (code, prev_date, event_date)).fetchall()}
        gated = gate_event(accepted[["code", "date", "open", "high", "low", "close", "volume"]],
                           _point_close(conn, code, prev_date), _point_close(conn, code, event_date),
                           ca_by_code.get(code, ()), ex_rights_by_code.get(code, ()),
                           next_anchor_date=event_date)
        blocked = event_blocked(gated)
        if blocked:
            blocked_events += 1
        for _, row in gated.iterrows():
            counts[row["decision"]] += 1
            if row["decision"] == "rejected_unverified_basis":
                counts["rejected_missing_from_db"] += int(row["date"] not in present)
                counts["rejected_already_present"] += int(row["date"] in present)
            if row["row_kind"] == "suspension_marker" and row["advisory"]:
                counts["marker_carry_forward_advisories"] += 1
            needs_review = (blocked or row["decision"] == "rejected_unverified_basis" or bool(row["advisory"]))
            if needs_review:
                queue.append(queue_row(row, row["decision"], row["evidence"], row["advisory"], prev_date, event_date,
                                       row["date"] in present, blocked))
            if blocked or row["decision"] == "rejected_unverified_basis" or row["date"] in present:
                continue
            insertable.append(row)

    ins = (pd.DataFrame([r.to_dict() for r in insertable]) if insertable
           else pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume"]))
    qpath = write_queue(Path(queue_path) if queue_path else QUEUE_PATH, queue)
    summary = {
        "events_fully_covered": covered_events,
        "events_blocked_for_review": blocked_events,
        "rows_insertable": len(ins),
        "insertable_suspension_markers": int((ins["row_kind"] == "suspension_marker").sum()) if len(ins) else 0,
        "insertable_trading_rows": int((ins["row_kind"] == "trading_row").sum()) if len(ins) else 0,
        "band_ok": counts["band_ok"],
        "corporate_action_confirmed": counts["corporate_action_confirmed"],
        "suspension_markers": counts["suspension_marker"],
        "rejected_unverified_basis": counts["rejected_unverified_basis"],
        "rejected_already_present": counts["rejected_already_present"],
        "invalid_provenance_rows": counts["invalid_provenance_rows"],
        "marker_carry_forward_advisories": counts["marker_carry_forward_advisories"],
        "queue_rows": len(queue),
        "queue_csv": str(qpath),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not apply or ins.empty:
        return summary

    run_id = f"suspension_gap_fill_{datetime.now():%Y%m%d_%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")
    reason = ("coverage_gap long gap filled with gate-verified marcap rows (suspension markers, or trading rows "
              "inside the +/-30% band of both neighbours / explained by a confirmed corporate action)")
    conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
    conn.executemany(
        "INSERT INTO price_history(stock_code,date,open,high,low,close,volume,created_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT (stock_code,date) DO NOTHING",
        [(r.code, r.date, r.open, r.high, r.low, r.close, r.volume, now) for r in ins.itertuples()])
    conn.executemany(
        """INSERT INTO price_history_fix_backup (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
           new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES(?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?)
           ON CONFLICT DO NOTHING""",
        [(run_id, r.code, r.date, r.open, r.high, r.low, r.close, r.volume, reason, now) for r in ins.itertuples()])
    conn.execute(
        """INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
             new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
        (now, "price_history", "suspension gap fill (marcap, gate-verified)", len(ins),
         "INSERT missing suspension days and band/corporate-action verified trading days", "row absent",
         "marcap gate-verified rows", reason, run_id))
    conn.commit()
    print(json.dumps({"run_id": run_id}, ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    main("--apply" in sys.argv)
