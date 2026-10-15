"""OFS 사업보고서 본문 표 후보 적용기 회귀 테스트 (계약 §4 수용 기준 1~10).

라이브 DB·원장을 건드리지 않는다 — 임시 sqlite 파일 픽스처와 임시 CSV/출력만 사용한다.
main()이 연결을 닫으므로 검증은 파일 재오픈으로 읽는다(라이브 readback과 같은 방식).
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
from pathlib import Path

from data_write_gate import ensure_canonical_schema

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "apply_ofs_doc_candidates_20261010", ROOT / "scripts" / "review" / "apply_ofs_doc_candidates_20261010.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CSV_COLS = ["stock_code", "year", "quarter", "fs", "field", "area", "group", "status", "cause", "candidate",
            "doc", "parse_reason", "fnguide_value", "verification_status", "fg_abs_diff", "fg_match_2m"]


def _open(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _conn(path):
    conn = _open(path)
    ensure_canonical_schema(conn)
    conn.execute("""CREATE TABLE financial_data (id INTEGER PRIMARY KEY, stock_code TEXT, year INT, quarter INT,
                    is_annual INT, report_type TEXT, data_source TEXT, revenue REAL, operating_profit REAL,
                    net_income REAL, total_assets REAL, total_liabilities REAL, total_equity REAL,
                    capital_stock REAL, eps REAL, bps REAL, dps REAL, roe REAL, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE cash_flow_data (id INTEGER PRIMARY KEY, stock_code TEXT, year INT, quarter INT,
                    is_annual INT, report_type TEXT, data_source TEXT, operating_cf REAL, investing_cf REAL,
                    financing_cf REAL, capex REAL, cash_end REAL, depreciation REAL, operating_cf_q REAL,
                    investing_cf_q REAL, financing_cf_q REAL, capex_q REAL, value_type TEXT, created_at TEXT,
                    updated_at TEXT)""")
    conn.execute("""CREATE TABLE financial_field_verification (stock_code TEXT, year INT, quarter INT,
                    report_type TEXT, field TEXT, db_value REAL, fnguide_value REAL, naver_value REAL,
                    status TEXT, cause TEXT, run_id TEXT)""")
    conn.execute("CREATE TABLE data_lock (stock_code TEXT, year INT, table_name TEXT, is_locked INT)")
    conn.execute("CREATE TABLE stock_collection_config (stock_code TEXT, config_key TEXT, config_value TEXT)")
    for log in ("financial_fix_log", "cashflow_fix_log"):
        conn.execute(f"""CREATE TABLE {log} (id INTEGER PRIMARY KEY, fixed_at TEXT, row_id INT, stock_code TEXT,
                        year INT, quarter INT, is_annual INT, report_type TEXT, field_name TEXT, old_value REAL,
                        new_value REAL, fix_rule TEXT, source TEXT, run_id TEXT)""")
    conn.execute("""CREATE TABLE data_fix_log (id INTEGER PRIMARY KEY, fixed_at TEXT, table_name TEXT, scope TEXT,
                    row_count INT, fix_rule TEXT, old_value_summary TEXT, new_value_summary TEXT, source TEXT,
                    run_id TEXT)""")
    conn.commit()
    return conn


def _csv(tmp_path, rows, name="candidates.csv"):
    p = tmp_path / name
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS)
        w.writeheader()
        for r in rows:
            row = {c: "" for c in CSV_COLS}
            row.update(r)
            w.writerow(row)
    return str(p)


def _row(code="241820", year=2022, field="revenue", candidate=37217226663.0, fnguide=37217000000.0,
         match="True", fs="OFS", quarter="0", doc="2022_20240314000576.zip", reason="section4_table"):
    return {"stock_code": code, "year": year, "quarter": quarter, "fs": fs, "field": field,
            "candidate": candidate, "doc": doc, "parse_reason": reason, "fnguide_value": fnguide,
            "fg_match_2m": match}


def _verify(conn, code, year, field, value, quarter=0):
    conn.execute("INSERT INTO financial_field_verification VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (code, year, quarter, "OFS", field, None, value, None, "미확인(DB 없음)", "DB 없음", "fixture"))
    conn.commit()


def _run(monkeypatch, db, tmp_path, rows, apply=False, name="out.json"):
    monkeypatch.setattr(mod, "_connect", lambda readonly: _open(db))
    argv = ["--input-csv", _csv(tmp_path, rows, name=name + ".csv"), "--output", str(tmp_path / name)]
    if apply:
        argv.append("--apply")
    mod.main(argv)
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


# ── 1·2) 기본 실행은 쓰지 않는다 ─────────────────────────────────────────────
def test_dry_run_plans_without_any_write(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    _verify(conn, "241820", 2022, "revenue", 37217000000.0)
    res = _run(monkeypatch, db, tmp_path, [_row()])
    chk = _open(db)
    assert chk.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 0
    assert chk.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 0
    assert chk.execute("SELECT COUNT(*) FROM canonical_financial_data").fetchone()[0] == 0
    assert chk.execute("SELECT COUNT(*) FROM data_fix_log").fetchone()[0] == 0
    assert res["mode"] == "dry_run" and res["db_write_statements"] == 0
    assert res["stats"]["planned_new_rows"] == 1 and res["stats"]["planned_financial_fields"] == 1
    assert res["run_id"] is None and res["write_path_not_executed"]


# ── 3) apply는 NULL 칸을 채우고 값·provenance를 readback ────────────────────
def test_apply_fills_null_cell_and_records_provenance(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, data_source,
                    revenue, operating_profit, net_income, total_assets, total_liabilities, total_equity)
                    VALUES (7,'241820',2022,0,1,'OFS','kis',NULL,NULL,NULL,NULL,NULL,NULL)""")
    conn.commit()
    _verify(conn, "241820", 2022, "revenue", 37217000000.0)
    res = _run(monkeypatch, db, tmp_path, [_row()], apply=True)
    chk = _open(db)
    assert res["stats"]["planned_updates"] == 1 and res["readback_mismatch"] == 0 and res["pass"] is True
    got = chk.execute("SELECT revenue, data_source, updated_at FROM financial_data WHERE id=7").fetchone()
    assert abs(got["revenue"] - 37217226663.0) < 0.5
    assert mod.TAG in got["data_source"] and got["updated_at"]
    assert chk.execute("SELECT COUNT(*) FROM financial_data_backup_ofs_doc_candidates_20261010").fetchone()[0] == 1
    log = chk.execute("SELECT field_name, new_value, source, run_id FROM financial_fix_log").fetchone()
    assert log["field_name"] == "revenue" and mod.TAG in log["source"] and "2022_20240314000576.zip" in log["source"]
    assert log["run_id"] == res["run_id"]
    assert chk.execute("SELECT COUNT(*) FROM data_fix_log").fetchone()[0] == 1
    assert abs(chk.execute("SELECT revenue FROM canonical_financial_data WHERE stock_code='241820'").fetchone()[0]
               - 37217226663.0) < 0.5
    assert res["canonical_sync"]["fd_synced"] == 1 and res["canonical_sync"]["pass"] is True


# ── 4) CSV true여도 런타임 FnGuide가 없거나 초과면 보류 ─────────────────────
def test_fresh_fnguide_missing_or_out_of_tolerance_holds(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    conn = _conn(db)
    conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, revenue)
                    VALUES (1,'241820',2022,0,1,'OFS',NULL)""")
    conn.commit()
    res = _run(monkeypatch, db, tmp_path, [_row()], apply=True)  # verification 없음
    assert res["stats"]["held_fnguide_changed_or_missing"] == 1 and res["stats"]["planned_updates"] == 0
    assert _open(db).execute("SELECT revenue FROM financial_data WHERE id=1").fetchone()[0] is None

    db2 = tmp_path / "b.db"
    conn2 = _conn(db2)
    conn2.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, revenue)
                     VALUES (1,'241820',2022,0,1,'OFS',NULL)""")
    conn2.commit()
    _verify(conn2, "241820", 2022, "revenue", 30_000_000_000.0)  # CSV와 200만원 초과 차이
    res2 = _run(monkeypatch, db2, tmp_path, [_row()], apply=True, name="out2.json")
    assert res2["stats"]["held_fnguide_changed_or_missing"] == 1
    assert _open(db2).execute("SELECT revenue FROM financial_data WHERE id=1").fetchone()[0] is None


# ── 5) 1,000배 스케일 행은 절대 적용하지 않는다 ─────────────────────────────
def test_scale_mismatch_row_is_never_applied(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    row = _row(code="065150", year=2023, field="operating_cf", candidate=-6816438608000.0,
               fnguide=-6779000000.0, match="False")
    res = _run(monkeypatch, db, tmp_path, [row], apply=True)
    chk = _open(db)
    assert res["stats"]["rejected_source_scale"] == 1
    assert res["stats"]["planned_updates"] == 0 and res["stats"]["planned_new_rows"] == 0
    assert chk.execute("SELECT COUNT(*) FROM cash_flow_data").fetchone()[0] == 0
    assert chk.execute("SELECT COUNT(*) FROM cashflow_fix_log").fetchone()[0] == 0
    # CSV상 true여도 재조회 FnGuide와 스케일이 1,000배 벌어지면 같은 이유로 거부
    db2 = tmp_path / "g.db"
    conn2 = _conn(db2)
    _verify(conn2, "065150", 2023, "operating_cf", -6779000000.0)
    res2 = _run(monkeypatch, db2, tmp_path,
                [_row(code="065150", year=2023, field="operating_cf", candidate=-6816438608000.0,
                      fnguide=-6779000000.0, match="True")], apply=True, name="out2.json")
    assert res2["stats"]["rejected_source_scale"] == 1
    assert _open(db2).execute("SELECT COUNT(*) FROM cash_flow_data").fetchone()[0] == 0


# ── 6) 기존 비NULL 불일치는 덮어쓰지 않는다 ─────────────────────────────────
def test_existing_different_value_is_held_not_overwritten(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, revenue)
                    VALUES (3,'241820',2022,0,1,'OFS',1000000000.0)""")
    conn.commit()
    _verify(conn, "241820", 2022, "revenue", 37217000000.0)
    res = _run(monkeypatch, db, tmp_path, [_row()], apply=True)
    assert res["stats"]["held_existing_value"] == 1 and res["stats"]["planned_updates"] == 0
    assert _open(db).execute("SELECT revenue FROM financial_data WHERE id=3").fetchone()[0] == 1000000000.0
    assert "기존" in res["decisions"][0]["note"]


# ── 7) 입력 유효성 실패는 fail-closed ───────────────────────────────────────
def test_invalid_inputs_fail_closed(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    _conn(db)
    rows = [_row(fs="CFS"), _row(quarter="2"), _row(field="capex"), _row(candidate="")]
    res = _run(monkeypatch, db, tmp_path, rows, apply=True)
    assert res["stats"]["invalid_input"] == 4
    assert res["stats"]["planned_updates"] == res["stats"]["planned_new_rows"] == 0
    assert all(d["decision"] == "invalid_input" for d in res["decisions"])
    assert _open(db).execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 0


# ── 8) 잠금·외화·비12월·단위오류·보호 칸은 쓰지 않는다 ──────────────────────
def test_safety_gates_block_writes(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    cases = [
        ("241820", 2020, "data_lock", "financial_data"),
        ("950210", 2021, "fs_quirk:reporting_currency", "USD"),
        ("123456", 2022, "fs_quirk:fiscal_month", "6"),
        ("654321", 2023, "fs_quirk:unit_scale_fixed", "1"),
    ]
    rows = []
    for code, year, key, val in cases:
        rows.append(_row(code=code, year=year, candidate=1e9, fnguide=1e9))
        _verify(conn, code, year, "revenue", 1e9)
        if key == "data_lock":
            conn.execute("INSERT INTO data_lock VALUES (?,?,?,1)", (code, year, val))
        else:
            conn.execute("INSERT INTO stock_collection_config VALUES (?,?,?)", (code, key, val))
    # 보호 칸: restated_annual_* fix log에 이미 기록된 (row, field)
    conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, revenue)
                    VALUES (9,'111111',2024,0,1,'OFS',NULL)""")
    conn.execute("""INSERT INTO financial_fix_log (id, fixed_at, row_id, stock_code, year, quarter, is_annual,
                    report_type, field_name, fix_rule, run_id) VALUES (1,'t',9,'111111',2024,0,1,'OFS','revenue',
                    'restated','restated_annual_20261009_212731')""")
    conn.commit()
    rows.append(_row(code="111111", year=2024, candidate=5e9, fnguide=5e9))
    _verify(conn, "111111", 2024, "revenue", 5e9)
    res = _run(monkeypatch, db, tmp_path, rows, apply=True)
    chk = _open(db)
    assert res["stats"]["excluded_by_safety_gate"] == 5
    assert res["stats"]["planned_updates"] == res["stats"]["planned_new_rows"] == 0
    assert chk.execute("SELECT COUNT(*) FROM financial_data WHERE id=9 AND revenue IS NOT NULL").fetchone()[0] == 0
    assert chk.execute("SELECT COUNT(*) FROM financial_fix_log WHERE id<>1").fetchone()[0] == 0
    assert chk.execute("SELECT COUNT(*) FROM financial_data WHERE id<>9").fetchone()[0] == 0


# ── 9) 정본 게이트 거부는 감사 결과로 남고 성공으로 위장되지 않는다 ──────────
def test_canonical_refusal_is_audited_not_masked(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    # 매출 10억 행에 영업이익 30억 후보 → 정본 게이트가 OPERATING_PROFIT_EXCEEDS_REVENUE로 거부
    conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type,
                    revenue, operating_profit) VALUES (5,'241820',2022,0,1,'OFS',1000000000.0,NULL)""")
    conn.commit()
    _verify(conn, "241820", 2022, "operating_profit", 3000000000.0)
    res = _run(monkeypatch, db, tmp_path,
               [_row(field="operating_profit", candidate=3000000000.0, fnguide=3000000000.0)], apply=True)
    chk = _open(db)
    assert res["stats"]["planned_updates"] == 1
    # 원장은 후보값으로 채워짐(되돌리지 않음)
    assert abs(chk.execute("SELECT operating_profit FROM financial_data WHERE id=5").fetchone()[0]
               - 3000000000.0) < 0.5
    cs = res["canonical_sync"]
    assert cs["refused"] == 1 and cs["fd_refused"] == 1 and cs["pass"] is False and cs["readback_pass"] is True
    assert cs["refused_sample"][0][-1] == "OPERATING_PROFIT_EXCEEDS_REVENUE"
    assert chk.execute("SELECT COUNT(*) FROM canonical_financial_data").fetchone()[0] == 0
    assert res["pass"] is False
    assert chk.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 1  # 감사 기록은 남는다


# ── 10) 결과 파일 카운터·_apply_noop 이름 규칙 ──────────────────────────────
def test_apply_noop_naming_preserves_first_result(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    _verify(conn, "241820", 2022, "revenue", 37217000000.0)
    first = _run(monkeypatch, db, tmp_path, [_row()], apply=True, name="apply.json")
    assert first["stats"]["planned_new_rows"] == 1
    second = _run(monkeypatch, db, tmp_path, [_row()], apply=True, name="apply_noop.json")
    # 두 번째 실행은 이미 값이 있으므로 noop(쓰기 0)
    assert second["stats"]["planned_new_rows"] == 0 and second["stats"]["planned_updates"] == 0
    assert second["stats"]["noop"] == 1
    chk = _open(db)
    assert chk.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 1
    assert (tmp_path / "apply.json").exists() and json.loads((tmp_path / "apply.json").read_text(encoding="utf-8"))["stats"]["planned_new_rows"] == 1


# ── 11) 연간 두 벌(quarter 0·4)은 둘 다 같은 값으로 채운다 ──────────────────
def test_annual_two_copies_filled_consistently(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    conn = _conn(db)
    for rid, q in ((11, 0), (12, 4)):
        conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, revenue)
                        VALUES (?, '241820', 2022, ?, 1, 'OFS', NULL)""", (rid, q))
    conn.commit()
    _verify(conn, "241820", 2022, "revenue", 37217000000.0)
    res = _run(monkeypatch, db, tmp_path, [_row()], apply=True)
    chk = _open(db)
    assert res["stats"]["planned_updates"] == 2 and res["readback_mismatch"] == 0
    vals = [r[0] for r in chk.execute("SELECT revenue FROM financial_data ORDER BY id").fetchall()]
    assert all(abs(v - 37217226663.0) < 0.5 for v in vals)
    # 정본에는 대표 행(quarter 0) 하나만 들어간다
    assert chk.execute("SELECT COUNT(*) FROM canonical_financial_data WHERE stock_code='241820'").fetchone()[0] == 1
    assert res["canonical_sync"]["fd_synced"] == 1 and res["canonical_sync"]["pass"] is True


# ── 12) CSV 스키마가 어긋나면 실행하지 않는다 ───────────────────────────────
def test_csv_schema_mismatch_aborts(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("stock_code,year\n241820,2022\n", encoding="utf-8")
    try:
        mod.main(["--input-csv", str(p), "--output", str(tmp_path / "o.json")])
    except SystemExit as e:
        assert "스키마" in str(e)
    else:
        raise AssertionError("스키마 오류는 SystemExit이어야 함")


# ── 13~17) 2026-10-10 Planner 검증: 다중필드 그룹은 신규 연간 행 1개로 병합 ──────
FIN6 = [("revenue", 37217226663.0), ("operating_profit", -8197077244.0), ("net_income", -4021844589.0),
        ("total_assets", 85806281084.0), ("total_liabilities", 54250890837.0), ("total_equity", 31555390247.0)]
CF3 = [("operating_cf", -1079189801.0), ("investing_cf", -20671152471.0), ("financing_cf", 9238752371.0)]


def _group_rows(pairs, code="241820", year=2022):
    return [_row(code=code, year=year, field=f, candidate=v, fnguide=v) for f, v in pairs]


def test_plan_groups_same_annual_key_cells_into_one_insert(tmp_path):
    """계획 단계에서 (table, 종목, 연도) 그룹당 insert 레코드 1개 — 칸별 레코드 금지(중복 연간 행 원인)."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    for f, v in FIN6 + CF3:
        _verify(conn, "241820", 2022, f, v)
    rows = mod.read_candidates(Path(_csv(tmp_path, _group_rows(FIN6 + CF3))))
    fg = {(r["stock_code"], int(r["year"]), r["field"]): r["candidate"] for r in rows}
    plan = mod.plan_cells(conn, rows, fg)
    assert len(plan["inserts"]) == 2, "그룹당 1개 레코드(financial_data·cash_flow_data)여야 한다"
    fd = [g for g in plan["inserts"] if g["table"] == "financial_data"][0]
    assert sorted(fd["cols"]) == sorted(c for c, _ in FIN6)
    assert plan["stats"]["planned_new_rows"] == 2 and plan["stats"]["planned_new_row_fields"] == 9
    assert plan["stats"]["merged_new_row_groups"] == 2
    # 칸별 결정(감사 단위)은 남고, 어느 행으로 병합됐는지 note에 표시된다
    ins_dec = [d for d in plan["decisions"] if d["decision"] == "planned_insert"]
    assert len(ins_dec) == 9
    assert [d["note"] for d in ins_dec if d["field"] == "revenue"] == ["신규 연간 행 1개로 병합(6칸) — "
                                                                      + ", ".join(sorted(c for c, _ in FIN6))]
    assert {d["merge_group"] for d in ins_dec} == {"financial_data:241820:2022", "cash_flow_data:241820:2022"}


def test_multifield_group_inserts_one_merged_row(monkeypatch, tmp_path):
    """6칸(재무) + 3칸(현금흐름)은 각각 1행으로 병합 — 같은 연간키에 중복 행을 만들지 않는다."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    for f, v in FIN6 + CF3:
        _verify(conn, "241820", 2022, f, v)
    res = _run(monkeypatch, db, tmp_path, _group_rows(FIN6 + CF3), apply=True)
    chk = _open(db)
    assert res["stats"]["planned_new_rows"] == 2 and res["stats"]["planned_new_row_fields"] == 9
    assert res["stats"]["merged_new_row_groups"] == 2 and res["stats"]["planned_updates"] == 0
    assert chk.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 1
    assert chk.execute("SELECT COUNT(*) FROM cash_flow_data").fetchone()[0] == 1
    fr = chk.execute(f"SELECT {','.join(c for c, _ in FIN6)}, data_source FROM financial_data").fetchone()
    assert all(abs(fr[i] - v) < 0.5 for i, (_, v) in enumerate(FIN6))
    assert mod.TAG in fr["data_source"]
    cr = chk.execute(f"SELECT {','.join(c for c, _ in CF3)} FROM cash_flow_data").fetchone()
    assert all(abs(cr[i] - v) < 0.5 for i, (_, v) in enumerate(CF3))
    assert res["readback_checked"] == 9 and res["readback_mismatch"] == 0
    assert res["applied"]["duplicate_annual_rows"] == 0 and res["pass"] is True
    # 감사: 칸 9개 = fix log 9행(그룹별 같은 row_id)
    assert chk.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 6
    assert chk.execute("SELECT COUNT(*) FROM cashflow_fix_log").fetchone()[0] == 3
    assert chk.execute("SELECT COUNT(DISTINCT row_id) FROM financial_fix_log").fetchone()[0] == 1
    assert chk.execute("SELECT COUNT(*) FROM data_fix_log").fetchone()[0] == 1
    assert "신규 2행" in chk.execute("SELECT old_value_summary FROM data_fix_log").fetchone()[0]
    # 정본(시그널이 읽는 경로)에도 신규 행이 들어간다
    assert chk.execute("SELECT COUNT(*) FROM canonical_financial_data WHERE stock_code='241820'").fetchone()[0] == 1
    assert abs(chk.execute("SELECT revenue FROM canonical_financial_data WHERE stock_code='241820'").fetchone()[0]
               - FIN6[0][1]) < 0.5
    assert chk.execute("SELECT COUNT(*) FROM canonical_cashflow_data WHERE stock_code='241820'").fetchone()[0] == 1
    assert res["canonical_sync"]["fd_synced"] == 1 and res["canonical_sync"]["cf_synced"] == 1
    assert res["canonical_sync"]["pass"] is True


def test_merged_row_fills_only_validated_cells(monkeypatch, tmp_path):
    """검증 통과 칸만 병합 행에 채운다 — 보류 칸은 행이 생겨도 비어 있어야 한다."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    pairs = [FIN6[0], FIN6[1], FIN6[3]]  # revenue, operating_profit, total_assets
    _verify(conn, "241820", 2022, "revenue", FIN6[0][1])
    _verify(conn, "241820", 2022, "total_assets", FIN6[3][1])
    _verify(conn, "241820", 2022, "operating_profit", 1.0)  # 재조회 FnGuide가 200만원 초과 → 보류
    res = _run(monkeypatch, db, tmp_path, _group_rows(pairs), apply=True)
    chk = _open(db)
    assert res["stats"]["held_fnguide_changed_or_missing"] == 1
    assert res["stats"]["planned_new_rows"] == 1 and res["stats"]["planned_new_row_fields"] == 2
    assert chk.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 1
    r = chk.execute("SELECT revenue, operating_profit, total_assets FROM financial_data").fetchone()
    assert abs(r["revenue"] - FIN6[0][1]) < 0.5 and abs(r["total_assets"] - FIN6[3][1]) < 0.5
    assert r["operating_profit"] is None
    assert chk.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 2


def test_existing_null_rows_updated_while_other_table_inserts_one_merged_row(monkeypatch, tmp_path):
    """기존 NULL 혼합: 있는 행은 UPDATE(연간 두 벌), 없는 테이블은 신규 1행 — 재실행은 idempotent."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    for rid, q in ((31, 0), (32, 4)):
        conn.execute("INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, "
                     "data_source, revenue, total_assets) VALUES (?, '462860', 2024, ?, 1, 'OFS', 'kis', NULL, NULL)",
                     (rid, q))
    conn.commit()
    pairs = [("revenue", 62657767124.0), ("total_assets", 115670733823.0)] + CF3
    for f, v in pairs:
        _verify(conn, "462860", 2024, f, v)
    res = _run(monkeypatch, db, tmp_path, _group_rows(pairs, code="462860", year=2024), apply=True)
    chk = _open(db)
    assert res["stats"]["planned_updates"] == 4, "연간 두 벌 × 2칸"
    assert res["stats"]["planned_new_rows"] == 1 and res["stats"]["planned_new_row_fields"] == 3
    assert res["stats"]["merged_new_row_groups"] == 1
    assert chk.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 2, "신규 재무 행이 생기면 안 된다"
    assert chk.execute("SELECT COUNT(*) FROM cash_flow_data").fetchone()[0] == 1
    assert chk.execute("SELECT COUNT(*) FROM financial_data WHERE revenue IS NOT NULL "
                       "AND total_assets IS NOT NULL").fetchone()[0] == 2
    # 재실행: 계획 0건·행 수 불변·감사표 1행 유지
    res2 = _run(monkeypatch, db, tmp_path, _group_rows(pairs, code="462860", year=2024), apply=True,
                name="out2.json")
    chk2 = _open(db)
    assert res2["stats"]["planned_updates"] == 0 and res2["stats"]["planned_new_rows"] == 0
    assert res2["stats"]["noop"] == 5
    assert chk2.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 2
    assert chk2.execute("SELECT COUNT(*) FROM cash_flow_data").fetchone()[0] == 1
    assert chk2.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 4
    assert chk2.execute("SELECT COUNT(*) FROM data_fix_log").fetchone()[0] == 1


def test_merged_insert_rerun_creates_no_duplicate_annual_row(monkeypatch, tmp_path):
    """재실행 idempotency: 두 번째 apply는 중복 연간 행을 만들지 않는다."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    for f, v in FIN6:
        _verify(conn, "241820", 2022, f, v)
    first = _run(monkeypatch, db, tmp_path, _group_rows(FIN6), apply=True, name="apply.json")
    assert first["stats"]["planned_new_rows"] == 1 and first["stats"]["planned_new_row_fields"] == 6
    second = _run(monkeypatch, db, tmp_path, _group_rows(FIN6), apply=True, name="apply2.json")
    chk = _open(db)
    assert second["stats"]["planned_new_rows"] == 0 and second["stats"]["planned_updates"] == 0
    assert second["stats"]["noop"] == 6
    assert chk.execute("SELECT COUNT(*) FROM financial_data WHERE stock_code='241820' AND year=2022 "
                       "AND quarter=0 AND report_type='OFS'").fetchone()[0] == 1
    assert chk.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 1
    assert chk.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 6
    assert chk.execute("SELECT COUNT(*) FROM data_fix_log").fetchone()[0] == 1
    assert chk.execute("SELECT COUNT(*) FROM canonical_financial_data WHERE stock_code='241820'").fetchone()[0] == 1


def test_canonical_payload_preserves_cells_missing_from_ledger_row(monkeypatch, tmp_path):
    """정본에만 있던 칸은 신규 병합 행 동기화로 NULL이 되지 않는다(정보 보존)."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    conn.execute("INSERT INTO canonical_financial_data (stock_code, year, quarter, is_annual, report_type, "
                 "revenue, operating_profit, net_income, total_assets, total_liabilities, total_equity, "
                 "capital_stock, eps, bps, roe, data_source, source_row_id, updated_at) "
                 "VALUES ('241820', 2022, 0, 1, 'OFS', NULL, NULL, NULL, NULL, NULL, NULL, "
                 "5978618000.0, -382.0, 11342.0, -12.75, 'dart_ofs_backfill', 204981, '2026-10-01')")
    conn.commit()
    for f, v in FIN6:
        _verify(conn, "241820", 2022, f, v)
    res = _run(monkeypatch, db, tmp_path, _group_rows(FIN6), apply=True)
    chk = _open(db)
    got = chk.execute("SELECT revenue, capital_stock, eps, bps, roe FROM canonical_financial_data "
                      "WHERE stock_code='241820' AND year=2022 AND is_annual=1").fetchone()
    assert abs(got["revenue"] - FIN6[0][1]) < 0.5
    assert (got["capital_stock"], got["eps"], got["bps"], got["roe"]) == (5978618000.0, -382.0, 11342.0, -12.75)
    assert res["canonical_sync"]["preserved_cells"] >= 4
    assert chk.execute("SELECT COUNT(*) FROM canonical_financial_data WHERE stock_code='241820'").fetchone()[0] == 1
    # 이전 정본 행은 백업에 남는다(되돌릴 수 있다)
    bk = "canonical_financial_data_backup_ofs_doc_candidates_20261010"
    assert chk.execute(f"SELECT COUNT(*) FROM {bk}").fetchone()[0] == 1
    assert chk.execute(f"SELECT revenue FROM {bk}").fetchone()[0] is None


# ── 18) [2026-10-10 Checker BLOCK] FnGuide는 연간(q0) 전용 — 분기 값으로 대체검증 금지 ────────────
def test_fnguide_without_q0_row_holds_instead_of_using_quarterly(monkeypatch, tmp_path):
    """q1~q4 값이 후보와 일치해도 q0 행이 없으면 보류한다(연간 후보를 분기 실적으로 검증하지 않는다)."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, revenue)
                    VALUES (1,'241820',2022,0,1,'OFS',NULL)""")
    for q in (1, 2, 3, 4):
        _verify(conn, "241820", 2022, "revenue", 37217000000.0, quarter=q)
    res = _run(monkeypatch, db, tmp_path, [_row()], apply=True)
    d = res["decisions"][0]
    assert res["stats"]["held_fnguide_changed_or_missing"] == 1 and res["stats"]["planned_updates"] == 0
    assert d["fresh_fnguide"] is None and d["fresh_fnguide_quarter"] is None
    assert "연간(q0)" in d["note"]
    assert _open(db).execute("SELECT revenue FROM financial_data WHERE id=1").fetchone()[0] is None


def test_fnguide_q0_is_authoritative_even_when_quarterly_matches(monkeypatch, tmp_path):
    """q0가 있으면 q0만 쓴다 — 분기 값이 후보와 일치해도 q0가 200만원 초과면 보류."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    conn.execute("""INSERT INTO financial_data (id, stock_code, year, quarter, is_annual, report_type, revenue)
                    VALUES (1,'241820',2022,0,1,'OFS',NULL)""")
    _verify(conn, "241820", 2022, "revenue", 30_000_000_000.0, quarter=0)
    _verify(conn, "241820", 2022, "revenue", 37217000000.0, quarter=1)
    res = _run(monkeypatch, db, tmp_path, [_row()], apply=True)
    d = res["decisions"][0]
    assert res["stats"]["held_fnguide_changed_or_missing"] == 1
    assert d["fresh_fnguide"] == 30_000_000_000.0 and d["fresh_fnguide_quarter"] == 0
    assert _open(db).execute("SELECT revenue FROM financial_data WHERE id=1").fetchone()[0] is None


def test_annual_fnguide_map_is_q0_only_and_drops_ambiguous_keys():
    """순수 헬퍼: 같은 연간키에 q0 값이 2개 이상·불일치면 키를 만들지 않는다(fail-closed)."""
    assert mod.annual_fnguide_map([("A", 2022, "revenue", 1.0), ("B", 2022, "revenue", 2.0)]) == {
        ("A", 2022, "revenue"): 1.0, ("B", 2022, "revenue"): 2.0}
    assert mod.annual_fnguide_map([("A", 2022, "revenue", 1.0), ("A", 2022, "revenue", 2.0)]) == {}
    assert mod.annual_fnguide_map([("A", 2022, "revenue", 1.0), ("A", 2022, "revenue", 1.0)]) == {
        ("A", 2022, "revenue"): 1.0}


def test_load_fnguide_sql_selects_only_annual_rows(monkeypatch, tmp_path):
    """load_fnguide가 실제로 quarter=0만 조회한다(쿼리 자체 검증 — 회귀 방지)."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    _verify(conn, "241820", 2022, "revenue", 37217000000.0, quarter=0)
    _verify(conn, "241820", 2022, "revenue", 111.0, quarter=1)
    got = mod.load_fnguide(conn, [("241820", 2022)])
    assert got == {("241820", 2022, "revenue"): 37217000000.0}


# ── 19~20) [2026-10-10 Checker BLOCK] provenance = section4_table · doc basename 정규화 ─────────
def test_doc_absolute_path_is_normalized_and_never_stored(monkeypatch, tmp_path):
    """절대경로 doc는 basename만 남기고, 결과 JSON·fix log 어디에도 경로를 저장하지 않는다."""
    db = tmp_path / "f.db"
    conn = _conn(db)
    _verify(conn, "241820", 2022, "revenue", 37217000000.0)
    res = _run(monkeypatch, db, tmp_path,
               [_row(doc="/Volumes/SecretDir/2022_20240314000576.zip")], apply=True)
    txt = (tmp_path / "out.json").read_text(encoding="utf-8")
    chk = _open(db)
    assert res["stats"]["doc_path_normalized"] == 1
    assert res["decisions"][0]["doc"] == "2022_20240314000576.zip"
    assert res["decisions"][0]["doc_path_normalized"] is True
    assert "SecretDir" not in txt and str(tmp_path) not in txt
    src = chk.execute("SELECT source FROM financial_fix_log").fetchone()[0]
    assert "SecretDir" not in src and "2022_20240314000576.zip" in src
    # canonical 쪽 감사 사유(run_id)에도 경로가 없다
    assert "SecretDir" not in (res["run_id"] or "")
    # basename 입력은 정규화 카운터가 올라가지 않는다(카운터가 의미를 갖도록)
    db2 = tmp_path / "g.db"
    conn2 = _conn(db2)
    _verify(conn2, "241820", 2022, "revenue", 37217000000.0)
    res2 = _run(monkeypatch, db2, tmp_path, [_row()], apply=True, name="out2.json")
    assert res2["stats"]["doc_path_normalized"] == 0


def test_doc_and_parse_reason_provenance_fail_closed(monkeypatch, tmp_path):
    """parse_reason이 section4_table이 아니거나 doc basename이 무효면 아무것도 하지 않는다."""
    db = tmp_path / "f.db"
    _conn(db)
    rows = [_row(reason="section3_note"), _row(reason=""), _row(doc=""), _row(doc="/a/b/"),
            _row(doc="bad name.zip"), _row(doc=".hidden.zip")]
    res = _run(monkeypatch, db, tmp_path, rows, apply=True)
    assert res["stats"]["invalid_input"] == 6
    assert res["stats"]["planned_updates"] == res["stats"]["planned_new_rows"] == 0
    assert all(d["decision"] == "invalid_input" for d in res["decisions"])
    reasons = {d["note"] for d in res["decisions"]}
    assert any("parse_reason" in r for r in reasons) and "doc_provenance_invalid" in reasons
    assert _open(db).execute("SELECT COUNT(*) FROM financial_data").fetchone()[0] == 0
