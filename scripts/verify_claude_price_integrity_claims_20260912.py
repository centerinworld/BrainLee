#!/usr/bin/env python3
"""Read-only independent checks for the 2026-09-12 Claude price claims."""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db
from price_integrity import price_band
from scripts.audit_selected_strategy_price_integrity import holding_windows, _distinct_windows, _is_survivorship

MANIFEST = ROOT / 'research_outputs/price_integrity_remediation_20260909/recurring_splice_confirmed_20260912.json'


def same(a, b):
    return len(a) == len(b) and all(x is not None and y is not None and abs(float(x)-float(y)) <= 1e-6 for x,y in zip(a,b))


def main():
    manifest = json.loads(MANIFEST.read_text())
    conn = connect_primary_db(timeout=180)
    conn.execute('SET default_transaction_read_only=on')
    try:
        backups = conn.execute("""SELECT run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
               new_open,new_high,new_low,new_close,new_volume
            FROM price_history_fix_backup WHERE run_id LIKE 'recurring_splice_repair_%'
            ORDER BY run_id,stock_code,date""").fetchall()
        latest_run = max((r[0] for r in backups), default='')
        latest = [tuple(r) for r in backups if r[0] == latest_run]
        current_new = current_old = changed_after = 0
        for r in latest:
            cur = conn.execute("SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND date::text=?",(r[1],r[2])).fetchone()
            if cur and same(tuple(cur),r[8:13]): current_new += 1
            elif cur and same(tuple(cur),r[3:8]): current_old += 1
            else: changed_after += 1

        ca_rows = conn.execute("SELECT stock_code,event_date::text,event_type,adjustment_status FROM corporate_action_events").fetchall()
        ca = defaultdict(list)
        for row in ca_rows: ca[row[0]].append(tuple(row[1:]))
        near_ca = []
        exact_ca = []
        near_ca_30 = []
        near_ca_90 = []
        codes=sorted({c['stock_code'] for c in manifest})
        marks=','.join('?' for _ in codes)
        disclosure_rows=conn.execute(f"""SELECT stock_code,rcept_dt,report_nm FROM dart_disclosures
            WHERE stock_code IN ({marks}) AND (report_nm LIKE '%분할%' OR report_nm LIKE '%병합%'
              OR report_nm LIKE '%증자%' OR report_nm LIKE '%감자%' OR report_nm LIKE '%상장폐지%'
              OR report_nm LIKE '%주식%' OR report_nm LIKE '%합병%')""",tuple(codes)).fetchall()
        disclosures=defaultdict(list)
        for code,rcept_dt,name in disclosure_rows:
            raw=str(rcept_dt).replace('-','')[:8]
            if len(raw)==8 and raw.isdigit():
                disclosures[code].append((date(int(raw[:4]),int(raw[4:6]),int(raw[6:])),name))
        near_disclosure_30=[]
        near_disclosure_90=[]
        zero_volume_rows = zero_ohlc_rows = 0
        inferred_tiers = Counter()
        for cand in manifest:
            run_dates = cand['run_dates']
            lo = min(date.fromisoformat(d) for d in run_dates) - timedelta(days=3)
            hi = max(date.fromisoformat(d) for d in run_dates) + timedelta(days=3)
            matches = [event for event in ca[cand['stock_code']] if lo <= date.fromisoformat(event[0]) <= hi]
            if matches:
                near_ca.append((cand['stock_code'],run_dates,matches))
                if any(event[0] in run_dates for event in matches): exact_ca.append((cand['stock_code'],run_dates,matches))
            for days,bucket in ((30,near_ca_30),(90,near_ca_90)):
                lo_w=min(date.fromisoformat(d) for d in run_dates)-timedelta(days=days)
                hi_w=max(date.fromisoformat(d) for d in run_dates)+timedelta(days=days)
                wide=[event for event in ca[cand['stock_code']] if lo_w<=date.fromisoformat(event[0])<=hi_w]
                if wide: bucket.append((cand['stock_code'],run_dates,wide))
            for days,bucket in ((30,near_disclosure_30),(90,near_disclosure_90)):
                lo_w=min(date.fromisoformat(d) for d in run_dates)-timedelta(days=days)
                hi_w=max(date.fromisoformat(d) for d in run_dates)+timedelta(days=days)
                wide=[(d.isoformat(),name) for d,name in disclosures[cand['stock_code']] if lo_w<=d<=hi_w]
                if wide: bucket.append((cand['stock_code'],run_dates,wide))
            lo_band, hi_band = price_band(run_dates[0])
            tier = 'cross_source_ratio_outside_price_band' if not lo_band <= cand['median_ratio'] <= hi_band else 'stale_rule'
            inferred_tiers[tier] += 1
            for d in cand['days']:
                zero_volume_rows += float(d['ph_volume']) == 0
                zero_ohlc_rows += float(d['ph_open']) == float(d['ph_high']) == float(d['ph_low']) == 0

        selected = conn.execute("""SELECT strategy,run_hash FROM selected_run_registry
            WHERE report_type='strategy_center' ORDER BY strategy""").fetchall()
        strategy_denominators = []
        latest_result = json.loads((ROOT/'research_outputs/selected_strategy_price_integrity_latest.json').read_text())
        latest_by_strategy = {s['strategy']:s for s in latest_result['strategies']}
        for strategy,suite_hash in selected:
            raw=[]
            members=conn.execute("""SELECT m.period_label,r.trades_json,r.end_date
                FROM backtest_run_set_members m
                JOIN backtest_run_specs s ON s.run_hash=m.run_hash
                JOIN backtest_runs r ON r.run_id=s.run_id
                WHERE m.suite_hash=? AND r.status='done' ORDER BY m.period_label,s.created_at DESC""",(suite_hash,)).fetchall()
            seen=set()
            for label,trades_json,end_date in members:
                if label in seen or not trades_json: continue
                seen.add(label)
                payload=json.loads(trades_json)
                trades=payload.get('trades',[]) if isinstance(payload,dict) else payload
                raw.extend((label,*w) for w in holding_windows(trades,str(end_date or '9999-12-31')[:10]))
            unique=set(raw)
            saved=latest_by_strategy.get(strategy,{})
            price_findings=[f for f in saved.get('contaminated_events',[]) if not _is_survivorship(f)]
            contaminated=len(_distinct_windows(price_findings))
            strategy_denominators.append({
                'strategy':strategy,'raw_windows':len(raw),'distinct_windows':len(unique),
                'duplicate_windows':len(raw)-len(unique),'reported_ratio':saved.get('price_jump_window_contamination_ratio'),
                'distinct_denominator_ratio':round(contaminated/len(unique),4) if unique else 0,
            })

        date_counts=[]
        for day in ('2026-07-17','2026-07-20','2026-07-21'):
            row=conn.execute("""SELECT COUNT(*),COUNT(*) FILTER(WHERE close>0),
                    COUNT(*) FILTER(WHERE close>0 AND volume>0),
                    COUNT(*) FILTER(WHERE stock_code IN ('^KS11','^KQ11','^KS200','^KQ150'))
                FROM price_history WHERE substr(date,1,10)=?""",(day,)).fetchone()
            date_counts.append([day,*tuple(row)])

        july_quality = conn.execute("""SELECT canonical_quality,COUNT(*)
            FROM canonical_price_history_v WHERE CAST(date AS TEXT)='2026-07-20'
            GROUP BY canonical_quality ORDER BY COUNT(*) DESC""").fetchall()
        july_audit = conn.execute("""SELECT classification,COUNT(*)
            FROM price_jump_audit WHERE CAST(event_date AS TEXT)='2026-07-20'
            GROUP BY classification ORDER BY COUNT(*) DESC""").fetchall()

        definitely_conflicted = []
        conflicted_keys = {
            (code, day)
            for code, run_dates, _matches in near_ca
            for day in run_dates
        } | {('000520', day) for day in ('2021-04-07', '2021-04-08', '2021-04-09')}
        for row in latest:
            if (row[1], row[2]) in conflicted_keys:
                episode = next((c for c in manifest if c['stock_code'] == row[1] and row[2] in c['run_dates']), None)
                if episode is not None:
                    definitely_conflicted.append({
                        'stock_code': row[1], 'date': row[2],
                        'old': list(row[3:8]), 'new': list(row[8:13]),
                        'tier': ('cross_source_ratio_outside_price_band'
                                 if not price_band(episode['run_dates'][0])[0] <= episode['median_ratio'] <= price_band(episode['run_dates'][0])[1]
                                 else 'stale_rule'),
                    })

        report={
            'manifest':{'episodes':len(manifest),'stocks':len({c['stock_code'] for c in manifest}),
                        'rows':sum(len(c['days']) for c in manifest),'inferred_tiers':dict(inferred_tiers),
                        'zero_volume_rows':zero_volume_rows,'zero_ohlc_rows':zero_ohlc_rows,
                        'near_corporate_action_within_3_calendar_days':len(near_ca),
                        'exact_corporate_action_date':len(exact_ca),'near_corporate_action_examples':near_ca[:20],
                        'near_corporate_action_within_30_days':len(near_ca_30),
                        'near_corporate_action_within_90_days':len(near_ca_90),
                        'near_relevant_disclosure_within_30_days':len(near_disclosure_30),
                        'near_relevant_disclosure_within_90_days':len(near_disclosure_90),
                        'near_relevant_disclosure_examples':near_disclosure_30[:20]},
            'applied_backup':{'latest_run':latest_run,'rows':len(latest),'current_matches_new':current_new,
                              'current_matches_old':current_old,'changed_after':changed_after},
            'strategy_gate':{'reported':{k:latest_result[k] for k in ('checked_at','strategy_count','passed','failed','no_trade_evidence')},
                             'strategies_with_duplicate_denominator':sum(r['duplicate_windows']>0 for r in strategy_denominators),
                             'details':strategy_denominators},
            'july_20_local_rows':date_counts,
            'july_20_canonical_quality':list(map(tuple,july_quality)),
            'july_20_price_jump_audit':list(map(tuple,july_audit)),
            'applied_rows_conflicting_with_ca_rule_or_known_split': definitely_conflicted,
            'invalidated_repair_quarantine_rows': conn.execute(
                "SELECT COUNT(*) FROM price_integrity_quarantine WHERE reason=?",
                ('recurring_splice_auto_confirmation_invalidated',),
            ).fetchone()[0],
        }
        print(json.dumps(report,ensure_ascii=False,indent=2,default=str))
    finally:
        conn.rollback();conn.close()


if __name__=='__main__': main()
