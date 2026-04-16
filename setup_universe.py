#!/usr/bin/env python3
"""
setup_universe.py — stock_universe 최초 설정 스크립트

실행 순서:
  1. python3 setup_universe.py  ← 이 파일 1회 실행으로 전부 완료
"""

import subprocess, sys, os
from pathlib import Path

BASE = Path("/Applications/stock_dashboard")
VENV_PYTHON = BASE / "venv/bin/python3"
EXCEL_PATH  = BASE / "주식종목전체검색.xlsx"  # ← 엑셀 파일을 여기에 복사해 두세요

def run(cmd: str):
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout: print(result.stdout.strip())
    if result.returncode != 0:
        print(f"[ERROR] {result.stderr.strip()}")
    return result.returncode == 0

print("=" * 60)
print("stock_universe 초기 설정")
print("=" * 60)

# 1. stock_universe.py 복사
print("\n[1] stock_universe.py 복사")
run(f"cp /Applications/stock_dashboard/stock_universe.py {BASE}/stock_universe.py || true")

# 2. DB 테이블 생성 + 엑셀 적재
print("\n[2] DB 테이블 생성 + 엑셀 적재")
if EXCEL_PATH.exists():
    run(f"cd {BASE} && source venv/bin/activate && python3 stock_universe.py --init-excel {EXCEL_PATH}")
else:
    print(f"  ⚠ 엑셀 파일 없음: {EXCEL_PATH}")
    print("  → 엑셀 파일을 위 경로에 복사 후 아래 명령 실행:")
    print(f"    cd {BASE} && source venv/bin/activate && python3 stock_universe.py --init-excel 주식종목전체검색.xlsx")

# 3. 안내
print("\n[3] main.py에 라우터 등록 (수동)")
print("""
  main.py 상단 import 추가:
    from stock_universe import get_universe_router, init_tables as init_universe

  app 선언 직후 추가:
    init_universe()
    app.include_router(get_universe_router())
""")

# 4. 자정 스케줄러 안내
print("[4] 자정 스케줄러 (별도 터미널에서 실행)")
print(f"""
  cd {BASE} && source venv/bin/activate
  python3 stock_universe.py --schedule
  # → 매일 자정 KRX API로 전종목 시세 자동 업데이트
""")

# 5. KRX 1회 테스트
print("[5] KRX API 수동 업데이트 (테스트)")
print(f"""
  cd {BASE} && source venv/bin/activate
  python3 stock_universe.py --update-krx
  python3 stock_universe.py --stats
""")

print("=" * 60)
print("완료! API 엔드포인트:")
print("  GET  /api/universe           전종목 목록 (최신 기준일)")
print("  GET  /api/universe/search?q= 종목명/코드 검색")
print("  GET  /api/universe/sectors   업종별 집계")
print("  GET  /api/universe/stats     전체 통계")
print("  POST /api/universe/update/krx  수동 KRX 업데이트 트리거")
print("=" * 60)
