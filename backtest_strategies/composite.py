"""
composite.py -- run_backtest_composite()
Split out of backtest.py on 2026-09-03. Pure relocation, no logic changed.
"""
import json
import uuid
import math
import re
import logging
import bisect
from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from backtest_common import (
    SignalEvidenceLedger,
    _composite_financial_inputs,
    _event_date_basis,
    evidence_aggregate,
    evidence_item,
    financial_row_evidence,
    DB_PATH,
    WARMUP_DAYS,
    _corp_action_adjusted_entry,
    _load_corp_action_factors,
    _load_backlog_surge_events,
    _load_contract_win_events,
    _load_segment_divergence_events,
    _load_material_cost_events,
    _ma,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _final_liquidation_quote_for_code,
    _score_stock,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_composite(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    score_threshold: int = 60,
    run_name: str = None,
    run_id: str = None,
    use_event_bonus: bool = True,  # 2026-08-30: 6기간 walk-forward 검증 통과(avg6 +9.47%→+10.95%, 4/6기간 개선) — 기본값 채택
    data_asof_ts: str = None,
    use_market_filter: bool = False,  # 2026-09-04 신규(opt-in) — 아래 참조
    fast_crash_gate: bool = False,
    fast_crash_drop: float = -0.07,
    fast_crash_days: int = 3,
    vol_scale_gate: bool = False,  # 2026-09-04 신규(opt-in) — 아래 참조
    vol_scale_lookback: int = 5,
    vol_scale_threshold: float = 0.025,
    vol_scale_factor: float = 0.5,
    vol_scale_smooth: bool = False,
    dynamic_score_gate: bool = False,  # 2026-09-04 신규(opt-in) — 아래 참조
    dynamic_score_boost: int = 15,
    dynamic_stop_gate: bool = False,  # 2026-09-04 신규(opt-in) — 아래 참조
    dynamic_stop_widen: float = 0.05,
    component_weights: "Dict[str, float]" = None,  # 2026-09-04 신규(opt-in) — 아래 참조
    trail_stop_gate: bool = False,  # 2026-09-05 신규(opt-in, 2026-09-06 재검증 후 기각 유지) — 아래 참조
    trail_stop_pct: float = -0.12,
    trail_min_profit: float = 0.05,
    value_trap_gate: bool = False,  # 2026-09-06 신규(opt-in) — 아래 참조
    value_trap_min_tvol_억: float = 3.0,
    value_trap_max_concentration: float = 50.0,
    value_trap_extreme_concentration: float = 75.0,
    use_material_backlog_bonus: bool = True,  # 2026-09-06 기본값 채택 — 아래 참조
    use_contract_bonus: bool = False,  # 2026-09-06 신규(opt-in, walk-forward 검증 후 기각) — 아래 참조
    use_segment_bonus: bool = False,  # 2026-09-06 신규(opt-in) — 아래 참조
    event_hold_grace_gate: bool = False,  # 2026-09-06 신규(opt-in, 기각) — 아래 참조
    event_hold_grace_days: int = 15,
    conviction_sizing_gate: bool = False,  # 2026-09-09 신규(opt-in, 기각) — 아래 참조
    conviction_sizing_low: float = 0.7,
    conviction_sizing_high: float = 1.3,
) -> str:
    """
    복합 스코어링 전략 (V10 선택적 복합 시그널).

    핵심: 100점 스코어에서 threshold(기본 60점) 이상인 종목만 매수.
    60점 달성 = 최소 3가지 독립 조건 동시 충족.

    동적 익절: 점수 60~69 → +20%, 70~79 → +30%, 80+ → +40%
    손절: -10% 고정
    MA60 붕괴 시 즉시 매도

    use_event_bonus=True: _score_stock()에 특허/기술이전(+3)·자사주매입(+2)·소각(+3)·
    희석위험(-3~-6) 이벤트 보정을 추가 반영 — 2026-08-30 세션에서 신규 도입,
    walk-forward(6기간) 검증 통과 후 기본값 True로 채택. avg6 +9.47%→+10.95%,
    4/6기간 개선(20.3~21.11/22.11~23.10/23.11~24.12/21.12~22.10 개선,
    24.6~25.5/25.6~26.3 소폭 악화 — 정직하게 트레이드오프 존재).

    data_asof_ts: 2026-09-04 신규. turnaround/regime_adaptive와 동일하게
    corporate_action_events.adjustment_status(매일 00:10 확정 잡)와 financial_data
    (매일 00:05 재검증 잡)가 실행 중에도 계속 갱신되므로, 같은 과거 구간을 재실행할
    때 결과가 흔들릴 수 있는 잠재 위험이 이 전략에도 동일하게 존재한다(현금원장에
    조정 진입가가 직접 반영됨). 'YYYY-MM-DD HH:MM:SS'를 주면 그 시각 기준 데이터로
    고정해 재실행해도 항상 동일한 결과를 보장한다. None이면 실행 시작 시각으로 자동 고정해
    run spec에 저장한다.

    use_market_filter/fast_crash_gate: 2026-09-04 신규(둘 다 opt-in, 기본 False —
    기존 결과 완전 불변). 코드 감사 결과 이 함수는 KOSPI MA120 market_bullish를
    계산만 하고 실제 매수 게이트(Phase D)에는 배선된 적이 없었다 — 즉 지금까지
    composite는 하락장 필터가 전혀 없는 상태로 운용됐다(2026-07 폭락에서 KOSPI와
    거의 동행한 -20.95%의 실제 원인). use_market_filter=True로 원래 의도대로
    배선하고, fast_crash_gate=True를 더하면 MA120보다 먼저 단기급락(N거래일
    fast_crash_drop 이하)에 반응해 신규매수만 차단한다(보유종목 손절 로직·D+1
    시가체결은 불변 — gap risk 자체는 못 없앰). walk-forward 비교 검증 결과
    (2026-09-04) 상승장 기회비용이 하락장 방어보다 커 기본값 False 유지.
    2026-09-06 material_backlog_bonus 기본값과 조합 재검증(7구간): avg 21.88%→
    market_filter 4.84%, +crash 7.63% — 여전히 대폭 악화(bear_ratehike 구간은
    하락장 내내 시장필터가 신규매수 자체를 0건으로 차단해 기회비용이 그대로
    손실 방지분을 상쇄). 기본값 False 유지 재확정.

    vol_scale_gate: 2026-09-04 신규(opt-in). fast_crash_gate 대신 시도 — 진입 자체는
    막지 않고 최근 vol_scale_lookback거래일 KOSPI 일변동 표준편차가
    vol_scale_threshold 이상이면 그날 신규 진입 티켓만 vol_scale_factor배로 축소
    (_run_generic_backtest의 동일 파라미터와 같은 설계). 7월 폭락 손절 30건 중 상당수가
    D close 신호→D+1 시가체결 갭으로 -10% 손절선을 -13~-17%까지 넘겨 체결됐음을
    trades_json으로 확인 — 진입을 막진 못해도 갭 손실의 절대금액은 줄일 수 있다.
    2026-09-04 walk-forward 재검증(threshold 2.5~6%, factor 0.5~0.8, lookback
    3~5일, 연속스케일링까지 총 7개 조합): composite는 구간당 최대 193건까지
    거래하는 고빈도 전략이라 이 레버(시장 변동성 기반 티켓 축소) 자체가 안 맞음 —
    모든 조합이 게이트를 끈 기준선(5구간 평균 +8.84%)보다 나빴다. turnaround/v2는
    채택했지만 composite는 기본값 False 유지. 2026-09-06 material_backlog_bonus
    기본값과 조합 재검증(7구간): avg 21.88%→19.27%로 여전히 악화. 기본값 False 유지.

    dynamic_score_gate: 2026-09-04 신규(opt-in, vol_scale과 별개 실험). 시장 변동성
    급등 시 포지션 사이즈를 줄이는 대신, 매수 문턱(score_threshold)을
    dynamic_score_boost만큼 높여 그날은 더 확신도 높은(고득점) 종목만 통과시킨다
    — "다 사되 작게 사기" 대신 "더 엄선해서 정상 크기로 사기" 접근. walk-forward
    검증(boost 10/15/20) 결과 전부 기준선(+8.84%)보다 나쁘고 폭락 방어도 vol_scale
    보다 약함(진짜 폭락은 고득점 종목도 같이 무너짐) — 기각, 기본값 False 유지.
    2026-09-06 material_backlog_bonus 기본값과 조합 재검증(7구간): avg 21.88%→
    12.10%로 여전히 큰 폭 악화. 기본값 False 유지 재확정.

    dynamic_stop_gate: 2026-09-04 신규(opt-in). 7월 폭락 trades_json 분석 결과
    최악의 손절 12건이 전부 -10% 문턱이 아니라 -12~-16.7%로 체결됐고, 그중 다수가
    매수 다음날(2026-07-02→07-03) 단 하루 만에 손절된 것으로 확인 — 갭하락이 -10%
    손절선을 이미 훌쩍 넘긴 채 D+1 시가로 체결된 것. 손절선을 좁히는 건 의미가
    없으므로(이미 갭으로 다 뚫림) 반대로 변동성 급등 감지 시에만 손절선을
    dynamic_stop_widen만큼 넓혀(-10%→-15%) 얕은 갭(-10~-14%대)에서는 강제청산을
    피하고 이후 반등(7/31 KOSPI +17.9%) 참여 기회를 준다 — 깊은 갭(-15%+)은 여전히
    손절되므로 무제한 방치는 아니다. 2026-09-06 material_backlog_bonus 기본값과
    조합 재검증(7구간): avg 21.88%→21.90%로 사실상 무변화(발동 조건인 변동성
    급등 자체가 이번 7구간에서 거의 안 걸림) — 개선도 악화도 아님, 기본값 False
    유지(굳이 켤 이유 없음).

    component_weights: 2026-09-04 신규(opt-in, 기본 None=기존 35/25/15/20/5 배점
    100% 동일). 위 4개 시장타이밍 오버레이가 전부 실패한 뒤 사용자 지적("기존
    배점이 맞다는 근거가 없다")에 따라 _score_stock()의 배점 자체를 재검증하기
    위해 도입. 키: turnaround/trend/volume/supply/value(각 기본 1.0). 0으로 주면
    해당 컴포넌트를 완전히 꺼서(ablation) 그 컴포넌트가 실제로 수익에 기여하는지
    walk-forward로 검증할 수 있다. 2026-09-05 재검증: ablation으로 추세/거래량을
    "무의미"로 판단해 통째로 재배점(흑자전환↑ 가치↑ 추세↓ 거래량↓)한 버전을
    walk-forward 7구간에서 재확인한 결과 오히려 전부 악화(+7.78%→+3.98%) —
    개별 요인 제거 효과가 조합에서는 그대로 합산되지 않음(추세+거래량이 실은
    2차 확인필터 역할을 하고 있었던 것으로 추정). 기본값 None 유지, 재배점 기각.

    trail_stop_gate: 2026-09-05 신규(opt-in). 시장타이밍/배점 조정이 전부 실패한
    뒤 코드 감사 결과 발견 — composite는 익절(20/30/40%)·손절(-10%)·MA60붕괴 3가지
    청산 조건만 있고 추적손절(trailing stop)이 아예 없다(다른 전략 v2/turnaround/
    regime_adaptive는 전부 갖추고 있음). 즉 포지션이 +15%까지 올랐다가 -9%까지
    되돌아와도 익절선(20%)에 못 미쳐 한 번도 청산 안 되고 그대로 손실권까지
    끌려간다. trail_min_profit(기본 +5%) 이상 오른 뒤 고점 대비 trail_stop_pct
    (기본 -12%) 하락 시 조기 청산 — 시장 상황과 무관하게 개별 포지션의 미실현
    이익을 보호하는, 지금까지 시도한 것과 다른 종류의 레버.

    2026-09-06 재검증: 당시엔 use_material_backlog_bonus가 기본값이 아니었어서
    material_backlog 채택 후 조합 테스트를 안 해봤음. 실제로 같이 켜보니(7구간
    walk-forward, trail_stop_pct -10%/-12%/-15%/-18% 스윕) 평균 +21.88%(기존
    기본값) 대비 전부 악화: -10%→-1.34%, -12%→+12.80%, -15%→+14.65%(최선이지만
    여전히 -7.2%p), -18%→+14.14%. 특히 bear_ratehike 구간에서 -17%→-36%까지
    크게 나빠짐 — material_backlog로 골라진 종목들은 매입재료비/수주잔고 증가
    후 실적에 반영되기까지 시차가 있어 중간에 눌렸다 다시 오르는 흐름이 많은데,
    추적손절이 이 되돌림에서 조기 청산시켜 손실 확정 후 재진입을 반복시키는
    것으로 추정. 기본값 False 유지 확정 — material_backlog_bonus와는 상충.

    value_trap_gate: 2026-09-06 신규(opt-in). 사용자 제보(미원화학 사례) 계기로
    진행한 흑자+저PBR(<1.2) 24,218건 walk-forward 연구 결과, "60일 평균거래대금
    <value_trap_min_tvol_억(기본 3억) AND 대주주+특수관계인 지분
    >value_trap_max_concentration(기본 50%)" 조합(n=38)은 6개월 forward return
    평균 +0.5%/정체·하락(≤5%) 68.4%로 나머지 전체(+22.3%/50.4%) 대비 확연히
    나빴다. 지분 value_trap_extreme_concentration(기본 75%) 이상은 조합과 무관
    하게 단독으로도 나쁨(평균 -1.2%/중앙값 -14.6%). 신규 매수 후보에서만 제외
    (보유 포지션 청산 로직 불변). dart_insider_holdings 커버리지가 전체 종목의
    ~15%뿐이라 백테스트 표본에서는 자주 발동하지 않는다 — value.py 검증 시
    7구간 중 5구간 완전동일(가드레일 미발동), 1구간 개선(+2.4%p), 1구간 소폭
    악화(-0.2%p)였다. 텐버거 라이브 추천(tenbagger_engine.py)에서는 동일 로직
    으로 미원화학을 정확히 걸러내는 것을 실측 확인. 2026-09-06 composite
    material_backlog_bonus 기본값과 조합 재검증(7구간): avg 21.88%→21.94%로
    거의 무변화 — value.py 때와 동일하게 가드레일이 이번 표본에서도 거의
    발동 안 함. 라이브 추천 가드레일로서의 가치는 유효하나 이 백테스트 표본
    에서는 검증력이 낮음 — 기본값 False 유지.

    event_hold_grace_gate: 2026-09-06 신규(opt-in). trail_stop_gate 재검증에서
    나온 가설을 직접 테스트 — material_backlog로 골라진 종목은 매입재료비/
    수주잔고 급증이 실적에 반영되기까지 시차가 있어 중간에 한 번 눌렸다가
    다시 오르는 흐름이 많다고 추정했는데, 시장 전체에 적용하는 오버레이(추적
    손절 등)는 전부 실패했다. 그래서 "전체 포지션의 청산 규칙을 바꾸는" 대신
    "이 이벤트로 진입한 포지션만" MA60붕괴 청산 발동 조건(현재 hold_days>5)을
    event_hold_grace_days(기본 15)만큼 늦춰(hold_days>20) 일시적 눌림에서
    강제청산되지 않고 버틸 시간을 더 준다. 진입 시점에 material_map/backlog_map
    이벤트가 활성 상태였던 포지션만 해당(event_flagged), 나머지는 기존과 동일.

    7구간 walk-forward 검증(grace 10/15/20/30일) 결과 전부 기각: avg 21.88%→
    10일 17.55%, 15일 14.99%, 20일 11.58%, 30일 15.65% — grace가 커질수록
    대체로 더 나빠지는 경향(bear2 구간은 20일에서 -10.56%→-38.47%까지 급격히
    악화). MA60붕괴 청산을 늦추면 개별 포지션이 -10% 손절선에 아직 안 걸린
    상태로 계속 자본을 묶어둬서, max_positions 한도 안에서 더 좋은 신규 후보로
    갈아탈 기회 자체가 줄어드는 것으로 추정(포지션 회전율 저하의 기회비용이
    "눌림목 버티기"로 얻는 이득보다 큼). 가설은 그럴듯했으나 실측은 반대 —
    기본값 False 유지, 재현연구용 opt-in만 보존.

    use_material_backlog_bonus: 2026-09-06 기본값 True로 채택(과거 기본은 False,
    use_event_bonus와 별개 플래그). 에이팩트(200470) 실사례(2023Q4 매입재료비
    YoY+146%→2025년 주가 +234%) 계기로 발견한 dart_tenbagger_triggers_quarterly
    (86,247건, 지금까지 어떤 코드도 안 읽던 테이블)를 전체 종목 10,052건으로
    walk-forward 재검증 — 매입재료비 급증(WATCH_COST_INFLATION류)과 수주잔고
    QoQ 급증(BACKLOG_SURGE)이 전체 무작위 베이스라인(평균+8.8%/승률9.3%) 대비
    각각 평균+17.6%/승률13.8%·14.1%로 확인. tenbagger_engine.py에는 이미 유사
    로직(연간 기준, Codex 2026-06-21 검증)이 있었지만 composite에는 전혀 없었음
    — _load_material_cost_events()/_load_backlog_surge_events()로 분기 단위
    point-in-time 버전을 이식. 7구간 walk-forward 검증(2026-09-06) 결과 평균
    +7.78%→+21.88%(거의 3배), 6/7구간 개선(거래건수는 거의 그대로— 질적 개선)
    — 이번 세션 전체에서 가장 강한 개선이라 기본값 채택. False로 넘기면 기존
    동작으로 되돌릴 수 있다.

    use_contract_bonus: 2026-09-06 신규(opt-in, 기각). dart_contracts(단일공급계약
    공시) 전체 종목 독립검증에서는 계약규모/매출 비율이 작을수록 좋아 보였으나
    (초기 검증에 disclosed_at 날짜형식(YYYYMMDD vs YYYY-MM-DD) 버그가 있어 수정
    후 재검증), composite에 실제로 연결해 7구간 walk-forward로 돌려보니 평균
    +21.88%→+17.09%로 오히려 악화(4/7구간 나빠짐, 특히 mixed2425 -19.2%p) —
    독립 신호가 다른 5개 스코어링 요소와 결합되면서 노이즈로 작용한 것으로 추정.
    기본값 False 유지, 재현연구용 opt-in만 보존.

    use_segment_bonus: 2026-09-06 신규(opt-in, 기각). segment_revenue(사업부문별
    매출) 다이버전스 — 특정 사업부 매출이 전사 매출보다 20%p+ 빠르게 성장 중이면
    "아직 전체 실적에 다 안 드러난 숨은 성장엔진" 신호. 전체 종목 10,470건 독립
    walk-forward 검증에서는 다이버전스 20%p+ 구간이 평균+31~34%/승률24~26%/
    함정2.8~2.9%로 이번 세션 개별 신호 중 최강이었으나, composite에 실제로
    연결해 7구간 walk-forward로 돌려보니 평균 +21.88%→+20.27%로 오히려 소폭
    악화(3/7구간 변화없음·3/7구간 악화·1/7구간만 개선) — use_contract_bonus와
    동일하게 독립 신호가 combined score에서는 노이즈로 작용. 기본값 False 유지,
    재현연구용 opt-in만 보존.

    conviction_sizing_gate: 2026-09-09 신규(opt-in). 지금까지는 새 신호/오버레이를
    "매수할지 말지"에만 반영했는데, 이번엔 구조적 변화 — 이미 계산되고 있던 score를
    포지션 사이즈에도 반영해본다(현재는 익절폭(20/30/40%)에만 쓰고 사이즈는 항상
    per_stock 고정). score 60~69점은 conviction_sizing_low(기본 0.7)배, 70~79점은
    1.0배(불변), 80점+는 conviction_sizing_high(기본 1.3)배로 매수금액을 조정 —
    고득점 확신 종목엔 더 크게, 문턱만 겨우 넘긴 종목엔 더 작게 베팅. 전체 현금
    한도(per_stock×max_positions)는 그대로라 재배분일 뿐 총 투입자본은 안 늘어남.

    7구간 walk-forward 검증(low/high 강도 5조합) 결과 전부 기각: baseline
    23.21%(같은 실행 시점 재현) 대비 mild(0.85/1.15)→12.34%, default(0.7/1.3)→
    8.10%, strong(0.5/1.5)→14.25%, high전용(1.0/1.3)→15.65%, low전용(0.7/1.0)→
    13.35% — 강도와 무관하게 전부 대폭 악화. 원인은 bull_covid 구간 하나로 명확:
    60~69점(문턱만 겨우 넘긴 종목)의 비중을 축소하자 그 구간 수익률이 142.58%→
    73.40%로 거의 반토막 났다(low전용만 켰는데도 동일하게 발생, 다른 6구간은
    완전 동일이었음에도). 즉 이 전략에서 score는 "살지 말지"를 가르는 품질필터일
    뿐, 문턱을 넘긴 종목들 사이에서 점수가 높을수록 수익률도 크다는 관계는
    성립하지 않는다(오히려 특정 구간에선 반대) — 확신도 기반 사이징의 전제 자체가
    이 전략엔 안 맞는 것으로 결론. 기본값 False 유지, 재현연구용 opt-in만 보존.
    """
    init_backtest_db()
    # 기본값도 실행 시작 시각으로 고정해, 이후 데이터 보정이 같은 실행 사양을 오염시키지 않게 한다.
    effective_data_asof_ts = data_asof_ts or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    # 2026-08-24: 확정된 기업행위 조정계수 로드(turnaround/regime_adaptive와 동일 목적).
    # 2026-09-04: data_asof_ts로 재현성 고정(위 docstring 참조).
    _corp_action_factors = _load_corp_action_factors(
        conn,
        [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM corporate_action_events WHERE adjustment_status='factor_confirmed'"
            + " AND updated_at <= ?",
            (effective_data_asof_ts,),
        ).fetchall()],
        data_asof_ts=effective_data_asof_ts,
    )
    run_id   = run_id or str(uuid.uuid4())[:8]
    run_name = run_name or f"composite_{start_date[:4]}"
    strategy = "composite"
    # 2026-07-27: 코드 재확인 결과 이 엔진도 유니버스 쿼리에 market_cap 조건이 없고
    # score 계산·positions에도 실제 시총이 쓰이지 않음(mkt_cap_억은 항상 기본값 500) —
    # v8/regime_adaptive와 동일하게 "current" 라벨이 부정확했으므로 정정.
    _record_run_spec(
        run_id, "composite", "composite_v3_eventbonus_20260830",
        {"per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date, "use_event_bonus": use_event_bonus,
         "data_asof_ts": effective_data_asof_ts,
         "use_market_filter": use_market_filter, "fast_crash_gate": fast_crash_gate,
         "fast_crash_drop": fast_crash_drop if fast_crash_gate else None,
         "fast_crash_days": fast_crash_days if fast_crash_gate else None,
         "vol_scale_gate": vol_scale_gate,
         "vol_scale_lookback": vol_scale_lookback if vol_scale_gate else None,
         "vol_scale_threshold": vol_scale_threshold if vol_scale_gate else None,
         "vol_scale_factor": vol_scale_factor if vol_scale_gate else None,
         "vol_scale_smooth": vol_scale_smooth if vol_scale_gate else None,
         "dynamic_score_gate": dynamic_score_gate,
         "dynamic_score_boost": dynamic_score_boost if dynamic_score_gate else None,
         "dynamic_stop_gate": dynamic_stop_gate,
         "dynamic_stop_widen": dynamic_stop_widen if dynamic_stop_gate else None,
         "component_weights": component_weights,
         "trail_stop_gate": trail_stop_gate,
         "trail_stop_pct": trail_stop_pct if trail_stop_gate else None,
         "trail_min_profit": trail_min_profit if trail_stop_gate else None,
         "value_trap_gate": value_trap_gate,
         "value_trap_min_tvol_억": value_trap_min_tvol_억 if value_trap_gate else None,
         "value_trap_max_concentration": value_trap_max_concentration if value_trap_gate else None,
         "value_trap_extreme_concentration": value_trap_extreme_concentration if value_trap_gate else None,
         "use_material_backlog_bonus": use_material_backlog_bonus,
         "use_contract_bonus": use_contract_bonus,
         "use_segment_bonus": use_segment_bonus,
         "event_hold_grace_gate": event_hold_grace_gate,
         "event_hold_grace_days": event_hold_grace_days if event_hold_grace_gate else None,
         "conviction_sizing_gate": conviction_sizing_gate,
         "conviction_sizing_low": conviction_sizing_low if conviction_sizing_gate else None,
         "conviction_sizing_high": conviction_sizing_high if conviction_sizing_gate else None},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
    )

    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,"
        "per_stock,max_pos,status) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, run_name, strategy, start_date, end_date, per_stock, max_positions, "running"),
    )
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            raise ValueError("시뮬레이션 날짜가 없습니다.")

        # KOSPI 레짐 로드
        market_bullish: Dict[str, bool] = {}
        vol_scale: Dict[str, float] = {}
        elevated_vol: Dict[str, bool] = {}
        try:
            krows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r["date"]  for r in krows]
            k_prices = [float(r["close"]) for r in krows]
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    continue
                kma = _ma(k_prices[max(0, ki-119):ki+1], 120)
                bullish = (kma is None) or (k_prices[ki] > kma)
                if fast_crash_gate and bullish and ki >= fast_crash_days:
                    k_ret = (k_prices[ki] - k_prices[ki - fast_crash_days]) / k_prices[ki - fast_crash_days]
                    if k_ret <= fast_crash_drop:
                        bullish = False
                market_bullish[kd] = bullish
                if (vol_scale_gate or dynamic_score_gate or dynamic_stop_gate) and ki >= vol_scale_lookback:
                    rets = [
                        (k_prices[j] - k_prices[j - 1]) / k_prices[j - 1]
                        for j in range(ki - vol_scale_lookback + 1, ki + 1)
                        if k_prices[j - 1] > 0
                    ]
                    if rets:
                        _mean = sum(rets) / len(rets)
                        _var  = sum((r - _mean) ** 2 for r in rets) / len(rets)
                        _sd = _var ** 0.5
                        elevated_vol[kd] = _sd >= vol_scale_threshold
                        if not vol_scale_gate:
                            pass
                        elif _sd < vol_scale_threshold:
                            vol_scale[kd] = 1.0
                        elif vol_scale_smooth:
                            vol_scale[kd] = max(vol_scale_factor, vol_scale_threshold / _sd)
                        else:
                            vol_scale[kd] = vol_scale_factor
        except Exception:
            pass

        # 종목 데이터 로드
        stock_codes = [r[0] for r in conn.execute("""
            SELECT stock_code, COUNT(*) AS cnt FROM price_history
            WHERE date>=? AND date<=? AND close>0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            GROUP BY stock_code HAVING COUNT(*) >= 200
            ORDER BY stock_code
        """, (warmup_start, end_date)).fetchall()]

        # ── 이벤트 보정 맵 (use_event_bonus=True일 때만 구축, 2026-08-30 실험) ──
        def _norm_date(raw) -> str:
            """'YYYY-MM-DD'/'YYYY.MM.DD'/'YYYYMMDD' 등을 'YYYY-MM-DD'로 정규화.
            파싱 불가하면 빈 문자열(호출부에서 걸러냄) — treasury_buyback.rcept_dt에
            극소수(13,229건 중 4건) 'YYYY.MM.DD' 표기가 섞여 있어 strptime 크래시 발견(2026-08-30)."""
            s = str(raw).strip()
            if len(s) >= 10 and s[4] == '-' and s[7] == '-':
                return s[:10]
            if len(s) >= 10 and s[4] == '.' and s[7] == '.':
                return s[:10].replace('.', '-')
            if len(s) == 8 and s.isdigit():
                return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
            return ""

        dilution_map: Dict[str, list] = {}
        buyback_map: Dict[str, list] = {}
        patent_map: Dict[str, list] = {}
        if use_event_bonus and stock_codes:
            ph = ",".join("?" * len(stock_codes))
            for r in conn.execute(f"""
                SELECT stock_code, disclosed_at FROM dilution_events
                WHERE event_type IN ('CB','BW','EB','RIGHTS')
                  AND (risk_event_bucket IS NULL OR risk_event_bucket != 'legacy_non_issuance_event')
                  AND stock_code IN ({ph})
            """, stock_codes).fetchall():
                d = _norm_date(r[1]) if r[1] else ""
                if d:
                    dilution_map.setdefault(r[0], []).append(d)
            for r in conn.execute(f"""
                SELECT stock_code, rcept_dt, event_type FROM treasury_buyback
                WHERE stock_code IN ({ph})
            """, stock_codes).fetchall():
                d = _norm_date(r[1]) if r[1] else ""
                if d:
                    buyback_map.setdefault(r[0], []).append((d, r[2]))
            for r in conn.execute(f"""
                SELECT stock_code, rcept_dt, signal_type FROM dart_rd_patent_signals
                WHERE stock_code IN ({ph})
            """, stock_codes).fetchall():
                d = _norm_date(r[1]) if r[1] else ""
                if d:
                    patent_map.setdefault(r[0], []).append((d, r[2]))
            for m in (dilution_map, buyback_map, patent_map):
                for c in m:
                    m[c].sort(key=lambda x: x if isinstance(x, str) else x[0])

        material_map: Dict[str, list] = {}
        backlog_map: Dict[str, list] = {}
        if use_material_backlog_bonus and stock_codes:
            material_map = _load_material_cost_events(conn, stock_codes)
            backlog_map = _load_backlog_surge_events(conn, stock_codes)

        contract_map: Dict[str, list] = {}
        if use_contract_bonus and stock_codes:
            contract_map = _load_contract_win_events(conn, stock_codes)

        segment_map: Dict[str, list] = {}
        if use_segment_bonus and stock_codes:
            segment_map = _load_segment_divergence_events(conn, stock_codes)

        stock_data: Dict[str, dict] = {}
        date_idx:   Dict[str, dict] = {}

        for sc in stock_codes:
            rows = conn.execute("""
                SELECT date, close, volume,
                       COALESCE(frn_net_buy, 0) AS frn,
                       COALESCE(inst_net_buy, 0) AS inst,
                       COALESCE(open, 0) AS open_p
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (sc, warmup_start, end_date)).fetchall()
            if len(rows) < 120:
                continue
            # data_asof_ts 지정 시 그 시각 이후 UPDATE된 행은 제외(재현성 고정용, 2026-09-04).
            fin_rows = conn.execute(f"""
                SELECT f.year, f.quarter, f.revenue, f.operating_profit, f.eps, f.bps,
                       f.total_equity, f.net_income, f.roe, f.is_annual,
                       COALESCE(d.avail_date,
                         CASE WHEN f.is_annual=1 THEN printf('%d-03-31', f.year+1)
                              WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date,
                       f.id, f.report_type, f.created_at, f.updated_at
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=? AND d.year=f.year
                    AND d.quarter=CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                    AND d.is_annual=CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
                WHERE f.stock_code=? AND f.report_type IN ('CFS','') AND f.quarter IN (1,2,3,4)
                  AND f.updated_at <= ?
                ORDER BY f.year DESC, f.quarter DESC, f.report_type DESC, f.id DESC
            """, (sc, sc, effective_data_asof_ts)).fetchall()
            if not fin_rows:
                continue
            fin_all = [(r["year"], r["quarter"], r["revenue"], r["operating_profit"],
                        r["eps"], r["bps"], r["total_equity"], r["net_income"],
                        r["roe"], bool(r["is_annual"]), r["avail_date"],
                        r["id"], r["report_type"], r["created_at"], r["updated_at"]) for r in fin_rows]

            dts = [r["date"] for r in rows]
            prs = [float(r["close"]) for r in rows]
            vls = [float(r["volume"]) if r["volume"] else 0.0 for r in rows]
            fns = [float(r["frn"]) if r["frn"] else 0.0 for r in rows]
            ins = [float(r["inst"]) if r["inst"] else 0.0 for r in rows]
            ops = [float(r["open_p"]) if r["open_p"] else 0.0 for r in rows]

            # sim_start_i
            sim_i = next((j for j, d in enumerate(dts) if d >= start_date), len(dts))

            stock_data[sc] = {
                'dates': dts, 'prices': prs, 'volumes': vls,
                'frn': fns, 'inst': ins, 'fins': fin_all,
                'sim_start_i': sim_i, 'opens': ops,
            }
            date_idx[sc] = {d: j for j, d in enumerate(dts)}

        # ── 밸류트랩 가드레일 사전계산 (2026-09-06, value_trap_gate=True일 때만) ──
        # 저유동성+대주주 지분집중 조합 종목을 신규매수 후보에서 제외.
        # 근거: 흑자+저PBR(<1.2) 24,218건 walk-forward — "60일 평균거래대금<3억 AND
        # 대주주+특수관계인 지분>50%" 조합(n=38)은 6개월 forward return 평균 +0.5%/
        # 정체·하락 68.4%로, 나머지 전체(+22.3%/50.4%) 대비 확연히 나빴다(미원화학
        # 실사례). 지분 75%+는 조합 조건과 무관하게 단독으로도 나쁨.
        value_trap_map: Dict[str, bool] = {}
        if value_trap_gate and stock_data:
            share_hist: Dict[str, list] = {}
            for code, ef, et, shares in conn.execute("""
                SELECT stock_code, effective_from, effective_to, shares_issued
                FROM security_share_history ORDER BY stock_code, effective_from
            """).fetchall():
                share_hist.setdefault(code, []).append((ef, et, float(shares or 0)))

            def _shares_asof(code: str, day: str) -> float:
                for ef, et, sh in reversed(share_hist.get(code, [])):
                    if ef <= day and (et is None or day < et):
                        return sh
                return 0.0

            _codes = list(stock_data.keys())
            _ph = ",".join("?" * len(_codes))
            _latest_by_code: Dict[str, dict] = {}
            for sc, reporter, cnt in conn.execute(
                f"""SELECT stock_code, repror, sp_stock_lmp_cnt
                    FROM dart_insider_holdings
                    WHERE stock_code IN ({_ph}) AND sp_stock_lmp_cnt IS NOT NULL
                      AND rcept_dt <= ?
                    ORDER BY stock_code, rcept_dt ASC""",
                _codes + [start_date],
            ).fetchall():
                _latest_by_code.setdefault(sc, {})[reporter] = float(cnt)

            for sc, sd in stock_data.items():
                sh = _shares_asof(sc, start_date)
                concentration = None
                if sh and sh > 0 and sc in _latest_by_code:
                    concentration = sum(_latest_by_code[sc].values()) / sh * 100
                si = sd['sim_start_i']
                lo = max(0, si - 60)
                win_vols = sd['volumes'][lo:si]
                win_prices = sd['prices'][lo:si]
                avg_tvol_억 = (sum(v * p for v, p in zip(win_vols, win_prices)) / len(win_vols) / 1e8
                               if win_vols else None)
                is_trap = False
                if concentration is not None:
                    if concentration >= value_trap_extreme_concentration:
                        is_trap = True
                    elif (avg_tvol_억 is not None and avg_tvol_억 < value_trap_min_tvol_억
                          and concentration > value_trap_max_concentration):
                        is_trap = True
                value_trap_map[sc] = is_trap

        conn.row_factory = None

        # 시뮬레이션
        cash = per_stock * max_positions  # 2026-07-16: 실현손익 누산(capital) → 현금원장 전환
        positions: Dict[str, dict] = {}
        trades: List[dict] = []
        daily_pnl: List[Tuple[str, float]] = []
        _pb: Dict[str, dict] = {}   # pending buys  (D+1 집행)
        _ps: Dict[str, dict] = {}   # pending sells (D+1 집행)
        evidence = SignalEvidenceLedger("composite", {
            "score_threshold": score_threshold, "use_event_bonus": use_event_bonus,
            "use_material_backlog_bonus": use_material_backlog_bonus,
            "use_contract_bonus": use_contract_bonus, "use_segment_bonus": use_segment_bonus,
            "data_asof_ts": effective_data_asof_ts,
        })
        _event_windows = {"patent": 365, "buyback": 180, "dilution": 365, "material": 365,
                          "backlog": 180, "contract": 180, "segment": 365}

        def _note_evidence(sc: str, day: str) -> None:
            items = [financial_row_evidence(
                r[11], sc, r[0], r[1], report_type=r[12], role=role, is_annual=bool(r[9]),
                value={"revenue": r[2], "operating_profit": r[3], "eps": r[4], "bps": r[5]},
                available_at=r[10], collected_at=r[13], modified_at=r[14])
                for role, r in _composite_financial_inputs(stock_data[sc]['fins'], day)]
            if not items:
                items = [evidence_item("financial_data", None, None)]
            active = {
                "patent": patent_map if use_event_bonus else None,
                "buyback": buyback_map if use_event_bonus else None,
                "dilution": dilution_map if use_event_bonus else None,
                "material": material_map if use_material_backlog_bonus else None,
                "backlog": backlog_map if use_material_backlog_bonus else None,
                "contract": contract_map if use_contract_bonus else None,
                "segment": segment_map if use_segment_bonus else None,
            }
            ev_rows = []
            for name, m in active.items():
                if m is None:
                    continue
                cutoff = (datetime.strptime(day, "%Y-%m-%d")
                          - timedelta(days=_event_windows[name])).strftime("%Y-%m-%d")
                for ev in m.get(sc, ()):
                    ev_date = ev if isinstance(ev, str) else ev[0]
                    if cutoff <= ev_date <= day:
                        ev_rows.append({
                            "row_id": f"{name}:{sc}:{ev_date}:{'' if isinstance(ev, str) else ev[-1]}",
                            "available_at": ev_date,
                            "value": {"map": name, "event": ev if isinstance(ev, str) else list(ev),
                                      "basis": _event_date_basis(sc, ev_date)
                                      if name in ("material", "backlog", "segment") else "actual_disclosure"},
                        })
            ev_item = evidence_aggregate(
                "event_adjustment", ev_rows,
                source_key="maps:" + ",".join(k for k, v in active.items() if v is not None) if any(
                    v is not None for v in active.values()) else "no_event_maps_enabled")
            if any(r["value"]["basis"] == "statutory_estimate" for r in ev_rows):
                ev_item["availability_basis"] = "statutory_estimate"
            items.append(ev_item)
            evidence.note(sc, day, items)

        for day in sim_dates:

            # ── Phase A: 전일 매도 신호 → 오늘 시가/종가 집행 ────────
            to_remove_ps = []
            for sc in list(_ps.keys()):
                if sc not in positions:
                    to_remove_ps.append(sc)
                    continue
                pos = positions[sc]
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i   = im[day]
                op  = sd['opens'][i] if i < len(sd['opens']) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                ep   = pos['entry_price']
                qty  = pos['qty']
                _cmp_ep_adj = _corp_action_adjusted_entry(
                    _corp_action_factors, sc, pos['entry_date'], day, ep)
                _cmp_amt, _cmp_pct = _net_profit(_cmp_ep_adj, curr, qty, pos.get('mkt_cap_억', 500))
                cash += qty * _cmp_ep_adj + _cmp_amt
                held = pos.get('hold_days', 0)
                trades.append({
                    'sc': sc, 'entry': pos['entry_date'], 'exit': day,
                    'entry_price': ep, 'exit_price': curr,
                    'return_pct': _cmp_pct, 'pnl': _cmp_amt,
                    'reason': _ps[sc].get('reason', '매도'),
                    'score': pos.get('score', 0), 'held_days': held,
                })
                del positions[sc]
                to_remove_ps.append(sc)
            for sc in to_remove_ps:
                _ps.pop(sc, None)

            # ── Phase B: 전일 매수 신호 → 오늘 시가/종가 집행 ────────
            sorted_buys = sorted(_pb.items(), key=lambda x: (-x[1].get('score', 0), x[0]))
            for sc, meta in sorted_buys:
                if sc in positions or len(positions) >= max_positions:
                    continue
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i   = im[day]
                op  = sd['opens'][i] if i < len(sd['opens']) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                if curr <= 0:
                    continue
                s = meta.get('score', 60)
                take_p = 0.40 if s >= 80 else (0.30 if s >= 70 else 0.20)
                _eff_per_stock = per_stock * vol_scale.get(day, 1.0) if vol_scale_gate else per_stock
                if conviction_sizing_gate:
                    _conv_mult = conviction_sizing_high if s >= 80 else (1.0 if s >= 70 else conviction_sizing_low)
                    _eff_per_stock *= _conv_mult
                budget = min(_eff_per_stock, cash * 0.99)
                qty = int(budget / curr)
                if qty < 1 or qty * curr > cash:
                    continue
                cash -= qty * curr
                positions[sc] = {
                    'entry_date': day, 'entry_price': curr, 'qty': qty,
                    'score': s, 'take_profit': take_p, 'hold_days': 0,
                    'mkt_cap_억': meta.get('mkt_cap_억', 500),
                    'peak_price': curr,
                    'event_flagged': meta.get('event_flagged', False),
                }
            _pb.clear()

            # ── hold_days 증가 ─────────────────────────────────────
            for pos in positions.values():
                pos['hold_days'] = pos.get('hold_days', 0) + 1

            # ── Phase C: 매도 신호 탐지 → _ps 큐 ─────────────────
            for sc, pos in list(positions.items()):
                if sc in _ps:
                    continue
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i    = im[day]
                curr = sd['prices'][i]
                if curr <= 0:
                    continue
                ep   = pos['entry_price']
                ret  = (curr - ep) / ep
                held = pos.get('hold_days', 0)
                take = pos.get('take_profit', 0.25)
                peak = max(pos.get('peak_price', ep), curr)
                pos['peak_price'] = peak
                _stop_th = -0.10
                if dynamic_stop_gate and elevated_vol.get(day, False):
                    _stop_th -= dynamic_stop_widen
                exit_reason = None
                if ret >= take:
                    exit_reason = f"익절{take*100:.0f}%"
                elif ret <= _stop_th:
                    exit_reason = f"손절{_stop_th*100:.0f}%"
                elif trail_stop_gate and held > 5 and ret > trail_min_profit and \
                        (curr - peak) / peak <= trail_stop_pct:
                    exit_reason = f"추적손절(고점-{abs((curr-peak)/peak)*100:.0f}%)"
                else:
                    _ma60_hold_min = 5
                    if event_hold_grace_gate and pos.get('event_flagged'):
                        _ma60_hold_min = 5 + event_hold_grace_days
                    if held > _ma60_hold_min:
                        ma60_e = _ma(sd['prices'][max(0, i-59):i+1], 60)
                        if ma60_e and curr < ma60_e:
                            exit_reason = "MA60붕괴"
                    # 240일 장기횡보 보류: 하락장 -1.3%→-25.3% 악화로 미적용
                if exit_reason:
                    _ps[sc] = {'reason': exit_reason}

            # ── Phase D: 매수 신호 탐지 → _pb 큐 ─────────────────
            if len(positions) < max_positions and (not use_market_filter or market_bullish.get(day, True)):
                _eff_score_threshold = score_threshold
                if dynamic_score_gate and elevated_vol.get(day, False):
                    _eff_score_threshold = score_threshold + dynamic_score_boost
                candidates = []
                for sc, sd in stock_data.items():
                    if sc in positions or sc in _ps:
                        continue
                    if value_trap_gate and value_trap_map.get(sc, False):
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]
                    s = _score_stock(
                        i, sd['sim_start_i'], sd['dates'], sd['prices'],
                        sd['volumes'], sd['frn'], sd['inst'], sd['fins'],
                        code=sc if (use_event_bonus or use_material_backlog_bonus or use_contract_bonus or use_segment_bonus) else None,
                        dilution_map=dilution_map if use_event_bonus else None,
                        buyback_map=buyback_map if use_event_bonus else None,
                        patent_map=patent_map if use_event_bonus else None,
                        material_map=material_map if use_material_backlog_bonus else None,
                        backlog_map=backlog_map if use_material_backlog_bonus else None,
                        contract_map=contract_map if use_contract_bonus else None,
                        segment_map=segment_map if use_segment_bonus else None,
                        weights=component_weights)
                    if s >= _eff_score_threshold:
                        ev_flag = False
                        if event_hold_grace_gate and use_material_backlog_bonus:
                            asof = sd['dates'][i]
                            for ev_date, _pts, _label in material_map.get(sc, ()):
                                if ev_date <= asof and (datetime.strptime(asof, "%Y-%m-%d") - datetime.strptime(ev_date, "%Y-%m-%d")).days <= 365:
                                    ev_flag = True
                                    break
                            if not ev_flag:
                                for ev_date, _pts, _label in backlog_map.get(sc, ()):
                                    if ev_date <= asof and (datetime.strptime(asof, "%Y-%m-%d") - datetime.strptime(ev_date, "%Y-%m-%d")).days <= 180:
                                        ev_flag = True
                                        break
                        candidates.append((s, sc, ev_flag))
                candidates.sort(key=lambda x: (-x[0], x[1]))
                for s, sc, ev_flag in candidates:
                    if len(_pb) + len(positions) - len(_ps) >= max_positions:
                        break
                    _pb[sc] = {'score': s, 'event_flagged': ev_flag}
                    _note_evidence(sc, day)

            # ── Phase E: 일별 PnL ──────────────────────────────────
            portfolio_val = cash
            for sc, pos in positions.items():
                sd = stock_data[sc]
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i = im[day]
                curr = sd['prices'][i]
                portfolio_val += curr * pos['qty']
            daily_pnl.append((day, portfolio_val))

        # 미청산 포지션 강제 청산
        last_day = sim_dates[-1] if sim_dates else end_date
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            curr, final_reason = _final_liquidation_quote_for_code(conn, sc, last_day, im, sd['prices'])
            ep   = pos['entry_price']
            qty  = pos['qty']
            _cmpf_ep_adj = _corp_action_adjusted_entry(
                _corp_action_factors, sc, pos['entry_date'], last_day, ep)
            _cmpf_amt, _cmpf_pct = _net_profit(_cmpf_ep_adj, curr, qty, pos.get('mkt_cap_억', 500))
            cash += qty * _cmpf_ep_adj + _cmpf_amt
            trades.append({
                'sc': sc, 'entry': pos['entry_date'], 'exit': last_day,
                'entry_price': ep, 'exit_price': curr,
                'return_pct': _cmpf_pct, 'pnl': _cmpf_amt,
                'reason': final_reason, 'score': pos.get('score', 0),
                'held_days': pos.get('hold_days', 0),
            })
        if last_day:
            terminal = (last_day, cash)
            if daily_pnl and daily_pnl[-1][0] == last_day:
                daily_pnl[-1] = terminal
            else:
                daily_pnl.append(terminal)

        # 집계
        total_trades   = len(trades)
        winners        = [t for t in trades if t['return_pct'] > 0]
        losers         = [t for t in trades if t['return_pct'] <= 0]
        win_rate       = len(winners) / total_trades * 100 if total_trades else 0
        total_invested = per_stock * max_positions
        total_ret_pct  = (cash - total_invested) / total_invested * 100 if total_invested else 0

        avg_win  = sum(t['return_pct'] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t['return_pct'] for t in losers) / len(losers)   if losers  else 0
        pf       = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        # 스코어 분포
        score_dist = {
            '60-69': len([t for t in trades if 60 <= t.get('score', 0) < 70]),
            '70-79': len([t for t in trades if 70 <= t.get('score', 0) < 80]),
            '80+':   len([t for t in trades if t.get('score', 0) >= 80]),
        }

        days = len(sim_dates)
        yrs  = days / 252
        cagr = (cash / total_invested) ** (1 / yrs) * 100 - 100 if yrs > 0 and total_invested > 0 else 0

        summary = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 복합 스코어링 전략 (threshold={score_threshold}점)\n"
            f"스코어 분포: 60-69점={score_dist['60-69']}건 / 70-79점={score_dist['70-79']}건 / 80+점={score_dist['80+']}건\n"
            f"총 거래: {total_trades}건  승률: {win_rate:.1f}%  Profit Factor: {pf:.2f}\n"
            f"avg 수익: {avg_win:+.1f}%  avg 손실: {avg_loss:+.1f}%\n"
            f"CAGR: {cagr:.2f}%  총수익: {total_ret_pct:+.1f}%\n"
        )

        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("""
            UPDATE backtest_runs SET
                status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, summary_text=?, trades_json=?, strategy=?
            WHERE run_id=?
        """, (total_ret_pct, cagr, win_rate, total_trades,
              summary, json.dumps(trades, ensure_ascii=False), strategy, run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        _register_execution_artifacts(run_id, total_invested, cash)
        evidence.persist(run_id, trades)
        return run_id

    except Exception as e:
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        raise


# ══════════════════════════════════════════════════════════════
#  Meta-V 2.0: BULL → 복합스코어링, BEAR → V7 흑자전환
# ══════════════════════════════════════════════════════════════
