# 데이터 신선도 전체 점검 결과 — 2026-09-27

도구: `scripts/ops/audit_data_freshness.py`(전체 테이블 스캔, 목록은 `DATA_FRESHNESS_20260927.md`) + `collection_health.evaluate_all_contracts`(수집 계약 24개) + 실행 원장.
2026-09-24~26은 추석 연휴·주말이라 **마지막 정상 거래일은 2026-09-23**이다.

## A. 실제 결함 — 수정함
| 데이터 | 상태 | 원인 | 조치 |
|---|---|---|---|
| 섹터 로테이션 캐시(`sector_rotation_cache`) | 9/11 기준에서 9일 정지 | 영업이익 NULL 행 `float(None)` 오류 + 잡이 예외를 삼켜 원장엔 success | NULL 제외, 예외 재발생, 재계산(기준일 9/23) |
| 매크로 시세(미국10Y·환율·VIX 등) | 9/8~9/11 정지 | 5일 창과 저장분 무겹침 → 게이트 영구 격리, 저장분 스냅샷 오염, VIX 9/8=8.73 오염값, 오류 연쇄 | `macro_window_repair` 신설 등(상세 CLAUDE.md), 9/25까지 복구 |
| 대차종목순위 `short_rank_daily`·내외국인잔고 `short_foreign_balance` | 9/3·9/1에서 정지 | 수집기가 PG에서 이미 있는 컬럼에 `ALTER TABLE ADD COLUMN`을 실행 → `db_compat`가 실패 문장마다 **트랜잭션 전체 롤백**해 앞서 넣은 순위가 매번 사라짐(로그엔 "N건 저장") | 공통 수정: `ADD COLUMN`을 `IF NOT EXISTS`로 자동 변환(수집기 7곳 동일 패턴), 잔고 보강 UPDATE 앞 커밋·타임아웃 600초, 누락일 백필 |
| `db_compat` readonly 연결 누수 | `ReadOnlySqlTransaction` 간헐 실패 | readonly 연결이 그 상태로 풀에 반납 | `close()`에서 복구 |
| 섹터 지수 `sector_index_daily` | 9/22 | 재시작으로 잡이 interrupted | 재계산(9/23) |
| 신선도 계약 오탐 | 텐버거·수주공시 stale | 달력일 기준 + 연휴 | 거래일 기준으로 전환 |

## B. 의도된 정지 — 정책 결정 필요
| 데이터 | 상태 | 이유 |
|---|---|---|
| 텔레그램 언급 집계 `tg_daily_mentions`(7/10), `telegram_messages`(8/8) | 정지 | `telegram_monitor.py`가 **비용 절감으로 비활성화**됨. 그래서 수급 현황 › 관심도·수급 검증 탭이 비어 있음(탭에 사유 표시 추가). 재개하려면 LLM 없이 종목명 규칙 매칭으로 집계하는 저비용 버전이 필요 |

## C. 외부 조치 필요
| 데이터 | 상태 | 이유 |
|---|---|---|
| 키움 장중 분봉·틱·스냅샷, 대량체결(`kiwoom_*`), 투자자수급 일부 | 9/21에서 정지 | 키움 REST **8050: IP가 등록되지 않았습니다** — 공인 IP 변경. 키움 REST 포털 허용 IP에 현재 IP 등록 필요. 잡은 0행이어도 success로 남음(원장 신뢰 주의) |
| `listed_company_info`(공공데이터 상장회사 정보) | 0행 | 알려진 이슈(KRX getItemInfo 무응답) |

## D. 정상(연·월·분기·이벤트성) 또는 폐기 테이블
`investor_trading_daily`(폐기), `radar_price_cache`·`radar_market_cache`(폐기 캐시), `futures_*`(4월 정지·미사용), `nps_workplace_monthly`·`employment_*`(월간), `dart_*_quarterly`·`order_backlog`(분기 배치 9/20), `quant_stock_trade_signal_snapshots`(이벤트 구동), 각종 일회성 연구·백업 테이블.
목록 전체(110개 후보)는 `DATA_FRESHNESS_20260927.md` 참조 — 5일 이상 뒤처진 것 중 나머지는 위 분류에 해당하거나 일회성이다.

## E. 추가 점검 후보(이번엔 원인 미조사)
`cafe_signal_posts`(8/24 이후 정지인데 `카페시그널주간` 잡은 success), `market_regime_daily`(8/7)·`quant_market_regime_signal`(8/21), `market_signal_briefing`·`sector_posts`(8/9), `stockeasy_sector_rs_daily`(7/22), `stock_meta`(5/24), `naver_financial`(5/27).
