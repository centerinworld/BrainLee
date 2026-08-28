"""
collect_bizr_no.py — 전체 보통주 사업자번호(bizr_no) 수집

전략:
  1) employment_company.bizr_no → stock_bizr_no_map 동기화 (API 없이 즉시)
  2) 미보유 종목 → DART company.json API 조회
  3) DART 실패 → Naver 기업정보 / 금감원 DART 웹 파싱 시도

결과:
  - stock_bizr_no_map 전체 갱신
  - collect_bizr_no_result.csv 저장 (미수집 종목 목록)
  - 새로 bizr_no 확보된 종목은 fetch_employment_insurance.py --code 로 즉시 수집 가능

실행:
  python3 employment_monitor/collect_bizr_no.py
  python3 employment_monitor/collect_bizr_no.py --only-missing   # 미보유만
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
import time
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
EMP_DB   = os.path.join(_DIR, 'employment.db')
STOCK_DB = os.path.join(_DIR, '..', 'stock.db')

sys.path.insert(0, os.path.join(_DIR, '..'))
from dart_key_manager import DartKeyRotator, RotatingOpenDartReader

DART_COMPANY_URL = 'https://opendart.fss.or.kr/api/company.json'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


# ── DART corp_code 맵 로드 ────────────────────────────────────────────────────
def load_corp_map() -> dict:
    """OpenDartReader로 stock_code → corp_code 맵 반환."""
    try:
        dart = RotatingOpenDartReader()
        clist = dart.corp_codes
        result = {}
        for _, row in clist.iterrows():
            sc = str(row.get('stock_code', '')).strip()
            cc = str(row.get('corp_code', '')).strip()
            if sc and len(sc) == 6 and sc.isdigit():
                result[sc] = cc
        logger.info(f'DART corp_code 맵: {len(result)}개')
        return result
    except Exception as e:
        logger.error(f'corp_code 맵 로드 실패: {e}')
        return {}


# ── DART company.json ─────────────────────────────────────────────────────────
def dart_bizr_no(corp_code: str) -> str | None:
    """DART company.json API로 사업자번호(10자리) 조회."""
    rotator = DartKeyRotator()
    while True:
        key = rotator.next_key()
        if not key:
            return None
        try:
            r = requests.get(DART_COMPANY_URL,
                params={'crtfc_key': key, 'corp_code': corp_code},
                timeout=10)
            d = r.json()
            if d.get('status') == '020':
                rotator.mark_exhausted(key)
                continue
            if d.get('status') != '000':
                return None
            bizr = str(d.get('bizr_no', '') or '').replace('-', '').strip()
            return bizr if len(bizr) == 10 else None
        except Exception:
            return None


# ── Naver 기업정보 파싱 ───────────────────────────────────────────────────────
def naver_bizr_no(stock_code: str) -> str | None:
    """
    Naver Finance 기업정보에서 사업자번호 파싱 시도.
    실제로는 노출 안 되므로 None 반환이 정상.
    """
    try:
        r = requests.get(
            f'https://finance.naver.com/item/coinfo.naver?code={stock_code}',
            headers=HEADERS, timeout=10)
        text = r.text
        # 10자리 연속 숫자 패턴 (사업자번호 형식)
        m = re.search(r'사업자[^0-9]*([0-9]{10})', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


# ── DART 웹 공시 파싱 ─────────────────────────────────────────────────────────
def dart_web_bizr_no(corp_code: str) -> str | None:
    """
    DART 기업 개요 웹페이지에서 사업자번호 파싱.
    dart.fss.or.kr/corp/searchCorpInfo.do
    """
    try:
        r = requests.get(
            'https://dart.fss.or.kr/corp/searchCorpInfo.do',
            params={'textCrpCd': corp_code},
            headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        # 테이블에서 사업자번호 셀 찾기
        for td in soup.find_all('td'):
            txt = td.get_text(strip=True)
            m = re.match(r'^([0-9]{3}-[0-9]{2}-[0-9]{5})$', txt)
            if m:
                return m.group(1).replace('-', '')
            m2 = re.match(r'^([0-9]{10})$', txt)
            if m2:
                return m2.group(1)
    except Exception:
        pass
    return None


# ── 메인 수집 ─────────────────────────────────────────────────────────────────
def run(only_missing: bool = False, delay: float = 0.2):
    emp_conn = sqlite3.connect(EMP_DB, timeout=60)
    stk_conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)

    # 대상: 보통주 코스피/코스닥 종목 (ETF/ETN/리츠/스팩/외국법인 제외)  {code: name}
    universe = {r[0]: r[1] for r in stk_conn.execute("""
        SELECT stock_code, stock_name, market_cap
        FROM stock_universe
        WHERE market IN ('유가증권','코스피','코스닥','KOSDAQ')
          AND kind_stkcert_nm = '보통주'
          AND COALESCE(secugrp_nm, '주권') NOT IN ('외국주권', '주식예탁증권')
          AND LENGTH(stock_code) = 6
        ORDER BY market_cap DESC NULLS LAST
    """).fetchall()}
    logger.info(f'대상 보통주 종목: {len(universe)}개')

    # ── Step 1: employment_company.bizr_no → stock_bizr_no_map 동기화 ──────
    logger.info('[Step 1] employment_company → stock_bizr_no_map 동기화')
    ec_bizr = emp_conn.execute("""
        SELECT DISTINCT ec.stock_code, ec.stock_name, ec.bizr_no
        FROM employment_company ec
        WHERE ec.bizr_no IS NOT NULL AND ec.bizr_no != ''
        ORDER BY ec.stock_code
    """).fetchall()

    synced = 0
    for code, name, bizr in ec_bizr:
        emp_conn.execute("""
            INSERT OR REPLACE INTO stock_bizr_no_map
            (stock_code, stock_name, bizr_no, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (code, name, bizr))
        synced += 1
    emp_conn.commit()
    logger.info(f'  동기화 완료: {synced}개')

    # ── 현재 bizr_no 보유 현황 ────────────────────────────────────────────
    have_bizr = {r[0]: r[1] for r in emp_conn.execute(
        "SELECT stock_code, bizr_no FROM stock_bizr_no_map WHERE bizr_no IS NOT NULL AND bizr_no != ''"
    ).fetchall()}
    logger.info(f'stock_bizr_no_map 현재 보유: {len(have_bizr)}개')

    # 미보유 종목
    missing = [(c, n) for c, n in universe.items() if c not in have_bizr]
    logger.info(f'bizr_no 미보유 종목: {len(missing)}개 → DART/Naver 조회 시작')

    if not missing:
        logger.info('모든 종목 bizr_no 보유 — 조회 불필요')
        stk_conn.close(); emp_conn.close()
        return

    # ── Step 2: DART corp_code 맵 로드 ───────────────────────────────────
    logger.info('[Step 2] DART corp_code 맵 로드')
    corp_map = load_corp_map()

    # ── Step 3: 미보유 종목 API 조회 ─────────────────────────────────────
    logger.info('[Step 3] 미보유 종목 DART 조회')
    found_dart = found_web = 0
    still_missing = []

    total = len(missing)
    for i, (code, name) in enumerate(missing, 1):
        corp_code = corp_map.get(code)
        bizr = None

        # DART company.json (주력)
        if corp_code:
            bizr = dart_bizr_no(corp_code)
            time.sleep(delay)
            if bizr:
                found_dart += 1

        # DART company.json 실패 → 웹 파싱 시도
        if not bizr and corp_code:
            bizr = dart_web_bizr_no(corp_code)
            time.sleep(delay)
            if bizr:
                found_web += 1

        # Naver Finance (최후 수단)
        if not bizr:
            bizr = naver_bizr_no(code)
            if bizr:
                found_web += 1

        if bizr:
            emp_conn.execute("""
                INSERT OR REPLACE INTO stock_bizr_no_map
                (stock_code, stock_name, bizr_no, corp_code, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (code, name, bizr, corp_code or ''))
            emp_conn.commit()
            logger.info(f'  [{i}/{total}] {name}({code}): bizr_no={bizr}')
        else:
            still_missing.append((code, name, corp_code or ''))
            if i % 50 == 0 or i <= 5:
                logger.info(f'  [{i}/{total}] {name}({code}): 미수집')

        if i % 100 == 0:
            logger.info(f'  진행: {i}/{total} | DART={found_dart} 웹={found_web} 미수집={len(still_missing)}')

    # ── 최종 통계 ─────────────────────────────────────────────────────────
    total_have = emp_conn.execute(
        "SELECT COUNT(*) FROM stock_bizr_no_map WHERE bizr_no IS NOT NULL AND bizr_no != ''"
    ).fetchone()[0]

    logger.info(f'\n=== 수집 완료 ===')
    logger.info(f'Step1 동기화: {synced}개')
    logger.info(f'DART API 신규: {found_dart}개')
    logger.info(f'웹 파싱 신규: {found_web}개')
    logger.info(f'최종 보유: {total_have}개 / {len(universe)}개')
    logger.info(f'여전히 미수집: {len(still_missing)}개')

    # ── 미수집 CSV 저장 ───────────────────────────────────────────────────
    if still_missing:
        result_path = os.path.join(_DIR, 'collect_bizr_no_missing.csv')
        with open(result_path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['종목코드', '종목명', 'DART_corp_code', '비고'])
            for code, name, cc in still_missing:
                w.writerow([code, name, cc, '수동 확인 필요'])
        logger.info(f'미수집 목록: {result_path}')

    # ── 신규 bizr_no 확보 종목 중 2026-05 ec 없는 것 출력 ────────────────
    ec_2605 = {r[0] for r in emp_conn.execute(
        "SELECT DISTINCT stock_code FROM employment_company WHERE ym='2026-05'"
    ).fetchall()}
    new_to_collect = [
        (code, name) for code, name in universe.items()
        if code in {r[0] for r in emp_conn.execute(
            "SELECT stock_code FROM stock_bizr_no_map WHERE bizr_no IS NOT NULL AND bizr_no != ''"
        ).fetchall()} and code not in ec_2605
    ]
    logger.info(f'\nbizr_no 확보됐지만 2026-05 미수집: {len(new_to_collect)}개')
    logger.info('→ fetch_employment_insurance.py --force 로 수집 가능')

    emp_conn.close()
    stk_conn.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--only-missing', action='store_true', help='미보유 종목만 조회 (Step1 스킵)')
    ap.add_argument('--delay', type=float, default=0.2, help='API 딜레이(초)')
    args = ap.parse_args()
    run(only_missing=args.only_missing, delay=args.delay)
