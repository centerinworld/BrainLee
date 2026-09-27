"""system_map.py — 시스템 전체 지도(코드·API·스케줄러·데이터·프로세스)를 소스에서 **자동 집계** (2026-09-27, 사이트 전면 개편)

관리자 화면(/admin/system_map)과 `docs/SYSTEM_MAP.md`(AI 세션용 압축 지도, scripts/ops/gen_system_map_doc.py)가 같은 함수를 쓴다.
수기로 관리하는 목록이 아니라 코드에서 읽어오므로 **파일을 수정하면 다음 조회에 바로 반영**된다(fingerprint = 소스 mtime·크기 해시, 변하면 정적 부분 재계산).

  static  (fingerprint 키 캐시): 코드 규모, API 엔드포인트, 스케줄러 잡, 데이터셋 계보(테이블→쓰는 파일→읽는 API→화면), 최근 수정 파일, git
  live    (짧은 TTL 캐시)     : 서비스 포트·프로세스, 데이터 신선도(collection_health), 최근 수집 실행, DB 용량

계보(lineage)는 텍스트 스캔이라 정밀 파서가 아니다 — `INSERT/UPDATE … <table>` 이 있는 파일을 쓰는 쪽, routes/*.py 에서 테이블명이 나오는 파일을 읽는 쪽으로 본다.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FRONT = ROOT / "frontend" / "src"
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".venvs", "dist", "dist_new", "backups", "scratch", ".git", "research_outputs", "data", "data_cache"}
_lock = threading.Lock()
_static_cache: dict[str, Any] = {"fp": "", "data": None}
_live_cache: dict[str, Any] = {"at": 0.0, "data": None}
_db_cache: dict[str, Any] = {"at": 0.0, "data": None}
LIVE_TTL, DB_TTL = 20.0, 300.0


# ── fingerprint: 소스가 바뀌면 값이 바뀐다 ──────────────────────────────────────────────────────
def _code_dirs() -> list[tuple[Path, bool, tuple[str, ...]]]:
    """(경로, 재귀 여부, 확장자)"""
    return [
        (ROOT, False, (".py",)), (ROOT / "routes", False, (".py",)), (ROOT / "collectors", False, (".py",)),
        (ROOT / "scripts", False, (".py", ".sh")), (ROOT / "scripts" / "ops", False, (".py",)), (ROOT / "tests", False, (".py",)),
        (ROOT / "backtest_strategies", False, (".py",)), (ROOT / "ETF_check", False, (".py",)),
        (FRONT, True, (".jsx", ".js", ".css")),
    ]


def _iter_files():
    for base, recursive, exts in _code_dirs():
        if not base.exists():
            continue
        if recursive:
            for dp, dns, fns in os.walk(base):
                dns[:] = [d for d in dns if d not in SKIP_DIRS]
                for fn in fns:
                    if fn.endswith(exts):
                        yield Path(dp) / fn
        else:
            with os.scandir(base) as it:
                for e in it:
                    if e.is_file() and e.name.endswith(exts):
                        yield Path(e.path)


def fingerprint() -> str:
    n, newest, size = 0, 0.0, 0
    for p in _iter_files():
        try:
            st = p.stat()
        except OSError:
            continue
        n += 1
        newest = max(newest, st.st_mtime)
        size += st.st_size
    extra = ""
    for g in (ROOT / ".git" / "HEAD", ROOT / ".git" / "index", ROOT / "frontend" / "dist" / "index.html"):
        try:
            extra += f"{g.name}:{g.stat().st_mtime:.0f};"
        except OSError:
            pass
    return hashlib.sha1(f"{n}|{newest:.3f}|{size}|{extra}".encode()).hexdigest()[:16]


# ── 정적 집계 ────────────────────────────────────────────────────────────────────────────────
def _loc(p: Path) -> int:
    try:
        with p.open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _code_metrics() -> dict[str, Any]:
    areas = {
        "백엔드 코어": (ROOT, False, (".py",)), "API 라우터": (ROOT / "routes", False, (".py",)), "수집기": (ROOT / "collectors", False, (".py",)),
        "배치·운영 스크립트": (ROOT / "scripts", True, (".py", ".sh")), "백테스트 전략": (ROOT / "backtest_strategies", False, (".py",)),
        "테스트": (ROOT / "tests", False, (".py",)), "프런트엔드": (FRONT, True, (".jsx", ".js", ".css")),
    }
    out, biggest = [], []
    for name, (base, rec, exts) in areas.items():
        files, lines = 0, 0
        it = os.walk(base) if rec else [(str(base), [], [e.name for e in os.scandir(base) if e.is_file()])] if base.exists() else []
        for dp, dns, fns in it:
            if rec:
                dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                if fn.endswith(exts):
                    p = Path(dp) / fn
                    n = _loc(p)
                    files += 1
                    lines += n
                    biggest.append((n, str(p.relative_to(ROOT))))
        out.append({"area": name, "files": files, "lines": lines})
    biggest.sort(reverse=True)
    return {"areas": out, "largest": [{"path": p, "lines": n} for n, p in biggest[:8]]}


_DECOR = re.compile(r'@(?:router|app)\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)', re.I)
_INCLUDE = re.compile(r'app\.include_router\(\s*(_\w+)\s*(?:,\s*prefix\s*=\s*["\']([^"\']+)["\'])?')
_IMPORT_ROUTER = re.compile(r'from\s+routes\.(\w+)\s+import\s+router\s+as\s+(_\w+)')
_OWN_PREFIX = re.compile(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']+)["\']')


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _endpoint_metrics(app=None) -> dict[str, Any]:
    """라우트 파일·main.py 의 데코레이터로 센다(app 이 있으면 등록된 라우트로 정확히 센다). 그룹 = /api/<첫 세그먼트>."""
    groups: dict[str, int] = {}
    total = 0
    if app is not None:
        for r in app.routes:
            path = getattr(r, "path", "")
            methods = getattr(r, "methods", None)
            if not methods or not path.startswith("/api/"):
                continue
            g = "/api/" + path.split("/")[2] if path.count("/") >= 2 else path
            n = len([m for m in methods if m not in ("HEAD", "OPTIONS")])
            groups[g] = groups.get(g, 0) + n
            total += n
    else:
        main_src = _read(ROOT / "main.py")
        mod_var = {v: m for m, v in _IMPORT_ROUTER.findall(main_src)}
        prefix_of = {mod_var[v]: pfx for v, pfx in _INCLUDE.findall(main_src) if v in mod_var and pfx}
        for p in [ROOT / "main.py", *sorted((ROOT / "routes").glob("*.py"))]:
            src = _read(p)
            base = prefix_of.get(p.stem, "") or (_OWN_PREFIX.search(src).group(1) if _OWN_PREFIX.search(src) else "")
            for _, path in _DECOR.findall(src):
                full = (base + path) if p.name != "main.py" else path
                if not full.startswith("/api/"):
                    continue
                g = "/api/" + full.split("/")[2]
                groups[g] = groups.get(g, 0) + 1
                total += 1
    top = sorted(groups.items(), key=lambda kv: -kv[1])
    return {"total": total, "groups": [{"prefix": k, "count": v} for k, v in top[:14]], "group_count": len(groups)}


def _scheduler_jobs() -> list[dict[str, Any]]:
    src = _read(ROOT / "scheduler.py")
    first = src.find('("월간업데이트"')
    if first < 0:
        return []
    start = src.rfind("[", 0, first)
    block = src[start: src.index("\n        ]", first)]
    rows = []
    for m in re.finditer(r'\("([^"]+)",\s*self\.(\w+)\),?\s*(?:#\s*(.*))?', block):
        desc = (m.group(3) or "").strip()
        rows.append({"name": m.group(1), "fn": m.group(2), "desc": desc.lstrip("★⛔ ").strip(), "disabled": desc.startswith("⛔")})
    return rows


def _table_rows() -> dict[str, dict[str, Any]]:
    """PostgreSQL 통계 기반 테이블 크기/추정 행수 (5분 캐시)"""
    now = time.time()
    if _db_cache["data"] is not None and now - _db_cache["at"] < DB_TTL:
        return _db_cache["data"]
    data: dict[str, Any] = {"tables": {}, "db_size_mb": None, "table_count": None}
    try:
        from db_compat import connect_primary_db
        conn = connect_primary_db(readonly=True, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT c.relname, GREATEST(c.reltuples,0)::bigint, pg_total_relation_size(c.oid)/1048576 FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','v','m')")
        for name, rows, mb in [tuple(r) for r in cur.fetchall()]:
            data["tables"][name] = {"rows": int(rows), "mb": int(mb)}
        cur.execute("SELECT pg_database_size(current_database())/1048576")
        data["db_size_mb"] = int([tuple(r) for r in cur.fetchall()][0][0])
        data["table_count"] = len(data["tables"])
        conn.close()
    except Exception as e:  # noqa: BLE001 - 통계 실패가 지도 전체를 막지 않게
        data["error"] = type(e).__name__
    _db_cache.update({"at": now, "data": data})
    return data


# 계약(collection_health)에 없는 핵심 데이터 — 재무·마스터·전략 산출물. 신선도 계약이 없어 행수만 본다.
EXTRA_DATASETS = [
    ("stock_master", "종목 마스터", "stock_universe", "KRX/네이버", "일별 KRX 잡(시총·주식수)"),
    ("financials", "재무제표", "financial_data", "DART/FnGuide", "월간·공시 후 증분"),
    ("cashflow", "현금흐름표", "cash_flow_data", "DART", "월간 현금흐름배치"),
    ("valuation", "밸류에이션 이력", "valuation_history", "재무+가격 계산", "재무 갱신 후"),
    ("credit", "신용잔고", "kiwoom_credit_balance", "Kiwoom ka10013", "영업일"),
    ("signals", "시그널 결과", "signal_result", "signal_engine", "장마감 후"),
    ("feature_snapshot", "전략 피처 스냅샷", "strategy_feature_snapshot", "build_strategy_research_dataset", "월간 재생성"),
    ("backtests", "백테스트 결과", "backtest_runs", "backtest_strategies", "수동·연구 실행"),
    ("portfolio", "내 포트폴리오", "portfolio", "수동 입력/KIS", "수시"),
    ("virtual_trading", "가상매매 보유", "peak_holding", "peak_monitor", "장중"),
    ("reports", "섹터 보고서", "report_files", "텔레그램/PDF 수집", "매일"),
]


def _lineage(contracts, jobs_by_dataset, app_endpoint_prefixes) -> list[dict[str, Any]]:
    """데이터셋별: 쓰는 파일(수집·배치) / 읽는 API 라우트 / 그 API를 부르는 프런트 파일"""
    entries = [{"key": c.key, "label": c.label, "table": c.table, "source": c.source, "schedule": c.schedule, "contract": True} for c in contracts]
    have = {e["table"] for e in entries}
    entries += [{"key": k, "label": lb, "table": t, "source": s, "schedule": sc, "contract": False} for k, lb, t, s, sc in EXTRA_DATASETS if t not in have]
    tables = sorted({e["table"] for e in entries}, key=len, reverse=True)
    alt = "|".join(re.escape(t) for t in tables)
    any_re = re.compile(rf"\b({alt})\b")
    write_re = re.compile(rf"(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+(?:public\.)?({alt})\b", re.I)

    writers: dict[str, set] = {t: set() for t in tables}
    readers: dict[str, set] = {t: set() for t in tables}
    write_dirs = [ROOT, ROOT / "collectors", ROOT / "scripts", ROOT / "scripts" / "ops", ROOT / "ETF_check"]
    for d in write_dirs:
        if not d.exists():
            continue
        for e in os.scandir(d):
            if not (e.is_file() and e.name.endswith(".py")) or e.name in ("main.py",):
                continue
            p = Path(e.path)
            if p.stat().st_size > 2_000_000:
                continue
            for m in set(write_re.findall(_read(p))):
                writers[m.lower() if m.lower() in writers else m].add(str(p.relative_to(ROOT)))
    for p in [ROOT / "main.py", *sorted((ROOT / "routes").glob("*.py"))]:
        for t in set(any_re.findall(_read(p))):
            readers[t].add(p.stem if p.name != "main.py" else "main")

    # 라우트 모듈 → /api/<세그먼트>
    main_src = _read(ROOT / "main.py")
    mod_var = {v: m for m, v in _IMPORT_ROUTER.findall(main_src)}
    mod_prefix = {mod_var[v]: pfx for v, pfx in _INCLUDE.findall(main_src) if v in mod_var and pfx}
    for p in (ROOT / "routes").glob("*.py"):
        if p.stem not in mod_prefix:
            m = _OWN_PREFIX.search(_read(p))
            if m:
                mod_prefix[p.stem] = m.group(1)
    seg_files: dict[str, set] = {}
    for dp, dns, fns in os.walk(FRONT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.endswith((".jsx", ".js")):
                for seg in set(re.findall(r"['\"`]/api/([a-z0-9\-]+)", _read(Path(dp) / fn))):
                    seg_files.setdefault(seg, set()).add(fn)

    out = []
    for e in entries:
        t = e["table"]
        apis = sorted(readers.get(t, set()))
        segs = {mod_prefix[a].split("/")[2] for a in apis if a in mod_prefix and mod_prefix[a].count("/") >= 2}
        screens = sorted({f for s in segs for f in seg_files.get(s, set())})
        out.append({**e, "jobs": jobs_by_dataset.get(e["key"], []), "writers": sorted(writers.get(t, set()))[:6], "writer_count": len(writers.get(t, set())),
                    "apis": apis[:8], "api_count": len(apis), "screens": screens[:8], "screen_count": len(screens)})
    return out


def _git() -> dict[str, Any]:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=4).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""
    log = run("log", "-8", "--pretty=%h|%cr|%s")
    commits = [dict(zip(("hash", "when", "subject"), ln.split("|", 2))) for ln in log.splitlines() if ln.count("|") >= 2]
    return {"branch": run("rev-parse", "--abbrev-ref", "HEAD"), "dirty": len([x for x in run("status", "--porcelain").splitlines() if x]), "commits": commits}


def _recent_files(limit: int = 14) -> list[dict[str, Any]]:
    rows = []
    for p in _iter_files():
        try:
            rows.append((p.stat().st_mtime, str(p.relative_to(ROOT))))
        except OSError:
            pass
    rows.sort(reverse=True)
    return [{"path": p, "mtime": int(m)} for m, p in rows[:limit]]


def _static(app=None) -> dict[str, Any]:
    from collection_health import DATASET_CONTRACTS, JOB_DATASET_KEYS
    jobs = _scheduler_jobs()
    jobs_by_dataset: dict[str, list[str]] = {}
    for job, keys in JOB_DATASET_KEYS.items():
        for k in keys:
            jobs_by_dataset.setdefault(k, []).append(job)
    lineage = _lineage(DATASET_CONTRACTS, jobs_by_dataset, None)
    code = _code_metrics()
    ep = _endpoint_metrics(app)
    collectors = next((a["files"] for a in code["areas"] if a["area"] == "수집기"), 0)
    sources = sorted({e["source"] for e in lineage if e["source"]})
    return {
        "code": code, "endpoints": ep,
        "scheduler": {"total": len(jobs), "active": len([j for j in jobs if not j["disabled"]]), "disabled": len([j for j in jobs if j["disabled"]]), "jobs": jobs},
        "lineage": lineage, "sources": sources,
        "pipeline": [
            {"stage": "외부 데이터원", "count": len(sources), "unit": "종", "note": "KIS·Kiwoom·KRX·DART·FnGuide·yfinance·네이버 등"},
            {"stage": "수집기·잡", "count": collectors, "unit": "개 수집기 / 스케줄러 잡 %d개" % len(jobs), "note": "collectors/ · scheduler.py · launchd/cron"},
            {"stage": "검증·계약", "count": len(DATASET_CONTRACTS), "unit": "개 데이터 계약", "note": "collection_health: 신선도·커버리지 판정, 실패 시 텔레그램"},
            {"stage": "저장소(PostgreSQL)", "count": None, "unit": "테이블", "note": "db_compat.connect_primary_db"},
            {"stage": "엔진·분석", "count": None, "unit": "", "note": "signal_engine · tenbagger_engine · peak_monitor · backtest_strategies"},
            {"stage": "API", "count": ep["total"], "unit": "개 엔드포인트", "note": "FastAPI routes/ · security_gate(공개/열람/관리자)"},
            {"stage": "화면", "count": None, "unit": "", "note": "Stock Info · Stock Lab · 관리자 (React)"},
        ],
        "git": _git(), "recent": _recent_files(),
    }


# ── 실시간 부분 ──────────────────────────────────────────────────────────────────────────────
def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pg_port() -> int:
    m = re.search(r":(\d+)/", os.environ.get("POSTGRES_DATABASE_URL", ""))
    return int(m.group(1)) if m else 5432


def _tunnels() -> int:
    try:
        return len([x for x in subprocess.run(["pgrep", "-f", "cloudflared tunnel"], capture_output=True, text=True, timeout=2).stdout.split() if x])
    except Exception:  # noqa: BLE001
        return 0


def _services() -> list[dict[str, Any]]:
    svc = [
        ("backend", "백엔드 API (FastAPI)", 8000), ("frontend", "프런트 서버 (vite preview)", 5173), ("db", "PostgreSQL", _pg_port()),
        ("llm", "Stock LLM (Brian_RAG)", 8888), ("key", "Key Indicator 정적 서버", 5500), ("keyapi", "Key Indicator API", 8011),
    ]
    out = [{"key": k, "name": n, "port": p, "up": True if k == "backend" else _port_open(p)} for k, n, p in svc]
    t = _tunnels()
    out.append({"key": "tunnel", "name": "Cloudflare 터널 프로세스", "port": None, "up": t > 0, "detail": f"{t}개"})
    return out


def _uptime() -> int | None:
    try:
        import psutil
        return int(time.time() - psutil.Process(os.getpid()).create_time())
    except Exception:  # noqa: BLE001
        return None


def _live() -> dict[str, Any]:
    now = time.time()
    if _live_cache["data"] is not None and now - _live_cache["at"] < LIVE_TTL:
        return _live_cache["data"]
    data: dict[str, Any] = {"services": _services(), "uptime_s": _uptime()}
    try:
        from collection_health import evaluate_all_contracts, latest_collection_runs
        items = evaluate_all_contracts()
        data["freshness"] = [{"key": i["key"], "label": i["label"], "status": i["status"], "as_of": i.get("source_as_of"), "lag": i.get("lag"),
                              "issues": i.get("issues", [])[:2]} for i in items]
        runs = latest_collection_runs(limit=40)
        data["runs"] = [{"job": r["job_name"], "status": r["status"], "started_at": r["started_at"], "duration": r.get("duration_seconds"),
                         "error": (r.get("error") or "")[:120]} for r in runs[:14]]
        data["run_summary"] = {"total": len(runs), "failed": len([r for r in runs if r["status"] in ("failed", "error")]),
                               "running": len([r for r in runs if r["status"] == "running"])}
    except Exception as e:  # noqa: BLE001
        data["freshness"], data["runs"], data["run_error"] = [], [], type(e).__name__
    db = _table_rows()
    top = sorted(db["tables"].items(), key=lambda kv: -kv[1]["mb"])[:10]
    data["db"] = {"size_mb": db.get("size_mb") or db.get("db_size_mb"), "table_count": db.get("table_count"),
                  "top": [{"table": t, **v} for t, v in top], "error": db.get("error")}
    _live_cache.update({"at": now, "data": data})
    return data


def overview(client_fp: str = "", app=None) -> dict[str, Any]:
    """client_fp 가 현재 fingerprint 와 같으면 static 을 생략한다(변경 없음 — 폴링 비용 절감)."""
    fp = fingerprint()
    with _lock:
        if _static_cache["fp"] != fp or _static_cache["data"] is None:
            data = _static(app)
            # 테이블 행수 병합(계보 항목)
            tabs = _table_rows()["tables"]
            for e in data["lineage"]:
                e["rows"] = tabs.get(e["table"], {}).get("rows")
                e["exists"] = e["table"] in tabs if tabs else None
            data["generated_at"] = int(time.time())
            _static_cache.update({"fp": fp, "data": data})
        static = _static_cache["data"]
    live = _live()
    pipe = static["pipeline"]
    for st in pipe:
        if st["stage"] == "저장소(PostgreSQL)":
            st["count"] = live["db"]["table_count"]
    return {"fingerprint": fp, "changed": client_fp != fp, "server_time": int(time.time()),
            "static": static if client_fp != fp else None, "live": live}
