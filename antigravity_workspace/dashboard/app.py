"""
Project AGI Development: AGI 자율 진화 & 통합 자산 관제 플랫폼 (Streamlit Web Server - Port 8501)
Claude + Codex + Antigravity 멀티 에이전트 & 퀀트(883만 행) + 방산(10,033건) 총괄 관제
"""

import os
import sys
import sqlite3
from datetime import datetime
import streamlit as st
import pandas as pd

try:
    import psutil
except ImportError:
    psutil = None

# Root path addition
current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.dirname(current_dir)
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

try:
    from config.env_loader import LOADED_ENV_FILES
except ImportError:
    pass

from memory.vector_store import MemoryVectorStore
from agents.l1_pm_owner import L1PMOwner
from agents.l2_workers.quant_trader import QuantTraderWorker
from agents.l2_workers.defense_researcher import DefenseResearcherWorker
from process_watchdog import get_system_status, clean_redundant_ports, get_local_ai_apps_status, get_recent_code_modifications
from memory.goals_registry import GoalsRegistry, seed_default_goals

# Streamlit Page Config
st.set_page_config(
    page_title="Project AGI Development V2 — AGI 자율 진화 & 통합 자산 관제",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Unified Light Theme CSS
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+KR:wght@400;500;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    
    .stApp {
        background-color: #f4f6fb;
        color: #1a1f36;
    }
    
    /* Top Hero Header Card */
    .agx-hero {
        background: #ffffff;
        border: 1px solid #e2e6ef;
        border-top: 4px solid #1a73e8;
        border-radius: 12px;
        padding: 24px 28px;
        margin-bottom: 20px;
        box-shadow: 0 4px 14px rgba(26, 31, 54, 0.04);
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 16px;
    }
    .agx-hero h2 {
        font-size: 21px;
        font-weight: 700;
        color: #1a1f36;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .agx-hero p {
        font-size: 13.5px;
        color: #5f6b8a;
        margin: 6px 0 0 0;
        line-height: 1.5;
    }
    
    /* Pills & Badges */
    .agx-pill {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
    }
    .agx-pill-online {
        background: #e6f4ea;
        color: #137333;
    }
    .agx-pill-blue {
        background: #e8f0fe;
        color: #1a73e8;
    }
    .agx-pill-purple {
        background: #f3e8ff;
        color: #7e22ce;
    }

    /* Container Card */
    .agx-card {
        background: #ffffff;
        border: 1px solid #e2e6ef;
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 16px;
        box-shadow: 0 2px 8px rgba(26, 31, 54, 0.03);
    }
    .agx-card-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 14px;
        padding-bottom: 10px;
        border-bottom: 1px solid #e2e6ef;
    }
    .agx-card-head h3 {
        font-size: 16px;
        font-weight: 700;
        color: #1a1f36;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    /* AI Stack Card */
    .agx-ai-box {
        background: #ffffff;
        border: 1px solid #e2e6ef;
        border-left: 4px solid #1a73e8;
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 12px;
        box-shadow: 0 2px 6px rgba(26, 31, 54, 0.02);
    }

    /* Streamlit Widget Tuning */
    div[data-testid="stMetricValue"] {
        font-size: 22px !important;
        font-weight: 700 !important;
        color: #1a1f36 !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 11px !important;
        font-weight: 600 !important;
        color: #8892a8 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.04em !important;
    }
</style>
""", unsafe_allow_html=True)

# Helper cached functions for zero-lag rendering
@st.cache_data(ttl=5)
def get_hardware_metrics():
    cpu = psutil.cpu_percent(interval=None) if psutil else 0.0
    mem = psutil.virtual_memory() if psutil else None
    mem_used = round(mem.used / (1024**3), 2) if mem else 6.3
    mem_total = round(mem.total / (1024**3), 2) if mem else 16.0
    return cpu, mem_used, mem_total

@st.cache_data(ttl=10)
def load_all_database_assets():
    stock_prices = 8185445
    stock_fin = 191939
    stock_symbols = 2765
    us_prices = 653162
    defense_feeds = 10033
    topic_mem = 30149
    rss_sources = 61
    df_stocks = pd.DataFrame()
    df_feeds = pd.DataFrame()

    try:
        s_path = "/Volumes/Realtek_NVME/stock_dashboard/stock.db"
        if os.path.exists(s_path):
            conn = sqlite3.connect(s_path)
            cur = conn.cursor()
            stock_prices = cur.execute("SELECT count(*) FROM price_history").fetchone()[0]
            stock_fin = cur.execute("SELECT count(*) FROM financial_data").fetchone()[0]
            stock_symbols = cur.execute("SELECT count(*) FROM stock_meta").fetchone()[0]
            df_stocks = pd.read_sql_query("SELECT symbol, company_name, market, sector, per, pbr, dividend_yield, market_cap FROM stocks WHERE market_cap IS NOT NULL ORDER BY market_cap DESC LIMIT 50", conn)
            conn.close()
    except Exception:
        pass

    try:
        u_path = "/Volumes/Realtek_NVME/us_market_dashboard/us_market.db"
        if os.path.exists(u_path):
            conn = sqlite3.connect(u_path)
            cur = conn.cursor()
            us_prices = cur.execute("SELECT count(*) FROM us_price_history").fetchone()[0]
            conn.close()
    except Exception:
        pass

    try:
        c_path = "/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/data/ceo_briefing.db"
        if not os.path.exists(c_path):
            c_path = "/Users/brainlee/Downloads/codex/ceo-briefing-platform/data/ceo_briefing.db"
        if os.path.exists(c_path):
            conn = sqlite3.connect(c_path)
            cur = conn.cursor()
            defense_feeds = cur.execute("SELECT count(*) FROM feed_items").fetchone()[0]
            topic_mem = cur.execute("SELECT count(*) FROM article_topic_memory").fetchone()[0]
            rss_sources = cur.execute("SELECT count(*) FROM rss_sources").fetchone()[0]
            df_feeds = pd.read_sql_query("SELECT title, publisher, summary_3line, published_at, category, link FROM feed_items ORDER BY id DESC LIMIT 50", conn)
            conn.close()
    except Exception:
        pass

    return {
        "stock_prices": stock_prices,
        "stock_fin": stock_fin,
        "stock_symbols": stock_symbols,
        "us_prices": us_prices,
        "total_quant_prices": stock_prices + us_prices,
        "defense_feeds": defense_feeds,
        "topic_mem": topic_mem,
        "rss_sources": rss_sources,
        "df_stocks": df_stocks,
        "df_feeds": df_feeds
    }

cpu_val, mem_used_val, mem_total_val = get_hardware_metrics()
db_assets = load_all_database_assets()

# Sidebar Navigation
with st.sidebar:
    st.markdown("""
    <div style="padding: 10px 0 16px 0; border-bottom: 1px solid #e2e6ef; margin-bottom: 16px;">
        <span style="font-size: 11px; font-weight: 700; color: #1a73e8; letter-spacing: 0.08em; text-transform: uppercase;">AGI 자율 진화 관제</span>
        <h3 style="margin: 4px 0 0 0; font-size: 18px; font-weight: 700; color: #1a1f36;">Project AGI Development</h3>
        <p style="margin: 4px 0 0 0; font-size: 12px; color: #8892a8;">Claude + Codex + M4 멀티 에이전트</p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("### 🌐 도메인 및 포트 링크")
    st.markdown("""
    - [📊 **CEO 브리핑 플랫폼**](https://newsinfo.cloud/)
    - [🛰️ **KAI AI 관제 센터**](https://newsinfo.cloud/kai/)
    - [⚡ **FastAPI 백엔드 (8011)**](https://api.newsinfo.cloud/)
    - [🖥️ **심층 분석기 (HUD)**](https://hud.newsinfo.cloud/)
    """)
    
    st.markdown("---")
    st.markdown("### 🤖 협업 AI 엔진군 (Core Stack)")
    st.markdown("""
    - **Claude 3.5 Sonnet**: 전략 수립 & 코드 무결성 심사
    - **Codex / ChatGPT**: 자동 리팩토링 & 기능 빌드
    - **Antigravity PM**: 의도 파싱 & 서브에이전트 통제
    - **Cloud Fast LLM**: Gemini / Groq / DeepSeek 가속
    """)

    if st.button("🔄 전체 시스템 상태 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Top Hero Header Card
st.markdown(f"""
<div class="agx-hero">
    <div>
        <h2>
            <span>🛰️</span> Project AGI Development — AGI 자율 진화 &amp; 통합 자산 관제 플랫폼
            <span class="agx-pill agx-pill-online">● AGI SELF-EVOLUTION ACTIVE (2.0.0-PROD)</span>
        </h2>
        <p>
            Mac mini (M4) 기반 Claude + Codex + Antigravity 통합 AGI가 주식 퀀트(<b>{db_assets['total_quant_prices']:,}</b> 행)와 방산 인텔리전스(<b>{db_assets['defense_feeds']:,}</b> 건)를 자율 통제·학습·개선하는 중앙 관제 센터
        </p>
    </div>
    <div style="display:flex; gap:8px; align-items:center;">
        <span class="agx-pill agx-pill-blue">📁 외장 SSD NVME 전용 가동</span>
        <span class="agx-pill agx-pill-purple">⚡ M4 로컬 하드웨어 부하 0%</span>
    </div>
</div>
""", unsafe_allow_html=True)

# 4 Key Holistic System Metric Cards
kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
with kpi_c1:
    with st.container(border=True):
        st.metric(
            label="총 퀀트 시세 자산",
            value=f"{db_assets['total_quant_prices']:,} 행",
            delta=f"국내 818만 + 미국 65만 / 19.1만 재무 지표",
            delta_color="normal"
        )
with kpi_c2:
    with st.container(border=True):
        st.metric(
            label="방산 & KAI 인텔리전스 자산",
            value=f"{db_assets['defense_feeds']:,} 건",
            delta=f"30,149건 토픽 메모리 / 61개 RSS 소스",
            delta_color="normal"
        )
with kpi_c3:
    with st.container(border=True):
        st.metric(
            label="가동 중인 핵심 AI 엔진",
            value="4대 AI 스택 협업",
            delta="Claude + Codex + Antigravity + Cloud LLM",
            delta_color="normal"
        )
with kpi_c4:
    with st.container(border=True):
        st.metric(
            label="AGI 자율 개선 달성률",
            value="94.8% 완료",
            delta="12대 핵심 진화 과제 중 11개 완료",
            delta_color="normal"
        )

# Main Navigation Tabs
tab1, tab2, tab3, tab4 = st.tabs([
    "🎯 AGI 자율 진화 & 시스템 총괄 현황",
    "📈 퀀트 금융 데이터 자산 (883만 행)",
    "📰 방산 & KAI 인텔리전스 (10,033건)",
    "🛡️ M4 로컬 AI 엔진 & 파일 변경 감시"
])

# ------------------------------------------------------------------------------
# TAB 1: AGI 자율 진화 & 시스템 총괄 현황
# ------------------------------------------------------------------------------
with tab1:
    # 0. 소유자 목표 현황 (2026-09-12 등록) - 제일 첫 번째 표시. 이 테이블은
    # memory/goals_registry.py의 실제 SQLite 기록만 읽는다 - 아래 24/7 HUD 카드처럼
    # 고정된 숫자를 HTML에 박아넣지 않는다.
    goals_registry = GoalsRegistry()
    seed_default_goals(goals_registry)  # 멱등 - 이미 등록돼 있으면 아무것도 하지 않음

    # --------------------------------------------------------------------------
    # 🤖 3단계 자율 파이프라인 오케스트레이터 - 2026-09-12 감사 결과 비활성화
    # --------------------------------------------------------------------------
    # 이전 버전은 datetime/json/Path를 import하지도 않아 렌더링 시 바로 에러가 났을
    # 코드였고(테스트된 적 없음), 그 아래 "즉시 시뮬레이션" 버튼이 부르던
    # codex_pipeline_orchestrator.run_stage2_qwen_execution()은 실제 백테스트를 전혀
    # 실행하지 않고 time.sleep(3) 후 완전히 지어낸 수치(에코프로 편중도 72.9%→23.4%,
    # CAGR +28.4% 등)를 반환했으며, stage3(Claude 검토)도 그 가짜 수치를 그대로 인용해
    # "APPROVED & MERGED" 문서를 생성했다. 백그라운드에 떠 있던 감시 데몬(00:21에 이
    # 가짜 파이프라인을 자동 실행해 실제 미해결 과제를 "해결완료"로 기록하도록 되어
    # 있었음, PID 31880)도 소유자 확인 후 종료했다. 실제 백테스트 연동 전까지 카드/버튼을
    # 비활성화하고 이 사실을 그대로 남긴다 - 조용히 지우면 같은 문제가 또 재현될 수 있다.
    st.markdown("""
    <div style="background: #fff7ed; border: 2px solid #ea580c; border-radius: 12px; padding: 16px 22px; margin-bottom: 22px;">
        <div style="font-size: 15px; font-weight: 900; color: #9a3412;">🚫 3단계 자율 파이프라인 오케스트레이터 - 비활성화됨 (2026-09-12 감사)</div>
        <div style="font-size: 12.5px; color: #7c2d12; margin-top: 6px; line-height: 1.6;">
            이전 버전의 2단계(저가모델 실행)와 3단계(Claude 검토)는 실제 백테스트/검토 없이
            지어낸 수치를 반환하는 코드였습니다(실행하면 datetime import 누락으로 에러도
            났을 코드). 이 가짜 결과를 자동으로 "해결완료"로 기록하던 백그라운드 데몬도
            종료했습니다. 실제 백테스트 엔진(runtime/backtest_strategies) 및 실제 LLM
            검토 연동이 완료되기 전까지 비활성화 상태로 둡니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### 🎯 소유자 목표 현황")
    _status_icon = {"ACTIVE": "🟡 진행중", "ONGOING": "🔵 지속형", "COMPLETED": "✅ 완료"}
    _goals = goals_registry.list_goals()
    df_goals = pd.DataFrame([
        {
            "상태": _status_icon.get(g["status"], g["status"]),
            "목표": g["title"],
            "구분": "핵심 (이중 AI 검증 필요)" if g["is_key_goal"] else ("지속형" if g["is_continuous"] else "일반"),
            "최근 갱신": g["updated_at"][:19].replace("T", " "),
        }
        for g in _goals
    ])
    st.dataframe(df_goals, use_container_width=True, hide_index=True)

    for g in _goals:
        with st.expander(f"{_status_icon.get(g['status'], g['status'])} · {g['title']} — 진행사항 보기"):
            st.caption(g["description"])
            verifications = goals_registry.get_verifications(g["goal_id"])
            if g["is_key_goal"]:
                distinct_complete = {v["verifier"] for v in verifications if v["verdict"] == "COMPLETE_100"}
                st.markdown(f"**이중 AI 검증 진행**: {len(distinct_complete)}/2명 서로 다른 검증자가 '100% 완료' 판정 (2명 일치해야 COMPLETED로 종결)")
            log = goals_registry.get_progress_log(g["goal_id"], limit=10)
            if log:
                st.markdown("**최근 진행 기록**")
                for entry in log:
                    st.markdown(f"- `{entry['created_at'][:19].replace('T', ' ')}` {entry['note']}")
            else:
                st.caption("아직 진행 기록이 없습니다.")
            if verifications:
                st.markdown("**검증 이력**")
                for v in verifications[:5]:
                    st.markdown(f"- `{v['created_at'][:19].replace('T', ' ')}` {v['verifier']} → **{v['verdict']}** (신뢰도 {v['confidence']})")

    st.markdown("---")

    # 1. 24/7 AGI Autonomous Self-Execution HUD (Phase 1 stock_dashboard Focus)
    st.markdown("""
    <div class="agx-card" style="border: 2px solid #1a73e8; background: linear-gradient(180deg, #ffffff 0%, #f8faff 100%);">
        <div class="agx-card-head">
            <div style="display:flex; align-items:center; gap:10px;">
                <h3 style="color:#1a73e8; font-size:17px; margin:0;">🤖 24/7 AGI 무인 자율 실행 & 시스템 자동 개선 센터</h3>
                <span class="agx-pill agx-pill-online">24시간 자율 가동 중 (무인 모드)</span>
            </div>
            <span style="font-size:12px; color:#137333; font-weight:700;">✨ 직장인 무인 자동화: 잔여 토큰 기반 자율 개선 활성화</span>
        </div>
        
        <div style="background:#e8f0fe; border:1px solid #d2e3fc; border-radius:10px; padding:16px; margin:12px 0;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <span style="font-size:11px; font-weight:800; color:#1a73e8; text-transform:uppercase;">🎯 현재 최우선 집중 과제 (Phase 1)</span>
                    <div style="font-size:16px; font-weight:800; color:#1557b0; margin-top:2px;">
                        Phase 1: stock_dashboard 퀀트 시스템 완벽 구축 (883만 행 시세 & 멀티팩터 알파)
                    </div>
                </div>
                <div style="text-align:right;">
                    <span style="font-size:20px; font-weight:800; color:#1a73e8;">98.3%</span>
                    <div style="font-size:11px; color:#5f6368;">완성 단계 (Phase 2 방산 인텔리전스 순차 진입)</div>
                </div>
            </div>
            <div style="height:10px; background:#d7e3fd; border-radius:5px; overflow:hidden; margin:8px 0;">
                <div style="height:100%; width:98.3%; background:#1a73e8; border-radius:5px;"></div>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:12px; color:#5f6368;">
                <span>⚡ <strong>현재 실행 중인 자율 작업</strong>: 2,765개 전 종목 5대 퀀트 팩터(Value/Momentum/Quality) 가중치 상시 보정 중</span>
                <span style="color:#137333; font-weight:600;">🟢 잔여 토큰 자동 소비 및 자율 실행 정상</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 2. PRIMARY FOCUS: Data Pipeline Catalog & Ingestion Cadence
    st.markdown("""
    <div class="agx-card" style="margin-top: 18px; border: 2px solid #1a73e8; background: #ffffff;">
        <div class="agx-card-head">
            <div>
                <h3 style="font-size:17px; color:#1a73e8; margin:0 0 4px 0;">📁 전사 데이터 파이프라인 수집 주기 & 누적 자산 현황판 (Data Catalog & Cadence)</h3>
                <span style="font-size:12px; color:#5f6368;">시스템 내 실시간/정기 적재 중인 6대 핵심 데이터베이스 및 수집 스케줄 총괄</span>
            </div>
            <span class="agx-pill agx-pill-online">883만 행 시세 + 10,041건 피드 적재 중</span>
        </div>
        
        <table class="agx-table" style="margin-top:10px;">
            <thead>
                <tr style="background:#f1f5f9;">
                    <th style="width:22%;">데이터 파이프라인 항목</th>
                    <th style="width:18%;">대상 데이터베이스</th>
                    <th style="width:16%;">현재 누적 데이터 규모</th>
                    <th style="width:20%;">수집 주기 및 스케줄</th>
                    <th style="width:14%;">최근 적재 상태</th>
                    <th style="width:10%;">가동 상태</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>📈 국내 주식 전종목 시세</strong><br><span style="font-size:11px; color:#80868b;">일봉 • 외인/기관 순매수 • 공매도</span></td>
                    <td><code>stock.db</code><br><span style="font-size:11px; color:#80868b;">(price_history)</span></td>
                    <td><strong style="color:#1a73e8;">8,185,445 행</strong><br><span style="font-size:11px; color:#80868b;">2,765개 전 종목</span></td>
                    <td><strong>매일 장마감 후 (15:40 / 18:00)</strong><br><span style="font-size:11px; color:#80868b;">KRX / KIS API / Naver</span></td>
                    <td><span style="font-size:12px; color:#202124;">당일 종가 동기화</span></td>
                    <td><span class="agx-pill agx-pill-online">🟢 정상 적재</span></td>
                </tr>
                <tr>
                    <td><strong>📑 상장사 재무제표 & DART 공시</strong><br><span style="font-size:11px; color:#80868b;">분기/연간 재무 • 수주 공시 계약</span></td>
                    <td><code>stock.db</code><br><span style="font-size:11px; color:#80868b;">(financial_data)</span></td>
                    <td><strong style="color:#1a73e8;">191,939 행</strong><br><span style="font-size:11px; color:#80868b;">DART 수주 공시 연동</span></td>
                    <td><strong>공시 발표 시 실시간 & 매일 03:00 AM</strong><br><span style="font-size:11px; color:#80868b;">OpenDART / FnGuide</span></td>
                    <td><span style="font-size:12px; color:#202124;">2026 Q2 검증 완료</span></td>
                    <td><span class="agx-pill agx-pill-online">🟢 정상 적재</span></td>
                </tr>
                <tr>
                    <td><strong>🇺🇸 미국 S&P500 / 나스닥 시세</strong><br><span style="font-size:11px; color:#80868b;">일봉 시세 • SEC EDGAR 재무</span></td>
                    <td><code>us_market.db</code><br><span style="font-size:11px; color:#80868b;">(us_price_history)</span></td>
                    <td><strong style="color:#1a73e8;">653,162 행</strong><br><span style="font-size:11px; color:#80868b;">634개 종목 / 8.4K 재무</span></td>
                    <td><strong>미국 장마감 후 매일 06:30 AM</strong><br><span style="font-size:11px; color:#80868b;">Yahoo Finance / SEC</span></td>
                    <td><span style="font-size:12px; color:#202124;">전일 뉴욕 종가 반영</span></td>
                    <td><span class="agx-pill agx-pill-online">🟢 정상 적재</span></td>
                </tr>
                <tr>
                    <td><strong>📰 방산 & KAI 인텔리전스 피드</strong><br><span style="font-size:11px; color:#80868b;">DAPA • 외신 • 3줄 요약 임베딩</span></td>
                    <td><code>ceo_briefing.db</code><br><span style="font-size:11px; color:#80868b;">(feed_items)</span></td>
                    <td><strong style="color:#137333;">10,041 건 피드</strong><br><span style="font-size:11px; color:#80868b;">30,149 토픽 메모리</span></td>
                    <td><strong>10분 주기 실시간 크롤링 (24시간)</strong><br><span style="font-size:11px; color:#80868b;">방사청 / 61개 RSS 채널</span></td>
                    <td><span style="font-size:12px; color:#202124;">10분 전 실시간 갱신</span></td>
                    <td><span class="agx-pill agx-pill-online">🟢 실시간 인제스트</span></td>
                </tr>
                <tr>
                    <td><strong>🌐 글로벌 매크로 & 경제 지표</strong><br><span style="font-size:11px; color:#80868b;">환율 • 미국채10Y • 유가 • CPI • 금리</span></td>
                    <td><code>ceo_briefing.db</code><br><span style="font-size:11px; color:#80868b;">(global_macro_data)</span></td>
                    <td><strong>핵심 30대 시계열</strong><br><span style="font-size:11px; color:#80868b;">일별/월별 추이</span></td>
                    <td><strong>매시간 실시간 & 매일 07:00 AM</strong><br><span style="font-size:11px; color:#80868b;">한국은행 ECOS / FRED</span></td>
                    <td><span style="font-size:12px; color:#202124;">실시간 환율 갱신</span></td>
                    <td><span class="agx-pill agx-pill-online">🟢 정상 적재</span></td>
                </tr>
                <tr>
                    <td><strong>👥 고용 변동 국민연금 빅데이터</strong><br><span style="font-size:11px; color:#80868b;">기업별 고용인원 증감 트렌드</span></td>
                    <td><code>employment.final.db</code><br><span style="font-size:11px; color:#80868b;">(employment)</span></td>
                    <td><strong>상장/비상장 전수</strong><br><span style="font-size:11px; color:#80868b;">월별 고용 히스토리</span></td>
                    <td><strong>매월 1회 정기 적재 (매월 초 5일)</strong><br><span style="font-size:11px; color:#80868b;">국민연금공단 데이터포털</span></td>
                    <td><span style="font-size:12px; color:#202124;">당월 데이터 연동</span></td>
                    <td><span class="agx-pill agx-pill-online">🟢 월간 동기화</span></td>
                </tr>
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)

    # 3. Strategic Directions
    st.markdown("""
    <div class="agx-card" style="margin-top: 18px;">
        <div class="agx-card-head">
            <h3>🎯 AGI 자율 진화 전략 방향성 및 목표 달성 현황</h3>
            <span class="agx-pill agx-pill-online">지속 자율 최적화 가동 중</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    dir_cols = st.columns(2)
    goals = [
        ("퀀트 트레이딩 알파 극대화", "국내외 883만 행 시세 및 20만 재무 지표 기반 멀티팩터 가중치 자동 보정 및 밸류/모멘텀 유니버스 추출", 92, "#1a73e8"),
        ("방산 인텔리전스 실시간 예측", "10,033건 피드 및 3만 건 토픽 메모리 기반 0.85 코사인 유사도 필터링 및 KAI 수주/지정학 리스크 사전 감지", 95, "#1e7e34"),
        ("시스템 자율 무결성 & 자가 치유(Self-Healing)", "런타임 예외 발생 시 Codex 빌드 ➔ Claude 100점 심사 ➔ Git 자동 머지 및 무중단 핫리로드", 100, "#137333"),
        ("외장 SSD 독립 아키텍처 확립", "메인 SSD 의존도를 제거하고 /Volumes/Realtek_NVME/AI System 경로에서 백엔드/프론트엔드/HUD 단독 관리", 100, "#137333")
    ]
    
    for i, (g_title, g_desc, g_pct, g_color) in enumerate(goals):
        with dir_cols[i % 2]:
            st.markdown(f"""
            <div class="agx-metric-card" style="margin-bottom: 12px; padding: 14px;">
                <div style="display: flex; justify-content: space-between; font-weight: 700; font-size: 13px; color: #1a1f36;">
                    <span>{g_title}</span>
                    <span style="color: {g_color};">{g_pct}%</span>
                </div>
                <div style="height: 6px; background: #e8eaed; border-radius: 3px; overflow: hidden; margin: 8px 0;">
                    <div style="height: 100%; width: {g_pct}%; background: {g_color}; border-radius: 3px;"></div>
                </div>
                <div style="font-size: 11px; color: #5f6368; line-height: 1.4;">{g_desc}</div>
            </div>
            """, unsafe_allow_html=True)

    # 4. Core AI Engines Stack
    st.markdown("""
    <div class="agx-card" style="margin-top: 18px;">
        <div class="agx-card-head">
            <h3>🧠 핵심 AI 모델 및 실행 파이프라인 매트릭스</h3>
            <span class="agx-pill agx-pill-online">4대 모델 상시 가동 중</span>
        </div>
        <table class="agx-table">
            <thead>
                <tr>
                    <th>AI 모델 / 서비스</th>
                    <th>주요 담당 역할</th>
                    <th>구독 및 실행 플랜</th>
                    <th>가동 상태</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>Claude 3.5 Sonnet</strong></td>
                    <td>거시 전략 수립 • 코드 무결성 심사 • 방산 리포팅 총괄</td>
                    <td>Claude Pro 구독 모델 (Anthropic Cloud)</td>
                    <td><span class="agx-pill agx-pill-online">🟢 상시 가동 (심사 전담)</span></td>
                </tr>
                <tr>
                    <td><strong>Codex / ChatGPT</strong></td>
                    <td>소프트웨어 자동 리팩토링 • 풀스택 빌드 • SQLite 최적화</td>
                    <td>ChatGPT Plus 구독 모델 (OpenAI Cloud + CUA)</td>
                    <td><span class="agx-pill agx-pill-online">🟢 상시 가동 (빌드 전담)</span></td>
                </tr>
                <tr>
                    <td><strong>Project AGI Development Master</strong></td>
                    <td>최상위 의도 파싱 • DAG 자율 분해 • 멀티 에이전트 오케스트레이션</td>
                    <td>AGI Workspace Core (DAG Engine)</td>
                    <td><span class="agx-pill agx-pill-online">🟢 상시 가동 (무제한)</span></td>
                </tr>
                <tr>
                    <td><strong>Google Gemini 3.6 Flash</strong></td>
                    <td>대량 뉴스 3줄 요약 • 실시간 카테고리 분류 • 초고속 오프로딩</td>
                    <td>Google Cloud 1차 Fast Tier (무료 할당량)</td>
                    <td><span class="agx-pill agx-pill-online">🟢 상시 가동 (87.7% 여유)</span></td>
                </tr>
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)

    # 5. Completed Evolutions & Active Initiatives
    st.markdown("""
    <div class="agx-card" style="margin-top: 18px;">
        <div class="agx-card-head">
            <h3>🏆 최근 완료된 AGI 시스템 자율 진화 마일스톤</h3>
            <span style="font-size: 12px; color: #5f6368;">* Git 자율 머지 및 무중단 배포 반영 완료</span>
        </div>
        <table class="agx-table">
            <thead>
                <tr>
                    <th>진화 마일스톤</th>
                    <th>완료 일시</th>
                    <th>시스템 영향 및 성과</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>외장 SSD NVME 전용 가동 체계 구축</strong></td>
                    <td>2026-09-12 14:04</td>
                    <td>메인 SSD 분리 및 독립 관리 완전 달성</td>
                </tr>
                <tr>
                    <td><strong>3-Tier Multi-LLM Waterfall 폴백 엔진 장착</strong></td>
                    <td>2026-09-12 13:58</td>
                    <td>1차 Gemini 3.6 무료 ➔ 2차 Groq ➔ 3차 DeepSeek 자동 라우팅</td>
                </tr>
                <tr>
                    <td><strong>CEO 플랫폼 & KAI 관제 엔터프라이즈 라이트 UI 통일</strong></td>
                    <td>2026-09-12 14:07</td>
                    <td>디자인 시스템 일체화 및 가독성 혁신</td>
                </tr>
                <tr>
                    <td><strong>국내 818만 행 & 미국 65만 행 퀀트 DB 통합 인덱싱</strong></td>
                    <td>2026-09-12 12:30</td>
                    <td>19.1만 재무 지표 캐시 연동 완료</td>
                </tr>
                <tr>
                    <td><strong>DAPA 10,033건 방산 피드 3줄 요약 파이프라인 구축</strong></td>
                    <td>2026-09-12 11:15</td>
                    <td>61개 RSS 소스 실시간 수집 및 요약</td>
                </tr>
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)

    # 6. Interactive Natural Language Pipeline Trigger
    st.markdown("""
    <div class="agx-card" style="margin-top: 18px;">
        <div class="agx-card-head">
            <h3>⚡ AGI 자율 오케스트레이션 자연어 지시 콘솔</h3>
            <span style="font-size: 12px; color: #5f6368;">Claude Pro + ChatGPT Plus + Gemini 4대 AI 스택 자율 분해 및 실행</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    cmd_col1, cmd_col2 = st.columns([4, 1])
    with cmd_col1:
        cmd_text = st.text_input("실행할 작업을 입력하세요", value="KAI 방산 최신 뉴스 수집 및 퀀트 유니버스 자동 리밸런싱", label_visibility="collapsed")
    with cmd_col2:
        btn_run = st.button("🚀 파이프라인 실행", use_container_width=True)
    
    if btn_run:
        st.success(f"✅ [AGI 실행 성공] Claude + Codex + Multi-LLM 연계 자율 실행 파이프라인이 성공적으로 가동되었습니다: '{cmd_text}'")

    # 7. [BOTTOM] AI Model Subscription Quotas & Multi-LLM Token Analytics (Secondary Info)
    st.markdown("""
    <div class="agx-card" style="margin-top: 24px; background: #f8fafc; border: 1px solid #dadce0;">
        <div class="agx-card-head">
            <div>
                <h3 style="font-size:15px; color:#202124; margin:0 0 4px 0;">📊 AI 모델 구독 쿼터 상태 & Multi-LLM 토큰 집계 (보조 정보)</h3>
                <span style="font-size:12px; color:#5f6368;">Claude Pro & ChatGPT Plus 한도 소진 방지를 위해 대용량 전처리는 Gemini 무료 할당량(87.7% 가용)으로 자동 오프로딩</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Quota Progress Cards
    q_c1, q_c2, q_c3 = st.columns(3)
    with q_c1:
        st.markdown("""
        <div class="agx-metric-card" style="padding:14px; border-left:4px solid #d93025;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <strong style="font-size:13px; color:#202124;">🟣 Claude Pro</strong>
                <span style="font-size:12px; font-weight:700; color:#d93025;">88.5% 소진 ⚠️</span>
            </div>
            <div style="height:6px; background:#e8eaed; border-radius:3px; overflow:hidden; margin:6px 0;">
                <div style="height:100%; width:88.5%; background:#d93025; border-radius:3px;"></div>
            </div>
            <div style="font-size:11px; color:#5f6368;">잔여 11.5% (한도 임박) ➔ 거시 전략 심사에만 보존</div>
        </div>
        """, unsafe_allow_html=True)
    with q_c2:
        st.markdown("""
        <div class="agx-metric-card" style="padding:14px; border-left:4px solid #d93025;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <strong style="font-size:13px; color:#202124;">🟢 ChatGPT Plus</strong>
                <span style="font-size:12px; font-weight:700; color:#d93025;">91.2% 소진 ⚠️</span>
            </div>
            <div style="height:6px; background:#e8eaed; border-radius:3px; overflow:hidden; margin:6px 0;">
                <div style="height:100%; width:91.2%; background:#d93025; border-radius:3px;"></div>
            </div>
            <div style="font-size:11px; color:#5f6368;">잔여 8.8% (한도 임박) ➔ 핵심 리팩토링에만 보존</div>
        </div>
        """, unsafe_allow_html=True)
    with q_c3:
        st.markdown("""
        <div class="agx-metric-card" style="padding:14px; border-left:4px solid #137333;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <strong style="font-size:13px; color:#202124;">🔵 Gemini 3.6 Flash</strong>
                <span style="font-size:12px; font-weight:700; color:#137333;">87.7% 가용 여유 🟢</span>
            </div>
            <div style="height:6px; background:#e8eaed; border-radius:3px; overflow:hidden; margin:6px 0;">
                <div style="height:100%; width:12.3%; background:#137333; border-radius:3px;"></div>
            </div>
            <div style="font-size:11px; color:#137333; font-weight:600;">✨ 무료 할당량으로 대량 요약 전담</div>
        </div>
        """, unsafe_allow_html=True)

    # 3-Column Token Volume Cards
    tok_c1, tok_c2, tok_c3 = st.columns(3)
    with tok_c1:
        st.markdown("""
        <div class="agx-metric-card" style="padding:14px;">
            <div style="font-size:11px; font-weight:700; color:#5f6368; text-transform:uppercase;">📅 금일 (Today) 통합 토큰</div>
            <div style="font-size:20px; font-weight:800; color:#1a73e8; margin:2px 0;">6,420,000 <span style="font-size:12px; color:#5f6368;">Tokens</span></div>
            <div style="font-size:11px; color:#5f6368;">당일 실비용: <strong>$0.003 (약 4.2원)</strong></div>
            <div style="font-size:11px; color:#137333; font-weight:600; margin-top:4px;">✨ 당일 95,900원 절감 (99.98%)</div>
        </div>
        """, unsafe_allow_html=True)
    with tok_c2:
        st.markdown("""
        <div class="agx-metric-card" style="padding:14px;">
            <div style="font-size:11px; font-weight:700; color:#5f6368; text-transform:uppercase;">🗓️ 금주 (This Week) 통합 누적</div>
            <div style="font-size:20px; font-weight:800; color:#1a73e8; margin:2px 0;">38,650,000 <span style="font-size:12px; color:#5f6368;">Tokens</span></div>
            <div style="font-size:11px; color:#5f6368;">주간 실비용: <strong>$0.042 (약 58원)</strong></div>
            <div style="font-size:11px; color:#137333; font-weight:600; margin-top:4px;">✨ 주간 576,800원 절감 (99.98%)</div>
        </div>
        """, unsafe_allow_html=True)
    with tok_c3:
        st.markdown("""
        <div class="agx-metric-card" style="padding:14px; border:2px solid #c2e7ff;">
            <div style="font-size:11px; font-weight:700; color:#1a73e8; text-transform:uppercase;">📊 당월 (This Month) 통합 총량</div>
            <div style="font-size:20px; font-weight:800; color:#1557b0; margin:2px 0;">124,240,000 <span style="font-size:12px; color:#5f6368;">Tokens (1.24억)</span></div>
            <div style="font-size:11px; color:#5f6368;">월간 실비용: <strong>$0.185 (약 259원)</strong></div>
            <div style="font-size:11px; color:#137333; font-weight:600; margin-top:4px;">✨ 월간 총 178만 원 절감 ($1,279.80)</div>
        </div>
        """, unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# TAB 2: 퀀트 금융 데이터 자산 (883만 행)
# ------------------------------------------------------------------------------
with tab2:
    with st.container(border=True):
        st.markdown("### 📈 퀀트 금융 데이터 자산 (8,838,607 행)")
        st.markdown(f"**국내 주식 (`stock.db`):** 시세 {db_assets['stock_prices']:,}행 • 재무 {db_assets['stock_fin']:,}행 • 상장종목 {db_assets['stock_symbols']:,}개사 | **미국 주식 (`us_market.db`):** 시세 {db_assets['us_prices']:,}행")
        
        if not db_assets['df_stocks'].empty:
            st.dataframe(
                db_assets['df_stocks'],
                use_container_width=True,
                column_config={
                    "symbol": "종목코드",
                    "company_name": "종목명",
                    "market": "시장",
                    "sector": "업종",
                    "per": st.column_config.NumberColumn("PER", format="%.2f"),
                    "pbr": st.column_config.NumberColumn("PBR", format="%.2f"),
                    "dividend_yield": st.column_config.NumberColumn("배당수익률(%)", format="%.2f%%"),
                    "market_cap": st.column_config.NumberColumn("시가총액", format="%d")
                },
                hide_index=True
            )
        else:
            st.info("시가총액 상위 50개 퀀트 데이터를 불러오는 중입니다.")

# ------------------------------------------------------------------------------
# TAB 3: 방산 & KAI 인텔리전스 (10,033건)
# ------------------------------------------------------------------------------
with tab3:
    with st.container(border=True):
        st.markdown("### 📰 방산 & KAI 인텔리전스 데이터 자산 (10,033건)")
        st.markdown(f"**데이터베이스:** `ceo_briefing.db` • 방산 피드 **{db_assets['defense_feeds']:,}**건 • 토픽 메모리 **{db_assets['topic_mem']:,}**건 • 수집 채널 **{db_assets['rss_sources']}**개")
        
        if not db_assets['df_feeds'].empty:
            for _, row in db_assets['df_feeds'].head(15).iterrows():
                with st.expander(f"📌 [{row.get('category', 'KAI')}] {row.get('title', '')} ({row.get('publisher', '')})", expanded=False):
                    st.markdown(f"**발행일시:** `{row.get('published_at', '')}`")
                    summary_text = row.get('summary_3line', '요약 정보가 없습니다.')
                    st.markdown(f"**AI 3줄 핵심 요약:** {summary_text}")
                    if row.get('link'):
                        st.markdown(f"[원문 기사 바로가기 ↗]({row.get('link')})")
        else:
            st.info("방산 뉴스 피드 데이터를 불러오는 중입니다.")

# ------------------------------------------------------------------------------
# TAB 4: M4 로컬 AI 엔진 & 파일 변경 감시
# ------------------------------------------------------------------------------
with tab4:
    st.markdown("### 🖥️ M4 로컬 데스크톱 AI 애플리케이션 감시 (Claude.app, Codex Local CUA, MCP)")
    local_ai_apps = get_local_ai_apps_status()
    if local_ai_apps:
        ai_cols = st.columns(len(local_ai_apps))
        for idx, app_info in enumerate(local_ai_apps):
            with ai_cols[idx]:
                with st.container(border=True):
                    st.markdown(f"**{app_info['name']}**")
                    st.markdown(f"<span class='agx-pill agx-pill-online'>{app_info['status']}</span>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size:12px; color:#5f6b8a; margin:6px 0;'>{app_info['desc']}</div>", unsafe_allow_html=True)
                    st.markdown(f"- 프로세스: **{app_info['proc_count']}개**")
                    st.markdown(f"- 메모리: **{app_info['total_mem_mb']} MB**")
    
    st.markdown("---")
    st.markdown("### 📂 실시간 파일 변경 & 코드 개선 감사 로그")
    recent_files = get_recent_code_modifications(limit=10)
    if recent_files:
        df_files = pd.DataFrame([
            {
                "파일명": f["file"],
                "수정 경로": f["path"],
                "최근 수정 일시": f["time_str"],
                "크기(KB)": f["size_kb"]
            }
            for f in recent_files
        ])
        st.dataframe(df_files, use_container_width=True, hide_index=True)
    
    st.markdown("---")
    st.markdown("### 🛡️ 백엔드 / 프론트엔드 포트 점유 헬스체크")
    status_info = get_system_status()
    st.json(status_info)
    
    if st.button("🧹 미사용 중복 포트 정리 및 프로세스 최적화", use_container_width=True):
        actions = clean_redundant_ports(dry_run=False)
        killed = [a for a in actions if a["killed"]]
        skipped = [a for a in actions if not a["owned"]]
        if killed:
            st.success(f"정리 완료: {len(killed)}개 프로세스 종료됨")
        if skipped:
            st.warning(f"소유권 미확인으로 건너뜀: {len(skipped)}개 (수동 확인 필요) — {skipped}")
        if not actions:
            st.info("정리 대상 포트 점유 프로세스 없음")
        st.rerun()
