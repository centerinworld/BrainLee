#!/usr/bin/env python3
"""DART 수주공시 5년치 전체 백필 스크립트 (2021~2024-06)"""
import sys, os, logging
sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
os.chdir('/Volumes/Realtek_NVME/stock_dashboard/runtime')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

from collectors.dart_contract_collector import backfill_dart_contracts

log.info("DART 5년치 백필 시작 (2021-01-01 ~ 2024-06-30)")
try:
    backfill_dart_contracts(days=5 * 365, skip_ai=True)
    log.info("DART 5년 백필 완료")
except Exception as e:
    log.error(f"DART 5년 백필 오류: {e}")
