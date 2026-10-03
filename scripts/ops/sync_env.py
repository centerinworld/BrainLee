#!/usr/bin/env python3
"""기준 .env(runtime/.env)를 모든 사용처에 퍼뜨린다(2026-10-03, docs/ENV_MANAGEMENT.md).

- 링크(symlink): 같은 값을 그대로 쓰는 곳 — 루트 .env, antigravity_workspace, 모든 worktree(.claude/·Codex)
- 생성: 예외값이 있는 곳 — ceo-briefing-platform/.env = 기준 + env_overrides/ceo-briefing.env (같은 키는 예외값이 이김)
기존 일반 파일을 링크로 바꿀 때는 backups/env_sync_<시각>/ 에 원본을 보관한다. launchd(WatchPaths)가 기준 파일 변경 시 자동 실행.
"""
import glob
import os
import re
import shutil
import time
from pathlib import Path

RT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
MASTER = RT / ".env"
LINKS = ["/Volumes/Realtek_NVME/stock_dashboard/.env", "/Volumes/Realtek_NVME/AI System/antigravity_workspace/.env"]
LINKS += glob.glob("/Volumes/Realtek_NVME/stock_dashboard/.claude/worktrees/*/.env")
LINKS += glob.glob(str(RT / ".claude/worktrees/*/.env"))
LINKS += glob.glob("/Users/brainlee/.codex/worktrees/*/runtime/.env")
GENERATED = {"/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/.env": RT / "env_overrides/ceo-briefing.env"}
BK = Path(f"/Volumes/Realtek_NVME/stock_dashboard/backups/env_sync_{time.strftime('%Y%m%d_%H%M%S')}")


def backup(p):
    BK.mkdir(parents=True, exist_ok=True)
    os.chmod(BK, 0o700)
    dst = BK / re.sub(r"[^A-Za-z0-9_.-]", "_", p.strip("/"))
    shutil.copy2(p, dst)
    os.chmod(dst, 0o600)


def main():
    n_link = n_gen = 0
    for p in LINKS:
        if os.path.islink(p) and os.path.realpath(p) == str(MASTER):
            continue
        if os.path.exists(p):
            backup(p)
            os.remove(p)
        os.symlink(MASTER, p)
        n_link += 1
    base = MASTER.read_text(encoding="utf-8")
    for p, ov in GENERATED.items():
        o = ov.read_text(encoding="utf-8") if ov.exists() else ""
        keys = {m.group(1) for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)=", o, re.M)}
        body = "\n".join(l for l in base.splitlines() if not any(l.startswith(k + "=") for k in keys))
        txt = ("# ⚠ 자동 생성 파일 — 직접 수정 금지. 기준: runtime/.env, 예외: runtime/env_overrides/" + ov.name +
               " (scripts/ops/sync_env.py)\n" + body + "\n\n# ── 예외값 ──\n" + o)
        if not os.path.exists(p) or open(p, encoding="utf-8").read() != txt:
            if os.path.exists(p) and not os.path.islink(p):
                backup(p)
            if os.path.islink(p):
                os.remove(p)
            Path(p).write_text(txt, encoding="utf-8")
            os.chmod(p, 0o600)
            n_gen += 1
    print(f"링크 {n_link}건 새로 연결, 생성 파일 {n_gen}건 갱신, 백업 {BK if BK.exists() else '없음'}")


if __name__ == "__main__":
    main()
