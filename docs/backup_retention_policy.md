# 백업 보존 정책 — 2026-05-16

## PostgreSQL 운영 백업 추가 — 2026-08-10

- 운영 백업 위치: `/Volumes/Realtek_NVME/stock_dashboard/postgres_backups/`
- 생성 명령: `venv/bin/python scripts/postgres_disaster_recovery.py backup`
- 각 `.dump` 옆의 `.manifest.json`을 반드시 함께 보존합니다.
- 매니페스트에는 SHA-256, 파일 크기, 덤프 카탈로그 수, 핵심 테이블 행 수가 기록됩니다.
- 최신 백업은 월 1회 실제 `restore-test`를 실행해 복원 가능성을 검증합니다.
- 일일 백업 7개, 주간 백업 8개, 월간 백업 12개를 기본 보존합니다.
- 삭제 전 최신 성공 백업 2개와 최신 성공 복원시험 대상 백업은 보호합니다.
- 운영 DB에 직접 덮어쓰지 않습니다. `stock_dashboard_recovered_*` 새 DB로 복원 후 접속 URL을 전환합니다.
- 상세 절차: `docs/postgres_disaster_recovery.md`

## 원칙

1. **DB backup 테이블**: 30일 보존 후 CSV export → DROP
2. **파일 .bak/.save**: 생성 후 7일 이내 확인, 불필요하면 즉시 삭제
3. **외부 DB 백업 파일** (*.db): 14일 보존 후 아카이브 이동, 90일 후 삭제
4. **로그 파일**: 30일 보존 (`logs/*.log`)
5. **아카이브**: `archives/YYYY-MM-DD/` 형식, 6개월 보존

## DB Backup 테이블 명명 규칙

```
{table_name}_backup_{YYYYMMDD}
```

보존 기준: 오늘 기준 30일 이내만 유지. 초과분은 CSV export 후 DROP.

## 즉시 보존 금지 대상

- `stock.db` 원본 삭제 — 절대 금지
- `financial_data_backup_*20260516` — 오늘 작업 백업, 최소 30일 보존
- `financial_data_backup_comprehensive_sync_20260516` — 최소 30일 보존

## 현재 backup 테이블 보존 결정

| 테이블 | 결정 | 사유 |
|--------|------|------|
| `report_files_backup_..._20260430` | CSV 후 DROP | 46일 경과 |
| `financial_data_backup_20260412` | CSV 후 DROP | 34일 경과 |
| `financial_data_dedup_backup_20260510` | CSV 후 DROP | 6일, 중복제거 완료 |
| `cash_flow_data_backup_20260510` | CSV 후 DROP | 6일, 작업 완료 |
| `cash_flow_data_backup_20260511` | CSV 후 DROP | 5일, 작업 완료 |
| `financial_data_backup_*_20260515` (5개) | **30일 보존** | 최근 작업 |
| `financial_data_backup_*_20260516` (2개) | **30일 보존** | 오늘 작업 |

## 외부 DB 파일 정책

| 파일 | 결정 | 사유 |
|------|------|------|
| `stock_corrupted_backup_20260506.db` (1.6G) | archives/ 이동 후 90일 보존 | 손상 DB, 운영 불필요 |
| `backtest.db` | 유지 | 활성 사용 |
| `stock_data.db` (0B) | 삭제 후보 | 빈 파일 |

## 자동화 스크립트

```bash
# 매월 1일 cron에서 실행
scripts/ops/monthly_backup_cleanup.sh
```

내용:
1. 30일+ backup 테이블 목록 추출
2. CSV export
3. DROP 실행
4. 결과 로그 `logs/backup_cleanup_YYYYMM.log`
