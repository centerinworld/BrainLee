# DB 최적화 계획 — 2026-05-16

## 현황 분석

### 테이블 용량 (상위 10)

| 테이블 | 추정 크기 | 비고 |
|--------|-----------|------|
| price_history | 505 MB | 핵심 시계열 |
| short_sector_daily | 271 MB | 공매도 섹터 |
| short_rank_daily | 238 MB | 공매도 순위 |
| futures_contract_daily | 199 MB | 선물 계약 |
| short_sell_daily | 194 MB | 공매도 일별 |
| idx_ph_code_date_close | 188 MB | price_history 인덱스 |
| uq_price_history_code_date | 147 MB | price_history 인덱스 |

### DB Backup 테이블 목록 (12개)

| 테이블명 | 생성일 | 크기 추정 | 보존 여부 |
|---------|-------|---------|----------|
| report_files_backup_before_cleanup_20260430 | 4월 30일 | 소 | 삭제 후보 |
| financial_data_backup_20260412 | 4월 12일 | 중 | 삭제 후보 |
| financial_data_dedup_backup_20260510 | 5월 10일 | 중 | 삭제 후보 |
| cash_flow_data_backup_20260510 | 5월 10일 | 중 | 삭제 후보 |
| cash_flow_data_backup_20260511 | 5월 11일 | 중 | 삭제 후보 |
| financial_data_backup_annual_q4_dup_20260515 | 5월 15일 | 중 | 승인 후 삭제 |
| financial_data_backup_cfs_dup_20260515 | 5월 15일 | 중 | 승인 후 삭제 |
| financial_data_backup_ofs_20260515 | 5월 15일 | 중 | 승인 후 삭제 |
| financial_data_backup_unit_error_20260515 | 5월 15일 | 중 | 승인 후 삭제 |
| cash_flow_data_backup_ofs_20260515 | 5월 15일 | 중 | 승인 후 삭제 |
| financial_data_backup_comprehensive_sync_20260516 | 오늘 | 중 | **보존** (최신) |
| financial_data_backup_fnguide_sync_20260516 | 오늘 | 중 | **보존** (최신) |

---

## 1단계: Backup 테이블 정리 (승인 필요)

### 절차

```bash
# 1. export (CSV)
mkdir -p /Applications/stock_dashboard/archives/2026-05-16/db_backups
cd /Applications/stock_dashboard

for tbl in \
  report_files_backup_before_cleanup_20260430_234638 \
  financial_data_backup_20260412 \
  financial_data_dedup_backup_20260510 \
  cash_flow_data_backup_20260510 \
  cash_flow_data_backup_20260511; do
  sqlite3 stock.db ".mode csv" ".headers on" \
    ".output archives/2026-05-16/db_backups/${tbl}.csv" \
    "SELECT * FROM ${tbl};"
  echo "Exported: $tbl"
done

# 2. 검증 후 DROP (사용자 승인 필요)
# sqlite3 stock.db "DROP TABLE IF EXISTS financial_data_backup_20260412;"
```

### 예상 효과
- backup 테이블 8개 DROP → VACUUM 후 수백 MB 회수 예상
- WAL 파일(17 MB) 체크포인트 후 축소

---

## 2단계: WAL 체크포인트 + ANALYZE

```bash
# WAL 강제 체크포인트 (서비스 종료 후 실행)
sqlite3 stock.db "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 stock.db "ANALYZE;"
```

**주의**: WAL 체크포인트는 서버 종료 상태에서 실행.

---

## 3단계: VACUUM (승인 필요, 장시간 소요)

```bash
# stock.db 3GB → backup 테이블 삭제 후 VACUUM
# 예상 소요 시간: 5~15분 (3GB 기준)
# 실행 전 stock.db 복사본 확보 권장
sqlite3 stock.db "VACUUM;"
```

---

## 4단계: 인덱스 최적화

현재 `price_history`에 인덱스 3개가 거의 동일 크기(147~188 MB):
- `idx_ph_code_date_close`
- `uq_price_history_code_date`
- `idx_price_history_code_date`

점검 쿼리:
```sql
SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='price_history';
```
중복 인덱스 존재 시 하나 삭제로 ~150 MB 절약 가능.

---

## 5단계: Hot/Cold 분리 전략 (장기)

| 테이블 | Hot 범위 | Cold 대상 |
|--------|---------|-----------|
| price_history | 최근 3년 | 3년 이전 → archive |
| short_sell_daily | 최근 1년 | 1년 이전 → archive |
| short_sector_daily | 최근 1년 | 1년 이전 → archive |

**구현 시**: SQLite Attach 또는 별도 `stock_archive.db` 활용.

---

## RS/52주 API 성능 — 이미 완료된 항목

| 항목 | 완료일 | 효과 |
|------|-------|------|
| Double-check locking | 2026-05-16 | 동시 요청 직렬화 제거 |
| `/dashboard-rows` 서버 페이지네이션 | 2026-05-16 | 2.3MB → 수십KB |
| 52주 탭 지연 로딩 | 2026-05-16 | 초기 로딩 50% 감소 |
| 18:30 precompute 스케줄러 | 2026-05-16 | 콜드스타트 7초 제거 |

---

## 정기 운영 정책

| 주기 | 작업 |
|------|------|
| 매일 18:30 | RS 캐시 사전계산 |
| 매주 일요일 | `PRAGMA optimize;` 실행 |
| 매월 1일 | backup 테이블 30일+ 것 export 후 DROP |
| 분기 1회 | VACUUM + 인덱스 점검 |
