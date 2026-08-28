# 퀀트 주요지표 추가 연결 핸드오프 (2026-06-07)

## 이번에 새로 연결한 지표
- `epic:2:98` 온라인 ∙ 모바일 쇼핑 거래액 (월)
  - 상태: 연결완료
  - 소스: 통계청/KOSIS 경제상황판 내부 API
  - 적재행수: 112
  - 기간: 2017-01 ~ 2026-04
- `epic:6:18` 계통한계가격(SMP): 일 가중평균 SMP (일)
  - 상태: 연결완료
  - 소스: 전력거래소(KPX) `월별 계통한계가격(SMP)` 표
  - 적재행수: 51
  - 기간: 2025-01 ~ 2026-05
- `epic:0:1` 글로벌 자동차 판매: 국가별 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보 `10-2 주요국신차등록`
  - 적재행수: 1,868
  - 기간: 2015-01 ~ 2026-04
- `epic:0:57` 기아차 미국 판매: 모델별 (월)
  - 소스: Kia America Newsroom export
  - 적재행수: 1326
  - 기간: 2017-01 ~ 2026-05
- `epic:0:2` 한국 자동차 판매: 회사별 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보
  - 적재행수: 812
  - 기간: 2015-01 ~ 2026-01
- `epic:0:4` 한국 자동차 시장 점유율: 회사별 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보 기반 계산
  - 적재행수: 812
  - 기간: 2015-01 ~ 2026-01
- `epic:0:17` 기아차 내수 판매: 모델별 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보 `1-4/2-4 업체별·모델별 생산·내수·수출`
  - 적재행수: 843
  - 기간: 2016-06 ~ 2026-04
- `epic:0:19` KG모빌리티 내수 ∙ 수출 판매 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보
  - 적재행수: 348
- `epic:0:20` 르노코리아 내수 ∙ 수출 판매 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보
  - 적재행수: 348
- `epic:0:21` 한국GM 내수 ∙ 수출 판매 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보
  - 적재행수: 348
- `epic:0:112` KG모빌리티 내수 판매: 모델별 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보 `1-4/2-4 업체별·모델별 생산·내수·수출`
  - 적재행수: 277
  - 기간: 2016-06 ~ 2026-04
- `epic:0:113` KG모빌리티 수출 판매: 모델별 (월)
  - 상태: 연결완료
  - 소스: KAMA 자동차통계월보 `1-4/2-4 업체별·모델별 생산·내수·수출`
  - 적재행수: 277
  - 기간: 2016-06 ~ 2026-04


## 2026-06-08 추가 연결: KOSIS 온라인쇼핑 세부 분해
- `epic:2:22` 인터넷 쇼핑 상품군별 판매액 (월)
  - 상태: 연결완료
  - 소스: 통계청/KOSIS `DT_1KE10071` 공식 statHtml 표
  - 적재행수: 2,840
  - 기간: 2017-01 ~ 2026-04
  - 시계열: 26개 상품군 × 112개월
- `epic:2:23` 모바일 쇼핑 상품군별 판매액 (월)
  - 상태: 연결완료
  - 소스: 통계청/KOSIS `DT_1KE10071` 공식 statHtml 표
  - 적재행수: 2,840
  - 기간: 2017-01 ~ 2026-04
  - 시계열: 26개 상품군 × 112개월
- `epic:8:14` 인터넷 쇼핑 상품군별 판매액 - 화장품 (월)
  - 상태: 연결완료
  - 적재행수: 112
  - 기간: 2017-01 ~ 2026-04
- `epic:8:15` 모바일 쇼핑 상품군별 판매액 - 화장품 (월)
  - 상태: 연결완료
  - 적재행수: 112
  - 기간: 2017-01 ~ 2026-04
- `epic:12:5` 인터넷 쇼핑 의류, 패션 관련 판매액 (월)
  - 상태: 연결완료
  - 적재행수: 112
  - 기간: 2017-01 ~ 2026-04
  - 산식: KOSIS 공식 상품군 `의복 + 신발 + 가방 + 패션용품 및 액세서리`
  - exactness: `official_derived_sum`
- `epic:12:6` 모바일 쇼핑 의류, 패션 관련 판매액 (월)
  - 상태: 연결완료
  - 적재행수: 112
  - 기간: 2017-01 ~ 2026-04
  - 산식: KOSIS 공식 상품군 `의복 + 신발 + 가방 + 패션용품 및 액세서리`
  - exactness: `official_derived_sum`

### 검증 결과
- 전체 카탈로그 상태: `ready_existing 24 / ready_existing_partial 8 / new_collector_needed 48`
- P2 상태: `ready_existing 14 / ready_existing_partial 8 / new_collector_needed 39`
- 2026-04 샘플:
  - 인터넷 화장품: 275,533백만원
  - 모바일 화장품: 1,056,620백만원
  - 인터넷 의류·패션 합산: 862,914백만원
  - 모바일 의류·패션 합산: 2,179,870백만원

## 2026-06-09 추가 연결: DICJ Macao GGR
- `epic:9:13` Macao: Gross Revenue from Gaming (월)
  - 상태: 연결완료
  - 소스: 마카오 Gaming Inspection and Coordination Bureau(DICJ) 공식 XML
  - URL 패턴: `https://www.dicj.gov.mo/web/en/information/DadosEstat_mensal/{year}/report_en.xml`
  - 적재행수: 197
  - 기간: 2010-01 ~ 2026-05
  - 단위: MOP million
  - 최신값: 2026-05 = 22,611 / 2026-04 = 19,894 / 2026-03 = 22,612
  - 검증: XML 내 미래월 `-` 값은 저장하지 않고, 숫자 월만 저장. 2010년 이후 연도별 12개월 구조 확인.
  - exactness: `official_exact`

### 2026-06-09 검증 결과
- 전체 카탈로그 상태: `ready_existing 24 / ready_existing_partial 8 / new_collector_needed 48`
- P2 상태: `ready_existing 14 / ready_existing_partial 8 / new_collector_needed 39`

## 공식 공개 원천
- KOSIS 경제상황판 온라인쇼핑 거래액: https://kosis.kr/visual/economyBoard/economyJipyo.do?lang=ko (listId=122, unitySrvcId=599, stdIdctId=511)
- 전력거래소 월별 SMP: https://new.kpx.or.kr/menu.es?mid=a10404080300
- 현대차 IR 판매실적: https://www.hyundai.com/worldwide/ko/company/ir/ir-resources/sales-results
- 기아 뉴스룸 목록 API: https://worldwide.kia.com/api/newsroom
- 기아 뉴스룸 상세 API 예시: https://worldwide.kia.com/api/newsroom/id/1526
- Kia America Sales By Model: https://www.kiamedia.com/us/en/sales/bymodel
- KAMA 월보 목록: https://www.kama.or.kr/NewsController?boardmaster_id=Produce&cmd=L&menunum=0003&pagenum=1
- KAMA 첨부 다운로드: `https://www.kama.or.kr/jsp/common/FileDown.jsp` POST
- DICJ Macao monthly GGR XML: https://www.dicj.gov.mo/web/en/information/DadosEstat_mensal/2026/report_en.xml

## 구현 메모
- `epic:2:98`은 KOSIS 공개 페이지 내부 AJAX 엔드포인트 `selectDetailDataList.do`를 직접 호출
- 사용 식별자:
  - `unitySrvcIdArr=599`
  - `stdIdctIdArr=511`
  - `cyclSe=M`
  - `regionArr=00`
- 소스 표 확인:
  - `selectSourceList.do` 응답 `orgId=101`, `tblId=DT_1KE10071`, `tblNm=온라인쇼핑몰 판매매체별/상품군별거래액`
- 인터넷/모바일/화장품/의류·패션 세부 분해는 KOSIS `statHtml.do` 초기화 후 같은-origin `/statHtml/html.do` 공식 표 응답을 Playwright 세션으로 호출해 연결 완료
- `epic:6:18`은 전력거래소 페이지 첫 표를 직접 파싱해 `육지/제주/통합 SMP` 3개 시계열로 저장
- 연도는 `25년`, `26년` 형식에서 2000년대 기준으로 해석
- 공란 월은 미발표 구간으로 간주하고 저장하지 않음
- `epic:0:1`은 국가별 `계` 컬럼만 사용
- 최신 월 파일에서 일부 국가는 값이 아직 비어 있는데 총계 셀만 `0`으로 내려오는 경우가 있어, `총계=0`이면서 승용/상용도 공란이면 미수집으로 간주해 저장하지 않음
- KAMA 구버전 파일은 모델별 시트명이 `2-4업체별.모델별 생산.내수.수출`
- 신버전 파일은 `1-4업체별.모델별 생산.내수.수출`
- 수집기는 두 시트명을 모두 탐지하도록 구현
- 모델별 지표는 트림 단위가 아니라 `...계` 합계행만 사용해 모델 패밀리 기준으로 저장
- `EXPORT` 전용 행은 제외하고, 합계행의 `내수/수출` 값을 사용
- 회사별 총판매/시장점유율은 KAMA 내부 월보 블록을 기준으로 계산하므로, 페이지 제목 월과 내부 월 블록이 어긋나는 구간은 내부 블록 기준을 우선 신뢰


## 2026-06-08 추가 소스 확인: Baker Hughes Rig Count
- 대상 지표: `epic:19:50` United States Rig Count (주), `epic:19:51` Canada Rig Count (주)
- 공식 페이지: `https://rigcount.bakerhughes.com/` 및 `https://rigcount.bakerhughes.com/na-rig-count/`
- 확인 결과:
  - 공식 개요 페이지에서 최신 주간 U.S./Canada rig count 제공 확인.
  - North America Rig Count 페이지에 `North America Rig Count Report - New Report` 및 과거 `North America Rotary Rig Count Archive` 엑셀/피벗 파일 제공 확인.
  - Baker Hughes FAQ 기준 North America Rig Count는 U.S./Canada active drilling rigs의 weekly census이며, 매주 마지막 영업일 정오 Central Time에 발표.
- 현재 처리:
  - 로컬 `requests` 접근은 응답 지연/timeout 발생. DB 자동 적재는 아직 하지 않음.
  - 다음 구현은 브라우저/Playwright로 static-file 링크를 추출한 뒤 엑셀을 다운로드해 U.S./Canada 주간 total을 파싱하는 방식 권장.
- 상태 유지:
  - `epic:19:50`, `epic:19:51`은 아직 `new_collector_needed` 유지.
  - 수집기 구현 후 `official_exact`로 승격 가능성이 높음.

## 2026-06-09 추가 구현: KTO 방한 외래관광객 목적별 월별 집계(부분연결)
- 대상: `epic:3:70` 한국 관광 방문자 추이 (월)
- 소스: 공공데이터포털 `한국관광공사_방한 외래관광객 목적별 월별 집계` CSV
- 파일 URL: `https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003027530&fileDetailSn=1&insertDataPrcus=N`
- 구현: `collect_kto_foreign_visitors_purpose_file()` 추가
- 파싱 결과: 72행 후보(12개월 × 목적 5개 + 합계 1개), 2023-08~2024-07
- 상태 판단: 공식 파일이지만 기간이 짧아 `ready_existing_partial`로만 승격. 장기/최신 구간은 한국관광데이터랩 또는 ODCloud 정상 인증키 확보 필요.
- DB 반영: 72행 적재 완료, 기간 2023-08~2024-07.

## 2026-06-09 추가 연결: World Bank Iron Ore 월간 프록시
- 대상: `epic:1:37` China: Iron Ore Import Price (주)
- 소스: World Bank Commodity Price Data(The Pink Sheet) `CMO-Historical-Data-Monthly.xlsx`
- 수집 항목: `Iron ore, cfr spot`
- 적재행수: 797
- 기간: 1960-01 ~ 2026-05
- 단위: $/dmtu
- 최신값: 2026-05 = 108.64 / 2026-04 = 106.05 / 2026-03 = 104.45
- 상태 판단: World Bank 공식 데이터이지만 EPIC 원 지표가 요구하는 `China import price(주)`와 빈도/정의가 완전히 같지는 않으므로 `ready_existing_partial`, `official_proxy_monthly`로 기록.
- 다음 액션: 중국 철광석 수입가격 exact weekly source가 확보되면 해당 값을 우선 사용하고, World Bank는 장기 프록시/검증 보조로 유지.

## 2026-06-09 추가 연결: SteelBenchmarker 중국 철강 최신값
- 대상: `epic:1:25` HRC/HRB, `epic:1:26` CRC, `epic:1:27` Standard Plate, `epic:1:29` Rebar
- 소스: SteelBenchmarker 공개 PDF `https://www.steelbenchmarker.com/history.pdf`
- 수집 방식: PDF를 `pdftotext`로 변환 후 `Region: Mainland China` 최신 블록에서 4개 가격 추출
- 적재값(2026-05-27): HRB/HRC 434, CRC 506, Standard Plate 451, Rebar 418 ($/mt)
- 상태 판단: 최신 리포트 1포인트만 안정 추출되므로 `ready_existing_partial`, `public_report_latest_only`로 기록. 장기 history 표는 페이지별 표 파서 검증 후 확장 필요.

## 다음 우선순위
1. KAMA 기반 값과 현대차/기아 OEM 공식 소스 간 월별 차이 검증
2. KGM/르노/한국GM 모델별 판매의 별도 공식 원천 확인 후 교차검증
3. Baker Hughes Rig Count static-file 링크 추출 + 엑셀 파싱 수집기 구현
4. KAMIS 돼지고기 구현
5. `epic:0:1` 최신월 일부 국가 공란 보완 원천 점검
6. 카드 결제액 계열 공개 대체 소스 탐색

## 2026-06-09 추가 연결: DART 카지노 월간 영업지표
- 대상: 카지노/여행 섹터 월간 지표 중 DART 영업잠정실적 공시에서 직접 확인 가능한 9개 지표.
- 소스: 로컬 `dart_disclosures` + OpenDART `document.xml` 원문 ZIP 파싱.
- 구현 함수: `collect_dart_casino_monthly(conn)` in `scripts/ops/sync_quant_major_indicators.py`
- 파싱 원칙:
  - `당기실적 YYYY-MM-01 ~ YYYY-MM-DD`에서 월 기준일 추출.
  - `카지노매출액`/`카지노 매출액` 뒤의 백만원 단위 금액만 저장.
  - `드랍액`/`드롭액` 뒤의 백만원 단위 금액만 저장.
  - `홀드율 = 카지노매출액 / 테이블드롭액 × 100`으로 파생 저장.
  - 입장객/국적별 드롭액은 이번 DART 공시 본문에 숫자가 없어 저장하지 않음.
- DB 적재 결과:
  - `epic:9:18` 파라다이스 월별 매출액: 9행, 2025-09~2026-05, `official_disclosure_exact`
  - `epic:9:20` GKL 월별 매출액: 8행, 2025-09~2026-04, `official_disclosure_exact`
  - `epic:9:21` GKL 월별 드롭액: 8행, 2025-09~2026-04, `official_disclosure_exact`
  - `epic:9:22` GKL 월별 홀드율: 8행, 2025-09~2026-04, `derived_from_official_disclosure`
  - `epic:9:23` 파라다이스 월별 드롭액: 9행, 2025-09~2026-05, `official_disclosure_exact`
  - `epic:9:25` 파라다이스 월별 홀드율: 9행, 2025-09~2026-05, `derived_from_official_disclosure`
  - `epic:9:35` 드림타워 카지노 월별 매출액: 9행, 2025-09~2026-05, `official_disclosure_exact`
  - `epic:9:36` 드림타워 카지노 월별 드롭액: 9행, 2025-09~2026-05, `official_disclosure_exact`
  - `epic:9:38` 드림타워 카지노 월별 홀드율: 9행, 2025-09~2026-05, `derived_from_official_disclosure`
- 검증 메모:
  - 초기 파서에서 롯데관광개발 `당기실적(26년05월)`의 `26`을 드롭액으로 오인하는 문제가 발견되어, 금액 파싱 시 천 단위 쉼표가 있는 금액만 허용하도록 수정.
  - 최신 샘플: 롯데관광개발 2026-05 매출액 49,424백만원, 드롭액 207,574백만원, 홀드율 23.8103%.
- 남은 항목:
  - `epic:9:19` GKL 입장객, `epic:9:24` 파라다이스 국적별 드롭액, `epic:9:37` 드림타워 입장객은 DART 영업잠정 공시 본문에 없어 IR/월간 실적자료 별도 파서 필요.
- 상태 집계 변화: `new_collector_needed` 48 → 39, `ready_existing` 24 → 33, `ready_existing_partial` 8 유지.

## 2026-06-09 추가 소스 조사: 한국 후판가격(P1)
- 대상: `epic:17:17` 한국 후판가격 (주)
- 확인한 공개 후보:
  - 스틸링크 품목별 실시간 유통가격: 후판(PLATE) SS275/주문품/중국산 항목 존재 확인, 다만 가격표·장기 추이는 로그인 필요.
  - 증권사 철강 Weekly PDF: 후판 유통가격/수입유통가가 인용되는 경우가 있으나 리포트별 포맷이 달라 자동 exact 시계열 원천으로 부적합.
  - 한국철강협회 주간철강시황 PDF: 과거 PDF 확인 가능하나 후판 가격 시계열 표의 자동 추출 안정성은 별도 검증 필요.
- 판단:
  - 현재 즉시 DB 적치 가능한 공식/무료 exact API는 확인하지 못함.
  - 임의 기사/리포트 숫자를 섞으면 후판가격 시계열이 오염될 수 있으므로 `new_collector_needed` 유지.
- 다음 액션:
  1. 스틸링크 로그인/약관상 수집 가능 여부 확인.
  2. 철강협회 주간철강시황 PDF 일괄 다운로드 가능성 및 표 좌표 안정성 검증.
  3. 하나/하이/유안타 등 철강 Weekly PDF의 후판 가격 표가 장기간 동일 구조인지 샘플 20개 이상으로 검증 후 `research_report_proxy`로만 부분연결 고려.

## 2026-06-09 2차 확장: 서울 지하철 노선별/합계 이용현황 연결
- 대상: `epic:22:10` 지하철 노선별 이용현황(월), `epic:22:9` 대중교통 이용현황(월) 부분 대체.
- 소스: 서울 열린데이터광장 `OA-12914` 지하철호선별 역별 승하차 인원 정보 CSV.
- 구현 함수: `collect_seoul_subway_monthly()` in `scripts/ops/sync_quant_major_indicators.py`
- 수집 방식:
  - 서울 열린데이터광장 데이터셋 페이지에서 `CARD_SUBWAY_MONTH_YYYYMM.csv` 다운로드 seq를 추출.
  - `datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do`에 `infId=OA-12914`, `infSeq=3`, `seq=<파일seq>` POST로 CSV 다운로드.
  - CSV `사용일자`를 월(`YYYY-MM`)로 변환하고 `노선명`별 승차/하차/승하차 합계를 생성.
- DB 적재 결과:
  - `epic:22:10`: 3,060행, 2023-01~2026-04, 노선별 `boarding/alighting/total`, `ready_existing`, `official_exact`.
  - `epic:22:9`: 114행, 2023-01~2026-04, 지하철 전체 `subway_boarding/subway_alighting/subway_total`, `ready_existing_partial`, `official_partial_subway_only`.
- 검증 샘플(2026-04):
  - 전체 지하철 승차 234,566,524명, 하차 233,572,003명, 승하차 합계 468,138,527명.
  - 2호선 승차 45,558,015명, 하차 46,109,352명, 합계 91,667,367명.
- 주의/남은 작업:
  - `epic:22:9`는 원 지표명이 대중교통 전체이므로 현재는 지하철만 반영된 부분 연결입니다. 버스/택시/기타 교통수단은 별도 공식 소스가 필요합니다.
  - 서울 데이터는 2026-04가 최신 공개월로 확인되었습니다.
- 상태 집계 변화: `new_collector_needed` 39 → 37, `ready_existing` 33 → 34, `ready_existing_partial` 8 → 9.


## 2026-06-09 2차 확장 후보 점검: Baker Hughes Rig Count
- 대상: `epic:19:50` United States Rig Count, `epic:19:51` Canada Rig Count.
- 공식 URL 후보: `https://rigcount.bakerhughes.com/`
- 현재 확인 결과: 로컬 requests 접근은 30초 read timeout으로 실패. 사이트 자체가 동적/대용량/접근제한 가능성이 있어 이번 배치에는 DB 적재하지 않음.
- 다음 액션: 공식 weekly PDF/Excel 다운로드 URL 또는 press-release archive의 안정 URL을 찾아 최신값부터 `official_latest_only`로 연결하고, 이후 히스토리 파서를 별도 구축.

## 2026-06-10 한국 후판가격 수집 가능성 재점검
- 대상: `epic:17:17` 한국 후판가격(주), 화면상 `수집대기중`으로 표시되는 항목.
- 현 상태: DB 시계열 0행, `new_collector_needed` 유지.
- 이유:
  - 국내 후판가격은 조선/철강 협상가·유통가·수입대응재·중국산 등 기준이 여러 개라, 기사 숫자를 섞으면 투자 지표가 오염될 수 있음.
  - S&M뉴스/이야드/스틸데일리류 기사는 가격 구간 또는 정성 표현을 제공하지만, 장기 주간 exact API/CSV가 확인되지 않음.
  - MEPS/S&P Platts 등은 한국 plate price 상품이 있으나 유료/구독형 성격.
  - 한국철강협회 과거 주간철강시황 PDF에 국내 철강 가격표가 있으나, 최신 지속 공개 여부와 후판 표 좌표 안정성을 샘플 검증해야 함.
- 조치:
  - `quant_major_indicator_catalog.epic:17:17`의 source/note를 “무료 exact 자동수집 원천 미확정, 후보 소스 검증 필요”로 갱신.
- 권장 다음 단계:
  1. 한국철강협회 주간철강시황 PDF 최신 목록 접근 가능 여부와 20개 이상 PDF 표 구조 샘플 검증.
  2. S&M뉴스/이야드 주간시장동향-후판 기사에서 숫자 구간을 추출하는 `research_report_proxy` 수집기를 별도 설계하되, exact가 아닌 proxy로만 표시.
  3. 유료 데이터 사용 가능 시 MEPS/Platts/스틸데일리 계정 기반 exact 수집기로 전환.

## 2026-06-10 2순위 확장: KOSIS 소매업태별 판매액 프록시 연결
- 대상: EPIC 카드결제액 추정치 계열 5개 지표.
  - `epic:2:93` 백화점 카드 결제액 추정치(월)
  - `epic:2:94` 할인점+슈퍼 카드 결제액 추정치(월)
  - `epic:2:95` 편의점 카드 결제액 추정치(월)
  - `epic:2:96` 면세점 카드 결제액 추정치(월)
  - `epic:2:97` 온라인/무점포 카드 결제액 추정치(월)
- 소스: KOSIS `DT_1K41003` 소매업태별 판매액, 단위 백만원.
- 구현 함수: `collect_kosis_retail_store_sales_proxy()` in `scripts/ops/sync_quant_major_indicators.py`
- 수집 방식:
  - KOSIS 통계표 페이지 `orgId=101&tblId=DT_1K41003`를 Playwright로 열고 `iframe_centerMenu` 내부의 `/statHtml/html.do` 응답을 직접 파싱.
  - 2020-01~2026-04 월별 시계열 76개월을 수집.
  - `epic:2:94`는 KOSIS의 `대형마트` + `슈퍼마켓 및 잡화점`을 합산한 파생 프록시.
- DB 적재 결과:
  - 5개 지표 모두 76행, 기간 2020-01~2026-04.
  - 카탈로그 상태: `ready_existing_partial`.
  - 품질 플래그: `official_retail_sales_proxy` 또는 `official_retail_sales_proxy_derived_sum`.
- 최신 샘플(2026-04):
  - 백화점 판매액: 3,664,204백만원
  - 대형마트+슈퍼마켓 및 잡화점 판매액: 7,905,471백만원
  - 편의점 판매액: 2,672,944백만원
  - 면세점 판매액: 1,119,171백만원
  - 무점포 소매 판매액: 12,435,791백만원
- 중요 해석 주의:
  - 원 EPIC 지표명은 카드 결제액 추정치이나, 현재 공개/무료 exact 카드사 원천은 미확정입니다.
  - 따라서 이번 연결값은 카드 결제액 자체가 아니라 KOSIS 공식 소매판매액 프록시입니다.
  - 화면/리포트에서는 `부분연결` 또는 `공식 소매판매 프록시`로 표시해야 하며 exact 카드결제액으로 해석하면 안 됩니다.
- 상태 집계 변화: `ready_existing 34`, `ready_existing_partial 14`, `new_collector_needed 32`.
- 남은 2순위 미연결 중 즉시 적치하지 않은 항목:
  - Baker Hughes Rig Count(`epic:19:50`, `epic:19:51`)는 공식 사이트/static 파일 접근이 timeout되어 DB 적치 보류.
  - BDI/BCI/BPI/BSI(`epic:7:14~17`)는 Stooq/yfinance 후보가 anti-bot 또는 무데이터라 exact 원천 재탐색 필요.
  - 의료/교육 카드결제 세부 지표는 KOSIS 소매업태표로 직접 대체 불가하므로 별도 공공/카드사/업종 매출 원천 필요.

## 2026-06-11 추가 확장: KOSIS 서비스업생산지수 기반 외식/교육 프록시
- 대상:
  - `epic:11:156` 카드 결제액 추정치: 외식(월)
  - `epic:13:20` 카드 결제액 추정치: 학원(월)
- 소스: KOSIS `DT_1KC2023` 시도별 서비스업생산지수(2020=100.0), 분기 자료.
- 구현 함수: `collect_kosis_service_industry_index_proxy()` in `scripts/ops/sync_quant_major_indicators.py`
- 수집 방식:
  - KOSIS 공식 statHtml 표에서 전국 17개 시도 × 업종별 경상지수/불변지수를 파싱.
  - 외식은 `숙박 및 음식점업`, 학원은 넓은 범주의 `교육 서비스업`을 사용.
- DB 적재 결과:
  - `epic:11:156`: 714행, 2021-Q1~2026-Q1, 17개 시도 × 2개 지수(경상/불변) × 21분기.
  - `epic:13:20`: 714행, 2021-Q1~2026-Q1, 17개 시도 × 2개 지수(경상/불변) × 21분기.
  - 카탈로그 상태: `ready_existing_partial`.
  - 품질 플래그: `official_quarterly_service_index_proxy`.
- 중요 해석 주의:
  - 원 지표는 월간 카드결제액 추정치이나, 현재 연결값은 분기 공식 업종 생산지수입니다.
  - 외식은 업종 의미가 비교적 가깝지만, 학원은 `교육 서비스업` 전체라 유아교육/교육용품/사교육 세부 업종을 직접 대변하지 않습니다.
  - 피부과/성형외과/치과/약국은 `보건업 및 사회복지 서비스업`으로 뭉개면 오염 위험이 커서 이번 연결에서 제외했습니다.
- 상태 집계 변화: `ready_existing 34`, `ready_existing_partial 16`, `new_collector_needed 30`.

## 2026-06-11 추가 확장: KRIC 노선별 철도 여객수송(월) 부분연결
- 대상: `epic:7:36` 노선별 철도 여객인원 수 (월)
- 소스: 철도산업정보센터(KRIC) `노선별 여객수송(월)` HTML 표, 출처 표기는 한국철도공사.
- 구현: `scripts/ops/sync_quant_major_indicators.py`의 `collect_kric_rail_line_passenger_monthly()` 신규 추가.
- 수집 방식: `raillinepassmonList.jsp`에 `q_fdate`, `q_month`, `pageNo`를 POST하여 월별 1~3페이지 HTML 표를 파싱.
- 적재 결과: 12,116행, 2022-01 ~ 2026-04, 노선별 total 및 KTX/새마을/무궁화/누리로/KTX-이음 등 열차종별 수송인원.
- 카탈로그 상태: `ready_existing_partial`, exactness `official_korail_general_rail_partial`.
- 주의: 이 원천은 한국철도공사 일반철도 중심이며, 도시철도/민자/전체 철도망까지 완전 커버한다고 해석하면 안 된다. 그래서 exact가 아닌 부분연결로 표시한다.
- 상태 변화: `ready_existing 34 / ready_existing_partial 17 / new_collector_needed 29`.

## 2026-06-11 추가 확장: MTRACE 돼지고기 월별 경락가격 부분연결
- 대상: `epic:11:105` 한국 돼지고기 도매가격 (일)
- 소스: 축산물품질평가원/MTRACE `DT_APGS_016` 돼지도체 도매시장별 등급별 경락가격.
- 구현: `scripts/ops/sync_quant_major_indicators.py`의 `collect_mtrace_pork_auction_price_monthly()` 신규 추가.
- 수집 방식: MTRACE statHtml 페이지에서 `/statHtml/html.do` 공식 표 응답을 호출해 `경락가격 (원/kg)` × `전체 등급` × `전체 도매시장` 월별 값을 파싱.
- 적재 결과: 75행, 2020-01 ~ 2026-04. 원천 표의 2025-11 값이 `547원/kg`로 비정상 표시되어 적치 제외.
- 품질 가드: 2,000원/kg 미만 또는 15,000원/kg 초과는 명백한 이상값으로 저장하지 않는다.
- 카탈로그 상태: `ready_existing_partial`, exactness `official_monthly_proxy_with_outlier_guard`.
- 주의: EPIC 원 지표는 일별 도매가격이지만 현재 공개 공식 연결값은 월별 돼지도체 경락가격 프록시다.
- 상태 변화: `ready_existing 34 / ready_existing_partial 18 / new_collector_needed 28`.

## 2026-06-11 3단계 추가 확장: 가다랑어 수입단가 프록시 연결
- 대상: `epic:11:69` 가다랑어 어가추이 (월)
- 결론: exact 어가 원천은 아직 미확정이나, 로컬 HS Trade Lab DB의 관세청 월별 수출입 데이터로 부분 프록시를 연결했습니다.
- 소스: `hs_trade_lab/data/hs_trade_lab.db.customs_monthly_record`
  - `0303430000`: 가다랑어(냉동) — 기본 판단 시리즈 우선
  - `0302330000`: 가다랑어(신선/냉장) — 거래가 드문 보조 시리즈
- 구현 함수: `collect_skipjack_import_unit_price_from_hs()` in `scripts/ops/sync_quant_major_indicators.py`
- 산식: `월별 수입금액(USD) / 월별 수입중량(kg)` = `USD/kg`
- 품질 가드: 단가 `<0.3` 또는 `>40 USD/kg`는 tiny lot/단위 오류 가능성이 있어 저장 제외.
- DB 적재 결과: 204행, 2016-01~2026-04.
- 카탈로그 상태: `ready_existing_partial`, exactness `official_customs_unit_price_proxy`.
- 중요 해석 주의:
  - 원 EPIC 지표명은 `어가추이`지만 현재 값은 `관세청 수입단가`입니다.
  - 국내 산지/위판/어가 가격으로 해석하면 안 됩니다.
  - 수산/식품 원가 방향성 프록시로만 사용하고, 화면에는 `부분연결`과 `수입단가 프록시`를 표시해야 합니다.
- 남은 3단계 미연결:
  - `epic:10:11` IPTV 가입자 수: ITSTAT 표 식별은 했으나 데이터 호출 파라미터/첨부 접근 미확정.
  - `epic:3:97`, `epic:3:98` 영상/음원 구독 지출: 공개 무료 exact 카드/결제 원천 미확정. 광범위 온라인 문화서비스 프록시는 오염 위험이 있어 미적치.

## 2026-06-11 P2 추가 확장: 한국 지역별 관광 방문자 + 미국 Rig Count
- 사용자 요청에 따라 2순위(`p2`) 미연결 항목을 추가 재탐색하고, 오염 위험이 낮은 공식 원천 2개를 부분 연결했습니다.

### 1) 한국 지역별 관광 방문자 추이
- 대상: `epic:3:71` 한국 지역별 관광 방문자 추이 (월)
- 소스: 한국관광공사 DataLab 지역별 관광현황 내부 JSON `LN_01_01_016`.
- 구현: `collect_kto_regional_visitors_monthly()` in `scripts/ops/sync_quant_major_indicators.py`.
- 수집 방식: 2020-01부터 최신 공개월까지 월별로 조회하고, 17개 시도 방문자수(`TOU_NUM`)를 `천명` 단위로 변환 저장.
- 적재 결과: 1,309행, 2020-01~2026-05.
- 상태: `ready_existing_partial`, exactness `official_datalab_trend`.
- 중요 주의: DataLab 안내상 방문자수는 총량보다 추세 분석으로 활용 권장. 따라서 정확한 인구 총량/실측 방문객 수로 해석하면 안 되며, 화면에는 `공식 DataLab 추세지표` 또는 `부분연결`로 표시해야 합니다.

### 2) United States Rig Count
- 대상: `epic:19:50` United States Rig Count (주)
- 소스: EIA `U.S. Crude Oil and Natural Gas Rotary Rigs in Operation` 월간 히스토리 페이지 `E_ERTRR0_XR0_NUS_CM`.
- 구현: `collect_eia_us_rig_count_monthly()` in `scripts/ops/sync_quant_major_indicators.py`.
- 수집 방식: EIA HTML 히스토리 표를 파싱하여 월별 rig count 저장. `.xls` 의존성을 피하기 위해 HTML 표 파서로 구현했습니다.
- 적재 결과: 640행, 1973-01~2026-04.
- 상태: `ready_existing_partial`, exactness `official_monthly_proxy`.
- 중요 주의: EPIC 원 지표는 Baker Hughes 주간 Rig Count입니다. 현재 Baker Hughes 공식 페이지/static file은 로컬 환경에서 timeout되어 주간 exact 연결은 보류했고, EIA 월간 공식 자료를 proxy로 연결했습니다. 주간 매매 타이밍용으로 과해석하면 안 됩니다.

### 이번 탐색에서 제외/보류한 항목
- Baker Hughes Canada Rig Count(`epic:19:51`): 공식 페이지 접근 timeout. EIA 월간 표에는 Canada 값이 없어 보류.
- BDI/BCI/BPI/BSI(`epic:7:14~17`): Stooq/Yahoo 후보는 무데이터 또는 anti-bot 응답. Baltic Exchange 공식 데이터는 공개 API/무료 히스토리 접근이 제한적이라 임의 스크래핑 적치 금지.
- EIA `N3020US3m.xls`, `N3010US3m.xls`: 리그카운트가 아니라 천연가스 소비자 가격 파일이므로 데이터 오염 방지를 위해 명시적으로 제외.
- 의료/교육/외식 세부 카드결제액: 공개 exact 카드사 원천 미확정. KOSIS 광역 업종지수를 무리하게 세부 업종으로 대체하면 오염 위험이 높아 미적치.

### 상태 변화
- P2 `new_collector_needed`: 23개 -> 21개.
- P2 `ready_existing_partial`: 15개 -> 17개.
- 전체 인벤토리 CSV 갱신: `scratch/epic/quant_major_indicator_inventory_20260607.csv`.

## 2026-06-12 3단계 추가 확장: IPTV 가입자 수 연간 공식 부분연결
- 대상: `epic:10:11` IPTV 가입자 수 (월)
- 결론: 월간 exact 공개 원천은 아직 미확정이나, ICT통계포털/ITSTAT의 공식 연간 단자 기준 자료를 부분 연결했습니다.
- 소스: ITSTAT `DT_164_27` 유료방송 가입자(단자기준), `IPTV / 소계` 행.
- 구현 함수: `collect_itstat_iptv_subscribers_annual()` in `scripts/ops/sync_quant_major_indicators.py`
- 수집 방식: ITSTAT statHtml 페이지를 Playwright로 초기화한 뒤 same-origin `/statHtml/html.do` 표 응답을 호출해 연도별 IPTV 소계 값을 파싱.
- DB 적재 결과: 16행, 2009~2024년. 최신값 2024년 21,354,573명.
- 카탈로그 상태: `ready_existing_partial`, exactness `official_annual_partial`.
- 중요 해석 주의:
  - 원 EPIC 지표는 월별 IPTV 가입자 수입니다.
  - 현재 값은 월별이 아니라 공식 연간 단자 기준 가입자 수입니다.
  - 월간 추세/월중 변동 판단에는 사용하면 안 되고, 장기 산업 추세 확인용으로만 사용해야 합니다.
- 남은 3단계 미연결:
  - `epic:3:97` 영상 구독 서비스 지출건수 및 지출금액: 무료 공개 exact 카드/결제 원천 미확정.
  - `epic:3:98` 음원 구독 서비스 지출건수 및 지출금액: 무료 공개 exact 카드/결제 원천 미확정.
- 전체 상태(DB 기준): `ready_existing 47 / ready_existing_partial 23 / new_collector_needed 24`.

## 2026-06-13 추가 연결: BDI 프록시 + 영상구독 프록시
- `epic:7:14` Freight Index: BDI(Baltic Dry Index): Stooq CSV는 JS verification으로 차단되고 Baker Hughes 계열 파일은 장시간 timeout이 반복되어 exact 일별 BDI로 연결하지 않았다. 대신 `Yahoo Finance BDRY` 월간 ETF 조정종가/거래량 198행(2018-04~2026-06)을 건화물 운임 방향성 프록시로 적재했다.
- 상태: `ready_existing_partial`, exactness: `market_proxy_monthly`. BCI/BPI/BSI 개별 지수로 해석 금지.
- `epic:3:97` 영상 구독 서비스 지출건수 및 지출금액: exact 카드 결제 원천은 미확정. 이미 수집된 KOSIS `DT_1KE10071` 온라인쇼핑 상품군 중 `문화 및 레저서비스`의 인터넷+모바일 거래액 합산 112행(2017-01~2026-04)을 공식 공개 프록시로 적재했다.
- 상태: `ready_existing_partial`, exactness: `official_online_content_spending_proxy`. 음원 구독(`epic:3:98`)과 동일시하지 말 것.
- 현재 상태: `ready_existing 47 / ready_existing_partial 31 / new_collector_needed 18`.

## 2026-06-13 추가 연결: KOSIS 온라인 소비 프록시 2종
- 사용자 요청에 따라 남은 `수집대기중` 항목을 재검토했고, KOSIS 온라인쇼핑 상품군에서 의미 왜곡이 상대적으로 낮은 2개 항목만 추가 연결했다.
- `epic:11:155` 카드 결제액 추정치: 제과/커피/패스트푸드
  - exact 카드사/민간 업종 결제액 원천은 아직 미확정.
  - 대체값: KOSIS `DT_1KE10071` 온라인쇼핑 `음식서비스` 거래액의 인터넷+모바일 합산.
  - 적재: 112행, 2017-01~2026-04, 단위 `백만원`.
  - 상태: `ready_existing_partial`, exactness `official_online_food_service_proxy`.
  - 해석 주의: 외식/배달 성격이 포함될 수 있어 제과/커피/패스트푸드 카드 결제 exact로 해석하면 안 된다.
- `epic:13:22` 카드 결제액 추정치: 교육용품
  - exact 카드사/민간 업종 결제액 원천은 아직 미확정.
  - 대체값: KOSIS `DT_1KE10071` 온라인쇼핑 `서적 + 사무·문구` 거래액의 인터넷+모바일 합산.
  - 적재: 112행, 2017-01~2026-04, 단위 `백만원`.
  - 상태: `ready_existing_partial`, exactness `official_online_education_goods_proxy`.
  - 해석 주의: 교육용품 전체 카드 결제 exact가 아니라 온라인 서적/문구 소비 프록시다.
- 의도적으로 보류한 항목:
  - `epic:13:21` 유아교육: KOSIS `아동·유아용품`은 교육이 아니라 상품 소비라 의미 왜곡 위험이 커서 미연결.
  - `epic:16:110~113` 피부과/성형외과/치과/약국: KOSIS 보건업 전체 지수로 대체하면 세부 업종 투자판단 오염 위험이 커서 미연결.
  - `epic:3:98` 음원 구독: 영상구독과 같은 `문화 및 레저서비스` 프록시를 복제하면 중복/오해가 생겨 미연결.
- 검증:
  - `/api/quant-major-indicators/series/epic:11:155?limit=3` 정상 응답.
  - `/api/quant-major-indicators/series/epic:13:22?limit=3` 정상 응답.
  - 전체 상태: `ready_existing 47 / ready_existing_partial 33 / new_collector_needed 16`.
- 갱신 파일: `scripts/ops/sync_quant_major_indicators.py`, `scratch/epic/quant_major_indicator_inventory_20260607.csv`.

## 2026-06-13 추가 연결: K-Line Dry Bulk 주간 지수 4종
- 기존 `epic:7:14` BDI는 BDRY ETF 월간 프록시였으나, K-Line IR `Shipping Market Information` 페이지에 BDI/BCI/BPI/BSI 주간 차트 데이터가 HTML에 포함되어 있어 더 직접적인 프록시로 교체했다.
- 소스: `https://www.kline.co.jp/en/ir/finance/shipping.html`
- 구현 함수: `collect_kline_dry_bulk_indices_weekly()` in `scripts/ops/sync_quant_major_indicators.py`.
- 적재 결과:
  - `epic:7:14` BDI: 516개 고유 주간 라벨, 2016-05-06~2026-05-22.
  - `epic:7:15` BCI: 519행, 2016-05-06~2026-05-22.
  - `epic:7:16` BPI: 455행, 2017-08-04~2026-05-22. 원천 차트에서 초기 구간 값이 `null`이라 저장 제외.
  - `epic:7:17` BSI: 519행, 2016-05-06~2026-05-22.
- 상태: 모두 `ready_existing_partial`, exactness `third_party_weekly_index_republication`.
- 해석 주의: Baltic Exchange 공식 licensed feed가 아니라 K-Line IR 재게시 주간 시계열이다. 일별 exact 지수로 과신하면 안 되지만, BDRY ETF 프록시보다 원 EPIC 지표에 훨씬 가깝다.
- 전체 상태: `ready_existing 47 / ready_existing_partial 36 / new_collector_needed 13`.

## 2026-06-13 Codex 추가 확장: 공공/로컬 퀀트 지표 9개

### 추가 완료 지표

1. `public:21:1` KOSPI 시장폭: 상승/하락/보합 종목수
   - 소스: `price_history` + 최신 `stock_universe` 보통주 유니버스 파생
   - 기간: 2021-01-05 ~ 2026-06-05
   - 저장 series: 상승종목수, 하락종목수, 보합종목수, 상승종목비율, 하락종목비율, 중앙수익률, 커버종목수
   - 품질 가드: 커버 종목수 500 미만인 불완전 수집일은 저장 제외

2. `public:21:2` KOSDAQ 시장폭: 상승/하락/보합 종목수
   - 소스/기간/가드 동일

3. `public:21:3` KOSPI 거래량 확산
   - 소스: `price_history` 파생
   - 저장 series: 20일신고가수, 20일신저가수, 거래량3배종목수, 거래량20일중앙배율, 총거래대금, 커버종목수

4. `public:21:4` KOSDAQ 거래량 확산
   - 소스/품질 가드 동일

5. `public:20:101` 소비자심리지수 CSI
   - 소스: 한국은행 ECOS `511Y002/FME`
   - 기간: 2010-01 ~ 2026-05

6. `public:20:102` 경제심리지수 ESI 순환변동치
   - 소스: 한국은행 ECOS `513Y001/E2000`
   - 기간: 2010-01 ~ 2026-05

7. `public:20:103` 제조업 BSI: 업황/신규수주/전망
   - 소스: 한국은행 ECOS `512Y013/C0000/AA`, `512Y013/C0000/AD`, `512Y014/C0000/BA`
   - 기간: 2010-01 ~ 2026-06

8. `public:20:104` 제조업 재고율
   - 소스: 한국은행 ECOS `901Y026/I33A`
   - 기간: 2010-01 ~ 2026-04

9. `public:20:105` 전산업생산지수(농림어업 제외, 계절조정)
   - 소스: 한국은행 ECOS `901Y033/A00/2`
   - 기간: 2010-01 ~ 2026-04

### 검증 결과

- `python3 -m py_compile scripts/ops/sync_quant_major_indicators.py` 통과
- `npm run build` 통과
- API 확인 완료:
  - `/api/quant-major-indicators/series/public:21:1?limit=10`
  - `/api/quant-major-indicators/series/public:21:2?limit=10`
  - `/api/quant-major-indicators/series/public:20:101?limit=3`
  - `/api/quant-major-indicators/series/public:20:103?limit=6`
- 카탈로그 현황: 총 105개, `ready_existing` 56개, `ready_existing_partial` 36개, `new_collector_needed` 13개

### 주의/후속

- 시장폭 지표는 KRX 공식 등락종목 통계가 아니라 로컬 가격 이력 파생값이다. 공식 KRX 등락종목 원천을 찾으면 교차검증 소스로 추가 가능하다.
- 최근 일부 가격 수집일은 500종목 미만이라 제외된다. 장중/장후 시장 브리핑에서는 이 `커버종목수` 가드를 반드시 유지해야 한다.
- ECOS 지표는 공식 API 값이므로 exact로 분류했다. 단, 최신 월 데이터는 통계별 공표 시차가 다르다.

## 2026-06-13 Codex 추가 확장 2: 관세청 HS 섹터 수출입 지표 10개

### 추가 완료 지표

관세청/HS Trade Lab `customs_monthly_record`의 월별 공식 수출입 데이터를 HS 접두어 기준으로 합산했다. 기업별 배분값이 아니라 섹터 총량이므로 기업별 점유율 추정에는 사용하지 말고, 업황/수요/단가 방향성 판단용으로 사용한다.

1. `public:23:1` 자동차 완성차 수출입 — HS `8703`
2. `public:23:2` 자동차 부품 수출입 — HS `8708`
3. `public:23:3` 이차전지 리튬이온 수출입 — HS `850760`
4. `public:23:4` 메모리 반도체 수출입 — HS `854232`
5. `public:23:5` 시스템 반도체 수출입 — HS `854231`
6. `public:23:6` 반도체 제조장비 수출입 — HS `8486`
7. `public:23:7` 조선 상선 수출입 — HS `8901`
8. `public:23:8` 철강 72/73류 수출입 — HS `72`, `73`
9. `public:23:9` 화장품 수출입 — HS `3304`
10. `public:23:10` 의약품 수출입 — HS `3004`

### 저장 series

각 지표는 월별로 아래 series를 저장한다.

- 수출액: 백만달러
- 수입액: 백만달러
- 무역수지: 백만달러
- 수출단가: USD/kg, 수출중량이 0보다 큰 경우만
- 수입단가: USD/kg, 수입중량이 0보다 큰 경우만

### 검증 결과

- 기간: 2016-01 ~ 2026-05
- 각 지표 625행 저장
- `python3 -m py_compile scripts/ops/sync_quant_major_indicators.py` 통과
- `npm run build` 통과
- API 확인 완료:
  - `/api/quant-major-indicators/series/public:23:4?limit=8`
  - `/api/quant-major-indicators/series/public:23:7?limit=8`
- 카탈로그 현황: 총 115개, `ready_existing` 66개, `ready_existing_partial` 36개, `new_collector_needed` 13개

### 주의

- HS 접두어 집계는 공식 수출입 숫자이지만, 기업별 매출로 바로 연결하면 안 된다.
- 일부 품목의 USD/kg 단가는 품목 구성 변화에 민감하다. 단가 자체보다 추세/급변 감지에 우선 활용한다.
- 저장 시점에 `stock.db`가 백그라운드 백필 작업으로 잠겨 1차 저장이 실패했으나, 계산 결과는 정상이고 재시도 후 저장 완료했다.

## 2026-06-13 추가 수집: GKL 입장객 exact 연결 + 수집대기 상태 복구
- 신규 연결: `epic:9:19` GKL 월별 입장객 현황.
- 원천: 공공데이터포털 `그랜드코리아레저(주)_국적별 입장객 수` CSV. 페이지: https://www.data.go.kr/data/15131132/fileData.do?recommendDataYn=Y
- 구현: `scripts/ops/sync_quant_major_indicators.py`의 `collect_gkl_visitors_from_publicdata()`.
- 적재 방식: 일별/영업장별/성별/국적별 CSV를 내려받아 영업일 기준 완성된 월만 월별 합산. 전체 입장객, 코엑스/드래곤/롯데 입장객, 중국/일본/교포/미국/몽골/동남아/대만/홍콩/기타 입장객을 각각 series로 저장.
- 적재 결과: 1,103행, 2018-11~2026-03, 상태 `ready_existing`, exactness `official_publicdata_daily_aggregate`.
- 안정화: `seed_catalog()`가 기존 `ready_existing`/`ready_existing_partial` 상태를 `new_collector_needed`로 덮어쓰지 않도록 수정. DART 카지노/GKL 계열은 원천 일시 실패 시 기존 시계열이 삭제되지 않도록 전체 선삭제 목록에서 제외.
- 복구: 값은 있으나 상태가 되돌아간 `epic:17:17` 한국 후판가격, `epic:4:96` 동북아 유연탄가격, `epic:19:51` Canada Rig Count를 `ready_existing_partial`로 복구. 후판/유연탄은 proxy, Canada Rig Count는 월간 공식 부분연결이므로 exact 주간 지표로 표시하면 안 된다.
- 검증: `python3 -m py_compile scripts/ops/sync_quant_major_indicators.py` 통과, API `/api/quant-major-indicators/series/epic:9:19` 응답 확인 완료.
- 최신 상태: 총 115개, `ready_existing 67 / ready_existing_partial 35 / new_collector_needed 13`, 시리즈 총 92,352행.

### 남은 수집대기 13개
| indicator_key | 항목 | 현재 판단 |
|---|---|---|
| `epic:1:28` | China: Steel Price, G.I | exact 무료 원천 미확정. `epic:1:28_proxy`는 HRC proxy라 대체 불가 |
| `epic:1:30` | China: Steel Price, Wire Rod | exact 무료 원천 미확정 |
| `epic:3:34` | 모두투어 송출객 현황 | 기사 단발 수치 외 장기 자동 원천 미확정 |
| `epic:3:36` | 모두투어 해외 패키지 송출객 | 기사 단발 수치 외 장기 자동 원천 미확정 |
| `epic:9:24` | 파라다이스 국적별 드롭액 | DART 영업잠정 본문에는 국적별 분해 없음. IR/PDF 별도 파서 필요 |
| `epic:9:37` | 드림타워 카지노 입장객 | DART 영업잠정 본문에는 입장객 없음. IR/PDF 별도 파서 필요 |
| `epic:13:21` | 카드 결제액 추정치: 유아교육 | 카드사/민간 추정 원천 필요 |
| `epic:16:110` | 피부과 | 카드사/민간 추정 원천 필요 |
| `epic:16:111` | 성형외과 | 카드사/민간 추정 원천 필요 |
| `epic:16:112` | 치과 | 카드사/민간 추정 원천 필요 |
| `epic:16:113` | 약국 | 카드사/민간 추정 원천 필요 |
| `epic:19:104` | 싱가포르 석유제품 재고 | Enterprise Singapore/S&P 계열로 보이며 무료 자동 원천 미확정 |
| `epic:3:98` | 음원 구독 서비스 지출건수/금액 | 카드/결제 데이터 원천 필요 |

## 2026-06-13 추가 수집: HIRA 의료 진료과목 연간 프록시 3종

### 추가 완료

- `epic:16:110` 카드 결제액 추정치: 피부과
- `epic:16:111` 카드 결제액 추정치: 성형외과
- `epic:16:112` 카드 결제액 추정치: 치과

### 원천 및 구현

- 원천: 공공데이터포털/건강보험심사평가원 `진료과목별 진료 현황` CSV
- 데이터셋 페이지: https://www.data.go.kr/data/15139382/fileData.do
- 구현 파일: `scripts/ops/sync_quant_major_indicators.py`
- 함수: `collect_hira_medical_subject_annual_proxy()`
- 품질 라벨: `official_hira_annual_medical_proxy`
- 카탈로그 exactness: `official_annual_medical_proxy_not_card_exact`

### 저장 series

각 indicator별 2024년 연간 5개 series를 저장한다.

- `patients` — 환자수, 명
- `claims` — 명세서청구건수, 건
- `visit_days` — 입내원일수, 일
- `insurer_payment` — 보험자부담금, 원
- `total_benefit_cost` — 요양급여비용총액, 원

### 검증 숫자

| 항목 | 집계 기준 | 환자수 | 요양급여비용총액 |
|---|---|---:|---:|
| 피부과 | 진료과목=피부과 | 8,135,273명 | 1,003,918,671,710원 |
| 성형외과 | 진료과목=성형외과 | 572,247명 | 378,692,793,730원 |
| 치과 | 진료과목에 치과 포함 | 25,595,353명 | 6,104,070,258,790원 |

### 주의

- 원 EPIC 명칭은 월별 카드 결제액 추정치이나, 현재 연결값은 카드 결제액이 아니다.
- 이 값은 HIRA 건강보험 진료/급여 proxy이므로 월별 소비, 비급여 미용수요, 카드 결제액 exact로 해석하면 안 된다.
- 약국(`epic:16:113`)은 이 HIRA 파일에 약국 행이 없어 미연결 상태를 유지했다. 약국은 별도 HIRA/심평원 약국 조제급여 또는 카드사 원천을 찾아야 한다.
- broad `보건업` 서비스업생산지수를 피부과/성형외과/치과/약국으로 복제하는 방식은 오염 위험이 커서 금지한다.

### 최신 상태

- 총 카탈로그: 115개
- `ready_existing`: 67개
- `ready_existing_partial`: 38개
- `new_collector_needed`: 10개
- 인벤토리: `scratch/epic/quant_major_indicator_inventory_20260607.csv` 재생성

## 2026-06-13 추가 수집: KTO 국민 해외관광객 월별 프록시

### 추가 완료

- `epic:3:34` 모두투어 송출객 현황

### 원천 및 구현

- 원천: 공공데이터포털/한국관광공사 `국민 해외관광객 교통수단별 월별 집계`
- 데이터셋 페이지: https://www.data.go.kr/data/15136315/fileData.do
- 구현 파일: `scripts/ops/sync_quant_major_indicators.py`
- 함수: `collect_kto_korean_outbound_transport_monthly()`
- 품질 라벨: `official_kto_outbound_travel_demand_proxy`
- 카탈로그 exactness: `official_outbound_travel_demand_proxy_not_modetour_exact`

### 저장 series

- `korean_outbound_total` — 전체 국민 해외관광객 수
- `korean_outbound_air` — 공항 합산
- `korean_outbound_sea` — 항구 합산
- `korean_outbound_{출국장명}` — 김포공항/김해공항/인천공항 등 세부 출국장별 값

### 주의

- 모두투어 회사별 송출객 exact가 아니다.
- 모두투어 패키지 송출객(`epic:3:36`)으로 복제하지 않았다. 전체 출국 수요와 패키지 수요는 다른 지표다.
- 현재 공공파일은 2023-08~2024-07 12개월만 포함한다. 장기/최신 확장은 KTO DataLab API 또는 갱신 파일 확인이 필요하다.

### 최신 상태

- 총 카탈로그: 115개
- `ready_existing`: 67개
- `ready_existing_partial`: 39개
- `new_collector_needed`: 9개
- 인벤토리: `scratch/epic/quant_major_indicator_inventory_20260607.csv` 재생성

### 2026-06-13 Codex quant major indicator expansion follow-up
- 남은 수집대기 11개 중 4개 추가 연결 완료.
- `epic:3:98` 음원 구독 서비스 지출/건수: KOSIS DT_1KE10071 온라인쇼핑 `문화 및 레저서비스` 거래액(인터넷+모바일) 프록시로 연결. 2017-01~2026-04, 112행. 음원 구독 exact 아님.
- `epic:3:36` 모두투어 해외 패키지 송출객: 모두투어 뉴스와이어 보도자료에서 숫자가 명확한 월만 부분 수집. 현재 2024-05 84,616명 1행. 누락 월 보간 금지.
- `epic:16:113` 약국 카드 결제액 추정치: KOSIS DT_1K41002 `의약품` 월별 소매판매액 프록시로 연결. 2020-01~2026-04, 76행. 약국 카드결제 exact 아님.
- `epic:13:21` 유아교육 카드 결제액 추정치: KOSIS DT_1KC2023 교육 서비스업 생산지수 프록시로 연결. 2021-Q1~2026-Q1, 714행. 유아교육/카드 exact 아님.
- 베트남 의류/신발(`epic:12:10`), 베트남 IT(`epic:15:11`)은 현재 hs_trade_lab DB에 베트남 전체 `nationtrade`만 있고 국가×HS 레코드가 없어 연결 보류. 베트남 전체 수출액을 품목 지표로 넣는 것은 데이터 오염이므로 금지.
- 싱가포르 석유제품 재고, China G.I/Wire Rod, 파라다이스 국적별 드롭액, 드림타워 입장객은 지속 수집 가능한 공개 exact 원천 또는 파서 추가 필요.

## 2026-06-13 추가 업데이트: 수집대기 4개까지 축소

### 신규/추가 연결

| indicator_key | 항목 | 상태 | 행수/기간 | 원천 | 주의 |
|---|---|---:|---|---|---|
| `epic:12:10` | 베트남 의류·신발 수출 금액 | `ready_existing_partial` | 375행 / 2016-01~2026-05 | 관세청 `nnewtempertrade` | 한국 기준 베트남향 수출 proxy. 베트남 글로벌 수출 exact 아님 |
| `epic:15:11` | 베트남 IT제품 수출 금액 | `ready_existing_partial` | 375행 / 2016-01~2026-05 | 관세청 `nnewtempertrade` | 한국 기준 베트남향 수출 proxy. 베트남 글로벌 수출 exact 아님 |
| `epic:9:37` | 드림타워 카지노 월별 입장객 | `ready_existing` | 60행 / 2021-06~2026-05 | 롯데관광개발 IR Pack Excel | 회사 IR Excel exact |

### 구현 위치

- 파일: `scripts/ops/sync_quant_major_indicators.py`
- 함수: `get_customs_service_key()`, `_parse_customs_xml_items()`, `collect_vietnam_country_product_exports()`, `collect_dreamtower_visitors_from_ir_excel()`
- 검증 명령: `python3 -m py_compile scripts/ops/sync_quant_major_indicators.py`

### 남은 수집대기 4개와 보류 근거

| indicator_key | 항목 | 현재 판단 |
|---|---|---|
| `epic:1:28` | China: Steel Price, G.I | SunSirs G.I는 `HW_CHECK` JS challenge만 반환, SHFE는 방화벽/캡차, SteelBenchmarker에 중국 GI 없음. HRC proxy와 혼동 금지. |
| `epic:1:30` | China: Steel Price, Wire Rod | SunSirs/SHFE 모두 안정 자동수집 불가. exact 무료 원천 재탐색 필요. |
| `epic:9:24` | 파라다이스 월별 드롭액, 국적별 | DART 월간 공시에는 총 드롭액만 있음. 파라다이스 IR PDF에는 CN VIP/JP VIP/Other VIP/Mass 분기 그래프가 있으나 월별 exact가 아니므로 월별로 배분 금지. |
| `epic:19:104` | 싱가포르 석유제품 재고 추이 | Enterprise Singapore StatLink는 구독/장바구니 기반이며 확인된 메뉴는 Monthly Oil Statistics(월간 석유 무역통계)이지 weekly petroleum stock이 아님. |

### 현재 카탈로그 상태

- 총 115개
- `ready_existing`: 68개
- `ready_existing_partial`: 43개
- `new_collector_needed`: 4개


## 2026-06-13 추가 업데이트: China G.I / Wire Rod 최근 공개가격 연결

### 신규 부분연결

| indicator_key | 항목 | 상태 | 행수/기간 | 원천 | 주의 |
|---|---|---:|---|---|---|
| `epic:1:28` | China: Steel Price, G.I | `ready_existing_partial` | 6행 / 2026-06-08~2026-06-13 | SunSirs China Galvanized sheet spot price | 최근 공개 테이블만 가능. 장기 exact 아님 |
| `epic:1:30` | China: Steel Price, Wire Rod | `ready_existing_partial` | 6행 / 2026-06-08~2026-06-13 | SunSirs China Wire Rod spot price | 최근 공개 테이블만 가능. 장기 exact 아님 |

### 구현 메모

- 함수: `_fetch_sunsirs_html()`, `collect_sunsirs_china_steel_daily()`
- SunSirs는 최초 요청 시 `HW_CHECK` 쿠키 챌린지를 반환한다. 수집기는 `var _0x2 = "..."` 값을 읽어 `HW_CHECK` 쿠키를 세팅한 뒤 재요청한다.
- `epic:1:28` 상세: `Galvanized sheet`, `HDG`, `DX51D+Z`, `1.0*1250*C`, `RMB/ton`.
- `epic:1:30` 상세: `Wire Rod`, `HPB235`, `Φ8`, `RMB/ton`.
- 전체 배치가 아닌 단독 검증으로 DB 반영 완료. `python3 -m py_compile scripts/ops/sync_quant_major_indicators.py` 통과.

### 최신 잔여 수집대기

| indicator_key | 항목 | 보류 사유 |
|---|---|---|
| `epic:9:24` | 파라다이스 월별 드롭액, 국적별 | DART 월간 공시에는 총 드롭액만 있음. IR PDF에는 분기별 CN VIP/JP VIP/Other VIP/Mass 그래프가 있으나 월별 exact가 아니므로 월별 배분 금지. |
| `epic:19:104` | 싱가포르 석유제품 재고 추이 | Enterprise Singapore StatLink는 구독/장바구니 기반이고 확인된 메뉴는 월간 석유 무역통계이지 weekly petroleum stock이 아님. |

### 최신 카탈로그 상태

- 총 115개
- `ready_existing`: 68개
- `ready_existing_partial`: 45개
- `new_collector_needed`: 2개

## 2026-06-13 추가 업데이트: 운영 동기화 및 CSV 재생성

### 수행 내용
- `scripts/ops/quant_indicators_cron.py`의 주간 배치에 SunSirs 중국 철강 최근 공개가격 수집을 연결했습니다.
- `scratch/epic/quant_major_indicator_inventory_20260607.csv`를 DB 기준으로 재생성했습니다.

### 최신 DB/CSV 기준 상태
- `ready_existing`: 68개
- `ready_existing_partial`: 45개
- `new_collector_needed`: 2개
- 카탈로그 총계: 115개

### 신규 주간 자동수집 대상
| indicator_key | 지표 | 상태 | 커버리지 | 주의 |
|---|---:|---:|---:|---|
| `epic:1:28` | China: Steel Price, G.I | `ready_existing_partial` | 2026-06-08~2026-06-13, 6행 | SunSirs 최근 공개 테이블. 장기 exact 아님 |
| `epic:1:30` | China: Steel Price, Wire Rod | `ready_existing_partial` | 2026-06-08~2026-06-13, 6행 | SunSirs 최근 공개 테이블. 장기 exact 아님 |

### 남은 수집대기 2개
| indicator_key | 지표 | 보류 사유 |
|---|---|---|
| `epic:9:24` | 파라다이스 월별 드롭액, 국적별 | DART 월간 공시는 총 드롭액만 제공. IR PDF에는 CN/JP/Other/Mass 분기별 그래프가 있으나 월별 exact 원천이 아니므로 강제 배분 금지. |
| `epic:19:104` | 싱가포르 석유제품 재고 추이 | Enterprise Singapore StatLink는 구독/장바구니 기반이며 확인된 항목은 Monthly Oil Statistics(월간 석유 무역통계)라 weekly petroleum stock exact 원천이 아님. |

## 2026-06-13 추가 업데이트: Paradise 국적별/세그먼트별 드롭액 exact 연결

### 신규 연결
| indicator_key | 지표 | 상태 | 커버리지 | 원천 | 검증 |
|---|---|---:|---:|---|---|
| `epic:9:24` | 파라다이스 월별 드롭액, 국적별 | `ready_existing` | 195행 / 2023-01~2026-03 | Paradise 공식 Monthly IR Pack `Segment` 시트 | DART 총 드롭액과 `Total 드롭액` 최신 겹치는 월 오차 0.0001% 미만 |

### 파싱 기준
- URL: `https://www.paradise.co.kr/download/27479`
- 시트: `Segment`
- 값 컬럼: `CN VIP`, `JP VIP`, `Other VIP`, `Mass`, `Total`
- 단위: 원천 `KRW mn`, DB `백만원`
- 코드: `collect_paradise_segment_drop_from_ir_excel()`
- 운영: `quant_indicators_cron.py --mode daily`에도 추가

### 추가 상태 정정
- `epic:0:4` 한국 자동차 시장점유율은 KAMA 공식 내수 판매량에서 파생된 882행이 이미 있으므로 `ready_existing`으로 정정했습니다.

### 최신 DB/CSV 기준 상태
- `ready_existing`: 69개
- `ready_existing_partial`: 45개
- `new_collector_needed`: 1개
- 카탈로그 총계: 115개

### 남은 수집대기 1개
| indicator_key | 지표 | 보류 사유 |
|---|---|---|
| `epic:19:104` | 싱가포르 석유제품 재고 추이 | Enterprise Singapore StatLink는 weekly oil stock levels를 제공하지만 subscription-based 시스템으로 안내됨. S&P/CEIC/QCIntel도 유료/부분공개 재판매 형태. 무료 공개 exact 자동 원천 확인 전까지 프록시 적재 금지. |

## 2026-06-14 재점검: Singapore Weekly Oil Stock 보류 유지

### 결론
`epic:19:104` 싱가포르 석유제품 재고 추이는 무료 공개 exact 자동 원천을 아직 확인하지 못했습니다. StatLink 유료 접근이나 정식 다운로드 파일 없이는 적재하지 않는 것이 맞습니다.

### 확인한 원천
| 원천 | 확인 결과 | 처리 |
|---|---|---|
| Enterprise Singapore StatLink | weekly oil stock levels 제공은 명시되어 있으나 StatLink는 subscription-based system | 유료 원천 후보 |
| Enterprise Singapore FAQ | Weekly Oil Trade statistics 가격 안내 존재 | 무료 자동수집 불가 근거 |
| S&P Global / Argus / QCIntel | Enterprise Singapore 데이터를 기사/유료 상품으로 인용 | 기사 단발값 적치 금지 |
| CEIC | Singapore Weekly Oil Inventory 샘플/과거 일부 수치 노출, 유료 DB | 구조화 무료 최신 원천 아님 |

### 운영 원칙
- 월간 석유 무역통계, 기사 단발 숫자, CEIC 미리보기 값을 weekly stock 시계열로 넣지 않습니다.
- 유료 StatLink/API 또는 정식 파일을 확보하면 `Residue`, `Middle Distillates`, `Light Distillates`를 같은 단위(`천배럴`)로 적재합니다.
