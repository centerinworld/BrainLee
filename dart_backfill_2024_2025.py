#!/usr/bin/env python3
"""DART 수주공시 2024-07~2025-12 백필 스크립트"""
import sys, os, logging, time
sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
os.chdir('/Volumes/Realtek_NVME/stock_dashboard/runtime')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

from collectors.dart_contract_collector import collect_dart_contracts

periods = [
    ('20240701', '20241231'),
    ('20250101', '20250630'),
    ('20250701', '20251231'),
]

total = 0
for bgn, end in periods:
    log.info(f"수집 시작: {bgn}~{end}")
    try:
        result = collect_dart_contracts(
            days=0, min_signal=1, skip_ai=True,
            bgn_str=bgn, end_str=end
        )
        cnt = len(result) if result else 0
        total += cnt
        log.info(f"완료: {bgn}~{end} → {cnt}건")
    except Exception as e:
        log.error(f"오류 {bgn}~{end}: {e}")
    time.sleep(2)  # 구간 사이 잠시 대기

log.info(f"DART 2024-07~2025-12 백필 완료. 총 {total}건")
