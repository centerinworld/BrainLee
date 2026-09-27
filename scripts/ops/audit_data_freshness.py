#!/usr/bin/env python3
"""전체 테이블 데이터 신선도 점검 (2026-09-27) — 수집 계약(collection_health)에 없는 테이블까지 PostgreSQL 전체를 훑는다.

각 테이블에서 날짜성 컬럼(date/dt/trade_date/bas_dt/… 우선순위)을 골라 최신 값과 행수를 구하고, 오늘 기준 며칠 뒤처졌는지 표로 낸다.
백업·수정로그·아카이브·일회성(날짜 접미사) 테이블은 제외. 쿼리마다 statement_timeout 8초(큰 테이블이 점검을 막지 않게).
  cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python scripts/ops/audit_data_freshness.py [--days 7] [--out docs/DATA_FRESHNESS_YYYYMMDD.md]
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

DATE_COLS = ["date", "dt", "trade_date", "bas_dt", "base_date", "snapshot_date", "as_of", "as_of_date", "signal_date", "report_date", "disclosed_at",
             "period", "run_time", "collected_at", "updated_at", "created_at", "fetched_at", "last_synced_at", "last_checked_at"]
SKIP = re.compile(r"(backup|_bak|fix_log|_log$|archive|legacy|tmp|_stage|staging|quarantine|_20\d{6}|_v\d+$|_old$|rebuild|snapshot_rebuild|history_fix)", re.I)
TODAY = date.today()


def norm(v):
    """'2026-09-23T15:00', '20260923', datetime → date | None"""
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.date() if isinstance(v, datetime) else v
    digits = re.sub(r"\D", "", str(v))[:8]
    if len(digits) == 8:
        try:
            d = date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
            return d if 2000 <= d.year <= TODAY.year + 1 else None
        except ValueError:
            return None
    return None


def main() -> int:
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "docs" / f"DATA_FRESHNESS_{TODAY:%Y%m%d}.md"
    conn = connect_primary_db(readonly=True, timeout=30)
    cur = conn.cursor()

    def q(sql, *a):
        cur.execute(sql, a) if a else cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]

    tables = q("SELECT c.relname, GREATEST(c.reltuples,0)::bigint FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
               "WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1")
    cols = {}
    for t, c, typ in q("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='public'"):
        cols.setdefault(t, {})[c] = typ
    rows, skipped, nodate, errors = [], 0, [], []
    for t, est in tables:
        if SKIP.search(t):
            skipped += 1
            continue
        pick = next((c for c in DATE_COLS if c in cols.get(t, {})), None)
        if not pick:
            nodate.append(t)
            continue
        try:
            cur.execute("SET LOCAL statement_timeout = 8000")
            cur.execute(f'SELECT MAX("{pick}"), COUNT(*) FROM "{t}"')
            mx, n = cur.fetchone()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            errors.append((t, type(e).__name__))
            continue
        d = norm(mx)
        rows.append((t, pick, d, int(n or 0), (TODAY - d).days if d else None, str(mx)[:19]))
    conn.rollback()
    stale = sorted([r for r in rows if r[3] > 0 and r[4] is not None and r[4] >= days], key=lambda r: -r[4])
    unknown = [r for r in rows if r[3] > 0 and r[4] is None]
    empty = [r[0] for r in rows if r[3] == 0]

    L = [f"# 데이터 신선도 전체 점검 — {TODAY} (자동 생성: scripts/ops/audit_data_freshness.py)", "",
         f"- 대상 테이블 {len(rows)}개(제외 {skipped}: 백업·로그·일회성 / 날짜 컬럼 없음 {len(nodate)} / 조회 오류 {len(errors)}). 기준: 오늘({TODAY}) 대비 **{days}일 이상** 뒤처진 것.",
         "- 주의: 월·분기·연간 데이터, 이벤트 구동(조건 충족 시만 적재), 일회성 연구 테이블은 오래돼도 정상일 수 있다 — 아래는 후보 목록이며 원인 분류는 `원인` 열/본문 참조.", "",
         f"## 뒤처진 테이블 {len(stale)}개", "", "| 테이블 | 기준 컬럼 | 최신 | 뒤처진 일수 | 행수 |", "|---|---|---|--:|--:|"]
    L += [f"| `{t}` | {c} | {mx} | {lag} | {n:,} |" for t, c, d, n, lag, mx in stale]
    L += ["", f"## 행은 있으나 날짜를 해석하지 못한 테이블 {len(unknown)}개", "", ", ".join(f"`{r[0]}`" for r in unknown) or "없음",
          "", f"## 비어 있는 테이블 {len(empty)}개", "", ", ".join(f"`{t}`" for t in empty) or "없음", ""]
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"{out.relative_to(ROOT)} 저장 — 대상 {len(rows)}, 뒤처짐 {len(stale)}, 해석불가 {len(unknown)}, 빈 테이블 {len(empty)}, 오류 {len(errors)}")
    for t, c, d, n, lag, mx in stale[:60]:
        print(f"{lag:4d}일  {t:42s} {c:14s} {mx}  rows={n:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
