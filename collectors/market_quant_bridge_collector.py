"""
Bridge selected stock-market quant indicators into Global Intelligence.

This collector does not fetch new external data. It reuses curated
stock_dashboard quant_major_indicator_* series and only exposes indicators that
are not already represented in global_macro_data.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"


@dataclass(frozen=True)
class QuantBridgeSpec:
    code: str
    name: str
    name_en: str
    subcategory: str
    unit: str
    source_code: str
    frequency: str
    importance: int
    indicator_key: str
    series_name: str


SPECS = [
    # Semiconductors: price/proxy, exports, equipment, materials.
    QuantBridgeSpec("MQ_DRAM_PROXY", "D램 가격 대리지표(반도체 수출단가)", "DRAM Price Proxy: Korea Semiconductor Export Unit Value", "SEMICONDUCTOR", "USD/kg", "epic:semi:dram_proxy", "MONTHLY", 3, "epic:semi:dram_proxy", "반도체 수출단가"),
    QuantBridgeSpec("MQ_MEMORY_SEMI_EXPORT", "메모리 반도체 수출액", "Memory Semiconductor Exports", "SEMICONDUCTOR", "백만달러", "public:23:4", "MONTHLY", 3, "public:23:4", "반도체_메모리_수출액"),
    QuantBridgeSpec("MQ_MEMORY_SEMI_UNIT_EXPORT", "메모리 반도체 수출단가", "Memory Semiconductor Export Unit Value", "SEMICONDUCTOR", "USD/kg", "public:23:4", "MONTHLY", 3, "public:23:4", "반도체_메모리_수출단가"),
    QuantBridgeSpec("MQ_SYSTEM_SEMI_EXPORT", "시스템 반도체 수출액", "System Semiconductor Exports", "SEMICONDUCTOR", "백만달러", "public:23:5", "MONTHLY", 2, "public:23:5", "반도체_시스템_수출액"),
    QuantBridgeSpec("MQ_SEMI_EQUIP_EXPORT", "반도체 제조장비 수출액", "Semiconductor Equipment Exports", "SEMICONDUCTOR", "백만달러", "public:23:6", "MONTHLY", 2, "public:23:6", "반도체_제조장비_수출액"),
    QuantBridgeSpec("MQ_SEMI_GAS_EXPORT", "반도체 특수가스 수출액", "Semiconductor Specialty Gas Exports", "SEMICONDUCTOR", "백만달러", "public:23:32", "MONTHLY", 2, "public:23:32", "반도체_특수가스_수출액"),
    QuantBridgeSpec("MQ_SEMI_PCB_EXPORT", "반도체 기판/PCB 수출액", "Semiconductor Substrate/PCB Exports", "SEMICONDUCTOR", "백만달러", "public:23:40", "MONTHLY", 2, "public:23:40", "반도체_기판PCB_수출액"),
    # Battery, shipbuilding, power equipment.
    QuantBridgeSpec("MQ_LI_ION_BATTERY_EXPORT", "이차전지 리튬이온 수출액", "Li-ion Battery Exports", "BATTERY", "백만달러", "public:23:3", "MONTHLY", 3, "public:23:3", "이차전지_리튬이온_수출액"),
    QuantBridgeSpec("MQ_LI_ION_BATTERY_UNIT_EXPORT", "이차전지 리튬이온 수출단가", "Li-ion Battery Export Unit Value", "BATTERY", "USD/kg", "public:23:3", "MONTHLY", 2, "public:23:3", "이차전지_리튬이온_수출단가"),
    QuantBridgeSpec("MQ_SHIP_EXPORT", "조선 상선 수출액", "Commercial Ship Exports", "SHIPBUILDING", "백만달러", "public:23:7", "MONTHLY", 3, "public:23:7", "조선_상선_수출액"),
    QuantBridgeSpec("MQ_POWER_EQUIP_EXPORT", "전력기기 수출액", "Power Equipment Exports", "POWER_EQUIPMENT", "백만달러", "public:23:36", "MONTHLY", 3, "public:23:36", "전력기기_수출액"),
    QuantBridgeSpec("MQ_AEROSPACE_DEFENSE_EXPORT", "항공/방산 수출액", "Aerospace and Defense Exports", "AEROSPACE_DEFENSE", "백만달러", "public:23:37", "MONTHLY", 3, "public:23:37", "항공방산_수출액"),
    # Shipping, steel/raw materials, energy market indicators.
    QuantBridgeSpec("MQ_BDI", "BDI 건화물 운임지수", "Baltic Dry Index", "SHIPPING", "지수", "epic:7:14", "WEEKLY", 3, "epic:7:14", "baltic_dry_index_bdi"),
    QuantBridgeSpec("MQ_BCI", "BCI 케이프사이즈 운임지수", "Baltic Capesize Index", "SHIPPING", "지수", "epic:7:15", "WEEKLY", 2, "epic:7:15", "baltic_capesize_index_bci"),
    QuantBridgeSpec("MQ_BPI", "BPI 파나막스 운임지수", "Baltic Panamax Index", "SHIPPING", "지수", "epic:7:16", "WEEKLY", 2, "epic:7:16", "baltic_panamax_index_bpi"),
    QuantBridgeSpec("MQ_BSI", "BSI 수프라막스 운임지수", "Baltic Supramax Index", "SHIPPING", "지수", "epic:7:17", "WEEKLY", 2, "epic:7:17", "baltic_supramax_index_bsi"),
    QuantBridgeSpec("MQ_IRON_ORE", "철광석 수입가격", "Iron Ore CFR Spot Price", "STEEL_RAW_MATERIAL", "$/dmtu", "epic:1:37", "MONTHLY", 3, "epic:1:37", "iron_ore_cfr_spot_usd_dmtu"),
    QuantBridgeSpec("MQ_HRC_STEEL_PROXY", "열연강판 가격 대리지표", "HRC Steel Price Proxy", "STEEL", "USD/short_ton", "epic:1:28_proxy", "MONTHLY", 2, "epic:1:28_proxy", "US_HRC_monthly_avg"),
    QuantBridgeSpec("MQ_NEWCASTLE_COAL", "뉴캐슬 유연탄 가격", "Newcastle Coal Price", "ENERGY", "USD/ton", "epic:4:96", "MONTHLY", 2, "epic:4:96", "newcastle_coal_monthly_avg"),
    QuantBridgeSpec("MQ_KPX_SMP", "계통한계가격 SMP", "Korea System Marginal Price", "POWER", "원/kWh", "epic:6:18", "MONTHLY", 2, "epic:6:18", "integrated_smp_krw_per_kwh"),
    QuantBridgeSpec("MQ_US_RIG_COUNT", "미국 원유/가스 리그 수", "US Oil and Gas Rotary Rig Count", "ENERGY", "개", "epic:19:50", "MONTHLY", 2, "epic:19:50", "us_crude_oil_and_natural_gas_rotary_rigs"),
    # Market internals: breadth, volume expansion, 52-week highs/lows.
    QuantBridgeSpec("MQ_KOSPI_ADVANCE_RATIO", "KOSPI 상승종목비율", "KOSPI Advance Ratio", "MARKET_BREADTH", "%", "public:21:1", "DAILY", 3, "public:21:1", "상승종목비율"),
    QuantBridgeSpec("MQ_KOSDAQ_ADVANCE_RATIO", "KOSDAQ 상승종목비율", "KOSDAQ Advance Ratio", "MARKET_BREADTH", "%", "public:21:2", "DAILY", 3, "public:21:2", "상승종목비율"),
    QuantBridgeSpec("MQ_KOSPI_MEDIAN_RETURN", "KOSPI 종목 중앙수익률", "KOSPI Median Stock Return", "MARKET_BREADTH", "%", "public:21:1", "DAILY", 2, "public:21:1", "중앙수익률"),
    QuantBridgeSpec("MQ_KOSDAQ_MEDIAN_RETURN", "KOSDAQ 종목 중앙수익률", "KOSDAQ Median Stock Return", "MARKET_BREADTH", "%", "public:21:2", "DAILY", 2, "public:21:2", "중앙수익률"),
    QuantBridgeSpec("MQ_KOSPI_NEW_HIGH_LOW_SPREAD", "KOSPI 52주 신고가-신저가 스프레드", "KOSPI 52w High-Low Spread", "MARKET_BREADTH", "%", "public:21:7", "DAILY", 3, "public:21:7", "신고신저스프레드"),
    QuantBridgeSpec("MQ_KOSDAQ_NEW_HIGH_LOW_SPREAD", "KOSDAQ 52주 신고가-신저가 스프레드", "KOSDAQ 52w High-Low Spread", "MARKET_BREADTH", "%", "public:21:8", "DAILY", 3, "public:21:8", "신고신저스프레드"),
    QuantBridgeSpec("MQ_KOSPI_VOLUME_TRIPLE_COUNT", "KOSPI 거래량 3배 종목수", "KOSPI 3x Volume Stock Count", "MARKET_VOLUME", "종목", "public:21:3", "DAILY", 2, "public:21:3", "거래량3배종목수"),
    QuantBridgeSpec("MQ_KOSDAQ_VOLUME_TRIPLE_COUNT", "KOSDAQ 거래량 3배 종목수", "KOSDAQ 3x Volume Stock Count", "MARKET_VOLUME", "종목", "public:21:4", "DAILY", 2, "public:21:4", "거래량3배종목수"),
    QuantBridgeSpec("MQ_KOSPI_TRADING_VALUE", "KOSPI 총거래대금", "KOSPI Total Trading Value", "MARKET_VOLUME", "억원", "public:21:3", "DAILY", 2, "public:21:3", "총거래대금"),
    QuantBridgeSpec("MQ_KOSDAQ_TRADING_VALUE", "KOSDAQ 총거래대금", "KOSDAQ Total Trading Value", "MARKET_VOLUME", "억원", "public:21:4", "DAILY", 2, "public:21:4", "총거래대금"),
    # Liquidity, investor flow, short interest, and program trading.
    QuantBridgeSpec("MQ_CUSTOMER_DEPOSIT", "투자자 예탁금", "Customer Deposits", "MARKET_LIQUIDITY", "억원", "epic:20:99", "MONTHLY", 3, "epic:20:99", "customer_deposit_100m"),
    QuantBridgeSpec("MQ_CREDIT_BALANCE", "신용공여 잔고", "Margin Credit Balance", "MARKET_LIQUIDITY", "억원", "epic:20:99", "MONTHLY", 3, "epic:20:99", "credit_balance_100m"),
    QuantBridgeSpec("MQ_KIWOOM_AVG_CREDIT_RATIO", "평균 신용잔고비율", "Average Credit Balance Ratio", "MARKET_LIQUIDITY", "%", "public:20:106", "MONTHLY", 2, "public:20:106", "kiwoom_avg_credit_ratio"),
    QuantBridgeSpec("MQ_FOREIGN_NET_BUY_QTY", "외국인 순매수 총량", "Foreign Net Buy Quantity", "INVESTOR_FLOW", "주", "public:20:107", "DAILY", 3, "public:20:107", "foreign_net_buy_qty_sum"),
    QuantBridgeSpec("MQ_INSTITUTION_NET_BUY_QTY", "기관 순매수 총량", "Institution Net Buy Quantity", "INVESTOR_FLOW", "주", "public:20:107", "DAILY", 3, "public:20:107", "institution_net_buy_qty_sum"),
    QuantBridgeSpec("MQ_INDIVIDUAL_NET_BUY_QTY", "개인 순매수 총량", "Individual Net Buy Quantity", "INVESTOR_FLOW", "주", "public:20:107", "DAILY", 2, "public:20:107", "individual_net_buy_qty_sum"),
    QuantBridgeSpec("MQ_SHORT_SELL_QTY", "공매도 수량 총합", "Short Sell Quantity", "SHORT_SELL", "주", "public:20:108", "DAILY", 3, "public:20:108", "short_sell_qty_sum"),
    QuantBridgeSpec("MQ_BORROW_BALANCE_QTY", "대차잔고 수량 총합", "Borrow Balance Quantity", "SHORT_SELL", "주", "public:20:108", "DAILY", 3, "public:20:108", "borrow_balance_qty_sum"),
    QuantBridgeSpec("MQ_LENDING_BALANCE", "월간 대차잔고 금액", "Monthly Lending Balance", "SHORT_SELL", "억원", "public:20:108", "MONTHLY", 2, "public:20:108", "monthly_lending_balance_100m"),
    QuantBridgeSpec("MQ_KOSPI_PROGRAM_NET_BUY", "KOSPI 프로그램 순매수", "KOSPI Program Net Buy", "PROGRAM_TRADING", "억원", "public:21:5", "DAILY", 3, "public:21:5", "kiwoom_KOSPI_program_net_buy_100m"),
    QuantBridgeSpec("MQ_KOSDAQ_PROGRAM_NET_BUY", "KOSDAQ 프로그램 순매수", "KOSDAQ Program Net Buy", "PROGRAM_TRADING", "억원", "public:21:5", "DAILY", 3, "public:21:5", "kiwoom_KOSDAQ_program_net_buy_100m"),
    QuantBridgeSpec("MQ_PROGRAM_STOCK_CONCENTRATION", "종목 프로그램 상위10 집중도", "Program Trading Top10 Concentration", "PROGRAM_TRADING", "%", "public:21:6", "DAILY", 2, "public:21:6", "kiwoom_KRX_program_top10_abs_concentration_pct"),
]


def _normalize_date(period: str) -> str | None:
    period = (period or "").strip()
    if not period:
        return None
    if len(period) == 10 and period[4] == "-" and period[7] == "-":
        return period
    if len(period) == 7 and period[4] == "-":
        return f"{period}-01"
    if len(period) == 6 and period.isdigit():
        return f"{period[:4]}-{period[4:]}-01"
    return None


def collect_market_quant_bridge() -> int:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany(
        """
        INSERT INTO global_macro_categories
        (code,name,name_en,category,subcategory,unit,source,source_code,frequency,importance)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name,
            name_en=excluded.name_en,
            category=excluded.category,
            subcategory=excluded.subcategory,
            unit=excluded.unit,
            source=excluded.source,
            source_code=excluded.source_code,
            frequency=excluded.frequency,
            importance=excluded.importance
        """,
        [
            (
                spec.code,
                spec.name,
                spec.name_en,
                "MARKET_QUANT",
                spec.subcategory,
                spec.unit,
                "quant_major_indicator_series",
                spec.source_code,
                spec.frequency,
                spec.importance,
            )
            for spec in SPECS
        ],
    )

    total = 0
    for spec in SPECS:
        rows = conn.execute(
            """
            SELECT period, value
            FROM quant_major_indicator_series
            WHERE indicator_key = ?
              AND series_name = ?
              AND value IS NOT NULL
            ORDER BY period
            """,
            (spec.indicator_key, spec.series_name),
        ).fetchall()
        values = []
        for period, value in rows:
            date = _normalize_date(period)
            if date is None:
                continue
            values.append((date, float(value)))
        values.sort(key=lambda item: item[0])
        for i, (date, value) in enumerate(values):
            prev = values[i - 1][1] if i > 0 else None
            change_pct = ((value - prev) / abs(prev) * 100.0) if prev else None
            conn.execute(
                """
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value,
                    prev_value=excluded.prev_value,
                    change_pct=excluded.change_pct
                """,
                (spec.code, date, value, prev, change_pct),
            )
            total += 1

    conn.execute(
        """
        INSERT INTO global_macro_collection_log (source, status, records, message)
        VALUES ('market_quant', 'ok', ?, ?)
        """,
        (total, f"bridged {len(SPECS)} existing quant indicators"),
    )
    conn.commit()
    conn.close()
    logger.info("Market quant bridge collected %s records", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = collect_market_quant_bridge()
    print(f"시장 주요 퀀트 지표 브릿지 완료: {count}건")
