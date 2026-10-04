#!/usr/bin/env python3
"""비12월 결산 회사의 재무 분기 행을 회계 기준 키로 재배치하고 4분기를 다시 계산(2026-10-04, FINANCIAL_STATEMENTS.md §2-7).

배경: DB가 DART 표기(bsns_year = 기간 종료 달력 연도)와 FnGuide·네이버 계열(달력 분기)을 섞어 저장 → 같은 연도에 회계연도가 섞여
4분기·전년 대비·TTM이 틀림. 규칙 하나로 연도만 옮기면 출처마다 표기가 달라 잘못 옮겨진다 → **값으로 기간을 판정**한다.
  1) DART 원문(dart_cf_*.jsonl)의 각 보고서를 fiscal_period.to_fiscal 로 회계 키화.
  2) DB 분기 행(1~3분기)마다 같은 구분(CFS/OFS)의 DART 원문 중 매출 또는 자산총계(재무), 영업현금흐름 누적(현금흐름)이 일치하는
     보고서를 찾아 그 회계 키로 이동. 일치 없음 → 옮기지 않고 보고(unmatched).
  3) 이동 대상 키에 다른 행이 이미 있으면 → 같은 기간 값(매출·자산 일치)이면 중복으로 보고 이동 행을 남기지 않음(삭제, 백업), 다르면 보류.
  4) 4분기 재계산: 같은 회계연도의 연간·1~3분기가 모두 있을 때 흐름 = 연간 − (1+2+3), 재무상태 = 연간 값. 현금흐름 *_q(4) = 연간 누적 − 3분기 누적.
연간 행은 바꾸지 않는다(DART·FnGuide 모두 결산 종료 연도). 기본 dry-run, --apply 시 백업 테이블·fix_log·data_fix_log.
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import fiscal_period as fp  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002"
FLOW = ["revenue", "operating_profit", "net_income"]
BS = ["total_assets", "total_liabilities", "total_equity"]
CFC = ["operating_cf", "investing_cf", "financing_cf", "capex"]


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.005)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    fmap = fp.fiscal_month_map(conn)
    reit = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:fiscal_period'").fetchall()}
    codes = sorted(c for c, m in fmap.items() if m != 12 and c not in reit)  # 리츠(6개월 결산)는 분기 개념이 달라 제외
    # 2026-10-04 사고 재발 방지: 재실행이 이미 옮긴 행을 다시 옮겼다(출처 추정이 회계 키 행을 DART 표기로 오인) → 완료 종목은 값 판정만
    keyed = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:fiscal_keyed' AND config_value='1'").fetchall()}
    dart = collections.defaultdict(list)  # code -> [(fiscal_year, fq, fs, vals)]
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if p.exists():
            for line in open(p):
                d = json.loads(line)
                if d.get("code") in fmap and d.get("fs") and d.get("vals") and d["q"] in (1, 2, 3):
                    fy, fq = fp.to_fiscal(d["code"], d["year"], d["q"], False, conn)
                    dart[d["code"]].append((fy, fq, d["fs"], d["vals"]))
    st, moves, dups, unmatched = collections.Counter(), [], [], []
    ph = ",".join("?" * len(codes))
    for tbl, keycols in (("financial_data", ("revenue", "operating_profit")), ("cash_flow_data", ("operating_cf", "investing_cf"))):
        rows = [tuple(r) for r in conn.execute(f"SELECT id, stock_code, year, quarter, report_type, {','.join(keycols)}, COALESCE(data_source,'') FROM {tbl} "
                                               f"WHERE stock_code IN ({ph}) AND NOT is_annual AND quarter IN (1,2,3)", codes).fetchall()]
        occupied = {(r[1], r[2], r[3], r[4]): r for r in rows}
        for r in rows:
            rid, code, y, q, fs = r[:5]
            target = None
            for fy, fq, dfs, v in dart.get(code, []):
                if dfs != fs:
                    continue
                # 자산총계는 해마다 비슷해 다른 연도와 우연히 맞는다(001080 사례) → 흐름 값으로만 판정
                if tbl == "financial_data":
                    hit = close(r[5], v.get("revenue")) or (r[5] is None and close(r[6], v.get("operating_profit")))
                else:
                    hit = close(r[5], v.get("ocf")) and close(r[6], v.get("icf"))
                if hit:
                    target = (fy, fq)
                    break
            if target is None and (code in keyed or "fiscal_rekey" in r[-1]):
                st[f"{tbl}: 회계 키 완료 종목·원문 일치 없음(보류)"] += 1
                unmatched.append((tbl, rid, code, y, q, fs))
                continue
            if target is None:
                # 원문이 없는 기간: 출처로 표기 방식을 추정(값 판정 다음 순위). FnGuide·네이버 계열 = 달력 분기 표기, 그 외 = DART 표기
                src = r[-1].lower()
                f = fmap[code]
                if src.startswith(("fnguide", "naver", "quarterly_recalc_naver")):
                    m_ = 3 * q  # 달력 분기 → 기간 종료 월
                    target = (y if m_ <= f else y + 1, ((m_ - f - 1) % 12) // 3 + 1)
                    st[f"{tbl}: 출처 추정(달력 분기 표기)"] += 1
                else:
                    target = fp.to_fiscal(code, y, q, False, conn)
                    st[f"{tbl}: 출처 추정(DART 표기)"] += 1
                if target[1] == 4:  # 달력 기준으로 4분기(결산 분기)에 해당 → 4분기는 재계산 대상이라 이동하지 않음
                    unmatched.append((tbl, rid, code, y, q, fs))
                    continue
            if target == (y, q):
                st[f"{tbl}: 이미 회계 키"] += 1
                continue
            other = occupied.get((code, target[0], target[1], fs))
            if other is not None and other[0] != rid:
                # 같은 기간 값이면 이동 행은 중복
                same = all(close(r[5 + i], other[5 + i]) for i in range(len(keycols)) if r[5 + i] is not None and other[5 + i] is not None)
                if same:
                    st[f"{tbl}: 대상 키에 같은 기간 행 있음 → 중복 제거"] += 1
                    dups.append((tbl, rid, code, y, q, fs))
                    continue
                # 대상 키의 행도 이동 예정이면 문제없음(연쇄 이동) — 아래 2단계 이동으로 처리
            st[f"{tbl}: 회계 키로 이동"] += 1
            moves.append((tbl, rid, code, y, q, fs, target[0], target[1]))
    # 이동 후 키 충돌 검사 — 보류된 이동 행은 원래 자리에 남으므로 고정점이 될 때까지 반복
    base = {}
    for tbl in ("financial_data", "cash_flow_data"):
        for r in conn.execute(f"SELECT id, stock_code, year, quarter, report_type FROM {tbl} WHERE stock_code IN ({ph}) AND NOT is_annual", codes).fetchall():
            r = tuple(r)
            base[(tbl, r[0])] = (tbl, r[1], r[2], r[3], r[4])
    dup_ids = {(d[0], d[1]) for d in dups}
    held = set()
    while True:
        accepted = [m for m in moves if (m[0], m[1]) not in held]
        pos = {}
        for key_id, k in base.items():
            if key_id in dup_ids:
                continue
            pos[key_id] = k
        for m in accepted:
            pos[(m[0], m[1])] = (m[0], m[2], m[6], m[7], m[5])
        cnt = collections.Counter(pos.values())
        new_held = {(m[0], m[1]) for m in accepted if cnt[(m[0], m[2], m[6], m[7], m[5])] > 1}
        if not new_held:
            break
        held |= new_held
    ok_moves = [m for m in moves if (m[0], m[1]) not in held]
    final = collections.defaultdict(list)
    for key_id, k in pos.items():
        final[k].append(key_id[1])
    for m in moves:
        if (m[0], m[1]) in held:
            st[f"{m[0]}: 이동 대상 키 충돌 → 보류"] += 1
            unmatched.append(m[:6])
    import csv
    with open(SRC / "rekey_fiscal_conflicts.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["table", "id", "stock_code", "year", "quarter", "fs", "target_year", "target_quarter", "occupant_ids"])
        for m in moves:
            k = (m[0], m[2], m[6], m[7], m[5])
            if m not in ok_moves:
                w.writerow(list(m) + [final.get(k)])
    print(json.dumps(dict(st), ensure_ascii=False, indent=1), "대상 종목", len(codes))
    if not a.apply:
        return
    run_id = f"rekey_fiscal_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for tbl in ("financial_data", "cash_flow_data"):
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_rekey_20261004 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
        conn.execute(f"INSERT INTO {tbl}_backup_rekey_20261004 SELECT *, ? FROM {tbl} WHERE stock_code IN ({ph})", [run_id] + codes)
        dl = [d[1] for d in dups if d[0] == tbl]
        for i in range(0, len(dl), 500):
            conn.execute(f"DELETE FROM {tbl} WHERE id IN ({','.join('?' * len(dl[i:i + 500]))})", dl[i:i + 500])
        mv = [m for m in ok_moves if m[0] == tbl]
        for i, m in enumerate(mv):  # 1단계: 행마다 고유한 임시 음수 분기로 비켜 두기(고유 제약 회피)
            conn.execute(f"UPDATE {tbl} SET quarter=? WHERE id=?", (-(i + 100), m[1]))
        for m in mv:  # 2단계: 회계 키
            conn.execute(f"UPDATE {tbl} SET year=?, quarter=?, updated_at=? WHERE id=?", (m[6], m[7], now, m[1]))
    # 4분기 재계산
    n_q4 = 0
    for code in codes:
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
                vals = {}
                for i, f in enumerate(FLOW):
                    parts = [qs[y][k][i] for k in (1, 2, 3)]
                    vals[f] = av[i] - sum(parts) if av[i] is not None and None not in parts else None
                for i, f in enumerate(BS):
                    vals[f] = av[len(FLOW) + i]
                ex = conn.execute("SELECT id FROM financial_data WHERE stock_code=? AND year=? AND quarter=4 AND NOT is_annual AND report_type=?", (code, y, fs)).fetchone()
                if ex:
                    conn.execute(f"UPDATE financial_data SET {','.join(f + '=?' for f in vals)}, data_source=COALESCE(data_source,'')||'+q4_fiscal_rekey', updated_at=? WHERE id=?",
                                 list(vals.values()) + [now, ex[0]])
                else:
                    conn.execute(f"INSERT INTO financial_data(stock_code,year,quarter,is_annual,report_type,{','.join(vals)},data_source,created_at,updated_at) "
                                 f"VALUES (?,?,4,false,?,{','.join('?' * len(vals))},'q4_fiscal_rekey',?,?)", [code, y, fs] + list(vals.values()) + [now, now])
                n_q4 += 1
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", f"비12월 결산 {len(codes)}종목 회계 키 재배치", len(ok_moves) + len(dups),
                  "DART 원문 값으로 기간 판정 → 회계연도(결산 종료 연도)·회계 분기, 4분기 재계산", json.dumps(dict(st), ensure_ascii=False),
                  f"이동 {len(ok_moves)} 중복 제거 {len(dups)} 4분기 {n_q4}", "scripts/review/rekey_fiscal_nondec_20261004.py", run_id))
    conn.commit()
    print("적용 완료", run_id, "이동", len(ok_moves), "중복 제거", len(dups), "4분기", n_q4)


if __name__ == "__main__":
    main()
