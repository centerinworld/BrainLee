"""
📡 경제 지표 데이터 수집기 (Fetchers)
======================================
각종 API/공개데이터를 호출하여 DB에 저장
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .eco_db import (
    SEOUL,
    connect as eco_connect,
    get_latest_value,
    save_indicator_data,
    log_fetch,
)


# ============================================================
# API 키 로딩
# ============================================================

def _get_env(key: str) -> str:
    return os.environ.get(key, "")


def get_bok_api_key() -> str:
    """한국은행 ECOS API 키"""
    return _get_env("BOK_API_KEY")


def get_kosis_api_key() -> str:
    """통계청 KOSIS API 키"""
    return _get_env("KOSIS_API_KEY")


# ============================================================
# 유틸리티
# ============================================================


def _request_json(url: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """JSON API 호출 유틸리티"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; EconomicIndicatorBot/1.0)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] API 호출 실패: {url[:80]}... - {e}")
        return None


def _request_xml(url: str, timeout: int = 30) -> Optional[ET.Element]:
    """XML API 호출 유틸리티"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; EconomicIndicatorBot/1.0)",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return ET.fromstring(resp.read())
    except Exception as e:
        print(f"  [ERROR] XML API 호출 실패: {url[:80]}... - {e}")
        return None


def _today_str() -> str:
    return datetime.now(SEOUL).strftime("%Y-%m-%d")


def _prev_day_str(days: int = 1) -> str:
    return (datetime.now(SEOUL) - timedelta(days=days)).strftime("%Y-%m-%d")


def _calc_change_rate(current: float, previous: Optional[float]) -> Optional[float]:
    """변화율 계산"""
    if previous is not None and previous != 0:
        return ((current - previous) / previous) * 100
    return None


# ============================================================
# 1. 한국은행 ECOS API
# ============================================================
# 문서: https://ecos.bok.or.kr/api/

BOK_BASE_URL = "https://ecos.bok.or.kr/api"


def _bok_stat_search(api_key: str, stat_code: str, start_date: str, end_date: str, 
                     item_code: str = "", cycle: str = "D") -> Optional[List[Dict[str, Any]]]:
    """ECOS 통계조회 API 호출

    Args:
        stat_code: 통계코드 (예: '731Y004' = 원/달러 환율)
        item_code: 항목코드
        cycle: 주기 (D:일, M:월, Q:분기, A:년)
    """
    # ECOS OpenAPI 날짜 규격은 하이픈(-)이 없어야 함
    sd = start_date.replace("-", "")
    ed = end_date.replace("-", "")

    # 주기에 맞춰 ECOS 날짜 형식 변환
    if cycle == "Q":
        # YYYYMMDD -> YYYYQn
        def _to_quarter(d_str):
            if len(d_str) >= 6:
                year = d_str[:4]
                m = int(d_str[4:6])
                q = (m - 1) // 3 + 1
                return f"{year}Q{q}"
            return d_str[:4] + "Q1"
        sd = _to_quarter(sd)
        ed = _to_quarter(ed)
    elif cycle == "A" or cycle == "Y":
        cycle = "A"  # ECOS 연간 표기는 'A'
        sd = sd[:4]
        ed = ed[:4]
    elif cycle == "M":
        sd = sd[:6]
        ed = ed[:6]
    elif cycle == "D":
        sd = sd[:8]
        ed = ed[:8]

    url = f"{BOK_BASE_URL}/StatisticSearch/{api_key}/json/kr/1/100/{stat_code}/{cycle}/{sd}/{ed}/{item_code}"
    data = _request_json(url)
    
    if not data:
        return None
    
    try:
        rows = data.get("StatisticSearch", {}).get("row", [])
        return rows if isinstance(rows, list) else []
    except Exception:
        return None


def fetch_bok_exchange_rates(conn: sqlite3.Connection) -> int:
    """한국은행 환율 데이터 수집

    ECOS 통계코드:
    - 731Y001: 주요국 통화의 대원화환율 (일일환율 - USD, JPY, CNY, EUR 모두 포함)
    """
    api_key = get_bok_api_key()
    if not api_key:
        print("  [SKIP] BOK API 키가 설정되지 않음")
        return 0

    today = _today_str()
    prev = _prev_day_str(30)  # 최근 30일 범위

    indicators = [
        ("EXCHANGE_USD", "731Y001", "0000001"),  # 원/달러
        ("EXCHANGE_JPY", "731Y001", "0000002"),  # 원/100엔
        ("EXCHANGE_CNY", "731Y001", "0000053"),  # 원/위안
        ("EXCHANGE_EUR", "731Y001", "0000003"),  # 원/유로
    ]

    total_count = 0
    for indicator_code, stat_code, item_code in indicators:
        print(f"  환율 수집: {indicator_code}...")
        rows = _bok_stat_search(api_key, stat_code, prev, today, item_code, "D")
        if not rows:
            print(f"    → 데이터 없음")
            continue

        count = 0
        for row in rows:
            date = row.get("TIME", "")
            value_str = row.get("DATA_VALUE", "")
            if not date or not value_str:
                continue
            
            # 날짜 포맷 변환 (YYYYMMDD -> YYYY-MM-DD)
            if len(date) == 8:
                date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
            
            try:
                value = float(value_str)
            except ValueError:
                continue

            # 이전값 조회
            prev_data = get_latest_value(conn, indicator_code)
            prev_value = prev_data["value"] if prev_data else None
            change_rate = _calc_change_rate(value, prev_value) if prev_data and prev_data["date"] != date else None

            save_indicator_data(
                conn, indicator_code, date, value, prev_value, change_rate,
                source_ref=f"BOK_{stat_code}_{item_code}",
                raw_json=json.dumps(row, ensure_ascii=False),
            )
            count += 1

        if count > 0:
            print(f"    → {count}건 저장")
            total_count += count

    log_fetch(conn, "BOK_EXCHANGE", "success", items_count=total_count)
    return total_count


def fetch_bok_interest_rates(conn: sqlite3.Connection) -> int:
    """한국은행 금리 데이터 수집

    ECOS 통계코드:
    - 722Y001: 기준금리 (M:월)
    - 817Y002: 시장금리 일별 (국고채 3년, 국고채 10년, 콜금리)
    """
    api_key = get_bok_api_key()
    if not api_key:
        return 0

    today = _today_str()
    prev = _prev_day_str(30)

    indicators = [
        ("BASE_RATE", "722Y001", "0101000", "M"),    # 기준금리 (월)
        ("BOND_YIELD_3Y", "817Y002", "010200000", "D"), # 국고채 3년
        ("BOND_YIELD_10Y", "817Y002", "010210000", "D"), # 국고채 10년
        ("CALL_RATE", "817Y002", "010101000", "D"),     # 콜금리
    ]

    total_count = 0
    for indicator_code, stat_code, item_code, cycle in indicators:
        print(f"  금리 수집: {indicator_code}...")
        rows = _bok_stat_search(api_key, stat_code, prev, today, item_code, cycle)
        if not rows:
            print(f"    → 데이터 없음")
            continue

        count = 0
        for row in rows:
            date = row.get("TIME", "")
            value_str = row.get("DATA_VALUE", "")
            if not date or not value_str:
                continue

            # ECOS 날짜 포맷: YYYYMMDD (일), YYYYMM (월)
            if len(date) == 8:
                date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
            elif len(date) == 6:
                date = f"{date[:4]}-{date[4:6]}-01"

            try:
                value = float(value_str)
            except ValueError:
                continue

            prev_data = get_latest_value(conn, indicator_code)
            prev_value = prev_data["value"] if prev_data else None
            change_rate = _calc_change_rate(value, prev_value) if prev_data and prev_data["date"] != date else None

            save_indicator_data(
                conn, indicator_code, date, value, prev_value, change_rate,
                source_ref=f"BOK_{stat_code}_{item_code}",
                raw_json=json.dumps(row, ensure_ascii=False),
            )
            count += 1

        if count > 0:
            print(f"    → {count}건 저장")
            total_count += count

    log_fetch(conn, "BOK_INTEREST", "success", items_count=total_count)
    return total_count


def fetch_bok_cpi(conn: sqlite3.Connection) -> int:
    """소비자물가 데이터 수집

    ECOS 통계코드:
    - 901Y009: 소비자물가지수 (총지수)
    - 901Y010: 소비자물가지수 특수분류 (농산물·석유류 제외지수)
    """
    api_key = get_bok_api_key()
    if not api_key:
        return 0

    today = _today_str()
    # 2년 범위로 늘려 1년 전 데이터를 기준으로 전년동월대비 상승률(YoY)을 계산할 수 있게 함
    prev = (datetime.now(SEOUL) - timedelta(days=365*2)).strftime("%Y-%m-%d")

    indicators = [
        ("CPI_CHANGE", "901Y009", "0", "M"),    # 소비자물가 (총지수)
        ("CORE_CPI", "901Y010", "QB", "M"),      # 근원물가 (농산물 및 석유류 제외지수)
    ]

    total_count = 0
    for indicator_code, stat_code, item_code, cycle in indicators:
        print(f"  물가 수집: {indicator_code}...")
        rows = _bok_stat_search(api_key, stat_code, prev, today, item_code, cycle)
        if not rows:
            print(f"    → 데이터 없음")
            continue

        count = 0
        for row in rows:
            date = row.get("TIME", "")
            value_str = row.get("DATA_VALUE", "")
            if not date or len(date) < 6:
                continue

            if len(date) == 6:
                date = f"{date[:4]}-{date[4:6]}-01"

            try:
                value = float(value_str)
            except ValueError:
                continue

            # 전년동월대비(YoY) 상승률 계산을 위해 1년 전의 지수 데이터 조회
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            one_year_ago_date = (date_obj - timedelta(days=365)).strftime("%Y-%m-%d")
            one_year_ago_data = conn.execute(
                "SELECT value FROM indicator_data WHERE indicator_code = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                (indicator_code, one_year_ago_date)
            ).fetchone()
            one_year_ago_value = one_year_ago_data[0] if one_year_ago_data else None
            change_rate = _calc_change_rate(value, one_year_ago_value) if one_year_ago_value is not None else None

            save_indicator_data(
                conn, indicator_code, date, value, one_year_ago_value, change_rate,
                source_ref=f"BOK_{stat_code}_{item_code}",
                raw_json=json.dumps(row, ensure_ascii=False),
            )
            count += 1

        if count > 0:
            print(f"    → {count}건 저장")
            total_count += count

    log_fetch(conn, "BOK_CPI", "success", items_count=total_count)
    return total_count


def fetch_bok_trade(conn: sqlite3.Connection) -> int:
    """무역 데이터 수집

    ECOS 통계코드:
    - 301Y013: 국제수지 (상품수출, 상품수입 포함)
    """
    api_key = get_bok_api_key()
    if not api_key:
        return 0

    today = _today_str()
    prev = (datetime.now(SEOUL) - timedelta(days=365)).strftime("%Y-%m-%d")

    indicators = [
        ("EXPORT_AMOUNT", "301Y013", "110000"),  # 상품수출
        ("IMPORT_AMOUNT", "301Y013", "120000"),  # 상품수입(FOB)
    ]

    total_count = 0
    
    # 수출/수입액 수집
    for indicator_code, stat_code, item_code in indicators:
        print(f"  무역 수집: {indicator_code}...")
        rows = _bok_stat_search(api_key, stat_code, prev, today, item_code, "M")
        if not rows:
            continue

        count = 0
        for row in rows:
            date = row.get("TIME", "")
            value_str = row.get("DATA_VALUE", "")
            if not date or len(date) < 6:
                continue
            if len(date) == 6:
                date = f"{date[:4]}-{date[4:6]}-01"

            try:
                value = float(value_str)
            except ValueError:
                continue

            prev_data = get_latest_value(conn, indicator_code)
            prev_value = prev_data["value"] if prev_data else None
            change_rate = _calc_change_rate(value, prev_value) if prev_data else None

            save_indicator_data(
                conn, indicator_code, date, value, prev_value, change_rate,
                source_ref=f"BOK_{stat_code}_{item_code}",
                raw_json=json.dumps(row, ensure_ascii=False),
            )
            count += 1

        if count > 0:
            print(f"    → {count}건 저장")
            total_count += count

    # 무역수지 = 수출 - 수입 (월별 계산)
    print(f"  무역수지 계산...")
    export_data = conn.execute(
        "SELECT date, value FROM indicator_data WHERE indicator_code = 'EXPORT_AMOUNT' ORDER BY date DESC"
    ).fetchall()
    import_data = conn.execute(
        "SELECT date, value FROM indicator_data WHERE indicator_code = 'IMPORT_AMOUNT' ORDER BY date DESC"
    ).fetchall()

    export_map = {row["date"]: row["value"] for row in export_data}
    import_map = {row["date"]: row["value"] for row in import_data}

    count = 0
    for date in export_map:
        if date in import_map:
            balance = export_map[date] - import_map[date]
            prev_bal = conn.execute(
                "SELECT value FROM indicator_data WHERE indicator_code = 'TRADE_BALANCE' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            prev_value = prev_bal["value"] if prev_bal else None
            change_rate = _calc_change_rate(balance, prev_value) if prev_bal else None

            save_indicator_data(
                conn, "TRADE_BALANCE", date, balance, prev_value, change_rate,
                source_ref="BOK_CALCULATED",
                raw_json="",
            )
            count += 1

    if count > 0:
        total_count += count

    log_fetch(conn, "BOK_TRADE", "success", items_count=total_count)
    return total_count


def fetch_bok_gdp(conn: sqlite3.Connection) -> int:
    """GDP 및 GNI 데이터 수집

    ECOS 통계코드:
    - 200Y102: 주요지표(분기지표) -> 10111: 국내총생산(GDP)(실질, 계절조정, 전기비) - Q
    - 200Y101: 주요지표(연간지표) -> 1010601: 1인당 국민총소득(명목, 달러표시) - A
    """
    api_key = get_bok_api_key()
    if not api_key:
        return 0

    today = _today_str()
    prev = "2010-01-01"  # 넉넉히 범위 설정

    indicators = [
        ("GDP_GROWTH", "200Y102", "10111", "Q"),
        ("GNI_PER_CAPITA", "200Y101", "1010601", "A"),
    ]

    total_count = 0
    for indicator_code, stat_code, item_code, cycle in indicators:
        print(f"  BOK 수집: {indicator_code}...")
        rows = _bok_stat_search(api_key, stat_code, prev, today, item_code, cycle)
        if not rows:
            print(f"    → 데이터 없음")
            continue

        count = 0
        for row in rows:
            date = row.get("TIME", "")
            value_str = row.get("DATA_VALUE", "")
            if not date or not value_str:
                continue

            # 날짜 포맷 변환
            if cycle == "Q":
                # '2025Q1' -> '2025-Q1'
                if len(date) == 6 and "Q" in date:
                    date = f"{date[:4]}-{date[4:]}"
                else:
                    # '202501' -> '2025-Q1'
                    quarter_map = {"01": "Q1", "04": "Q2", "07": "Q3", "10": "Q4"}
                    q = quarter_map.get(date[4:6], date[4:6])
                    date = f"{date[:4]}-{q}"
            elif cycle == "A":
                # '2025' -> '2025-12-31'
                date = f"{date[:4]}-12-31"

            try:
                value = float(value_str)
            except ValueError:
                continue

            prev_data = get_latest_value(conn, indicator_code)
            prev_value = prev_data["value"] if prev_data else None
            change_rate = _calc_change_rate(value, prev_value) if prev_data and prev_data["date"] != date else None

            save_indicator_data(
                conn, indicator_code, date, value, prev_value, change_rate,
                source_ref=f"BOK_{stat_code}_{item_code}",
                raw_json=json.dumps(row, ensure_ascii=False),
            )
            count += 1

        if count > 0:
            print(f"    → {count}건 저장")
            total_count += count

    log_fetch(conn, "BOK_GDP_GNI", "success", items_count=total_count)
    return total_count


def fetch_bok_foreign_reserve(conn: sqlite3.Connection) -> int:
    """외환보유액 수집

    ECOS 통계코드: 732Y001 (외환보유액 총합계)
    """
    api_key = get_bok_api_key()
    if not api_key:
        return 0

    today = _today_str()
    prev = (datetime.now(SEOUL) - timedelta(days=365)).strftime("%Y-%m-%d")

    print(f"  외환보유액 수집...")
    rows = _bok_stat_search(api_key, "732Y001", prev, today, "99", "M")
    if not rows:
        return 0

    count = 0
    for row in rows:
        date = row.get("TIME", "")
        value_str = row.get("DATA_VALUE", "")
        if not date or len(date) < 6:
            continue
        if len(date) == 6:
            date = f"{date[:4]}-{date[4:6]}-01"

        try:
            # ECOS 원본값은 천달러 단위이므로, 백만달러 단위로 환산하여 저장
            value = float(value_str) / 1000.0
        except ValueError:
            continue

        prev_data = get_latest_value(conn, "FOREIGN_RESERVE")
        prev_value = prev_data["value"] if prev_data else None
        change_rate = _calc_change_rate(value, prev_value) if prev_data else None

        save_indicator_data(
            conn, "FOREIGN_RESERVE", date, value, prev_value, change_rate,
            source_ref="BOK_732Y001_99",
            raw_json=json.dumps(row, ensure_ascii=False),
        )
        count += 1

    if count > 0:
        print(f"    → {count}건 저장")
    log_fetch(conn, "BOK_FOREIGN_RESERVE", "success", items_count=count)
    return count


# ============================================================
# 2. 한국거래소 KRX (공개 데이터)
# ============================================================

def fetch_krx_stock_indices(conn: sqlite3.Connection) -> int:
    """KRX 주가지수 수집

    한국거래소 정보데이터시스템 공개 API 사용
    """
    today = _today_str()
    prev = _prev_day_str(7)

    indicators = [
        ("KOSPI", "KOSPI"),
        ("KOSDAQ", "KOSDAQ"),
    ]

    total_count = 0
    
    # KRX 공개 API (JSONP 제거)
    for indicator_code, index_name in indicators:
        print(f"  주가 수집: {indicator_code}...")
        
        # KRX 정보데이터시스템 API
        url = (
            f"http://www.krx.co.kr/porhq/query/stkIdxDdPrcPor.do"
            f"?mkTpCd=1&isuCd=&isuTpCd=2&fwkTpCd=0&grcTpCd=&"
            f"strtDd={prev.replace('-', '')}&endDd={today.replace('-', '')}&"
            f"idxTpCd=KOSPI&parm=each&rlt=1&lang=ko&"
            f"_={int(datetime.now().timestamp() * 1000)}"
        )
        
        if indicator_code == "KOSDAQ":
            url = url.replace("idxTpCd=KOSPI", "idxTpCd=KOSDAQ")

        data = _request_json(url)
        if not data:
            # 대체: Naver 금융 API
            try:
                naver_url = (
                    f"https://query1.finance.yahoo.com/v8/finance/chart/"
                    f"{'^KS11' if indicator_code == 'KOSPI' else '^KQ11'}"
                    f"?period1={int((datetime.now(SEOUL)-timedelta(days=7)).timestamp())}"
                    f"&period2={int(datetime.now(SEOUL).timestamp())}"
                    f"&interval=1d"
                )
                yahoo_data = _request_json(naver_url)
                if yahoo_data:
                    result = yahoo_data.get("chart", {}).get("result", [{}])[0]
                    timestamps = result.get("timestamp", [])
                    quotes = result.get("indicators", {}).get("quote", [{}])[0]
                    closes = quotes.get("close", [])
                    
                    count = 0
                    for ts, close in zip(timestamps, closes):
                        if close is None:
                            continue
                        date = datetime.fromtimestamp(ts, tz=SEOUL).strftime("%Y-%m-%d")
                        prev_data = get_latest_value(conn, indicator_code)
                        prev_value = prev_data["value"] if prev_data else None
                        change_rate = _calc_change_rate(close, prev_value) if prev_data and prev_data["date"] != date else None

                        save_indicator_data(
                            conn, indicator_code, date, close, prev_value, change_rate,
                            source_ref="YAHOO_FINANCE",
                            raw_json=json.dumps({"close": close}, ensure_ascii=False),
                        )
                        count += 1
                    
                    if count > 0:
                        print(f"    → Yahoo Finance: {count}건 저장")
                        total_count += count
                        log_fetch(conn, f"KRX_{indicator_code}", "success", items_count=count)
                        continue
            except Exception as e:
                print(f"    → Yahoo Finance 실패: {e}")
            
            print(f"    → 데이터 수집 실패")
            log_fetch(conn, f"KRX_{indicator_code}", "failed", error_message="API 응답 없음")
            continue

        try:
            rows = data.get("result", [])
            count = 0
            for row in rows:
                date = row.get("trdDd", "")
                if len(date) == 8:
                    date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
                close_str = row.get("clsPrc", "").replace(",", "")
                if not date or not close_str:
                    continue
                try:
                    value = float(close_str)
                except ValueError:
                    continue

                prev_data = get_latest_value(conn, indicator_code)
                prev_value = prev_data["value"] if prev_data else None
                change_rate = _calc_change_rate(value, prev_value) if prev_data and prev_data["date"] != date else None

                save_indicator_data(
                    conn, indicator_code, date, value, prev_value, change_rate,
                    source_ref="KRX",
                    raw_json=json.dumps(row, ensure_ascii=False),
                )
                count += 1

            if count > 0:
                print(f"    → {count}건 저장")
                total_count += count
                log_fetch(conn, f"KRX_{indicator_code}", "success", items_count=count)
        except Exception as e:
            print(f"    → 파싱 실패: {e}")
            log_fetch(conn, f"KRX_{indicator_code}", "failed", error_message=str(e))

    return total_count


# ============================================================
# 3. 통계청 KOSIS API
# ============================================================

KOSIS_BASE_URL = "https://kosis.kr/openapi"


def _kosis_request(api_key: str, org_id: str, tbl_id: str, 
                   start_date: str, end_date: str) -> Optional[List[Dict[str, Any]]]:
    """KOSIS API 호출"""
    url = (
        f"{KOSIS_BASE_URL}/statisticsData/statisticsData01.json"
        f"?apiKey={api_key}"
        f"&format=json"
        f"&jsonVD=Y"
        f"&userStatsId=eco"
        f"&prdSe=M"
        f"&startPrdDe={start_date}"
        f"&endPrdDe={end_date}"
        f"&orgId={org_id}"
        f"&tblId={tbl_id}"
    )
    return _request_json(url)


def fetch_bok_employment(conn: sqlite3.Connection) -> int:
    """한국은행 ECOS 고용 데이터 수집 (실업률, 고용률)

    ECOS 통계코드:
    - 901Y027: 경제활동인구 -> I61BC: 실업률 (M)
    - 901Y027: 경제활동인구 -> I61E: 고용률 (M)
    """
    api_key = get_bok_api_key()
    if not api_key:
        return 0

    today = _today_str()
    prev = (datetime.now(SEOUL) - timedelta(days=365)).strftime("%Y-%m-%d")

    indicators = [
        ("UNEMPLOYMENT_RATE", "901Y027", "I61BC"),  # 실업률 (%)
        ("EMPLOYMENT_RATE", "901Y027", "I61E"),     # 고용률 (%)
    ]

    total_count = 0
    for indicator_code, stat_code, item_code in indicators:
        print(f"  고용 수집: {indicator_code}...")
        rows = _bok_stat_search(api_key, stat_code, prev, today, item_code, "M")
        if not rows:
            print(f"    → 데이터 없음")
            continue

        count = 0
        for row in rows:
            date = row.get("TIME", "")
            value_str = row.get("DATA_VALUE", "")
            if not date or len(date) < 6:
                continue
            if len(date) == 6:
                date = f"{date[:4]}-{date[4:6]}-01"

            try:
                value = float(value_str)
            except ValueError:
                continue

            prev_data = get_latest_value(conn, indicator_code)
            prev_value = prev_data["value"] if prev_data else None
            change_rate = _calc_change_rate(value, prev_value) if prev_data and prev_data["date"] != date else None

            save_indicator_data(
                conn, indicator_code, date, value, prev_value, change_rate,
                source_ref=f"BOK_{stat_code}_{item_code}",
                raw_json=json.dumps(row, ensure_ascii=False),
            )
            count += 1

        if count > 0:
            print(f"    → {count}건 저장")
            total_count += count

    log_fetch(conn, "BOK_EMPLOYMENT", "success", items_count=total_count)
    return total_count


# ============================================================
# 4. 기획재정부 경제동향 (열린재정)
# ============================================================

def fetch_moef_trends(conn: sqlite3.Connection) -> int:
    """기획재정부 '열린재정' 월간 경제동향"""
    url = "https://www.openfiscaldata.go.kr/portal/service/openDataApi.do"
    # 공개 RSS/API 부재시 대체: 
    # 기획재정부 보도자료 RSS 활용
    rss_url = "https://www.moef.go.kr/rss/reform.xml"
    
    print(f"  기재부 경제동향 수집...")
    
    try:
        root = _request_xml(rss_url)
        if root is None:
            log_fetch(conn, "MOEF", "failed", error_message="RSS 수집 실패")
            return 0

        items = []
        for item in root.iter("item"):
            title = item.findtext("title", "")
            if "경제동향" in title or "월간" in title:
                items.append(item)

        log_fetch(conn, "MOEF", "success", items_count=len(items))
        return len(items)
    except Exception as e:
        log_fetch(conn, "MOEF", "failed", error_message=str(e))
        return 0


# ============================================================
# 5. 통합 수집 실행기
# ============================================================


def fetch_all(conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """모든 데이터 수집 실행"""
    close_conn = False
    if conn is None:
        conn = eco_connect()
        close_conn = True

    print("\n" + "=" * 50)
    print("📊 경제 지표 데이터 수집 시작")
    print(f"🕐 {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    results: Dict[str, int] = {}

    # 1. 환율
    print("\n💱 1. 환율 데이터 수집...")
    results["BOK_EXCHANGE"] = fetch_bok_exchange_rates(conn)

    # 2. 금리
    print("\n🏦 2. 금리 데이터 수집...")
    results["BOK_INTEREST"] = fetch_bok_interest_rates(conn)

    # 3. 물가
    print("\n💰 3. 물가 데이터 수집...")
    results["BOK_CPI"] = fetch_bok_cpi(conn)

    # 4. 무역
    print("\n🚢 4. 무역 데이터 수집...")
    results["BOK_TRADE"] = fetch_bok_trade(conn)

    # 5. GDP
    print("\n📈 5. GDP 데이터 수집...")
    results["BOK_GDP"] = fetch_bok_gdp(conn)

    # 6. 주식
    print("\n📊 6. 주가지수 수집...")
    results["KRX"] = fetch_krx_stock_indices(conn)

    # 7. 고용
    print("\n👔 7. 고용 통계 수집...")
    results["BOK_EMPLOYMENT"] = fetch_bok_employment(conn)

    # 8. 외환보유액
    print("\n🌍 8. 외환보유액 수집...")
    results["BOK_FOREIGN_RESERVE"] = fetch_bok_foreign_reserve(conn)

    # 9. 기재부 동향
    print("\n📋 9. 기재부 경제동향 수집...")
    results["MOEF"] = fetch_moef_trends(conn)

    total = sum(results.values())
    print("\n" + "=" * 50)
    print(f"✅ 수집 완료: 총 {total}건")
    print("=" * 50)

    if close_conn:
        conn.close()

    return results


# ============================================================
# 개별 수집 실행 (테스트용)
# ============================================================


if __name__ == "__main__":
    fetch_all()
