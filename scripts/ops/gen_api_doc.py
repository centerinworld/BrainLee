#!/usr/bin/env python3
"""실행 중인 서버의 /openapi.json 에서 docs/API_ENDPOINTS.md 를 생성한다 (CLAUDE.md 섹션 3의 전체 목록 정본).
사용: python3 scripts/ops/gen_api_doc.py   (서버 :8000 기동 필요)"""
import json, urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
paths = json.load(urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=30))["paths"]
groups = defaultdict(list)
for p, v in sorted(paths.items()):
    for m, op in v.items():
        if m in ("get", "post", "put", "patch", "delete"):
            parts = p.split("/")
            key = "/".join(parts[:3]) if p.startswith("/api/") and len(parts) > 2 else p
            groups[key].append((m.upper(), p, (op.get("summary") or "").replace("|", "/")))
total = sum(len(v) for v in groups.values())
out = ["# API 엔드포인트 목록 (자동 생성)", "",
       f"> `scripts/ops/gen_api_doc.py` 생성 — {date.today()} 기준 {total}개 / 그룹 {len(groups)}개. **수동 편집 금지**(서버 기동 후 재실행). 파라미터는 `http://127.0.0.1:8000/docs`.", ""]
for k in sorted(groups):
    out.append(f"## `{k}` ({len(groups[k])})")
    out.append(", ".join(f"`{m} {p[len(k):] or '/'}`" for m, p, _ in groups[k]))
    out.append("")
(ROOT / "docs" / "API_ENDPOINTS.md").write_text("\n".join(out), encoding="utf-8")
print(total, "endpoints,", len(groups), "groups -> docs/API_ENDPOINTS.md")
(ROOT / "scratch").mkdir(exist_ok=True)
json.dump({k: len(v) for k, v in groups.items()}, open(ROOT / "scratch" / "api_group_counts.json", "w"), ensure_ascii=False)
