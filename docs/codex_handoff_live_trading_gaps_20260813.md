# 실전매매 전환 잔여 갭 핸드오프 (2026-08-13)

> **✅ 2026-08-13(3차) 갱신: 아래 3개 항목 전부 구현·검증 완료.** CLAUDE.md "2026-08-13(3차)"
> 변경이력 참조. 이 문서는 구현 세부(코드 제안)가 실제로 어떻게 반영됐는지 대조용으로 보존.

## 배경

Codex가 `scripts/audit_strategy_center_live_data_readiness.py`로 전략센터 실전매매 전환의
데이터 준비도를 감사 중(우선순위: 기업행사 보정 → 거래제한·호가 데이터 → PostgreSQL 이관
완결 → 수급 최신화 → 주문·체결 대사 → 전략별 PIT/전진검증). 사용자 지시로 Claude가 이
목록에 없는 사각지대를 재검토 — `routes/kis_trading.py`의 KRX 관리종목 데이터가 한 달 넘게
정체돼 있던 버그를 발견·수정하고 7번째 리스크게이트(`_gate_managed_issue`)로 연결 완료
(CLAUDE.md 2026-08-13(2차) 참조). 이 문서는 같은 과정에서 발견했으나 이번 세션 범위에서
고치지 않은 3개 항목을 정리한다 — 전부 로컬 코드 구현이 가능하고 외부 API 권한이 추가로
필요하지 않다(Codex 감사 항목들과 달리 데이터 수집 문제가 아니라 주문 실행 로직의 안전성
문제).

## 1. 주문 idempotency(중복주문 방지) 부재 — ✅완료(2026-08-13(3차))

**현재 상태**: `place_paper_order()`(`routes/kis_trading.py` L598~)는 요청이 들어올 때마다
무조건 체결 처리한다. 같은 전략키(`strategy_key`)+종목+당일 조합에 대해 이미 주문을
넣었는지 확인하는 로직이 전혀 없다.

**리스크**: 실전 전환 시 다음 시나리오에서 중복 주문이 발생할 수 있다:
- 스케줄러 잡이 크래시 후 재시작되며 같은 신호를 다시 제출(이 프로젝트는 세션 내내 서버
  재시작이 잦았다 — `scheduler.py`의 각 루프가 `_run_job_safe`로 감싸져 있지만, 잡 자체가
  "오늘 이미 처리했는지" 확인하지 않는 경우 재시작마다 재실행될 수 있음).
- 네트워크 타임아웃 후 클라이언트가 같은 주문을 재요청.
- 병합조합(combo) 등 여러 전략이 동시에 같은 종목에 신호를 낼 때 순서 경합.

**완화 완료(이미 존재)**: 현금 확인(P0), 최대 보유종목수 한도, 일일손실한도 — 이들이
대규모 중복매수는 어느 정도 막지만, "한도 안에서의 소규모 중복"은 막지 못한다.

**제안 구현**: `live_orders` 테이블에 `(strategy_key, stock_code, side, DATE(created_at))`
복합 유니크 인덱스를 추가하거나, `place_paper_order()` 시작부에 다음 확인을 추가:
```python
existing = c.execute(
    "SELECT COUNT(*) FROM live_orders WHERE strategy_key=? AND stock_code=? AND side=? "
    "AND DATE(created_at)=DATE('now') AND status='FILLED'",
    (o.strategy_key, o.stock_code, o.side),
).fetchone()[0]
if existing > 0 and not o.allow_duplicate:
    raise HTTPException(409, "오늘 이미 동일 전략/종목/방향으로 체결된 주문이 있습니다")
```
`allow_duplicate` 같은 명시적 오버라이드 플래그를 두어(기존 `override_wait_confirm` 패턴과
동일) 의도적 추가매수(피라미딩 등 이미 검증된 전략)는 막지 않도록 설계할 것 — 무조건
차단하면 2026-08-08 세션에서 검증한 점수기반 피라미딩(`score_based_pyramiding_20260809`)
같은 정당한 재진입 로직과 충돌한다.

## 2. 호가단위(tick size) 반올림 없음 — ✅완료(2026-08-13(3차))

**현재 상태**: `fill_price = float(o.limit_price) if o.limit_price else px` — KRX 호가단위
규칙(가격대별 최소 호가 간격, 예: 2,000원 미만은 1원 단위, 5,000천만원 이상은 1,000원
단위 등)을 전혀 반영하지 않는다.

**리스크**: PAPER 모드에서는 문제가 없지만(가상 체결이라 임의 가격 허용), 실전 KIS API에
`limit_price`를 보낼 때 호가단위에 맞지 않으면 주문 자체가 거부될 수 있다.

**제안 구현**: KRX 공식 호가단위 테이블(가격대 8구간)을 함수화해 `place_paper_order()`와
향후 `/live/order`가 활성화될 때 공용으로 쓰도록 신규 유틸(예: `utils.py`에
`round_to_tick_size(price: float) -> int`)을 추가. 이번 세션에서는 실전 주문 자체가
403으로 완전 차단돼 있어 급하지 않으나, `/live/order` 차단을 해제하기 전에는 반드시
선행되어야 한다.

## 3. `kis_client.py` 토큰 갱신 실패 시 장중 무음 실패 가능성 — ✅완료(2026-08-13(3차))

이번 세션에서 코드를 읽지 못했다 — `kis_client.py`가 토큰 만료/갱신 실패를 어떻게
처리하는지, 실패 시 텔레그램 알림 등 가시적 경고가 발생하는지 확인이 필요하다.
`_latest_price()`가 `kis_client.get_current_price()` 실패 시 `price_history` DB 폴백을
쓰도록 이미 방어돼 있어(L165~175) 시세 조회 자체는 안전하지만, **주문 제출** 경로에서
토큰 실패가 났을 때 어떤 예외가 사용자/스케줄러에 전달되는지는 확인하지 못했다. 다음
세션에서 `kis_client.py`의 토큰 관리 로직과, 주문 제출 시 인증 실패를 리스크게이트/주문
생애주기 로그(`live_order_events`)에 명시적으로 기록하는지 확인 권장.

## 참고 — 이번 세션에서 완료한 것 (재작업 방지용)

- `collectors/krx_isu_base_info.py`: `basDd=오늘` 빈 응답 시 최대 5일 역순 재조회하도록
  수정 완료(`max_lookback_days=5`). 실제 데이터를 받은 날짜를 `snapshot_date`로 저장(오늘
  날짜로 위장하지 않음). 즉시 실행해 2026-07-10→2026-08-12로 자가복구 완료.
- `routes/kis_trading.py`: `_gate_managed_issue()` 신규, `evaluate_risk_gates()`의 buy
  경로와 `BLOCKED_RISK` 판정에 연결 완료. HTTP 엔드투엔드 검증 완료.
- 이 raw-sqlite3 수집기의 쓰기가 Postgres 라우터를 안 타는 것 자체는 **버그가 아님** —
  `sync_tenbagger_postgres.py`의 `stock_universe` 항목이 날짜필터 없는 전체동기화라 30분
  주기로 자동 catch-up된다(단, 이번엔 즉시검증을 위해 psycopg 직접 UPDATE로 수동
  선반영함). `stock_base_info_history`(스냅샷 이력 테이블 자체)는 sync 대상이 아니지만
  이건 감사용 이력일 뿐 라이브 게이트가 참조하는 건 `stock_universe.sector_type`이므로
  문제 없음.
