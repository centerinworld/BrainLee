#!/usr/bin/env python3
"""비12월 결산 종목의 분기 키 충돌 해소(2026-10-05, FINANCIAL_STATEMENTS.md §2-7).

rekey_fiscal_nondec_20261004.py 가 보류한 '이동 대상 키에 값이 다른 행이 이미 있음' 충돌을 외부 값으로 판정한다.
행마다 '실제 기간'을 값으로 찾는다(같은 구분 CFS/OFS, 매출 일치 — 매출 없으면 영업이익):
  F = FnGuide 원문(회계 키로 환산된 wcomp, compare_db_vs_fnguide_raw_20261003.parse_code)에서 일치하는 회계 기간
  D = DART 원문(dart_cf_*.jsonl, fiscal_period.to_fiscal)에서 일치하는 회계 기간
  실제 기간 = F(유일할 때) 우선, 없으면 D(유일할 때). 둘 다 없으면 '근거 없음'.
판정(행 X가 자리 S에 있고 실제 기간이 T≠S):
  T 자리가 비었음                      → X를 T로 이동
  T 자리 행 Y의 실제 기간 = T(제자리)  → X는 같은 기간의 중복(값·기준이 다름) → X 삭제(백업). 단 F가 X만 지지하고 Y는 F 불일치면 Y 삭제·X 이동
  T 자리 행 Y가 근거 없음              → X의 실제 기간이 F(외부 확인)면 Y 삭제·X 이동, D뿐이면 보류(원칙 0: DART 단독 확정 금지)
  T 자리 행 Y도 다른 곳으로 가야 함    → 연쇄(고정점 반복으로 처리)
  원문 일치가 없는 네이버 행(data_source='fnguide_naver', 억 단위 반올림)은 달력 분기 표기로 보고 회계 키로 환산(근거 N, 가장 약함)
  같은 자리로 가는 행이 여럿이고 값이 같으면(1억·1% 이내) 근거가 강한 행만 남기고 나머지는 중복으로 삭제
X가 떠난 자리는 비워 둔다(틀린 값보다 결측이 낫다 — fail-closed). 연간 행은 건드리지 않는다.
적용 뒤 영향 종목의 4분기(연간 − 1~3분기)를 다시 계산한다. 기본 dry-run, --apply 시 백업·data_fix_log.
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
import fiscal_period as fp  # noqa: E402
from compare_db_vs_fnguide_raw_20261003 import parse_code  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002"
FLOW = ["revenue", "operating_profit", "net_income"]
BS = ["total_assets", "total_liabilities", "total_equity"]


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.005)


def close_n(a, b):
    """네이버 억 단위 반올림까지 허용한 같은 값 판정(1억 또는 1%)."""
    return a is not None and b is not None and abs(a - b) <= max(1e8, abs(b) * 0.01)


def match(rev, op, cands):
    """cands: {(fy,fq): {'revenue':..,'operating_profit':..}} → 일치 기간 집합."""
    out = set()
    for k, v in cands.items():
        if rev is not None and v.get("revenue") is not None:
            if close(rev, v["revenue"]) and (op is None or v.get("operating_profit") is None or close(op, v["operating_profit"])):
                out.add(k)
        elif rev is None and op is not None and close(op, v.get("operating_profit")):
            out.add(k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    fmap = fp.fiscal_month_map(conn)
    reit = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:fiscal_period'").fetchall()}
    codes = sorted(c for c, m in fmap.items() if m != 12 and c not in reit)
    dart = collections.defaultdict(dict)  # (code, fs) -> {(fy,fq): vals}
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if p.exists():
            for line in open(p):
                d = json.loads(line)
                if d.get("code") in fmap and d.get("fs") and d.get("vals") and d["q"] in (1, 2, 3):
                    fy, fq = fp.to_fiscal(d["code"], d["year"], d["q"], False, conn)
                    dart[(d["code"], d["fs"])][(fy, fq)] = d["vals"]
    fg = collections.defaultdict(dict)
    for c in codes:
        r, _ = parse_code(c)
        for (y, q, fs), v in r.items():
            if isinstance(q, int) and q in (1, 2, 3):
                fg[(c, fs)][(y, q)] = v
    ph = ",".join("?" * len(codes))
    rows = [tuple(r) for r in conn.execute(f"SELECT id, stock_code, year, quarter, report_type, revenue, operating_profit, COALESCE(data_source,'') FROM financial_data "
                                           f"WHERE stock_code IN ({ph}) AND NOT is_annual AND quarter IN (1,2,3)", codes).fetchall()]
    info = {}  # id -> (code, fs, slot, true_period, basis, fnguide_supported)
    rev_of = {}
    for rid, code, y, q, fs, rev, op, src in rows:
        rev_of[rid] = rev
        f = match(rev, op, fg.get((code, fs), {}))
        d = match(rev, op, dart.get((code, fs), {}))
        if len(f) == 1:
            t, b = next(iter(f)), "F"
        elif not f and len(d) == 1:
            t, b = next(iter(d)), "D"
        elif not f and not d and src == "fnguide_naver":
            # 네이버(억 단위 반올림) 행은 달력 분기 표기 — 018500·001720 표본에서 (2026,2)=4~6월 = 3월 결산 (2027,1) DART 값과 일치 확인
            m_ = 3 * q
            fm = fmap[code]
            t, b = (y if m_ <= fm else y + 1, ((m_ - fm - 1) % 12) // 3 + 1), "N"
            if t[1] == 4:
                t, b = None, "근거 없음"
        else:
            t, b = None, ("모호" if (f or d) else "근거 없음")
        info[rid] = (code, fs, (y, q), t, b, bool(f))
    RANK = {"F": 3, "D": 2, "N": 1}
    slot = {(v[0], v[1], v[2]): rid for rid, v in info.items()}
    st = collections.Counter()
    moves, deletes, held = {}, set(), []
    for rid, (code, fs, s, t, b, fsup) in info.items():
        st[f"판정 근거 {b}"] += 1
        if t is None or t == s:
            continue
        moves[rid] = t
    # 고정점: 대상 자리 판정
    changed = True
    while changed:
        changed = False
        for rid, t in list(moves.items()):
            code, fs, s, _, b, fsup = info[rid]
            occ = slot.get((code, fs, t))
            if occ is None or occ in deletes or occ in moves:
                continue  # 비었거나 떠날 행 → 이동 가능(연쇄 포함)
            ob, ot = info[occ][4], info[occ][3]
            if ot == t:  # 제자리 행과 같은 기간
                if b == "F" and info[occ][4] != "F":
                    deletes.add(occ); st["점유 행(외부 불일치) 삭제·이동"] += 1
                elif b == "N" and not close_n(rev_of[rid], rev_of[occ]):
                    held.append((rid, code, fs, s, t, b, occ, ob)); del moves[rid]; st["보류(네이버 행 값이 제자리 행과 다름)"] += 1
                else:
                    deletes.add(rid); del moves[rid]; st["이동 행 = 같은 기간 중복 → 삭제"] += 1
            elif ot is None and b == "F":
                deletes.add(occ); st["점유 행(근거 없음) 삭제·FnGuide 확인 행 이동"] += 1
            else:
                held.append((rid, code, fs, s, t, b, occ, ob)); del moves[rid]; st["보류(DART 단독 또는 모호)"] += 1
            changed = True
    # 같은 빈 자리로 가는 이동 행이 여럿: 같은 기간 중복이면 근거가 강한 행(F>D>N, 네이버 반올림값보다 원문 정밀값)만 남긴다
    by_t = collections.defaultdict(list)
    for rid, t in moves.items():
        by_t[(info[rid][0], info[rid][1], t)].append(rid)
    for k, ids in by_t.items():
        if len(ids) < 2:
            continue
        ids.sort(key=lambda r: -RANK[info[r][4]])
        keep = ids[0]
        for r in ids[1:]:
            if close_n(rev_of[r], rev_of[keep]):
                deletes.add(r); del moves[r]; st["같은 자리 이동 행 중복 → 약한 근거 행 삭제"] += 1
    # 최종 충돌 검사
    pos = collections.Counter()
    for rid, v in info.items():
        if rid in deletes:
            continue
        pos[(v[0], v[1], moves.get(rid, v[2]))] += 1
    bad = {k for k, n in pos.items() if n > 1}
    for rid in list(moves):
        v = info[rid]
        if (v[0], v[1], moves[rid]) in bad:
            held.append((rid, v[0], v[1], v[2], moves[rid], v[4], None, None)); del moves[rid]; st["보류(최종 충돌)"] += 1
    st["이동"] = len(moves)
    st["삭제"] = len(deletes)
    print(json.dumps(dict(st), ensure_ascii=False, indent=1))
    import csv
    with open(SRC / "fiscal_conflict_resolution_20261005.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "id", "stock_code", "fs", "slot", "true_period", "basis", "occupant", "occupant_basis"])
        for rid, t in moves.items():
            v = info[rid]; w.writerow(["move", rid, v[0], v[1], v[2], t, v[4], "", ""])
        for rid in deletes:
            v = info[rid]; w.writerow(["delete", rid, v[0], v[1], v[2], v[3], v[4], "", ""])
        for h in held:
            w.writerow(["hold", h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]])
    if not a.apply:
        return
    run_id = f"fiscal_conflict_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    touched = sorted({info[r][0] for r in list(moves) + list(deletes)})
    conn.execute("CREATE TABLE IF NOT EXISTS financial_data_backup_rekey_20261004 AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
    if touched:
        conn.execute(f"INSERT INTO financial_data_backup_rekey_20261004 SELECT *, ? FROM financial_data WHERE stock_code IN ({','.join('?' * len(touched))})", [run_id] + touched)
    dl = sorted(deletes)
    for i in range(0, len(dl), 500):
        conn.execute(f"DELETE FROM financial_data WHERE id IN ({','.join('?' * len(dl[i:i + 500]))})", dl[i:i + 500])
    mv = list(moves.items())
    for i, (rid, _) in enumerate(mv):
        conn.execute("UPDATE financial_data SET quarter=? WHERE id=?", (-(i + 100), rid))
    for rid, (ty, tq) in mv:
        conn.execute("UPDATE financial_data SET year=?, quarter=?, data_source=COALESCE(data_source,'')||'+fiscal_rekey', updated_at=? WHERE id=?", (ty, tq, now, rid))
    n_q4 = 0
    for code in touched:
        for fs in ("CFS", "OFS"):
            ann = {r[0]: r[1:] for r in map(tuple, conn.execute(f"SELECT year, {','.join(FLOW + BS)} FROM financial_data WHERE stock_code=? AND report_type=? AND is_annual",
                                                                (code, fs)).fetchall())}
            qs = collections.defaultdict(dict)
            for r in map(tuple, conn.execute(f"SELECT year, quarter, {','.join(FLOW)} FROM financial_data WHERE stock_code=? AND report_type=? AND NOT is_annual AND quarter IN (1,2,3)",
                                             (code, fs)).fetchall()):
                qs[r[0]][r[1]] = r[2:]
            for y, av in ann.items():
                if set(qs.get(y, {})) != {1, 2, 3}:
                    continue
                vals = {f: (av[i] - sum(qs[y][k][i] for k in (1, 2, 3)) if av[i] is not None and all(qs[y][k][i] is not None for k in (1, 2, 3)) else None)
                        for i, f in enumerate(FLOW)}
                vals.update({f: av[len(FLOW) + i] for i, f in enumerate(BS)})
                ex = conn.execute("SELECT id FROM financial_data WHERE stock_code=? AND year=? AND quarter=4 AND NOT is_annual AND report_type=?", (code, y, fs)).fetchone()
                if ex:
                    conn.execute(f"UPDATE financial_data SET {','.join(f + '=?' for f in vals)}, updated_at=? WHERE id=?", list(vals.values()) + [now, ex[0]])
                else:
                    conn.execute(f"INSERT INTO financial_data(stock_code,year,quarter,is_annual,report_type,{','.join(vals)},data_source,created_at,updated_at) "
                                 f"VALUES (?,?,4,false,?,{','.join('?' * len(vals))},'q4_fiscal_rekey',?,?)", [code, y, fs] + list(vals.values()) + [now, now])
                n_q4 += 1
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data", "비12월 결산 분기 키 충돌 해소", len(moves) + len(deletes),
                  "행의 실제 기간을 FnGuide(우선)·DART 원문 값으로 판정, 외부 확인 행 우선·DART 단독은 보류", json.dumps(dict(st), ensure_ascii=False),
                  f"이동 {len(moves)} 삭제 {len(deletes)} 4분기 {n_q4}", "scripts/review/resolve_fiscal_conflicts_20261005.py", run_id))
    conn.commit()
    print("적용 완료", run_id, "이동", len(moves), "삭제", len(deletes), "4분기", n_q4)


if __name__ == "__main__":
    main()
