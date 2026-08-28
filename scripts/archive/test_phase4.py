import httpx
import json

BASE_URL = "http://127.0.0.1:8000"

def test_ai_analysis():
    # 1. AI 리포트 생성 테스트 (이미 Phase 3에서 데이터가 주입된 005930 사용)
    print("Testing /api/reports/generate/005930...")
    response = httpx.post(f"{BASE_URL}/api/reports/generate/005930")
    if response.status_code == 200:
        report = response.json()
        print(f"Generated Report ID: {report['id']}")
        print(f"Content Preview: {report['content'][:100]}...")
    else:
        print(f"FAILED: {response.status_code}, {response.text}")

    # 2. 최근 리포트 조회 테스트
    print("\nTesting /api/reports/latest/005930...")
    response = httpx.get(f"{BASE_URL}/api/reports/latest/005930")
    if response.status_code == 200:
        report = response.json()
        print(f"Latest Report Date: {report['report_date']}")
        print("Success: AI 리포트가 정상적으로 조회되었습니다.")
    else:
        print(f"FAILED: {response.status_code}, {response.text}")

if __name__ == "__main__":
    test_ai_analysis()
