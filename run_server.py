import os
import subprocess
import sys
import signal

# 규칙 7: 한글로 가이드 제공

BACKEND_PORT = 8000
FRONTEND_PORT = 5173  # Vite 기본 포트 (5174 충돌 시 자동 해제)


def kill_port(port: int):
    """지정한 포트를 사용 중인 프로세스를 종료합니다."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                    print(f"  포트 {port} 점유 프로세스(PID {pid}) 종료")
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True
            )
            pids = result.stdout.strip().split()
            for pid in pids:
                if pid:
                    os.kill(int(pid), signal.SIGTERM)
                    print(f"  포트 {port} 점유 프로세스(PID {pid}) 종료")
    except Exception as e:
        print(f"  포트 {port} 해제 중 오류 (무시): {e}")


def run():
    """주식 분석 백엔드 서버 통합 실행 프로그램"""
    print("=" * 55)
    print("프로젝트 안티그래비티: 백엔드 서버 시작")
    print("=" * 55)

    # ── 가상환경 확인 ──────────────────────────────────────────
    venv_path = os.path.join(os.getcwd(), "venv")
    if not os.path.exists(venv_path):
        print("[오류] 가상환경(venv)을 찾을 수 없습니다.")
        sys.exit(1)

    # ── 포트 충돌 사전 해제 ────────────────────────────────────
    print(f"\n포트 점유 확인 중 (백엔드:{BACKEND_PORT})...")
    kill_port(BACKEND_PORT)

    # ── uvicorn 경로 (플랫폼 호환) ─────────────────────────────
    if sys.platform == "win32":
        uvicorn_bin = os.path.join(venv_path, "Scripts", "uvicorn.exe")
    else:
        uvicorn_bin = os.path.join(venv_path, "bin", "uvicorn")

    if not os.path.exists(uvicorn_bin):
        print(f"[오류] uvicorn을 찾을 수 없습니다: {uvicorn_bin}")
        print("  → pip install uvicorn 으로 설치하세요.")
        sys.exit(1)

    print(f"\n백엔드 서버 시작 → http://127.0.0.1:{BACKEND_PORT}")
    print(f"API 문서          → http://127.0.0.1:{BACKEND_PORT}/docs\n")

    try:
        subprocess.run([
            uvicorn_bin, "main:app",
            "--host", "0.0.0.0",
            "--port", str(BACKEND_PORT),
            "--reload",
        ])
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
    except Exception as e:
        print(f"\n[오류] 서버 실행 중 문제 발생: {e}")


if __name__ == "__main__":
    run()
