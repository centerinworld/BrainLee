# HS Trade Lab

기존 `/Volumes/Realtek_NVME/stock_dashboard/runtime` 시스템과 분리된 독립 HS Code 분석 실험용 앱입니다.

## 목적

- 한국 HS Code 기준 수출입 추세를 월별로 적재
- HS Code와 상장사/섹터를 수동 매핑
- 기존 `stock.db`의 기업/섹터 정보를 읽기 전용으로 조회
- 데이터소스 URL, API Key, 파라미터를 별도로 저장
- Gemini 기반 상품명 → HS Code 제안 보조 기능 제공

## 구조

- `app/main.py`: FastAPI 진입점
- `app/trade_connector.py`: 외부 수출입 API 연결과 HS Code 제안
- `app/stock_reference.py`: 기존 `stock.db` 읽기 전용 조회
- `static/`: 독립 정적 페이지
- `data/hs_trade_lab.db`: 이 앱만 사용하는 로컬 DB

## 실행

```bash
uvicorn app.main:app --app-dir /Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab --reload --port 8011
```

브라우저:

- [http://127.0.0.1:8011](http://127.0.0.1:8011)

## 참고

- 기존 파일은 수정하지 않습니다.
- 기존 `stock.db`는 `mode=ro`로만 연결됩니다.
- 공공데이터포털/관세청 API 스펙이 확정되면 `params_json`과 `endpoint_path`를 맞춰 연결하면 됩니다.

## 관세청 API 키 입력

```bash
cd /Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab
chmod +x bin/set_customs_key.sh bin/download_customs_all.sh
./bin/set_customs_key.sh
```

- 입력한 키는 `hs_trade_lab/.env`의 `CUSTOMS_ITEMTRADE_SERVICE_KEY`로만 저장됩니다.
- 기존 루트 `.env`는 수정하지 않습니다.

## 관세청 전체 다운로드

```bash
cd /Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab
./bin/download_customs_all.sh --start-year 2016 --end-year 2026
```

- 기본 포함 API:
  - 품목별 수출입실적
  - 시도별 품목별 수출입실적
  - 시도별 성질별 수출입실적
  - 국가별 수출입실적
  - 성질별 수출입실적
  - 신성질별 수출입실적
  - 시도별 수출입실적
- 산출물:
  - `data/reference/*.json`: 코드표 스냅샷
  - `data/customs_downloads/<endpoint>/*.jsonl.gz`: 요청 단위 raw 적재
  - `data/customs_downloads/manifest.json`: 수집 이력/요청 결과

## 적재와 일일 갱신

```bash
cd /Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab
./bin/download_customs_all.sh --start-year 2016 --end-year 2026
../venv/bin/python scripts/ingest_customs_data.py
./bin/daily_refresh.sh
```

- `scripts/ingest_customs_data.py`
  - raw `jsonl.gz`를 `data/hs_trade_lab.db`의 `customs_monthly_record`로 적재합니다.
- `bin/daily_refresh.sh`
  - 최근 2개 연도를 다시 내려받아 월별 정정분을 반영하고, 이어서 DB 적재까지 실행합니다.

## 대시보드

```bash
uvicorn app.main:app --app-dir /Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab --reload --port 8011
```

- 주요 화면
  - 7개 섹터 선택형 트렌드 그래프
  - HS Code → 섹터 수동 매핑
  - HS Code → 상장사 수동 매핑
  - 특징 섹터/종목 리스트
  - OpenAI 기반 섹터 요약 버튼

## 중요 제약

- `관세청_신성질별 국가별 수출입실적(GW)`는 `imexTpcd × 통합성질코드 × 국가코드`가 모두 필수입니다.
- 현재 보유 코드표 기준으로 대략 `2 × 758 × 269 = 407,804` 요청이 필요하므로, 일반 운영 트래픽 한도에서도 바로 전수 수집이 어렵습니다.
- 이 API는 다음 중 하나가 필요합니다.
  - 기간 또는 국가/코드 범위 축소
  - 더 높은 호출 한도
  - 관세청 측 bulk 제공 방식 확인
- 필요하면 이 API만 별도 우선순위 수집 전략으로 다시 쪼개 드리겠습니다.
