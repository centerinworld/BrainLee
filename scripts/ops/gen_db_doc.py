#!/usr/bin/env python3
"""운영 PostgreSQL 카탈로그에서 docs/DB_TABLES_PG.md 를 생성한다 (CLAUDE.md 섹션 2의 전체 목록 정본).
사용: python3 scripts/ops/gen_db_doc.py   (행수는 pg_class 추정치)"""
import os, re, sys
from datetime import date
from pathlib import Path
import psycopg

ROOT = Path(__file__).resolve().parents[2]
for line in (ROOT / ".env").read_text().splitlines():
    if line.startswith("POSTGRES_DATABASE_URL="):
        url = re.sub(r"^postgresql\+psycopg", "postgresql", line.split("=", 1)[1].strip().strip("\"'"))
c = psycopg.connect(url)
tabs = c.execute("""select c.relname, greatest(c.reltuples,0)::bigint from pg_class c
  join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' order by 1""").fetchall()
cols = {}
for t, col in c.execute("select table_name, column_name from information_schema.columns where table_schema='public' order by table_name, ordinal_position"):
    cols.setdefault(t, []).append(col)
BK = re.compile(r"(_backup|_bak|_full_backup|_quarantine_\d|_tmp|_stage|_snapshot_\d|_\d{8}(_\d+)?$|backup_)")
main = [(t, n) for t, n in tabs if not BK.search(t)]
bk = [(t, n) for t, n in tabs if BK.search(t)]
def h(n):
    return f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.0f}K" if n >= 1e3 else str(n)
out = ["# PostgreSQL 테이블 목록 (자동 생성)", "",
       f"> `scripts/ops/gen_db_doc.py` 생성 — {date.today()} 기준 전체 {len(tabs)}개(백업/임시 {len(bk)}개 제외 {len(main)}개). 행수는 추정치. **수동 편집 금지**.",
       "> 컬럼 의미·단위·주의사항은 CLAUDE.md 섹션 2 표(핵심 테이블)와 섹션 8을 본다. 이 문서는 '테이블이 있는지/컬럼명이 뭔지' 확인용.", "",
       "| 테이블 | 행수 | 컬럼 |", "|---|---:|---|"]
for t, n in main:
    cs = cols.get(t, [])
    out.append(f"| `{t}` | {h(n)} | {', '.join(cs[:14])}{' …+%d' % (len(cs)-14) if len(cs) > 14 else ''} |")
out += ["", f"## 백업/임시/일자별 스냅샷 테이블 ({len(bk)}개, 조회 대상 아님)", "", ", ".join(f"`{t}`({h(n)})" for t, n in bk)]
(ROOT / "docs" / "DB_TABLES_PG.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print(len(main), "main +", len(bk), "backup tables ->", "docs/DB_TABLES_PG.md")
