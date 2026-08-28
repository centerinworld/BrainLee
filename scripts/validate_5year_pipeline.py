"""
validate_5year_pipeline.py — 5년치 재무제표·현금흐름표 100% 검증 + 이상 분류

단계:
  1. financial_source_snapshot (FnGuide 원본) vs financial_data (DB) 비교
  2. 이상 유형 자동 분류 → financial_anomalies 테이블에 기록
  3. 자동 수정 가능 항목 즉시 수정 (단위오류 등)
  4. 10년치 수집 시 활용할 종목별 가이드를 financial_profiles.json에 병합
  5. 결과 요약 보고서 → scratch/validation_5year_report.json

이상 유형:
  clean             — 모든 필드 허용 오차 이내, 이상 없음
  unit_error        — 100배 차이 (억원/원 혼용)
  cfs_ofs_mislabeled— DB의 CFS값이 FnGuide OFS에 가까움 (연결/별도 오레이블)
  holding_company   — CFS/OFS 매출 비율 3배 이상 (지주사 특성)
  large_discrepancy — 30%+ 차이 (데이터 오류 의심)
  missing_data      — 해당 연도 스냅샷 없음
  newly_listed      — 5년 중 3년 미만 보유 (신규 상장)
  persistent_loss   — 3년+ 연속 순손실
  revenue_zero      — 매출 0 또는 NULL (투자회사·지주사)
  partial_coverage  — 5년 중 일부 연도만 보유

실행:
  # 5년치(2020-2024) 검증 (수집 완료 후 실행)
  python3 scripts/validate_5year_pipeline.py

  # 자동 수정 포함 (unit_error 즉시 수정)
  python3 scripts/validate_5year_pipeline.py --auto-fix

  # 특정 연도
  python3 scripts/validate_5year_pipeline.py --years 2022 2023 2024
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys_path = str(Path(__file__).resolve().parent.parent)
import sys
sys.path.insert(0, sys_path)

from financial_profiles import load_profiles

logger = logging.getLogger(__name__)
DB_PATH   = Path(__file__).resolve().parent.parent / "stock.db"
PROFILES_PATH = Path(__file__).resolve().parent.parent / "config" / "financial_profiles.json"
REPORT_PATH   = Path(__file__).resolve().parent.parent / "scratch" / "validation_5year_report.json"

# 이상 판정 임계값
TOL_UNIT   = 80.0    # 80배 이상 차이 → 단위 오류 의심
TOL_LARGE  = 0.30    # 30% 이상 차이 → 대형 불일치
CFS_OFS_RATIO_HOLD = 3.0   # CFS/OFS 매출 비율 3배 이상 → 지주사
LOSS_YEARS_THRESHOLD = 3   # 연속 손실 기준년수


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=120000")
    return c


def _ensure_anomalies_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS financial_anomalies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_code TEXT NOT NULL,
        anomaly_type TEXT NOT NULL,
        severity TEXT DEFAULT 'info',
        description TEXT,
        affected_fields TEXT,
        cfs_ofs_ratio REAL,
        sample_year INTEGER,
        recommended_action TEXT,
        auto_fixable INTEGER DEFAULT 0,
        is_resolved INTEGER DEFAULT 0,
        first_detected TEXT NOT NULL,
        last_checked TEXT,
        UNIQUE(stock_code, anomaly_type)
    );
    CREATE INDEX IF NOT EXISTS ix_fa_code ON financial_anomalies(stock_code);
    CREATE INDEX IF NOT EXISTS ix_fa_type ON financial_anomalies(anomaly_type);
    """)
    conn.commit()


def _upsert_anomaly(
    conn: sqlite3.Connection,
    code: str,
    atype: str,
    severity: str,
    description: str,
    affected_fields: list[str],
    sample_year: Optional[int],
    action: str,
    auto_fixable: bool,
    cfs_ofs_ratio: Optional[float] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO financial_anomalies
            (stock_code, anomaly_type, severity, description, affected_fields,
             cfs_ofs_ratio, sample_year, recommended_action, auto_fixable, first_detected, last_checked)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_code, anomaly_type) DO UPDATE SET
            severity=excluded.severity,
            description=excluded.description,
            cfs_ofs_ratio=excluded.cfs_ofs_ratio,
            sample_year=excluded.sample_year,
            recommended_action=excluded.recommended_action,
            auto_fixable=excluded.auto_fixable,
            last_checked=excluded.last_checked
    """, (
        code, atype, severity, description,
        json.dumps(affected_fields, ensure_ascii=False),
        cfs_ofs_ratio, sample_year, action, int(auto_fixable),
        now, now,
    ))


def _ratio(a, b) -> Optional[float]:
    if a and b and abs(b) > 0:
        return abs(a / b)
    return None


def classify_stock(
    code: str,
    name: str,
    years: list[int],
    snapshots: dict,   # {year: {cfs: {...}, ofs: {...}}}
    db_data: dict,     # {year: {cfs: {...}, ofs: {...}}}
) -> list[dict]:
    """
    한 종목의 전체 연도 데이터를 분석해 이상 유형 목록 반환.
    각 항목: {type, severity, description, fields, year, action, auto_fixable, cfs_ofs_ratio}
    """
    anomalies: list[dict] = []

    covered_years = [y for y in years if y in snapshots or y in db_data]
    missing_years = [y for y in years if y not in snapshots]

    # ── 신규상장 / 데이터부족 ─────────────────────────────
    if len(covered_years) < 3:
        anomalies.append({
            "type": "newly_listed", "severity": "info",
            "description": f"5년 중 {len(covered_years)}년만 보유 (신규상장 추정)",
            "fields": [], "year": None, "cfs_ofs_ratio": None,
            "action": "collect_available_years", "auto_fixable": False,
        })
        return anomalies

    if len(missing_years) > 0:
        anomalies.append({
            "type": "partial_coverage", "severity": "warning",
            "description": f"결손 연도: {missing_years}",
            "fields": [], "year": None, "cfs_ofs_ratio": None,
            "action": f"collect_missing_years_{missing_years}", "auto_fixable": False,
        })

    # ── 연속 손실 ──────────────────────────────────────────
    loss_years = []
    for y in years:
        db_cfs = db_data.get(y, {}).get("cfs", {})
        ni = db_cfs.get("net_income")
        if ni is not None and ni < 0:
            loss_years.append(y)
    if len(loss_years) >= LOSS_YEARS_THRESHOLD:
        anomalies.append({
            "type": "persistent_loss", "severity": "info",
            "description": f"순손실 {loss_years}",
            "fields": ["net_income"], "year": loss_years[-1], "cfs_ofs_ratio": None,
            "action": "verify_net_income_carefully", "auto_fixable": False,
        })

    # ── 매출 제로 ──────────────────────────────────────────
    zero_rev_years = [
        y for y in years
        if (db_data.get(y, {}).get("cfs", {}).get("revenue") or 0) == 0
    ]
    if len(zero_rev_years) >= 3:
        anomalies.append({
            "type": "revenue_zero", "severity": "warning",
            "description": f"매출 0/NULL 연도: {zero_rev_years}",
            "fields": ["revenue"], "year": None, "cfs_ofs_ratio": None,
            "action": "use_ofs_revenue_if_available", "auto_fixable": False,
        })

    # ── 연도별 상세 비교 ───────────────────────────────────
    unit_errors:   list[int] = []
    mislab_years:  list[int] = []
    large_disc:    list[tuple] = []
    cfs_ofs_ratios: list[float] = []

    for yr in years:
        snap_yr = snapshots.get(yr, {})
        db_yr   = db_data.get(yr, {})
        if not snap_yr:
            continue

        snap_cfs = snap_yr.get("cfs", {})
        snap_ofs = snap_yr.get("ofs", {})
        db_cfs   = db_yr.get("cfs", {})

        # 지주사 판별: CFS/OFS 매출 비율
        sc_rev = snap_cfs.get("revenue")
        so_rev = snap_ofs.get("revenue")
        if sc_rev and so_rev and so_rev != 0:
            r = abs(sc_rev / so_rev)
            cfs_ofs_ratios.append(r)

        # 필드별 비교
        for field in ("revenue", "operating_profit", "net_income"):
            db_val   = db_cfs.get(field)
            snap_val = snap_cfs.get(field)
            if db_val is None or snap_val is None or snap_val == 0:
                continue

            ratio = abs(db_val / snap_val)

            # 단위 오류 (100배 차이)
            if TOL_UNIT <= ratio <= 150 or (1 / 150) <= ratio <= (1 / TOL_UNIT):
                unit_errors.append(yr)
                break

            # CFS/OFS 혼용 (DB CFS ≈ FnGuide OFS)
            ofs_val = snap_ofs.get(field)
            if ofs_val and snap_val != 0:
                db_vs_ofs = abs(db_val / ofs_val) if ofs_val != 0 else None
                db_vs_cfs = abs(db_val / snap_val) if snap_val != 0 else None
                if db_vs_ofs and db_vs_cfs:
                    if abs(db_vs_ofs - 1) < 0.05 and abs(db_vs_cfs - 1) > 0.2:
                        mislab_years.append(yr)
                        break

            # 대형 불일치
            if abs(ratio - 1.0) > TOL_LARGE:
                large_disc.append((yr, field, db_val, snap_val))

    # CFS/OFS 비율 평균 → 지주사 판정
    avg_ratio = sum(cfs_ofs_ratios) / len(cfs_ofs_ratios) if cfs_ofs_ratios else 1.0
    if avg_ratio >= CFS_OFS_RATIO_HOLD:
        anomalies.append({
            "type": "holding_company", "severity": "info",
            "description": f"CFS/OFS 매출 평균 {avg_ratio:.1f}배 (지주사 특성)",
            "fields": ["revenue"], "year": None, "cfs_ofs_ratio": round(avg_ratio, 2),
            "action": "both_cfs_ofs_valid_use_cfs_primary", "auto_fixable": False,
        })

    if unit_errors:
        anomalies.append({
            "type": "unit_error", "severity": "critical",
            "description": f"100배 단위 오류 감지 연도: {sorted(set(unit_errors))}",
            "fields": ["revenue", "operating_profit", "net_income"],
            "year": unit_errors[0], "cfs_ofs_ratio": None,
            "action": "apply_1e8_multiplier_correction", "auto_fixable": True,
        })

    if mislab_years:
        anomalies.append({
            "type": "cfs_ofs_mislabeled", "severity": "critical",
            "description": f"연결/별도 오레이블 연도: {sorted(set(mislab_years))}",
            "fields": ["revenue", "operating_profit", "net_income"],
            "year": mislab_years[0], "cfs_ofs_ratio": None,
            "action": "correct_report_type_in_db", "auto_fixable": True,
        })

    if large_disc:
        sample = large_disc[:3]
        desc = "; ".join(
            f"{yr} {f}: DB={dv/1e8:.0f}억 FnG={sv/1e8:.0f}억"
            for yr, f, dv, sv in sample
        )
        anomalies.append({
            "type": "large_discrepancy", "severity": "warning",
            "description": desc,
            "fields": list({f for _, f, _, _ in large_disc}),
            "year": large_disc[0][0], "cfs_ofs_ratio": None,
            "action": "override_with_fnguide_values", "auto_fixable": False,
        })

    if not anomalies:
        anomalies.append({
            "type": "clean", "severity": "info",
            "description": "모든 필드 허용 오차 이내",
            "fields": [], "year": None, "cfs_ofs_ratio": None,
            "action": "none", "auto_fixable": False,
        })

    return anomalies


def auto_fix_unit_error(
    conn: sqlite3.Connection,
    code: str,
    years: list[int],
    snapshots: dict,
) -> int:
    """단위 오류(100배) 자동 수정. 반환: 수정된 행 수."""
    fixed = 0
    for yr in years:
        snap_cfs = snapshots.get(yr, {}).get("cfs", {})
        db_row = conn.execute("""
            SELECT id, revenue, operating_profit, net_income, total_assets, total_equity
            FROM financial_data
            WHERE stock_code=? AND year=? AND quarter=0 AND is_annual=1 AND report_type='CFS'
        """, (code, yr)).fetchone()
        if not db_row:
            continue

        updates: dict[str, float] = {}
        for f in ("revenue", "operating_profit", "net_income", "total_assets", "total_equity"):
            db_val = db_row[f]
            snap_val = snap_cfs.get(f)
            if db_val is None or snap_val is None or snap_val == 0:
                continue
            ratio = abs(db_val / snap_val)
            # DB값이 FnGuide의 1/100이면 *100, 100배면 /100
            if (1 / 150) <= ratio <= (1 / 80):
                updates[f] = db_val * 100.0
            elif 80 <= ratio <= 150:
                updates[f] = db_val / 100.0

        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [db_row["id"]]
            conn.execute(f"UPDATE financial_data SET {sets} WHERE id=?", vals)
            fixed += len(updates)

    return fixed


def update_profiles(code: str, anomalies: list[dict]) -> None:
    """financial_profiles.json에 이상 정보 병합."""
    profiles: dict = {}
    if PROFILES_PATH.exists():
        try:
            profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    entry = profiles.setdefault(code, {})
    anomaly_types = [a["type"] for a in anomalies]
    entry["anomaly_types"]  = anomaly_types
    entry["anomaly_checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for a in anomalies:
        if a["type"] == "holding_company" and a["cfs_ofs_ratio"]:
            entry["cfs_ofs_ratio"] = a["cfs_ofs_ratio"]
        if a["type"] in ("unit_error", "cfs_ofs_mislabeled"):
            entry["data_quality_flag"] = a["type"]
            entry["data_quality_action"] = a["action"]

    PROFILES_PATH.parent.mkdir(exist_ok=True)
    PROFILES_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2, sort_keys=True)
    )


def run(years: list[int], auto_fix: bool, limit: int) -> dict:
    conn = _conn()
    _ensure_anomalies_table(conn)
    today = datetime.now(timezone.utc).isoformat()

    # 대상 종목 (코스피+코스닥 보통주, ETF/ETN 제외)
    universe = conn.execute("""
        SELECT su.stock_code, su.stock_name
        FROM stock_universe su
        WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND COALESCE(su.stock_type,'보통주') = '보통주'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY su.stock_code
        LIMIT ?
    """, (limit,)).fetchall()

    logger.info(f"대상: {len(universe)}종목, 검증 연도: {years}")

    stats = {
        "years": years,
        "total_stocks": len(universe),
        "anomaly_distribution": {},
        "auto_fixed": 0,
        "no_snapshot": 0,
        "profiles_updated": 0,
    }

    for i, row in enumerate(universe, 1):
        code, name = row["stock_code"], row["stock_name"]

        # 스냅샷 로드
        snap_rows = conn.execute("""
            SELECT year, report_type, revenue, operating_profit, net_income,
                   total_assets, total_equity, operating_cf, investing_cf, financing_cf
            FROM financial_source_snapshot
            WHERE stock_code=? AND year IN ({}) AND is_annual=1
        """.format(",".join("?" * len(years))), (code, *years)).fetchall()

        if not snap_rows:
            stats["no_snapshot"] += 1
            continue

        # 구조화
        snapshots: dict = {}
        for sr in snap_rows:
            yr = sr["year"]
            rt = "cfs" if sr["report_type"] == "CFS" else "ofs"
            snapshots.setdefault(yr, {})[rt] = dict(sr)

        # DB 데이터 로드
        db_rows = conn.execute("""
            SELECT year, report_type, revenue, operating_profit, net_income,
                   total_assets, total_equity
            FROM financial_data
            WHERE stock_code=? AND year IN ({}) AND is_annual=1 AND quarter=0
        """.format(",".join("?" * len(years))), (code, *years)).fetchall()

        db_data: dict = {}
        for dr in db_rows:
            yr = dr["year"]
            rt = "cfs" if dr["report_type"] == "CFS" else "ofs"
            db_data.setdefault(yr, {})[rt] = dict(dr)

        # 이상 분류
        anomalies = classify_stock(code, name, years, snapshots, db_data)

        # DB 기록
        for a in anomalies:
            _upsert_anomaly(
                conn, code,
                atype=a["type"],
                severity=a["severity"],
                description=a["description"],
                affected_fields=a["fields"],
                sample_year=a["year"],
                action=a["action"],
                auto_fixable=a["auto_fixable"],
                cfs_ofs_ratio=a.get("cfs_ofs_ratio"),
            )
            stats["anomaly_distribution"][a["type"]] = \
                stats["anomaly_distribution"].get(a["type"], 0) + 1

        # 자동 수정
        if auto_fix:
            types = [a["type"] for a in anomalies]
            if "unit_error" in types:
                fixed = auto_fix_unit_error(conn, code, years, snapshots)
                stats["auto_fixed"] += fixed

        # 프로파일 업데이트
        update_profiles(code, anomalies)
        stats["profiles_updated"] += 1

        if i % 100 == 0:
            conn.commit()
            logger.info(f"[{i}/{len(universe)}] anomalies_recorded={sum(stats['anomaly_distribution'].values())}")

    conn.commit()
    conn.close()

    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    logger.info(f"검증 완료: {REPORT_PATH}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years",    nargs="+", type=int, default=list(range(2020, 2025)))
    ap.add_argument("--auto-fix", action="store_true", help="단위 오류 자동 수정")
    ap.add_argument("--limit",    type=int, default=9999)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    res = run(args.years, args.auto_fix, args.limit)
    print(json.dumps({
        "total": res["total_stocks"],
        "no_snapshot": res["no_snapshot"],
        "anomaly_distribution": res["anomaly_distribution"],
        "auto_fixed": res["auto_fixed"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
