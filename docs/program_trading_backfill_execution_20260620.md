# 프로그램 매매 데이터 수집 실행 기록 — 2026-06-20

## 목적

텐버거/텐배거 프로젝트의 수급 신호 보강을 위해 프로그램 매매 데이터를 시장 단위와 종목 단위로 수집한다.

## 적용 변경

- `scripts/collect_broker_program_trading.py`
  - `--start`, `--end` 범위 수집 추가
  - `--all-stocks`, `--limit-stocks` 추가
  - `--skip-existing` 이어받기 추가
  - `--save-all-returned` 추가: 종목 API가 반환하는 여러 일자 행을 모두 저장
  - `broker_program_market_daily` 테이블 추가: KIS/Kiwoom 시장 단위 원본을 소스별로 보존
  - 장기 종목 수집 중 1,000행마다 커밋/진행 로그 출력

## 실행 중인 백필

### 시장 단위 2020년 이후

- launchd label: `com.stock-dashboard.programtrading2020backfill`
- 범위: `2020-01-01` ~ `2026-06-20`
- 소스: KIS + Kiwoom
- 저장:
  - `broker_program_market_daily`: 소스별 원본
  - `program_trading_daily`: 기존 대시보드 호환 대표 테이블
- 로그:
  - `run/program_trading_backfill_20260620/market_2020_20260620.launchd.log`
  - `run/program_trading_backfill_20260620/market_2020_20260620.launchd.err`

### 종목 단위 최근 반환 구간

- launchd label: `com.stock-dashboard.programtradingstocks`
- 기준일: `2026-06-19`
- 소스: Kiwoom
- 대상: `stock_universe` 전체 상장 주권
- 저장:
  - `broker_program_stock_daily`
- 로그:
  - `run/program_trading_backfill_20260620/stocks_latest_kiwoom.launchd.log`
  - `run/program_trading_backfill_20260620/stocks_latest_kiwoom.launchd.err`

## 현재 확인사항

- KIS 시장 단위는 최신일은 수집되지만 2020년 과거일은 응답 0건으로 확인됨.
- Kiwoom 시장 단위는 2020년 과거일도 정상 저장 중.
- Kiwoom 종목 단위는 1회 요청에 여러 일자 행을 반환하며, 삼성전자 샘플에서 20행 저장 확인.

## 상태 확인 명령

```bash
launchctl print gui/$(id -u)/com.stock-dashboard.programtrading2020backfill
launchctl print gui/$(id -u)/com.stock-dashboard.programtradingstocks
tail -f run/program_trading_backfill_20260620/market_2020_20260620.launchd.log
tail -f run/program_trading_backfill_20260620/stocks_latest_kiwoom.launchd.log
sqlite3 stock.db "SELECT source, COUNT(*), MIN(dt), MAX(dt) FROM broker_program_market_daily GROUP BY source;"
sqlite3 stock.db "SELECT source, COUNT(*), COUNT(DISTINCT stock_code), MIN(dt), MAX(dt) FROM broker_program_stock_daily GROUP BY source;"
```
