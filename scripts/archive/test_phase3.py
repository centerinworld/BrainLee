import httpx
import json
from datetime import datetime, timedelta

BASE_URL = "http://127.0.0.1:8000"

def setup_dummy_data():
    # 1. 삼성전자(005930) 4분기 연속 성장 데이터 주입
    print("Setting up 4 quarters of growth for 005930...")
    for q in range(1, 5):
        data = {
            "stock_code": "005930",
            "year": 2023,
            "quarter": q,
            "revenue": 50000000.0 * q,
            "operating_profit": 1000000.0 * q,
            "net_income": 800000.0 * q,
            "is_annual": False
        }
        httpx.post(f"{BASE_URL}/api/ingest/fundamentals", json=data)

    # 2. 하이닉스(000660) 역성장 데이터 주입 (스크리닝 탈락용)
    print("Setting up declining data for 000000...")
    for q in range(1, 5):
        data = {
            "stock_code": "000660",
            "year": 2023,
            "quarter": q,
            "revenue": 50000000.0 / q,
            "operating_profit": 1000000.0 / q,
            "net_income": 800000.0 / q,
            "is_annual": False
        }
        httpx.post(f"{BASE_URL}/api/ingest/fundamentals", json=data)

    # 3. 주가 데이터 주입 (차트 테스트용)
    print("Setting up price history for 005930...")
    price_data = {
        "stock_code": "005930",
        "prices": [
            {
                "date": (datetime.now() - timedelta(days=i)).isoformat(),
                "open": 70000.0 + i*100,
                "high": 71000.0 + i*100,
                "low": 69000.0 + i*100,
                "close": 70500.0 + i*100,
                "volume": 1000000.0
            } for i in range(5)
        ]
    }
    httpx.post(f"{BASE_URL}/api/ingest/market-price", json=price_data)

def test_endpoints():
    # 1. 차트 데이터 조회
    print("\nTesting /api/dashboard/chart/005930...")
    response = httpx.get(f"{BASE_URL}/api/dashboard/chart/005930")
    print(f"Chart Data Sample: {response.json()[:1]}")

    # 2. 섹터 성능 조회
    print("\nTesting /api/dashboard/sectors...")
    response = httpx.get(f"{BASE_URL}/api/dashboard/sectors")
    print(f"Sectors: {response.json()}")

    # 3. 3대 스크리닝 조회
    print("\nTesting /api/dashboard/screening/triple...")
    response = httpx.get(f"{BASE_URL}/api/dashboard/screening/triple")
    print(f"Screening Result: {response.json()}")

if __name__ == "__main__":
    setup_dummy_data()
    test_endpoints()
