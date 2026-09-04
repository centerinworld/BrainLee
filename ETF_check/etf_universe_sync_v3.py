"""Correct the ETF universe to include KRX alphanumeric short codes."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime

import requests

from direct_etf_pipeline import ETFMeta,MASTER_URL,NAMES,WIDTHS
from etf_universe_sync import initialize


SOURCE_VERSION="KIS_MASTER_ALNUM_V2"


def fetch_complete_universe() -> list[ETFMeta]:
    response=requests.get(MASTER_URL,timeout=30); response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        raw=archive.read(archive.namelist()[0])
    result=[]; tail_size=sum(WIDTHS)
    for line in raw.splitlines():
        if len(line)<=tail_size+21: continue
        head,tail,offset,fields=line[:-tail_size],line[-tail_size:],0,{}
        for name,width in zip(NAMES,WIDTHS):
            fields[name]=tail[offset:offset+width].decode("cp949",errors="ignore").strip(); offset+=width
        ticker=head[:9].decode("cp949",errors="ignore").strip()[-6:]
        if fields["group"]!="EF" or not re.fullmatch(r"[0-9A-Z]{6}",ticker): continue
        listed_text=fields["listed_shares_thousand"].replace(",","").strip()
        listed=float(listed_text)*1000 if listed_text else None
        result.append(ETFMeta(ticker,head[21:].decode("cp949",errors="ignore").strip(),"KOSPI",head[9:21].decode("cp949",errors="ignore").strip(),fields["listed_date"],listed))
    result.sort(key=lambda row:row.ticker)
    if len(result)<1000 or len({row.ticker for row in result})!=len(result):
        raise RuntimeError(f"Corrected ETF universe validation failed: {len(result)}")
    return result


def _digest(rows:list[ETFMeta])->str:
    text="\n".join(f"{r.ticker}|{r.isin}|{r.name}" for r in rows)
    return hashlib.sha256(text.encode()).hexdigest()


def get_or_sync_universe(conn:sqlite3.Connection,day:str)->dict:
    initialize(conn)
    existing=conn.execute(
        "SELECT COUNT(*),MIN(source) FROM etf_universe_daily WHERE base_date=?",(day,)
    ).fetchone()
    if existing[0] and existing[1]==SOURCE_VERSION:
        return {"base_date":day,"count":existing[0],"reused":True,"source":SOURCE_VERSION}
    rows=fetch_complete_universe(); current={row.ticker for row in rows}
    previous={row[0] for row in conn.execute("SELECT etf_ticker FROM etf_meta WHERE is_active=1")}
    added=sorted(current-previous); removed=sorted(previous-current)
    correction=(previous and previous<current and not removed and all(not code.isdigit() for code in added))
    ratio=(len(added)+len(removed))/max(len(previous),1)
    if previous and ratio>0.10 and not correction:
        raise RuntimeError(f"ETF universe changed abnormally: {len(previous)} -> {len(rows)}")
    now=datetime.now().isoformat(timespec="seconds"); digest=_digest(rows)
    details={"added":added,"removed":removed,"change_ratio":ratio,"migration":"alphanumeric_ticker_correction" if correction else None,"source":SOURCE_VERSION}
    with conn:
        conn.execute("DELETE FROM etf_universe_daily WHERE base_date=?",(day,))
        conn.executemany(
            "INSERT INTO etf_universe_daily VALUES(?,?,?,?,?,?,?,?,?)",
            [(day,r.ticker,r.name,r.market,r.isin,r.listed_date,r.listed_shares,SOURCE_VERSION,now) for r in rows],
        )
        conn.execute("UPDATE etf_meta SET is_active=0")
        conn.executemany(
            """
            INSERT INTO etf_meta(etf_ticker,etf_name,market,isin,listed_date,listed_shares,universe_source,is_active,updated_at)
            VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(etf_ticker) DO UPDATE SET
              etf_name=excluded.etf_name,market=excluded.market,isin=excluded.isin,
              listed_date=excluded.listed_date,listed_shares=excluded.listed_shares,
              universe_source=excluded.universe_source,is_active=1,updated_at=excluded.updated_at
            """,
            [(r.ticker,r.name,r.market,r.isin,r.listed_date,r.listed_shares,SOURCE_VERSION,now) for r in rows],
        )
        conn.execute(
            """
            INSERT INTO etf_universe_sync_run(base_date,status,previous_count,current_count,added_count,removed_count,universe_hash,details_json,collected_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (day,"corrected_complete",len(previous),len(rows),len(added),len(removed),digest,json.dumps(details,ensure_ascii=False),now),
        )
        conn.execute("UPDATE etf_source_control SET consecutive_pass_days=0,mode='legacy_validation',last_failure='universe_alphanumeric_correction',updated_at=? WHERE control_id=1",(now,))
    return {"base_date":day,"count":len(rows),"previous_count":len(previous),"added":added,"removed":removed,"universe_hash":digest,"source":SOURCE_VERSION,"correction":correction}


def dated_universe(conn:sqlite3.Connection,day:str):
    rows=conn.execute("SELECT etf_ticker,etf_name,isin FROM etf_universe_daily WHERE base_date=? AND source=? ORDER BY etf_ticker",(day,SOURCE_VERSION)).fetchall()
    if not rows: raise RuntimeError(f"No corrected ETF universe for {day}")
    return [(row[0],row[1],row[2]) for row in rows]
