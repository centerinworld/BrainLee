import httpx
import json

BASE_URL = "http://127.0.0.1:8000"

def test_final_output():
    # 1. OpenClaw용 최종 통합 리포트 조회 테스트
    print("Testing /api/reports/ready (Final Output)...")
    response = httpx.get(f"{BASE_URL}/api/reports/ready")
    if response.status_code == 200:
        results = response.json()
        print(f"Total Ready Reports: {len(results)}")
        for i, res in enumerate(results):
            print(f"[{i+1}] Stock: {res['stock_code']}")
            print(f"    AI Report Preview: {res['ai_report'][:50]}...")
    else:
        print(f"FAILED: {response.status_code}, {response.text}")

if __name__ == "__main__":
    test_final_output()
