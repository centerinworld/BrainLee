"""Immutable backtest run selection and machine-derived verification gates."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from config import IS_POSTGRES
from db_compat import connect_primary_db


DB_PATH = Path(__file__).resolve().parent / "stock.db"
STATUS_ORDER = {
    "legacy": 0,
    "execution_strict": 1,
    "point_in_time_approx": 2,
    "point_in_time_verified": 3,
    "forward_validated": 4,
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    if IS_POSTGRES:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS run_verification_artifacts (
            run_hash TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            passed INTEGER NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            artifact_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_hash, artifact_type)
        );
        CREATE TABLE IF NOT EXISTS selected_run_registry (
            strategy TEXT NOT NULL,
            report_type TEXT NOT NULL,
            run_hash TEXT NOT NULL,
            selected_at TEXT NOT NULL,
            selected_by TEXT NOT NULL DEFAULT 'system',
            note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (strategy, report_type)
        );
        CREATE INDEX IF NOT EXISTS ix_selected_run_hash
          ON selected_run_registry(run_hash);

        CREATE TABLE IF NOT EXISTS backtest_run_sets (
            suite_hash TEXT PRIMARY KEY,
            strategy TEXT NOT NULL,
            report_type TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backtest_run_set_members (
            suite_hash TEXT NOT NULL,
            period_label TEXT NOT NULL,
            run_hash TEXT NOT NULL,
            PRIMARY KEY (suite_hash, period_label),
            UNIQUE (suite_hash, run_hash)
        );
        CREATE INDEX IF NOT EXISTS ix_run_set_member_hash
          ON backtest_run_set_members(run_hash);
        """
    )


def source_snapshot(conn: sqlite3.Connection) -> dict:
    """데이터 스냅샷 지문. 같은 run-set(6기간) 구성 요소가 동일 데이터 시대에서
    나왔는지 판별하는 용도 — 대규모 백필/재적재를 구분하는 것이 목적이지,
    장중 분단위 시세 틱(1분마다 갱신되는 today 행)까지 구분하는 것이 목적이 아니다.

    2026-07-16 수정: COUNT(*)/MAX(created_at)를 그대로 쓰면 장중 실시간 수집
    (매 1분 KIS 현재가 수집)에 의해 같은 세션 내 순차 실행한 6개 백테스트도
    거의 항상 다른 지문을 받아 register_run_set()이 "one source snapshot" 계약을
    만족 못 하는 문제가 실측됨. 오늘 날짜(당일 진행 중인 마지막 거래일) 행은
    지문에서 제외하고, 그 이전(확정된 과거 거래일)까지의 데이터로만 지문을 계산해
    분단위 흔들림에 안정적으로 만든다 — 실제 백필/재적재(과거 구간 값 변경)는
    여전히 감지된다.
    """
    def one(sql: str, params: tuple = ()):
        row = conn.execute(sql, params).fetchone()
        return list(row) if row else []
    today = datetime.now().date().isoformat()
    payload = {
        "price_history": one(
            "SELECT COUNT(*),MIN(date),MAX(date) FROM price_history WHERE date<?", (today,)
        ),
        "financial_data": one("SELECT COUNT(*),MAX(year),MAX(quarter) FROM financial_data"),
        "security_master": one("SELECT COUNT(*),MAX(substr(updated_at,1,10)) FROM security_master_history")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='security_master_history'").fetchone()
        else [0, None],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return {"fingerprint": hashlib.sha256(canonical.encode()).hexdigest()[:16], "datasets": payload}


def canonical_hash(spec: dict) -> str:
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def register_artifact(
    run_hash: str,
    artifact_type: str,
    passed: bool,
    details: dict,
    db_path: Path | str = DB_PATH,
) -> dict:
    conn = connect_primary_db(timeout=60)
    ensure_schema(conn)
    details_json = json.dumps(details, sort_keys=True, ensure_ascii=False, default=str)
    artifact_hash = hashlib.sha256(details_json.encode()).hexdigest()[:16]
    conn.execute(
        """
        INSERT INTO run_verification_artifacts
        (run_hash,artifact_type,passed,details_json,artifact_hash,created_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(run_hash,artifact_type) DO UPDATE SET
          passed=excluded.passed, details_json=excluded.details_json,
          artifact_hash=excluded.artifact_hash, created_at=excluded.created_at
        """,
        (run_hash, artifact_type, int(passed), details_json, artifact_hash, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {"artifact_type": artifact_type, "passed": bool(passed), "artifact_hash": artifact_hash}


def _spec_for_hash(conn: sqlite3.Connection, run_hash: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT s.*,r.status run_status,r.start_date,r.end_date
        FROM backtest_run_specs s
        JOIN backtest_runs r ON r.run_id=s.run_id
        WHERE s.run_hash=?
        ORDER BY s.created_at DESC LIMIT 1
        """,
        (run_hash,),
    ).fetchone()


def derive_status(conn: sqlite3.Connection, run_hash: str) -> dict:
    ensure_schema(conn)
    spec = _spec_for_hash(conn, run_hash)
    if not spec:
        suite = conn.execute(
            "SELECT * FROM backtest_run_sets WHERE suite_hash=?", (run_hash,)
        ).fetchone()
        if suite:
            members = conn.execute(
                """SELECT period_label,run_hash FROM backtest_run_set_members
                   WHERE suite_hash=? ORDER BY period_label""", (run_hash,)
            ).fetchall()
            statuses = [derive_status(conn, member["run_hash"]) for member in members]
            rank = min((item.get("status_rank", 0) for item in statuses), default=0)
            status = next(name for name, value in STATUS_ORDER.items() if value == rank)
            price_integrity = bool(statuses) and all(
                item.get("gates", {}).get("price_integrity", True) for item in statuses
            )
            return {
                "run_hash": run_hash, "suite_hash": run_hash, "strategy": suite["strategy"],
                "status": status, "status_rank": rank, "is_suite": True,
                "gates": {
                    "run_spec": bool(statuses),
                    "completed": bool(statuses) and all(item.get("gates", {}).get("completed") for item in statuses),
                    "single_suite_identity": True,
                    "all_components_same_or_higher_status": bool(statuses),
                    "price_integrity": price_integrity,
                },
                "reasons": sorted(
                    {reason for item in statuses for reason in item.get("reasons", [])}
                    | ({"price_integrity"} if not price_integrity else set())
                ),
                "components": [
                    {"period_label": member["period_label"], "run_hash": member["run_hash"],
                     "status": item["status"]}
                    for member, item in zip(members, statuses)
                ],
            }
    if not spec:
        return {"run_hash": run_hash, "status": "legacy", "gates": {"run_spec": False}, "reasons": ["missing_run_spec"]}
    artifacts = {
        r["artifact_type"]: {"passed": bool(r["passed"]), "details": json.loads(r["details_json"] or "{}")}
        for r in conn.execute("SELECT * FROM run_verification_artifacts WHERE run_hash=?", (run_hash,))
    }
    execution = bool(
        spec["run_status"] == "done"
        and spec["execution_timing"] == "next_open"
        and spec["signal_timing"] == "close_D"
        and spec["fee_model"]
        and artifacts.get("execution_contract", {}).get("passed")
        and artifacts.get("cash_reconciliation", {}).get("passed")
        and artifacts.get("price_integrity", {"passed": True}).get("passed")
    )
    universe = str(spec["universe_version"] or "").lower()
    # 2026-08-12: 시총 필터 자체를 쓰지 않는 전략(market_cap_mode='not_applicable')은
    # "시총이 정확한 PIT 값인가"라는 질문 자체가 성립하지 않는다. 이를 구분하지 않고
    # 무조건 point_in_time_coverage 아티팩트(항상 passed=False로 등록됨, asof_mktcap=False
    # 호출 시 backtest.py _register_execution_artifacts가 그렇게 기록)를 요구하면
    # 시총 무관 전략(예: contract_momentum)이 영구히 rank<=2에 갇히는 버그가 있었음.
    # market_cap_not_applicable=True면 시총 PIT 게이트를 스킵하고 execution만으로 승격.
    market_cap_not_applicable = spec["market_cap_mode"] == "not_applicable"
    pit_exact_spec = spec["market_cap_mode"] == "pit" and "approx" not in universe and "current" not in universe
    pit_artifact = artifacts.get("point_in_time_coverage", {})
    pit_pass = bool(
        execution and (
            market_cap_not_applicable
            or (pit_exact_spec and pit_artifact.get("passed"))
        )
    )
    pit_approx = bool(execution and not pit_pass and spec["market_cap_mode"] in {"pit", "asof_approx"})
    forward = bool(pit_pass and artifacts.get("forward_validation", {}).get("passed"))
    status = (
        "forward_validated" if forward
        else "point_in_time_verified" if pit_pass
        else "point_in_time_approx" if pit_approx
        else "execution_strict" if execution
        else "legacy"
    )
    gates = {
        "run_spec": True,
        "completed": spec["run_status"] == "done",
        "next_open": spec["execution_timing"] == "next_open",
        "fees_declared": bool(spec["fee_model"]),
        "execution_contract": bool(artifacts.get("execution_contract", {}).get("passed")),
        "cash_reconciliation": bool(artifacts.get("cash_reconciliation", {}).get("passed")),
        "price_integrity": bool(artifacts.get("price_integrity", {"passed": True}).get("passed")),
        "point_in_time_exact": pit_pass,
        "forward_validation": forward,
    }
    return {
        "run_hash": run_hash,
        "run_id": spec["run_id"],
        "strategy": spec["strategy"],
        "status": status,
        "status_rank": STATUS_ORDER[status],
        "gates": gates,
        "reasons": [key for key, passed in gates.items() if not passed],
        "artifacts": artifacts,
    }


def select_run(
    strategy: str,
    report_type: str,
    run_hash: str,
    selected_by: str = "system",
    note: str = "",
    db_path: Path | str = DB_PATH,
) -> dict:
    conn = connect_primary_db(timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    status = derive_status(conn, run_hash)
    if not status.get("run_id") and not status.get("is_suite"):
        conn.close()
        raise ValueError("run_hash has no completed run spec")
    if status.get("strategy") != strategy:
        conn.close()
        raise ValueError("strategy does not match run spec")
    conn.execute(
        """
        INSERT INTO selected_run_registry(strategy,report_type,run_hash,selected_at,selected_by,note)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(strategy,report_type) DO UPDATE SET
          run_hash=excluded.run_hash, selected_at=excluded.selected_at,
          selected_by=excluded.selected_by, note=excluded.note
        """,
        (strategy, report_type, run_hash, datetime.now().isoformat(timespec="seconds"), selected_by, note),
    )
    conn.commit()
    conn.close()
    return {"strategy": strategy, "report_type": report_type, **status}


def register_run_set(
    strategy: str,
    report_type: str,
    members: dict[str, str],
    db_path: Path | str = DB_PATH,
) -> dict:
    """Create an immutable suite identity for a multi-period report."""
    if not members or len(set(members.values())) != len(members):
        raise ValueError("run set requires unique period labels and run hashes")
    if report_type == "strategy_center":
        required = {"20.3~21.11", "21.12~22.10", "22.11~23.10", "23.11~24.12", "24.6~25.5", "25.6~26.3"}
        if set(members) != required:
            raise ValueError("strategy_center run set requires all six standard periods")
    conn = connect_primary_db(timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    component_specs = {}
    for label, run_hash in sorted(members.items()):
        status = derive_status(conn, run_hash)
        if not status.get("run_id") or status.get("strategy") != strategy:
            conn.close()
            raise ValueError(f"invalid component {label}: {run_hash}")
        params = json.loads(_spec_for_hash(conn, run_hash)["parameter_json"] or "{}")
        component_specs[label] = {
            "run_hash": run_hash, "status": status["status"],
            "source_snapshot": params.get("_source_snapshot"),
            "code_fingerprint": params.get("_code_fingerprint"),
        }
    snapshots = {json.dumps(value.get("source_snapshot"), sort_keys=True) for value in component_specs.values()}
    if len(snapshots) != 1:
        conn.close()
        raise ValueError("all run-set components must share one source snapshot")
    code_versions = {json.dumps(value.get("code_fingerprint"), sort_keys=True) for value in component_specs.values()}
    if len(code_versions) != 1:
        conn.close()
        raise ValueError("all run-set components must share one code fingerprint")
    manifest = {
        "strategy": strategy, "report_type": report_type,
        "members": component_specs, "source_snapshot": next(iter(component_specs.values())).get("source_snapshot"),
    }
    suite_hash = canonical_hash(manifest)
    manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR IGNORE INTO backtest_run_sets VALUES (?,?,?,?,?)",
        (suite_hash, strategy, report_type, manifest_json, now),
    )
    for label, run_hash in members.items():
        conn.execute(
            "INSERT OR IGNORE INTO backtest_run_set_members VALUES (?,?,?)",
            (suite_hash, label, run_hash),
        )
    conn.commit()
    result = derive_status(conn, suite_hash)
    conn.close()
    return result


def registry(
    db_path: Path | str = DB_PATH,
    report_type: str | None = None,
    *,
    include_verification: bool = True,
) -> list[dict]:
    conn = connect_primary_db(timeout=60)
    conn.row_factory = sqlite3.Row
    if include_verification:
        ensure_schema(conn)
    sql = "SELECT * FROM selected_run_registry"
    params: tuple = ()
    if report_type:
        sql += " WHERE report_type=?"
        params = (report_type,)
    sql += " ORDER BY strategy,report_type"
    rows = [dict(r) for r in conn.execute(sql, params)]
    if include_verification:
        for row in rows:
            row["verification"] = derive_status(conn, row["run_hash"])
    conn.close()
    return rows
