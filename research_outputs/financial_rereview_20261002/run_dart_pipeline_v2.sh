#!/bin/zsh
# DART 재수집 파이프라인(2026-10-03) — 하루 한도 안에서 매일 이어 받는다. 키2(공시 전용)는 수집기가 쓰지 않는다.
#  1) 2016~2022 본 수집  2) 2016~2022 지배주주 보완  3) 2023~ 지배주주 보완
cd /Volumes/Realtek_NVME/stock_dashboard/runtime
export PYTHONPATH=runtime_pg_bootstrap:.
for day in 1 2 3 4 5 6 7; do
  echo "$(date '+%F %T') ── ${day}일차"
  venv/bin/python scripts/review/fetch_dart_cashflow_20261002.py --years 2016-2022
  venv/bin/python scripts/review/fetch_dart_cashflow_20261002.py --years 2016-2022 --repair-parent
  venv/bin/python scripts/review/fetch_dart_cashflow_20261002.py --repair-parent
  # 2026-10-03: 사업보고서 전기 칸(재작성값) 확보 — 정정 표시 기준(FINANCIAL_STATEMENTS.md)
  venv/bin/python scripts/review/fetch_dart_cashflow_20261002.py --prev-only
  venv/bin/python scripts/review/fetch_dart_cashflow_20261002.py --years 2016-2022 --prev-only
  left=$(venv/bin/python - <<'PY'
import json
from pathlib import Path
R=Path('research_outputs/financial_rereview_20261002')
def todo(f):
    last={}
    for l in open(R/f):
        d=json.loads(l)
        if d.get('ok'): last[(d['code'],d['year'],d['q'])]=d
    return sum(1 for d in last.values() if d.get('fs')=='CFS' and d.get('vals') and not d.get('parent_checked') and ('ni_parent' not in d['vals'] or 'equity_parent' not in d['vals'])), len(last)
a=todo('dart_cf_2016_2022.jsonl'); b=todo('dart_cf_full.jsonl')
print(f"{a[0]+b[0]} {a[1]}")
PY
)
  echo "$(date '+%F %T') 남은 보완 ${left% *}, 2016~2022 수집 ${left#* } / 70588"
  [ "${left% *}" -eq 0 ] && [ "${left#* }" -ge 70500 ] && { echo 완료; exit 0; }
  now=$(date +%s); tomorrow=$(date -v+1d -v0H -v20M -v0S +%s); sleep $((tomorrow-now))
done
