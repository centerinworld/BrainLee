#!/usr/bin/env python3
"""docs/SYSTEM_MAP.md 생성 — AI 세션용 압축 시스템 지도 (2026-09-27, 토큰 최적화).

관리자 화면(/admin/system_map)과 같은 집계(system_map.py)를 마크다운으로 저장한다. 세션 시작 시 코드를 훑는 대신 이 파일 하나(수 KB)를 읽으면 된다.
`.claude/hooks/session_stop.sh` 가 매 응답 종료 시 자동 재생성한다(소스 fingerprint 가 그대로면 건너뜀 — 비용 거의 없음).
  cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python scripts/ops/gen_system_map_doc.py [--force]
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import system_map as sm  # noqa: E402

OUT = ROOT / "docs" / "SYSTEM_MAP.md"


def main() -> int:
    fp = sm.fingerprint()
    if not ("--force" in sys.argv) and OUT.exists() and f"fingerprint: {fp}" in OUT.read_text(encoding="utf-8")[:400]:
        print("변경 없음 — 건너뜀")
        return 0
    d = sm.overview("")
    st, live = d["static"], d["live"]
    L = ["# 시스템 지도 (자동 생성 — 수동 편집 금지)", "",
         f"> fingerprint: {fp} · {datetime.now():%Y-%m-%d %H:%M} · 생성: `scripts/ops/gen_system_map_doc.py` (관리자 화면 `/admin/system_map` 과 동일 집계). 코드 수정 후 세션이 끝나면 훅이 재생성.",
         "> 무엇이 어디 있는지 먼저 여기서 찾고, 파일은 필요한 줄만 읽을 것(큰 파일은 아래 표).", "",
         "## 사이트 구조", "",
         "| 진입 | 경로 | 내용 | 코드 |", "|---|---|---|---|",
         "| Stock Hub(첫 화면) | `stock.leanguy.cloud/` | 4개 메인 선택 + 관리자 | `frontend/src/hub/Landing.jsx` |",
         "| Stock Info | `/info/<탭>` | 시황·종목·섹터·공시 조회 | `App.jsx` + `views/` (탭 구성 `hub/modules.js`) |",
         "| Key Indicator | `newsinfo.cloud` | 별도 사이트(CEO 브리핑 콘솔) | `AI System/codex/ceo-briefing-platform/frontend` (`hubbar.js` 로 허브 바 주입) |",
         "| Stock Lab | `/lab/<탭>` | 전략·백테스트·발굴 | 위와 동일 |",
         "| Stock LLM | `/llm/` → `:8888` | Brian_RAG(하이브리드 RAG). 관리자 로그인 필요 | `/Volumes/Realtek_NVME/Brian_RAG/web_app.py`, 프록시 `routes/llm_proxy.py` |",
         "| 관리자 | `/admin/<탭>` | 비밀번호 로그인(`routes/admin_auth.py`, 쿠키 `sd_admin`) → 개요·시스템 현황·수집 상태·리스크게이트·설정 | `hub/AdminGate.jsx`, `hub/AdminSystemMap.jsx` |", "",
         "## 규모", "", "| 영역 | 파일 | 줄 |", "|---|--:|--:|"]
    L += [f"| {a['area']} | {a['files']} | {a['lines']:,} |" for a in st["code"]["areas"]]
    L += ["", "가장 큰 파일: " + ", ".join(f"`{x['path']}` {x['lines']:,}" for x in st["code"]["largest"]), "",
          f"## API — {st['endpoints']['total']}개 (상위 그룹)", "",
          ", ".join(f"`{g['prefix']}` {g['count']}" for g in st["endpoints"]["groups"]), "",
          "전체 목록: `docs/API_ENDPOINTS.md` (`scripts/ops/gen_api_doc.py`).", "",
          f"## 스케줄러 — 활성 {st['scheduler']['active']} / 전체 {st['scheduler']['total']}", "",
          "잡 이름·설명 전체: `docs/SCHEDULER_JOBS.md`. 비활성: " + (", ".join(j["name"] for j in st["scheduler"]["jobs"] if j["disabled"]) or "없음"), "",
          "## 데이터 계보 (데이터셋 → 테이블 → 쓰는 곳 → 읽는 API → 화면)", "",
          "| 데이터셋 | 출처 · 주기 | 테이블 | 수집 잡/쓰는 파일 | 읽는 API 라우트 | 화면 |", "|---|---|---|---|---|---|"]
    for e in st["lineage"]:
        writers = ", ".join(e["jobs"] + [w.split("/")[-1] for w in e["writers"][:2]]) or "–"
        L.append(f"| {e['label']} | {e['source']} · {e['schedule']} | `{e['table']}` ({e['rows'] or 0:,}행) | {writers} | {', '.join(e['apis'][:3]) or '–'} | {', '.join(e['screens'][:3]) or '–'} |")
    L += ["", f"## 데이터 신선도 (수집 계약 {len(live['freshness'])}개)", ""]
    bad = [f for f in live["freshness"] if f["status"] != "healthy"]
    L.append("모두 정상." if not bad else "주의: " + ", ".join(f"{f['label']}={f['status']}" for f in bad))
    L += ["", "## DB", "", f"PostgreSQL {live['db']['size_mb'] or 0:,} MB · 테이블 {live['db']['table_count']}개. 상위: " +
          ", ".join(f"`{t['table']}` {t['mb']:,}MB" for t in live["db"]["top"][:6]) + " — 스키마 전체 `docs/DB_TABLES_PG.md`.", "",
          "## 서비스", "", ", ".join(f"{s['name']}{' :' + str(s['port']) if s['port'] else ''} {'●' if s['up'] else '○(중지)'}" for s in live["services"])]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)} 저장 ({OUT.stat().st_size / 1024:.1f}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
