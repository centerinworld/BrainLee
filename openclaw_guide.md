# OpenClaw Integration Guide (데이터 연동 가이드)

본 문서는 외부 수집 도구인 **OpenClaw**가 안티그래비티 백엔드 API로 데이터를 전송할 때 지켜야 할 규격과 프롬프트 구성을 설명합니다.

---

## 1. 재무제표 데이터 연동 (Fundamentals)

DART 등에서 추출한 분기별 재무 정보를 전송합니다.

- **Endpoint**: `POST http://localhost:8000/api/ingest/fundamentals`
- **JSON Format**:
```json
{
  "stock_code": "005930",
  "year": 2023,
  "quarter": 4,
  "revenue": 67000000000000,
  "operating_profit": 2800000000000,
  "net_income": 6300000000000,
  "is_annual": false
}
```

- **OpenClaw 프롬프트 예시**:
> "다음 추출된 재무제표 텍스트에서 [종목코드, 연도, 분기, 매출액, 영업이익, 당기순이익]을 찾아 위 JSON 형식으로 변환해줘. 숫자는 콤마 없이 정수로만 표기해."

---

## 2. 주가/시세 데이터 연동 (Market Price)

일별 OHLCV(시가, 고가, 저가, 종가, 거래량) 데이터를 전송합니다. 대량의 시계열 데이터를 효율적으로 처리하도록 설계되었습니다.

- **Endpoint**: `POST http://localhost:8000/api/ingest/market-price`
- **JSON Format**:
```json
{
  "stock_code": "005930",
  "prices": [
    {
      "date": "2024-03-21T00:00:00",
      "open": 72000.0,
      "high": 75000.0,
      "low": 71500.0,
      "close": 74800.0,
      "volume": 15000000.0
    }
  ]
}
```

- **주의사항**: `prices`는 배열(Array)이므로 여러 날짜의 데이터를 한 번에 묶어서 보낼 수 있습니다 (Bulk Insert 지원).

---

## 3. 섹터 정보 업데이트 (Sectors)

종목이 어떤 섹터(반도체, 자동차 등)에 속하는지 맵핑 정보를 전송합니다.

- **Endpoint**: `POST http://localhost:8000/api/ingest/sectors`
- **JSON Format**:
```json
{
  "sector_name": "반도체",
  "stock_codes": ["005930", "000660"]
}
```

---

## 4. 최종 리포트 수신 (Output API)

OpenClaw가 백엔드에서 분석이 끝난 최종 결과를 가져가 텔레그램 등으로 전송할 때 사용합니다.

- **Endpoint**: `GET http://localhost:8000/api/reports/ready`
- **Response Format**:
```json
[
  {
    "stock_code": "005930",
    "financial_summary": { "revenue": ..., "profit": ... },
    "ai_report": "AI 분석 결과 텍스트..."
  }
]
```

---

## 💡 연동 팁 (CORS)
프론트엔드와 백엔드 간의 연동을 위해 CORS 설정이 되어 있으므로, OpenClaw 브라우저 환경에서도 직접 API 호출이 가능합니다. 모든 수치 데이터는 **한화(KRW) 기준**으로 전송하는 것을 권장합니다.
