# 2026-09-06 composite 백테스트 엔진 튜닝 세션 — 상세 기록

CLAUDE.md 섹션 11 항목("composite 백테스트 엔진 대규모 파라미터/신호 탐색")의 상세 근거.
7구간 walk-forward 기준: bull_covid(20.03~21.11)/bear_ratehike(21.12~22.10)/recov23(22.11~23.10)/
bear2(23.11~24.12)/mixed2425(24.06~25.05)/bull_recent(25.06~26.03)/crash_jul26(26.07).
baseline(현재 기본값) 평균 수익률 = **21.88%**.

## 채택된 변경 (기본값 변경)

- **`use_material_backlog_bonus: bool = True`** (기존 False) — `dart_cost_quarterly`(매입재료비 3중검증,
  YoY 25~100%+매출증가+재고감소 확인) + `order_backlog`/`dart_backlog_quarterly`(수주잔고 QoQ 30%+ 급증)
  이벤트를 `_score_stock()` 보너스로 반영. 에이팩트(200470) 실사례(2023Q4 매입재료비 YoY+146%→2025년
  주가+234%) 계기로 발견한 `dart_tenbagger_triggers_quarterly`(86,247건, 그동안 어떤 스코어링 코드도
  안 읽던 테이블)를 전체 종목 10,052건으로 walk-forward 재검증 — 채택 후 avg 7.78%→21.88%(거의 3배,
  6/7구간 개선). 이번 세션 전체를 통틀어 유일하게 net 채택된 변경.
- **tenbagger_engine.py 라이브 추천에 가치함정(value trap) 가드레일 신규**: 사용자 제보(미원화학이
  절대 오를 리 없는데 텐버거 후보로 추천됨) 조사 결과, 흑자+저PBR(<1.2) 24,218건 walk-forward에서
  "60일 평균거래대금<3억 AND 대주주+특수관계인 지분>50%" 조합(n=38)이 6개월 forward return
  평균+0.5%/정체·하락 68.4%로 나머지 전체(+22.3%/50.4%) 대비 확연히 나쁨을 확인, 지분 75%+ 단독으로도
  나쁨(평균-1.2%). `_fetch_ownership_concentration()`(dart_insider_holdings, sp_stock_lmp_cnt 절대
  보유주식수 기준 — sp_stock_lmp_irds_rate는 누적치가 아닌 건별 변동률이라 쓰면 안 됨, 이번에 발견한
  버그) 신규 + `_passes_tenbagger_guardrails()`에 배선, 미원화학 실측 필터링 확인. composite.py에도
  `value_trap_gate`(opt-in)로 동일 로직 이식했으나 이 표본에서는 거의 미발동(아래 기각 항목 참조).

## 기각된 변경 (전부 opt-in 유지, 기본값 불변)

파라미터/오버레이 계열(7구간 walk-forward, 전부 baseline 21.88% 대비 악화 또는 무변화):

| 레버 | 평균 결과 | 판정 |
|---|---|---|
| `use_market_filter`(KOSPI MA120 매수게이트) | 4.84% | 대폭 악화 |
| `use_market_filter`+`fast_crash_gate` | 7.63% | 대폭 악화 |
| `vol_scale_gate`(변동성 급등 시 매수축소) | 19.27% | 악화 |
| `dynamic_score_gate`(변동성 급등 시 문턱상향) | 12.10% | 대폭 악화 |
| `dynamic_stop_gate`(변동성 급등 시 손절폭 확대) | 21.90% | 무변화(거의 미발동) |
| `value_trap_gate`(가치함정 배제) | 21.94% | 무변화(이 표본에서 거의 미발동) |
| `trail_stop_gate`(추적손절, -10/-12/-15/-18%) | 17.55~14.65%(최선 -15%) | 전부 악화 — material_backlog로 골라진 종목은 실적반영 시차로 중간에 눌렸다 재상승하는 흐름이 많아 추적손절이 조기청산시킴 |
| `component_weights`(5요소 재배점, ablation 기반) | 3.98% | 대폭 악화 |
| `event_hold_grace_gate`(이벤트종목만 MA60붕괴 청산 유예 10~30일, 신규 구현) | 17.55~11.58% | 전부 악화 — 청산유예가 자본 회전율을 낮춰 신규 후보 진입 기회비용 발생 |
| `score_threshold`(50/55/65/70, 기본 60) | 11.4~12.6% | 전부 악화 — 60이 국지최적 |
| `max_positions`(5/7/15/20, 기본 10) | 6.5~13.0% | 전부 악화, 늘릴수록 더 나빠짐 |

독립 이벤트신호(5요소 스코어링을 전부 0으로 죽이고 신호 하나만으로 매수 후보를 선정 — "독립적으로만
하면 더 잘 되는 것 아니냐"는 질문에 대한 실측 답변):

| 변형 | 평균 | 비고 |
|---|---|---|
| segment 다이버전스 단독 | -2.58% | 독립검증(126일 고정보유, 리스크관리 無)에서는 최강 신호였으나 실제 매매엔진(손절/MA60붕괴)에 태우면 거래건수 폭증(150~380건, baseline 대비 2~3배)하며 붕괴 |
| contract(계약공시) 단독 | -4.95% | 상동 |
| contract 상위등급만 | -2.73% | 상동 |
| material+backlog 단독 | -3.83% | composite 결합 시엔 성공(위 채택 항목)했지만 단독으로는 실패 — 트렌드/수급 확인이 "타이밍"을 담당하고 이벤트는 "품질 필터/타이브레이커" 역할일 뿐이라는 결론 |
| segment+contract 결합 | -7.68% | 노이즈 배가 |
| 4개 이벤트 전부 결합 | -4.92% | 상동 |

composite에 실제 연결(combined) 후 기각 — 독립검증에서는 강했지만 조합에서 실패:

- **`use_contract_bonus`**: dart_contracts 계약공시 tier(3/2/1/0). 독립검증 25~100% 구간 lift는
  괜찮았으나(날짜포맷 버그 발견·수정 후 재검증, 아래 참조) composite 결합 시 avg 21.88%→17.09%(4/7구간
  악화, mixed2425 -19.2%p).
- **`use_segment_bonus`**: segment_revenue 사업부문 매출 다이버전스(20%p+). 독립검증 평균+31~34%로
  이번 세션 개별 신호 중 최강이었으나 composite 결합 시 avg 21.88%→20.27%(3/7 변화없음·3/7 악화·1/7
  개선).

독립검증(126일 forward return)만 하고 composite 연결까지 안 간 것 — 리프트 자체가 너무 약해서
(과거 성공 신호는 lift +20%p대, 아래는 +1~5%p 수준) 조합 시 살아남을 가능성이 거의 없다고 판단:

| 신호 | 소스 | 리프트 |
|---|---|---|
| PBR 자기역사 백분위(0~10%ile) | valuation_history | +4.5%p |
| 고정비 레버리지 발현(fixed_cost_ratio>40%+YoY하락) | cost_breakdown(2023~2025만 존재) | +3.1%p, 함정비율은 오히려 베이스라인보다 높음 |
| CAPEX_RAMP_SIGNAL(감가상각 급증) | dart_tenbagger_triggers_quarterly | +1.8%p |
| WARN_INVENTORY_SURGE(재고자산 급증) | dart_tenbagger_triggers_quarterly | +1.2%p |

## 버그 2건 발견·수정 (연구/코드 양쪽에 영향)

1. **`sp_stock_lmp_irds_rate` vs `sp_stock_lmp_cnt`**: dart_insider_holdings의 전자는 건별 변동률(누적
   아님), 후자가 절대 보유주식수. 첫 구현은 전자를 합산해 미원화학 지분율을 0.37%로 오계산(실제 ~44%).
2. **`dart_contracts.disclosed_at`이 `YYYYMMDD`(구분자 없음) 형식**: `str(x)[:10]` 방식 파싱이
   100% 실패(0건 반환)했었고, 독립 연구스크립트는 더 나쁘게 `-`가 숫자보다 ASCII상 작아 `bisect_left`가
   조용히 잘못된(연말 편향) 날짜로 진입점을 잡아 결과 자체가 오염(최초 "0~5% 구간 +32.9%" 통계는
   무효, 수정 후 재검증하니 +23.6%로 하향).

## 결론

이 엔진 구조(5요소 가중합 + 소수 이벤트 보너스, threshold=60, max_positions=10)는 시도 가능한
파라미터·오버레이·독립신호 축을 폭넓게 다 뒤져도 이미 국지최적에 가깝다. 추가 수익 개선은 파라미터
재조정이 아니라 완전히 새로운 데이터/아이디어에서 나올 가능성이 높다.
