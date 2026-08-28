import requests
import json
import os

url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
payload = {
    "bld": "dbms/MDC/STAT/standard/MDCSTAT02301",
    "mktId": "ALL",
    "invstTpCd": "ALL",
    "strtDd": "20260410",
    "endDd": "20260410",
    "share": "1",
    "money": "3",
}
headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "http://data.krx.co.kr/"
}

try:
    res = requests.post(url, data=payload, headers=headers, timeout=10)
    print(f"Status: {res.status_code}")
    data = res.json()
    output = data.get("output", [])
    print(f"Count: {len(output)}")
    if output:
        print(f"Example: {output[0]}")
except Exception as e:
    print(f"Error: {e}")
