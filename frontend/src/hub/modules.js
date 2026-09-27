/**
 * hub/modules.js — 모듈(메인 페이지) 정의와 탭↔모듈 매핑 (2026-09-27, 사이트 전면 개편)
 *
 *  Stock Info    /info   시황·종목·섹터·수급 정보 (기존 대시보드의 조회형 화면)
 *  Key Indicator         newsinfo.cloud (별도 사이트 — 외부 링크)
 *  Stock Lab     /lab    전략센터·백테스트·발굴·가상매매 (기존 대시보드의 연구형 화면)
 *  Stock LLM     /llm/   Brian_RAG(:8888) — 서버가 프록시, 관리자 로그인 필요
 *  Admin         /admin  관리자 모드 — 비밀번호 로그인 후 설정·시스템·리스크게이트
 *
 * SECTIONS는 사이드바 구성(섹션 라벨 + 탭 키). 화면 자체는 App.jsx의 activeTab 스위치가 그린다 —
 * 새 탭을 만들면 여기에 키를 추가하고 App.jsx NAV_DEFS에 아이콘·라벨을 넣는다.
 */
export const KEY_INDICATOR_URL = 'https://newsinfo.cloud';
export const LLM_PATH = '/llm/';

export const MODULES = {
  info: {
    key: 'info', title: 'Stock Info', short: '주식 정보', path: '/info', defaultTab: 'macro',
    sections: [
      { label: '시황', tabs: ['macro', 'market_indicators', 'stock_rs', 'global_foreign_flow', 'sector_rotation', 'etf_check'] },
      { label: '종목', tabs: ['analysis', 'us_stocks', 'peer_compare', 'detailed_analysis', 'sector_taxonomy'] },
      // 2026-09-27(사용자 지시): market_radar(구 "섹터 지표")를 "섹터 분류"로 개명해 반도체 섹터 바로 아래로. 안에 있던 주도섹터 진입신호 표는
      // 섹터 로테이션과 중복이라 삭제(MarketRadarView.jsx)하고 섹터 로테이션을 정본으로 둔다.
      { label: '퀀트지표', tabs: ['market_radar', 'semiconductor_sector', 'hs_trade2', 'export_health', 'employment', 'quant_indicators'] },
      { label: '공시/리포트', tabs: ['dart_contracts', 'reports', 'telegram', 'hot_sector'] },
      { label: '내 투자', tabs: ['buy_candidates', 'portfolio'], locked: true },   // 메뉴 진입 자체에 관리자 비밀번호 필요(2026-09-27)
    ],
    hidden: ['watchlist', 'insight'],
  },
  lab: {
    key: 'lab', title: 'Stock Lab', short: '전략 연구실', path: '/lab', defaultTab: 'strategy_hub',
    // 2026-09-27(사용자 지시): 전략 센터·실험 로드맵의 페이지 내부 가로 탭을 Stock Info처럼 왼쪽 메뉴(+ 섹션)로 재배열.
    // 각 항목은 같은 컴포넌트를 initialHubTab/initialPageTab만 다르게 넘겨 그린다(hub/AppSidebar 쪽 변경 없음).
    sections: [
      { label: '전략 센터', tabs: ['strategy_hub', 'strategy_hub_continuous', 'strategy_hub_desc', 'strategy_hub_ledger', 'strategy_hub_factor', 'strategy_hub_datalab'] },
      { label: '실험 로드맵', tabs: ['exp_roadmap', 'exp_roadmap_turnaround', 'exp_roadmap_cherry', 'exp_roadmap_consensus', 'exp_roadmap_hardening'] },
      { label: '매매·신호', tabs: ['trend', 'signal_impact'] },
      { label: '종목 발굴', tabs: ['tenbagger', 'tenbagger_proj', 'dart_excel'] },
    ],
    hidden: ['screener', 'megatrend', 'backtest'],
  },
  // 2026-09-27(사용자 지시): Stock LLM이 다른 모듈과 완전히 다른 화면(자체 HTML, 상단바만 흉내)이었다 —
  // 다른 모듈과 동일한 AppBar+왼쪽 사이드바 셸 안에서 열리도록 정식 모듈로 승격(실제 화면은 iframe, hub/StockLlmView.jsx).
  // 경로는 /stock-llm — 서버의 `/llm/*` 프록시(routes/llm_proxy.py)와 겹치지 않는 별도 경로다.
  'stock-llm': {
    key: 'stock-llm', title: 'Stock LLM', short: 'AI 리서치', path: '/stock-llm', defaultTab: 'llm_console',
    sections: [
      { label: 'Stock LLM', tabs: ['llm_console'] },
    ],
    hidden: [],
  },
  admin: {
    key: 'admin', title: '관리자', short: '관리자 모드', path: '/admin', defaultTab: 'admin_home',
    sections: [
      { label: '관리', tabs: ['admin_home', 'system_map', 'admin_ki', 'system', 'risk_gate', 'settings'] },
    ],
    hidden: [],
  },
};

/** 관리자 로그인이 있어야 열리는 탭(내 투자) */
export const LOCKED_TABS = ['buy_candidates', 'portfolio'];

export const MODULE_ORDER = ['info', 'lab', 'stock-llm', 'admin'];

const TAB_TO_MODULE = {};
Object.values(MODULES).forEach((m) => {
  [...m.sections.flatMap((s) => s.tabs), ...m.hidden].forEach((t) => { TAB_TO_MODULE[t] = m.key; });
});

export const moduleOfTab = (tab) => TAB_TO_MODULE[tab] || null;
export const tabPath = (moduleKey, tab) => `${MODULES[moduleKey].path}/${tab}`;
