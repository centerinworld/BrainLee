import httpx
import json
from datetime import datetime

BASE_URL = "http://127.0.0.1:8000"

def test_ingest():
    # 1. 재무 데이터 수신 테스트
    print("Testing /api/ingest/fundamentals...")
    financial_data = {
        "stock_code": "005930",
        "year": 2023,
        "quarter": 4,
        "revenue": 67000000000000.0,
        "operating_profit": 2800000000000.0,
        "net_income": 6000000000000.0,
        "is_annual": False
    }
    response = httpx.post(f"{BASE_URL}/api/ingest/fundamentals", json=financial_data)
    print(f"Response: {response.status_code}, {response.json()}")

    # 2. 주가 데이터 수신 테스트 (Bulk)
    print("\nTesting /api/ingest/market-price...")
    price_data = {
        "stock_code": "005930",
        "prices": [
            {
                "date": "2024-03-20T00:00:00",
                "open": 72000.0,
                "high": 73000.0,
                "low": 71500.0,
                "close": 72500.0,
                "volume": 20000000.0
            },
            {
                "date": "2024-03-21T00:00:00",
                "open": 73000.0,
                "high": 75000.0,
                "low": 72800.0,
                "close": 74800.0,
                "volume": 25000000.0
            }
        ]
    }
    response = httpx.post(f"{BASE_URL}/api/ingest/market-price", json=price_data)
    print(f"Response: {response.status_code}, {response.json()}")

    # 3. 섹터 데이터 수신 테스트
    print("\nTesting /api/ingest/sectors...")
    sector_data = {
        "sector_name": "반도체",
        "stock_codes": ["005930", "000660"]
    }
    response = httpx.post(f"{BASE_URL}/api/ingest/sectors", json=sector_data)
    print(f"Response: {response.status_code}, {response.json()}")

if __name__ == "__main__":
    test_ingest()
