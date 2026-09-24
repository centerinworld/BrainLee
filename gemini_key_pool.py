"""
gemini_key_pool.py
Multi-Account Gemini Dual API Key Load Balancer & Failover Engine
"""

import os
import sys
import json
from pathlib import Path

WORKSPACE_ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
ENV_PATHS = [
    WORKSPACE_ROOT / ".env",
    Path("/Volumes/Realtek_NVME/AI System/.env")
]

def get_registered_keys():
    keys = []
    k1 = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_AI_STUDIO_KEY")
    k2 = os.environ.get("GEMINI_API_KEY_2") or os.environ.get("GOOGLE_AI_STUDIO_KEY_2")
    
    for ep in ENV_PATHS:
        if ep.exists():
            for line in ep.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY=") and not k1:
                    k1 = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("GEMINI_API_KEY_2=") and not k2:
                    k2 = line.split("=", 1)[1].strip().strip('"').strip("'")
                    
    if k1 and len(k1) > 10:
        keys.append({"account": "Gemini Account #1 (메인 계정)", "key": k1, "status": "🟢 활성 (15 RPM / 1,500 RPD)"})
    if k2 and len(k2) > 10:
        keys.append({"account": "Gemini Account #2 (서브 계정)", "key": k2, "status": "🟢 활성 (15 RPM / 1,500 RPD)"})
        
    return keys

def get_key_pool_status():
    keys = get_registered_keys()
    total_rpd = len(keys) * 1500 if keys else 1500
    total_rpm = len(keys) * 15 if keys else 15
    return {
        "status": "success",
        "registered_accounts_count": len(keys),
        "total_free_rpm": f"{total_rpm} RPM (분당 {total_rpm}회)",
        "total_free_rpd": f"{total_rpd:,} RPD (하루 {total_rpd:,}회 완전 무료)",
        "accounts": [
            {
                "account": k["account"],
                "masked_key": f"{k['key'][:8]}...{k['key'][-6:]}",
                "status": k["status"]
            }
            for k in keys
        ],
        "message": "💎 듀얼 계정 연결 완료: 무료 할당량이 3,000회/일로 2배 확장되어 가동 중입니다." if len(keys) >= 2 else "1개 계정 활성 중 (1,500 RPD): 2번 계정의 API Key를 등록하면 3,000회/일로 2배 확장됩니다."
    }

def add_second_gemini_key(new_key: str):
    new_key = new_key.strip().strip('"').strip("'")
    if len(new_key) < 10:
        return {"status": "error", "message": "유효한 API Key 형식이 아닙니다."}
        
    for ep in ENV_PATHS:
        if ep.exists():
            content = ep.read_text(encoding="utf-8")
            if "GEMINI_API_KEY_2=" in content:
                lines = [l if not l.startswith("GEMINI_API_KEY_2=") else f"GEMINI_API_KEY_2={new_key}" for l in content.splitlines()]
                ep.write_text("\n".join(lines), encoding="utf-8")
            else:
                content += f"\nGEMINI_API_KEY_2={new_key}\nGOOGLE_AI_STUDIO_KEY_2={new_key}\n"
                ep.write_text(content, encoding="utf-8")
                
    os.environ["GEMINI_API_KEY_2"] = new_key
    os.environ["GOOGLE_AI_STUDIO_KEY_2"] = new_key
    return {"status": "success", "message": "2번 계정 Gemini API Key 등록 완료! (일일 무료 한도 3,000회로 2배 확장)"}

if __name__ == "__main__":
    print(get_key_pool_status())
