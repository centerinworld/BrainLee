#!/usr/bin/env python3
"""
quant_indicators_cron.py — 퀀트 주요지표 자동 수집 크론잡 래퍼

FastAPI 서버를 블로킹하지 않고 별도 프로세스로 실행.
crontab에서 호출하며 --mode 인수로 수집 주기를 분리.

수집 주기:
  daily   — 매일 19:30 (장 마감 후, ~2분)
            · 시장폭/거래량 (price_history 파생)
            · 대차잔고 (short_sell_daily 기반)
            · 한국은행 기준금리 (ECOS)
            · DART 카지노 공시 (파라다이스/GKL/롯데관광)
            · 파라다이스 Monthly IR Pack 세그먼트별 드롭액

  weekly  — 매주 월요일 08:00 (~5분)
            · K-Line BDI/BCI/BPI/BSI (건화물 지수)
            · SteelBenchmarker 중국 철강 최신값
            · SunSirs 중국 G.I/Wire Rod 최근 공개 일별 가격

  monthly — 매월 12일 05:00 (~40분, KAMA Excel 포함)
            · KAMA 자동차 월보 (회사별/모델별/점유율/글로벌)
            · 현대차·기아 US 판매
            · KOSIS 온라인쇼핑 (총액/세부/화장품/의류/음식서비스/교육용품)
            · KOSIS 소매업태별 판매
            · KOSIS 서비스업생산지수 (분기)
            · KPX 전력거래소 SMP
            · 서울 지하철 이용현황
            · KRIC 철도 여객인원
            · 돼지고기 도매가격 (MTRACE)
            · 가다랑어 수입단가 (관세청 HS)
            · KTO 외래관광객/국민해외관광객
            · 마카오 GGR (카지노 매출)
            · World Bank 철광석 가격
            · EIA 미국 리그카운트
            · ECOS 거시지표 (CSI/ESI/BSI/재고율/전산업생산)
            · ECOS 유동성 (예탁금/신용공여)
            · ECOS 농산물 PPI
            · 관세청 섹터별 수출입 (자동차/배터리/반도체/조선/철강/화장품/의약품/OLED/PCB/MLCC/에너지/비철/기계/헬스케어 등)
            · 반도체 수출 HS Trade
            · GKL 외국인 방문객 (공공데이터포털)
            · BDRY ETF (건화물 프록시, K-Line 실패 시 fallback)
            · 환율 (ECOS: 원/엔, 원/유로, 원/달러)

  annual  — 매년 1월 20일 05:00 (~10분)
            · HIRA 의료 통계 (피부과/성형외과/치과)
            · ITSTAT IPTV 가입자

사용:
  python3 scripts/ops/quant_indicators_cron.py --mode daily
  python3 scripts/ops/quant_indicators_cron.py --mode weekly
  python3 scripts/ops/quant_indicators_cron.py --mode monthly
  python3 scripts/ops/quant_indicators_cron.py --mode annual
  python3 scripts/ops/quant_indicators_cron.py --mode all

crontab 등록 (crontab -e):
  30 19 * * 1-5  cd /Applications/stock_dashboard && python3 scripts/ops/quant_indicators_cron.py --mode daily  >> logs/quant_daily.log 2>&1
  0  8  * * 1    cd /Applications/stock_dashboard && python3 scripts/ops/quant_indicators_cron.py --mode weekly >> logs/quant_weekly.log 2>&1
  0  5  12 * *   cd /Applications/stock_dashboard && python3 scripts/ops/quant_indicators_cron.py --mode monthly >> logs/quant_monthly.log 2>&1
  0  5  20 1 *   cd /Applications/stock_dashboard && python3 scripts/ops/quant_indicators_cron.py --mode annual  >> logs/quant_annual.log 2>&1
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 경로 설정 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

DB_PATH = str(ROOT / "stock.db")
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── PID 파일로 중복 실행 방지 ───────────────────────────────────────────────
PID_FILE = Path("/tmp/quant_indicators_cron.pid")

def _check_pid():
    if PID_FILE.exists():
        old_pid = PID_FILE.read_text().strip()
        try:
            os.kill(int(old_pid), 0)  # 프로세스 살아있으면 0 signal 성공
            print(f"[quant_cron] 이미 실행 중 (PID {old_pid}). 종료.")
            sys.exit(0)
        except (ProcessLookupError, ValueError):
            pass  # 죽은 PID → 계속 진행
    PID_FILE.write_text(str(os.getpid()))

def _remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

# ── 로깅 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("quant_cron")


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=300)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=300000")  # 5분 대기
    return conn


# ── 동기화 모듈 임포트 (함수 단위로만 사용) ────────────────────────────────
def _import_sync():
    from scripts.ops import sync_quant_major_indicators as S
    return S


def _safe(name: str, fn, *args, **kwargs):
    """각 수집기를 try/except로 감싸 하나 실패해도 나머지 계속 실행."""
    try:
        log.info(f"[시작] {name}")
        t0 = time.time()
        result = fn(*args, **kwargs)
        elapsed = time.time() - t0
        log.info(f"[완료] {name} — {elapsed:.1f}초")
        return result
    except Exception as e:
        log.error(f"[실패] {name}: {e}", exc_info=True)
        return None


def _upsert(conn: sqlite3.Connection, S, key: str, rows) -> int:
    """rows가 있으면 upsert, 없으면 0 반환."""
    if not rows:
        return 0
    try:
        return S.upsert_series(conn, key, rows)
    except Exception as e:
        log.error(f"upsert 실패 {key}: {e}")
        return 0


def _upsert_dict(conn: sqlite3.Connection, S, mapping: dict) -> dict:
    """dict[indicator_key → rows] 를 일괄 upsert."""
    counts = {}
    if not mapping:
        return counts
    for key, rows in mapping.items():
        counts[key] = _upsert(conn, S, key, rows)
    return counts


# ═══════════════════════════════════════════════════════════════════════════
# DAILY — 매일 19:30, ~2분
# ═══════════════════════════════════════════════════════════════════════════
def run_daily():
    log.info("=== DAILY 수집 시작 ===")
    conn = _open_db()
    S = _import_sync()
    S.init_tables(conn)
    S.seed_catalog(conn)

    counts = {}

    # 1. 시장폭/거래량 (price_history 파생, 가장 빠름)
    breadth_rows = _safe("시장폭/거래량", S.derive_market_breadth_from_price_history, conn)
    if breadth_rows:
        for k in ["public:21:1", "public:21:2", "public:21:3", "public:21:4"]:
            counts[k] = _upsert(conn, S, k, breadth_rows.get(k, []))
    else:
        # breadth가 None이면 빈 dict
        for k in ["public:21:1", "public:21:2", "public:21:3", "public:21:4"]:
            counts[k] = 0

    # 2. 대차잔고 (short_sell_daily → 월별 집계)
    short_rows = _safe("대차잔고", S.collect_monthly_short_balance, conn)
    counts["epic:20:22"] = _upsert(conn, S, "epic:20:22", short_rows)

    # 3. 한국은행 기준금리 (ECOS, 빠름)
    rate_rows = _safe("기준금리", S.collect_bok_base_rate)
    counts["epic:20:1"] = _upsert(conn, S, "epic:20:1", rate_rows)

    # 4. DART 카지노 공시 (최신 공시만 갱신)
    casino_rows = _safe("DART 카지노", S.collect_dart_casino_monthly, conn)
    if casino_rows:
        for k in ["epic:9:18", "epic:9:20", "epic:9:21", "epic:9:22",
                  "epic:9:23", "epic:9:25", "epic:9:35", "epic:9:36", "epic:9:38"]:
            counts[k] = _upsert(conn, S, k, casino_rows.get(k, []))

    # 5. 파라다이스 Monthly IR Pack — 세그먼트별 월별 드롭액
    paradise_segment_rows = _safe("파라다이스 세그먼트 드롭액", S.collect_paradise_segment_drop_from_ir_excel)
    counts["epic:9:24"] = _upsert(conn, S, "epic:9:24", paradise_segment_rows)

    conn.commit()
    conn.close()
    log.info(f"=== DAILY 완료 === {counts}")


# ═══════════════════════════════════════════════════════════════════════════
# WEEKLY — 매주 월요일 08:00, ~5분
# ═══════════════════════════════════════════════════════════════════════════
def run_weekly():
    log.info("=== WEEKLY 수집 시작 ===")
    conn = _open_db()
    S = _import_sync()
    S.init_tables(conn)
    S.seed_catalog(conn)

    counts = {}

    # 1. K-Line BDI/BCI/BPI/BSI
    kline_rows = _safe("K-Line BDI", S.collect_kline_dry_bulk_indices_weekly)
    if kline_rows:
        for k in ["epic:7:14", "epic:7:15", "epic:7:16", "epic:7:17"]:
            counts[k] = _upsert(conn, S, k, kline_rows.get(k, []))
    else:
        # K-Line 실패 시 BDRY ETF fallback
        bdry_rows = _safe("BDRY (fallback)", S.collect_bdry_dry_bulk_shipping_proxy_monthly)
        counts["epic:7:14"] = _upsert(conn, S, "epic:7:14", bdry_rows)

    # 2. SteelBenchmarker 중국 철강 최신값
    steel_rows = _safe("SteelBenchmarker 중국", S.collect_steelbenchmarker_china_latest)
    if steel_rows:
        for k in ["epic:1:25", "epic:1:26", "epic:1:27", "epic:1:29"]:
            counts[k] = _upsert(conn, S, k, steel_rows.get(k, []))

    # 3. SunSirs 중국 G.I / Wire Rod 최근 공개 일별 가격
    sunsirs_rows = _safe("SunSirs 중국 철강", S.collect_sunsirs_china_steel_daily)
    if sunsirs_rows:
        for k in ["epic:1:28", "epic:1:30"]:
            counts[k] = _upsert(conn, S, k, sunsirs_rows.get(k, []))

    conn.commit()
    conn.close()
    log.info(f"=== WEEKLY 완료 === {counts}")


# ═══════════════════════════════════════════════════════════════════════════
# MONTHLY — 매월 12일 05:00, ~40분
# ═══════════════════════════════════════════════════════════════════════════
def run_monthly():
    log.info("=== MONTHLY 수집 시작 ===")
    conn = _open_db()
    S = _import_sync()
    S.init_tables(conn)
    S.seed_catalog(conn)

    counts = {}

    # ── KAMA 자동차 월보 (가장 오래 걸림) ──────────────────────────────────
    log.info("--- KAMA 자동차 월보 (20-30분 소요) ---")
    kama_company, kama_share, kama_specific, kama_model, kama_global = (
        _safe("KAMA 자동차 월보", S.collect_kama_auto_company_indicators, start_year=2016)
        or ([], [], {}, {}, [])
    )
    if kama_company:
        counts["epic:0:2"] = _upsert(conn, S, "epic:0:2", kama_company)
        counts["epic:0:4"] = _upsert(conn, S, "epic:0:4", kama_share)
    if kama_global:
        counts["epic:0:1"] = _upsert(conn, S, "epic:0:1", kama_global)
    for k in ["epic:0:19", "epic:0:20", "epic:0:21"]:
        if kama_specific.get(k):
            counts[k] = _upsert(conn, S, k, kama_specific[k])
    for k in ["epic:0:17", "epic:0:112", "epic:0:113"]:
        if kama_model.get(k):
            counts[k] = _upsert(conn, S, k, kama_model[k])

    # ── 현대차·기아 US 판매 ────────────────────────────────────────────────
    hyundai_dom, hyundai_us = (
        _safe("현대차 IR", S.collect_hyundai_model_indicators) or ([], [])
    )
    counts["epic:0:14"] = _upsert(conn, S, "epic:0:14", hyundai_dom)
    counts["epic:0:55"] = _upsert(conn, S, "epic:0:55", hyundai_us)

    kia_us = _safe("기아 US 판매", S.collect_kia_us_model_sales)
    counts["epic:0:57"] = _upsert(conn, S, "epic:0:57", kia_us)

    # ── KOSIS 온라인쇼핑 ────────────────────────────────────────────────────
    online_total = _safe("KOSIS 온라인쇼핑 총액", S.collect_kosis_online_shopping_total)
    counts["epic:2:98"] = _upsert(conn, S, "epic:2:98", online_total)

    breakdown = _safe("KOSIS 온라인쇼핑 세부", S.collect_kosis_online_shopping_breakdown)
    if breakdown:
        for k in ["epic:2:22", "epic:2:23", "epic:8:14", "epic:8:15",
                  "epic:12:5", "epic:12:6"]:
            counts[k] = _upsert(conn, S, k, breakdown.get(k, []))
        # 파생 시리즈
        video_rows = _safe("영상구독 proxy", S.derive_video_subscription_proxy_from_online_breakdown, breakdown)
        if video_rows:
            counts["epic:3:97"] = _upsert(conn, S, "epic:3:97", video_rows.get("epic:3:97", []))
        consumption_rows = _safe("온라인소비 proxy", S.derive_online_consumption_proxies_from_online_breakdown, breakdown)
        if consumption_rows:
            counts["epic:11:155"] = _upsert(conn, S, "epic:11:155", consumption_rows.get("epic:11:155", []))
            counts["epic:13:22"] = _upsert(conn, S, "epic:13:22", consumption_rows.get("epic:13:22", []))

    retail_rows = _safe("KOSIS 소매업태", S.collect_kosis_retail_store_sales_proxy)
    if retail_rows:
        for k in ["epic:2:93", "epic:2:94", "epic:2:95", "epic:2:96", "epic:2:97"]:
            counts[k] = _upsert(conn, S, k, retail_rows.get(k, []))

    service_rows = _safe("KOSIS 서비스업생산지수", S.collect_kosis_service_industry_index_proxy)
    if service_rows:
        counts["epic:11:156"] = _upsert(conn, S, "epic:11:156", service_rows.get("epic:11:156", []))
        counts["epic:13:20"] = _upsert(conn, S, "epic:13:20", service_rows.get("epic:13:20", []))

    # ── KPX SMP ─────────────────────────────────────────────────────────────
    smp_rows = _safe("KPX SMP", S.collect_kpx_monthly_smp)
    counts["epic:6:18"] = _upsert(conn, S, "epic:6:18", smp_rows)

    # ── 교통 ────────────────────────────────────────────────────────────────
    subway_rows = _safe("서울 지하철", S.collect_seoul_subway_monthly)
    if subway_rows:
        counts["epic:22:9"] = _upsert(conn, S, "epic:22:9", subway_rows.get("epic:22:9", []))
        counts["epic:22:10"] = _upsert(conn, S, "epic:22:10", subway_rows.get("epic:22:10", []))

    rail_rows = _safe("KRIC 철도", S.collect_kric_rail_line_passenger_monthly)
    counts["epic:7:36"] = _upsert(conn, S, "epic:7:36", rail_rows)

    # ── 식품 가격 ────────────────────────────────────────────────────────────
    pork_rows = _safe("돼지고기 도매가", S.collect_mtrace_pork_auction_price_monthly)
    counts["epic:11:105"] = _upsert(conn, S, "epic:11:105", pork_rows)

    skipjack_rows = _safe("가다랑어 수입단가", S.collect_skipjack_import_unit_price_from_hs)
    counts["epic:11:69"] = _upsert(conn, S, "epic:11:69", skipjack_rows)

    # ── 관광 ────────────────────────────────────────────────────────────────
    foreign_visitors = _safe("KTO 외래관광객", S.collect_kto_foreign_visitors_purpose_file)
    counts["epic:3:70"] = _upsert(conn, S, "epic:3:70", foreign_visitors)

    regional_visitors = _safe("KTO 지역별관광", S.collect_kto_regional_visitors_monthly)
    counts["epic:3:71"] = _upsert(conn, S, "epic:3:71", regional_visitors)

    outbound = _safe("KTO 국민해외관광", S.collect_kto_korean_outbound_transport_monthly)
    counts["epic:3:34"] = _upsert(conn, S, "epic:3:34", outbound)

    macao_rows = _safe("마카오 GGR", S.collect_macao_ggr)
    counts["epic:9:13"] = _upsert(conn, S, "epic:9:13", macao_rows)

    gkl_rows = _safe("GKL 방문객", S.collect_gkl_visitors_from_publicdata)
    counts["epic:9:19"] = _upsert(conn, S, "epic:9:19", gkl_rows)

    # ── 원자재/에너지 ────────────────────────────────────────────────────────
    iron_rows = _safe("World Bank 철광석", S.collect_worldbank_iron_ore_monthly)
    counts["epic:1:37"] = _upsert(conn, S, "epic:1:37", iron_rows)

    eia_rows = _safe("EIA 미국 리그카운트", S.collect_eia_us_rig_count_monthly)
    counts["epic:19:50"] = _upsert(conn, S, "epic:19:50", eia_rows)

    # ── ECOS 거시지표 ─────────────────────────────────────────────────────────
    liquidity_rows = _safe("ECOS 유동성", S.collect_bok_market_liquidity)
    counts["epic:20:99"] = _upsert(conn, S, "epic:20:99", liquidity_rows)

    ecos_macro = _safe("ECOS 거시지표 확장", S.collect_ecos_macro_quant_extensions)
    if ecos_macro:
        for k in ["public:20:101", "public:20:102", "public:20:103",
                  "public:20:104", "public:20:105"]:
            counts[k] = _upsert(conn, S, k, ecos_macro.get(k, []))

    agri_ppi = _safe("ECOS 농산물 PPI", S.collect_ecos_agricultural_ppi)
    if agri_ppi:
        for k in ["epic:11:90", "epic:11:91", "epic:11:92"]:
            counts[k] = _upsert(conn, S, k, agri_ppi.get(k, []))

    # ── 수출입 ────────────────────────────────────────────────────────────────
    customs_rows = _safe("관세청 섹터 수출입", S.collect_customs_sector_quant_extensions)
    if customs_rows:
        customs_keys = [spec[0] for spec in getattr(S, "CUSTOMS_SECTOR_QUANT_SPECS", [])]
        for k in customs_keys:
            counts[k] = _upsert(conn, S, k, customs_rows.get(k, []))

    semi_rows = _safe("반도체 수출 HS", S.collect_semiconductor_export_from_hs, conn)
    if semi_rows:
        for k in ["epic:3:2", "epic:3:20", "epic:3:21"]:
            counts[k] = _upsert(conn, S, k, semi_rows.get(k, []))

    # ── 환율 (ECOS) ──────────────────────────────────────────────────────────
    # 기존 price_history 기반 환율은 항상 최신이지만, ECOS 환율도 갱신
    try:
        from scripts.ops.sync_quant_major_indicators import get_ecos_key, upsert_series
        import requests
        from datetime import date

        ecos_key = get_ecos_key()
        if ecos_key:
            start = "20100101"
            end = date.today().strftime("%Y%m%d")
            for symbol_code, indicator_key, divisor, sname in [
                ("0000002", "epic:20:jpykrw", 100.0, "jpy_krw"),
                ("0000003", "epic:20:eurkrw", 1.0, "eur_krw"),
                ("0000001", "epic:20:usdkrw", 1.0, "usd_krw"),
            ]:
                try:
                    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{ecos_key}/json/kr"
                           f"/1/10000/731Y001/D/{start}/{end}/{symbol_code}")
                    resp = requests.get(url, timeout=20)
                    items = resp.json().get("StatisticSearch", {}).get("row", [])
                    # 월별 평균으로 집계
                    monthly: dict = {}
                    for row in items:
                        period = row.get("TIME", "")[:6]  # YYYYMM
                        try:
                            v = float(row.get("DATA_VALUE", "")) / divisor
                            if v > 0:
                                if period not in monthly:
                                    monthly[period] = []
                                monthly[period].append(v)
                        except Exception:
                            pass
                    rows = [{"period": p, "series_name": sname, "value": sum(vs)/len(vs),
                             "unit": "원/1엔" if sname == "jpy_krw" else "원",
                             "source_name": "ECOS_731Y001",
                             "source_detail": f"731Y001/{symbol_code}",
                             "quality": "official_exact"}
                            for p, vs in sorted(monthly.items()) if vs]
                    if rows:
                        n = upsert_series(conn, indicator_key, rows)
                        counts[indicator_key] = n
                        log.info(f"환율 {indicator_key}: {n}행 upsert")
                except Exception as e:
                    log.warning(f"환율 {indicator_key} 실패: {e}")
    except Exception as e:
        log.warning(f"ECOS 환율 수집 실패: {e}")

    conn.commit()
    conn.close()
    total = sum(v for v in counts.values() if isinstance(v, int))
    log.info(f"=== MONTHLY 완료 === 총 {total}행 upsert, 지표 {len(counts)}개")


# ═══════════════════════════════════════════════════════════════════════════
# ANNUAL — 매년 1월 20일 05:00, ~10분
# ═══════════════════════════════════════════════════════════════════════════
def run_annual():
    log.info("=== ANNUAL 수집 시작 ===")
    conn = _open_db()
    S = _import_sync()
    S.init_tables(conn)
    S.seed_catalog(conn)

    counts = {}

    # HIRA 의료 통계 (피부과/성형외과/치과)
    hira_rows = _safe("HIRA 의료 통계", S.collect_hira_medical_subject_annual_proxy)
    if hira_rows:
        for k in ["epic:16:110", "epic:16:111", "epic:16:112"]:
            counts[k] = _upsert(conn, S, k, hira_rows.get(k, []))

    # ITSTAT IPTV 가입자
    iptv_rows = _safe("ITSTAT IPTV 가입자", S.collect_itstat_iptv_subscribers_annual)
    counts["epic:10:11"] = _upsert(conn, S, "epic:10:11", iptv_rows)

    conn.commit()
    conn.close()
    log.info(f"=== ANNUAL 완료 === {counts}")


# ═══════════════════════════════════════════════════════════════════════════
# ALL — 수동 실행 (기존 main()과 동일 효과)
# ═══════════════════════════════════════════════════════════════════════════
def run_all():
    log.info("=== ALL 수집 시작 (daily + weekly + monthly + annual) ===")
    run_daily()
    run_weekly()
    run_monthly()
    run_annual()
    log.info("=== ALL 완료 ===")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="퀀트 주요지표 자동 수집 크론잡")
    ap.add_argument(
        "--mode",
        choices=["daily", "weekly", "monthly", "annual", "all"],
        default="all",
        help="수집 주기 (기본: all)",
    )
    ap.add_argument("--no-pid", action="store_true", help="PID 중복 체크 비활성화")
    args = ap.parse_args()

    if not args.no_pid:
        _check_pid()

    t0 = time.time()
    log.info(f"quant_indicators_cron.py 시작 — mode={args.mode}, pid={os.getpid()}")

    try:
        if args.mode == "daily":
            run_daily()
        elif args.mode == "weekly":
            run_weekly()
        elif args.mode == "monthly":
            run_monthly()
        elif args.mode == "annual":
            run_annual()
        else:
            run_all()
    finally:
        _remove_pid()

    elapsed = time.time() - t0
    log.info(f"quant_indicators_cron.py 종료 — {elapsed:.0f}초 소요")


if __name__ == "__main__":
    main()
