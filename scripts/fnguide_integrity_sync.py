"""
FnGuide → financial_data 무결성 동기화 + stock_collection_config 구축

실행:
  python3 scripts/fnguide_integrity_sync.py            # critical 이슈 수정 + config 구축
  python3 scripts/fnguide_integrity_sync.py --dry-run  # 변경 없이 리포트만
  python3 scripts/fnguide_integrity_sync.py --all      # large_discrepancy 포함 전체
"""
import sqlite3
import json
import sys
import argparse
from datetime import datetime

DB_PATH = "stock.db"
DRY_RUN = "--dry-run" in sys.argv
ALL_MODE = "--all" in sys.argv

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")

# ── Step 0: stock_collection_config 테이블 생성 ──────────────────────
conn.executescript("""
CREATE TABLE IF NOT EXISTS stock_collection_config (
    stock_code      TEXT NOT NULL,
    config_key      TEXT NOT NULL,   -- 'report_type', 'fiscal_month', 'unit_multiplier', 'skip_cfs', ...
    config_value    TEXT NOT NULL,   -- 'CFS'/'OFS', '3'/'12', '100', 'true', ...
    reason          TEXT,            -- 왜 이 설정인지 (FnGuide 검증 결과, 수동 설정 등)
    source          TEXT DEFAULT 'fnguide_validation',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (stock_code, config_key)
);
CREATE INDEX IF NOT EXISTS idx_scc_code ON stock_collection_config(stock_code);
""")
conn.commit()
print("[INIT] stock_collection_config 테이블 준비 완료")


# ── 헬퍼 ──────────────────────────────────────────────────────────────
def upsert_config(stock_code, key, value, reason, source="fnguide_validation"):
    conn.execute("""
        INSERT INTO stock_collection_config (stock_code, config_key, config_value, reason, source, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(stock_code, config_key) DO UPDATE SET
            config_value=excluded.config_value,
            reason=excluded.reason,
            source=excluded.source,
            updated_at=excluded.updated_at
    """, (stock_code, key, str(value), reason, source))


def mark_resolved(anomaly_id, note=""):
    conn.execute("""
        UPDATE financial_anomalies SET is_resolved=1, last_checked=datetime('now')
        WHERE id=?
    """, (anomaly_id,))


def backup_table_name():
    return f"financial_data_backup_fnguide_sync_{datetime.now().strftime('%Y%m%d')}"


# ── Step 1: 백업 ──────────────────────────────────────────────────────
bk = backup_table_name()
existing_bk = conn.execute(
    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{bk}'"
).fetchone()
if not existing_bk:
    conn.execute(f"CREATE TABLE {bk} AS SELECT * FROM financial_data")
    conn.commit()
    print(f"[BACKUP] financial_data → {bk}")
else:
    print(f"[BACKUP] 이미 존재: {bk} (재사용)")


# ── Step 2: unit_error 수정 (auto_fixable=1, critical) ────────────────
unit_errors = conn.execute("""
    SELECT * FROM financial_anomalies
    WHERE anomaly_type='unit_error' AND auto_fixable=1 AND is_resolved=0
""").fetchall()

print(f"\n[UNIT_ERROR] {len(unit_errors)}건 처리")
unit_fixed = 0
for a in unit_errors:
    code = a["stock_code"]
    desc = a["description"]
    # 100배 vs 1000배 판단
    multiplier = 1000 if "1000배" in desc else 100
    # 영향 연도 파악
    import re
    years = [int(y) for y in re.findall(r'\d{4}', desc)]
    affected_fields = json.loads(a["affected_fields"] or "[]") or ["revenue", "operating_profit", "net_income"]

    # FnGuide snapshot이 있으면 그 값을 사용, 없으면 multiplier 적용
    for year in years:
        snap = conn.execute("""
            SELECT * FROM financial_source_snapshot
            WHERE stock_code=? AND year=? AND is_annual=1 AND data_source='fnguide'
            ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END) ASC LIMIT 1
        """, (code, year)).fetchone()

        db_row = conn.execute("""
            SELECT id, * FROM financial_data
            WHERE stock_code=? AND year=? AND is_annual=1
            ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END) ASC LIMIT 1
        """, (code, year)).fetchone()

        if not db_row:
            continue

        if snap:
            # FnGuide 값으로 덮어쓰기
            updates = {}
            for field in ["revenue", "operating_profit", "net_income"]:
                if snap[field] is not None and field in affected_fields:
                    updates[field] = snap[field]
            if updates:
                set_clause = ", ".join(f"{k}=?" for k in updates)
                vals = list(updates.values()) + [db_row["id"]]
                if not DRY_RUN:
                    conn.execute(f"UPDATE financial_data SET {set_clause} WHERE id=?", vals)
                print(f"  [{code}] {year} FnGuide 값으로 수정: {updates}")
                unit_fixed += 1
        else:
            # FnGuide 없으면 multiplier로 역산
            updates = {}
            for field in affected_fields:
                v = db_row[field]
                if v is not None and abs(v) > 0:
                    updates[field] = v * multiplier
            if updates:
                set_clause = ", ".join(f"{k}=?" for k in updates)
                vals = list(updates.values()) + [db_row["id"]]
                if not DRY_RUN:
                    conn.execute(f"UPDATE financial_data SET {set_clause} WHERE id=?", vals)
                print(f"  [{code}] {year} ×{multiplier} 수정: {updates}")
                unit_fixed += 1

    # stock_collection_config 등록: 이 종목은 수집 시 단위 검증 필수
    if not DRY_RUN:
        upsert_config(code, "unit_verified", "true",
                      f"unit_error 감지({desc}) → FnGuide 기준 수정 완료")
        mark_resolved(a["id"])

if not DRY_RUN:
    conn.commit()
print(f"  → {unit_fixed}건 수정")


# ── Step 3: cfs_ofs_mislabeled 수정 ──────────────────────────────────
mislabeled = conn.execute("""
    SELECT * FROM financial_anomalies
    WHERE anomaly_type='cfs_ofs_mislabeled' AND auto_fixable=1 AND is_resolved=0
""").fetchall()

print(f"\n[CFS_OFS] {len(mislabeled)}건 처리")
cfs_fixed = 0
for a in mislabeled:
    code = a["stock_code"]
    years = [int(y) for y in re.findall(r'\d{4}', a["description"])]

    for year in years:
        # FnGuide의 CFS vs OFS 중 어느 것이 DB값과 맞는지 확인
        snap_cfs = conn.execute("""
            SELECT * FROM financial_source_snapshot
            WHERE stock_code=? AND year=? AND is_annual=1 AND report_type='CFS' AND data_source='fnguide'
        """, (code, year)).fetchone()
        snap_ofs = conn.execute("""
            SELECT * FROM financial_source_snapshot
            WHERE stock_code=? AND year=? AND is_annual=1 AND report_type='OFS' AND data_source='fnguide'
        """, (code, year)).fetchone()
        db_row = conn.execute("""
            SELECT rowid, report_type, revenue FROM financial_data
            WHERE stock_code=? AND year=? AND is_annual=1
            ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END) ASC LIMIT 1
        """, (code, year)).fetchone()

        if not db_row:
            continue

        # DB revenue와 더 가까운 FnGuide snapshot 선택
        db_rev = db_row["revenue"] or 0
        correct_snap = None
        correct_type = None

        if snap_cfs and snap_ofs:
            diff_cfs = abs((snap_cfs["revenue"] or 0) - db_rev)
            diff_ofs = abs((snap_ofs["revenue"] or 0) - db_rev)
            if diff_cfs < diff_ofs:
                correct_snap = snap_cfs
                correct_type = "CFS"
            else:
                correct_snap = snap_ofs
                correct_type = "OFS"
        elif snap_cfs:
            correct_snap = snap_cfs; correct_type = "CFS"
        elif snap_ofs:
            correct_snap = snap_ofs; correct_type = "OFS"

        if correct_snap and correct_type != db_row["report_type"]:
            if not DRY_RUN:
                conn.execute(
                    "UPDATE financial_data SET report_type=? WHERE id=?",
                    (correct_type, db_row["id"])
                )
            print(f"  [{code}] {year} report_type {db_row['report_type']} → {correct_type}")
            cfs_fixed += 1

    # stock_collection_config: 이 종목의 올바른 report_type 기록
    if correct_type and not DRY_RUN:
        upsert_config(code, "preferred_report_type", correct_type,
                      f"cfs_ofs_mislabeled 교정 결과 {correct_type} 확인")
        mark_resolved(a["id"])

if not DRY_RUN:
    conn.commit()
print(f"  → {cfs_fixed}건 수정")


# ── Step 4: large_discrepancy — FnGuide 스냅샷으로 덮어쓰기 ──────────
if ALL_MODE:
    discrepancies = conn.execute("""
        SELECT * FROM financial_anomalies
        WHERE anomaly_type='large_discrepancy' AND is_resolved=0
    """).fetchall()

    print(f"\n[LARGE_DISCREPANCY] {len(discrepancies)}건 처리 (--all 모드)")
    disc_fixed = 0
    for a in discrepancies:
        code = a["stock_code"]
        year = a["sample_year"]
        affected = json.loads(a["affected_fields"] or "[]")
        if not year or not affected:
            continue

        snap = conn.execute("""
            SELECT * FROM financial_source_snapshot
            WHERE stock_code=? AND year=? AND is_annual=1 AND data_source='fnguide'
            ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END) ASC LIMIT 1
        """, (code, year)).fetchone()

        if not snap:
            continue

        db_row = conn.execute("""
            SELECT rowid FROM financial_data
            WHERE stock_code=? AND year=? AND is_annual=1
            ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END) ASC LIMIT 1
        """, (code, year)).fetchone()

        if not db_row:
            continue

        updates = {}
        for field in affected:
            col_map = {"net_income": "net_income", "revenue": "revenue",
                       "operating_profit": "operating_profit",
                       "total_assets": "total_assets", "total_equity": "total_equity"}
            if field in col_map and snap[col_map[field]] is not None:
                updates[field] = snap[col_map[field]]

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [db_row["id"]]
            if not DRY_RUN:
                conn.execute(f"UPDATE financial_data SET {set_clause} WHERE id=?", vals)
            print(f"  [{code}] {year} FnGuide 덮어쓰기: {list(updates.keys())}")
            disc_fixed += 1
            if not DRY_RUN:
                mark_resolved(a["id"])

    if not DRY_RUN:
        conn.commit()
    print(f"  → {disc_fixed}건 수정")
else:
    disc_count = conn.execute(
        "SELECT COUNT(*) FROM financial_anomalies WHERE anomaly_type='large_discrepancy' AND is_resolved=0"
    ).fetchone()[0]
    print(f"\n[LARGE_DISCREPANCY] {disc_count}건 대기 중. --all 옵션으로 실행하면 처리됩니다.")


# ── Step 5: holding_company → OFS 우선 수집 등록 ─────────────────────
holding_cos = conn.execute("""
    SELECT stock_code FROM financial_anomalies
    WHERE anomaly_type='holding_company' AND is_resolved=0
""").fetchall()

print(f"\n[HOLDING_CO] {len(holding_cos)}건 → OFS 우선 config 등록")
if not DRY_RUN:
    for row in holding_cos:
        upsert_config(row["stock_code"], "preferred_report_type", "OFS",
                      "지주사: 연결보다 별도재무제표가 실체 반영")
    conn.commit()


# ── Step 6: 요약 ──────────────────────────────────────────────────────
config_count = conn.execute("SELECT COUNT(*) FROM stock_collection_config").fetchone()[0]
resolved = conn.execute("SELECT COUNT(*) FROM financial_anomalies WHERE is_resolved=1").fetchone()[0]
unresolved = conn.execute("SELECT COUNT(*) FROM financial_anomalies WHERE is_resolved=0").fetchone()[0]

print(f"""
{'[DRY-RUN] ' if DRY_RUN else ''}=== 완료 ===
stock_collection_config: {config_count}건
financial_anomalies 해결: {resolved}건 / 미해결: {unresolved}건
""")

conn.close()
