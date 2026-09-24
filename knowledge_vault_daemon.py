# -*- coding: utf-8 -*-
"""
knowledge_vault_daemon.py
Background Scheduled Harvester for Personal Knowledge Hub
Periodically downloads and indexes:
- DART disclosures & business reports
- Brokerage research reports
- Customs trade data (HS codes)
- Telegram market intelligence
"""

import time
import datetime
import traceback
from knowledge_rag_engine import seed_comprehensive_intelligence, get_vault_statistics

def run_harvest_cycle():
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 🚀 [Knowledge Vault Daemon] Starting scheduled intelligence harvest cycle...")
    try:
        seed_comprehensive_intelligence()
        stats = get_vault_statistics()
        print(f"[{now_str}] ✅ [Knowledge Vault Daemon] Cycle completed! Total docs: {stats['total_documents']}, Chunks: {stats['total_indexed_chunks']}")
    except Exception as e:
        print(f"[{now_str}] ❌ [Knowledge Vault Daemon] Harvest error: {e}")
        traceback.print_exc()

def main():
    print("🧠 Personal Knowledge Vault Background Daemon started.")
    # Run once at startup
    run_harvest_cycle()
    
    # Run loop checking time
    while True:
        try:
            now = datetime.datetime.now()
            # 07:30 or 16:30
            if (now.hour == 7 and now.minute == 30) or (now.hour == 16 and now.minute == 30):
                run_harvest_cycle()
                time.sleep(65)
            else:
                time.sleep(30)
        except KeyboardInterrupt:
            print("Daemon stopping...")
            break
        except Exception as e:
            time.sleep(30)

if __name__ == "__main__":
    main()
