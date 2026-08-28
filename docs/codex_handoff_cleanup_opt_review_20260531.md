# Codex Handoff: 불필요 파일 정리 + 최적화/오류 점검 (2026-05-31)

## 1) 현재 디스크 사용 스냅샷 (프로젝트 루트 기준)
- 전체: 약 **23G**
- 대용량 상위 디렉토리
  - `reports/` **11G**
  - `hs_trade_lab/` **1.9G**
  - `archives/` **1.9G**
  - `.git/` **1.0G**
  - `venv/` **702M**
  - `.claude/` **427M**
  - `frontend/` **250M**
  - `scratch/` **224M**
  - `logs/` **132M**
  - `docs/` **90M**

## 2) 대용량 파일 현황 (직접 확인)
- `/Applications/stock_dashboard/stock.db` 5.1G
- `/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db` 901M
- `/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db-wal` 902M
- `/Applications/stock_dashboard/archives/2026-05-16/corrupted_db/stock_corrupted_backup_20260506.db` 1.6G
- `/Applications/stock_dashboard/reports/20260201_record.mp4` 345M

## 3) 삭제 후보 분류

### A. 즉시 삭제 가능(안전)
아래는 코드 참조/런타임 의존성이 사실상 없거나 임시/중복 성격:
- 0B DB 파일
  - `/Applications/stock_dashboard/financial_data.db`
  - `/Applications/stock_dashboard/stock_dashboard.db`
  - `/Applications/stock_dashboard/frontend/stock.db`
- 임시/백업 파일
  - `/Applications/stock_dashboard/main.py.bak`
  - `/Applications/stock_dashboard/stock.db-wal.bak`
- 파이썬 캐시 전체
  - `**/__pycache__/`
  - `**/*.pyc`

예상 회수: 수십 MB ~ 100MB+

### B. 확인 후 삭제(공간 효과 큼)
- `/Applications/stock_dashboard/reports/` (11G)
  - 산출 리포트 장기보관 필요 없으면 날짜 기준 압축/삭제
- `/Applications/stock_dashboard/archives/2026-05-16/corrupted_db/stock_corrupted_backup_20260506.db` (1.6G)
  - 복구 시나리오 끝났으면 삭제 가능
- `/Applications/stock_dashboard/.claude/worktrees/` (427M)
  - 과거 세션 작업트리 정리 가능
- `/Applications/stock_dashboard/scratch/` (224M), `/Applications/stock_dashboard/logs/` (132M)
  - 7~30일 보관 정책 후 정리 권장

### C. 삭제 금지(핵심 데이터)
- `/Applications/stock_dashboard/stock.db`
- `/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db`
- `/Applications/stock_dashboard/venv/` (재설치 가능하지만 즉시 삭제 비권장)

## 4) 성능/운영 리스크 점검

### 4-1) WAL 비대화 리스크
- `hs_trade_lab.db-wal`이 **902M**으로 큼.
- 의미: 트랜잭션/체크포인트 관리가 약하면 디스크 급증 + I/O 저하 가능.
- 조치 권장:
  1. 배치 종료 시 `wal_checkpoint(TRUNCATE)` 수행
  2. 장기 writer 프로세스 분리/주기적 commit
  3. WAL 크기 모니터링 알림 추가

### 4-2) 예외 삼킴(`pass`) 과다
- 정적검색 결과 `pass`/`except ...: pass` 다수
  - `main.py` **28건**
  - `routes/extra_signals.py` 7건
  - `routes/portfolio.py` 5건
  - `routes/market_radar.py` 4건
- 영향: 데이터 누락/오류가 사용자 화면에 조용히 전파될 수 있음.
- 조치 권장:
  1. `except ...: pass` → 최소 `logger.warning/error`로 치환
  2. 핵심 수집경로는 run_id + stock_code + source를 로그 필수화
  3. 경미한 파싱 실패와 치명 오류를 에러코드로 분리

### 4-3) 빌드/문법 상태
- `py_compile` 점검 통과:
  - `main.py`, `routes/*.py`, `collectors/*.py`, `scripts/ops/*.py`
- 즉, 현재 문법 레벨 치명 오류는 없음.

## 5) 클로드 실행 권장 순서
1. **보존정책 확정**: `reports/`, `archives/`, `scratch/`, `logs/` 보관일수 결정
2. **안전 삭제 배치 1차**: 0B DB + `.bak` + `__pycache__`
3. **WAL 관리 적용**: hs_trade_lab 배치 종료 후 체크포인트 트렁케이트
4. **예외 로깅 개선 PR**: `main.py`/`routes/extra_signals.py`부터 우선
5. **자동 청소 크론 추가**: 14일 지난 로그/스크래치 자동 정리

## 6) 바로 실행 가능한 정리 명령 (참고, 수동 승인 후 실행)
```bash
cd /Applications/stock_dashboard

# 1) 즉시 삭제 가능 파일
rm -f financial_data.db stock_dashboard.db frontend/stock.db main.py.bak stock.db-wal.bak

# 2) 파이썬 캐시
find . -type d -name '__pycache__' -prune -exec rm -rf {} +
find . -type f -name '*.pyc' -delete

# 3) 오래된 로그(예: 14일 초과)
find logs -type f -mtime +14 -delete
find . -maxdepth 1 -type f -name '*.log' -mtime +14 -delete
```

## 7) 결론
- 지금 당장 가장 큰 용량 이슈는 `reports(11G)` + `archives(1.6G 단일파일)` + `hs_trade_lab.db-wal(902M)`.
- 코드 품질에서 가장 위험한 부분은 `except/pass`로 오류가 묵살되는 구간.
- 우선은 **안전 삭제 + WAL 관리 + 예외 로깅 개선** 3축으로 진행하는 것이 효과가 큼.
