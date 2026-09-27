"""Deterministic scan observations; aggregate counts never authorize code changes."""
import hashlib
import json

RULES = {
    'invalid_ohlc_count': 'price.open_close_missing_or_nonpositive.v1',
    'high_less_than_low_count': 'price.high_below_low.v1',
    'future_dated_count': 'price.future_date_candidate.v1',
}
TABLES = {'price_history', 'stock_price_daily', 'us_price_history'}
MISSING = ['affected_row_keys', 'source_comparison', 'collector_path',
           'reproducing_test', 'verified_root_cause']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


def build_findings(scan):
    if not isinstance(scan, dict) or scan.get('db_backend') != 'postgresql':
        raise ValueError('Expected structured PostgreSQL scan, not prose')
    identity = {k: scan.get(k) for k in ('db_backend', 'db_name', 'db_host', 'db_port')}
    if any(v is None or v == '' for v in identity.values()) or not scan.get('scanned_at'):
        raise ValueError('Missing database identity or observation time')
    tables = scan.get('tables')
    if not isinstance(tables, list) or not tables:
        raise ValueError('Missing scan tables')
    findings, seen = [], set()
    for table in tables:
        name = table.get('table')
        if name not in TABLES or name in seen:
            raise ValueError('Unknown or duplicate scan table')
        seen.add(name)
        total = table.get('row_count')
        if type(total) is not int or total < 0:
            raise ValueError('Invalid row count')
        for field, rule in RULES.items():
            count = table.get(field)
            if type(count) is not int or not 0 <= count <= total:
                raise ValueError('Invalid or missing rule count')
            if not count:
                continue
            key = {'database': identity, 'table': name, 'rule_id': rule}
            findings.append({
                'finding_id': 'finding-' + digest(key), **key,
                'status': 'NEEDS_REPRODUCTION', 'affected_count': count,
                'observed_at': scan['scanned_at'], 'evidence_hash': digest(scan),
                'code_job_eligible': False, 'missing_evidence': list(MISSING),
                'scope': 'aggregate candidate; not a confirmed collector defect',
            })
    return {'schema_version': 1, 'status': 'NEEDS_REPRODUCTION' if findings else 'NO_CANDIDATES_IN_CHECKED_RULES',
            'findings': findings, 'scan': scan, 'evidence_hash': digest(scan),
            'code_job_eligible': False, 'coverage': {'tables': sorted(seen), 'rules': list(RULES.values())},
            'goal_verified': False}
