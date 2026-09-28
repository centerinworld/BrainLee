# 미국 티커 재사용/충돌 조사 (2026-09-27)

Minervini PIT 결측 감사에서 "S&P500 PIT(39) + Nasdaq-100 PIT(89) 합집합 117개" 중
24개는 `us_price_history`에 이미 행이 있는데도 PIT 로더가 missing 처리했다. 대부분은
단순히 이 프로젝트의 가격 수집 시작일(2021-05-24) 이전 이력이 없는 것뿐이지만, 그중
9개는 **해당 상장폐지/합병 시점과 현재 저장된 데이터 시작일 사이에 수년의 공백**이
있어 티커 재사용 여부를 확인했다. 방법은 전부 동일: 인수·합병·상폐 시점의 **실제
확정 가격(SEC/기업 보도자료 근거)** 과 `us_price_history`에 저장된 그 시점 전후 가격을
직접 대조한다.

## 확인 결과 (9건 확인, 1건 미해결)

| 티커 | 역사적 회사 | 상폐/개명 확정 시점·가격 | DB 시작일·가격 | 판정 | 근거 |
|---|---|---|---|---|---|
| `BBBY` | Bed Bath & Beyond Inc.(2023 파산 청산) | 2023-04-23 | 2026, Beyond Inc(구 Overstock)가 브랜드만 인수 후 2025-08 개명 | **다른 법인** | [SEC S-4](https://www.sec.gov/Archives/edgar/data/1130713/000114036126000734/ny20060053x1_s4.htm) |
| `PCLN` | The Priceline Group Inc. | 2018-02-27 개명(→BKNG) | 2025-10, Pictet Cleaner Planet **ETF**(기업 아님) | **완전히 다른 대상** | [Bloomberg](https://www.bloomberg.com/quote/PCLN*:MM) |
| `LIFE` | Life Technologies Corp. | 2014-02-03 인수(Thermo Fisher) | 2026, Ethos Technologies(보험/핀테크) | **다른 법인** | [Nasdaq](https://www.nasdaq.com/market-activity/stocks/life) |
| `SNDK` | SanDisk Corp.(1995~2016) | 2016-05-12 인수(Western Digital) | 2025-02, SanDisk Corp(2025 WDC 플래시사업부 스핀오프) | **연관은 있으나 별개 법인** | [SanDisk IR](https://investor.sandisk.com/news-releases/news-release-details/sandisk-celebrates-nasdaq-listing-after-completing-separation) |
| `GENZ` | Genzyme Corp. | 2011-04-04 인수확정 **$74.00/주**(Sanofi) | DB는 그 시점 포함 2008~2026 **공백 없이 $20~24대** | **가격 자체가 실제 Genzyme과 무관(데이터 오류)** | [Sanofi 발표](https://www.news.sanofi.us/press-releases?item=118549) |
| `JAVA` | Sun Microsystems, Inc. | 2010-01-27 인수확정 **$9.50/주**(Oracle) | 2021-10부터 **$46~47대**(인수가 5배, 11년 후) | **다른 법인** | [Oracle 발표](https://www.oracle.com/corporate/pressrelease/oracle-buys-sun-042009.html) |
| `SHLD` | Sears Holdings Corp. | 2018-10-15 파산, 상폐 직전 **$0.36** | 2023-09부터 **$24대**(생존shell은 SHLDQ로 심볼도 다름) | **다른 법인** | [근거](https://www.9news.com/article/money/business/sears-is-delisted-on-nasdaq/73-607988737) |
| `MEDI` | MedImmune, Inc. | 2007-06-18 인수확정 **$58.00/주**(AstraZeneca) | 2022-11부터 **$19~20대**(15년 후) | **다른 법인** | [FierceBiotech](https://www.fiercebiotech.com/biotech/press-release-astrazeneca-to-acquire-medimmune) |
| `INFO` | IHS Markit Ltd. | 2022-02-28 합병(SPGI, 0.2838주 교환 ≈ **$108/주** 환산가) | 2024-10부터 **$20대**(2.5년 후) | **다른 법인** | [S&P Global 발표](https://press.spglobal.com/2022-02-28-S-P-Global-and-IHS-Markit-Complete-Merger) |
| `MICC` | Millicom International Cellular S.A. | 현재 TIGO로 거래 | 2025-12-08부터, **The Magnum Ice Cream Company**(Unilever 아이스크림 스핀오프, 상장일 정확히 일치) | **완전히 다른 대상** | [SEC company_tickers.json](https://www.sec.gov/files/company_tickers.json), [Wikipedia](https://en.wikipedia.org/wiki/The_Magnum_Ice_Cream_Company) |
| `SPLS` | Staples Inc.(2017 비상장 전환) | 2017-09-12 LBO(Sycamore Partners) | 2026부터 174행 — SEC 현재 상장 티커 목록에 SPLS 자체가 없음 | **미해결** | 근거 불충분 |

`INFO`는 `us_security_outcomes`에 이미 SPGI 합병으로 정확히 기록되어 있어 백테스트 청산가치 계산 자체는 문제없다 — 이번 지적은 오직 `us_price_history`의 **가격 테이블**에 다른 회사 데이터가 들어있다는 점이다.

## 조치

- `us_ticker_identity_conflict` 테이블 신설(`scripts/sync_us_ticker_identity_conflicts.py --apply`, 11행) — 티커·역사적 회사·판정·근거 URL 보존.
- `scripts/backfill_us_delisted_prices.py`에 안전장치: 요청 티커가 이 테이블에서 `confirmed_*` 상태면 **API 호출 전에 fail-closed**(`--force-identity-conflict`로만 수동 우회). 테스트로 확인(BBBY/PCLN 요청 시 즉시 거부).
- 자동 백필 크론(`scripts/ops/cron_us_delisted_backfill.py`)의 대상 93개 목록에는 애초에 이 9개가 포함되지 않아 실행상 영향은 없음 — 이번 조치는 향후 다른 스크립트/세션이 무심코 포함시키는 것을 막는 방어선이다.
- 남은 13개(`DELL/EA/EQR/FOX/FOXA/GOLD/KHC/MNST/PEP/SATS/SBNY/VLTO`)는 상폐 시점과 데이터 시작일 사이 공백이 없거나(수집 시작일 2021-05-24 부근에서 시작) 정상적으로 지금도 상장 중인 회사라 이번 조사에서 문제로 분류하지 않았다.

## 2026-09-28 추가 — 백필 자체가 잘못된 데이터를 넣은 6건 발견 (⚠️ DB 정리 미완료)

93개 백필이 완료된 뒤 `scripts/run_us_minervini_survivors.py`를 재실행해 커버리지가
실제로 개선됐는지 확인하는 과정에서, "성공적으로 채워졌다"고 표시된 티커 중 6개가
**전혀 다른 회사의 데이터**였다는 것을 발견했다. 원인: Tiingo가 이 6개 심볼에 대해
404나 "유효한 행 없음"이 아니라 **HTTP 200 + 그럴듯한 OHLCV**를 돌려줬는데, 그게
실제로는 그 티커를 재사용 중인 다른 현재 회사의 데이터였다 — 백필 스크립트의 기존
검증(404/무효데이터 체크)은 이런 "형식은 멀쩡한 오답"을 걸러내지 못한다.

| 티커 | 실제 역사적 회사 | 실제 상폐/개명일 | DB에 들어간(잘못된) 구간 |
|---|---|---|---|
| `CA` | CA Technologies | 2018-11-05(Broadcom 인수) | 2023-12-14 ~ 2026-09-25 |
| `CTRP` | Ctrip → Trip.com | 2019-11-05(티커를 TCOM으로 변경) | 2019-11-05 이후도 계속 (2026-09-25까지) |
| `SGEN` | Seagen Inc. | 2023-12-14(Pfizer 인수) | 2023-12-14 이후도 계속 (2026-09-25까지) |
| `SIVB` | SVB Financial Group | 2023-03-10(파산/FDIC 관리) | 2023-03-10 이후도 계속 (2026-09-25까지) |
| `SPLK` | Splunk Inc. | 2024-03-18(Cisco 인수) | 2024-03-18 이후도 계속 (2026-09-25까지) |
| `ANSS` | ANSYS, Inc. | 2025-07-17(Synopsys 인수) | 2025-07-17 이후도 계속 (2026-09-25까지) |

각 건은 근거 URL과 함께 `us_ticker_identity_conflict`에 `confirmed_different_entity`로
등록했고(총 17행), `research_outputs/us_delisted_backfill_state.json`에서도 이 6개를
`done`에서 빼고 `skip_permanent`로 옮겼다. **`scripts/backfill_us_delisted_prices.py`의
안전장치 덕분에 앞으로 이 6개는 다시 자동으로 채워지지 않는다.**

**✅ 해결(2026-09-28, 사용자 직접 실행)**: Claude Code 자동모드 권한 분류기가 이 DELETE를
"공유 리소스(운영 PostgreSQL) 수정"으로 판단해 Claude의 실행을 차단했으나(백업 포함
스크립트였음에도), 같은 스크립트를 사용자가 본인 터미널에서 직접 실행해 해결. 결과:
`us_price_history`에서 6개 티커 **28,490행 전부 삭제 확인**(잔여 0행), 백업 테이블
`us_price_history_deleted_wrong_ticker_20260928`에 동일 28,490행 보존, `data_fix_log`
run_id `remove_wrong_tiingo_c7c304c247d94598b68d57e898ae48df`로 기록. 실행 중
`data_fix_log_id_seq`가 또 어긋나는 사고가 있었으나(오늘 세 번째 재발, 알려진 이슈)
트랜잭션 전체가 롤백돼 데이터 손상 없이 안전했고, 시퀀스 보정 후 재실행으로 완료.

이 사고는 앞으로 같은 방식(Tiingo/다른 벤더로 "결측 티커" 자동 백필)을 또 돌릴 때
**"채워졌다"=완료가 아니라, 채워진 구간의 마지막 날짜가 그 회사의 실제 상폐일과
맞는지까지 확인해야 한다**는 교훈을 남긴다 — 이번 조사도 매출·시가 대조가 아니라
"오늘 날짜까지 데이터가 있다"는 단순한 이상 신호로 시작했다.

## 2026-09-28 추가 — Tiingo 메타데이터로 6건 원인 재분류

`/tiingo/daily/<ticker>` 메타데이터를 직접 조회한 결과, "다른 회사가 티커를 재사용"이
아니라 두 가지 다른 원인으로 갈렸다:

- `CA`: Tiingo 자체가 이 심볼을 **"XTRACKERS CALIFORNIA MUNICIPAL BOND ETF"**로 인식 — 주식이 아니라 채권 ETF. 순수 심볼 충돌.
- `SGEN`/`SPLK`/`ANSS`: Tiingo 메타데이터가 여전히 원래 회사명(Seagen Inc/Splunk Inc/Ansys Inc)을 그대로 쓰면서 `endDate=2026-09-25`(오늘)로 표시 — **다른 회사가 아니라 Tiingo가 실제 상폐를 반영하지 않고 델리스트된 종목에 계속 값을 만들어내는 벤더 측 데이터 품질 버그**.
- `CTRP`/`SIVB`: 메타데이터 엔드포인트는 404인데 `/prices`는 응답 — Tiingo 내부 경로 불일치.

원인이 무엇이든 결론(사용 금지)은 동일해서 조치는 바뀌지 않았다.

## 남은 작업

- `SPLS`: SEC의 현재 거래소 상장 티커 목록(company_tickers.json)에 없음 — OTC 전용 상장이거나 데이터 수집 과정의 오류일 가능성. Tiingo 메타데이터 엔드포인트로 재확인 필요(두 차례 시간당 한도로 조회 실패).
- 나머지 62개 "성공" 티커도 전수로 "마지막 날짜가 실제 상폐일과 맞는지" 검증하지 못했다 — 이번엔 의심스러운 패턴(마지막 날짜가 2025~2026년으로 오늘에 가까움)이 있는 것만 표본 확인했다. `DFS/HES/JNPR/K/MRO/PXD/SRCL/WBA/IPG`는 실제 인수종결일과 정확히 일치해 정상 확인됐고, `CTRA/DAY/VMRK`는 같은 회사의 개명·리테커 사례로 정상 판단했다(VMRK=Equity Residential이 Vivmark Residential로 개명, CTRA=Cabot Oil & Gas가 Coterra Energy로 합병). 나머지(`ALTR/ALXN/APOL/ATVI/CELG/CERN/CMA/CTLT/CTRX/CTXS/DISCA/DISCK/DISH/DRE/DTV/ESRX/FLIR/FWLT/GMCR/HOLX/IACI/JNPR(재확인용)/LEAP/LLTC/LMCA/LVLT/MXIM/MYL/NDOI/NIHD/NLSN/NUAN/PBCT/PDCO/PEAK/PETM/QRTEA/SEE/SHPG/SIAL/SRCL(재확인용)/STRZA/TWTR/VIAB/VMED/VMRK(재확인용)/WCRX/WRK/XLNX/YHOO`)는 검증하지 않았다.
