"""Compare legacy ETF Check with the KRX/KIS pipeline and gate automatic cutover."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

from full_pdf_collector import DB_PATH, connect


REQUIRED_PASS_DAYS = 5
THRESHOLDS = {
    "new_coverage_ratio":0.995,
    "membership_jaccard":0.90,
    "count_within_one_ratio":0.80,
    "amount_correlation":0.95,
    "amount_total_ratio_min":0.85,
    "amount_total_ratio_max":1.15,
    "amount_median_smape_max":0.20,
}


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_source_parity_daily (
            base_date TEXT PRIMARY KEY,
            legacy_date TEXT NOT NULL,
            universe_count INTEGER NOT NULL,
            successful_etf_count INTEGER NOT NULL,
            scale_count INTEGER NOT NULL,
            new_coverage_ratio REAL NOT NULL,
            legacy_positive_count INTEGER NOT NULL,
            direct_positive_count INTEGER NOT NULL,
            overlap_positive_count INTEGER NOT NULL,
            membership_jaccard REAL NOT NULL,
            count_compared_count INTEGER NOT NULL,
            count_exact_ratio REAL NOT NULL,
            count_within_one_ratio REAL NOT NULL,
            amount_compared_count INTEGER NOT NULL,
            amount_correlation REAL,
            amount_total_ratio REAL,
            amount_median_smape REAL,
            passed INTEGER NOT NULL,
            failures_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            audited_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS etf_source_control (
            control_id INTEGER PRIMARY KEY CHECK(control_id=1),
            mode TEXT NOT NULL,
            required_pass_days INTEGER NOT NULL,
            consecutive_pass_days INTEGER NOT NULL,
            validation_started_at TEXT NOT NULL,
            cutover_at TEXT,
            last_evaluated_date TEXT,
            last_failure TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    now=datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT OR IGNORE INTO etf_source_control VALUES(1,'legacy_validation',?,0,?,NULL,NULL,NULL,?)
        """,
        (REQUIRED_PASS_DAYS,now,now),
    )
    conn.commit()


def _correlation(xs:list[float],ys:list[float]) -> float | None:
    if len(xs)<2:
        return None
    mx,my=statistics.mean(xs),statistics.mean(ys)
    numerator=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    dx=math.sqrt(sum((x-mx)**2 for x in xs)); dy=math.sqrt(sum((y-my)**2 for y in ys))
    return numerator/(dx*dy) if dx and dy else None


def evaluate(conn:sqlite3.Connection,day:str|None=None) -> dict:
    initialize(conn)
    selected=day or conn.execute(
        """
        SELECT MAX(s.base_date) FROM etf_pdf_full_snapshot s
        WHERE EXISTS(SELECT 1 FROM etf_scale_daily d WHERE d.base_date=s.base_date)
        """
    ).fetchone()[0]
    if not selected:
        raise RuntimeError("No date has both KRX PDF and ETF scale data")
    legacy_date=conn.execute(
        "SELECT MAX(trade_date) FROM etf_inclusion_daily WHERE trade_date<=? AND scope_label='K-ETF'",
        (selected,),
    ).fetchone()[0]
    if not legacy_date:
        raise RuntimeError(f"No legacy K-ETF snapshot on or before {selected}")
    universe=conn.execute(
        "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?",(selected,)
    ).fetchone()[0]
    successful=conn.execute(
        "SELECT COUNT(*) FROM etf_pdf_full_snapshot WHERE base_date=? AND status='success'",(selected,)
    ).fetchone()[0]
    scale_count=conn.execute(
        "SELECT COUNT(*) FROM etf_scale_daily WHERE base_date=?",(selected,)
    ).fetchone()[0]
    coverage=min(successful,scale_count)/max(universe,1)

    direct={row[0]:{"count":int(row[1]),"amount":float(row[2] or 0)} for row in conn.execute(
        """
        SELECT c.component_code,COUNT(DISTINCT c.etf_ticker),
               SUM(c.valuation_amount*d.scale_factor)/100000000.0
        FROM etf_pdf_full_component c
        JOIN etf_pdf_full_snapshot s ON s.base_date=c.base_date AND s.etf_ticker=c.etf_ticker
        JOIN etf_scale_daily d ON d.base_date=c.base_date AND d.etf_ticker=c.etf_ticker
        WHERE c.base_date=? AND s.status='success' AND c.is_domestic_stock=1
        GROUP BY c.component_code
        """,(selected,)
    )}
    legacy={row[0]:{"count":int(row[1] or 0),"amount":float(row[2] or 0)} for row in conn.execute(
        """
        SELECT stock_code,etf_count,etf_amount FROM etf_inclusion_daily
        WHERE trade_date=? AND scope_label='K-ETF' AND COALESCE(is_backfilled,0)=0
        """,(legacy_date,)
    )}
    new_positive={code for code,v in direct.items() if v["count"]>0}
    old_positive={code for code,v in legacy.items() if v["count"]>0}
    overlap=new_positive&old_positive
    union=new_positive|old_positive
    jaccard=len(overlap)/max(len(union),1)
    count_pairs=[(direct[c]["count"],legacy[c]["count"]) for c in overlap]
    exact=sum(a==b for a,b in count_pairs)/max(len(count_pairs),1)
    within_one=sum(abs(a-b)<=1 for a,b in count_pairs)/max(len(count_pairs),1)
    amount_pairs=[(direct[c]["amount"],legacy[c]["amount"]) for c in overlap if direct[c]["amount"]>=1 and legacy[c]["amount"]>=1]
    xs=[math.log1p(a) for a,_ in amount_pairs]; ys=[math.log1p(b) for _,b in amount_pairs]
    correlation=_correlation(xs,ys)
    direct_total=sum(a for a,_ in amount_pairs); legacy_total=sum(b for _,b in amount_pairs)
    total_ratio=direct_total/legacy_total if legacy_total else None
    smapes=[abs(a-b)/((abs(a)+abs(b))/2) for a,b in amount_pairs if a or b]
    median_smape=statistics.median(smapes) if smapes else None
    metrics={
        "new_coverage_ratio":coverage,"membership_jaccard":jaccard,
        "count_exact_ratio":exact,"count_within_one_ratio":within_one,
        "amount_correlation":correlation,"amount_total_ratio":total_ratio,
        "amount_median_smape":median_smape,
    }
    failures=[]
    for key in ("new_coverage_ratio","membership_jaccard","count_within_one_ratio","amount_correlation"):
        if metrics[key] is None or metrics[key] < THRESHOLDS[key]: failures.append(key)
    if total_ratio is None or not THRESHOLDS["amount_total_ratio_min"]<=total_ratio<=THRESHOLDS["amount_total_ratio_max"]:
        failures.append("amount_total_ratio")
    if median_smape is None or median_smape>THRESHOLDS["amount_median_smape_max"]:
        failures.append("amount_median_smape")
    passed=not failures and legacy_date==selected
    if legacy_date!=selected: failures.append("date_mismatch")
    now=datetime.now().isoformat(timespec="seconds")
    values=(selected,legacy_date,universe,successful,scale_count,coverage,len(old_positive),len(new_positive),len(overlap),jaccard,len(count_pairs),exact,within_one,len(amount_pairs),correlation,total_ratio,median_smape,int(passed),json.dumps(failures,ensure_ascii=False),json.dumps(metrics,ensure_ascii=False),now)
    with conn:
        conn.execute(
            """
            INSERT INTO etf_source_parity_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(base_date) DO UPDATE SET
                legacy_date=excluded.legacy_date,universe_count=excluded.universe_count,
                successful_etf_count=excluded.successful_etf_count,scale_count=excluded.scale_count,
                new_coverage_ratio=excluded.new_coverage_ratio,
                legacy_positive_count=excluded.legacy_positive_count,direct_positive_count=excluded.direct_positive_count,
                overlap_positive_count=excluded.overlap_positive_count,membership_jaccard=excluded.membership_jaccard,
                count_compared_count=excluded.count_compared_count,count_exact_ratio=excluded.count_exact_ratio,
                count_within_one_ratio=excluded.count_within_one_ratio,amount_compared_count=excluded.amount_compared_count,
                amount_correlation=excluded.amount_correlation,amount_total_ratio=excluded.amount_total_ratio,
                amount_median_smape=excluded.amount_median_smape,passed=excluded.passed,
                failures_json=excluded.failures_json,metrics_json=excluded.metrics_json,audited_at=excluded.audited_at
            """,values,
        )
        rows=conn.execute(
            "SELECT passed FROM etf_source_parity_daily ORDER BY base_date DESC LIMIT ?",(REQUIRED_PASS_DAYS,)
        ).fetchall()
        consecutive=0
        for row in rows:
            if not row[0]: break
            consecutive+=1
        control=conn.execute("SELECT mode FROM etf_source_control WHERE control_id=1").fetchone()[0]
        new_mode=control
        cutover=None
        if control in ("legacy_validation","legacy_fallback") and consecutive>=REQUIRED_PASS_DAYS:
            new_mode="krx_primary"; cutover=now
        elif control=="krx_primary" and len(rows)>=2 and not rows[0][0] and not rows[1][0]:
            new_mode="legacy_fallback"
        conn.execute(
            """
            UPDATE etf_source_control SET mode=?,consecutive_pass_days=?,
                cutover_at=COALESCE(?,cutover_at),last_evaluated_date=?,last_failure=?,updated_at=?
            WHERE control_id=1
            """,
            (new_mode,consecutive,cutover,selected,",".join(failures) or None,now),
        )
    return {"base_date":selected,"legacy_date":legacy_date,"passed":passed,"failures":failures,"metrics":metrics,"counts":{"universe":universe,"successful":successful,"scale":scale_count,"legacy_positive":len(old_positive),"direct_positive":len(new_positive),"overlap":len(overlap),"amount_compared":len(amount_pairs)},"consecutive_pass_days":consecutive,"mode":new_mode}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--date"); parser.add_argument("--db",default=str(DB_PATH)); args=parser.parse_args()
    conn=connect(Path(args.db)); print(json.dumps(evaluate(conn,args.date),ensure_ascii=False,indent=2)); conn.close()


if __name__=="__main__": main()
