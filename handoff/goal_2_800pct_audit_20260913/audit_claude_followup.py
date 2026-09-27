"""Read-only audit of stored runs, not a fresh strategy backtest.

Run with stock_dashboard/runtime/venv/bin/python and PYTHONDONTWRITEBYTECODE=1.
All output stays beside this script; operational PostgreSQL is read-only.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path('/Volumes/Realtek_NVME/stock_dashboard')
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'runtime'))
from db_compat import connect_primary_db
from config import IS_POSTGRES


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def reconcile(row):
    payload = json.loads(row['trades_json'])
    trades = payload['trades']
    initial = row['per_stock'] * row['max_pos']
    cash = initial
    positions = {}
    errors = []
    for t in trades:
        code, qty = t['code'], t.get('qty', 0)
        if not row['start_date'] <= t['date'] <= row['end_date']:
            errors.append('trade_outside_period')
        if code == '086520':
            errors.append('excluded_ecopro_traded')
        if t['action'] == 'BUY':
            cash -= qty * t['price']
            positions[code] = positions.get(code, 0) + qty
        elif t.get('pnl_krw') is not None and t.get('entry_price') is not None:
            cash += qty * t['entry_price'] + t['pnl_krw']
            positions[code] = positions.get(code, 0) - qty
            if positions[code] < 0:
                errors.append('oversell')
        else:
            errors.append('unresolved_or_unsupported_trade')
        if cash < -1:
            errors.append('negative_cash')
    if any(positions.values()):
        errors.append('unclosed_position')
    computed = (cash / initial - 1) * 100
    if abs(computed - row['total_return_pct']) > 0.0051:
        errors.append('return_mismatch')
    params = json.loads(row['parameter_json'] or '{}')
    return {'run_id': row['run_id'], 'initial_cash': initial,
            'ending_cash_reconstructed': cash, 'return_pct_reconstructed': computed,
            'stored_return_pct': row['total_return_pct'],
            'unique_traded_codes': len({t['code'] for t in trades}),
            'trade_count': len(trades), 'trade_hash': digest(trades),
            'run_hash': row['run_hash'], 'created_at': row['created_at'],
            'missing_variant_parameters': [k for k in ('partial_tp_pct', 'cost_multiplier',
                                                      'exclude_codes') if k not in params],
            'daily_equity_missing': not row['equity_json'],
            'mdd_missing': row['max_drawdown_pct'] is None,
            'errors': sorted(set(errors))}


def main():
    if not IS_POSTGRES:
        raise RuntimeError('Expected operational PostgreSQL; do not silently audit stale SQLite')
    conn = connect_primary_db(readonly=True)
    try:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows = [dict(r) for r in conn.execute('''
            SELECT r.id,r.run_id,r.start_date,r.end_date,r.per_stock,r.max_pos,
                   r.status,r.total_return_pct,r.total_trades,r.trades_json,r.equity_json,
                   r.max_drawdown_pct,r.created_at,s.parameter_json,s.run_hash,s.fee_model
            FROM backtest_runs r LEFT JOIN backtest_run_specs s ON r.run_id=s.run_id
            WHERE r.strategy=? ORDER BY r.id DESC LIMIT 48
        ''', ('sector_focus',)).fetchall()]
    finally:
        conn.rollback()
        conn.close()
    checks = [reconcile(r) for r in rows]
    collisions = {}
    for r, check in zip(rows, checks):
        collisions.setdefault(r['run_hash'], []).append(check)
    collisions = [{'run_hash': h, 'run_ids': [c['run_id'] for c in cs],
                   'distinct_trade_hashes': len({c['trade_hash'] for c in cs}),
                   'distinct_returns': sorted({c['stored_return_pct'] for c in cs})}
                  for h, cs in collisions.items()
                  if len({c['trade_hash'] for c in cs}) > 1]
    # Positional grouping is a hypothesis from the inspected script's call order.
    # It is not proof of parameters, session identity or independent execution.
    ordered = sorted(rows, key=lambda r: r['id'])
    groups = [ordered[i:i+24] for i in range(0, len(ordered), 24)]
    hypotheses = []
    for group in groups:
        variants = {}
        for offset, name in enumerate(('base_1x', 'base_2x', 'partial_1x', 'partial_2x')):
            v = group[offset::4]
            periods = [(r['start_date'], r['end_date']) for r in v]
            variants[name] = {'run_ids': [r['run_id'] for r in v], 'periods': periods,
                'nonoverlapping': all(a[1] < b[0] for a, b in zip(periods, periods[1:])),
                'mean_return_pct': sum(r['total_return_pct'] for r in v) / len(v),
                'hypothetical_compounded_pct': (math.prod(1+r['total_return_pct']/100 for r in v)-1)*100,
                'fixed_initial_capital_sum_pct': sum(r['total_return_pct'] for r in v)}
        hypotheses.append(variants)
    paths = [ROOT/'runtime/scripts/walkforward_nonoverlap_without_ecopro.py',
             ROOT/'runtime/backtest_strategies/sector.py',
             ROOT/'docs/claude_approval_orch-1789290863304.md',
             ROOT/'docs/qwen_handoff_to_claude_and_codex_20260913_181547_orch-1789290863304.md']
    result = {'audit_time_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Stored-run reconciliation only; no fresh backtest or model review',
              'source_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
              'snapshot_hash': digest(rows), 'run_count': len(rows),
              'checks': checks, 'run_hash_collisions': collisions,
              'unverified_call_order_hypotheses': hypotheses,
              'limitations': ['Fixed tickets and cash reset per fold: product is not a verified wealth path',
                              'Matching repeated artifacts do not prove separate processes/sessions',
                              'Trade pnl is reconciled, not independently repriced from market data',
                              'Historical reuse is not proof of pre-registered OOS training/selection']}
    (OUT/'claude_run_snapshot.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str))
    (OUT/'claude_run_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    print(json.dumps({'run_count': len(rows),
                      'reconciliation_failures': [c for c in checks if c['errors']],
                      'hash_collision_groups': len(collisions),
                      'missing_variant_specs': sum(bool(c['missing_variant_parameters']) for c in checks),
                      'missing_daily_equity': sum(c['daily_equity_missing'] for c in checks),
                      'group_hypotheses': hypotheses},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
