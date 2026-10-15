"""strategy_research_guard.py — 전략 연구 정적 요약의 fail-closed 검증 (2026-10-11 신설).

문제
----
`GET /api/backtest/strategy-research/summary` 는 정적 파일
`research_outputs/strategy_research_summary.json` 을 그대로 내보낸다. 그 파일은
`dataset.snapshot_table` 을 **존재하지 않는** `strategy_feature_snapshot_stage` 로
명시하고, 어떤 run spec / run hash 도 달고 있지 않다. 그런데 응답 소비자는
`evaluation.ml_top20.avg_forward_ret_12m`(12개월 OOS 평균수익) 같은 값을
전략 선택·승격의 근거로 읽는다. 즉 **증명되지 않은 자기 주장이 수익률로 표시**된다.

계약 (Planner 확정, 2026-10-11)
-------------------------------
1. canonical 은 `strategy_feature_snapshot` **단일 고정**. 형제 테이블
   (`_stage`, `_pit_v2`, `_legacy_*`, `_rebuild_*`)은 참조 금지.
2. 정적 요약의 생성시각·snapshot max date·row count·distinct stock count 를
   라이브 canonical 및 `backtest_run_specs` 와 대조한다.
3. 불일치/부재/PIT 증명 불가 중 하나라도 있으면
   `strategy_research_status="not_usable"` + 사유 코드를 반환하고
   순위·수익률을 매수/승격 근거로 노출하지 않는다.

설계 원칙
--------
- **fail-closed**: 판정 대상 하나라도 확인 못 하면 `not_usable`. "모르면 통과"는 없다.
- **순수 함수**: `evaluate_strategy_research()` 는 DB·파일을 만지지 않는다. canonical
  통계와 run-spec 조회 결과를 인자로 받는다 → 라이브 DB 없이 회귀 테스트 가능.
- **사유 분리**: "수치가 canonical 과 일치하는데 이름표만 틀린 경우"(`dataset_table_missing`
  + `counts_match: true`)와 "수치가 실제로 어긋난 경우"(`counts_mismatch`)를 뭉개지 않는다.
- **읽기 전용**: 이 모듈은 SELECT 만 한다. DDL·DML 없음.
"""

from __future__ import annotations

import calendar
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

CANONICAL_SNAPSHOT_TABLE = "strategy_feature_snapshot"
RUN_SPEC_TABLE = "backtest_run_specs"

STATUS_USABLE = "usable"
STATUS_NOT_USABLE = "not_usable"

# 스냅샷은 한국 시장 데이터다 → 기준일 경계는 KST 자정으로 해석한다.
KST = timezone(timedelta(hours=9))

# 요청 payload 안에서 run spec 연결고리를 찾을 때 보는 키(중첩 포함).
RUN_HASH_KEYS = ("run_hash", "run_spec_hash", "strategy_spec_hash", "spec_hash")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def add_months(yyyymmdd: str, months: int) -> str | None:
    """`YYYY-MM-DD` 에 개월을 더한다(일자는 그 달의 말일로 클램프). 형식이 아니면 None."""
    try:
        y, m, d = (int(x) for x in str(yyyymmdd).split("-")[:3])
    except (TypeError, ValueError):
        return None
    if not (1 <= m <= 12):
        return None
    total = y * 12 + (m - 1) + months
    y2, m2 = divmod(total, 12)
    m2 += 1
    d2 = min(d, calendar.monthrange(y2, m2)[1])
    return f"{y2:04d}-{m2:02d}-{d2:02d}"


def extract_run_hash(payload: dict | None) -> str | None:
    """payload 에서 run spec / run hash 후보를 찾는다. 없으면 None(= 증명 없음)."""
    if not isinstance(payload, dict):
        return None
    for container in (payload, payload.get("run_spec"), payload.get("evaluation"),
                      payload.get("dataset"), payload.get("methodology")):
        if not isinstance(container, dict):
            continue
        for key in RUN_HASH_KEYS:
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def static_summary_meta(path, raw_text: str | None = None, parse_error: str | None = None) -> dict:
    """정적 요약 파일의 provenance(존재·파싱·해시·mtime). 파일을 새로 읽지 않는다."""
    p = Path(path)
    exists = p.exists()
    mtime = None
    if exists:
        try:
            import datetime as _dt

            mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat()
        except OSError:
            mtime = None
    return {
        "path": str(p),
        "exists": exists,
        "parseable": bool(exists and raw_text is not None and parse_error is None),
        "parse_error": parse_error,
        "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else None,
        "mtime": mtime,
        "bytes": len(raw_text.encode("utf-8")) if raw_text else None,
    }


# ---------------------------------------------------------------------------
# 라이브 canonical 읽기 (읽기 전용)
# ---------------------------------------------------------------------------
def _table_exists(cur, name: str | None) -> bool:
    if not name:
        return False
    row = cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def extract_declared_table(payload: dict | None) -> str | None:
    """정적 요약이 주장하는 스냅샷 테이블명. 없으면 None."""
    if not isinstance(payload, dict):
        return None
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        return None
    value = dataset.get("snapshot_table")
    return value.strip() if isinstance(value, str) and value.strip() else None


def extract_train_end_date(payload: dict | None) -> str | None:
    """정적 요약이 주장하는 train/test 경계일. 없으면 None."""
    if not isinstance(payload, dict):
        return None
    evaluation = payload.get("evaluation")
    if not isinstance(evaluation, dict):
        return None
    value = evaluation.get("train_end_date")
    return value.strip() if isinstance(value, str) and value.strip() else None


def collect_canonical_stats(conn, declared_table: str | None, train_end_date: str | None) -> dict:
    """라이브 canonical(`strategy_feature_snapshot`) 통계를 읽는다. 예외는 error 필드로 흡수."""
    stats = {
        "canonical_table": CANONICAL_SNAPSHOT_TABLE,
        "canonical_table_exists": False,
        "declared_table": declared_table,
        "declared_table_exists": False,
        "rows": None,
        "min_date": None,
        "max_date": None,
        "stock_count": None,
        "month_count": None,
        "label_3x_6m_rows": None,
        "label_3x_12m_rows": None,
        "train_rows_12m": None,
        "test_rows_12m": None,
        "train_end_date": train_end_date,
        "max_label_12m_date": None,
        "immature_label_12m_rows": None,
        "maturity_boundary": None,
        "error": None,
    }
    try:
        cur = conn.cursor()
        stats["declared_table_exists"] = _table_exists(cur, declared_table)
        stats["canonical_table_exists"] = _table_exists(cur, CANONICAL_SNAPSHOT_TABLE)
        if not stats["canonical_table_exists"]:
            return stats

        row = cur.execute(
            "SELECT count(*), min(snapshot_date), max(snapshot_date), "
            "count(distinct stock_code), count(distinct substr(snapshot_date,1,7)), "
            "sum(CASE WHEN label_3x_6m IS NOT NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN label_3x_12m IS NOT NULL THEN 1 ELSE 0 END), "
            "max(CASE WHEN label_3x_12m IS NOT NULL THEN snapshot_date END) "
            "FROM strategy_feature_snapshot"
        ).fetchone()
        (
            stats["rows"], stats["min_date"], stats["max_date"], stats["stock_count"],
            stats["month_count"], stats["label_3x_6m_rows"], stats["label_3x_12m_rows"],
            stats["max_label_12m_date"],
        ) = tuple(row)

        if train_end_date:
            split = cur.execute(
                "SELECT sum(CASE WHEN label_3x_12m IS NOT NULL AND snapshot_date <= ? THEN 1 ELSE 0 END), "
                "sum(CASE WHEN label_3x_12m IS NOT NULL AND snapshot_date > ? THEN 1 ELSE 0 END) "
                "FROM strategy_feature_snapshot",
                (train_end_date, train_end_date),
            ).fetchone()
            stats["train_rows_12m"], stats["test_rows_12m"] = (int(split[0] or 0), int(split[1] or 0))

        boundary = add_months(stats["max_date"], -12) if stats["max_date"] else None
        stats["maturity_boundary"] = boundary
        if boundary:
            stats["immature_label_12m_rows"] = int(cur.execute(
                "SELECT count(*) FROM strategy_feature_snapshot "
                "WHERE label_3x_12m IS NOT NULL AND snapshot_date > ?",
                (boundary,),
            ).fetchone()[0] or 0)
    except Exception as exc:  # noqa: BLE001  (fail-closed 로 흡수)
        stats["error"] = f"{type(exc).__name__}: {exc}"
    return stats


def resolve_run_spec(conn, run_hash: str | None) -> dict:
    """payload 의 run hash 를 `backtest_run_specs` 에서 확인한다(읽기 전용)."""
    out = {
        "required": bool(run_hash),
        "run_hash": run_hash,
        "found": False,
        "table_rows": None,
        "error": None,
    }
    try:
        cur = conn.cursor()
        if _table_exists(cur, RUN_SPEC_TABLE):
            out["table_rows"] = int(cur.execute(
                f"SELECT count(*) FROM {RUN_SPEC_TABLE}"
            ).fetchone()[0])
        else:
            out["error"] = f"{RUN_SPEC_TABLE} table not found"
            return out
        if run_hash:
            out["found"] = bool(cur.execute(
                f"SELECT run_hash FROM {RUN_SPEC_TABLE} WHERE run_hash=? LIMIT 1", (run_hash,)
            ).fetchone())
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# ---------------------------------------------------------------------------
# 판정 (순수 함수)
# ---------------------------------------------------------------------------
def parse_timestamp(value):
    """타임존 **포함** ISO-8601 생성시각을 파싱한다.

    반환 `(datetime, None)` 또는 `(None, 사유코드)`.
    - naive(타임존 없음)는 `generated_at_naive_timezone` 으로 거부한다 — KST/UTC 를
      추정하면 기준일 경계 비교가 조용히 뒤집힐 수 있다(fail-closed).
    """
    if not isinstance(value, str) or not value.strip():
        return None, "generated_at_unparsable"
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, "generated_at_unparsable"
    if parsed.tzinfo is None:
        return None, "generated_at_naive_timezone"
    return parsed, None


def parse_kst_date(value):
    """`YYYY-MM-DD` 를 KST 자정 datetime 으로. 실패하면 None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()[:10]).replace(tzinfo=KST)
    except ValueError:
        return None


def _check(checks: list, reasons: list, name: str, ok: bool, detail, reason: str | None = None) -> bool:
    """검사 결과를 기록하고, 실패면 사유 코드를 하나만 남긴다(중복 코드 방지)."""
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        reasons.append(reason or name)
    return bool(ok)


def evaluate_strategy_research(payload, stats, meta, run_spec) -> dict:
    """정적 요약을 라이브 canonical·run spec·PIT 증거와 대조한다.

    반환: {"status": "usable"|"not_usable", "reasons": [코드...], "evidence": {...}}
    `payload` 가 None 이면 "정적 요약이 없다"는 뜻이며 그 자체가 차단 사유다.
    """
    checks: list = []
    reasons: list = []
    payload = payload if isinstance(payload, dict) else None
    stats = stats if isinstance(stats, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    run_spec = run_spec if isinstance(run_spec, dict) else {}

    # --- 0. 정적 요약 자체 ---------------------------------------------------
    if not meta.get("exists"):
        reasons.append("static_summary_missing")
    elif not meta.get("parseable"):
        reasons.append("static_summary_unparsable")
    if payload is None:
        if "static_summary_missing" not in reasons and "static_summary_unparsable" not in reasons:
            reasons.append("static_summary_missing")

    dataset = payload.get("dataset") if payload else None
    dataset = dataset if isinstance(dataset, dict) else {}
    evaluation = payload.get("evaluation") if payload else None
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    declared_table = dataset.get("snapshot_table")
    declared_table = declared_table.strip() if isinstance(declared_table, str) else None
    declared_max_date = payload.get("latest_snapshot_date") if payload else None
    declared_generated_at = payload.get("generated_at") if payload else None
    train_end_date = evaluation.get("train_end_date")

    # --- 1. canonical 읽기 가능 여부 ----------------------------------------
    if stats.get("error"):
        reasons.append("canonical_unreadable")
    canonical_ok = not stats.get("error") and bool(stats.get("canonical_table_exists"))
    if not stats.get("error") and not stats.get("canonical_table_exists"):
        reasons.append("canonical_table_missing")

    # --- 2. 생성시각 ---------------------------------------------------------
    # 존재 여부만 보면 "언제 만들어졌든 통과"다. 타임존 포함 파싱 + 기준일 대조까지 한다.
    _check(checks, reasons, "generated_at_present",
           isinstance(declared_generated_at, str) and bool(declared_generated_at.strip()),
           declared_generated_at, reason="generated_at_missing")

    generated_ts, generated_err = parse_timestamp(declared_generated_at)
    if declared_generated_at is not None:
        _check(checks, reasons, "generated_at_parsable", generated_err is None,
               {"value": declared_generated_at, "error": generated_err},
               reason=generated_err or "generated_at_unparsable")

    # 기준일은 canonical max_date 를 우선하고(라이브 진실), 없으면 정적 주장을 쓴다.
    compare_date = stats.get("max_date") or declared_max_date
    generated_after_snapshot = None
    if generated_ts is not None:
        boundary = parse_kst_date(compare_date)
        if boundary is None:
            reasons.append("snapshot_date_unparsable")
        else:
            generated_after_snapshot = generated_ts >= boundary
            _check(
                checks, reasons, "generated_at_after_snapshot", generated_after_snapshot,
                {"generated_at": generated_ts.isoformat(),
                 "generated_at_kst": generated_ts.astimezone(KST).isoformat(),
                 "snapshot_boundary_kst": boundary.isoformat(),
                 "compared_against": compare_date},
                reason="generated_at_before_snapshot_date",
            )

    # --- 3. 표기 테이블 ------------------------------------------------------
    _check(checks, reasons, "dataset_table_declared", bool(declared_table), declared_table,
           reason="dataset_table_declared_missing")

    table_missing = None
    table_not_canonical = None
    if declared_table and canonical_ok:
        table_missing = not bool(stats.get("declared_table_exists"))
        table_not_canonical = declared_table != CANONICAL_SNAPSHOT_TABLE
        _check(checks, reasons, "dataset_table_exists", not table_missing, declared_table,
               reason="dataset_table_missing")
        _check(checks, reasons, "dataset_table_canonical", not table_not_canonical,
               {"declared": declared_table, "canonical": CANONICAL_SNAPSHOT_TABLE},
               reason="dataset_table_not_canonical")

    # --- 4. 수치 대조 --------------------------------------------------------
    metric_map = (
        ("snapshot_rows", "rows"),
        ("snapshot_months", "month_count"),
        ("stocks_covered", "stock_count"),
        ("label_3x_6m_rows", "label_3x_6m_rows"),
        ("label_3x_12m_rows", "label_3x_12m_rows"),
        ("train_rows_12m", "train_rows_12m"),
        ("test_rows_12m", "test_rows_12m"),
    )
    mismatches: dict = {}
    counts_match = None
    if payload is not None and canonical_ok and not stats.get("error"):
        if not (isinstance(train_end_date, str) and train_end_date.strip()):
            reasons.append("train_end_date_missing")
        else:
            counts_match = True
            for static_key, db_key in metric_map:
                static_val = dataset.get(static_key)
                db_val = stats.get(db_key)
                if db_val is None or static_val != db_val:
                    counts_match = False
                    mismatches[static_key] = {"static": static_val, "canonical": db_val}
            _check(checks, reasons, "counts_consistent", bool(counts_match),
                   {"mismatches": mismatches}, reason="counts_mismatch")

    # --- 5. snapshot 기준일 --------------------------------------------------
    snapshot_date_match = None
    if payload is not None and canonical_ok and not stats.get("error"):
        snapshot_date_match = declared_max_date == stats.get("max_date")
        _check(checks, reasons, "snapshot_date_consistent", bool(snapshot_date_match),
               {"static": declared_max_date, "canonical": stats.get("max_date")},
               reason="snapshot_date_mismatch")

    # --- 6. PIT 증명 ---------------------------------------------------------
    pit = {
        "verified": False,
        "declared_point_in_time_universe": dataset.get("point_in_time_universe"),
        "max_label_12m_date": stats.get("max_label_12m_date"),
        "maturity_boundary": stats.get("maturity_boundary"),
        "immature_label_12m_rows": stats.get("immature_label_12m_rows"),
        "label_3x_12m_rows": stats.get("label_3x_12m_rows"),
        "detail": None,
    }
    if canonical_ok and not stats.get("error"):
        boundary = stats.get("maturity_boundary") or add_months(stats.get("max_date"), -12)
        pit["maturity_boundary"] = boundary
        max_label = stats.get("max_label_12m_date")
        immature = stats.get("immature_label_12m_rows")
        labelled = stats.get("label_3x_12m_rows")
        if dataset.get("point_in_time_universe") is not True:
            pit["detail"] = "정적 요약이 point-in-time universe availability를 증명하지 않는다"
            _check(checks, reasons, "pit_availability", False, pit["detail"],
                   reason="pit_availability_unproven")
        elif not boundary or not labelled:
            pit["detail"] = "canonical 에 12개월 라벨 행 또는 기준일이 없다"
        elif max_label is None:
            pit["detail"] = "12개월 라벨 행은 있는데 최대 라벨 기준일을 읽지 못했다"
        elif max_label > boundary:
            pit["detail"] = f"최대 라벨 기준일 {max_label} > 성숙 경계 {boundary} (look-ahead 의심)"
        elif immature:
            pit["detail"] = f"성숙 경계를 넘긴 라벨 행 {immature}건"
        else:
            pit["verified"] = True
            pit["detail"] = f"최대 라벨 기준일 {max_label} <= 성숙 경계 {boundary}"
        _check(checks, reasons, "pit_proof", pit["verified"], pit["detail"],
               reason="pit_proof_failed")

    # --- 7. run spec 증명 ----------------------------------------------------
    rs_required = bool(run_spec.get("required"))
    rs_error = run_spec.get("error")
    rs_found = bool(run_spec.get("found"))
    rs_reason = ("run_spec_unverifiable" if rs_error
                 else "run_spec_not_found" if rs_required
                 else "run_spec_proof_missing")
    _check(checks, reasons, "run_spec_proof",
           bool(rs_required and rs_found and not rs_error),
           {"run_hash": run_spec.get("run_hash"), "found": rs_found,
            "error": rs_error, "registry_rows": run_spec.get("table_rows")},
           reason=rs_reason)

    # --- 판정 ---------------------------------------------------------------
    reasons = list(dict.fromkeys(reasons))
    status = STATUS_USABLE if not reasons else STATUS_NOT_USABLE

    evidence = {
        "static_summary": meta,
        "declared_table": declared_table,
        "canonical_table": CANONICAL_SNAPSHOT_TABLE,
        "dataset_table_missing": table_missing,
        "dataset_table_not_canonical": table_not_canonical,
        "counts_match": counts_match,
        "count_mismatches": mismatches,
        "snapshot_date_match": snapshot_date_match,
        "declared_snapshot_date": declared_max_date,
        "generated_at_after_snapshot": generated_after_snapshot,
        "generated_at_kst": (generated_ts.astimezone(KST).isoformat() if generated_ts else None),
        "canonical": {k: v for k, v in stats.items()},
        "pit": pit,
        "run_spec": dict(run_spec),
        "checks": checks,
    }
    return {"status": status, "reasons": reasons, "evidence": evidence}


# ---------------------------------------------------------------------------
# 응답 반영 (순수 함수 — 입력 dict 를 변형하지 않는다)
# ---------------------------------------------------------------------------
def apply_strategy_research_verdict(payload: dict, verdict: dict) -> dict:
    """Apply verdict without mutating input; omit rankings/returns when not usable."""
    out = dict(payload)
    status = verdict.get("status") or STATUS_NOT_USABLE
    reasons = list(verdict.get("reasons") or [])
    out["strategy_research_status"] = status
    out["strategy_research_reasons"] = reasons
    out["strategy_research_evidence"] = verdict.get("evidence") or {}
    if status == STATUS_USABLE:
        out["strategy_research_notice"] = (
            "정적 전략 요약이 라이브 canonical·run spec·PIT 증거 대조를 통과했습니다."
        )
        return out

    # fail-closed: ranking/return-bearing keys must be absent, never null.
    # Keep the audit/provenance fields added above intact.
    out.pop("current_rankings", None)
    out.pop("evaluation", None)
    out["strategy_research_notice"] = (
        "정적 전략 요약(research_outputs/strategy_research_summary.json)이 라이브 canonical"
        f"({CANONICAL_SNAPSHOT_TABLE})·run spec·PIT 증거 대조를 통과하지 못해 순위·수익률을"
        " 매수·승격 근거로 노출하지 않습니다. 사유: " + (", ".join(reasons) or "unknown")
    )
    return out
