#!/bin/bash
# 2026-08-25: launchctl kickstart -k 가 무거운 연산 중인 프로세스에 SIGTERM을 보내도
# 즉시 반응하지 않아, launchd가 새 프로세스를 띄운 뒤에도 구 프로세스가 포트 없이
# CPU만 계속 점유하는 고아 프로세스로 남는 사고가 2026-08-23/24 세션에서 각 1회 재발함.
# 이 스크립트는 재시작 전후 PID 집합을 비교해 고아를 자동 탐지·정리한다.
set -euo pipefail

LABEL="gui/$(id -u)/com.stock-dashboard.local"
PORT=8000

before_pids=$(pgrep -f "uvicorn main:app --host 127.0.0.1 --port ${PORT}" || true)
echo "재시작 전 PID: ${before_pids:-없음}"

launchctl kickstart -k "$LABEL"

# launchd가 새 프로세스를 띄우고 포트 바인딩할 시간을 준다
for i in $(seq 1 15); do
    sleep 1
    if lsof -i ":${PORT}" -P 2>/dev/null | grep -q LISTEN; then
        break
    fi
done

listening_pid=$(lsof -i ":${PORT}" -P 2>/dev/null | awk '/LISTEN/{print $2}' | head -1)
echo "새로 포트 점유 중인 PID: ${listening_pid:-확인불가}"

if [ -z "${listening_pid:-}" ]; then
    echo "⚠️ 재시작 후 포트 ${PORT}가 열리지 않았습니다 — 수동 확인 필요"
    exit 1
fi

orphans=""
for pid in $before_pids; do
    if [ "$pid" != "$listening_pid" ] && ps -p "$pid" > /dev/null 2>&1; then
        orphans="$orphans $pid"
    fi
done

if [ -n "$orphans" ]; then
    echo "🧹 고아 프로세스 발견, 정리:$orphans"
    for pid in $orphans; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 3
    for pid in $orphans; do
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "  PID $pid 강제종료(SIGKILL)"
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
else
    echo "고아 프로세스 없음 — 정상"
fi

echo "=== 최종 상태 ==="
ps aux | grep "uvicorn main:app --host 127.0.0.1 --port ${PORT}" | grep -v grep
curl -s -o /dev/null -w "서버 응답: %{http_code}\n" "http://127.0.0.1:${PORT}/api/dashboard/stats" --max-time 10
