"""
Project Antigravity: 통합 프로세스 및 포트 Watchdog 관리자
로컬 서버(8000, 8011, 5500, 8501) 프로세스 + 로컬 Claude/Codex 데스크톱 앱 & MCP 실시간 감시
"""

import os
import sys
import time
import socket
import subprocess
import argparse
from datetime import datetime
from typing import Dict, Any, List

try:
    import psutil
except ImportError:
    psutil = None

MANAGED_SERVICES = {
    "Antigravity Dashboard": {"port": 8501, "desc": "Streamlit 통합 관제 HUD"},
    "Stock Dashboard API": {"port": 8000, "desc": "주식 퀀트 백엔드 & DB"},
    "CEO Briefing API": {"port": 8011, "desc": "KAI/방산 인텔리전스 백엔드"},
    "KAI Newsinfo Frontend": {"port": 5500, "desc": "newsinfo.cloud 프론트엔드 콘솔"}
}

def is_port_in_use(port: int) -> bool:
    """포트 활성화 여부 확인"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def get_pid_by_port(port: int) -> List[int]:
    """특정 포트를 점유하고 있는 PID 목록 반환"""
    try:
        out = subprocess.check_output(f"lsof -ti :{port}", shell=True, text=True)
        return [int(p.strip()) for p in out.strip().split("\n") if p.strip()]
    except Exception:
        return []

def _get_process_cmdline(pid: int) -> str:
    """PID의 실행 커맨드라인 조회 (소유권 확인용). 조회 실패 시 빈 문자열"""
    if not psutil:
        return ""
    try:
        return " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return ""

def _is_owned_by_workspace(pid: int) -> bool:
    """이 워크스페이스에서 기동된 프로세스인지 커맨드라인 경로로 확인.
    소유권을 확인할 수 없으면 False (미확인 프로세스는 종료 대상에서 제외)"""
    workspace_root = os.path.dirname(os.path.abspath(__file__))
    cmdline = _get_process_cmdline(pid)
    return bool(cmdline) and workspace_root in cmdline

def clean_redundant_ports(dry_run: bool = True) -> List[Dict[str, Any]]:
    """중복/고아 임시 포트(5174, 8012 등) 점유 프로세스 정리.
    이 워크스페이스 소유가 확인된 프로세스만, dry_run=False일 때만 실제로 종료한다.
    상태 조회(get_system_status)에서는 호출하지 않는다 — 사용자가 명시적으로 호출해야 한다.
    8502는 bridge_api.py의 실제 운영 포트이므로 이 목록에서 제외한다."""
    redundant_ports = [5174, 8012]
    actions = []
    for port in redundant_ports:
        pids = get_pid_by_port(port)
        for pid in pids:
            owned = _is_owned_by_workspace(pid)
            action = {"port": port, "pid": pid, "owned": owned, "killed": False}
            if owned and not dry_run:
                try:
                    subprocess.run(["kill", "-9", str(pid)], check=False)
                    action["killed"] = True
                except Exception as e:
                    action["error"] = str(e)
            actions.append(action)
    return actions

def get_system_status() -> List[Dict[str, Any]]:
    """전체 서비스 헬스체크 및 포트 상태 조회 (읽기 전용, 프로세스/파일/DB 변경 없음)"""
    status_list = []
    for name, info in MANAGED_SERVICES.items():
        port = info["port"]
        active = is_port_in_use(port)
        pids = get_pid_by_port(port) if active else []
        status_list.append({
            "name": name,
            "port": port,
            "active": active,
            "pids": pids,
            "desc": info["desc"]
        })
    return status_list

def get_local_ai_apps_status() -> List[Dict[str, Any]]:
    """M4 로컬에서 구동 중인 Claude, Codex, MCP, IDE 프로세스 실시간 감시"""
    if not psutil:
        return []

    apps = [
        {"name": "Claude Desktop App & Code Agent", "keywords": ["/Applications/Claude.app", "claude-code", "Claude Helper"], "desc": "로컬 Claude 데스크톱 및 claude-code 백그라운드 에이전트"},
        {"name": "Codex / ChatGPT App (Local CUA)", "keywords": ["cua_node", ".codex/plugins", "launch.mjs"], "desc": "로컬 Computer-Use Codex 플러그인 & Node REPL"},
        {"name": "Local MCP (Model Context Protocol)", "keywords": ["mcp-pdf-server", "@modelcontextprotocol"], "desc": "PDF 및 OS 로컬 컨텍스트 브리지 서버"},
        {"name": "Antigravity IDE & AI Subagents", "keywords": ["Antigravity IDE", "pyrefly"], "desc": "Google Deepmind Antigravity IDE & LSP 언어 서버"}
    ]
    
    results = []
    for app in apps:
        matched_procs = []
        for p in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_info"]):
            try:
                cmd = " ".join(p.info["cmdline"] or [])
                if any(kw in cmd for kw in app["keywords"]):
                    matched_procs.append({
                        "pid": p.info["pid"],
                        "name": p.info["name"],
                        "cpu": p.info["cpu_percent"],
                        "mem_mb": round((p.info["memory_info"].rss if p.info["memory_info"] else 0) / (1024*1024), 1),
                        "cmd": cmd[:120]
                    })
            except Exception:
                pass
        
        status = "🟢 ACTIVE" if matched_procs else "⚪ IDLE"
        total_mem = round(sum(p["mem_mb"] for p in matched_procs), 1)
        results.append({
            "name": app["name"],
            "status": status,
            "desc": app["desc"],
            "proc_count": len(matched_procs),
            "total_mem_mb": total_mem,
            "sample_pids": [p["pid"] for p in matched_procs[:5]]
        })
    return results

def get_recent_code_modifications(limit: int = 12) -> List[Dict[str, Any]]:
    """외장 SSD 및 워크스페이스 내 최근 수정된 코드 파일 감시"""
    target_dirs = [
        "/Volumes/Realtek_NVME/AI System",
        "/Volumes/Realtek_NVME/stock_dashboard"
    ]
    files = []
    now = time.time()
    for base in target_dirs:
        if not os.path.exists(base):
            continue
        for root, _, filenames in os.walk(base):
            if any(ign in root for ign in [".git", "node_modules", "venv", "__pycache__", ".pytest_cache"]):
                continue
            for f in filenames:
                if f.endswith((".py", ".js", ".html", ".css", ".json", ".yaml", ".db", ".sh")):
                    full_path = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(full_path)
                        size_kb = round(os.path.getsize(full_path) / 1024, 1)
                        files.append({
                            "file": f,
                            "path": full_path.replace("/Volumes/Realtek_NVME/", ""),
                            "mtime": mtime,
                            "time_str": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            "size_kb": size_kb
                        })
                    except Exception:
                        pass
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files[:limit]
