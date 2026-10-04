#!/usr/bin/env python3
"""Register corporate_action_events for unresolved price jumps that are real market events.

Population: price_jump_audit rows classified unresolved_active_common whose event/previous closes both equal
marcap raw closes (so the jump is what actually traded, not a data glitch). Evidence: DART disclosures for that
stock in [-300d, +10d] of the jump - the local dart_disclosures table first, then the live OpenDART list API
(local coverage starts 2016-05) using the corp_code map from corpCode.xml. Keyword -> event_type:
  회사분할/분할결정/분할합병 -> company_split      주식분할/액면분할 -> stock_split
  주식병합/액면병합/병합결정 -> reverse_split       감자 -> capital_reduction
  합병 -> merger   주식교환/주식이전 -> share_exchange   거래정지/정지해제/재상장/변경상장 -> trading_halt_resumption
An event without any such disclosure is NOT registered (stays unresolved: no evidence, no guessing).
Rows are inserted with adjustment_status='review_required' (halts: 'not_price_adjusting') and no
backward_price_factor - the audit then labels them corporate_action_pending_confirmation (return_usable stays 0)
instead of unresolved. Rollback: DELETE FROM corporate_action_events WHERE source='dart_disclosure+marcap_jump_2026-09-24'.
--apply to write.
"""
from __future__ import annotations

import io
import os
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from marcap_client import ensure_year, _share_series  # noqa: E402

try:
    from dotenv import load_dotenv
    for p in (ROOT / ".env", ROOT / "env"):
        if p.exists():
            load_dotenv(p)
except Exception:  # noqa: BLE001
    pass

SOURCE = "dart_disclosure+marcap_jump_2026-09-24"
KEYS = [k for k in (os.getenv("DART_API_KEY"), os.getenv("DART_API_KEY2"), os.getenv("DART_API_KEY3"), os.getenv("DART_API_KEY4")) if k]
RULES = [  # priority order
    ("company_split", ("회사분할", "분할결정", "분할합병", "분할 합병")),
    ("reverse_split", ("주식병합", "액면병합", "병합결정")),
    ("stock_split", ("주식분할", "액면분할")),
    ("capital_reduction", ("감자",)),
    ("merger", ("합병",)),
    ("share_exchange", ("주식교환", "주식이전", "포괄적")),
    ("trading_halt_resumption", ("거래정지", "정지해제", "재상장", "변경상장", "상장폐지")),
]
LIKE = ["%분할%", "%병합%", "%감자%", "%합병%", "%거래정지%", "%액면%", "%주식교환%", "%주식이전%", "%재상장%", "%변경상장%", "%상장폐지%"]


def classify(report_nm: str) -> str | None:
    for etype, words in RULES:
        if any(w in report_nm for w in words):
            return etype
    return None


def corp_code_map() -> dict:
    cache = ROOT / "data_cache" / "dart_corp_code.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
    else:
        r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": KEYS[0]}, timeout=60)
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(r.content)).read("CORPCODE.xml"))
        rows = [(e.findtext("stock_code", "").strip(), e.findtext("corp_code", "").strip()) for e in root.iter("list")]
        df = pd.DataFrame([x for x in rows if x[0]], columns=["stock_code", "corp_code"])
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache)
    return dict(zip(df.stock_code, df.corp_code))


def live_disclosures(corp: str, lo: str, hi: str, state: dict) -> list[tuple]:
    out = []
    for ty in ("B", "I"):
        page = 1
        while True:
            key = KEYS[state["k"] % len(KEYS)]
            r = requests.get("https://opendart.fss.or.kr/api/list.json", timeout=30, params={
                "crtfc_key": key, "corp_code": corp, "bgn_de": lo, "end_de": hi, "pblntf_ty": ty,
                "page_no": page, "page_count": 100}).json()
            state["calls"] += 1
            if r.get("status") == "020":  # quota: rotate key
                state["k"] += 1
                if state["k"] >= len(KEYS) * 2:
                    raise RuntimeError("DART quota exhausted")
                continue
            if r.get("status") != "000":
                break
            out += [(x["report_nm"], x["rcept_dt"], x["rcept_no"]) for x in r.get("list", [])]
            if page >= int(r.get("total_page", 1)):
                break
            page += 1
        time.sleep(0.05)
    return out


def main(apply: bool) -> None:
    conn = connect_primary_db(timeout=900)
    u = pd.DataFrame([tuple(r) for r in conn.execute(
        """SELECT stock_code,event_date,previous_date,previous_close,event_close,price_ratio
           FROM price_jump_audit WHERE classification='unresolved_active_common'""").fetchall()],
        columns=["code", "d", "pd", "pc", "ec", "ratio"])
    m = pd.concat([pd.read_parquet(ensure_year(y), columns=["Code", "Date", "Close"]) for y in range(2010, 2027)])
    m["Date"] = m["Date"].astype(str).str[:10]
    u = u.merge(m.rename(columns={"Close": "mec"}), left_on=["code", "d"], right_on=["Code", "Date"]) \
         .merge(m.rename(columns={"Close": "mpc", "Date": "pd2"})[["Code", "pd2", "mpc"]],
                left_on=["code", "pd"], right_on=["Code", "pd2"])
    u = u[(u.mec == u.ec) & (u.mpc == u.pc)].reset_index(drop=True)
    print({"real_jumps": len(u)}, flush=True)

    cc = corp_code_map() if KEYS else {}
    ss = _share_series()
    cond = " OR ".join(["report_nm LIKE ?"] * len(LIKE))
    state = {"k": 0, "calls": 0}
    have = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,event_date FROM corporate_action_events").fetchall()}
    new_rows, stats = [], {"local": 0, "live": 0, "none": 0}
    for i, r in u.iterrows():
        lo = (pd.Timestamp(r.d) - pd.Timedelta(days=300)).strftime("%Y%m%d")
        hi = (pd.Timestamp(r.d) + pd.Timedelta(days=10)).strftime("%Y%m%d")
        disc = [tuple(x) for x in conn.execute(
            f"SELECT report_nm,rcept_dt,rcept_no FROM dart_disclosures WHERE stock_code=? AND rcept_dt BETWEEN ? AND ? AND ({cond}) "
            "ORDER BY rcept_dt DESC", (r.code, lo, hi, *LIKE)).fetchall()]
        src = "local"
        if not [x for x in disc if classify(x[0])] and r.code in cc:
            try:
                disc = live_disclosures(cc[r.code], lo, hi, state)
            except RuntimeError as exc:
                print(str(exc)); break
            src = "live"
        cands = [(classify(x[0]), x) for x in disc if classify(x[0])]
        if not cands:
            stats["none"] += 1
            continue
        order = [t for t, _ in RULES]
        etype, ev = sorted(cands, key=lambda c: (order.index(c[0]), -int(str(c[1][1]).replace('-', ''))))[0]
        stats[src] += 1
        g = ss.get(r.code)
        old_sh = new_sh = ratio = None
        if g is not None:
            b = g[g.Date < (pd.Timestamp(r.d) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")].tail(1)
            a = g[g.Date > (pd.Timestamp(r.d) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")].head(1)
            if not b.empty and not a.empty:
                old_sh, new_sh = float(b.Stocks.iloc[0]), float(a.Stocks.iloc[0])
                ratio = new_sh / old_sh if old_sh else None
        status = "not_price_adjusting" if etype == "trading_halt_resumption" else "review_required"
        now = datetime.now().isoformat(timespec="seconds")
        if (r.code, r.d) in have:
            continue
        new_rows.append((r.code, r.d, etype, old_sh, new_sh, ratio, None, ev[0], ev[2],
                         f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={ev[2]}", SOURCE, 0.7, status,
                         f"DART '{ev[0]}' ({ev[1]}) within -300/+10d of a jump confirmed by marcap raw closes "
                         f"{r.pc:.0f}->{r.ec:.0f} (x{r.ratio:.4f}); price factor not derived", now, now))
    print({"stats": stats, "to_insert": len(new_rows), "dart_calls": state["calls"]}, flush=True)
    if not apply or not new_rows:
        return
    conn.executemany(
        """INSERT INTO corporate_action_events (stock_code,event_date,event_type,old_shares,new_shares,share_ratio,
           backward_price_factor,evidence_report_name,evidence_rcept_no,evidence_url,source,confidence,
           adjustment_status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new_rows)
    conn.execute(
        """INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
             new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
        (datetime.now().isoformat(timespec="seconds"), "corporate_action_events", "unresolved real jumps", len(new_rows),
         "INSERT corporate_action_events from DART disclosure + marcap-confirmed jump", "no event registered",
         "review_required / not_price_adjusting event rows", SOURCE, f"register_corporate_events_{datetime.now():%Y%m%d_%H%M%S}"))
    conn.commit()
    print("inserted", len(new_rows))
    conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
