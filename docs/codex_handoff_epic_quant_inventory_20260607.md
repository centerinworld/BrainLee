# EPIC Quant Indicator Full Inventory and Source Check (2026-06-07)

## 목적
- EPIC 유료 서비스 중단 이후, DB에 남아 있는 EPIC형 퀀트지표 카탈로그를 기준으로 **현재 연결 상태**, **공개소스 존재 여부**, **다음 구현 순서**를 전부 정리합니다.
- 다른 AI가 바로 이어받을 수 있도록 전체 리스트와 소스 체크 결과를 남깁니다.

## 스냅샷
- `forward_strategy_indicators` raw rows: `93`
- 정규화된 현재 카탈로그: `80`개
- 상태 분포: ready_existing `5`, ready_existing_partial `2`, derivable_after_new_collector `1`, new_collector_needed `72`
- 전체 CSV: `/Applications/stock_dashboard/scratch/epic/quant_major_indicator_inventory_20260607.csv`
- 카탈로그 CSV(원본): `/Applications/stock_dashboard/scratch/epic/quant_major_indicator_catalog_20260607.csv`

## 상태 정의
- `ready_existing`: 이미 시계열 적재 완료
- `ready_existing_partial`: 일부 proxy/partial만 가능, exact는 미완료
- `derivable_after_new_collector`: 원천 수집기만 만들면 계산 가능
- `new_collector_needed`: 새 수집기 또는 새 권한/새 원천 필요

## 공개소스/원천 확인 결과 요약
- 자동차: 현대차 IR 공식 판매 엑셀은 **실수집 검증 완료**. 기아 공식 Sales Results/미국 판매 페이지도 존재 확인.
- 온라인쇼핑/소비: KOSIS `온라인쇼핑동향` 계열 공식 통계 존재 확인.
- 관광: 한국관광데이터랩/KTO 공식 통계 존재 확인.
- 전력: 전력거래소(KPX) `SMP` 공식 페이지/월간 리포트 존재 확인.
- 유가/리그: Baker Hughes Rig Count 공식 공개 확인.
- 농식품 가격: KAMIS 오픈 API 및 가격 페이지 존재 확인.
- 관세청 베트남 품목×국가: current key로 `nitemtrade` 계열 호출 시 `403` 확인. exact 연결은 권한 이슈 가능성 큼.
- 한국 후판가격, 중국 철강가격 exact 공개 무료원천은 아직 불충분/미확인.

## 우선 구현 순서 추천
1. `기아차 내수/미국 판매` 연결
2. `한국 자동차 판매: 회사별` + `시장 점유율` 계산
3. `KOSIS 온라인쇼핑` 계열 수집기
4. `KPX SMP` 수집기
5. `KTO 관광` 수집기
6. `KAMIS 돼지고기/수산` 수집기
7. `베트남 의류/IT`는 관세청 GW 권한 확인 후 exact 진행

## 확인한 공개소스 링크
- KAMA Monthly Statistics: https://www.kama.or.kr/BoardController?board_id=563&boardmaster_id=months_e&cmd=V&gubun=eng&menunum=0081
- Kia Sales Results: https://worldwide.kia.com/int/company/ir/archive/sales-results/
- KAIDA DB Service: https://kaida.co.kr/en/service/dbService.do
- KOSIS 경제상황판 / 온라인쇼핑 거래액: https://kosis.kr/visual/economyBoard/economyDash.do?lang=ko
- KTO / Korea Tourism Data Lab reference: https://www.oecd.org/content/dam/oecd/en/publications/reports/2025/06/using-alternative-data-sources-and-tools-to-measure-and-monitor-tourism-case-studies_3508b538/the-korea-tourism-data-lab_01ee60bf/1b634f3f-en.pdf
- KPX SMP page: https://new.kpx.or.kr/menu.es?mid=a10404080300
- KPX Monthly Market Report example: https://new.kpx.or.kr/boardDownload.es?bid=0057&list_no=56816OOOMay+09+Monthly+Market+Report&seq=23284
- Baker Hughes Rig Count: https://rigcount.bakerhughes.com/
- KAMIS Open API: https://www.kamis.or.kr/customer/reference/openapi_list.do?action=detail&boardno=1
- Public transport usage example dataset: https://www.data.go.kr/data/15128573/fileData.do?recommendDataYn=Y

## 카테고리 0: 자동차

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:0:1` | 글로벌 자동차 판매: 국가별 (월) | `new_collector_needed` | `autos_sales` | KAMA Monthly Statistics 공식 공개 확인. 국가별/회사별 범위는 협회 자료와 OEM 자료 조합 필요. | KAMA 수집기 구현 |
| `epic:0:2` | 한국 자동차 판매: 회사별 (월) | `new_collector_needed` | `autos_sales` | KAMA Monthly Statistics 공식 공개 확인. | KAMA 수집기 구현 |
| `epic:0:4` | 한국 자동차 시장 점유율: 회사별 (월) | `derivable_after_new_collector` | `autos_share` | 회사별 판매량 확보 시 시장점유율 자동 계산 가능. | 판매량 수집 후 계산 |
| `epic:0:14` | 현대차 내수 판매: 모델별 (월) | `ready_existing` | `autos_model_sales` | 현대차 IR 공식 엑셀에서 국내 모델별 판매량 수집 완료. | 기아/KGM로 확장 |
| `epic:0:17` | 기아차 내수 판매: 모델별 (월) | `new_collector_needed` | `autos_model_sales` | Kia Global Sales Results 공식 아카이브 존재. 월간 판매 리포트 공개 확인. | 기아 파서 구현 |
| `epic:0:112` | KG모빌리티 내수 판매: 모델별 (월) | `new_collector_needed` | `autos_model_sales` | 현대차는 공식 IR 엑셀로 연결 완료. 기아는 공식 Sales Results 아카이브 확인, KGM/기타는 OEM 공개경로 개별 확인 필요. | OEM별 모델판매 파서 확장 |
| `epic:0:113` | KG모빌리티 수출 판매: 모델별 (월) | `new_collector_needed` | `autos_model_exports` | KGM 수출 모델별 공개소스는 아직 개별 확인 필요. 회사 공시/보도자료 경로 탐색 필요. | KGM 전용 파서 탐색 |
| `epic:0:19` | KG모빌리티 내수 ∙ 수출 판매 (월) | `new_collector_needed` | `autos_sales` | KAMA 월간통계 + OEM IR/뉴스룸 사용 가능. 회사별 총판매/국가별 판매는 공식 자료 존재하나 파서 미구현. | KAMA/완성차 OEM 수집기 구현 |
| `epic:0:20` | 르노코리아 내수 ∙ 수출 판매 (월) | `new_collector_needed` | `autos_sales` | KAMA 월간통계 + OEM IR/뉴스룸 사용 가능. 회사별 총판매/국가별 판매는 공식 자료 존재하나 파서 미구현. | KAMA/완성차 OEM 수집기 구현 |
| `epic:0:21` | 한국GM 내수 ∙ 수출 판매 (월) | `new_collector_needed` | `autos_sales` | KAMA 월간통계 + OEM IR/뉴스룸 사용 가능. 회사별 총판매/국가별 판매는 공식 자료 존재하나 파서 미구현. | KAMA/완성차 OEM 수집기 구현 |
| `epic:0:55` | 현대차 미국 판매: 모델별 (월) | `ready_existing` | `autos_sales` | 현대차 IR 공식 엑셀에서 미국 모델별 판매량 수집 완료. | 기아 미국판매로 확장 |
| `epic:0:57` | 기아차 미국 판매: 모델별 (월) | `new_collector_needed` | `autos_sales` | Kia America 월간 판매 공식 페이지 존재. | 기아 미국판매 파서 구현 |

## 카테고리 1: 중국 철강/원자재

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:1:25` | China: Steel Price, HRC (일) | `new_collector_needed` | `steel_price` | 중국 철강가격은 Mysteel/Platts/SGX 등 상용 또는 부분공개 소스 위주. 한국 후판가격 exact 공개소스는 아직 미확인. | 무료/반공개 시황원 탐색 또는 상용소스 검토 |
| `epic:1:26` | China: Steel Price, CRC (일) | `new_collector_needed` | `steel_price` | 중국 철강가격은 Mysteel/Platts/SGX 등 상용 또는 부분공개 소스 위주. 한국 후판가격 exact 공개소스는 아직 미확인. | 무료/반공개 시황원 탐색 또는 상용소스 검토 |
| `epic:1:27` | China: Steel Price, Heavy Plate (일) | `new_collector_needed` | `steel_price` | 중국 철강가격은 Mysteel/Platts/SGX 등 상용 또는 부분공개 소스 위주. 한국 후판가격 exact 공개소스는 아직 미확인. | 무료/반공개 시황원 탐색 또는 상용소스 검토 |
| `epic:1:28` | China: Steel Price, G.I (일) | `new_collector_needed` | `steel_price` | 중국 철강가격은 Mysteel/Platts/SGX 등 상용 또는 부분공개 소스 위주. 한국 후판가격 exact 공개소스는 아직 미확인. | 무료/반공개 시황원 탐색 또는 상용소스 검토 |
| `epic:1:29` | China: Steel Price, Rebar (일) | `new_collector_needed` | `steel_price` | 중국 철강가격은 Mysteel/Platts/SGX 등 상용 또는 부분공개 소스 위주. 한국 후판가격 exact 공개소스는 아직 미확인. | 무료/반공개 시황원 탐색 또는 상용소스 검토 |
| `epic:1:30` | China: Steel Price, Wire Rod (일) | `new_collector_needed` | `steel_price` | 중국 철강가격은 Mysteel/Platts/SGX 등 상용 또는 부분공개 소스 위주. 한국 후판가격 exact 공개소스는 아직 미확인. | 무료/반공개 시황원 탐색 또는 상용소스 검토 |
| `epic:1:37` | China: Iron Ore Import Price (주) | `new_collector_needed` | `raw_material_price` | SGX 철광석 기준지수는 공식 존재. EPIC exact 시계열로 맞추려면 규격/정산기준 확인 필요. | SGX/대체시계열 규격 검증 |

## 카테고리 2: 온라인쇼핑/소비

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:2:22` | 인터넷 쇼핑 상품군별 판매액 (월) | `new_collector_needed` | `retail_stats` | KOSIS 온라인쇼핑동향 공식 통계 확인. | KOSIS 수집기 구현 |
| `epic:2:23` | 모바일 쇼핑 상품군별 판매액 (월) | `new_collector_needed` | `retail_stats` | KOSIS 온라인쇼핑동향 공식 통계 확인. | KOSIS 수집기 구현 |
| `epic:2:93` | 카드 결제액 추정치: 백화점 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:2:94` | 카드 결제액 추정치: 할인점, 슈퍼마켓 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:2:95` | 카드 결제액 추정치: 편의점 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:2:96` | 카드 결제액 추정치: 면세점 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:2:97` | 카드 결제액 추정치: 온라인 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:2:98` | 온라인 ∙ 모바일 쇼핑 거래액 (월) | `new_collector_needed` | `retail_stats` | KOSIS 온라인쇼핑 거래액 공식 통계 확인. | KOSIS 수집기 구현 |

## 카테고리 3: 관광/구독

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:3:34` | 모두투어 송출객 현황 (월) | `new_collector_needed` | `travel_demand` | 모두투어 송출객/패키지 실적은 기업 IR/공시/보도자료 경로 확인 필요. | 기업 IR 파서 구현 |
| `epic:3:36` | 모두투어 해외 패키지 송출객 (월) | `new_collector_needed` | `travel_demand` | 모두투어 송출객/패키지 실적은 기업 IR/공시/보도자료 경로 확인 필요. | 기업 IR 파서 구현 |
| `epic:3:70` | 한국 관광 방문자 추이 (월) | `new_collector_needed` | `tourism_stats` | KTO/한국관광데이터랩 공식 통계 확인. | KTO/KOSIS 수집기 구현 |
| `epic:3:71` | 한국 지역별 관광 방문자 추이 (월) | `new_collector_needed` | `tourism_stats` | KTO/한국관광데이터랩 지역별 방문 데이터 존재 확인. | KTO 지역 파서 구현 |
| `epic:3:97` | 영상 구독 서비스 지출건수 및 지출금액 (월) | `new_collector_needed` | `consumer_spending` | 해당 family에 대한 공식 source check를 추가 수행해야 함. | 추가 조사 |
| `epic:3:98` | 음원 구독 서비스 지출건수 및 지출금액 (월) | `new_collector_needed` | `consumer_spending` | 해당 family에 대한 공식 source check를 추가 수행해야 함. | 추가 조사 |

## 카테고리 4: 에너지(석탄)

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:4:96` | 동북아시아 유연탄 가격 (주) | `new_collector_needed` | `energy_price` | 동북아 유연탄 public exact 소스는 아직 미확인. | 공개 시황원 탐색 |

## 카테고리 6: 전력/SMP

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:6:18` | 계통한계가격(SMP): 일 가중평균 SMP (일) | `new_collector_needed` | `power_price` | 전력거래소 SMP 공식 페이지/월간 리포트 확인. | KPX 파서 구현 |

## 카테고리 7: 운송/리그/철도

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:7:14` | Freight Index: BDI(Baltic Dry Index) (일) | `new_collector_needed` | `shipping_index` | Baker Hughes Rig Count는 공식 공개. Baltic 계열(BDI/BCI/BPI/BSI)은 exact 무료 공개 여부 불확실. | rig count는 구현, Baltic은 대체/라이선스 검토 |
| `epic:7:15` | Freight Index: BCI(Baltic Capesize Index) (일) | `new_collector_needed` | `shipping_index` | Baker Hughes Rig Count는 공식 공개. Baltic 계열(BDI/BCI/BPI/BSI)은 exact 무료 공개 여부 불확실. | rig count는 구현, Baltic은 대체/라이선스 검토 |
| `epic:7:16` | Freight Index: BPI(Baltic Panamax Index) (일) | `new_collector_needed` | `shipping_index` | Baker Hughes Rig Count는 공식 공개. Baltic 계열(BDI/BCI/BPI/BSI)은 exact 무료 공개 여부 불확실. | rig count는 구현, Baltic은 대체/라이선스 검토 |
| `epic:7:17` | Freight Index: BSI(Baltic Supramax Index) (일) | `new_collector_needed` | `shipping_index` | Baker Hughes Rig Count는 공식 공개. Baltic 계열(BDI/BCI/BPI/BSI)은 exact 무료 공개 여부 불확실. | rig count는 구현, Baltic은 대체/라이선스 검토 |
| `epic:7:36` | 노선별 철도 여객인원 수 (월) | `new_collector_needed` | `transport_usage` | 공공데이터포털에 대중교통/노선별 이용현황 파일·API 존재. | 공공데이터 수집기 구현 |

## 카테고리 8: 화장품 소비

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:8:14` | 인터넷 쇼핑 상품군별 판매액 - 화장품 (월) | `new_collector_needed` | `retail_stats` | KOSIS 온라인쇼핑 화장품 거래액 계열 존재 가능성이 높음. | 세부 통계표 ID 확인 |
| `epic:8:15` | 모바일 쇼핑 상품군별 판매액 - 화장품 (월) | `new_collector_needed` | `retail_stats` | KOSIS 온라인쇼핑 화장품 거래액 계열 존재 가능성이 높음. | 세부 통계표 ID 확인 |

## 카테고리 9: 카지노/복합리조트

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:9:13` | Macao: Gross Revenue from Gaming (월) | `new_collector_needed` | `casino_revenue` | 마카오 GGR은 공식 정부 통계 존재. | 공식 통계 파서 구현 |
| `epic:9:18` | 파라다이스 월별 매출액 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:19` | GKL 월별 입장객 현황 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:20` | GKL 월별 매출액 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:21` | GKL 월별 드롭액 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:22` | GKL 월별 홀드율 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:23` | 파라다이스 월별 드롭액, 카지노별 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:24` | 파라다이스 월별 드롭액, 국적별 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:25` | 파라다이스 월별 홀드율 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:35` | 드림타워 카지노 월별 매출액 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:36` | 드림타워 카지노 월별 드롭액 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:37` | 드림타워 카지노 월별 입장객 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |
| `epic:9:38` | 드림타워 카지노 월별 홀드율 (월) | `new_collector_needed` | `company_monthly_kpi` | 파라다이스/GKL/드림타워 월지표는 기업 IR/보도자료에 존재 가능하나 자동추출 경로 미확정. | 기업별 IR 탐색/파서 구현 |

## 카테고리 10: IPTV

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:10:11` | IPTV 가입자 수 (월) | `new_collector_needed` | `subscriber_stats` | 해당 family에 대한 공식 source check를 추가 수행해야 함. | 추가 조사 |

## 카테고리 11: 식품/외식/수산

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:11:155` | 카드 결제액 추정치: 제과/커피/패스트푸드 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:11:156` | 카드 결제액 추정치: 외식 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:11:69` | 가다랑어 어가추이 (월) | `new_collector_needed` | `food_price` | KAMIS Open API/가격 페이지 존재. 돼지고기 등은 공식 가격 수집 가능. | KAMIS 파서 구현 |
| `epic:11:105` | 한국 돼지고기 도매가격 (일) | `new_collector_needed` | `food_price` | KAMIS Open API/가격 페이지 존재. 돼지고기 등은 공식 가격 수집 가능. | KAMIS 파서 구현 |

## 카테고리 12: 패션/의류

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:12:5` | 인터넷 쇼핑 의류, 패션 관련 판매액 (월) | `new_collector_needed` | `retail_stats` | KOSIS/통계청 온라인쇼핑동향 계열 공개 통계 존재. 대시보드/통계표는 확인했으나 API 키/통계표ID 확정 필요. | KOSIS 수집기 구현 |
| `epic:12:6` | 모바일 쇼핑 의류, 패션 관련 판매액 (월) | `new_collector_needed` | `retail_stats` | KOSIS/통계청 온라인쇼핑동향 계열 공개 통계 존재. 대시보드/통계표는 확인했으나 API 키/통계표ID 확정 필요. | KOSIS 수집기 구현 |
| `epic:12:10` | 베트남 의류, 신발 수출 금액 (월) | `ready_existing_partial` | `customs_trade` | 관세청 customs 인프라는 존재하나 품목×국가 exact API는 현재 키로 403. HS 바스켓 정의도 필요. | GW 권한 확인 후 구현 |

## 카테고리 13: 교육

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:13:20` | 카드 결제액 추정치: 학원 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:13:21` | 카드 결제액 추정치: 유아교육 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:13:22` | 카드 결제액 추정치: 교육용품 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |

## 카테고리 15: IT 수출

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:15:11` | 베트남 IT제품 수출 금액 (월) | `ready_existing_partial` | `customs_trade` | 관세청 customs 인프라는 존재하나 품목×국가 exact API는 현재 키로 403. HS 바스켓 정의 필요. | GW 권한 확인 후 구현 |

## 카테고리 16: 미용/약국

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:16:110` | 카드 결제액 추정치: 피부과 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:16:111` | 카드 결제액 추정치: 성형외과 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:16:112` | 카드 결제액 추정치: 치과 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |
| `epic:16:113` | 카드 결제액 추정치: 약국 (월) | `new_collector_needed` | `card_spending` | 카드결제액 추정치는 카드사/민간 빅데이터 성격이 강함. exact 공개 원천은 아직 미확인. | 대체 프록시(KOSIS 소매/온라인쇼핑) 또는 민간데이터 검토 |

## 카테고리 17: 조선/후판

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:17:17` | 한국 후판가격 (주) | `new_collector_needed` | `steel_price` | 한국 후판가격 exact 공개소스는 아직 확보 못함. | 무료 시황원/협회자료 추가 조사 |

## 카테고리 19: 오일/리그/재고

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:19:50` | United States Rig Count (주) | `new_collector_needed` | `commodity_or_industry_price` | 해당 family에 대한 공식 source check를 추가 수행해야 함. | 추가 조사 |
| `epic:19:51` | Canada Rig Count (주) | `new_collector_needed` | `commodity_or_industry_price` | 해당 family에 대한 공식 source check를 추가 수행해야 함. | 추가 조사 |
| `epic:19:104` | 싱가포르 석유제품 재고 추이 (주) | `new_collector_needed` | `commodity_or_industry_price` | 해당 family에 대한 공식 source check를 추가 수행해야 함. | 추가 조사 |

## 카테고리 20: 시장 유동성

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:20:1` | 한국은행 기준금리 (월) | `ready_existing` | `macro_rate` | ECOS 공식 API로 수집 중. | 유지 |
| `epic:20:22` | 국내 주식시장 대차잔고 (월) | `ready_existing` | `short_balance` | KRX/시장데이터 조합으로 월말 대차잔고 금액/주수 계산 적재 중. | 유지 |
| `epic:20:99` | 국내 주식시장 투자자 예탁금, 신용공여 추이 (월) | `ready_existing` | `macro_liquidity` | ECOS 공식 API로 투자자 예탁금/신용공여 수집 중. | 유지 |

## 카테고리 22: 교통/대중교통

| key | 지표명 | 상태 | 소스 family | 확인결과 | 다음 작업 |
|---|---|---|---|---|---|
| `epic:22:9` | 대중교통 이용현황 (월) | `new_collector_needed` | `transport_usage` | 공공데이터포털에 대중교통 이용현황 요약/노선별 이용현황 데이터 존재. | 공공데이터 수집기 구현 |
| `epic:22:10` | 지하철 노선별 이용현황 (월) | `new_collector_needed` | `transport_usage` | 공공데이터포털에 노선별 이용현황 데이터 존재. | 공공데이터 수집기 구현 |

## 메모
- `현대차 내수 판매: 모델별`, `현대차 미국 판매: 모델별`은 이미 `/Applications/stock_dashboard/scripts/ops/sync_quant_major_indicators.py` 에 연결 완료.
- `베트남 의류/IT 수출`은 customs infra는 있으나 exact API 권한 문제(`403`)를 먼저 풀어야 함.
- 카지노/카드결제/철강시황 일부는 공개 원천이 있더라도 exactness와 지속성이 낮을 수 있으므로, 구현 전에 원천 검증을 한 번 더 해야 함.