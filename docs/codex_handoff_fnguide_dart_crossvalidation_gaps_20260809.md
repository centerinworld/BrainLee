# Codex 핸드오프 — FnGuide/DART 교차검증 잔여 이슈 (2026-08-09)

> **⚠️ 갱신(2026-08-09 추가5)**: 사용자 지시("너가해")로 아래 "남은 작업 1"(REIT/외국상장사
> 카테고리 처리)과 "남은 작업 2"의 total_liabilities/eps 부분은 **Claude가 이미 완료**했다.
> `stock_collection_config.entity_category` 신규(REIT 23개/foreign_issuer 21개),
> `cross_validate_annual()`에 `structural_diff` 상태 분리 추가, `dart_collector.py`에
> total_liabilities("자본과부채총계" 오매칭) + eps(우선주/보통주 오매칭, 75개사 5,543건
> 백필 — 첫 200건 중 48% 실제 변경 확인) 수정 완료. **capital_stock/bps/dps 3개 필드는
> 여전히 미검증** — 아래 "남은 작업 2"에서 이 3개만 남은 범위로 좁혀서 진행할 것.
> "남은 작업 3"(net_income 정책 영향도 분석)은 그대로 유효.

## 배경

오늘 세션(Claude)에서 `financial_data.cash` 음수값 오염(2,484종목·16,730행)을 계기로
`collectors/fnguide_financial_collector.py`(FnGuide 신규 JSON API 전면 재작성 +
`_fetch_dart_annual()` 교차검증 함수의 `from config import settings` import 버그 발견·수정)와
`collectors/dart_collector.py`(라이브 `financial_data` 테이블을 채우는 **별도의 독립** 파서)의
계정명 오매칭 버그 6종을 발견·수정했다. 상세 경위는 CLAUDE.md 섹션11의
"2026-08-09/(추가)/(추가2)/(추가3)" 항목 참조.

전종목 순차 검증 잡을 `scheduler.py`에 신규 등록(`_loop_fnguide_dart_verify_sweep`, 매일
03:15, 하루 450종목씩, FNGUIDE 일일한도 1,500건 내)했고, 발견되는 불일치는
`fnguide_dart_mismatch_log` 테이블에 자동 누적된다. 이 문서는 오늘 스팟체크에서 발견했지만
이번 세션 범위 밖으로 남긴 것들이다.

## 이미 완료된 것 (재작업 불필요)

- `dart_collector.py` revenue exclude에 `"기타"` 추가 (LG화학류 "기타영업수익" 오매칭 방지)
- `fnguide_financial_collector.py` `_fetch_dart_annual()`에 CFS→OFS 자동 폴백 추가
  (연결재무제표 미작성 단독기업 대응, status=013 91/226건→감소 예상)
- `net_income`/`operating_profit`/`total_equity`/`revenue`/`cash` 5개 필드의
  6가지 계정 오매칭 버그 (CLAUDE.md 참조)

## 남은 작업 1 — REIT/외국상장기업 카테고리 전용 처리 (우선순위: 중)

전종목 스윕에서 다음 두 카테고리가 구조적으로 다른 재무제표 양식을 쓰는 것으로 확인됨:

- **REIT(부동산투자회사)**: `SELECT stock_code, stock_name FROM stock_universe WHERE stock_name LIKE '%리츠%'`
  로 조회 시 26건이 나오나 "메리츠금융지주"/"블리츠웨이엔터테인먼트"처럼 이름에 "리츠"가
  우연히 포함된 비-REIT가 섞여 있음 — `dart_corp_type` 또는 업종 코드 기준으로 정확히
  분리 필요(실제 REIT는 약 20개 내외로 추정: 롯데리츠/신한알파리츠/한화리츠/제이알글로벌리츠 등).
  실측 사례: 마스턴프리미어리츠(357430) — revenue가 DART/FnGuide 3개년 연속 60~66%
  일관 차이(단순 1000배/100배 단위오류 아님). REIT는 K-IFRS상 투자부동산 공정가치
  평가손익을 매출에 포함/제외하는 방식이 회사·플랫폼마다 다를 수 있어, 이건 파싱버그가
  아니라 "REIT의 revenue 정의 자체가 이질적"인 구조적 문제일 가능성이 높음.
- **외국상장기업(종목코드 9로 시작, 6자리)**: `SELECT stock_code, stock_name FROM stock_universe
  WHERE stock_code GLOB '9[0-9][0-9][0-9][0-9][0-9]'` — 21건(코오롱티슈진/엑세스바이오/
  잉글우드랩/JTC 등). 실측 사례: JTC(950170) — 6개 교차검증 필드 전체(revenue/
  operating_profit/net_income/total_assets/total_equity/cash)가 3개년 연속 일관되게
  ~89%(≈9.2배) 차이. 클린한 10배/100배/1000배 단위오류가 아니라서 원인 미상 —
  가능성: ①FnGuide가 이 회사를 특수 처리(외화표시 원문을 자체 환산)하는데 DART 원문은
  현지통화 그대로일 가능성 ②DART/FnGuide 중 한쪽이 지주회사/SPC 별도 vs 실제 사업법인
  연결을 혼동했을 가능성. DART 원문(`dart.finstate_all('950170', 2024, '11011',
  fs_div='CFS')`)을 직접 열어 account_id/account_nm과 실제 사업보고서 원문(전자공시)을
  대조해 근본원인 규명 필요.

**권장 조치**: 두 카테고리를 `stock_collection_config`에 `is_reit`/`is_foreign_issuer` 같은
플래그로 등록(기존 `preferred_report_type` 오버라이드 패턴 재사용)하고, `cross_validate_annual()`
에서 이 카테고리는 기본 5% 허용오차(`_CROSS_TOL`)를 완화하거나 아예 스킵하도록 처리 —
"진짜 파싱버그"와 "설계상 다른 재무제표 양식"을 구분해 향후 검증 로그의 신호대잡음비를
높일 것.

## 남은 작업 2 — `dart_collector.py` 잔여 계정맵 필드 전종목 서브스트링 충돌 재감사 (우선순위: 중)

오늘은 revenue/operating_profit/net_income/total_equity/cash 5개 필드만 실측 스팟체크(6종목)로
검증했다. `_ACCOUNT_MAP`(collectors/dart_collector.py 55행)의 나머지 필드는 **한 번도
체계적으로 재검증되지 않았다**:

```python
(["자산총계"],  "total_assets"),        # 오늘 검증됨(삼성전자 등)
(["부채총계"],  "total_liabilities"),   # ⚠️ 미검증 — "유동부채총계"/"비유동부채총계" 같은
                                          #   소계 라벨을 쓰는 회사가 있으면 서브스트링 충돌 가능
                                          #   (삼성전자는 "유동부채"/"비유동부채"로 소계 라벨에
                                          #   "총계"가 안 붙어 문제없음을 확인했으나, 회사마다
                                          #   XBRL 렌더링 관행이 다를 수 있음 — 표본 확대 필요)
(["자본총계"],  "total_equity"),        # 오늘 검증됨
(["자본금"],   "capital_stock"),        # ⚠️ 미검증 — "우선주자본금"/"보통주자본금" 등
                                          #   세부계정과 "절댓값 최대"류 우선순위 충돌 가능성
(["기본주당순이익", "주당순이익", "기본EPS", "기본주당이익"], "eps"),  # ⚠️ 미검증 — "희석주당순이익"
                                          #   과 혼동 위험(희석 EPS가 기본 EPS보다 항상 작아야
                                          #   하는데, 키워드 "주당순이익"이 "희석주당순이익"의
                                          #   서브스트링이라 오매칭 가능)
(["주당순자산", "1주당순자산가액", "주당장부가액"], "bps"),  # ⚠️ 미검증
(["주당배당금", "주당현금배당금"], "dps"),  # ⚠️ 미검증
```

**요청**: 오늘 사용한 방법론(①`fnguide_dart_mismatch_log` 테이블 — 매일 03:15 스케줄러가
자동 누적하므로 며칠 뒤 조회하면 실제 문제 사례가 쌓여 있을 것 ②각 미검증 필드에 대해
DART `finstate_all()` 원문을 20~30개 다양한 업종(금융/지주회사/제조업/바이오 등)에서 직접
열어 `sj_nm`/`account_nm` 전체 목록을 눈으로 훑고 키워드가 의도치 않은 하위/소계 계정과
겹치는지 확인)을 총부채/자본금/EPS/BPS/DPS에도 적용해달라. 발견되는 버그는 오늘과 동일한
패턴(`_DART_EXCLUDE_MAP`류 exclude 키워드 추가, 또는 sj_nm 재무제표유형 필터 추가)으로 고칠 것 —
**"절댓값 최대" 류의 타이브레이커는 절대 새로 추가하지 말 것**(오늘 발견한 근본 결함 패턴,
총계 vs 부분치가 공존하는 구조에서 항상 신뢰 불가).

## 남은 작업 3 — `financial_data.net_income` 총계 vs 지배주주 귀속분 정책 결정 (우선순위: 높음, 의사결정 필요)

`dart_collector.py`는 net_income 키워드매칭에서 `"지배기업"` 포함 행을 **의도적으로 제외**하고
총계(비지배지분 포함) "당기순이익"을 채택(174~213행 근방, exclude 조건
`any(x in acc for x in ("귀속", "비지배", "지배기업"))`). 반면 오늘 수정한
`fnguide_financial_collector.py`는 FnGuide 관행(및 CLAUDE.md 섹션8의 PER/EPS_TTM 계산
공식)에 맞춰 **지배주주 귀속분**을 우선한다. 이 개념 차이가 오늘 전종목 스윕에서
net_income이 불일치 20건 중 상당수(누적 집계 시 압도적 1위, 대략 60~70%대)를 차지하는
근본 원인으로 추정된다(진짜 파싱버그가 섞여 있을 수도 있으나, 비지배지분 비중이 큰
대기업/지주회사군에서 특히 자주 걸릴 것으로 예상).

**요청**: `financial_data.net_income`을 실제로 소비하는 모든 지점(ROE 계산, 스크리너,
백테스트 엔진 등 — CLAUDE.md 섹션8 "PER/PBR 계산 방식" 및 여러 전략 엔진에서 참조)을
전수 조사해 "총계로 유지 시 영향" vs "지배주주분으로 전환 시 영향"을 정량적으로 비교하는
분석 리포트를 작성해달라(코드 변경은 하지 말고 분석만 — 재무 무결성 선행규칙에 따라
이 결정은 사용자 승인 필요). 조사 결과를 `docs/codex_handoff_net_income_convention_impact_*.md`
로 남기면 다음 세션에서 사용자와 함께 결정한다.

## 참고

- `fnguide_dart_mismatch_log` 테이블(오늘 신설)에서 `SELECT field, COUNT(*) FROM
  fnguide_dart_mismatch_log GROUP BY field ORDER BY 2 DESC`로 필드별 불일치 빈도를
  언제든 조회 가능 — 매일 아침 이 테이블을 먼저 확인하고 작업 우선순위를 정할 것.
- `scripts/verify_all_fnguide_dart_20260809.py`의 `run_verify_sweep(limit, conn)` 함수를
  재사용해 특정 종목군만 골라 검증할 수 있음(예: REIT 26종목만 `codes` 파라미터로 좁혀서
  실행하는 별도 스크립트 작성 가능).
