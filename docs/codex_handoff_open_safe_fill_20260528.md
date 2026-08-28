# Codex Handoff — OPEN 안전보강 배치 (2026-05-28)

## 1) 목적
- `QUARTERLY_4WAY OPEN` 감소를 위해 FnGuide 보강을 적용하되,
- DART 앵커 괴리율을 넘는 값은 즉시 롤백(NULL)하도록 안전 필터를 추가.
- 반영/롤백 이력을 `financial_fix_log`에 남기도록 구현.

## 2) 신규 스크립트
- `/Applications/stock_dashboard/scratch/safe_fnguide_open_fill.py`

### 핵심 로직
1. `fin_quarterly_validation_flags`에서 `check_type='QUARTERLY_4WAY' AND status='OPEN' AND dart_value IS NOT NULL` 대상 종목 추출
2. 대상 분기(연도,분기) 집합 생성
3. `check_financial_integrity.fnguide_fill_financial(codes, quarters)` 실행
4. 스냅샷 대비 신규 입력값에 대해 DART 괴리율 필터 적용
   - P&L(`revenue/op/net_income`) 허용: `<=15%`
   - BS(`total_assets/total_equity`) 허용: `<=30%`
   - 초과 시 `financial_data` 필드 즉시 `NULL` 롤백
5. `financial_fix_log` 기록
   - `SAFE_ACCEPT_DART_MATCH_*`
   - `SAFE_REVERT_DART_DEVIATION_*`
6. `resolve_open_dart_one_external_match` 실행으로 OPEN 승격 재시도

## 3) 실행 이력
### 3-1) 소배치(40종목)
```bash
python3 scratch/safe_fnguide_open_fill.py --limit-codes 40
```
- FnGuide 업데이트: `30 row`
- 안전필터: `accepted=0, reverted=0`
- OPEN 승격: `0`

### 3-2) 확대배치(120종목)
```bash
python3 scratch/safe_fnguide_open_fill.py --limit-codes 120
```
- FnGuide 업데이트: `66 row`
- 안전필터: `accepted=0, reverted=0`
- OPEN 승격: `0`

## 4) 파이프라인 재실행 결과(기준 수치)
```bash
bash scratch/run_daily_validation.sh
```
최종 요약:
- `QUARTERLY_4WAY` 총 419,725건, `OPEN=45,049`, `CLOSE_MATCH=178,609`, `SELF_CONSISTENT=54,831`, `AMBIGUOUS=0`
- `ANNUAL_CONSISTENCY` OK 73.7%, AMBIGUOUS 0
- `DART_NAVER_CROSS` OK 79.7%, AMBIGUOUS 0
- `DART_FG_CROSS` OK 40.1%, AMBIGUOUS 0
- `OFS_ANNUAL_CONSISTENCY` OK 47.9%, AMBIGUOUS 0

BS 교차 검증:
- total_assets OK 65.3%
- total_equity OK 64.9%

## 5) 해석
- 이번 배치에서 FnGuide가 채운 값은 존재했지만, `OPEN -> CLOSE_MATCH` 승격 조건에 직접 걸린 케이스는 0건.
- 즉, 현재 OPEN 잔량의 주원인은 "외부값 부재/구조 차이"이며, 단순 fill만으로 즉시 승격되지 않는 구간이 큼.

## 6) Claude 재검증 요청 포인트
1. `safe_fnguide_open_fill.py`의 안전필터 임계치(15%/30%)가 너무 보수적인지 점검
2. OPEN 잔여 45,049건을 `source_count`/`field`/`year` 별로 분해해 우선순위 큐 생성
3. `OPEN` 중 `dart_value 존재 + external NULL` 비중과, 외부 소스 확장 가능성(특히 BS)을 재산정
4. `financial_fix_log`를 이용해 "실제 반영/롤백" 이력 대시보드화

## 7) 참고 파일
- `/Applications/stock_dashboard/scratch/safe_fnguide_open_fill.py`
- `/Applications/stock_dashboard/scratch/resolve_open_classifications.py`
- `/Applications/stock_dashboard/check_financial_integrity.py`
- `/tmp/daily_validate_20260528_230612.log`
