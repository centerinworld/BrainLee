# 시스템 구조 개편 계획 — 2026-05-16

## 현황: 루트 Python 파일 난립 (77개)

### 문제점
- 엔트리포인트, 배치 스크립트, 마이그레이션, 디버그, 임시 패치가 모두 루트에 혼재
- 역할 파악에 시간 소요, 신규 파일 위치 혼란

---

## 제안: 목적별 분류 (실제 이동은 승인 후)

### 현재 → 제안 경로 매핑표

| 현재 경로 | 제안 경로 | 이유 |
|-----------|-----------|------|
| `main.py` | `main.py` (유지) | FastAPI 앱 — 이동 금지 |
| `run_server.py` | `run_server.py` (유지) | 엔트리포인트 |
| `scheduler.py` | `scheduler.py` (유지) | 스케줄러 |
| `data_collector.py` | `collectors/runner.py` | 수집기 실행기 |
| `collect_dart_cashflow_batch.py` | `scripts/collect/` | 배치 수집 |
| `collect_dart_disclosures.py` | `scripts/collect/` | 배치 수집 |
| `collect_kis_ohlcv.py` | `scripts/collect/` | 배치 수집 |
| `collect_kis_supply_history.py` | `scripts/collect/` | 배치 수집 |
| `collect_krx_history.py` | `scripts/collect/` | 배치 수집 |
| `collect_krx_investors.py` | `scripts/collect/` | 배치 수집 |
| `collect_naver_fundamentals.py` | `scripts/collect/` | 배치 수집 |
| `collect_naver_investor.py` | `scripts/collect/` | 배치 수집 |
| `collect_naver_ohlcv_today.py` | `scripts/collect/` | 배치 수집 |
| `collect_overseas_history.py` | `scripts/collect/` | 배치 수집 |
| `collect_short_5years.py` | `scripts/collect/` | 배치 수집 |
| `collect_stooq.py` | `scripts/collect/` | 배치 수집 |
| `collect_today.py` | `scripts/collect/` | 배치 수집 |
| `dart_backfill_2024_2025.py` | `scripts/backfill/` | 소급 수집 |
| `dart_backfill_5years.py` | `scripts/backfill/` | 소급 수집 |
| `patch_data.py` | `scripts/migrate/` (완료 후 삭제) | 일회성 패치 |
| `patch_data_v2.py` | 삭제 후보 | 완료된 패치 |
| `patch_data_v3.py` | 삭제 후보 | 완료된 패치 |
| `fix_financial_cfs_ofs.py` | 삭제 후보 | 완료된 수정 |
| `fix_financial_gaps.py` | 삭제 후보 | 완료된 수정 |
| `fix_q4_balance_sheet.py` | 삭제 후보 | 완료된 수정 |
| `migrate_*.py` | `scripts/migrate/` | DB 마이그레이션 |
| `init_db.py` | `scripts/migrate/` | DB 초기화 |
| `migrate_db.py` | `scripts/migrate/` | DB 마이그레이션 |
| `debug_naver.py` | 삭제 후보 | 디버그 임시 파일 |
| `find_naver_api.py` | 삭제 후보 | 디버그 임시 파일 |
| `test_interp.py` | 삭제 후보 | 테스트 임시 파일 |
| `test_krx_api.py` | 삭제 후보 | 테스트 임시 파일 |
| `jinju_apt_tracker.py` | 삭제 후보 | 프로젝트 무관 |
| `signal_logic_v1_backup.py` | `archives/` | 구 버전 백업 |

### 유지 파일 (루트)

```
main.py               # FastAPI 앱
run_server.py         # 서버 실행 엔트리포인트
scheduler.py          # 통합 스케줄러
config.py             # 환경변수
database.py           # DB 연결
models.py             # ORM 모델
schemas.py            # Pydantic 스키마
processor.py          # 재무 처리
signal_engine.py      # 시그널 계산
peak_monitor.py       # 가상매매 모니터
kis_client.py         # KIS API 클라이언트
notifier.py           # 텔레그램 알림
trading_calendar.py   # 거래일 유틸
db_utils.py           # DB 유틸
data_collector.py     # 수집 실행기
```

---

## import 영향 분석

파일 이동 전 확인 필요한 import:

```bash
# 이동 대상 파일의 import 현황 점검
grep -r "from collect_dart" /Applications/stock_dashboard --include="*.py" | grep -v venv
grep -r "import patch_data" /Applications/stock_dashboard --include="*.py" | grep -v venv
grep -r "import fix_financial" /Applications/stock_dashboard --include="*.py" | grep -v venv
```

**주의**: `scheduler.py`에서 `subprocess.run([sys.executable, "scripts/..."])` 패턴으로 실행되는 파일은 경로 변경 시 scheduler.py도 수정 필요.

---

## 엔트리포인트 표준화

### 현황 (중복/혼재)

| 파일 | 역할 | 상태 |
|------|------|------|
| `start_project.command` | GUI 더블클릭 실행 | 활성 |
| `start.sh` | 쉘 실행 | 중복 가능성 |
| `stop.sh` | 종료 | 활성 |
| `run_server.py` | API 서버만 | 활성 |
| `fix_db_and_restart.sh` | DB 수정+재시작 | 일회성 여부 확인 |
| `fix_ports.sh` | 포트 정리 | 유틸 |
| `cron_3am.sh` | cron 실행 | launchd 대체됐는지 확인 |
| `run_fnguide_resume.sh` | FnGuide 재시작 | 임시 여부 확인 |
| `tonight_backfill.sh` | 야간 백필 | 임시 여부 확인 |
| `run_full_backfill.sh` | 전체 백필 | 임시 여부 확인 |
| `backfill_5years.sh` | 5년 백필 | 임시 여부 확인 |

### 권장 표준

```
start_project.command  — Mac GUI 실행용 (현재 사용)
run_server.py          — API 서버 단독 실행
scripts/ops/start.sh   — 서버 실행 (headless)
scripts/ops/stop.sh    — 서버 종료
```

---

## 실행 우선순위

1. **즉시 삭제 가능** (git에 history 있음): debug_*.py, test_*.py, jinju_apt_tracker.py
2. **승인 후 archives/ 이동**: patch_data*.py, fix_*.py, migrate_*.py (완료 여부 확인 후)
3. **장기 과제**: collect_*.py → scripts/collect/ 이동 (scheduler.py 동기 수정 필요)
