// Sector Follow-up Integration Test
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  AreaChart, Area, ComposedChart, Bar, Cell, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine
} from 'recharts';
import {
  TrendingUp, Search, Cpu, Activity,
  LayoutDashboard, Database, Globe, BarChart3,
  Star, StarOff, Trash2, Plus, Eye, FileText, Target,
  Newspaper, Send, FlaskConical, Ship, Wallet, Settings, Server, Users
} from 'lucide-react';

import EmploymentYearlyView from './EmploymentYearlyView';
import EtfCheckView from './EtfCheckView';
import NpsTrendView from './NpsTrendView';
import StockAnalysisRsView from './views/StockAnalysisRsView';
import MarketIndicatorsView from './views/MarketIndicatorsView';
import SemiconductorView from './views/SemiconductorView';
import SectorFollowupView from './views/SectorFollowupView';
import MarketRadarView from './views/MarketRadarView';


// ──────────────────────────────────────────────────────────────
// [버그 ① 수정] API_BASE를 절대경로(포트 하드코딩)에서 상대경로로 변경.
// vite.config.js의 proxy 설정이 /api/* 요청을 백엔드로 전달하므로
// 직접 :8000 포트를 지정하면 proxy를 우회하고 CORS 오류가 발생.
// ──────────────────────────────────────────────────────────────
const API = (path) => path;

const isKRMarketOpen = () => {
  const now = new Date();
  const day = now.getDay();
  if (day===0||day===6) return false;
  const kst = new Date(now.toLocaleString('en-US',{timeZone:'Asia/Seoul'}));
  const t = kst.getHours()*100+kst.getMinutes();
  return t>=900 && t<=1535;
};
const isUSMarketOpen = () => {
  const now = new Date();
  const est = new Date(now.toLocaleString('en-US',{timeZone:'America/New_York'}));
  const day = est.getDay();
  if (day===0||day===6) return false;
  const t = est.getHours()*100+est.getMinutes();
  return t>=930 && t<=1600;
};
const anyMarketOpen = () => isKRMarketOpen()||isUSMarketOpen();

// 공시 조회 가능 시간: 평일 08:00~20:00 KST (장 마감 후 공시 포함)
const isDisclosureTime = () => {
  const now = new Date();
  const kst = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Seoul' }));
  const day  = kst.getDay();
  if (day === 0 || day === 6) return false;
  const t = kst.getHours() * 100 + kst.getMinutes();
  return t >= 800 && t <= 2000;
};

const useIsMobile = () => {
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  useEffect(() => {
    const fn = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', fn);
    return () => window.removeEventListener('resize', fn);
  }, []);
  return isMobile;
};

// ── localStorage 헬퍼 (새로고침 후 상태 복원) ──────────────────────────
const _lsGet = (key, fallback) => { try { const v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : fallback; } catch { return fallback; } };
const _lsSet = (key, val) => { try { localStorage.setItem(key, JSON.stringify(val)); } catch {} };


// ── 프론트엔드 시그널 캐시 (컴포넌트 재마운트와 무관하게 유지) ────
const _signalFrontCache = {};

const App = () => {
  const isMobile = useIsMobile();
  const [activeTab, setActiveTab] = useState('macro');
  const [portfolioAuth, setPortfolioAuth] = useState(false);
  const [selectedStock, setSelectedStock] = useState(() => _lsGet('sd_selectedStock', '005930'));
  const [shortData, setShortData]         = React.useState(null); // 대차잔고
  const [watchlist, setWatchlist] = useState([]);
  const [chartData, setChartData] = useState([]);
  const [finTable, setFinTable] = useState([]);
  const [summStats, setSummStats] = useState(null);
  const [aiReport, setAiReport] = useState(null);
  const [macroData, setMacroData] = useState(() => {
    try {
      const raw = localStorage.getItem('sd_macroCache');
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      // TTL: 장중 1시간(3600s), 장외 4시간(14400s). 구버전(ts 없음) 은 무효로 처리
      if (!parsed.ts) return null;
      const ttl = anyMarketOpen() ? 3600000 : 14400000;
      if (Date.now() - parsed.ts > ttl) return null;
      return parsed.data ?? parsed; // 신형({data,ts}) / 구형(raw object) 모두 지원
    } catch { return null; }
  });
  const [sysStats, setSysStats] = useState(null);
  const [loading, setLoading]         = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = React.useState([]);
  const [showSearchDrop, setShowSearchDrop] = React.useState(false);
  const [chartDays, setChartDays]     = useState(() => _lsGet('sd_chartDays', 30));
  const [quarterTable, setQuarterTable] = useState([]);
  const [cfAnnual, setCfAnnual]       = useState([]);   // 연간 현금흐름표
  const [cfQuarter, setCfQuarter]     = useState([]);   // 분기 현금흐름표
  const [reportType, setReportType]   = useState('CFS'); // 'CFS'(연결) | 'OFS'(별도)
  const [consensus, setConsensus]     = useState(null); // 컨센서스 (목표주가)
  const [consensusMonths, setConsensusMonths] = useState(12); // 컨센서스 기간 (6/12/24개월)
  const [consensusExpanded, setConsensusExpanded] = useState(false); // 컨센서스 전체 보기 토글
  const cfPollRef = React.useRef(null);   // 현금흐름 폴링 타이머
  const [collecting, setCollecting]   = useState(false);
  const [selectedStockName, setSelectedStockName] = useState(""); // 종목명 (watchlist 없어도 표시)

  // localStorage 동기화 (탭/종목 전환 시 저장 → 새로고침 후 복원)
  const changeTab = React.useCallback((tab) => {
    _lsSet('sd_activeTab', tab);
    setActiveTab(tab);
  }, []);

  const changeStock = React.useCallback((code) => {
    _lsSet('sd_selectedStock', code);
    setSelectedStock(code);
  }, []);

  const changeChartDays = React.useCallback((d) => {
    _lsSet('sd_chartDays', d);
    setChartDays(d);
  }, []);

  const fetchWatchlist = useCallback(async () => {
    try {
      const res = await fetch(API('/api/commands/watchlist'));
      if (res.ok) setWatchlist(await res.json());
    } catch (e) { console.error("Watchlist fetch error", e); }
  }, []);

  const fetchMacro = useCallback(async () => {
    try {
      // /api/realtime/macro: 장중이면 Yahoo 즉시 갱신 후 반환, 장외면 DB 최신값 반환
      const res = await fetch(API('/api/realtime/macro'));
      if (res.ok) {
        const data = await res.json();
        setMacroData(data);
        const now = new Date().toLocaleTimeString('ko-KR');
        setLastUpdated(now);
        try {
          localStorage.setItem('sd_macroCache', JSON.stringify({ data, ts: Date.now() }));
        } catch {}
      }
    } catch (e) { console.error("Macro fetch error", e); }
  }, []);


  const fetchSystem = useCallback(async () => {
    try {
      const res = await fetch(API('/api/dashboard/stats'));
      if (res.ok) setSysStats(await res.json());
    } catch (e) { console.error("System fetch error", e); }
  }, []);

  // 차트 기간 변경 — 보유 데이터보다 긴 기간 요청 시 재fetch
  const handleChartDaysChange = (days) => {
    changeChartDays(days);
    // 현재 로드된 데이터가 요청 기간보다 짧으면 재fetch
    if (days > chartData.length) {
      fetchStockDetail(days);
    }
  };

  // 전체 데이터 로드 (종목/탭 변경 시)
  const [marketInfo, setMarketInfo] = React.useState({});
  const fetchIdRef = React.useRef(0);

  const fetchStockDetail = useCallback(async (days) => {
    if (!selectedStock || selectedStock === 'None') return;

    // 이번 fetch의 고유 ID — 종목이 바뀌면 이 ID가 outdated됨
    const myId = ++fetchIdRef.current;
    const isStale = () => fetchIdRef.current !== myId;  // 종목이 바뀌었으면 true

    const d = days !== undefined ? days : chartDays;
    // 기존 데이터를 유지한 채 갱신해 화면 깜박임을 줄인다.
    // (최초 로드/명시적 종목 전환 시에만 오버레이)
    if (days === undefined) setLoading(true);
    setCollecting(false);
    if (cfPollRef.current) { clearInterval(cfPollRef.current); cfPollRef.current = null; }

    // fetch 대상 종목코드를 지역변수로 고정 (클로저 내 stale 방지)
    const code = selectedStock;

    try {
      // ① 온디맨드 수집 트리거 + 종목명 취득
      fetch(API(`/api/commands/analyze/${code}`), { method: 'POST' })
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (!isStale() && data?.stock_name) setSelectedStockName(data.stock_name); })
        .catch(() => {});

      // ① 시장정보 fetch
      fetch(API(`/api/dashboard/market-info/${code}`))
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (!isStale() && data) setMarketInfo(data); })
        .catch(() => {});

      // ② 대차잔고 별도 fetch
      fetch(API(`/api/buy-candidates/short-sell/${code}`))
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (!isStale()) setShortData(data); })
        .catch(() => { if (!isStale()) setShortData(null); });

      // 요청 기간 이상 항상 확보 (최소 365일, 10년 탭도 대응)
      const fetchDays = Math.max(d, 365);
      const [chartRes, tableRes, quarterRes, summRes, aiRes, cfARes, cfQRes, consRes] = await Promise.all([
        fetch(API(`/api/dashboard/chart/${code}?days=${fetchDays}`)),
        fetch(API(`/api/dashboard/financial-table/${code}?type=annual&report_type=CFS`)),
        fetch(API(`/api/dashboard/financial-table/${code}?type=quarter&report_type=CFS`)),
        fetch(API(`/api/dashboard/fundamentals/${code}`)),
        fetch(API(`/api/reports/latest/${code}`)),
        fetch(API(`/api/dashboard/cashflow/${code}?type=annual&report_type=CFS`)),
        fetch(API(`/api/dashboard/cashflow/${code}?type=quarter&report_type=CFS`)),
        /^\d{6}$/.test(code) ? fetch(API(`/api/consensus/${code}`)) : Promise.resolve(null),
      ]);

      if (isStale()) return;  // 종목 전환됨 → 결과 버림

      const chartDataFetched = chartRes.ok ? await chartRes.json() : [];
      if (chartRes.ok)   setChartData(chartDataFetched);
      if (tableRes.ok)   setFinTable(await tableRes.json());
      if (quarterRes.ok) setQuarterTable(await quarterRes.json());
      if (aiRes.ok)      setAiReport(await aiRes.json());
      if (consRes?.ok)   { const cd = await consRes.json(); if (!isStale()) setConsensus(cd); }

      const cfAData = cfARes.ok ? await cfARes.json() : [];
      const cfQData = cfQRes.ok ? await cfQRes.json() : [];
      if (!isStale()) { setCfAnnual(cfAData); setCfQuarter(cfQData); }

      // 현금흐름 백그라운드 수집 중 → 15초 간격으로 최대 8회 폴링
      if (cfAData.length === 0 && /^\d{6}$/.test(code)) {
        if (cfPollRef.current) clearInterval(cfPollRef.current);
        let cfTry = 0;
        cfPollRef.current = setInterval(async () => {
          if (isStale() || cfTry >= 8) { clearInterval(cfPollRef.current); return; }
          cfTry++;
          try {
            const [ra, rq] = await Promise.all([
              fetch(API(`/api/dashboard/cashflow/${code}?type=annual`)),
              fetch(API(`/api/dashboard/cashflow/${code}?type=quarter`)),
            ]);
            if (isStale()) { clearInterval(cfPollRef.current); return; }
            const da = ra.ok ? await ra.json() : [];
            const dq = rq.ok ? await rq.json() : [];
            if (da.length > 0 || dq.length > 0) {
              setCfAnnual(da); setCfQuarter(dq);
              clearInterval(cfPollRef.current);
            }
          } catch {}
        }, 15000);
      }

      if (summRes.ok) {
        const sData = await summRes.json();
        if (!isStale()) setSummStats(sData);

        // PBR/PER가 null이면 백그라운드 스크래핑 중 → 5초 후 재조회
        if (sData && sData.pbr === null && sData.per === null && /^\d{6}$/.test(code)) {
          setTimeout(async () => {
            if (isStale()) return;
            try {
              const r2 = await fetch(API(`/api/dashboard/fundamentals/${code}`));
              if (r2.ok && !isStale()) {
                const d2 = await r2.json();
                if (d2?.pbr !== null || d2?.per !== null) setSummStats(d2);
              }
            } catch {}
          }, 5000);
        }

        if (sData?.collecting) {
          if (!isStale()) setCollecting(true);
          let pollCount = 0;
          const poll = async () => {
            if (isStale()) return;  // ★ 종목 바뀌면 폴링 즉시 중단
            pollCount++;
            if (pollCount > 24) { if (!isStale()) setCollecting(false); return; }
            await new Promise(r => setTimeout(r, 10000));
            if (isStale()) return;  // ★ 대기 후 다시 체크
            try {
              const [c2, t2, q2, s2, cf2a, cf2q] = await Promise.all([
                fetch(API(`/api/dashboard/chart/${code}?days=365`)),
                fetch(API(`/api/dashboard/financial-table/${code}?type=annual`)),
                fetch(API(`/api/dashboard/financial-table/${code}?type=quarter`)),
                fetch(API(`/api/dashboard/fundamentals/${code}`)),
                fetch(API(`/api/dashboard/cashflow/${code}?type=annual`)),
                fetch(API(`/api/dashboard/cashflow/${code}?type=quarter`)),
              ]);
              if (isStale()) return;
              if (c2.ok) { const cd = await c2.json(); if (!isStale() && cd.length > 0) setChartData(cd); }
              if (t2.ok) { const td = await t2.json(); if (!isStale() && td.length > 0) setFinTable(td); }
              if (q2.ok) { const qd = await q2.json(); if (!isStale() && qd.length > 0) setQuarterTable(qd); }
              if (cf2a.ok) { const d = await cf2a.json(); if (!isStale() && d.length > 0) setCfAnnual(d); }
              if (cf2q.ok) { const d = await cf2q.json(); if (!isStale() && d.length > 0) setCfQuarter(d); }
              if (s2.ok) {
                const s2d = await s2.json();
                if (!isStale()) {
                  setSummStats(s2d);
                  if (s2d?.collecting) poll();
                  else setCollecting(false);
                }
              } else { if (!isStale()) setCollecting(false); }
            } catch { if (!isStale()) setCollecting(false); }
          };
          poll();
        } else {
          // 데이터가 없으면 10초 후 1회 재시도
          const hasNoData = chartDataFetched.length === 0;
          if (hasNoData) {
            setTimeout(async () => {
              if (isStale()) return;  // ★ 타이머 발동 전에 종목 바뀌면 취소
              try {
                const [c3, t3, q3] = await Promise.all([
                  fetch(API(`/api/dashboard/chart/${code}?days=365`)),
                  fetch(API(`/api/dashboard/financial-table/${code}?type=annual`)),
                  fetch(API(`/api/dashboard/financial-table/${code}?type=quarter`)),
                ]);
                if (isStale()) return;
                if (c3.ok) { const cd = await c3.json(); if (!isStale() && cd.length > 0) setChartData(cd); }
                if (t3.ok) { const td = await t3.json(); if (!isStale() && td.length > 0) setFinTable(td); }
                if (q3.ok) { const qd = await q3.json(); if (!isStale() && qd.length > 0) setQuarterTable(qd); }
              } catch {}
            }, 10000);
          }
        }
      }
    } catch (e) { console.error("Detail load error", e); }
    finally { if (!isStale()) setLoading(false); }
  }, [selectedStock]);

  useEffect(() => { fetchWatchlist(); fetchSystem(); }, []);

  // ── reportType(연결/별도) 변경 시 재무제표·현금흐름 재조회 ──────────
  React.useEffect(() => {
    if (!selectedStock || !/^\d{6}$/.test(selectedStock)) return;
    const controller = new AbortController();
    Promise.all([
      fetch(API(`/api/dashboard/financial-table/${selectedStock}?type=annual&report_type=${reportType}`), { signal: controller.signal }),
      fetch(API(`/api/dashboard/financial-table/${selectedStock}?type=quarter&report_type=${reportType}`), { signal: controller.signal }),
      fetch(API(`/api/dashboard/cashflow/${selectedStock}?type=annual&report_type=${reportType}`), { signal: controller.signal }),
      fetch(API(`/api/dashboard/cashflow/${selectedStock}?type=quarter&report_type=${reportType}`), { signal: controller.signal }),
    ]).then(async ([t, q, ca, cq]) => {
      if (t.ok) setFinTable(await t.json());
      if (q.ok) setQuarterTable(await q.json());
      if (ca.ok) setCfAnnual(await ca.json());
      if (cq.ok) setCfQuarter(await cq.json());
    }).catch(() => {});
    return () => controller.abort();
  }, [reportType, selectedStock]);

  // ── 매크로 300초 폴링 ────────────────────────────────────────
  // 앱 마운트 시 즉시 1회 + 이후 300초마다 자동 갱신
  useEffect(() => {
    fetchMacro(); // 마운트 즉시 1회
    const interval = anyMarketOpen()?300000:null;
    const iv = interval?setInterval(fetchMacro,interval):null;
    return ()=>{if(iv)clearInterval(iv);};
  }, [fetchMacro]);

  useEffect(() => {
    if (activeTab === "analysis" || activeTab === "insight") fetchStockDetail();
  }, [selectedStock, activeTab]);

  // 개별종목 탭 진입/종목 변경 시 본문 스크롤을 항상 맨 위로 초기화
  useEffect(() => {
    if (activeTab !== "analysis") return;
    const el = document.getElementById('main-scroll');
    if (el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: 0, behavior: 'auto' });
    }
  }, [activeTab, selectedStock]);

  const handleSearch = async (e, overrideCode = null) => {
    if (e) e.preventDefault();
    const q = overrideCode || searchQuery.trim();
    if (!q) return;
    setShowSearchDrop(false); setSearchResults([]);
    // 드롭다운 클릭: 즉시 네비게이션 후 백그라운드 fetch
    if (overrideCode) {
      changeStock(overrideCode);
      changeTab("analysis");
      setSearchQuery("");
      fetch(API(`/api/commands/analyze/${encodeURIComponent(overrideCode)}`), { method: 'POST' })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.stock_name) setSelectedStockName(d.stock_name); fetchWatchlist(); })
        .catch(() => {});
      return;
    }
    // Enter 키: API 응답 대기 후 이동 (종목코드 모를 때)
    setLoading(true);
    try {
      const res = await fetch(API(`/api/commands/analyze/${encodeURIComponent(q)}`), { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        changeStock(data.stock_code);
        if (data.stock_name) setSelectedStockName(data.stock_name);
        setSearchQuery("");
        fetchWatchlist();
        changeTab("analysis");
      }
    } catch (e) { console.error("Search error", e); }
    finally { setLoading(false); }
  };

  // 헤더 검색 자동완성
  React.useEffect(() => {
    if (!searchQuery.trim()) { setSearchResults([]); setShowSearchDrop(false); return; }
    const t = setTimeout(async () => {
      try {
        const res = await fetch(API(`/api/search?q=${encodeURIComponent(searchQuery.trim())}`));
        if (res.ok) {
          const data = await res.json();
          setSearchResults(data);
          setShowSearchDrop(data.length > 0);
        }
      } catch {}
    }, 200);
    return () => clearTimeout(t);
  }, [searchQuery]);

  // ── 전역 포맷터 ─────────────────────────────────────────────────────
  // fmtWonRaw: 원화 원값(원 단위)을 사람이 읽기 쉬운 단위로 변환
  const formatWon = (val) => {
    if (val == null || val === "" || val === "N/A") return "-";
    const n = Number(val); if (isNaN(n)) return "-";
    const abs = Math.abs(n), sign = n < 0 ? "-" : "";
    if (abs >= 1e12) return sign + (abs/1e12).toLocaleString('ko-KR',{maximumFractionDigits:1}) + "조원";
    if (abs >= 1e8)  return sign + Math.round(abs/1e8).toLocaleString('ko-KR') + "억원";
    if (abs >= 1e4)  return sign + Math.round(abs/1e4).toLocaleString('ko-KR') + "만원";
    return sign + Math.round(abs).toLocaleString('ko-KR') + "원";
  };
  // fmtPct: % 값 소수점 1자리 표시
  const fmtPct = (v, showSign = false) => {
    if (v == null) return '-';
    const n = Number(v); if (isNaN(n)) return '-';
    const sign = showSign ? (n >= 0 ? '+' : '') : '';
    return sign + n.toFixed(1) + '%';
  };
  // fmtUkWon: 억원 단위 입력값 → 조원/억원 표시 (재무제표용)
  const fmtUkWon = (v) => {
    if (v == null) return '-';
    const n = Number(v); if (isNaN(n)) return '-';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 10000) return sign + (abs / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 1 }) + '조원';
    return sign + Math.round(abs).toLocaleString('ko-KR') + '억원';
  };
  // fmtNum: 정수 콤마 표시 (소수점 없음)
  const fmtNum = (v) => {
    if (v == null) return '-';
    const n = Number(v); if (isNaN(n)) return '-';
    return Math.round(n).toLocaleString('ko-KR');
  };
  // ──────────────────────────────────────────────────────────────────

  const handleRemoveWatchlist = async (stock_code) => {
    try {
      const res = await fetch(API(`/api/commands/watchlist/${stock_code}`), { method: 'DELETE' });
      if (res.ok) setWatchlist(prev => prev.filter(i => i.stock_code !== stock_code));
    } catch (e) { console.error("Watchlist delete error", e); }
  };

  // ── 탭별 타이틀 ─────────────────────────────────────────────
  const TAB_TITLES = {
    macro:          "Global Market Overview",
    analysis:       "개별 종목",
    semiconductor_sector: "반도체 섹터",
    watchlist:      "관심종목 리스트",
    buy_candidates: "📋 매수 후보 시그널 보드",
    portfolio:      "계좌현황",
    settings:       "시스템 설정",
    screener:       "AI 종목 스크리너",
    trend:          "가상 매매 Leading",
    reports:        "섹터 보고서",
    insight:        "AI Analysis Deep Insight",
    system:         "Database Management",
    telegram:       "텔레그램 종목 언급 순위",
    hs_trade:       "수출입분석",
    hs_trade2:      "수출입 분석",
  };

  // ── 매수후보 시그널 보드 ────────────────────────────────────────
  const BuyCandidateView = () => {
    const [candidates, setCandidates]   = React.useState([]);
    const [loading,    setLoading]      = React.useState(true);
    const [editId,     setEditId]       = React.useState(null);
    const [editForm,   setEditForm]     = React.useState({});
    const [addQuery,   setAddQuery]     = React.useState('');
    const [searchRes,  setSearchRes]    = React.useState([]);
    const [showDrop,   setShowDrop]     = React.useState(false);
    const [adding,     setAdding]       = React.useState(false);
    const [refDate1Label, setRefDate1Label] = React.useState('2026-01-01');
    const [refDate2Label, setRefDate2Label] = React.useState('2025-10-01');
    const [editRefDate1, setEditRefDate1]   = React.useState(false);
    const [editRefDate2, setEditRefDate2]   = React.useState(false);

    const load = () => {
      setLoading(true);
      fetch(API('/api/buy-candidates')).then(r=>r.ok?r.json():[]).then(d=>{
        // 정렬: 1) 목표가 도달(현재가<=목표가) 2) 매수신호(strong_buy>buy) 3) 기준일1 상승률
        const sorted = [...d].sort((a,b) => {
          const aReached = a.target_price && a.current_price && a.current_price <= a.target_price;
          const bReached = b.target_price && b.current_price && b.current_price <= b.target_price;
          if(aReached && !bReached) return -1;
          if(!aReached && bReached) return 1;
          const sigOrder = {add_buy:0,strong_buy:0,hold:1,hold_value:2,take_profit:3,caution:3,sell:4,real_sell:4,cut_loss:5,strong_sell:5};
          const aSig = sigOrder[a.trade_signal] ?? 2;
          const bSig = sigOrder[b.trade_signal] ?? 2;
          if(aSig !== bSig) return aSig - bSig;
          return (b.ref_chg1||0) - (a.ref_chg1||0);
        });
        setCandidates(sorted);
        setLoading(false);
      }).catch(()=>setLoading(false));
    };
    React.useEffect(()=>{ load(); }, []);

    // 종목 검색
    React.useEffect(()=>{
      if(!addQuery.trim()){ setSearchRes([]); setShowDrop(false); return; }
      const t = setTimeout(async()=>{
        const res = await fetch(API(`/api/search?q=${encodeURIComponent(addQuery)}`));
        if(res.ok){ setSearchRes(await res.json()); setShowDrop(true); }
      }, 300);
      return ()=>clearTimeout(t);
    }, [addQuery]);

    const addCandidate = async (code, name) => {
      setAdding(true); setShowDrop(false); setAddQuery('');
      await fetch(API('/api/buy-candidates'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({stock_code:code, stock_name:name}),
      });
      setAdding(false); load();
    };

    const deleteCandidate = async (code) => {
      if(!window.confirm(`${code} 매수후보에서 삭제하시겠습니까?`)) return;
      await fetch(API(`/api/buy-candidates/${code}`), {method:'DELETE'});
      load();
    };

    const saveEdit = async (code) => {
      await fetch(API(`/api/buy-candidates/${code}`), {
        method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(editForm),
      });
      setEditId(null); setEditForm({}); load();
    };

    const SIG = {
      strong_buy:  {emoji:'🟢', label:'강매수',  color:'#22c55e', bg:'rgba(34,197,94,0.15)'},
      buy:         {emoji:'🟢', label:'매수',    color:'#22c55e', bg:'rgba(34,197,94,0.08)'},
      hold:        {emoji:'🟡', label:'대기',    color:'#fbbf24', bg:'rgba(251,191,36,0.1)'},
      caution:     {emoji:'🟠', label:'주의',    color:'#f97316', bg:'rgba(249,115,22,0.12)'},
      sell:        {emoji:'🔴', label:'진입불가', color:'#ef4444', bg:'rgba(239,68,68,0.1)'},
      strong_sell: {emoji:'🔴', label:'강진입불가',color:'#dc2626',bg:'rgba(220,38,38,0.15)'},
    };

    const fp = (v) => v ? Math.round(v).toLocaleString('ko-KR') : '-';
    const pc = (v) => !v ? 'rgba(255,255,255,0.4)' : v>0?'#ef4444':'#3b82f6';
    const pctStr = (v) => v==null?'-':(v>=0?'+':'')+Number(v).toFixed(1)+'%';
    const fmtMkt = (v) => {
      if(!v) return '-';
      // stock_universe.market_cap 단위: 원(KRW)
      if(v >= 1e12) return (v/1e12).toLocaleString('ko-KR',{maximumFractionDigits:1})+'조원';
      if(v >= 1e8)  return Math.round(v/1e8).toLocaleString('ko-KR')+'억원';
      return Math.round(v/1e4).toLocaleString('ko-KR')+'만원';
    };

    const inputSt = {padding:'0.25rem 0.5rem',borderRadius:'5px',background:'rgba(255,255,255,0.08)',
      border:'1px solid rgba(255,255,255,0.2)',color:'#fff',fontSize:'0.78rem',width:'100%'};

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
        {/* 헤더 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem',display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.75rem',position:'relative'}}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem'}}>
            <Target size={20} color="#f59e0b"/>
            <h2 style={{fontSize:'1rem',fontWeight:700}}>매수 후보 시그널 보드</h2>
            <span style={{padding:'0.15rem 0.6rem',background:'rgba(245,158,11,0.15)',borderRadius:'20px',fontSize:'0.72rem',color:'#f59e0b'}}>
              {candidates.length}종목
            </span>
          </div>
          <div style={{display:'flex',gap:'0.5rem',position:'relative'}}>
            <input value={addQuery} onChange={e=>setAddQuery(e.target.value)}
              placeholder="종목명 검색 후 추가..."
              style={{padding:'0.4rem 0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.06)',
                border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.82rem',width:'200px'}}/>
            {showDrop && searchRes.length>0 && (
              <div style={{position:'absolute',top:'100%',left:0,right:0,marginTop:'3px',
                background:'rgba(20,20,35,0.97)',border:'1px solid var(--glass-border)',
                borderRadius:'8px',zIndex:50,overflow:'hidden',boxShadow:'0 8px 24px rgba(0,0,0,0.5)'}}>
                {searchRes.slice(0,8).map((item,i)=>(
                  <div key={i} onClick={()=>addCandidate(item.code,item.name)}
                    style={{padding:'0.5rem 0.8rem',cursor:'pointer',display:'flex',justifyContent:'space-between',
                      borderBottom:'1px solid rgba(255,255,255,0.05)',fontSize:'0.82rem'}}
                    onMouseEnter={e=>e.currentTarget.style.background='rgba(245,158,11,0.1)'}
                    onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
                    <span style={{fontWeight:600}}>{item.name}</span>
                    <span style={{color:'var(--text-secondary)'}}>{item.code}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.35)'}}>
            💡 행 더블클릭 → 목표가/기준일 수정 가능
          </div>
        </div>

        {/* 테이블 */}
        {loading ? (
          <div style={{textAlign:'center',padding:'3rem',color:'var(--text-secondary)'}}>로딩 중...</div>
        ) : candidates.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <Target size={40} style={{margin:'0 auto 1rem',display:'block',opacity:0.3}}/>
            <p>매수 후보 종목을 추가해주세요.</p>
          </div>
        ) : (
          <div className="glass-panel" style={{overflow:'clip'}}>
            <table className="premium-table" style={{width:'100%',minWidth:'1100px'}}>
              <thead><tr>
                <th style={{minWidth:'110px'}}>기업명</th>
                <th style={{textAlign:'right',minWidth:'80px'}}>시총</th>
                <th style={{textAlign:'center',minWidth:'65px'}}>매수신호</th>
                <th style={{textAlign:'right',minWidth:'80px'}}>현재가</th>
                <th style={{textAlign:'right',minWidth:'70px'}}>변동(%)</th>
                <th style={{textAlign:'right',minWidth:'85px'}}>목표매수가</th>
                <th style={{textAlign:'center',minWidth:'130px',cursor:'pointer'}}>
                  {editRefDate1 ? (
                    <input value={refDate1Label} onChange={e=>setRefDate1Label(e.target.value)}
                      onBlur={()=>{setEditRefDate1(false);}}
                      onKeyDown={e=>{ if(e.key==='Enter') setEditRefDate1(false); }}
                      autoFocus style={{width:'100px',padding:'2px 4px',borderRadius:'4px',
                        background:'rgba(255,255,255,0.1)',border:'1px solid #f59e0b',
                        color:'#fff',fontSize:'0.72rem',textAlign:'center'}}/>
                  ) : (
                    <span onClick={()=>setEditRefDate1(true)}
                      title="클릭하여 날짜 변경"
                      style={{color:'#f59e0b',cursor:'pointer',textDecoration:'underline dotted'}}>
                      {refDate1Label} 대비 ✎
                    </span>
                  )}
                </th>
                <th style={{textAlign:'center',minWidth:'130px',cursor:'pointer'}}>
                  {editRefDate2 ? (
                    <input value={refDate2Label} onChange={e=>setRefDate2Label(e.target.value)}
                      onBlur={()=>setEditRefDate2(false)}
                      onKeyDown={e=>{ if(e.key==='Enter') setEditRefDate2(false); }}
                      autoFocus style={{width:'100px',padding:'2px 4px',borderRadius:'4px',
                        background:'rgba(255,255,255,0.1)',border:'1px solid #f59e0b',
                        color:'#fff',fontSize:'0.72rem',textAlign:'center'}}/>
                  ) : (
                    <span onClick={()=>setEditRefDate2(true)}
                      title="클릭하여 날짜 변경"
                      style={{color:'#f59e0b',cursor:'pointer',textDecoration:'underline dotted'}}>
                      {refDate2Label} 대비 ✎
                    </span>
                  )}
                </th>
                <th style={{textAlign:'left',minWidth:'180px'}}>추세추종 / 차트신호</th>
                <th style={{minWidth:'70px'}}></th>
              </tr></thead>
              <tbody>
                {candidates.map(h => {
                  const sig = SIG[h.trade_signal] || SIG.hold;
                  const isEdit = editId === h.stock_code;
                  return (
                    <tr key={h.stock_code}
                      onDoubleClick={()=>{ setEditId(h.stock_code); setEditForm({
                        target_price: h.target_price||'',
                        ref_date1: h.ref_date1||'', ref_price1: h.ref_price1||'',
                        ref_date2: h.ref_date2||'', ref_price2: h.ref_price2||'',
                        memo: h.memo||'',
                      }); }}
                      style={{cursor:'pointer',background:isEdit?'rgba(245,158,11,0.05)':undefined}}>

                      {/* 기업명 */}
                      <td onClick={()=>{changeStock(h.stock_code);changeTab('analysis');}}>
                        <div style={{fontWeight:700,fontSize:'0.85rem',color:'var(--text-primary)',cursor:'pointer'}}>
                          {h.stock_name}
                        </div>
                        <div style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{h.stock_code}</div>
                      </td>

                      {/* 시총 */}
                      <td style={{textAlign:'right',fontSize:'0.8rem',color:'var(--text-secondary)'}}>{fmtMkt(h.mktcap)}</td>

                      {/* 매수 신호등 */}
                      <td style={{textAlign:'center'}}>
                        <div title={h.trade_reason} style={{display:'inline-flex',flexDirection:'column',alignItems:'center',
                          padding:'2px 6px',borderRadius:'6px',background:sig.bg,cursor:'help'}}>
                          <span style={{fontSize:'1rem',lineHeight:1}}>{sig.emoji}</span>
                          <span style={{fontSize:'0.58rem',color:sig.color,fontWeight:700}}>{sig.label}</span>
                        </div>
                      </td>

                      {/* 현재가 */}
                      <td style={{textAlign:'right',fontWeight:700,fontSize:'0.88rem'}}>
                        {h.current_price ? fp(h.current_price)+'원' : '-'}
                      </td>

                      {/* 등락률 */}
                      <td style={{textAlign:'right',fontWeight:600,color:pc(h.change_pct)}}>
                        {pctStr(h.change_pct)}
                      </td>

                      {/* 목표매수가 */}
                      <td style={{textAlign:'right'}}>
                        {isEdit ? (
                          <input value={editForm.target_price} onChange={e=>setEditForm(p=>({...p,target_price:e.target.value}))}
                            style={inputSt} placeholder="목표가"/>
                        ) : (
                          <span style={{fontSize:'0.85rem',color:'#f59e0b',fontWeight:700}}>
                            {h.target_price ? fp(h.target_price)+'원' : <span style={{color:'rgba(255,255,255,0.3)',fontSize:'0.75rem'}}>미설정</span>}
                          </span>
                        )}
                        {h.target_price && h.current_price && !isEdit && (() => {
                          const diff = ((h.current_price - h.target_price) / h.target_price * 100);
                          const reached = h.current_price <= h.target_price;
                          return (
                            <div style={{fontSize:'0.65rem',marginTop:'2px',fontWeight:600,
                              color:reached?'#22c55e':'rgba(255,255,255,0.5)'}}>
                              {reached
                                ? '✓ 목표가 ▼'+Math.abs(diff).toFixed(1)+'%'
                                : '▲ '+diff.toFixed(1)+'% 위'}
                            </div>
                          );
                        })()}
                      </td>

                      {/* 기준일1 대비 */}
                      <td style={{textAlign:'center'}}>
                        {isEdit ? (
                          <div style={{display:'flex',flexDirection:'column',gap:'2px'}}>
                            <input value={editForm.ref_date1} onChange={e=>setEditForm(p=>({...p,ref_date1:e.target.value}))}
                              style={inputSt} placeholder="2026-01-01"/>
                            <input value={editForm.ref_price1} onChange={e=>setEditForm(p=>({...p,ref_price1:e.target.value}))}
                              style={inputSt} placeholder="기준가"/>
                          </div>
                        ) : h.ref_price1 ? (
                          <div style={{textAlign:'center'}}>
                            <div style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{fp(h.ref_price1)}원</div>
                            <div style={{fontSize:'0.85rem',fontWeight:700,color:pc(h.ref_chg1)}}>{pctStr(h.ref_chg1)}</div>
                          </div>
                        ) : <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>미설정</span>}
                      </td>

                      {/* 기준일2 대비 */}
                      <td style={{textAlign:'center'}}>
                        {isEdit ? (
                          <div style={{display:'flex',flexDirection:'column',gap:'2px'}}>
                            <input value={editForm.ref_date2} onChange={e=>setEditForm(p=>({...p,ref_date2:e.target.value}))}
                              style={inputSt} placeholder="2025-10-01"/>
                            <input value={editForm.ref_price2} onChange={e=>setEditForm(p=>({...p,ref_price2:e.target.value}))}
                              style={inputSt} placeholder="기준가"/>
                          </div>
                        ) : h.ref_price2 ? (
                          <div style={{textAlign:'center'}}>
                            <div style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{fp(h.ref_price2)}원</div>
                            <div style={{fontSize:'0.85rem',fontWeight:700,color:pc(h.ref_chg2)}}>{pctStr(h.ref_chg2)}</div>
                          </div>
                        ) : <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>미설정</span>}
                      </td>

                      {/* 추세추종 / 차트신호 */}
                      <td>
                        <div title={h.trade_reason} style={{fontSize:'0.72rem',color:sig.color,lineHeight:1.4,cursor:'help'}}>
                          {h.trade_reason ? h.trade_reason.split('[')[0].trim() : '-'}
                        </div>
                        {h.trade_reason && h.trade_reason.includes('[') && (
                          <div style={{fontSize:'0.62rem',color:'rgba(255,255,255,0.3)',marginTop:'2px'}}>
                            {h.trade_reason.match(/\[.*?\]/)?.[0]}
                          </div>
                        )}
                      </td>

                      {/* 액션 버튼 */}
                      <td>
                        {isEdit ? (
                          <div style={{display:'flex',flexDirection:'column',gap:'3px'}}>
                            <button onClick={()=>saveEdit(h.stock_code)}
                              style={{padding:'0.2rem 0.5rem',borderRadius:'4px',border:'none',
                                background:'#f59e0b',color:'#000',cursor:'pointer',fontSize:'0.72rem',fontWeight:700}}>저장</button>
                            <button onClick={()=>{setEditId(null);setEditForm({});}}
                              style={{padding:'0.2rem 0.5rem',borderRadius:'4px',
                                border:'1px solid var(--glass-border)',background:'transparent',
                                color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.72rem'}}>취소</button>
                          </div>
                        ) : (
                          <button onClick={()=>deleteCandidate(h.stock_code)}
                            style={{padding:'0.2rem 0.45rem',borderRadius:'4px',border:'none',
                              background:'rgba(239,68,68,0.12)',color:'#ef4444',cursor:'pointer',fontSize:'0.72rem'}}>삭제</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* 범례 */}
        <div className="glass-panel" style={{padding:'0.75rem 1rem',display:'flex',gap:'1rem',flexWrap:'wrap',alignItems:'center'}}>
          <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.4)',fontWeight:600}}>신호 기준:</span>
          {Object.entries(SIG).map(([k,v])=>(
            <span key={k} style={{fontSize:'0.7rem',color:v.color}}>
              {v.emoji} {v.label}
            </span>
          ))}
          <span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.3)',marginLeft:'auto'}}>
            더블클릭 → 목표가/기준일/기준가 수정
          </span>
        </div>
      </div>
    );
  };


  // ── 관심종목 ─────────────────────────────────────────────────
  const WatchlistView = () => {
    const [addQuery, setAddQuery] = React.useState("");
    const [adding, setAdding] = React.useState(false);
    const [searchResults, setSearchResults] = React.useState([]);
    const [showDropdown, setShowDropdown] = React.useState(false);

    useEffect(() => {
      if (!addQuery.trim()) { setSearchResults([]); setShowDropdown(false); return; }
      const t = setTimeout(async () => {
        try {
          const res = await fetch(API(`/api/search?q=${encodeURIComponent(addQuery)}`));
          if (res.ok) { setSearchResults(await res.json()); setShowDropdown(true); }
        } catch {}
      }, 300);
      return () => clearTimeout(t);
    }, [addQuery]);

    const handleAdd = async (query = addQuery, e = null) => {
      if (e) e.preventDefault();
      if (!query.trim()) return;
      setAdding(true); setShowDropdown(false);
      try {
        const res = await fetch(API(`/api/commands/analyze/${encodeURIComponent(query.trim())}`), { method: 'POST' });
        if (res.ok) { setAddQuery(""); fetchWatchlist(); }
        else { const d = await res.json(); alert(`종목 추가 실패: ${d.detail || '알 수 없는 오류'}`); }
      } catch { alert('네트워크 오류가 발생했습니다.'); }
      finally { setAdding(false); }
    };

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '1.2rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'relative' }}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            <Star size={20} color="var(--accent-purple)" />
            <h2 style={{ fontSize: '1.1rem' }}>관심종목 리스트</h2>
            <span style={{ marginLeft: '0.5rem', padding: '0.2rem 0.7rem', background: 'rgba(167,139,250,0.15)', borderRadius: '20px', fontSize: '0.75rem', color: 'var(--accent-purple)' }}>
              {watchlist.length}종목
            </span>
          </div>
          <form onSubmit={(e) => handleAdd(addQuery, e)} style={{ display: 'flex', gap: '0.5rem' }}>
            <input
              type="text" placeholder="종목명 입력 (ex. 삼성전자)"
              value={addQuery} onChange={e => setAddQuery(e.target.value)}
              onFocus={() => { if (searchResults.length > 0) setShowDropdown(true); }}
              style={{ padding: '0.45rem 0.9rem', borderRadius: '8px', background: 'rgba(255,255,255,0.06)', border: '1px solid var(--glass-border)', color: '#fff', fontSize: '0.85rem', width: '220px' }}
            />
            <button type="submit" disabled={adding} style={{ padding: '0.45rem 1rem', borderRadius: '8px', background: adding ? 'rgba(167,139,250,0.3)' : 'var(--accent-purple)', border: 'none', color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600 }}>
              <Plus size={15} />{adding ? '추가 중...' : '추가'}
            </button>
          </form>
          {showDropdown && searchResults.length > 0 && (
            <div style={{ position: 'absolute', top: '100%', right: '1.2rem', width: '300px', background: 'rgba(20,20,35,0.95)', backdropFilter: 'blur(10px)', border: '1px solid var(--glass-border)', borderRadius: '8px', marginTop: '4px', zIndex: 50, boxShadow: '0 10px 30px rgba(0,0,0,0.5)' }}>
              {searchResults.map((item, idx) => (
                <div key={idx} onClick={() => handleAdd(item.code)}
                  style={{ padding: '0.75rem 1rem', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', borderBottom: idx === searchResults.length - 1 ? 'none' : '1px solid rgba(255,255,255,0.05)' }}
                  onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.1)'}
                  onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                  <span style={{ fontWeight: 600 }}>{item.name}</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{item.code}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        {watchlist.length === 0 ? (
          <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <StarOff size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.4 }} />
            <p>등록된 관심종목이 없습니다.</p>
            <p style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>위 검색상자에 종목명을 입력해 주세요.</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '0.75rem' }}>
            {watchlist.map((item, idx) => (
              <div key={item.stock_code} className="glass-panel"
                style={{ padding: '1.2rem 1.4rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', border: selectedStock === item.stock_code ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)', animation: `fadeIn 0.3s ease ${idx * 0.04}s both` }}>
                <div style={{ flex: 1, cursor: 'pointer' }} onClick={() => { changeStock(item.stock_code); changeTab('analysis'); }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
                    <span style={{ fontSize: '0.65rem', padding: '0.15rem 0.5rem', background: 'rgba(45,212,191,0.1)', borderRadius: '4px', color: 'var(--accent-mint)' }}>{item.stock_code}</span>
                    {selectedStock === item.stock_code && <span style={{ fontSize: '0.65rem', color: 'var(--accent-mint)' }}>● 선택중</span>}
                  </div>
                  <p style={{ fontWeight: 700, fontSize: '0.95rem' }}>{item.stock_name || item.stock_code}</p>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button onClick={() => { changeStock(item.stock_code); changeTab('analysis'); }} style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', border: 'none', background: 'rgba(45,212,191,0.15)', color: 'var(--accent-mint)', cursor: 'pointer' }}><Eye size={15} /></button>
                  <button onClick={() => handleRemoveWatchlist(item.stock_code)} style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', border: 'none', background: 'rgba(251,113,133,0.12)', color: 'var(--accent-red)', cursor: 'pointer' }}><Trash2 size={15} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };


  // ── 시그널 보드 컴포넌트 ────────────────────────────────────

  const SignalBoard = ({ scope, stockCode = '' }) => {
    const [signals,  setSignals]  = React.useState([]);
    const [loading,  setLoading]  = React.useState(false);
    const [expanded, setExpanded] = React.useState(false);
    const [showGuide, setShowGuide] = React.useState(false); // 로직 가이드 토글
    const [regime, setRegime] = React.useState(null);

    React.useEffect(() => {
      if (!stockCode && scope !== 'market') return;
      const cacheKey = scope === 'market' ? 'market' : stockCode;
      const cached = _signalFrontCache[cacheKey];
      const now = Date.now();
      // 장중 1시간 / 장외 4시간 캐시 — 백엔드와 동일
      // 시장 국면/시장 시그널은 체감 지연을 줄이기 위해 더 짧은 TTL 사용
      const frontTtl = scope === 'market'
        ? (isKRMarketOpen() ? 60000 : 300000)  // 장중 1분 / 장외 5분
        : (isKRMarketOpen() ? 3600000 : 14400000);
      if (cached && (now - cached.at) < frontTtl) {
        setSignals(cached.data);
        setLoading(false);
        return;
      }
      setLoading(true);
      const url = scope === 'market'
        ? API('/api/signals/market')
        : API(`/api/signals/stock/${stockCode}`);
      fetch(url)
        .then(r => r.ok ? r.json() : [])
        .then(d => {
          const sigs = Array.isArray(d) ? d : (d?.signals || []);
          _signalFrontCache[cacheKey] = { data: sigs, at: Date.now() };
          setSignals(sigs);
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }, [scope, stockCode]);

    React.useEffect(() => {
      if (scope !== 'market') return;
      let alive = true;
      const loadRegime = () => {
        fetch(API(`/api/signals/market-regime?t=${Date.now()}`))
          .then(r => r.ok ? r.json() : null)
          .then(d => { if (alive) setRegime(d); })
          .catch(() => {});
      };
      loadRegime(); // 페이지 진입 시 즉시 재조회
      const iv = setInterval(loadRegime, isKRMarketOpen() ? 60000 : 300000); // 장중 1분 / 장외 5분
      return () => { alive = false; clearInterval(iv); };
    }, [scope]);

    const C = {
      green:  { bg:'rgba(34,197,94,0.12)',  border:'rgba(34,197,94,0.4)',   text:'#22c55e', dot:'#22c55e',  light:'rgba(34,197,94,0.06)' },
      yellow: { bg:'rgba(251,191,36,0.12)', border:'rgba(251,191,36,0.4)',  text:'#fbbf24', dot:'#fbbf24',  light:'rgba(251,191,36,0.06)' },
      red:    { bg:'rgba(239,68,68,0.12)',  border:'rgba(239,68,68,0.4)',   text:'#ef4444', dot:'#ef4444',  light:'rgba(239,68,68,0.06)' },
      gray:   { bg:'rgba(255,255,255,0.04)',border:'var(--glass-border)',   text:'#64748b', dot:'#475569',  light:'rgba(255,255,255,0.02)' },
    };

    // ── 시그널별 상세 로직 설명 ──────────────────────────────────
    const SIGNAL_GUIDE = {
      // 시장 시그널(신규 5단계 로직 기준)
      market_regime_5stage: {
        basis: '6개 그룹 점수 합산(추세/미국금리/밸류/수급/리스크/시장폭) → 0~100점',
        criteria: '80~100:1단계, 65~79:2단계, 45~64:3단계, 25~44:4단계, 0~24:5단계',
        action: '1~2단계만 신규매수 검토. 4~5단계는 신규매수 제한/금지 및 방어 우선',
        note: 'MA200 위는 매수 허가가 아니라 최소 조건'
      },
      market_forced_downgrade: {
        basis: '강제 하향 조건 카운트',
        criteria: '4단계 트리거 2개↑면 최소 4단계, 5단계 트리거 2개↑면 즉시 5단계',
        action: '점수가 높아도 강제 하향 조건 충족 시 공격적 매수 차단',
        note: '금리·환율·변동성·시장폭 악화 시 우선적으로 리스크 관리'
      },
      trend_follow: {
        basis: '추세 그룹 점수(지수 MA200 상단/기울기/괴리/MA50)',
        criteria: 'KOSPI/KOSDAQ 추세 점수 합산 비율로 green/yellow/red 판정',
        action: '신규 매수 검토의 최소 조건(충분조건 아님)'
      },
      us10y_rate: {
        basis: '미국 10년물 금리 레벨 + 20일 변화폭',
        criteria: '금리 그룹 점수 높을수록 완화, 낮을수록 부담',
        action: '4.7% 상회·상승 추세면 매수 제한 강화'
      },
      us30y_rate: {
        basis: '미국 30년물 금리 레벨',
        criteria: '5.0% 상회 시 위험 신호 가중',
        action: '장기 금리 부담 확대로 밸류에이션 리레이팅 점검'
      },
      valuation_forward: {
        basis: '미국/국내 Forward PER·PBR 상대 밸류',
        criteria: '과열 구간일수록 감점',
        action: '추세가 좋아도 고평가 과열이면 신규 매수 속도 조절'
      },
      foreign_flow: {
        basis: '외국인 20·60일 누적 순매수/순매도',
        criteria: '순매수 우위면 가점, 연속 순매도면 감점',
        action: '외국인 중기 수급이 약하면 신규 매수 제한'
      },
      inst_flow: {
        basis: '기관 20일 누적 수급 + 외국인/기관 동조',
        criteria: '동시 순매도는 위험 신호',
        action: '동시 대규모 순매도 시 방어 단계 강화'
      },
      vix_risk: {
        basis: 'VIX 중심 변동성 위험 점수',
        criteria: 'VIX 상승/고점 구간일수록 감점',
        action: 'VIX 급등 구간은 포지션 축소·현금 비중 확대'
      },
      fx_trend: {
        basis: '원/달러 MA200 위치 + 상승 추세',
        criteria: 'MA200 상단 상승 추세면 위험',
        action: '환율 리스크가 커지면 공격적 매수 차단'
      },
      market_breadth: {
        basis: '상승종목 비율 + 신고가/신저가 + 확산도',
        criteria: '지수 상승 대비 시장 폭 약화면 감점',
        action: '지수만 오르고 폭이 약하면 추격매수 지양'
      },
      // 종목 시그널
      frn_supply:     { basis:'외국인 5일 순매수 누적금액', criteria:'양수=🟢 매수 / 음수=🔴 매도', action:'Step3 진입 트리거 — 외국인 유입 시 매수 고려' },
      inst_supply:    { basis:'기관 5일 순매수 누적금액', criteria:'양수=🟢 매수 / 음수=🔴 매도', action:'Step3 진입 트리거 — 기관+외국인 동반 유입이 가장 강한 신호' },
      financials:     { basis:'분기 영업이익 흑자 + YoY 매출 성장률', criteria:'흑자+성장 5%+=🟢 / 적자=🔴', action:'Step2 종목 필터 — 재무 미통과 시 매수 보류' },
      value:          { basis:'MA60 대비 현재가 + 52주 모멘텀 AND 조건', criteria:'MA60 위+고점-20%이내=🟢 / MA60 아래=🔴', action:'가치함정 방지 — 가치만 좋고 추세 없으면 Value Trap', note:'★ 핵심: 가치지표 단독 사용 금지 — 반드시 추세와 AND 조건' },
      rs_score:       { basis:'3개월 주가상승률 - KOSPI 상승률 = RS 초과수익률', criteria:'>+5%=🟢 주도주 / <-5%=🔴 약세주', action:'Step2 종목 필터 — 시장보다 강한 주도주만 매수 대상', note:'★ 추세추종 핵심 — 하락장에서 덜 빠지거나 오르는 종목' },
      ma_align:       { basis:'현재가 > MA5 > MA20 > MA60 정배열', criteria:'완전 정배열=🟢 / 역배열=🔴', action:'Step2 종목 필터 + MACD/RSI 필터의 기준선', note:'역배열에서의 매수 신호는 신뢰도 낮음 — 무시' },
      macd_signal:    { basis:'MACD 골든크로스 + 이평선 정배열 필터', criteria:'정배열+골든크로스+0선위=🟢 강함 / 역배열+골든크로스=🟡 노이즈', action:'Step3 진입 트리거 — 정배열 상태에서만 유효 매수신호', note:'★ 박스권/역배열에서의 MACD 골든크로스는 무시' },
      rsi_signal:     { basis:'RSI + 이평선 정배열 필터', criteria:'정배열+RSI 50 돌파=🟢 / 역배열+RSI 30=🟡 추가하락 위험', action:'Step3 진입 트리거 — 정배열 상태 RSI 50 돌파만 유효', note:'강세장에서 RSI 70+ 과매수도 계속 오름 — 추세 먼저 확인' },
      trend52w:       { basis:'52주 고점 -15% 이내 + 최근 1개월 거래량 증가율', criteria:'고점근접+거래량급증=🟢 / 고점-30%+=🔴', action:'Step3 진입 트리거 — 신고가 돌파 직전 거래량 급증이 핵심', note:'윌리엄 오닐 CAN SLIM — 신고가에 가까울수록 강한 종목' },
      atr_stop:       { basis:'ATR(14일) × 2 = 기계적 손절 범위', criteria:'ATR 낮음=🟢 리스크 작음 / ATR 높음=🔴 변동성 큼', action:'Step4 청산 트리거 — 매수가 - 2×ATR 이탈 시 무조건 손절', note:'★ 감정 배제의 핵심 — 손절가 미리 계산해 기계적 실행' },
      vol_price:      { basis:'당일 거래량 vs 20일 평균 거래량 비율', criteria:'상승+2배이상=🟢 / 하락+2배이상=🔴', action:'거래량 없는 상승은 가짜 — 신뢰도 낮음' },
      short_sell:     { basis:'대차잔고비율 + 증가 추세 여부', criteria:'2%이하=🟢 / 5%이상 or 증가추세=🔴', action:'대차 급증 = 공매도 세력 유입 — 주가 하락 압력' },
      system_judgment: { basis:'추세(MA정배열+트리거) + 가치(Graham할인) + 섹터회복 3-트랙 독립 판정', criteria:'추세 OR 가치 통과=🟢 / 트리거 대기=🟡 / 모두 미충족=🔴', action:'시장이 나빠도 Graham 30%+ 할인+흑자 재무면 가치 매수 green 가능', note:'★ 가치 트랙은 시장 하락과 독립 — 시장 위험은 경고 문구만 추가(차단 안 함)' },
      // Graham 가치투자 트랙
      graham_value:    { basis:'Graham 내재가치 = sqrt(22.5 × EPS × BPS)', criteria:'30%+ 할인=🟢 강력저평가 / 15%+ 할인=🟢 저평가 / 고평가=🔴', action:'안전마진 30% 이상일 때 가치 매수 검토', note:'★ 벤저민 그레이엄 공식 — EPS+BPS 모두 양수일 때만 유효' },
      macd_divergence: { basis:'MACD 강세 다이버전스 (0선 아래)', criteria:'가격 신저점 + MACD 저점 상승=🟢 반전신호', action:'바닥 형성 감지 — 가치 종목과 결합 시 강력한 진입 신호', note:'0선 아래에서의 다이버전스가 핵심 — 추세 반전 조짐' },
      smart_money:     { basis:'최근 5거래일 중 기관+외국인 동반 순매수 일수', criteria:'3일 이상=🟢 유입 / 2일=🟡 / 1일 이하=🔴 부재', action:'저평가 구간에서 스마트머니 진입 = 반등 신호', note:'가격이 바닥권일 때만 의미 있음 — 고점에서의 수급은 별도 판단' },
      ma20_slope:      { basis:'MA20 최근 5봉 대비 현재 기울기 (%)', criteria:'기울기 -0.5%~+0.5%=🟢 완만 / 가파른 하락=🔴', action:'MA20 기울기 완만화 = 하락 모멘텀 소진 신호', note:'완전 반전 전에 먼저 기울기가 완만해지는 선행 신호' },
      value_turnaround:{ basis:'Graham + PBR/PER + MACD다이버전스 + MA20기울기 + 스마트머니 종합점수 (9점)', criteria:'6+점=🟢 강력 / 4+점=🟢 / 2+점=🟡 관심 / 미달=🔴', action:'가치 + 반전 조건 종합 점수 — 높을수록 매수 우선순위', note:'★ 가치 트랙 최종 판정 — 이 신호가 🟢(4점+)면 가치 매수 검토' },
    };

    // ── 그룹 구성 (새 시그널 포함) ──────────────────────────────
    const MARKET_GROUPS = [
      { key:'regime',  label:'🏛 Market Regime (시장 환경 필터)', names:['trend_follow','us10y_rate','us30y_rate','valuation_forward'] },
      { key:'risk',    label:'⚠ 위험 지표',                      names:['vix_risk','fx_trend','market_breadth'] },
      { key:'supply',  label:'📊 시장 수급',                     names:['foreign_flow','inst_flow'] },
    ];

    const STOCK_GROUPS = [
      { key:'judgment', label:'🎯 종합 판정 (추세 + 가치 병렬)',                       names:['system_judgment'] },
      { key:'step2',    label:'📈 [추세] Step2 — 종목 필터',                          names:['ma_align','rs_score','financials','value'] },
      { key:'step3',    label:'🚀 [추세] Step3 — 진입 트리거',                         names:['frn_supply','inst_supply','macd_signal','rsi_signal','trend52w'] },
      { key:'step4',    label:'🛡 [추세] Step4 — 리스크 관리',                         names:['atr_stop','vol_price','short_sell'] },
      { key:'value',    label:'💎 [가치] Graham 가치투자 트랙',                         names:['graham_value','macd_divergence','smart_money','ma20_slope','value_turnaround'] },
    ];

    const GROUPS = scope === 'market' ? MARKET_GROUPS : STOCK_GROUPS;
    const stageMeta = (stage) => {
      const n = Number(stage || 3);
      if (n <= 1) return { label: '1단계 (강한 매수 가능)', tone: 'green', score: 1 };
      if (n === 2) return { label: '2단계 (선택적 매수)', tone: 'green', score: 2 };
      if (n === 3) return { label: '3단계 (중립/주의)', tone: 'yellow', score: 3 };
      if (n === 4) return { label: '4단계 (신규매수 금지, 방어 준비)', tone: 'red', score: 4 };
      return { label: '5단계 (시장진입 신중, 방어 필요)', tone: 'red', score: 5 };
    };

    if (loading) return (
      <div style={{padding:'0.6rem 1rem',display:'flex',alignItems:'center',gap:'0.5rem',
        fontSize:'0.78rem',color:'var(--text-secondary)',background:'rgba(255,255,255,0.02)',
        borderRadius:'8px',border:'1px solid var(--glass-border)'}}>
        <div style={{width:'10px',height:'10px',borderRadius:'50%',border:'2px solid var(--accent-mint)',
          borderTopColor:'transparent',animation:'spin 0.8s linear infinite'}}/>
        시그널 계산 중...
      </div>
    );
    if (!signals.length && !(scope === 'market' && regime?.markets?.length)) return null;

    const getGroupByMarket = (marketName) => {
      const target = (regime?.markets || []).find((m) => String(m.market || '').toUpperCase() === marketName);
      return target?.groups || {};
    };

    const marketSignalsFromRegime = scope === 'market' && regime?.markets?.length > 0 ? (() => {
      const kospiGroups = getGroupByMarket('KOSPI');
      const kosdaqGroups = getGroupByMarket('KOSDAQ');
      const trendK = kospiGroups.trend || {};
      const trendQ = kosdaqGroups.trend || {};
      const rates = kospiGroups.rates || kosdaqGroups.rates || {};
      const valuation = kospiGroups.valuation || kosdaqGroups.valuation || {};
      const flowK = kospiGroups.flow || {};
      const flowQ = kosdaqGroups.flow || {};
      const flowKDetail = flowK.detail || {};
      const flowQDetail = flowQ.detail || {};
      const risk = kospiGroups.risk || kosdaqGroups.risk || {};
      const breadthK = kospiGroups.breadth || {};
      const breadthQ = kosdaqGroups.breadth || {};
      const valToSig = (v) => (v >= 0.7 ? 'green' : (v >= 0.4 ? 'yellow' : 'red'));
      const sum = (v) => `${v?.score ?? 0}/${v?.max ?? 0}`;
      const frnWeightedAmt = ((Number(flowKDetail['frn_5d_억'] || 0) + Number(flowQDetail['frn_5d_억'] || 0)) * 0.2)
        + ((Number(flowKDetail['frn_20d_억'] || 0) + Number(flowQDetail['frn_20d_억'] || 0)) * 0.35)
        + ((Number(flowKDetail['frn_60d_억'] || 0) + Number(flowQDetail['frn_60d_억'] || 0)) * 0.45);
      const foreignFlowSignal =
        frnWeightedAmt <= -20000 ? 'red' :
        frnWeightedAmt <= -3000 ? 'yellow' :
        valToSig(((flowK.score || 0) + (flowQ.score || 0)) / ((flowK.max || 1) + (flowQ.max || 1)));
      const foreignFlowDetail = `금액가중(5일20%·20일35%·60일45%): ${frnWeightedAmt.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억`;
      const inst20dAmt = Number(flowKDetail['inst_20d_억'] || 0) + Number(flowQDetail['inst_20d_억'] || 0);
      const instSignal = inst20dAmt <= -30000 ? 'red' : (inst20dAmt < 0 ? 'yellow' : 'green');
      return [
        { name:'trend_follow', label:'추세추종 시그널(지수 MA200 상단)', signal: valToSig(((trendK.score || 0) + (trendQ.score || 0)) / ((trendK.max || 1) + (trendQ.max || 1))), detail:`기준: MA200 상단·기울기·MA50·괴리 | KOSPI ${sum(trendK)} · KOSDAQ ${sum(trendQ)}` },
        { name:'us10y_rate', label:'금리 시그널(미국 10년물)', signal: valToSig((rates.score || 0) / (rates.max || 1)), detail:`기준: 10Y 레벨 + 20일 변화폭 | 점수 ${sum(rates)}` },
        { name:'us30y_rate', label:'금리 시그널(미국 30년물)', signal: valToSig((rates.score || 0) / (rates.max || 1)), detail:`기준: 30Y 레벨(5.0% 상회 위험) | 점수 ${sum(rates)}` },
        { name:'valuation_forward', label:'밸류 시그널(Forward PER/PBR)', signal: valToSig((valuation.score || 0) / (valuation.max || 1)), detail:`기준: 미국·국내 상대 밸류 | 점수 ${sum(valuation)}` },
        { name:'foreign_flow', label:'수급 시그널(외국인 금액가중)', signal: foreignFlowSignal, detail:`기준: 일수보다 금액 우선 | ${foreignFlowDetail}` },
        { name:'inst_flow', label:'수급 시그널(기관 20일)', signal: instSignal, detail:`기준: 기관 20일 누적 순매수/순매도 | 합계 ${inst20dAmt.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억` },
        { name:'vix_risk', label:'리스크 시그널(VIX)', signal: valToSig((risk.score || 0) / (risk.max || 1)), detail:`기준: VIX·공포탐욕·리스크 선호 | 점수 ${sum(risk)}` },
        { name:'fx_trend', label:'환율 시그널(원/달러 추세)', signal: valToSig((risk.score || 0) / (risk.max || 1)), detail:`기준: MA200 위치 + 20일 상승률 | 점수 ${sum(risk)}` },
        { name:'market_breadth', label:'시장폭 시그널(상승비율/신고가·신저가)', signal: valToSig(((breadthK.score || 0) + (breadthQ.score || 0)) / ((breadthK.max || 1) + (breadthQ.max || 1))), detail:`기준: 상승종목비율 + NH/NL + 확산도 | KOSPI ${sum(breadthK)} · KOSDAQ ${sum(breadthQ)}` },
      ];
    })() : null;
    const displaySignals = marketSignalsFromRegime || signals;

    // 4단계 판정 시그널 분리
    const judgmentSig = displaySignals.find(s => s.name === 'system_judgment');
    const normalSigs  = displaySignals.filter(s => s.name !== 'system_judgment');

    const active  = normalSigs.filter(s => s.signal !== 'gray');
    const greens  = active.filter(s => s.signal === 'green').length;
    const reds    = active.filter(s => s.signal === 'red').length;
    const yellows = active.filter(s => s.signal === 'yellow').length;
    const total   = active.length;  // 전체 active(green+red+yellow)

    // 종합 신호: 백엔드 system_judgment 신뢰 (3-트랙 독립 판정)
    // 가치 트랙은 추세 적신호와 무관하게 green 가능 → 프론트 강제 보정 최소화
    const rawJudgment = judgmentSig?.signal;
    let overall;
    if (total === 0) {
      overall = 'gray';
    } else {
      const redRatio   = reds / total;
      const greenRatio = greens / total;
      if (rawJudgment) {
        // 추세 Track: 적신호 70%+ (대부분 추세 시그널이 나쁨) + 가치 판정도 green이면 → yellow 보정
        // → 단, 가치 트랙 green은 추세 적신호와 독립이므로 완화된 기준 적용
        if (rawJudgment === 'green' && redRatio >= 0.6) {
          overall = 'yellow';  // 압도적 적신호 시에만 보정 (35% → 60%로 완화)
        } else {
          overall = rawJudgment;
        }
      } else {
        overall = greenRatio >= 0.6 ? 'green' : redRatio >= 0.6 ? 'red' : 'yellow';
      }
    }
    const oc = C[overall];

    // 레이블: system_judgment detail에서 핵심 문구 추출
    const judgeDetail = judgmentSig?.detail || '';
    const getStockLabel = () => {
      if (overall === 'green') {
        if (judgeDetail.includes('추세+가치')) return '추세+가치 최강 매수';
        if (judgeDetail.includes('[가치]') || judgeDetail.includes('가치]')) return '가치 분할매수 검토';
        return '추세 매수 가능';
      }
      if (overall === 'yellow') {
        if (judgeDetail.includes('시장 약세') || judgeDetail.includes('극위험')) return '가치우수 — 시장위험 소량';
        if (judgeDetail.includes('가치 우수')) return '가치우수 — 시장위험 소량';
        if (judgeDetail.includes('트리거')) return '진입 트리거 대기 중';
        if (judgeDetail.includes('가치 관심')) return '가치 관심 구간';
        return '조건 준비 중';
      }
      return '매수 보류';
    };
    const marketWorstStage = scope === 'market' && regime?.markets?.length ? Math.max(...regime.markets.map((m) => Number(m.stage || 3))) : null;
    const marketStageView = marketWorstStage ? stageMeta(marketWorstStage) : null;
    const OVERALL_LABEL = {
      green:  { emoji:'🟢', label: scope==='stock' ? getStockLabel() : (marketStageView?.label || '시장 우호적') },
      yellow: { emoji:'🟡', label: scope==='stock' ? getStockLabel() : (marketStageView?.label || '시장 혼조') },
      red:    { emoji:'🔴', label: scope==='stock' ? '매수 보류' : (marketStageView?.label || '시장 위험') },
      gray:   { emoji:'⚪', label:'데이터 부족' },
    };
    const ovl = OVERALL_LABEL[overall];
    const stageIcon = (stage) => {
      if (stage <= 1) return '🟢';
      if (stage === 2) return '🟩';
      if (stage === 3) return '🟨';
      if (stage === 4) return '🟧';
      return '🔴';
    };

    const renderSignalCard = (s) => {
      const c    = C[s.signal] || C.gray;
      const EMOJI= { green:'🟢', yellow:'🟡', red:'🔴', gray:'⚪' };
      const guide= SIGNAL_GUIDE[s.name];
      const isJudgment = s.name === 'system_judgment';

      return (
        <div key={s.id || s.name} style={{
          padding: isJudgment ? '0.7rem 1rem' : '0.5rem 0.75rem',
          borderRadius:'8px',
          background: isJudgment ? oc.bg : c.light,
          border:`${isJudgment?'2px':'1px'} solid ${isJudgment?oc.border:c.border}`,
        }}>
          {/* 시그널 헤더 */}
          <div style={{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:'0.25rem'}}>
            <div style={{display:'flex',alignItems:'center',gap:'0.4rem'}}>
              <span style={{fontSize:'0.8rem'}}>{EMOJI[s.signal]}</span>
              <span style={{fontSize: isJudgment?'0.85rem':'0.78rem', fontWeight:700, color:c.text}}>
                {s.label}
              </span>
            </div>
          </div>

          {/* 현재 계산값 + 상세 */}
          <p style={{fontSize:'0.7rem',color:'var(--text-primary)',lineHeight:1.5,marginBottom:'0.2rem',fontWeight:500}}>
            {s.detail || '-'}
          </p>

          {/* 로직 가이드 (showGuide 활성화 시) */}
          {showGuide && guide && (
            <div style={{marginTop:'0.4rem',paddingTop:'0.4rem',
              borderTop:'1px solid rgba(255,255,255,0.07)',
              display:'flex',flexDirection:'column',gap:'0.2rem'}}>
              <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                <span style={{fontSize:'0.62rem',padding:'0.1rem 0.4rem',borderRadius:'4px',
                  background:'rgba(56,189,248,0.12)',color:'#38bdf8',whiteSpace:'nowrap'}}>
                  📐 {guide.basis}
                </span>
              </div>
              <p style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.55)',lineHeight:1.4}}>
                <span style={{color:'rgba(255,255,255,0.3)'}}>기준: </span>{guide.criteria}
              </p>
              <p style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.55)',lineHeight:1.4}}>
                <span style={{color:'rgba(255,255,255,0.3)'}}>활용: </span>{guide.action}
              </p>
              {guide.note && (
                <p style={{fontSize:'0.63rem',color:'#f59e0b',lineHeight:1.4,
                  padding:'0.15rem 0.4rem',background:'rgba(245,158,11,0.08)',borderRadius:'4px'}}>
                  ⚡ {guide.note}
                </p>
              )}
            </div>
          )}
        </div>
      );
    };

    return (
      <div style={{borderRadius:'10px',border:`1px solid ${oc.border}`,
        background:'rgba(255,255,255,0.02)',marginBottom:'0.75rem',overflow:'hidden'}}>

        {scope === 'market' && regime?.markets?.length > 0 && (
          <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',background:'rgba(15,23,42,0.45)'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.45rem',gap:'0.6rem',flexWrap:'wrap'}}>
              <div style={{fontSize:'0.82rem',fontWeight:700,color:'#2dd4bf'}}>🧭 5단계 시장 국면 보드</div>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>기준: {regime.generated_at || '-'}</span>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(260px,1fr))',gap:'0.55rem',marginBottom:'0.55rem'}}>
              {regime.markets.map((m)=>{
                const meta = stageMeta(m.stage);
                const toneBg = meta.tone === 'green' ? 'rgba(34,197,94,0.14)' : (meta.tone === 'yellow' ? 'rgba(251,191,36,0.16)' : 'rgba(239,68,68,0.14)');
                const toneBorder = meta.tone === 'green' ? 'rgba(34,197,94,0.45)' : (meta.tone === 'yellow' ? 'rgba(251,191,36,0.45)' : 'rgba(239,68,68,0.45)');
                const toneText = meta.tone === 'green' ? '#86efac' : (meta.tone === 'yellow' ? '#fde68a' : '#fca5a5');
                return (
                  <div key={`${m.market}-hero`} style={{padding:'0.65rem 0.75rem',borderRadius:'10px',background:toneBg,border:`1px solid ${toneBorder}`}}>
                    <div style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.65)',marginBottom:'0.15rem'}}>{m.market} 현재 국면</div>
                    <div style={{fontSize:'1.02rem',fontWeight:900,color:toneText}}>
                      {stageIcon(m.stage)} {meta.label}
                    </div>
                  </div>
                );
              })}
            </div>
            <div style={{overflowX:'auto'}}>
              <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.74rem'}}>
                <thead>
                  <tr style={{borderBottom:'1px solid rgba(255,255,255,0.12)'}}>
                    {['시장','최종단계','총점','신규매수','방어필요','긍정요인','부정요인','강제하향 사유'].map(h=>(
                      <th key={h} style={{textAlign:h==='시장'?'left':'center',padding:'0.35rem 0.4rem',color:'var(--text-secondary)',fontWeight:600,whiteSpace:'nowrap'}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {regime.markets.map((m)=>(
                    <tr key={m.market} style={{borderBottom:'1px solid rgba(255,255,255,0.05)'}}>
                      <td style={{padding:'0.35rem 0.4rem',fontWeight:700}}>{m.market}</td>
                      <td style={{padding:'0.35rem 0.4rem',textAlign:'center',fontWeight:800,color:m.stage<=2?'#22c55e':m.stage===3?'#fbbf24':'#ef4444',
                        background:m.stage>=4?'rgba(239,68,68,0.14)':m.stage===3?'rgba(251,191,36,0.14)':'rgba(34,197,94,0.12)',
                        borderRadius:'6px'}}>
                        {stageIcon(m.stage)} {m.stage}단계
                      </td>
                      <td style={{padding:'0.35rem 0.4rem',textAlign:'center'}}>{m.score}</td>
                      <td style={{padding:'0.35rem 0.4rem',textAlign:'center',color:m.buy_allowed?'#22c55e':'#ef4444',fontWeight:800,
                        background:m.buy_allowed?'rgba(34,197,94,0.12)':'rgba(239,68,68,0.16)',borderRadius:'6px'}}>
                        {m.buy_allowed ? '가능' : '제한'}
                      </td>
                      <td style={{padding:'0.35rem 0.4rem',textAlign:'center',color:m.sell_defense?'#ef4444':'#94a3b8',fontWeight:m.sell_defense?800:500,
                        background:m.sell_defense?'rgba(239,68,68,0.16)':'transparent',borderRadius:'6px'}}>
                        {m.sell_defense ? '필요' : '보통'}
                      </td>
                      <td style={{padding:'0.35rem 0.4rem',textAlign:'center',maxWidth:'220px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',color:'#86efac',
                        borderLeft:'1px solid rgba(34,197,94,0.35)',borderRight:'1px solid rgba(34,197,94,0.35)'}}>
                        {(m.positive_factors||[]).slice(0,2).join(', ') || '-'}
                      </td>
                      <td style={{padding:'0.35rem 0.4rem',textAlign:'center',maxWidth:'220px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',color:'#fca5a5',
                        borderLeft:'1px solid rgba(239,68,68,0.35)',borderRight:'1px solid rgba(239,68,68,0.35)'}}>
                        {(m.negative_factors||[]).slice(0,2).join(', ') || '-'}
                      </td>
                      <td style={{padding:'0.35rem 0.4rem',textAlign:'left',color:m.forced_level==='none'?'#94a3b8':'#f59e0b',maxWidth:'260px'}}>
                        {m.forced_level === 'none' ? '-' : (
                          <div style={{display:'flex',flexDirection:'column',gap:'0.2rem'}}>
                            <span style={{fontSize:'0.66rem',fontWeight:800,color:'#f59e0b'}}>{m.forced_level.toUpperCase()}</span>
                            <span style={{fontSize:'0.66rem',lineHeight:1.35,color:'#fdba74'}}>
                              {(m.forced_reasons || []).slice(0,2).join(', ') || '-'}
                            </span>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{marginTop:'0.45rem',fontSize:'0.68rem',color:'rgba(255,255,255,0.52)'}}>
              {regime.principle}
            </div>
            <div style={{marginTop:'0.5rem',padding:'0.45rem 0.55rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.08)'}}>
              <div style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.72)',marginBottom:'0.3rem'}}>단계 스케일 (긍정 → 부정)</div>
              <div style={{display:'grid',gridTemplateColumns:'repeat(5,minmax(56px,1fr))',gap:'0.3rem'}}>
                {[1,2,3,4,5].map((s)=>{
                  const meta = stageMeta(s);
                  const active = marketWorstStage === s;
                  const color = meta.tone === 'green' ? '#22c55e' : (meta.tone === 'yellow' ? '#fbbf24' : '#ef4444');
                  return (
                    <div key={`stage-${s}`} style={{padding:'0.28rem 0.25rem',borderRadius:'6px',textAlign:'center',
                      border:`1px solid ${active ? color : 'rgba(255,255,255,0.15)'}`,
                      background:active ? (meta.tone === 'green' ? 'rgba(34,197,94,0.15)' : meta.tone === 'yellow' ? 'rgba(251,191,36,0.15)' : 'rgba(239,68,68,0.14)') : 'rgba(255,255,255,0.02)'}}>
                      <div style={{fontSize:'0.66rem',fontWeight:700,color:active ? color : 'rgba(255,255,255,0.65)'}}>{stageIcon(s)} {s}단계</div>
                      <div style={{fontSize:'0.6rem',marginTop:'0.08rem',color:active ? color : 'rgba(255,255,255,0.5)'}}>{meta.label.replace(`${s}단계 `,'').replace(`${s}단계`,'').trim()}</div>
                    </div>
                  );
                })}
              </div>
            </div>
            {(regime.briefings || []).length > 0 && (
              <div style={{marginTop:'0.55rem',display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:'0.5rem'}}>
                {regime.briefings.map((b, i)=>(
                  <div key={`${b.market}-${i}`} style={{padding:'0.55rem 0.65rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.08)'}}>
                    <div style={{fontSize:'0.7rem',fontWeight:700,color:'#93c5fd',marginBottom:'0.25rem'}}>🗞 {b.market} 아침 브리핑 ({b.briefing_date})</div>
                    <pre style={{margin:0,whiteSpace:'pre-wrap',fontFamily:'inherit',fontSize:'0.69rem',lineHeight:1.45,color:'rgba(255,255,255,0.8)'}}>
                      {String(b.summary || '').split('\n').slice(0,5).join('\n')}
                    </pre>
                  </div>
                ))}
              </div>
            )}
            <div style={{marginTop:'0.55rem',padding:'0.55rem 0.65rem',borderRadius:'8px',background:'rgba(56,189,248,0.07)',border:'1px solid rgba(56,189,248,0.2)'}}>
              <div style={{fontSize:'0.72rem',fontWeight:700,color:'#7dd3fc',marginBottom:'0.22rem'}}>📘 5단계 로직 설명</div>
              <div style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.75)',lineHeight:1.45}}>
                • 점수 구간: 1단계(80~100) / 2단계(65~79) / 3단계(45~64) / 4단계(25~44) / 5단계(0~24)<br/>
                • 신규 매수 허용: 단계 1~2 + 추세추종(지수 MA200 상단) + 외국인 20일 비순매도 + 미국10년물 4.7% 이하 또는 하락추세<br/>
                • 강제 하향: 위험 조건 다중 충족 시 점수와 무관하게 4~5단계로 하향
              </div>
            </div>
            <div style={{marginTop:'0.55rem',display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:'0.45rem'}}>
              {(regime.markets || []).map((m)=>(
                <div key={`${m.market}-groups`} style={{padding:'0.5rem 0.6rem',borderRadius:'8px',background:'rgba(255,255,255,0.02)',border:'1px solid rgba(255,255,255,0.07)'}}>
                  <div style={{fontSize:'0.7rem',fontWeight:700,color:'#c4b5fd',marginBottom:'0.25rem'}}>{m.market} 세부 점수</div>
                  <div style={{display:'flex',gap:'0.25rem',flexWrap:'wrap'}}>
                    {Object.entries(m.groups || {}).map(([k,v])=>(
                      <span key={k} style={{fontSize:'0.66rem',padding:'0.1rem 0.35rem',borderRadius:'999px',background:'rgba(99,102,241,0.18)',color:'#a5b4fc'}}>
                        {k}:{v?.score ?? 0}/{v?.max ?? '-'}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 헤더 */}
        <div style={{padding:'0.6rem 1.1rem',background:oc.bg,
          display:'flex',alignItems:'center',justifyContent:'space-between',
          cursor:'pointer'}} onClick={()=>setExpanded(v=>!v)}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
            {/* 신호등 1개 */}
            <span style={{fontSize:'1.3rem'}}>{ovl.emoji}</span>
            {/* 1줄 요약 */}
            <div style={{display:'flex',alignItems:'center',gap:'0.5rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.88rem',fontWeight:800,color:oc.text}}>
                {ovl.label}
              </span>
              {judgmentSig && (
                <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)',
                  borderLeft:'1px solid rgba(255,255,255,0.2)',paddingLeft:'0.5rem'}}>
                  {judgmentSig.detail.replace(/[✅🟡🔴]\s*/g,'').split('—')[1]?.trim() || judgmentSig.detail.split('—')[0]?.trim()}
                </span>
              )}
            </div>
            {/* 시그널 카운트: 🟢green / 🔴red / 🟡yellow / 전체 */}
            <div style={{display:'flex',gap:'0.25rem',marginLeft:'0.3rem',alignItems:'center'}}>
              <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.7rem',fontWeight:700,
                background:'rgba(34,197,94,0.2)',color:'#22c55e',border:'1px solid rgba(34,197,94,0.4)',
                display:'flex',alignItems:'center',gap:'0.2rem'}}>
                <span style={{width:'7px',height:'7px',borderRadius:'50%',background:'#22c55e',display:'inline-block'}}/>
                {greens}
              </span>
              <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.7rem',fontWeight:700,
                background:'rgba(239,68,68,0.2)',color:'#ef4444',border:'1px solid rgba(239,68,68,0.4)',
                display:'flex',alignItems:'center',gap:'0.2rem'}}>
                <span style={{width:'7px',height:'7px',borderRadius:'50%',background:'#ef4444',display:'inline-block'}}/>
                {reds}
              </span>
              {yellows > 0 && (
                <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.7rem',fontWeight:700,
                  background:'rgba(251,191,36,0.15)',color:'#fbbf24',border:'1px solid rgba(251,191,36,0.35)',
                  display:'flex',alignItems:'center',gap:'0.2rem'}}>
                  <span style={{width:'7px',height:'7px',borderRadius:'50%',background:'#fbbf24',display:'inline-block'}}/>
                  {yellows}
                </span>
              )}
              <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.68rem',
                background:'rgba(255,255,255,0.06)',color:'#94a3b8',border:'1px solid rgba(255,255,255,0.1)'}}>
                /{total}
              </span>
            </div>
          </div>
          <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
            {/* 로직 설명 토글 버튼 */}
            <button onClick={(e)=>{e.stopPropagation();setShowGuide(v=>!v);setExpanded(true);}}
              style={{padding:'0.2rem 0.55rem',borderRadius:'6px',fontSize:'0.68rem',fontWeight:600,
                border:`1px solid ${showGuide?'#38bdf8':'rgba(255,255,255,0.2)'}`,
                background: showGuide?'rgba(56,189,248,0.15)':'transparent',
                color: showGuide?'#38bdf8':'rgba(255,255,255,0.4)',cursor:'pointer',
                whiteSpace:'nowrap'}}>
              {showGuide ? '📖 설명 ON' : '📖 설명'}
            </button>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
              {scope==='market'?'📊 시장':'🔍 종목'} {expanded?'▲':'▼'}
            </span>
          </div>
        </div>

        {/* 4단계 시스템 흐름도 (시장 시그널일 때는 숨김) */}
        {expanded && scope === 'stock' && (
          <div style={{padding:'0.6rem 1rem',background:'rgba(0,0,0,0.2)',
            borderBottom:'1px solid var(--glass-border)',
            display:'flex',flexDirection:'column',gap:'0.5rem'}}>
            {/* Track A: 추세 */}
            <div style={{display:'flex',alignItems:'center',gap:'0.3rem',overflowX:'auto',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.62rem',color:'#38bdf8',fontWeight:700,minWidth:'52px',flexShrink:0}}>
                📈 추세
              </span>
              {[
                {step:'Step1',label:'마켓 필터',desc:'200일선+VIX',color:'#38bdf8'},
                {step:'→'},
                {step:'Step2',label:'MA 정배열',desc:'5>20>60 정배열',color:'#a78bfa'},
                {step:'→'},
                {step:'Step3',label:'진입 트리거',desc:'수급+MACD/RSI',color:'#22c55e'},
                {step:'→'},
                {step:'Step4',label:'ATR 리스크',desc:'2×ATR 손절선',color:'#f59e0b'},
              ].map((item, i) => item.step === '→' ? (
                <span key={i} style={{color:'rgba(255,255,255,0.3)',fontSize:'0.9rem'}}>→</span>
              ) : (
                <div key={i} style={{padding:'0.25rem 0.6rem',borderRadius:'6px',
                  background:'rgba(255,255,255,0.04)',border:`1px solid ${item.color}33`,
                  textAlign:'center',minWidth:'75px'}}>
                  <div style={{fontSize:'0.62rem',color:item.color,fontWeight:700}}>{item.step}</div>
                  <div style={{fontSize:'0.72rem',color:'var(--text-primary)',fontWeight:600}}>{item.label}</div>
                  <div style={{fontSize:'0.6rem',color:'var(--text-secondary)'}}>{item.desc}</div>
                </div>
              ))}
            </div>
            {/* Track B: 가치 (독립) */}
            <div style={{display:'flex',alignItems:'center',gap:'0.3rem',overflowX:'auto',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.62rem',color:'#f59e0b',fontWeight:700,minWidth:'52px',flexShrink:0}}>
                💎 가치
              </span>
              {[
                {step:'Graham',label:'내재가치 할인',desc:'≥30% 할인 필수',color:'#f59e0b'},
                {step:'+'},
                {step:'재무',label:'영업흑자',desc:'적자 시 제외',color:'#f59e0b'},
                {step:'+'},
                {step:'반전신호',label:'MACD 다이버전스',desc:'바닥 반전 감지',color:'#f59e0b'},
                {step:'→'},
                {step:'시장위험',label:'시장 위험도',desc:'경고 추가(차단 안 함)',color:'#94a3b8'},
              ].map((item, i) => item.step === '+' || item.step === '→' ? (
                <span key={i} style={{color:'rgba(255,255,255,0.3)',fontSize:'0.9rem'}}>{item.step}</span>
              ) : (
                <div key={i} style={{padding:'0.25rem 0.6rem',borderRadius:'6px',
                  background: item.color==='#94a3b8' ? 'rgba(148,163,184,0.06)' : 'rgba(245,158,11,0.06)',
                  border:`1px solid ${item.color}33`,
                  textAlign:'center',minWidth:'75px'}}>
                  <div style={{fontSize:'0.62rem',color:item.color,fontWeight:700}}>{item.step}</div>
                  <div style={{fontSize:'0.72rem',color:'var(--text-primary)',fontWeight:600}}>{item.label}</div>
                  <div style={{fontSize:'0.6rem',color:'var(--text-secondary)'}}>{item.desc}</div>
                </div>
              ))}
            </div>
            {/* Track C: 섹터 회복 */}
            <div style={{display:'flex',alignItems:'center',gap:'0.3rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.62rem',color:'#34d399',fontWeight:700,minWidth:'52px',flexShrink:0}}>
                🏭 섹터
              </span>
              <div style={{padding:'0.2rem 0.6rem',borderRadius:'6px',
                background:'rgba(52,211,153,0.06)',border:'1px solid rgba(52,211,153,0.2)'}}>
                <span style={{fontSize:'0.68rem',color:'#34d399'}}>
                  섹터 주도주 50%+ 52주선 상회 시 가치 신호 강화 (탑다운 보너스)
                </span>
              </div>
            </div>
            <div style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.35)',paddingTop:'0.2rem',borderTop:'1px solid rgba(255,255,255,0.06)'}}>
              💡 추세·가치 트랙은 독립 판정 — 가치 매수는 MA 역배열·시장 하락과 무관하게 green 가능
            </div>
          </div>
        )}

        {/* 시그널 상세 */}
        {expanded && (
          <div style={{padding:'0.8rem 1rem'}}>
            {scope === 'market' && regime?.markets?.length > 0 && (
              <div style={{marginBottom:'0.9rem',padding:'0.65rem 0.75rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.08)'}}>
                <div style={{fontSize:'0.73rem',fontWeight:700,color:'#c4b5fd',marginBottom:'0.3rem'}}>시장 단계별 상세 근거</div>
                {regime.markets.map((m)=>(
                  <div key={`${m.market}-detail`} style={{fontSize:'0.68rem',lineHeight:1.45,color:'rgba(255,255,255,0.78)',marginBottom:'0.35rem'}}>
                    • <strong>{m.market}</strong>: {stageMeta(m.stage).label} / 총점 {m.score} /
                    매수 {m.buy_allowed ? '가능' : '제한'} / 방어 {m.sell_defense ? '필요' : '보통'} /
                    강제하향 {m.forced_level === 'none' ? '-' : `${m.forced_level} (${(m.forced_reasons || []).join(', ') || '-'})`}
                  </div>
                ))}
              </div>
            )}
            {GROUPS.map(g => {
              const grpSigs = displaySignals.filter(s => g.names.includes(s.name));
              if (!grpSigs.length) return null;
              return (
                <div key={g.key} style={{marginBottom:'1rem'}}>
                  <p style={{fontSize:'0.72rem',fontWeight:700,color:'var(--text-secondary)',
                    marginBottom:'0.4rem',letterSpacing:'0.04em',
                    borderLeft:'3px solid rgba(255,255,255,0.2)',paddingLeft:'0.5rem'}}>
                    {g.label}
                  </p>
                  <div style={{display:'grid',
                    gridTemplateColumns: g.key==='judgment' ? '1fr' : 'repeat(auto-fill,minmax(220px,1fr))',
                    gap:'0.4rem'}}>
                    {grpSigs.map(renderSignalCard)}
                  </div>
                </div>
              );
            })}

            {/* 설명 꺼져있을 때 안내 */}
            {!showGuide && (
              <div style={{marginTop:'0.5rem',padding:'0.4rem 0.7rem',borderRadius:'6px',
                background:'rgba(56,189,248,0.06)',border:'1px solid rgba(56,189,248,0.15)',
                display:'flex',alignItems:'center',gap:'0.4rem'}}>
                <span style={{fontSize:'0.68rem',color:'rgba(56,189,248,0.7)'}}>
                  📖 각 시그널의 계산 기준과 활용법을 보려면 우측 상단 <b>설명</b> 버튼을 클릭하세요
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

    // ── 매크로 대시보드 ──────────────────────────────────────────
  const MacroDashboard = React.memo(() => {
    const [lastUpdated, setLastUpdated] = React.useState(() => new Date().toLocaleTimeString('ko-KR'));
    const [marketCash, setMarketCash] = React.useState(null);
    const [cashRange, setCashRange] = React.useState(1095); // 30/183/365/1095
    const [kospiTab,   setKospiTab]   = React.useState('90');
    const [kosdaqTab,  setKosdaqTab]  = React.useState('90');
    const [nasdaqTab,  setNasdaqTab]  = React.useState('90');
    const [sp500Tab,   setSp500Tab]   = React.useState('90');
    const [treasuryTabs, setTreasuryTabs] = React.useState({ US2Y:'90', US10Y:'90', US30Y:'90' }); // 90/180/365
    // lastUpdated는 fetchMacro 완료 시 업데이트 (불필요한 5분 타이머 제거)

    // 구버전({KOSPI:...}) / 신버전({index:{KOSPI:...}}) 정규화
    const norm = (data) => {
      if (!data) return { idx:{}, vix:{}, comm:{}, tsy:{} };
      if (data.index && typeof data.index === 'object')
        return { idx: data.index||{}, vix: data.vix||{}, comm: data.commodities||{}, tsy: data.us_treasury||{} };
      return {
        idx:  { KOSPI: data.KOSPI||{}, KOSDAQ: data.KOSDAQ||{} },
        vix:  data.VIX || {},
        comm: { 'USD/KRW': data['USD/KRW']||{}, GOLD: data.GOLD||{}, OIL: data.OIL||{} },
        tsy: {},
      };
    };
    const { idx, vix, comm, tsy } = norm(macroData);
    const hasData = !!(idx.KOSPI?.value || idx.KOSDAQ?.value || comm['USD/KRW']?.value);

    React.useEffect(() => {
      let alive = true;
      fetch(API(`/api/market-indicators/market-cash?days=${cashRange}`))
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (alive) setMarketCash(d); })
        .catch(() => { if (alive) setMarketCash(null); });
      return () => { alive = false; };
    }, [cashRange]);

    // ── 포맷 헬퍼 ──
    const fv = (v, dec=0) => (v == null ? '-' : Number(v).toLocaleString('ko-KR', {maximumFractionDigits: dec}));
    // fq: 지수 수급 — 항상 억원 단위 고정, 천단위 콤마, 소수점 없음
    const fq = (v) => {
      if (v == null) return '-';
      const n = Number(v);
      if (n === 0) return '-';
      const sign = n > 0 ? '+' : '-';
      return sign + Math.round(Math.abs(n)).toLocaleString('ko-KR') + '억';
    };
    const pc  = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.35)';
    const arr = (v) => v >= 0 ? '▲' : '▼';

    // VIX 단계 색상
    const vixColor = (v) =>
      !v     ? '#34d399' :
      v >= 40 ? '#ef4444' :
      v >= 20 ? '#fb923c' : '#34d399';
    const vixLabel = (v) =>
      !v     ? '-' :
      v >= 40 ? '극심한 공포' :
      v >= 20 ? '주의단계' : '안정적';
    const riskColor = (level) => level === '위험' ? '#ef4444' : level === '주의' ? '#fb923c' : '#34d399';

    // VIX 전용 차트 컴포넌트 (IIFE 대신 컴포넌트로 분리)
    const VixChart = ({ data, color }) => {
      if (!data || data.length < 2) return (
        <div style={{ height:'100%', display:'flex', alignItems:'center', justifyContent:'center', color:'var(--text-secondary)', fontSize:'0.75rem' }}>
          VIX 히스토리 수집 중...
        </div>
      );
      const vvals  = data.map(d => d.close).filter(v => v != null);
      const vMin   = Math.min(...vvals);
      const vMax   = Math.max(...vvals);
      const vPad   = (vMax - vMin) * 0.08 || 1;
      const vTicks = [0, Math.floor(data.length / 2), data.length - 1]
                     .map(i => data[i]?.date).filter(Boolean);
      return (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top:4, right:24, left:2, bottom:0 }}>
            <defs>
              <linearGradient id="vixG" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={color} stopOpacity={0}    />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="date" ticks={vTicks}
              tick={{ fontSize:9, fill:'rgba(255,255,255,0.3)' }}
              tickFormatter={v => v?.slice(5)} axisLine={false} tickLine={false} />
            <YAxis domain={[vMin - vPad, vMax + vPad]}
              tick={{ fontSize:9, fill:'rgba(255,255,255,0.3)' }}
              tickFormatter={v => v.toFixed(1)} axisLine={false} tickLine={false}
              width={28} tickCount={4} />
            <Tooltip
              contentStyle={{ background:'rgba(15,15,25,0.95)', border:'1px solid rgba(255,255,255,0.1)', borderRadius:'6px', fontSize:'0.72rem' }}
              formatter={v => [Number(v).toFixed(1), 'VIX']}
              labelFormatter={v => v?.slice(5)}
            />
            <ReferenceLine y={20} stroke="rgba(251,146,60,0.5)" strokeDasharray="4 2" />
            <ReferenceLine y={40} stroke="rgba(239,68,68,0.5)"  strokeDasharray="4 2" />
            <Area type="monotone" dataKey="close" stroke={color} strokeWidth={1.5} fill="url(#vixG)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      );
    };

    // 미니 스파크라인 (X/Y축 포함, 최솟값-최댓값 기반 도메인)
    const MiniChart = ({ data, color, height = 70, dec = 2 }) => {
      if (!data || data.length < 2) return (
        <div style={{ height, display:'flex', alignItems:'center', justifyContent:'center', color:'rgba(255,255,255,0.2)', fontSize:'0.7rem' }}>
          히스토리 수집 중
        </div>
      );
      const vals = data.map(d => d.close).filter(v => v != null && !isNaN(v));
      const minV = Math.min(...vals);
      const maxV = Math.max(...vals);
      const pad  = (maxV - minV) * 0.08 || maxV * 0.01;
      const domMin = minV - pad;
      const domMax = maxV + pad;

      // X축: 처음·중간·마지막 날짜만 표시
      const tickIdxs = [0, Math.floor(data.length / 2), data.length - 1];
      const xTicks   = tickIdxs.map(i => data[i]?.date).filter(Boolean);

      const fmtDate = (d) => d ? d.slice(5) : ''; // "MM-DD"

      return (
        <ResponsiveContainer width="100%" height={height}>
          <AreaChart data={data} margin={{ top: 4, right: 8, left: 2, bottom: 0 }}>
            <defs>
              <linearGradient id={`grad_${color.replace('#','')}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={color} stopOpacity={0.3} />
                <stop offset="95%" stopColor={color} stopOpacity={0}   />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="date"
              ticks={xTicks}
              tickFormatter={fmtDate}
              tick={{ fontSize: 9, fill: 'rgba(255,255,255,0.35)' }}
              axisLine={false}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={[domMin, domMax]}
              tick={{ fontSize: 9, fill: 'rgba(255,255,255,0.35)' }}
              tickFormatter={v => Number(v).toLocaleString('ko-KR', { maximumFractionDigits: dec })}
              axisLine={false}
              tickLine={false}
              width={40}
              tickCount={3}
            />
            <Tooltip
              contentStyle={{ background:'rgba(15,15,25,0.95)', border:'1px solid rgba(255,255,255,0.1)', borderRadius:'6px', fontSize:'0.72rem' }}
              formatter={(v) => [Number(v).toLocaleString('ko-KR', { maximumFractionDigits: dec }), '']}
              labelFormatter={fmtDate}
            />
            <Area type="monotone" dataKey="close" stroke={color} strokeWidth={1.5}
              fill={`url(#grad_${color.replace('#','')})`} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      );
    };

    if (!hasData) return (
      <div className="fade-in glass-panel" style={{ padding:'3rem', textAlign:'center', color:'var(--text-secondary)' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'center', gap:'0.6rem', marginBottom:'0.8rem' }}>
          <div style={{ width:'14px', height:'14px', borderRadius:'50%', border:'2px solid var(--accent-mint)', borderTopColor:'transparent', animation:'spin 0.8s linear infinite' }} />
          <p style={{ fontWeight:600, color:'var(--accent-mint)' }}>매크로 데이터 조회 중...</p>
        </div>
        <p style={{ fontSize:'0.78rem' }}>
          {macroData ? '데이터를 파싱하는 중입니다.' : 'data_collector.py 실행 후 300초 이내에 자동으로 채워집니다.'}
        </p>
        <button onClick={fetchMacro} style={{ marginTop:'1rem', padding:'0.4rem 1rem', borderRadius:'6px', border:'1px solid var(--accent-mint)', background:'transparent', color:'var(--accent-mint)', cursor:'pointer', fontSize:'0.8rem' }}>
          새로고침
        </button>
      </div>
    );

    const card  = { padding:'1.2rem 1.4rem' };
    const lbl   = { fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600, letterSpacing:'0.05em', marginBottom:'0.4rem' };
    const big   = { fontSize:'1.5rem', fontWeight:700, lineHeight:1.2 };

    return (
    <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1.1rem' }}>

      {/* 시장 시그널 보드 */}
      <SignalBoard scope="market" />

      {/* 예탁금 추이 (한국은행 ECOS/네이버 폴백) */}
      <div className="glass-panel" style={{ padding:'1rem' }}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:'0.6rem',flexWrap:'wrap',marginBottom:'0.7rem'}}>
          <div style={{fontSize:'0.82rem',fontWeight:700,color:'#2dd4bf'}}>💰 예탁금/신용잔고</div>
          <div style={{display:'flex',gap:'0.35rem',alignItems:'center'}}>
            {[[30,'30일'],[183,'6개월'],[365,'1년'],[1095,'3년']].map(([d,l])=>(
              <button key={d} onClick={()=>setCashRange(d)} style={{
                padding:'0.14rem 0.5rem', borderRadius:'4px', fontSize:'0.68rem', cursor:'pointer',
                border: cashRange===d ? '1px solid #2dd4bf' : '1px solid rgba(255,255,255,0.15)',
                background: cashRange===d ? 'rgba(45,212,191,0.15)' : 'transparent',
                color: cashRange===d ? '#2dd4bf' : 'rgba(255,255,255,0.55)', fontWeight: cashRange===d ? 700 : 500,
              }}>{l}</button>
            ))}
          </div>
        </div>
        <div style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginBottom:'0.6rem'}}>
          출처: {(marketCash?.source || '').startsWith('ecos') ? '한국은행 ECOS' : '네이버 증시자금동향'} · 최신기준일: {marketCash?.latest_date || '-'} · 업데이트: {marketCash?.updated_at || '-'}
        </div>
        {marketCash?.rows?.length > 0 ? (
          <ResponsiveContainer width="100%" height={210}>
            <ComposedChart data={marketCash.rows} margin={{top:4,right:8,left:2,bottom:0}}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="date" tick={{fontSize:9,fill:'#94a3b8'}} tickFormatter={d=>d?.slice(5)} interval="preserveStartEnd" />
              <YAxis tick={{fontSize:9,fill:'#94a3b8'}} tickFormatter={v=>`${Math.round(v).toLocaleString()}`} />
              <Tooltip
                contentStyle={{background:'rgba(15,15,25,0.95)',border:'1px solid rgba(255,255,255,0.1)',borderRadius:'6px',fontSize:'0.72rem'}}
                formatter={(v,n)=>[
                  `${Number(v||0).toLocaleString()}억`,
                  n==='customer_deposit_100m' ? '고객예탁금' : n==='credit_balance_100m' ? '신용융자잔고' : n==='kospi_trade_value_100m' ? '코스피 거래대금' : '코스닥 거래대금'
                ]}
                labelFormatter={l=>`날짜: ${l}`}
              />
              <Legend formatter={(v)=> v==='customer_deposit_100m' ? '고객예탁금' : v==='credit_balance_100m' ? '신용융자잔고' : v==='kospi_trade_value_100m' ? '코스피 거래대금' : '코스닥 거래대금'} />
              <Area type="monotone" dataKey="customer_deposit_100m" stroke="#2dd4bf" fill="rgba(45,212,191,0.15)" strokeWidth={2} />
              <Line type="monotone" dataKey="credit_balance_100m" stroke="#f59e0b" dot={false} strokeWidth={2} />
            </ComposedChart>
          </ResponsiveContainer>
        ) : (
          <div style={{fontSize:'0.78rem',color:'var(--text-secondary)',padding:'0.7rem 0.2rem'}}>예탁금 데이터를 불러오는 중입니다.</div>
        )}
      </div>

      {/* 갱신 상태 */}
      <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', fontSize:'0.72rem', color:'var(--text-secondary)', flexWrap:'wrap' }}>
        <span style={{ width:'6px', height:'6px', borderRadius:'50%', background:'var(--accent-mint)', flexShrink:0 }} />
        <span>300초 자동 갱신</span>
        {idx.KOSPI?.date  && <span style={{color:'rgba(52,211,153,0.8)'}}>KOSPI ({idx.KOSPI.date})</span>}
        {idx.KOSDAQ?.date && <span style={{color:'rgba(96,165,250,0.8)'}}>KOSDAQ ({idx.KOSDAQ.date})</span>}
        {idx.NASDAQ?.date && <span style={{color:'rgba(167,139,250,0.8)'}}>NASDAQ ({idx.NASDAQ.date})</span>}
        {idx['S&P500']?.date && <span style={{color:'rgba(251,146,60,0.8)'}}>S&P500 ({idx['S&P500'].date})</span>}
        <button onClick={fetchMacro} style={{ marginLeft:'auto', padding:'0.15rem 0.6rem', borderRadius:'4px', border:'1px solid rgba(255,255,255,0.15)', background:'transparent', color:'rgba(255,255,255,0.4)', cursor:'pointer', fontSize:'0.7rem' }}>새로고침</button>
      </div>

      {/* ══ PARA 1: KOSPI / KOSDAQ ══ */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem' }}>
        {[['KOSPI','#34d399',kospiTab,setKospiTab],['KOSDAQ','#60a5fa',kosdaqTab,setKosdaqTab]].map(([name,idxColor,idxTab,setIdxTab]) => {
          const d = idx[name] || {};
          const noSupply = !d.frn_net_buy && !d.inst_net_buy;
          const histData = idxTab==='90'?(d.history_90||[]):idxTab==='365'?(d.history_365||[]):(d.history_1095||[]);
          // 탭별 기간 수익률 계산
          const calcPeriodReturn = (hist) => {
            if (!hist || hist.length < 2) return null;
            const first = hist[0]?.close; const last = hist[hist.length-1]?.close;
            if (!first || !last || first === 0) return null;
            return ((last - first) / first * 100);
          };
          const ret90   = calcPeriodReturn(d.history_90   || []);
          const ret365  = calcPeriodReturn(d.history_365  || []);
          const ret1095 = calcPeriodReturn(d.history_1095 || []);
          const retMap  = {'90': ret90, '365': ret365, '1095': ret1095};
          const currRet = retMap[idxTab];
          return (
            <div key={name} className="glass-panel" style={{ padding:'1.2rem 1.4rem' }}>
              {/* 헤더: 지수명 + 날짜 인라인 */}
              <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.35rem' }}>
                <span style={{ ...lbl, marginBottom:0 }}>{name}</span>
                <span style={{ fontSize:'0.65rem', color:'var(--text-secondary)' }}>{d.date||'-'}</span>
              </div>
              {/* 지수 값 */}
              <div style={{ display:'flex', alignItems:'baseline', gap:'0.5rem', flexWrap:'wrap', marginBottom:'0.8rem' }}>
                <span style={big}>{fv(d.value)}</span>
                {d.value != null && d.change != null && (() => {
                  const chgPct = d.change || 0;
                  const diff = d.value - d.value / (1 + chgPct / 100);
                  const clr = pc(chgPct);
                  const pStr = Math.abs(chgPct) < 0.1 ? Math.abs(chgPct).toFixed(2) : Math.abs(chgPct).toFixed(1);
                  return (
                    <span style={{ display:'flex', alignItems:'baseline', gap:'0.3rem' }}>
                      <span style={{ fontSize:'0.9rem', fontWeight:700, color:clr }}>
                        {arr(chgPct)} {diff >= 0 ? '+' : '-'}{Math.round(Math.abs(diff)).toLocaleString('ko-KR')}
                      </span>
                      <span style={{ fontSize:'0.8rem', fontWeight:600, color:clr }}>
                        ({pStr}%)
                      </span>
                    </span>
                  );
                })()}
              </div>
              {/* 수급: 당일(좌) + 5일 누적(우) */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.8rem', marginBottom:'0.9rem' }}>
                <div style={{ borderLeft:'2px solid rgba(255,255,255,0.1)', paddingLeft:'0.7rem' }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.25rem' }}>
                    <span style={{ fontSize:'0.65rem', color:'var(--text-secondary)', fontWeight:600 }}>당일 수급</span>
                    {noSupply && <span style={{ fontSize:'0.6rem', color:'rgba(255,255,255,0.25)' }}>수집 중</span>}
                  </div>
                  {[{label:'외국인',val:d.frn_net_buy},{label:'기관',val:d.inst_net_buy},{label:'개인',val:d.ind_net_buy}].map(({label,val})=>(
                    <div key={label} style={{ display:'flex', justifyContent:'space-between', padding:'0.1rem 0' }}>
                      <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)' }}>{label}</span>
                      <span style={{ fontSize:'0.76rem', fontWeight:700, color:val>0?'#ef4444':val<0?'#3b82f6':'rgba(255,255,255,0.3)' }}>{fq(val)}</span>
                    </div>
                  ))}
                </div>
                <div style={{ borderLeft:'2px solid rgba(255,255,255,0.1)', paddingLeft:'0.7rem' }}>
                  <span style={{ fontSize:'0.65rem', color:'var(--text-secondary)', fontWeight:600, display:'block', marginBottom:'0.25rem' }}>5일 누적</span>
                  {[{label:'외국인',val:d.frn_5d},{label:'기관',val:d.inst_5d},{label:'개인',val:d.ind_5d}].map(({label,val})=>(
                    <div key={label} style={{ display:'flex', justifyContent:'space-between', padding:'0.1rem 0' }}>
                      <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)' }}>{label}</span>
                      <span style={{ fontSize:'0.76rem', fontWeight:700, color:val>0?'#ef4444':val<0?'#3b82f6':'rgba(255,255,255,0.3)' }}>{fq(val)}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ borderTop:'1px solid var(--glass-border)', paddingTop:'0.7rem' }}>
                <div style={{ display:'flex', alignItems:'center', gap:'0.4rem', marginBottom:'0.5rem', flexWrap:'wrap' }}>
                  {[['90','3개월'],['365','1년'],['1095','3년']].map(([val,label])=>(
                    <button key={val} onClick={()=>setIdxTab(val)} style={{
                      padding:'0.15rem 0.55rem', borderRadius:'4px', fontSize:'0.68rem', cursor:'pointer',
                      fontWeight:idxTab===val?700:400,
                      border:idxTab===val?`1px solid ${idxColor}`:'1px solid rgba(255,255,255,0.12)',
                      background:idxTab===val?`${idxColor}22`:'transparent',
                      color:idxTab===val?idxColor:'rgba(255,255,255,0.4)',
                    }}>{label}</button>
                  ))}
                  {currRet != null && (
                    <span style={{
                      fontSize:'0.78rem', fontWeight:700,
                      color: currRet >= 0 ? '#ef4444' : '#3b82f6',
                      marginLeft:'0.2rem',
                    }}>
                      {currRet >= 0 ? '▲' : '▼'} {Math.abs(currRet).toFixed(1)}%
                    </span>
                  )}
                </div>
                <MiniChart data={histData} color={idxColor} height={90} dec={2} />
              </div>
            </div>
          );
        })}
      </div>

      {/* ══ PARA 1-a: KOSPI200 / KOSDAQ150 (파생 지수, 2-column) ══ */}
      {(() => {
        const kp200  = idx['KOSPI200']  || null;
        const kq150  = idx['KOSDAQ150'] || null;
        if (!kp200?.value && !kq150?.value) return null;
        const Cell = ({label, d}) => d?.value ? (
          <div style={{ display:'flex', alignItems:'baseline', gap:'0.5rem' }}>
            <span style={{ fontSize:'0.78rem', fontWeight:700, color:'rgba(255,255,255,0.6)' }}>{label}</span>
            <span style={{ fontSize:'0.95rem', fontWeight:800 }}>{fv(d.value, 2)}</span>
            {d.change != null && (() => {
              const chgPct = d.change || 0;
              const pStr = Math.abs(chgPct) < 0.1 ? Math.abs(chgPct).toFixed(2) : Math.abs(chgPct).toFixed(1);
              return (
                <span style={{ fontSize:'0.8rem', fontWeight:700, color:pc(chgPct) }}>
                  {arr(chgPct)} {pStr}%
                </span>
              );
            })()}
            <span style={{ fontSize:'0.65rem', color:'var(--text-secondary)' }}>{d.date || ''}</span>
          </div>
        ) : <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>-</span>;
        return (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem' }}>
            <div className="glass-panel" style={{ padding:'0.75rem 1.2rem', display:'flex', alignItems:'center', gap:'1rem' }}>
              <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600 }}>📈 KOSPI200</span>
              <Cell label="" d={kp200} />
            </div>
            <div className="glass-panel" style={{ padding:'0.75rem 1.2rem', display:'flex', alignItems:'center', gap:'1rem' }}>
              <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600 }}>📈 KOSDAQ150</span>
              <Cell label="" d={kq150} />
            </div>
          </div>
        );
      })()}

      {/* ══ PARA 1-b: 나스닥 / S&P500 ══ */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem' }}>
        {[['NASDAQ','#a78bfa',nasdaqTab,setNasdaqTab],['S&P500','#fb923c',sp500Tab,setSp500Tab]].map(([name,idxColor,idxTab,setIdxTab]) => {
          const d = idx[name] || {};
          const histData = idxTab==='90'?(d.history_90||[]):idxTab==='365'?(d.history_365||[]):(d.history_1095||[]);
          const calcPeriodReturn = (hist) => {
            if (!hist || hist.length < 2) return null;
            const first = hist[0]?.close; const last = hist[hist.length-1]?.close;
            if (!first || !last || first === 0) return null;
            return ((last - first) / first * 100);
          };
          const retMap = {'90': calcPeriodReturn(d.history_90||[]), '365': calcPeriodReturn(d.history_365||[]), '1095': calcPeriodReturn(d.history_1095||[])};
          const currRet = retMap[idxTab];
          return (
            <div key={name} className="glass-panel" style={{ padding:'1.2rem 1.4rem' }}>
              <div style={{ marginBottom:'0.9rem' }}>
                <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.35rem' }}>
                  <span style={{ ...lbl, marginBottom:0 }}>{name}</span>
                  <span style={{ fontSize:'0.65rem', color:'var(--text-secondary)' }}>{d.date||'-'}</span>
                </div>
                <div style={{ display:'flex', alignItems:'baseline', gap:'0.5rem', flexWrap:'wrap' }}>
                  <span style={big}>{fv(d.value, 2)}</span>
                  {d.value != null && d.change != null && (() => {
                    const chgPct = d.change || 0;
                    const diff = d.value - d.value / (1 + chgPct / 100);
                    const clr = pc(chgPct);
                    const pctStr = Math.abs(chgPct) < 0.1
                      ? Math.abs(chgPct).toFixed(2)
                      : Math.abs(chgPct).toFixed(1);
                    return (
                      <span style={{ display:'flex', alignItems:'baseline', gap:'0.3rem' }}>
                        <span style={{ fontSize:'0.9rem', fontWeight:700, color:clr }}>
                          {arr(chgPct)} {diff >= 0 ? '+' : '-'}{Math.abs(diff).toFixed(1)}
                        </span>
                        <span style={{ fontSize:'0.8rem', fontWeight:600, color:clr }}>
                          ({pctStr}%)
                        </span>
                      </span>
                    );
                  })()}
                </div>
              </div>
              <div style={{ borderTop:'1px solid var(--glass-border)', paddingTop:'0.7rem' }}>
                <div style={{ display:'flex', alignItems:'center', gap:'0.4rem', marginBottom:'0.5rem', flexWrap:'wrap' }}>
                  {[['90','3개월'],['365','1년'],['1095','3년']].map(([val,label])=>(
                    <button key={val} onClick={()=>setIdxTab(val)} style={{
                      padding:'0.15rem 0.55rem', borderRadius:'4px', fontSize:'0.68rem', cursor:'pointer',
                      fontWeight:idxTab===val?700:400,
                      border:idxTab===val?`1px solid ${idxColor}`:'1px solid rgba(255,255,255,0.12)',
                      background:idxTab===val?`${idxColor}22`:'transparent',
                      color:idxTab===val?idxColor:'rgba(255,255,255,0.4)',
                    }}>{label}</button>
                  ))}
                  {currRet != null && (
                    <span style={{
                      fontSize:'0.78rem', fontWeight:700,
                      color: currRet >= 0 ? '#ef4444' : '#3b82f6',
                      marginLeft:'0.2rem',
                    }}>
                      {currRet >= 0 ? '▲' : '▼'} {Math.abs(currRet).toFixed(1)}%
                    </span>
                  )}
                </div>
                <MiniChart data={histData} color={idxColor} height={90} dec={2} />
              </div>
            </div>
          );
        })}
      </div>

      {/* ══ PARA 2: VIX + 30일 그래프 ══ */}
      <div className="glass-panel" style={card}>
        <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.4rem' }}>
          <span style={{ ...lbl, marginBottom:0 }}>VIX — 공포지수 (CBOE Volatility Index)</span>
          <span style={{ fontSize:'0.65rem', color:'var(--text-secondary)' }}>{vix.date||'-'}</span>
        </div>
        <div style={{ display:'grid', gridTemplateColumns:'180px 1fr', gap:'1.5rem', alignItems:'flex-start' }}>
          {/* 좌: 수치 + 배지 */}
          <div>
            <div style={{ display:'flex', alignItems:'baseline', gap:'0.5rem' }}>
              <span style={{ ...big, color: vixColor(vix.value) }}>{vix.value ?? '-'}</span>
              {vix.change != null && (
                <span style={{ fontSize:'0.82rem', fontWeight:600, color: pc(vix.change) }}>
                  {arr(vix.change)} {Math.abs(vix.change).toFixed(1)}%
                </span>
              )}
            </div>
            <div style={{
              display:'inline-block', marginTop:'0.7rem',
              padding:'0.2rem 0.8rem', borderRadius:'20px',
              fontSize:'0.72rem', fontWeight:700,
              background: vix.value >= 40 ? 'rgba(239,68,68,0.15)'  : vix.value >= 20 ? 'rgba(251,146,60,0.15)' : 'rgba(52,211,153,0.15)',
              color:       vix.value >= 40 ? '#ef4444'               : vix.value >= 20 ? '#fb923c'              : '#34d399',
              border: `1px solid ${vix.value>=40?'rgba(239,68,68,0.35)':vix.value>=20?'rgba(251,146,60,0.35)':'rgba(52,211,153,0.35)'}`,
            }}>
              {vixLabel(vix.value)}
            </div>
            {/* 임계선 범례 */}
            <div style={{ marginTop:'0.7rem', display:'flex', flexDirection:'column', gap:'0.2rem' }}>
              {[['#34d399','< 20','안정적'],['#fb923c','20~40','주의단계'],['#ef4444','≥ 40','극심한 공포']].map(([color,range,desc])=>(
                <div key={range} style={{ display:'flex', alignItems:'center', gap:'0.35rem', fontSize:'0.67rem', color:'var(--text-secondary)' }}>
                  <span style={{ width:'8px', height:'8px', borderRadius:'50%', background:color, flexShrink:0 }} />
                  <span style={{ color }}>{range}</span>
                  <span>{desc}</span>
                </div>
              ))}
            </div>
          </div>
          {/* 우: 30일 차트 */}
          <div>
            <p style={{ fontSize:'0.67rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>30일 추이</p>
            <div style={{ height:'130px' }}>
              <VixChart data={vix.history||[]} color={vixColor(vix.value)} />
            </div>
          </div>
        </div>
      </div>

      {/* ══ PARA 2-b: 미국 국채 2Y/10Y/30Y ══ */}
      <div className="glass-panel" style={card}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.6rem' }}>
          <span style={{ ...lbl, marginBottom:0 }}>미국 국채 금리 (2Y / 10Y / 30Y)</span>
          <span style={{ fontSize:'0.65rem', color:'var(--text-secondary)' }}>{tsy.updated_at || '-'}</span>
        </div>
        <div style={{ marginTop:'0.1rem', display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.6rem' }}>
          <div style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>
            10Y-2Y: <span style={{ fontWeight:700, color:(tsy.spreads?.['10Y_minus_2Y'] ?? 0) < 0 ? '#ef4444' : '#93c5fd' }}>
              {tsy.spreads?.['10Y_minus_2Y'] == null ? '-' : `${tsy.spreads['10Y_minus_2Y'].toFixed(2)}%p`}
            </span>
            <span style={{ marginLeft:'0.45rem' }}>(장단기 역전 체크)</span>
          </div>
          <div style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>
            30Y-10Y: <span style={{ fontWeight:700, color:(tsy.spreads?.['30Y_minus_10Y'] ?? 0) < 0 ? '#ef4444' : '#93c5fd' }}>
              {tsy.spreads?.['30Y_minus_10Y'] == null ? '-' : `${tsy.spreads['30Y_minus_10Y'].toFixed(2)}%p`}
            </span>
            <span style={{ marginLeft:'0.45rem' }}>(희귀 위험 조합)</span>
          </div>
        </div>

        {(() => {
          const isInversion = (tsy.spreads?.['10Y_minus_2Y'] ?? 0) < 0 || (tsy.spreads?.['30Y_minus_10Y'] ?? 0) < 0;
          const isDanger = (tsy.risk?.level === '위험') || isInversion;
          const isWarn = (tsy.risk?.level === '주의');
          const riskBg = isDanger ? 'rgba(239,68,68,0.16)' : isWarn ? 'rgba(251,146,60,0.16)' : 'rgba(52,211,153,0.10)';
          const riskBd = isDanger ? 'rgba(239,68,68,0.45)' : isWarn ? 'rgba(251,146,60,0.45)' : 'rgba(52,211,153,0.35)';
          return (
        <div style={{ marginTop:'0.65rem', padding:'0.5rem 0.7rem', borderRadius:'8px',
          background:riskBg, border:`1px solid ${riskBd}` }}>
          <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.3rem' }}>
            <span style={{ fontSize:'0.72rem', color:'var(--text-secondary)' }}>오늘 금리 위험신호</span>
            <span style={{ fontSize:'0.72rem', fontWeight:700, color:riskColor(tsy.risk?.level) }}>
              {tsy.risk?.level || '데이터 대기'}
            </span>
          </div>
          <div style={{ fontSize:'0.73rem', color:'rgba(255,255,255,0.82)', lineHeight:1.5, whiteSpace:'pre-line' }}>
            {tsy.ai_summary || '수집 중입니다.'}
          </div>
        </div>
          );
        })()}

        <div style={{ marginTop:'0.7rem', display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:'1rem' }}>
          {[
            { key:'US2Y', label:'미국 2년물', unit:'%', color:'#60a5fa', interp:(v)=> v==null?'-': v>=4.8 ? '정책금리 고점 부담' : '단기 정책 기대 반영' },
            { key:'US10Y', label:'미국 10년물', unit:'%', color:'#f59e0b', interp:(v)=> v==null?'-': v>=5.0 ? '금융시장 스트레스 급증 가능' : v>=4.5 ? '위험자산 압박 구간' : '비교적 안정 구간' },
            { key:'US30Y', label:'미국 30년물', unit:'%', color:'#ef4444', interp:(v)=> v==null?'-': v>=5.5 ? '매우 위험한 수준 가능' : v>=5.0 ? '장기 재정 신뢰 우려' : '정상~주의 구간' },
          ].map(({ key, label, unit, color, interp }) => {
            const d = tsy[key] || {};
            const ttab = treasuryTabs[key] || '90';
            const th = d.history || [];
            const calcTsyReturn = (days) => {
              if (!th.length || th.length < 2) return null;
              const sorted = [...th].sort((a,b) => a.date > b.date ? 1 : -1);
              const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - days);
              const filtered = sorted.filter(r => new Date(r.date) >= cutoff);
              if (filtered.length < 2) return null;
              const first = filtered[0]?.close; const last = filtered[filtered.length-1]?.close;
              if (first == null || first === 0 || last == null) return null;
              return ((last - first) / first * 100);
            };
            const tRetMap = { '90': calcTsyReturn(90), '180': calcTsyReturn(180), '365': calcTsyReturn(365) };
            const tCurRet = tRetMap[ttab];
            const tDays = ttab === '90' ? 90 : ttab === '180' ? 180 : 365;
            const tHistFiltered = (() => {
              if (!th.length) return [];
              const sorted = [...th].sort((a,b) => a.date > b.date ? 1 : -1);
              const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - tDays);
              return sorted.filter(r => new Date(r.date) >= cutoff);
            })();
            const trendUp = (tCurRet ?? 0) >= 0;
            const trendColor = tCurRet == null ? 'rgba(255,255,255,0.45)' : (trendUp ? '#ef4444' : '#3b82f6');
            return (
              <div key={key} className="glass-panel" style={{ padding:'0.9rem 1rem' }}>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:'0.18rem' }}>
                  <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginBottom:0 }}>{label}</p>
                  <div style={{ textAlign:'right' }}>
                    <p style={{ fontSize:'0.72rem', color:'var(--text-secondary)', marginBottom:'0.12rem' }}>{d.date || '-'}</p>
                    <p style={{ fontSize:'0.7rem', color:'var(--text-secondary)' }}>{interp(d.value)}</p>
                  </div>
                </div>
                <h3 style={{ fontSize:'2rem', lineHeight:1, marginBottom:'0.15rem', color:'#ffffff', fontWeight:800 }}>
                  {d.value == null ? '-' : Number(d.value).toFixed(2)}<span style={{ fontSize:'1rem', color:'var(--text-secondary)', marginLeft:'0.2rem' }}>{unit}</span>
                </h3>
                <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.32rem' }}>
                  <span style={{ fontSize:'0.96rem', color:pc(d.change||0), fontWeight:700 }}>
                    {d.change == null ? '-' : `${arr(d.change)} ${Math.abs(d.change).toFixed(2)}%`}
                  </span>
                </div>
                <div style={{ display:'flex', alignItems:'center', gap:'0.35rem', marginBottom:'0.35rem', flexWrap:'wrap' }}>
                  {[
                    ['90','3개월'],
                    ['180','6개월'],
                    ['365','1년'],
                  ].map(([k, lbl]) => {
                    const active = ttab === k;
                    return (
                      <button
                        key={k}
                        onClick={() => setTreasuryTabs(prev => ({ ...prev, [key]: k }))}
                        style={{
                          fontSize:'0.67rem', padding:'0.1rem 0.45rem', borderRadius:'4px',
                          border:`1px solid ${active ? 'rgba(45,212,191,0.6)' : 'rgba(255,255,255,0.15)'}`,
                          background: active ? 'rgba(45,212,191,0.12)' : 'transparent',
                          color: active ? '#2dd4bf' : 'rgba(255,255,255,0.45)', cursor:'pointer'
                        }}
                      >
                        {lbl}
                      </button>
                    );
                  })}
                  <span style={{
                    fontSize:'0.8rem', fontWeight:700, marginLeft:'0.2rem',
                    color: tCurRet == null ? 'rgba(255,255,255,0.45)' : (tCurRet >= 0 ? '#ef4444' : '#3b82f6')
                  }}>
                    {tCurRet == null ? '-' : `${tCurRet >= 0 ? '▲' : '▼'} ${Math.abs(tCurRet).toFixed(2)}%`}
                  </span>
                </div>
                <MiniChart data={tHistFiltered} color={tCurRet == null ? color : (trendUp ? '#ef4444' : '#3b82f6')} height={85} dec={2} />
              </div>
            );
          })}
        </div>
      </div>

      {/* ══ PARA 3: 원달러·금·유가 + 미니차트 ══ */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:'1rem' }}>
        {[
          { key:'USD/KRW', label:'원/달러 환율', unit:'원',      color:'#60a5fa', dec:2 },
          { key:'GOLD',    label:'금 (XAU/USD)', unit:'USD/oz',  color:'#fbbf24', dec:1 },
          { key:'OIL',     label:'WTI 원유',     unit:'USD/bbl', color:'#f97316', dec:2 },
        ].map(({ key, label, unit, color, dec }) => {
          const d = comm[key] || {};
          const [commTab, setCommTab] = React.useState('90');
          const commHistory = d.history || [];
          // 탭별 기간 수익률
          const calcCommReturn = (days) => {
            if (commHistory.length < 2) return null;
            const sorted = [...commHistory].sort((a,b) => a.date > b.date ? 1 : -1);
            const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - days);
            const filtered = sorted.filter(r => new Date(r.date) >= cutoff);
            if (filtered.length < 2) return null;
            const first = filtered[0]?.close; const last = filtered[filtered.length-1]?.close;
            if (!first || first === 0) return null;
            return ((last - first) / first * 100);
          };
          const commRetMap = {'90': calcCommReturn(90), '365': calcCommReturn(365), '1095': calcCommReturn(1095)};
          const commCurrRet = commRetMap[commTab];
          const commHistFiltered = (() => {
            if (!commHistory.length) return [];
            const sorted = [...commHistory].sort((a,b) => a.date > b.date ? 1 : -1);
            const days = commTab==='90'?90:commTab==='365'?365:1095;
            const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - days);
            return sorted.filter(r => new Date(r.date) >= cutoff);
          })();
          return (
            <div key={key} className="glass-panel" style={card}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
                <p style={lbl}>{label}</p>
                <p style={{ fontSize:'0.65rem', color:'var(--text-secondary)' }}>{d.date||'-'}</p>
              </div>
              <div style={{ display:'flex', alignItems:'baseline', gap:'0.4rem' }}>
                <span style={{ fontSize:'1.3rem', fontWeight:700 }}>{fv(d.value, dec)}</span>
                <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)' }}>{unit}</span>
                <span style={{ fontSize:'0.8rem', fontWeight:700, color: pc(d.change) }}>
                  {arr(d.change||0)} {Math.abs(d.change||0).toFixed(1)}%
                </span>
              </div>
              <div style={{ marginBottom:'0.4rem' }} />
              {/* 탭 버튼 + 기간 수익률 */}
              <div style={{ display:'flex', alignItems:'center', gap:'0.3rem', marginBottom:'0.4rem', flexWrap:'wrap' }}>
                {[['90','3개월'],['365','1년'],['1095','3년']].map(([val,lbl2])=>(
                  <button key={val} onClick={()=>setCommTab(val)} style={{
                    padding:'0.1rem 0.45rem', borderRadius:'4px', fontSize:'0.65rem', cursor:'pointer',
                    fontWeight:commTab===val?700:400,
                    border:commTab===val?`1px solid ${color}`:'1px solid rgba(255,255,255,0.12)',
                    background:commTab===val?`${color}22`:'transparent',
                    color:commTab===val?color:'rgba(255,255,255,0.4)',
                  }}>{lbl2}</button>
                ))}
                {commCurrRet != null && (
                  <span style={{
                    fontSize:'0.75rem', fontWeight:700,
                    color: commCurrRet >= 0 ? '#ef4444' : '#3b82f6',
                    marginLeft:'0.1rem',
                  }}>
                    {commCurrRet >= 0 ? '▲' : '▼'} {Math.abs(commCurrRet).toFixed(1)}%
                  </span>
                )}
              </div>
              <MiniChart data={commHistFiltered.length ? commHistFiltered : commHistory} color={color} height={70} dec={dec} />
            </div>
          );
        })}
      </div>

    </div>
    );
  });

  // ── 개별 종목 분석 ───────────────────────────────────────────
  const StockAnalysis = () => {
    const displayChartData = React.useMemo(() => {
      if (!chartData.length) return [];
      return chartData.slice(-chartDays);
    }, [chartData, chartDays]);
    const isMobile = useIsMobile();
    // ── 데이터 신뢰도 ─────────────────────────────────────────────────
    const [dataQuality, setDataQuality] = React.useState(null);
    const [dqExpanded, setDqExpanded] = React.useState(false);
    React.useEffect(() => {
      if (!selectedStock || !/^\d{6}$/.test(selectedStock)) { setDataQuality(null); return; }
      setDataQuality(null);
      setDqExpanded(false);
      fetch(API(`/api/dashboard/data-quality/${selectedStock}`))
        .then(r => r.ok ? r.json() : null)
        .then(d => setDataQuality(d))
        .catch(() => {});
    }, [selectedStock]);

    // 종목별 보고서
    const [stockReports, setStockReports] = React.useState([]);
    const [reportsExpanded, setReportsExpanded] = React.useState(false);
    React.useEffect(() => {
      if (!selectedStock) return;
      setStockReports([]);
      setReportsExpanded(false);
      fetch(API(`/api/reports/stock/${selectedStock}`))
        .then(r => r.ok ? r.json() : [])
        .then(d => setStockReports(d || []))
        .catch(() => {});
    }, [selectedStock]);

    // ── DART 공시 조회 (5분 폴링 / 장일 08:00~20:00 KST) ────────────
    const [disclosures, setDisclosures] = React.useState([]);
    const [disclosureLoading, setDisclosureLoading] = React.useState(false);
    const [showAllDisclosures, setShowAllDisclosures] = React.useState(false);
    const DISCLOSURE_PREVIEW = 5; // 기본 표시 건수

    const fetchDisclosures = React.useCallback(async () => {
      if (!selectedStock) return;
      // 국내 종목만 (6자리 숫자)
      if (!/^\d{6}$/.test(selectedStock)) return;
      try {
        setDisclosureLoading(true);
        const res = await fetch(API(`/api/dashboard/disclosures/${selectedStock}`));
        if (res.ok) setDisclosures(await res.json());
      } catch {}
      finally { setDisclosureLoading(false); }
    }, [selectedStock]);

    React.useEffect(() => {
      if (!selectedStock) return;
      setDisclosures([]);
      setShowAllDisclosures(false);
      fetchDisclosures();
      // 공시 가능 시간(평일 08:00~20:00)에만 5분 폴링
      if (!isDisclosureTime()) return;
      const iv = setInterval(fetchDisclosures, 300000);
      return () => clearInterval(iv);
    }, [selectedStock, fetchDisclosures]);

    // ── 추가 시그널 (고용/수출/섹터/수급/ETF) ───────────────────────
    const [extraSignals, setExtraSignals] = React.useState(null);
    const [extraSignalsLoading, setExtraSignalsLoading] = React.useState(false);
    React.useEffect(() => {
      if (!selectedStock || !/^\d{6}$/.test(selectedStock)) { setExtraSignals(null); setExtraSignalsLoading(false); return; }
      setExtraSignals(null);
      setExtraSignalsLoading(true);
      fetch(API(`/api/extra-signals/extra-signals/${selectedStock}`))
        .then(r => r.ok ? r.json() : null)
        .then(d => { setExtraSignals(d); setExtraSignalsLoading(false); })
        .catch(() => { setExtraSignalsLoading(false); });
    }, [selectedStock]);

    // ── KRX 공지사항 + 대주주/임원 지분변동 ────────────────────────
    const [notices, setNotices] = React.useState([]);
    const [majorHolders, setMajorHolders] = React.useState({ current_holders: [], history: [] });
    const [insiderHist, setInsiderHist] = React.useState([]);
    React.useEffect(() => {
      if (!selectedStock || !/^\d{6}$/.test(selectedStock)) {
        setNotices([]); setMajorHolders({ current_holders: [], history: [] }); setInsiderHist([]); return;
      }
      fetch(API(`/api/notices/stock/${selectedStock}`))
        .then(r => r.ok ? r.json() : [])
        .then(d => setNotices(Array.isArray(d) ? d : []))
        .catch(() => {});
      fetch(API(`/api/insider/major/${selectedStock}?limit=50`))
        .then(r => r.ok ? r.json() : null)
        .then(d => setMajorHolders(d || { current_holders: [], history: [] }))
        .catch(() => {});
      fetch(API(`/api/insider/holdings/${selectedStock}?limit=20`))
        .then(r => r.ok ? r.json() : null)
        .then(d => setInsiderHist((d && d.items) || []))
        .catch(() => {});
    }, [selectedStock]);

    const numColor = (v) => (v != null && Number(v) < 0) ? 'var(--accent-red)' : 'inherit';

    const tableRows = [
      { label:'매출액',    key:'revenue',     fmt:fmtUkWon },
      { label:'영업이익',  key:'op_profit',   fmt:fmtUkWon },
      { label:'순이익',    key:'net_income',  fmt:fmtUkWon },
      { label:'영업이익률', key:'opm',        fmt:v => v!=null ? Number(v).toFixed(1)+'%' : '-' },
      { label:'자산',      key:'assets',      fmt:fmtUkWon },
      { label:'부채',      key:'liabilities', fmt:fmtUkWon },
      { label:'자본',      key:'equity',      fmt:fmtUkWon },
      { label:'자본금',    key:'capital',     fmt:fmtUkWon },
      { label:'EPS(원)',   key:'eps',         fmt: v => (v == null || v === 0) ? '-' : Math.round(Number(v)).toLocaleString('ko-KR') },
    ];

    // watchlist → selectedStockName (analyze 응답) → 종목코드 순으로 fallback
    const stockName = watchlist.find(i => i.stock_code === selectedStock)?.stock_name
                   || selectedStockName
                   || selectedStock;
    const latestClose = chartData.length > 0 ? chartData[chartData.length-1].close : null;

    return (
      <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>

        {/* 수집 중 배너 */}
        {collecting && (
          <div style={{ padding:'0.75rem 1.2rem', background:'rgba(45,212,191,0.09)', border:'1px solid rgba(45,212,191,0.3)', borderRadius:'8px', display:'flex', alignItems:'center', gap:'0.75rem' }}>
            <div style={{ width:'14px', height:'14px', borderRadius:'50%', border:'2px solid var(--accent-mint)', borderTopColor:'transparent', animation:'spin 0.8s linear infinite', flexShrink:0 }}/>
            <div style={{ fontSize:'0.83rem' }}>
              <span style={{ fontWeight:700, color:'var(--accent-mint)' }}>📡 실시간 데이터 수집 중</span>
              <span style={{ marginLeft:'0.5rem', color:'rgba(45,212,191,0.75)' }}>
                — Yahoo Finance · KIS · DART에서 주가 1년치 및 재무데이터를 수집 중입니다. 10초마다 자동 업데이트 (최대 4분).
              </span>
            </div>
          </div>
        )}
        {/* 재무 없음 경고 (수집 완료 후에도 재무 없는 경우) */}
        {!collecting && summStats !== null && summStats.revenue === null && chartData.length > 0 && (
          <div style={{ padding:'0.65rem 1.2rem', background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.25)', borderRadius:'8px', fontSize:'0.82rem', color:'#fbbf24', display:'flex', alignItems:'center', gap:'0.6rem' }}>
            <span style={{ fontWeight:700 }}>⚠</span>
            <span>재무제표 없음 — DART 미등록 종목이거나 아직 공시 전입니다. 매일 자정 공시 기준으로 자동 업데이트됩니다.</span>
          </div>
        )}

        {/* 헤더 */}
        <header className="glass-panel" style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'1rem 1.5rem' }}>
          <div style={{ display:'flex', alignItems:'center', gap:'1rem', flexWrap:'wrap' }}>
            {/* 종목명 + 시장정보 블록 */}
            <div>
              <div style={{ display:'flex', alignItems:'baseline', gap:'0.6rem' }}>
                <h2 style={{ fontSize:'1.3rem' }}>{stockName} <span style={{ fontSize:'0.9rem', color:'var(--text-secondary)' }}>({selectedStock})</span></h2>
              </div>
              {/* 종목명 아래: 시장구분·시총·순위 */}
              <div style={{ display:'flex', alignItems:'center', gap:'0.6rem', marginTop:'0.25rem', flexWrap:'wrap' }}>
                {marketInfo.market && (
                  <span style={{ fontSize:'0.7rem', padding:'0.1rem 0.55rem', borderRadius:'20px', fontWeight:700,
                    background: marketInfo.market === 'KOSPI' ? 'rgba(45,212,191,0.15)' : 'rgba(167,139,250,0.15)',
                    color:      marketInfo.market === 'KOSPI' ? 'var(--accent-mint)'    : 'var(--accent-purple)',
                    border:     marketInfo.market === 'KOSPI' ? '1px solid rgba(45,212,191,0.3)' : '1px solid rgba(167,139,250,0.3)',
                  }}>{marketInfo.market}</span>
                )}
                {marketInfo.mktcap && (
                  <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>
                    시총 <span style={{ color:'var(--text-primary)', fontWeight:600 }}>
                      {fmtUkWon(marketInfo.mktcap)}
                    </span>
                  </span>
                )}
                {marketInfo.mktcap_rank && (
                  <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>
                    시총순위 <span style={{ color:'var(--text-primary)', fontWeight:600 }}>{marketInfo.mktcap_rank}위</span>
                  </span>
                )}
              </div>
            </div>
            {/* 현재가 */}
            {latestClose && (
              <span style={{ fontSize:'1.6rem', fontWeight:700, color:'var(--accent-mint)' }}>
                {latestClose.toLocaleString('ko-KR')}원
              </span>
            )}
            {/* 당일 변동률 + 변동금액 + 유통주식수 */}
            {chartData.length >= 1 && (() => {
              const last = chartData[chartData.length-1];
              let chg = last?.change_rate ?? null;
              let vs  = last?.vs ?? null;
              if (chg == null && chartData.length >= 2) {
                const prev = chartData[chartData.length-2]?.close;
                const curr = last?.close;
                if (prev && curr && prev !== 0) {
                  chg = (curr - prev) / prev * 100;
                  vs  = curr - prev;
                }
              }
              if (chg == null) return null;
              const clr = chg > 0 ? '#ef4444' : chg < 0 ? '#3b82f6' : 'rgba(255,255,255,0.4)';

              // 유통주식수 포맷 (주 단위)
              const fmtShares = (v) => {
                if (!v) return null;
                if (v >= 1e8)  return (v/1e8).toFixed(1)+'억주';
                if (v >= 1e4)  return Math.round(v/1e4).toLocaleString('ko-KR')+'만주';
                return Math.round(v).toLocaleString('ko-KR')+'주';
              };
              const floatStr  = fmtShares(summStats?.float_shares);
              const totalStr  = fmtShares(summStats?.shares_outstanding);
              const floatRatio = (summStats?.float_shares && summStats?.shares_outstanding)
                ? ((summStats.float_shares / summStats.shares_outstanding) * 100).toFixed(1)
                : null;

              return (
                <div style={{ display:'flex', flexDirection:'column', alignItems:'flex-start', gap:'0.1rem' }}>
                  <span style={{ fontSize:'1rem', fontWeight:700, color: clr }}>
                    {chg > 0 ? '▲' : chg < 0 ? '▼' : '▶'} {Math.abs(chg).toFixed(1)}%
                  </span>
                  {vs != null && vs !== 0 && (
                    <span style={{ fontSize:'0.75rem', color: clr, opacity:0.8 }}>
                      ({vs > 0 ? '+' : ''}{Math.round(vs).toLocaleString('ko-KR')}원)
                    </span>
                  )}
                  {/* 유통주식수 배지 */}
                  {floatStr && (
                    <div title={`총발행주식 ${totalStr||'-'} 중 유통주식 ${floatStr} (대주주·임원 제외)`}
                      style={{ marginTop:'0.2rem', display:'inline-flex', alignItems:'center', gap:'0.4rem',
                        padding:'0.25rem 0.7rem', borderRadius:'6px', cursor:'help',
                        background:'rgba(255,255,255,0.07)', border:'1px solid rgba(255,255,255,0.15)' }}>
                      <span style={{ fontSize:'0.72rem', color:'rgba(255,255,255,0.45)' }}>유통</span>
                      <span style={{ fontSize:'0.85rem', fontWeight:700, color:'rgba(255,255,255,0.85)' }}>{floatStr}</span>
                      {floatRatio && (
                        <span style={{ fontSize:'0.75rem', color:'rgba(255,255,255,0.5)' }}>({floatRatio}%)</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
          {/* 구분선 + 수급 + 대차잔고 */}
          {chartData.length > 0 && (() => {
            const recent5 = chartData.slice(-5);
            const today   = chartData[chartData.length-1];
            // 당일
            const inst1 = today?.inst_net_buy || 0;
            const frn1  = today?.frn_net_buy  || 0;
            const ind1  = -(inst1 + frn1);
            // 5일 누적 (수량)
            const inst5 = recent5.reduce((s,d)=>s+(d.inst_net_buy||0),0);
            const frn5  = recent5.reduce((s,d)=>s+(d.frn_net_buy||0),0);
            const ind5  = -(inst5 + frn5);
            // 5일 누적 금액 (백만원→억원)
            const inst5a = recent5.reduce((s,d)=>s+(d.inst_net_buy_amt||0),0);
            const frn5a  = recent5.reduce((s,d)=>s+(d.frn_net_buy_amt||0),0);
            const ind5a  = -(inst5a + frn5a);

            const fmtQty = (v) => {
              if (!v) return '-';
              const sg=v>0?'+':'-', a=Math.abs(v);
              if(a>=10000) return sg+Math.round(a/10000).toLocaleString('ko-KR')+'만주';
              return sg+Math.round(a).toLocaleString('ko-KR')+'주';
            };
            const fmtAmt = (v) => {
              if(!v) return null;
              const sg=v>0?'+':'-', a=Math.abs(v);
              if(a>=100) return sg+Math.round(a/100).toLocaleString('ko-KR')+'억원';
              if(a>=1)   return sg+Math.round(a).toLocaleString('ko-KR')+'백만원';
              return null;
            };
            const hasSupply = inst1 !== 0 || frn1 !== 0 || inst5 !== 0 || frn5 !== 0
              || (today?.inst_net_buy_amt||0) !== 0 || (today?.frn_net_buy_amt||0) !== 0;
            if (!hasSupply) return null;

            // amt가 null/0이면 qty × close로 추정 (단위: 백만원)
            const estAmt = (qty, amt) =>
              (amt != null && amt !== 0) ? amt :
              (qty && today?.close ? Math.round(qty * today.close / 1e6) : null);
            const supplyData = [
              {lbl:'외국인', val1:frn1,  amt1:estAmt(frn1,  today?.frn_net_buy_amt),  val5:frn5,  amt5:frn5a},
              {lbl:'기관',   val1:inst1, amt1:estAmt(inst1, today?.inst_net_buy_amt), val5:inst5, amt5:inst5a},
              {lbl:'개인',   val1:ind1,  amt1:estAmt(ind1,  today?.ind_net_buy_amt),  val5:ind5,  amt5:ind5a},
            ];

            return (
              <>
                <div style={{ width:'1px', height:'60px', background:'rgba(255,255,255,0.15)', margin:'0 0.5rem' }} />
                <div style={{ display:'flex', flexDirection:'column', gap:'0.6rem' }}>
                <div style={{ fontSize:'0.62rem', color:'rgba(255,255,255,0.35)', letterSpacing:'0.03em' }}>
                  수급 기준일: <span style={{ color:'rgba(255,255,255,0.55)', fontWeight:600 }}>{today?.date?.slice(0,10) || '-'}</span>
                </div>
                <div style={{ display:'flex', gap:'1.2rem', fontSize:'0.8rem', alignItems:'flex-start' }}>
                  {supplyData.map(({lbl,val1,amt1,val5,amt5}) => (
                    <div key={lbl} style={{ textAlign:'center', minWidth:'70px' }}>
                      <p style={{ color:'var(--text-secondary)', fontSize:'0.68rem', marginBottom:'0.2rem', letterSpacing:'0.03em' }}>
                        {lbl}
                      </p>
                      {/* 당일 — 금액(위) / 주수(아래) */}
                      <p style={{ fontWeight:700, fontSize:'0.78rem', color: (amt1||0)>0?'#ef4444':(amt1||0)<0?'#3b82f6':'rgba(255,255,255,0.65)' }}>
                        {fmtAmt(amt1) ?? '-'}
                      </p>
                      <p style={{ fontSize:'0.65rem', color: val1>0?'rgba(239,68,68,0.65)':val1<0?'rgba(59,130,246,0.65)':'rgba(255,255,255,0.35)' }}>{fmtQty(val1)}</p>
                      {/* 5일 누적 — 금액(위) / 주수(아래) */}
                      <div style={{marginTop:'0.2rem',paddingTop:'0.2rem',borderTop:'1px solid rgba(255,255,255,0.08)'}}>
                        <p style={{fontSize:'0.6rem',color:'rgba(255,255,255,0.3)',marginBottom:'0.1rem'}}>5일누적</p>
                        <p style={{ fontWeight:600, fontSize:'0.72rem', color: (amt5||0)>0?'rgba(239,68,68,0.8)':(amt5||0)<0?'rgba(59,130,246,0.8)':'rgba(255,255,255,0.25)' }}>
                          {fmtAmt(amt5) ?? '-'}
                        </p>
                        <p style={{ fontSize:'0.62rem', color: val5>0?'rgba(239,68,68,0.5)':val5<0?'rgba(59,130,246,0.5)':'rgba(255,255,255,0.2)' }}>{fmtQty(val5)}</p>
                      </div>
                    </div>
                  ))}

                  {/* 대차잔고 — 신호등 + 수량 완전 분리 */}
                  {shortData && (() => {
                    const fmtBal = (v) => {
                      if(!v) return '-';
                      if(v >= 100000000) return (v/100000000).toFixed(1) + '억주';
                      if(v >= 10000000)  return (v/10000000).toFixed(1)  + '천만주';
                      if(v >= 10000)     return (v/10000).toFixed(1)     + '만주';
                      return Math.round(v).toLocaleString('ko-KR') + '주';
                    };
                    const lights = [
                      {label:'금일', val:shortData.today, sig:shortData.today_signal},
                      {label:'5일평균', val:shortData.avg5, sig:shortData.week_signal},
                    ];
                    return (
                      <>
                        <div style={{width:'1px',height:'70px',background:'rgba(255,255,255,0.15)',margin:'0 0.3rem'}}/>
                        <div style={{textAlign:'center'}}>
                          <p style={{color:'var(--text-secondary)',fontSize:'0.68rem',marginBottom:'0.15rem',letterSpacing:'0.03em'}}>
                            대차잔고
                          </p>
                          {shortData.latest_date && (
                            <p style={{fontSize:'0.58rem',color:'rgba(255,200,100,0.65)',marginBottom:'0.25rem'}}>
                              {shortData.latest_date}
                            </p>
                          )}
                          <div style={{display:'flex',gap:'5px'}}>
                            {lights.map(({label,val,sig})=>{
                              const color = sig==='green' ? '#22c55e' : '#ef4444';
                              return (
                                <div key={label} style={{display:'flex',flexDirection:'column',alignItems:'center',
                                  padding:'3px 7px',borderRadius:'5px',minWidth:'56px',
                                  background:`${color}12`,border:`1px solid ${color}35`}}>
                                  <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.4)',marginBottom:'1px'}}>{label}</span>
                                  <span style={{fontSize:'0.88rem',fontWeight:700,color,lineHeight:1.1}}>{sig==='green'?'▼':'▲'}</span>
                                  <span style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.6)',marginTop:'2px',fontWeight:500}}>
                                    {fmtBal(val)}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </>
                    );
                  })()}
                </div>
                </div>
              </>
            );
          })()}
        </header>

        {/* ── 데이터 신뢰도 배지 ───────────────────────────────────────── */}
        {dataQuality && (() => {
          const gradeIcon = { A:'🟢', B:'🔵', 'B+':'🟢', C:'🟡', D:'⚫' }[dataQuality.grade] || '⚫';
          const statusColor = {
            confirmed: '#10b981', ok: '#6b7280', ambiguous: '#f59e0b',
            missing: '#ef4444', partial: '#f59e0b', na: '#4b5563',
          };
          const statusBg = {
            confirmed: 'rgba(16,185,129,0.06)', ambiguous: 'rgba(245,158,11,0.10)',
            missing: 'rgba(239,68,68,0.08)', ok: 'transparent', na: 'transparent', partial: 'rgba(245,158,11,0.06)',
          };
          // 소스 뱃지 색상
          const srcColor = (s) => {
            if (!s) return '#4b5563';
            if (s.includes('Naver')) return '#3b82f6';
            if (s.includes('Seibro')) return '#8b5cf6';
            if (s.includes('2중')) return '#6b7280';
            return '#4b5563';
          };
          // 항목 수 집계
          const confirmedItems = dataQuality.items.filter(i => i.status === 'confirmed').length;
          const ambiguousItems = dataQuality.items.filter(i => i.status === 'ambiguous').length;
          return (
            <div className="glass-panel" style={{ padding:'0.75rem 1.1rem', border: `1px solid ${dataQuality.grade_color}33` }}>
              {/* 헤더 행 */}
              <div style={{ display:'flex', alignItems:'center', gap:'0.75rem', cursor:'pointer' }}
                   onClick={() => setDqExpanded(p => !p)}>
                {/* 등급 배지 */}
                <div style={{ display:'flex', alignItems:'center', gap:'0.4rem',
                              padding:'0.25rem 0.75rem', borderRadius:'20px',
                              background: `${dataQuality.grade_color}18`,
                              border: `1px solid ${dataQuality.grade_color}55`, flexShrink:0 }}>
                  <span style={{ fontSize:'0.9rem' }}>{gradeIcon}</span>
                  <span style={{ fontSize:'0.75rem', fontWeight:700, color: dataQuality.grade_color }}>
                    {dataQuality.grade} — {dataQuality.grade_label}
                  </span>
                </div>
                {/* 항목 수 요약 */}
                <div style={{ display:'flex', gap:'0.4rem', flexShrink:0 }}>
                  {confirmedItems > 0 && (
                    <span style={{ fontSize:'0.68rem', padding:'0.1rem 0.45rem', borderRadius:'10px',
                                   background:'rgba(16,185,129,0.12)', color:'#10b981', fontWeight:600 }}>
                      ✅ {confirmedItems}
                    </span>
                  )}
                  {ambiguousItems > 0 && (
                    <span style={{ fontSize:'0.68rem', padding:'0.1rem 0.45rem', borderRadius:'10px',
                                   background:'rgba(245,158,11,0.15)', color:'#f59e0b', fontWeight:600 }}>
                      ⚠️ {ambiguousItems}
                    </span>
                  )}
                </div>
                {/* 설명 */}
                <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)', flex:1 }}>
                  {dataQuality.grade_desc}
                  {dataQuality.val_year_min && (
                    <span style={{ marginLeft:'0.5rem', opacity:0.6 }}>
                      {dataQuality.val_year_min === dataQuality.val_year_max
                        ? `(검증기간: ${dataQuality.val_year_min}년)`
                        : `(검증기간: ${dataQuality.val_year_min}~${dataQuality.val_year_max}년, ${dataQuality.val_years?.length || 0}개년)`}
                    </span>
                  )}
                </span>
                {/* 펼치기 버튼 */}
                <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)', flexShrink:0 }}>
                  {dqExpanded ? '▲ 접기' : '▼ 상세보기'}
                </span>
              </div>

              {/* 상세 항목 테이블 (펼쳤을 때) */}
              {dqExpanded && (
                <div style={{ marginTop:'0.75rem', borderTop:'1px solid rgba(255,255,255,0.08)', paddingTop:'0.65rem' }}>
                  <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.76rem' }}>
                    <thead>
                      <tr style={{ color:'rgba(255,255,255,0.35)', fontSize:'0.68rem' }}>
                        <th style={{ textAlign:'left', paddingBottom:'0.4rem', fontWeight:600, letterSpacing:'0.04em', width:'28%' }}>항목</th>
                        <th style={{ textAlign:'left', paddingBottom:'0.4rem', fontWeight:600, letterSpacing:'0.04em', width:'22%' }}>상태</th>
                        <th style={{ textAlign:'left', paddingBottom:'0.4rem', fontWeight:600, letterSpacing:'0.04em', width:'22%' }}>검증 소스</th>
                        <th style={{ textAlign:'left', paddingBottom:'0.4rem', fontWeight:600, letterSpacing:'0.04em' }}>상세</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dataQuality.items.map((item, idx) => (
                        <tr key={idx} style={{
                          borderTop:'1px solid rgba(255,255,255,0.05)',
                          background: statusBg[item.status] || 'transparent',
                        }}>
                          <td style={{ padding:'0.35rem 0.5rem 0.35rem 0', color:'rgba(255,255,255,0.75)', minWidth:'130px', fontWeight: item.status==='ambiguous' ? 600 : 400 }}>
                            {item.field}
                          </td>
                          <td style={{ padding:'0.35rem 0.5rem', whiteSpace:'nowrap' }}>
                            <span style={{ color: statusColor[item.status] || '#6b7280', fontWeight:600 }}>
                              {item.label}
                            </span>
                          </td>
                          <td style={{ padding:'0.35rem 0.5rem', whiteSpace:'nowrap' }}>
                            {item.sources && item.sources !== '—' ? (
                              <span style={{
                                fontSize:'0.66rem', padding:'0.1rem 0.4rem', borderRadius:'8px',
                                background:'rgba(255,255,255,0.06)', color: srcColor(item.sources),
                                border:`1px solid ${srcColor(item.sources)}33`,
                              }}>
                                {item.sources}
                              </span>
                            ) : (
                              <span style={{ color:'rgba(255,255,255,0.2)', fontSize:'0.68rem' }}>—</span>
                            )}
                          </td>
                          <td style={{ padding:'0.35rem 0 0.35rem 0.5rem', color:'rgba(255,255,255,0.4)', fontSize:'0.7rem' }}>
                            {item.detail}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {/* 범례 */}
                  <div style={{ marginTop:'0.75rem', padding:'0.5rem 0.75rem', background:'rgba(255,255,255,0.03)',
                                borderRadius:'6px', display:'flex', gap:'0.8rem', flexWrap:'wrap',
                                fontSize:'0.66rem', color:'rgba(255,255,255,0.3)' }}>
                    <span style={{ color:'rgba(255,255,255,0.5)', fontWeight:600 }}>범례:</span>
                    <span><span style={{ color:'#10b981' }}>✅ 완전검증</span> 교차검증 CONFIRMED</span>
                    <span><span style={{ color:'#6b7280' }}>✔ 검증됨</span> 수집완료 (교차검증 미실시)</span>
                    <span><span style={{ color:'#f59e0b' }}>⚠️ 재확인</span> 소스 간 불일치</span>
                    <span><span style={{ color:'#ef4444' }}>❌ 미수집</span></span>
                    <span><span style={{ color:'#4b5563' }}>〰 구조적 한계</span> DART 미제공</span>
                    <span style={{ borderLeft:'1px solid rgba(255,255,255,0.1)', paddingLeft:'0.8rem' }}>
                      <span style={{ color:'#3b82f6' }}>●</span> DART+FnGuide+Naver(3중)
                      <span style={{ color:'#8b5cf6', marginLeft:'0.5rem' }}>●</span> DART+FnGuide+Seibro(3중)
                      <span style={{ color:'#6b7280', marginLeft:'0.5rem' }}>●</span> 2중
                    </span>
                  </div>
                </div>
              )}
            </div>
          );
        })()}

        {/* 재무 지표 + 52주 고저가 (6칸) */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(6,1fr)', gap:'0.75rem' }}>
          {[
            { label:'매출액',   val: formatWon(summStats?.revenue) },
            { label:'영업이익', val: formatWon(summStats?.operating_profit) },
            { label:'순이익',   val: formatWon(summStats?.net_income) },
            { label:'OPM',     val: summStats?.opm != null ? Number(summStats.opm).toFixed(1)+'%' : '-' },
            { label:'52주 최고가', val: summStats?.high52 != null ? summStats.high52.toLocaleString('ko-KR')+'원' : '-',
              sub: summStats?.high52 && latestClose ? `현재 ${((latestClose/summStats.high52-1)*100).toFixed(1)}%` : '',
              color: summStats?.high52 && latestClose && latestClose >= summStats.high52 * 0.95 ? '#22c55e' : '#ef4444' },
            { label:'52주 최저가', val: summStats?.low52 != null ? summStats.low52.toLocaleString('ko-KR')+'원' : '-',
              sub: summStats?.low52 && latestClose ? `현재 +${((latestClose/summStats.low52-1)*100).toFixed(1)}%` : '',
              color:'#fbbf24' },
          ].map(({label,val,sub,color}) => (
            <div key={label} className="glass-panel" style={{ padding:'0.9rem 1rem' }}>
              <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>{label}</p>
              <h3 style={{ fontSize:'1rem', color: color||'inherit' }}>{val || '-'}</h3>
              {sub && <p style={{ fontSize:'0.65rem', color:'var(--text-secondary)', marginTop:'0.2rem' }}>{sub}</p>}
            </div>
          ))}
        </div>

        {/* 밸류에이션: PBR / PER / EPS / BPS / ROE / ROA (6칸) */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(6,1fr)', gap:'0.75rem' }}>
          {[
            { label:'PBR', val: summStats?.pbr != null ? Number(summStats.pbr).toFixed(2)+'x' : '-',
              sub: summStats?.pbr != null ? (summStats.source||'네이버금융') : (collecting ? '📡 조회 중...' : '자정 업데이트 후 표시'), dim: summStats?.pbr==null, color:'var(--accent-purple)' },
            { label:'PER (TTM)', val: summStats?.per != null ? Number(summStats.per).toFixed(1)+'x' : '-',
              sub: collecting ? '📡 조회 중...' : (summStats?.per==null ? '업데이트 후 표시' : (summStats.source||'네이버금융')), dim: summStats?.per==null, color:'var(--accent-purple)' },
            { label:'EPS (원)', val: summStats?.trailing_eps != null ? fmtNum(summStats.trailing_eps)+'원' : (summStats?.eps != null ? fmtNum(summStats.eps)+'원' : '-'),
              sub: 'TTM 기준', dim: summStats?.trailing_eps==null && summStats?.eps==null, color:'#34d399' },
            { label:'BPS (원)', val: summStats?.bps != null ? fmtNum(summStats.bps)+'원' : '-',
              sub: '최신 연간', dim: summStats?.bps==null, color:'#60a5fa' },
            { label:'ROE', val: summStats?.roe != null ? Number(summStats.roe).toFixed(1)+'%' : '-',
              sub: '자기자본이익률', dim: summStats?.roe==null,
              color: summStats?.roe != null ? (summStats.roe >= 15 ? '#22c55e' : summStats.roe >= 8 ? '#fbbf24' : summStats.roe < 0 ? '#ef4444' : 'inherit') : 'inherit' },
            { label:'ROA', val: summStats?.roa != null ? Number(summStats.roa).toFixed(1)+'%' : '-',
              sub: '총자산이익률', dim: summStats?.roa==null,
              color: summStats?.roa != null ? (summStats.roa >= 5 ? '#22c55e' : summStats.roa < 0 ? '#ef4444' : 'inherit') : 'inherit' },
          ].map(({label,val,sub,dim,color}) => (
            <div key={label} className="glass-panel" style={{ padding:'0.9rem 1rem' }}>
              <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>{label}</p>
              <h3 style={{ fontSize:'1rem', color: dim ? 'var(--text-secondary)' : color }}>{val}</h3>
              <p style={{ fontSize:'0.65rem', color:'var(--text-secondary)', marginTop:'0.2rem' }}>{sub}</p>
            </div>
          ))}
        </div>

        {/* 차트 영역 */}
        <div style={{ display:'flex', flexDirection:'column', gap:'0.75rem' }}>
          {/* 종목 시그널 보드 */}
          <SignalBoard scope="stock" stockCode={selectedStock} key={selectedStock} />

          {/* 추가 시그널 */}
          {/^\d{6}$/.test(selectedStock) && extraSignalsLoading && (
            <div style={{ padding:'1rem 1.2rem', background:'var(--surface)', borderRadius:'10px', border:'1px solid var(--border)', color:'var(--text-secondary)', fontSize:'0.85rem', display:'flex', alignItems:'center', gap:'0.5rem' }}>
              <div style={{ width:'12px', height:'12px', borderRadius:'50%', border:'2px solid var(--accent-mint)', borderTopColor:'transparent', animation:'spin 0.8s linear infinite', flexShrink:0 }}/>
              추가 시그널 로딩 중...
            </div>
          )}
          {/^\d{6}$/.test(selectedStock) && !extraSignalsLoading && extraSignals && (() => {
            const sigColor = s => s==='green'?'#22c55e':s==='red'?'#ef4444':s==='yellow'?'#fbbf24':'#6b7280';
            const sigLabelColor = (s, isGrey) => isGrey ? 'var(--text-secondary)' : s==='green'?'#22c55e':s==='red'?'#ef4444':s==='yellow'?'#fbbf24':'var(--text-secondary)';
            const em = extraSignals.employment || {};
            const ex = extraSignals.exports || {};
            const st = extraSignals.sector_trend || {};
            const su = extraSignals.supply || {};
            const er = extraSignals.etf_ratio || {};
            const ei = extraSignals.etf_inclusion || {};
            const fmt억 = v => {
              if (v == null || v === 0) return '-';
              const sign = v < 0 ? '-' : '+';
              return sign + Math.round(Math.abs(v)).toLocaleString('ko-KR') + '억';
            };
            const fmtNet = v => v == null ? '-' : (v > 0 ? `+${v.toLocaleString('ko-KR')}명` : `${v.toLocaleString('ko-KR')}명`);
            const etfNoData = !(ei.etf_count > 0 && ei.amt_억);
            const etfRatioNoData = er.diff_1d == null;
            const cards = [
              {
                icon:'👥', title:'고용 트렌드', signal: em.signal, isGrey: !em.signal || em.signal === 'gray',
                label: em.label || '데이터 없음',
                body: (() => {
                  const rows = [];
                  if (em.current_workers != null)
                    rows.push(['현재 인원', em.current_workers.toLocaleString('ko-KR') + '명']);
                  if (em.net_1m != null) {
                    rows.push(['1개월', fmtNet(em.net_1m)]);
                    rows.push(['3개월', fmtNet(em.net_3m)]);
                    rows.push(['6개월', fmtNet(em.net_6m)]);
                  } else if (em.net_1y != null) {
                    rows.push(['1년', fmtNet(em.net_1y)]);
                  }
                  return rows.length ? rows : null;
                })(),
              },
              {
                icon:'🚢', title:'수출/계약', signal: ex.signal, isGrey: !ex.signal || ex.signal === 'gray',
                label: ex.label || '데이터 없음',
                body: (() => {
                  const xp = ex.export;
                  if (!xp && !ex.contracts) return null;
                  const rows = [];
                  if (xp?.trend_desc) rows.push(['수출 추세', xp.trend_desc]);
                  if (xp?.mom_pct != null) rows.push([`MoM (${xp.latest_ym||''})`, (xp.mom_pct>0?'+':'')+xp.mom_pct.toFixed(1)+'%']);
                  if (xp?.shared_stocks?.length) {
                    const names = xp.shared_stocks.slice(0,2).map(s=>s.name);
                    const extra = (xp.shared_hs_cnt||0) > xp.shared_stocks.length ? ` 외 ${(xp.shared_hs_cnt||0)-xp.shared_stocks.length}개사` : '';
                    rows.push(['△ 공동', names.join(', ') + extra]);
                  }
                  rows.push(['계약', ex.contracts ? `${ex.contracts.count}건` : '공시 없음']);
                  return rows.length ? rows : null;
                })(),
              },
              {
                icon:'🏭', title:'섹터 트렌드', signal: st.signal_5d || 'gray', isGrey: !st.signal_5d,
                label: st.label || st.sector_key || '데이터 없음',
                body: (st.chg_5d != null || st.chg_10d != null || st.chg_30d != null) ? [
                  ['5일', st.chg_5d  != null ? (st.chg_5d >0?'+':'')+st.chg_5d .toFixed(1)+'%' : '-'],
                  ['10일', st.chg_10d != null ? (st.chg_10d>0?'+':'')+st.chg_10d.toFixed(1)+'%' : '-'],
                  ['30일', st.chg_30d != null ? (st.chg_30d>0?'+':'')+st.chg_30d.toFixed(1)+'%' : '-'],
                ] : null,
              },
              {
                icon:'🌊', title:'외국인/기관 수급',
                signal: (() => { const f=su.signal_frn_5d,i=su.signal_inst_5d; if(!f)return'gray'; if(f==='green'&&i==='green')return'green'; if(f==='red'&&i==='red')return'red'; return'yellow'; })(),
                isGrey: !su.signal_frn_5d,
                label: (() => { const f=su.signal_frn_5d,i=su.signal_inst_5d; if(!f)return'수급 데이터'; const ft=f==='green'?'외국인↑':f==='red'?'외국인↓':'외국인→'; const it=i==='green'?'기관↑':i==='red'?'기관↓':'기관→'; return`${ft}/${it}`; })(),
                body: su.frn_amt_5d != null ? [
                  ['외국인 5일',fmt억(su.frn_amt_5d)],['외국인 10일',fmt억(su.frn_amt_10d)],['외국인 30일',fmt억(su.frn_amt_30d)],
                  ['기관 5일',fmt억(su.inst_amt_5d)],['기관 10일',fmt억(su.inst_amt_10d)],['기관 30일',fmt억(su.inst_amt_30d)],
                ] : null,
              },
              {
                icon:'📊', title:'ETF 편입', signal: ei.signal, isGrey: etfNoData,
                label: ei.label || '데이터 없음',
                body: ei.etf_count > 0 ? [
                  ['편입규모', ei.amt_억 ? ei.amt_억.toLocaleString('ko-KR')+'억' : '-'],
                  ['시총대비', `${(ei.ratio||0).toFixed(2)}%`],
                ] : [['ETF 편입 없음', '']],
              },
              {
                icon:'📈', title:'ETF 비중 추이', signal: er.signal, isGrey: etfRatioNoData || etfNoData,
                label: er.label || '데이터 없음',
                body: er.diff_1d != null ? [
                  ['전일대비', (er.diff_1d>=0?'+':'')+er.diff_1d.toFixed(3)+'%p '+(er.diff_1d>=0?'증가':'감소')],
                  ['5일전대비', (er.diff_5d>=0?'+':'')+er.diff_5d.toFixed(3)+'%p '+(er.diff_5d>=0?'증가':'감소')],
                ] : null,
              },
            ];
            return (
              <section className="glass-panel" style={{ overflow:'hidden' }}>
                <div style={{ padding:'0.45rem 1rem', borderBottom:'1px solid var(--glass-border)',
                  fontSize:'0.75rem', fontWeight:600, color:'var(--text-secondary)', display:'flex', alignItems:'center', gap:'0.4rem' }}>
                  <span>🔎</span> 추가 시그널
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'repeat(6,1fr)', gap:'0.5rem', padding:'0.5rem 0.75rem 0.75rem' }}>
                  {cards.map((c, ci) => {
                    const borderColor = c.isGrey ? 'rgba(107,114,128,0.3)' : sigColor(c.signal);
                    const bgBase = c.isGrey
                      ? 'rgba(107,114,128,0.06)'
                      : c.signal === 'green' ? 'rgba(34,197,94,0.06)'
                      : c.signal === 'red'   ? 'rgba(239,68,68,0.06)'
                      : c.signal === 'yellow'? 'rgba(251,191,36,0.05)'
                      : 'rgba(107,114,128,0.04)';
                    return (
                      <div key={ci} style={{
                        padding:'0.75rem 0.9rem',
                        borderRadius:'8px',
                        borderTop: `2px solid ${borderColor}`,
                        border: `1px solid ${c.isGrey ? 'rgba(107,114,128,0.15)' : borderColor+'28'}`,
                        borderTopWidth: '2px',
                        background: bgBase,
                        boxShadow: !c.isGrey && c.signal !== 'gray'
                          ? `inset 0 1px 0 ${borderColor}20`
                          : 'none',
                      }}>
                        <div style={{ display:'flex', alignItems:'center', gap:'0.35rem', marginBottom:'0.3rem' }}>
                          <span style={{ fontSize:'0.95rem', lineHeight:1 }}>{c.icon}</span>
                          <span style={{ fontSize:'0.68rem', fontWeight:700,
                            color: c.isGrey ? 'rgba(148,163,184,0.6)' : sigColor(c.signal) }}>
                            {c.title}
                          </span>
                        </div>
                        <p style={{
                          fontSize:'0.72rem',
                          color: sigLabelColor(c.signal, c.isGrey),
                          fontWeight: (!c.isGrey && (c.signal === 'green' || c.signal === 'red')) ? 600 : 400,
                          marginBottom:'0.4rem', lineHeight:1.4,
                        }}>{c.label}</p>
                        {c.body && (
                          <div style={{
                            display:'flex', flexDirection:'column', gap:'0.18rem',
                            opacity: c.isGrey ? 0.45 : 1,
                            padding:'0.3rem 0.4rem',
                            background:'rgba(0,0,0,0.18)',
                            borderRadius:'5px',
                            border:'1px solid rgba(255,255,255,0.05)',
                          }}>
                            {c.body.map(([k,v], bi) => (
                              <div key={bi} style={{ display:'flex', justifyContent:'space-between', fontSize:'0.63rem' }}>
                                <span style={{ color:'rgba(148,163,184,0.7)' }}>{k}</span>
                                <span style={{ color: v?.startsWith('+') ? '#22c55e' : v?.startsWith('-') ? '#ef4444' : 'rgba(255,255,255,0.75)', fontWeight:500 }}>{v}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })()}

          {/* 기간 버튼 */}
          <div style={{ display:'flex', gap:'0.4rem', justifyContent:'flex-end' }}>
            {[{label:'30일',days:30},{label:'180일',days:180},{label:'1년',days:365},{label:'3년',days:1095},{label:'10년',days:3650}].map(({label,days}) => (
              <button key={days} onMouseDown={e => e.preventDefault()} onClick={() => handleChartDaysChange(days)} style={{
                padding:'0.3rem 0.75rem', borderRadius:'6px', fontSize:'0.78rem', cursor:'pointer', fontWeight:600,
                border: chartDays===days ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
                background: chartDays===days ? 'rgba(45,212,191,0.15)' : 'transparent',
                color: chartDays===days ? 'var(--accent-mint)' : 'var(--text-secondary)',
              }}>{label}</button>
            ))}
          </div>

          {/* candle chart */}
          <div className="glass-panel" style={{ padding:'1rem' }}>
            <div style={{ display:'flex', alignItems:'center', gap:'1rem', marginBottom:'0.6rem', flexWrap:'wrap' }}>
              <p style={{ fontSize:'0.8rem', color:'var(--text-secondary)' }}>주가 차트 ({chartDays>=3650?'10년':chartDays>=1095?'3년':chartDays===365?'1년':chartDays+'일'})</p>
              {chartData.length > 0 && (
                <div style={{ display:'flex', gap:'0.8rem', fontSize:'0.68rem' }}>
                  {[['MA5','#facc15'],['MA20','#f97316'],['MA60','#a78bfa']].map(([lb,cl]) => (
                    <span key={lb} style={{ display:'flex', alignItems:'center', gap:'0.25rem' }}>
                      <span style={{ display:'inline-block', width:'16px', height:'2px', background:cl }}/><span style={{ color:'var(--text-secondary)' }}>{lb}</span>
                    </span>
                  ))}
                  {[['양봉','#ef4444'],['음봉','#3b82f6']].map(([lb,cl]) => (
                    <span key={lb} style={{ display:'flex', alignItems:'center', gap:'0.3rem' }}>
                      <span style={{ display:'inline-block', width:'8px', height:'10px', background:cl, borderRadius:'1px' }}/><span style={{ color:'var(--text-secondary)' }}>{lb}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
            {chartData.length === 0 ? (
              <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'260px', gap:'0.6rem' }}>
                {collecting ? <><div style={{width:'22px',height:'22px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',animation:'spin 0.8s linear infinite'}}/><span style={{color:'var(--accent-mint)',fontSize:'0.8rem'}}>주가 수집 중...</span></> : <span style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>주가 데이터 없음</span>}
              </div>
            ) : (() => {
              const mc=(arr,n)=>arr.map((_,i)=>{if(i<n-1)return null;return arr.slice(i-n+1,i+1).reduce((s,d)=>s+(d.close||0),0)/n;});
              const ma5=mc(displayChartData,5),ma20=mc(displayChartData,20),ma60=mc(displayChartData,60);
              const W=900,HC=220,HV=55,PL=58,PR=8,PT=10,PB=20,N=displayChartData.length;
              const minP=Math.min(...displayChartData.map(d=>d.low||d.close||0))*0.998;
              const maxP=Math.max(...displayChartData.map(d=>d.high||d.close||0))*1.002;
              const maxV=Math.max(...displayChartData.map(d=>d.volume||0))||1;
              const xs=(W-PL-PR)/N,xp=i=>PL+(i+0.5)*xs;
              const yp=v=>PT+(1-(v-minP)/(maxP-minP))*(HC-PT-PB);
              const yv=v=>HV-2-(v/maxV)*(HV-4);
              const pt=Array.from({length:4},(_,i)=>minP+(maxP-minP)*i/3);
              const fp=v=>v>=100000?(v/10000).toFixed(0)+"만":Math.round(v).toLocaleString("ko-KR");
              const xt=Array.from({length:5},(_,i)=>Math.floor(i*(N-1)/4));
              const [tip,setTip]=React.useState(null);
              const cw=Math.max(1,xs*0.6);
              return (
                <div style={{position:"relative"}}>
                  <svg viewBox={`0 0 ${W} ${HC+HV+8}`} style={{width:"100%",height:"auto",cursor:"crosshair"}} onMouseLeave={()=>setTip(null)}>
                    {pt.map((v,i)=>(<g key={i}><line x1={PL} x2={W-PR} y1={yp(v)} y2={yp(v)} stroke="rgba(255,255,255,0.05)" strokeWidth="1"/><text x={PL-4} y={yp(v)+4} textAnchor="end" fontSize="9" fill="rgba(100,116,139,0.8)">{fp(v)}</text></g>))}
                    <line x1={PL} x2={W-PR} y1={HC-PB} y2={HC-PB} stroke="rgba(255,255,255,0.08)" strokeWidth="1"/>
                    <line x1={PL} x2={W-PR} y1={HC+2} y2={HC+2} stroke="rgba(255,255,255,0.06)" strokeWidth="1"/>
                    {xt.map(i=>(<text key={i} x={xp(i)} y={HC-2} textAnchor="middle" fontSize="9" fill="rgba(100,116,139,0.8)">{displayChartData[i]?.date?.slice(5).replace("-","/")}</text>))}
                    {displayChartData.map((d,i)=>{const u=(d.close||0)>=(d.open||d.close||0);const vh=HV-2-yv(d.volume||0);return <rect key={i} x={xp(i)-cw/2} y={HC+2+yv(d.volume||0)} width={cw} height={Math.max(1,vh)} fill={u?"rgba(239,68,68,0.35)":"rgba(59,130,246,0.35)"}/>;}) }
                    {displayChartData.map((d,i)=>{const o=d.open||d.close||0,h=d.high||d.close||0,l=d.low||d.close||0,c=d.close||0,u=c>=o,cl=u?"#ef4444":"#3b82f6",bT=yp(Math.max(o,c)),bH=Math.max(1,Math.abs(yp(o)-yp(c))),cx=xp(i);return(<g key={i} onMouseEnter={()=>setTip({i,x:cx,d,ma5:ma5[i],ma20:ma20[i],ma60:ma60[i]})}><line x1={cx} x2={cx} y1={yp(h)} y2={bT} stroke={cl} strokeWidth="1"/><rect x={cx-cw/2} y={bT} width={cw} height={bH} fill={u?cl:"none"} stroke={cl} strokeWidth="1"/><line x1={cx} x2={cx} y1={bT+bH} y2={yp(l)} stroke={cl} strokeWidth="1"/></g>);}) }
                    {(()=>{let p="";displayChartData.forEach((d,i)=>{if(d.close!=null)p+=p===''?`M${xp(i)},${yp(d.close)}`:`L${xp(i)},${yp(d.close)}`;});return <path d={p} fill="none" stroke="var(--accent-mint)" strokeWidth="1.5" opacity="0.7"/>;})()}
                    {[{ma:ma5,cl:"#facc15",w:1.2,da:"4 3"},{ma:ma20,cl:"#f97316",w:1.5,da:"5 3"},{ma:ma60,cl:"#a78bfa",w:1.5,da:"6 3"}].map(({ma,cl,w,da},li)=>{let p="";ma.forEach((v,i)=>{if(v!=null)p+=ma[i-1]!=null?` L${xp(i)},${yp(v)}`:`M${xp(i)},${yp(v)}`;});return <path key={li} d={p} fill="none" stroke={cl} strokeWidth={w} strokeDasharray={da}/>;}) }
                    {tip&&<line x1={tip.x} x2={tip.x} y1={PT} y2={HC-PB} stroke="rgba(255,255,255,0.2)" strokeWidth="1" strokeDasharray="4 2"/>}
                  </svg>
                  {tip&&(()=>{const d=tip.d,u=(d.close||0)>=(d.open||d.close||0),chg=d.open?((d.close-d.open)/d.open*100):0;return(<div style={{position:"absolute",top:8,left:tip.x>W*0.6?"5%":"55%",background:"rgba(10,10,20,0.97)",border:"1px solid rgba(255,255,255,0.12)",borderRadius:"8px",padding:"0.6rem 0.8rem",fontSize:"0.72rem",lineHeight:1.8,minWidth:"140px",pointerEvents:"none"}}><div style={{fontWeight:700,color:"var(--text-primary)",marginBottom:"0.2rem"}}>{d.date}</div>{[["시가",d.open],["고가",d.high],["저가",d.low],["종가",d.close]].map(([lb,v])=>(<div key={lb} style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:"var(--text-secondary)"}}>{lb}</span><span style={{color:u?"#ef4444":"#3b82f6",fontWeight:600}}>{Math.round(v||0).toLocaleString("ko-KR")}</span></div>))}<div style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:"var(--text-secondary)"}}>{"등락"}</span><span style={{color:u?"#ef4444":"#3b82f6",fontWeight:600}}>{chg>=0?"+":""}{chg.toFixed(1)}%</span></div><div style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:"var(--text-secondary)"}}>{"거래량"}</span><span style={{color:"rgba(255,255,255,0.8)"}}>{Math.round(d.volume||0).toLocaleString("ko-KR")}</span></div><div style={{borderTop:"1px solid rgba(255,255,255,0.08)",marginTop:"0.3rem",paddingTop:"0.3rem"}}>{[["MA5",tip.ma5,"#facc15"],["MA20",tip.ma20,"#f97316"],["MA60",tip.ma60,"#a78bfa"]].map(([lb,v,cl])=>v!=null&&(<div key={lb} style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:cl}}>{lb}</span><span style={{color:cl}}>{Math.round(v).toLocaleString("ko-KR")}</span></div>))}</div></div>);})()}
                </div>
              );
            })()}
          </div>

          {/* 수급 차트 */}
          {(() => {
            const [showInstBar, setShowInstBar] = React.useState(false);
            const [showFrnBar,  setShowFrnBar]  = React.useState(false);
            const supLabel = chartDays>=3650?'10년':chartDays>=1095?'3년':chartDays===365?'1년':chartDays+'일';
            const btnStyle = (on, color) => ({
              padding:'0.15rem 0.55rem', borderRadius:'5px', fontSize:'0.68rem', cursor:'pointer', fontWeight:600,
              border: `1px solid ${on ? color : 'rgba(255,255,255,0.12)'}`,
              background: on ? `${color}22` : 'transparent',
              color: on ? color : 'rgba(255,255,255,0.4)',
            });
            return (
          <div className="glass-panel" style={{ padding:'1rem' }}>
            <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.5rem', flexWrap:'wrap' }}>
              <p style={{ fontSize:'0.8rem', color:'var(--text-secondary)', marginRight:'0.3rem' }}>순매수 ({supLabel})</p>
              {/* 누적선 범례 (항상 표시) */}
              <span style={{ fontSize:'0.68rem', color:'#fca5a5' }}>- - 기관누적</span>
              <span style={{ fontSize:'0.68rem', color:'#dc2626' }}>— 외국인누적</span>
              {/* 바차트 토글 버튼 */}
              <div style={{ marginLeft:'auto', display:'flex', gap:'0.3rem' }}>
                <button style={btnStyle(showInstBar,'#fca5a5')} onMouseDown={e=>e.preventDefault()} onClick={()=>setShowInstBar(v=>!v)}>
                  기관 일별 {showInstBar?'숨기기':'표시'}
                </button>
                <button style={btnStyle(showFrnBar,'#dc2626')} onMouseDown={e=>e.preventDefault()} onClick={()=>setShowFrnBar(v=>!v)}>
                  외국인 일별 {showFrnBar?'숨기기':'표시'}
                </button>
              </div>
            </div>
            {chartData.length===0 ? (
              <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'130px', gap:'0.6rem' }}>
                {collecting
                  ? <><div style={{width:'22px',height:'22px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',animation:'spin 0.8s linear infinite'}}/><span style={{color:'var(--accent-mint)',fontSize:'0.8rem'}}>수급 수집 중...</span></>
                  : <span style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>수급 데이터 없음 — 조회 시 KIS API로 자동 수집됩니다</span>}
              </div>
            ) : (() => {
              let ci=0, cf=0;
              const d2 = displayChartData.map(d => { ci+=d.inst_net_buy||0; cf+=d.frn_net_buy||0; return {...d,inst_cum:ci,frn_cum:cf}; });
              // 수급 데이터 존재 여부 (모두 0이면 KIS 미수집 상태)
              const hasSupplyData = d2.some(d => d.inst_net_buy !== 0 || d.frn_net_buy !== 0);
              return (
                <>
                {!hasSupplyData && (
                  <div style={{textAlign:'center',padding:'0.4rem',fontSize:'0.72rem',color:'rgba(100,116,139,0.8)',background:'rgba(251,191,36,0.05)',borderRadius:'6px',marginBottom:'0.4rem',border:'1px solid rgba(251,191,36,0.15)'}}>
                    ⚠ 기관/외국인 수급 데이터 없음 — KIS API 수집 대기 중 (자정 배치 또는 data_collector.py 실행 필요)
                  </div>
                )}
                <ResponsiveContainer width="100%" height={180}>
                  <ComposedChart data={d2} margin={{top:5,right:5,bottom:0,left:0}} barCategoryGap="20%">
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="date" tick={{fontSize:11,fill:'#94a3b8',fontWeight:600}} tickLine={false} interval="preserveStartEnd"
                      tickFormatter={d => d ? d.slice(5).replace('-','/') : ''} />
                    <YAxis yAxisId="bar" domain={[dataMin => Math.min(0, dataMin), dataMax => Math.max(0, dataMax)]} tick={{fontSize:9,fill:'#64748b'}} tickLine={false} axisLine={false} width={62}
                      tickFormatter={v=>{const a=Math.abs(v),s=v<0?'-':'';if(a>=10000)return s+(a/10000).toFixed(1)+'만주';if(a>=1000)return s+(a/1000).toFixed(0)+'천주';return s+a.toLocaleString('ko-KR')+'주';}}/>
                    <YAxis yAxisId="line" orientation="right" domain={[dataMin => Math.min(0, dataMin), dataMax => Math.max(0, dataMax)]} tick={{fontSize:9,fill:'#64748b'}} tickLine={false} axisLine={false} width={62}
                      tickFormatter={v=>{const a=Math.abs(v),s=v<0?'-':'';if(a>=10000)return s+(a/10000).toFixed(1)+'만';if(a>=1000)return s+(a/1000).toFixed(0)+'천';return s+a.toLocaleString('ko-KR');}}/>
                    <ReferenceLine yAxisId="bar" y={0} stroke="rgba(255,255,255,0.45)" strokeWidth={1.5} strokeDasharray="4 2"/>
                    <ReferenceLine yAxisId="line" y={0} stroke="rgba(255,255,255,0.15)" strokeWidth={1}/>
                    <Tooltip contentStyle={{background:'rgba(15,15,25,0.95)',border:'1px solid rgba(255,255,255,0.12)',borderRadius:'8px',fontSize:'0.78rem'}}
                      formatter={(v,n)=>{const a=Math.abs(v),s=v>=0?'+':'-';return[a>=10000?s+(a/10000).toFixed(1)+'만주':s+a.toLocaleString('ko-KR')+'주',n];}}/>
                    {showInstBar && <Bar yAxisId="bar" dataKey="inst_net_buy" name="기관(일)" barSize={chartDays<=30?10:chartDays<=180?4:chartDays<=365?2:1}>
                      {d2.map((e,i)=><Cell key={i} fill={e.inst_net_buy>=0?'#fca5a5':'#93c5fd'} fillOpacity={0.9}/>)}
                    </Bar>}
                    {showFrnBar && <Bar yAxisId="bar" dataKey="frn_net_buy" name="외국인(일)" barSize={chartDays<=30?10:chartDays<=180?4:chartDays<=365?2:1}>
                      {d2.map((e,i)=><Cell key={i} fill={e.frn_net_buy>=0?'#dc2626':'#1d4ed8'} fillOpacity={0.9}/>)}
                    </Bar>}
                    <Line yAxisId="line" type="monotone" dataKey="inst_cum" name="기관(누적)" stroke="#fca5a5" dot={false} strokeWidth={1.5} strokeDasharray="5 3"/>
                    <Line yAxisId="line" type="monotone" dataKey="frn_cum" name="외국인(누적)" stroke="#dc2626" dot={false} strokeWidth={2}/>
                  </ComposedChart>
                </ResponsiveContainer>
                </>
              );
            })()}
          </div>
            );
          })()}
        </div>

        {/* ── DART 공시 정보 ────────────────────────────────────── */}
        {/^\d{6}$/.test(selectedStock) && (
          <section className="glass-panel">
            <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)',
              display:'flex', alignItems:'center', justifyContent:'space-between' }}>
              <span style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--accent-yellow, #facc15)' }}>
                📢 최근 공시 (최근 1년 / 최대 100건)
              </span>
              <div style={{ display:'flex', alignItems:'center', gap:'0.5rem' }}>
                {disclosureLoading && (
                  <span style={{ width:'10px', height:'10px', borderRadius:'50%',
                    border:'2px solid var(--accent-yellow, #facc15)',
                    borderTopColor:'transparent', display:'inline-block',
                    animation:'spin 0.8s linear infinite' }} />
                )}
                <button onClick={fetchDisclosures}
                  style={{ padding:'0.15rem 0.5rem', borderRadius:'4px', fontSize:'0.7rem',
                    background:'rgba(250,204,21,0.1)', border:'1px solid rgba(250,204,21,0.3)',
                    color:'#facc15', cursor:'pointer' }}>
                  새로고침
                </button>
              </div>
            </div>
            {disclosures.length === 0 ? (
              <div style={{ padding:'1.2rem', textAlign:'center', fontSize:'0.82rem',
                color:'var(--text-secondary)' }}>
                {disclosureLoading
                  ? 'DART 공시 조회 중...'
                  : '최근 1년 내 공시가 없습니다'}
              </div>
            ) : (() => {
              const visible = showAllDisclosures
                ? disclosures
                : disclosures.slice(0, DISCLOSURE_PREVIEW);
              const hasMore = disclosures.length > DISCLOSURE_PREVIEW;
              return (
                <div style={{ display:'flex', flexDirection:'column' }}>
                  {visible.map((d, i) => (
                    <div key={d.rcept_no || i}
                      style={{ display:'flex', alignItems:'flex-start', gap:'0.75rem',
                        padding:'0.55rem 1rem',
                        borderBottom: '1px solid rgba(255,255,255,0.05)',
                        background: i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent' }}>
                      {/* 날짜 */}
                      <span style={{ fontSize:'0.72rem', color:'var(--text-secondary)',
                        whiteSpace:'nowrap', flexShrink:0, marginTop:'0.1rem' }}>
                        {d.rcept_dt}
                      </span>
                      {/* 보고서명 */}
                      <div style={{ flex:1, minWidth:0 }}>
                        {d.dart_url ? (
                          <a href={d.dart_url} target="_blank" rel="noopener noreferrer"
                            style={{ fontSize:'0.82rem', color:'var(--text-primary)',
                              textDecoration:'none', display:'block',
                              overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}
                            title={d.report_nm}>
                            {d.report_nm}
                          </a>
                        ) : (
                          <span style={{ fontSize:'0.82rem',
                            overflow:'hidden', textOverflow:'ellipsis', display:'block',
                            whiteSpace:'nowrap' }}>
                            {d.report_nm}
                          </span>
                        )}
                        {d.flr_nm && d.flr_nm !== d.corp_name && (
                          <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)' }}>
                            제출: {d.flr_nm}
                          </span>
                        )}
                      </div>
                      {/* 원문 링크 */}
                      {d.dart_url && (
                        <a href={d.dart_url} target="_blank" rel="noopener noreferrer"
                          style={{ padding:'0.2rem 0.5rem', borderRadius:'4px', fontSize:'0.68rem',
                            background:'rgba(250,204,21,0.1)', border:'1px solid rgba(250,204,21,0.25)',
                            color:'#facc15', textDecoration:'none', whiteSpace:'nowrap',
                            flexShrink:0, alignSelf:'center' }}>
                          원문
                        </a>
                      )}
                    </div>
                  ))}
                  {/* 더 보기 / 접기 버튼 */}
                  {hasMore && (
                    <button onClick={() => setShowAllDisclosures(v => !v)}
                      style={{ width:'100%', padding:'0.55rem', border:'none',
                        borderTop:'1px solid rgba(255,255,255,0.05)',
                        background:'rgba(255,255,255,0.02)',
                        color:'var(--text-secondary)', fontSize:'0.78rem',
                        cursor:'pointer', display:'flex', alignItems:'center',
                        justifyContent:'center', gap:'0.3rem' }}>
                      {showAllDisclosures
                        ? `▲ 접기`
                        : `▼ 더 보기 (${disclosures.length - DISCLOSURE_PREVIEW}건 더)`}
                    </button>
                  )}
                </div>
              );
            })()}
          </section>
        )}

        {/* ── KRX 공지사항 (종목기본정보 변동) ─────────────────────── */}
        {/^\d{6}$/.test(selectedStock) && notices.length > 0 && (
          <section className="glass-panel">
            <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)',
              display:'flex', alignItems:'center', justifyContent:'space-between' }}>
              <span style={{ fontSize:'0.8rem', fontWeight:600, color:'#60a5fa' }}>
                🔔 종목 공지사항 (KRX 변동 — 최근 {notices.length}건)
              </span>
              <span style={{ fontSize:'0.66rem', color:'var(--text-secondary)' }}>
                상장주식수·소속부·종목명 변경 자동 추적
              </span>
            </div>
            <div style={{ maxHeight:'200px', overflowY:'auto' }}>
              {notices.map((n, i) => (
                <div key={i} style={{ padding:'0.45rem 1rem',
                  borderBottom: i < notices.length-1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                  display:'flex', gap:'0.75rem', alignItems:'flex-start' }}>
                  <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)', whiteSpace:'nowrap', flexShrink:0 }}>
                    {n.change_date || n.date || ''}
                  </span>
                  <span style={{ fontSize:'0.78rem' }}>{n.change_type || n.type || ''}</span>
                  <span style={{ fontSize:'0.78rem', color:'var(--text-secondary)', flex:1 }}>
                    {n.old_value && n.new_value ? `${n.old_value} → ${n.new_value}` : (n.description || '')}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── 대주주 / 임원 지분변동 ────────────────────────────────── */}
        {/^\d{6}$/.test(selectedStock) && (majorHolders.current_holders.length > 0 || insiderHist.length > 0) && (
          <section className="glass-panel">
            <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)' }}>
              <span style={{ fontSize:'0.8rem', fontWeight:600, color:'#a78bfa' }}>
                👤 대주주 · 임원 지분변동
              </span>
            </div>
            {majorHolders.current_holders.length > 0 && (
              <div style={{ padding:'0.5rem 1rem 0.25rem' }}>
                <p style={{ fontSize:'0.7rem', color:'var(--text-secondary)', marginBottom:'0.35rem' }}>현재 주요주주</p>
                <div style={{ display:'flex', flexWrap:'wrap', gap:'0.4rem' }}>
                  {majorHolders.current_holders.slice(0, 8).map((h, i) => (
                    <span key={i} style={{ fontSize:'0.73rem', padding:'0.15rem 0.5rem',
                      background:'rgba(167,139,250,0.12)', borderRadius:'4px',
                      border:'1px solid rgba(167,139,250,0.25)' }}>
                      {h.holder_name} {h.hold_ratio != null ? `${Number(h.hold_ratio).toFixed(1)}%` : ''}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {insiderHist.length > 0 && (
              <div style={{ maxHeight:'160px', overflowY:'auto', padding:'0.25rem 0' }}>
                {insiderHist.map((r, i) => (
                  <div key={i} style={{ padding:'0.35rem 1rem',
                    borderTop: i === 0 && majorHolders.current_holders.length > 0
                      ? '1px solid rgba(255,255,255,0.05)' : 'none',
                    display:'flex', gap:'0.6rem', fontSize:'0.75rem', alignItems:'center' }}>
                    <span style={{ color:'var(--text-secondary)', whiteSpace:'nowrap', width:'80px', flexShrink:0 }}>
                      {r.report_date || r.date || ''}
                    </span>
                    <span style={{ fontWeight:600, whiteSpace:'nowrap' }}>{r.holder_name || r.name}</span>
                    <span style={{ color:'var(--text-secondary)' }}>{r.relation || ''}</span>
                    <span style={{ marginLeft:'auto', color: (r.change_qty||0) > 0 ? '#ef4444' : '#3b82f6',
                      whiteSpace:'nowrap' }}>
                      {(r.change_qty||0) > 0 ? '▲' : '▼'} {Math.abs(r.change_qty||0).toLocaleString()}주
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

                {/* 연결/별도 탭 */}
        <div style={{ display:'flex', gap:'0.5rem', marginBottom:'0.5rem', alignItems:'center' }}>
          {['CFS', 'OFS'].map(rt => (
            <button
              key={rt}
              onClick={() => setReportType(rt)}
              style={{
                padding:'0.3rem 0.9rem', borderRadius:'6px', border:'none', cursor:'pointer',
                fontSize:'0.75rem', fontWeight:600,
                background: reportType === rt ? 'var(--accent-mint)' : 'rgba(255,255,255,0.06)',
                color: reportType === rt ? '#0a0a14' : 'var(--text-secondary)',
                transition:'all 0.15s',
              }}
            >
              {rt === 'CFS' ? '연결' : '별도'}
            </button>
          ))}
          <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)', marginLeft:'0.3rem' }}>
            {reportType === 'CFS' ? '연결재무제표 기준' : '별도(개별)재무제표 기준'}
          </span>
        </div>

                {/* 연간 재무 테이블 */}
        <section className="glass-panel" style={{ overflow:'clip' }}>
          <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)' }}>
            <span style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--accent-mint)' }}>연간 실적</span>
          </div>
          {finTable.length === 0 ? (
            <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem' }}>
              {collecting
                ? <span style={{display:'inline-flex',alignItems:'center',gap:'0.5rem',color:'var(--accent-mint)'}}>
                    <span style={{width:'12px',height:'12px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',display:'inline-block',animation:'spin 0.8s linear infinite'}}/>
                    DART에서 재무제표 수집 중...
                  </span>
                : <span style={{color:'var(--text-secondary)'}}>
                    {reportType === 'OFS' ? '별도재무제표 없음 — 이 종목은 별도(개별) 재무제표 데이터가 없습니다' : '연간 재무데이터 없음 — 매일 자정 DART 공시 기준으로 자동 업데이트됩니다'}
                  </span>}
            </div>
          ) : (
            <table className="premium-table" style={{ width:'100%' }}>
              <thead><tr>
                <th style={{ minWidth:'90px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>기간</th>
                {finTable.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
              </tr></thead>
              <tbody>{tableRows.map(row => (
                <tr key={row.key}>
                  <td style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>{row.label}</td>
                  {finTable.map((t,i) => <td key={i} style={{ textAlign:'right', color:numColor(t[row.key]), whiteSpace:'nowrap' }}>{row.fmt(t[row.key])}</td>)}
                </tr>
              ))}</tbody>
            </table>
          )}
        </section>

        {/* 분기 재무 테이블 */}
        <section className="glass-panel" style={{ overflow:'clip' }}>
          <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)' }}>
            <span style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--accent-purple)' }}>분기 실적</span>
          </div>
          {quarterTable.length === 0 ? (
            <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem' }}>
              {collecting
                ? <span style={{display:'inline-flex',alignItems:'center',gap:'0.5rem',color:'var(--accent-mint)'}}>
                    <span style={{width:'12px',height:'12px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',display:'inline-block',animation:'spin 0.8s linear infinite'}}/>
                    분기 실적 수집 중...
                  </span>
                : <span style={{color:'var(--text-secondary)'}}>분기 재무데이터 없음 — 매일 자정 DART 공시 기준으로 자동 업데이트됩니다</span>}
            </div>
          ) : (
            <table className="premium-table" style={{ width:'100%' }}>
              <thead><tr>
                <th style={{ minWidth:'90px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>기간</th>
                {quarterTable.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
              </tr></thead>
              <tbody>{tableRows.map(row => (
                <tr key={row.key}>
                  <td style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>{row.label}</td>
                  {quarterTable.map((t,i) => <td key={i} style={{ textAlign:'right', color:numColor(t[row.key]), whiteSpace:'nowrap' }}>{row.fmt(t[row.key])}</td>)}
                </tr>
              ))}</tbody>
            </table>
          )}
        </section>

        {/* ── 컨센서스 (목표주가) ── */}
        {/^\d{6}$/.test(selectedStock) && consensus && consensus.records?.length > 0 && (() => {
          const allRecords = consensus.records || [];
          const fmtPrice = v => v ? Math.round(v).toLocaleString('ko-KR') + '원' : '-';
          const opinionColor = op => {
            const l = (op || '').toLowerCase();
            if (['매수','buy','strong buy','적극매수','강력매수'].includes(l)) return '#22c55e';
            if (['매도','sell','underperform','비중축소'].includes(l)) return '#ef4444';
            return '#94a3b8';
          };
          const isBuy  = op => ['매수','buy','strong buy','적극매수','강력매수'].includes((op||'').toLowerCase());
          const isSell = op => ['매도','sell','underperform','비중축소'].includes((op||'').toLowerCase());
          const chgPct = (cur, prev) => {
            if (!cur || !prev || prev === 0) return null;
            return ((cur - prev) / prev * 100).toFixed(1);
          };

          // 기간 필터링
          const cutoff = new Date();
          cutoff.setMonth(cutoff.getMonth() - consensusMonths);
          const records = allRecords.filter(r => !r.report_date || new Date(r.report_date) >= cutoff);

          // 필터된 레코드 기준으로 요약 재계산
          const targets = records.filter(r => r.target_price > 0).map(r => r.target_price);
          const avgTarget = targets.length ? targets.reduce((a, b) => a + b, 0) / targets.length : null;
          const maxTarget = targets.length ? Math.max(...targets) : null;
          const minTarget = targets.length ? Math.min(...targets) : null;
          const buyCnt  = records.filter(r => isBuy(r.opinion)).length;
          const holdCnt = records.filter(r => r.opinion && !isBuy(r.opinion) && !isSell(r.opinion)).length;
          const sellCnt = records.filter(r => isSell(r.opinion)).length;

          const displayedRecords = consensusExpanded ? records : records.slice(0, 6);
          return (
            <section className="glass-panel" style={{ overflow:'clip' }}>
              {/* 헤더: 타이틀 | 요약 통계 | 기간 탭 */}
              <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)',
                display:'flex', alignItems:'center', flexWrap:'wrap', gap:'0.5rem' }}>
                <span style={{ fontSize:'0.8rem', fontWeight:600, color:'#fbbf24' }}>🎯 컨센서스 (목표주가)</span>
                {/* 요약 통계 — 목표주가 옆 */}
                <div style={{ display:'flex', gap:'0.8rem', fontSize:'0.75rem', flexWrap:'wrap', marginLeft:'0.5rem' }}>
                  <span>평균 <b style={{ color:'#fbbf24' }}>{fmtPrice(avgTarget)}</b></span>
                  <span style={{ color:'var(--text-secondary)' }}>최고 <b style={{ color:'#2dd4bf' }}>{fmtPrice(maxTarget)}</b></span>
                  <span style={{ color:'var(--text-secondary)' }}>최저 <b style={{ color:'#f87171' }}>{fmtPrice(minTarget)}</b></span>
                  {(buyCnt + holdCnt + sellCnt) > 0 && (
                    <span style={{ color:'var(--text-secondary)', fontSize:'0.72rem' }}>
                      <span style={{ color:'#22c55e' }}>매수{buyCnt}</span>
                      {' / '}
                      <span style={{ color:'#94a3b8' }}>중립{holdCnt}</span>
                      {sellCnt > 0 && <><span> / </span><span style={{ color:'#ef4444' }}>매도{sellCnt}</span></>}
                    </span>
                  )}
                </div>
                {/* 기간 탭 — 오른쪽 끝 */}
                <div style={{ display:'flex', background:'rgba(255,255,255,0.05)', borderRadius:'6px', padding:'2px', gap:'1px', marginLeft:'auto' }}>
                  <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)', padding:'0.2rem 0.4rem', alignSelf:'center' }}>{records.length}건</span>
                  {[6,12,24].map(m => (
                    <button key={m} onMouseDown={e => e.preventDefault()} onClick={() => setConsensusMonths(m)}
                      style={{ padding:'0.2rem 0.55rem', borderRadius:'4px', fontSize:'0.7rem', fontWeight:600,
                        border:'none', cursor:'pointer', transition:'all 0.15s',
                        background: consensusMonths === m ? 'rgba(251,191,36,0.25)' : 'transparent',
                        color: consensusMonths === m ? '#fbbf24' : 'var(--text-secondary)' }}>
                      {m}개월
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ overflowX:'auto' }}>
                <table className="premium-table" style={{ width:'100%', minWidth:'640px' }}>
                  <thead><tr>
                    <th style={{ textAlign:'left', minWidth:'80px' }}>날짜</th>
                    <th style={{ textAlign:'left', minWidth:'90px' }}>증권사</th>
                    <th style={{ textAlign:'left', minWidth:'60px' }}>애널리스트</th>
                    <th style={{ textAlign:'center', minWidth:'55px' }}>의견</th>
                    <th style={{ textAlign:'right', minWidth:'90px' }}>목표주가</th>
                    <th style={{ textAlign:'right', minWidth:'80px' }}>직전대비</th>
                    <th style={{ textAlign:'left' }}>리포트</th>
                  </tr></thead>
                  <tbody>
                    {displayedRecords.map((r, i) => {
                      const pct = chgPct(r.target_price, r.prev_target_price);
                      return (
                        <tr key={r.id || i}>
                          <td style={{ color:'var(--text-secondary)', fontSize:'0.78rem' }}>{r.report_date}</td>
                          <td style={{ fontWeight:600, fontSize:'0.8rem' }}>{r.securities_firm}</td>
                          <td style={{ color:'var(--text-secondary)', fontSize:'0.75rem' }}>{r.analyst || '-'}</td>
                          <td style={{ textAlign:'center' }}>
                            <span style={{ fontSize:'0.72rem', fontWeight:700, color: opinionColor(r.opinion) }}>
                              {r.opinion || '-'}
                            </span>
                          </td>
                          <td style={{ textAlign:'right', fontWeight:700, color:'#fbbf24', fontSize:'0.82rem' }}>
                            {fmtPrice(r.target_price)}
                          </td>
                          <td style={{ textAlign:'right', fontSize:'0.75rem',
                            color: pct == null ? 'var(--text-secondary)' : parseFloat(pct) > 0 ? '#2dd4bf' : parseFloat(pct) < 0 ? '#f87171' : 'var(--text-secondary)' }}>
                            {pct == null ? '-' : `${parseFloat(pct) > 0 ? '+' : ''}${pct}%`}
                          </td>
                          <td style={{ maxWidth:'240px', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
                            fontSize:'0.72rem', color:'rgba(255,255,255,0.55)' }}>
                            {r.report_idx ? (
                              <a href={`https://consensus.hankyung.com/analysis/view/${r.report_idx}`}
                                target="_blank" rel="noopener noreferrer"
                                style={{ color:'#818cf8', textDecoration:'none' }}>
                                {(r.report_title || '').replace(/\(\d{6}\)\s*/g, '').slice(0, 50)}
                              </a>
                            ) : (
                              <span>{(r.report_title || '-').slice(0, 50)}</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {/* 전체 보기 토글 */}
              {records.length > 6 && (
                <div style={{ textAlign:'center', padding:'0.5rem', borderTop:'1px solid var(--glass-border)' }}>
                  <button onClick={() => setConsensusExpanded(p => !p)}
                    style={{ fontSize:'0.75rem', padding:'0.3rem 1.2rem', borderRadius:'6px', cursor:'pointer',
                      background:'rgba(255,255,255,0.05)', border:'1px solid var(--glass-border)',
                      color:'var(--text-secondary)', fontWeight:600 }}>
                    {consensusExpanded ? `▲ 접기` : `▼ 전체 보기 (${records.length}건)`}
                  </button>
                </div>
              )}
            </section>
          );
        })()}

        {/* ── 현금흐름표 ── */}
        {(() => {
          const cfRows = [
            { key:'operating_cf', label:'영업활동현금흐름', hint:'영업에서 창출한 현금' },
            { key:'investing_cf', label:'투자활동현금흐름', hint:'설비·투자에 사용한 현금' },
            { key:'financing_cf', label:'재무활동현금흐름', hint:'차입·배당 등 재무활동' },
            { key:'capex',        label:'설비투자(CapEx)',  hint:'유형자산 취득(절대값)' },
            { key:'free_cf',      label:'잉여현금흐름(FCF)',hint:'영업CF - CapEx' },
            { key:'cash_end',     label:'기말현금',         hint:'기말 현금및현금성자산' },
            { key:'depreciation', label:'감가상각비',       hint:'비현금 비용' },
          ];
          const cfColor = (key, val) => {
            if (val == null) return 'rgba(255,255,255,0.5)';
            if (key === 'investing_cf' || key === 'financing_cf') return 'rgba(255,255,255,0.7)';
            if (key === 'capex') return val > 0 ? '#fbbf24' : 'rgba(255,255,255,0.5)';
            return val > 0 ? '#2dd4bf' : '#f87171';
          };
          const fmtCf = v => v == null ? '-' : (v >= 0 ? '+' : '') + v.toLocaleString() + '억';

          return (
            <>
              {/* 연간 현금흐름표 */}
              <section className="glass-panel" style={{ overflow:'clip' }}>
                <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.6rem' }}>
                  <span style={{ fontSize:'0.8rem', fontWeight:600, color:'#34d399' }}>연간 현금흐름표</span>
                  <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)' }}>(억원)</span>
                </div>
                {cfAnnual.length === 0 ? (
                  <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem', color:'var(--text-secondary)' }}>
                    연간 현금흐름 데이터 없음 — DART 미공시이거나 수집 전입니다
                  </div>
                ) : (
                  <table className="premium-table" style={{ width:'100%' }}>
                    <thead><tr>
                      <th style={{ minWidth:'110px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>항목</th>
                      {cfAnnual.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
                    </tr></thead>
                    <tbody>{cfRows.map(row => (
                      <tr key={row.key}>
                        <td title={row.hint} style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)', cursor:'help' }}>{row.label}</td>
                        {cfAnnual.map((t,i) => (
                          <td key={i} style={{ textAlign:'right', color:cfColor(row.key, t[row.key]), whiteSpace:'nowrap' }}>
                            {fmtCf(t[row.key])}
                          </td>
                        ))}
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </section>

              {/* 분기 현금흐름표 */}
              <section className="glass-panel" style={{ overflow:'clip' }}>
                <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.6rem' }}>
                  <span style={{ fontSize:'0.8rem', fontWeight:600, color:'#a78bfa' }}>분기 현금흐름표</span>
                  <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)' }}>(억원)</span>
                </div>
                {cfQuarter.length === 0 ? (
                  <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem', color:'var(--text-secondary)' }}>
                    분기 현금흐름 데이터 없음 — DART 분기보고서 미공시이거나 수집 전입니다
                  </div>
                ) : (
                  <table className="premium-table" style={{ width:'100%' }}>
                    <thead><tr>
                      <th style={{ minWidth:'110px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>항목</th>
                      {cfQuarter.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
                    </tr></thead>
                    <tbody>{cfRows.map(row => (
                      <tr key={row.key}>
                        <td title={row.hint} style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)', cursor:'help' }}>{row.label}</td>
                        {cfQuarter.map((t,i) => (
                          <td key={i} style={{ textAlign:'right', color:cfColor(row.key, t[row.key]), whiteSpace:'nowrap' }}>
                            {fmtCf(t[row.key])}
                          </td>
                        ))}
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </section>
            </>
          );
        })()}

        {/* 종목 보고서 */}
        <div className="glass-panel" style={{padding:'1.2rem'}}>
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:'0.8rem'}}>
            <h3 style={{fontSize:'0.9rem',fontWeight:700,
              color:'var(--accent-mint)',display:'flex',alignItems:'center',gap:'0.5rem',margin:0}}>
              📄 애널리스트 보고서
              {stockReports.length > 0 && (
                <span style={{fontSize:'0.75rem',fontWeight:400,color:'rgba(255,255,255,0.5)'}}>
                  ({stockReports.length}건)
                </span>
              )}
            </h3>
            <button onClick={()=>setActiveTab('reports')}
              style={{padding:'0.2rem 0.6rem',borderRadius:'5px',fontSize:'0.7rem',cursor:'pointer',
                border:'1px solid var(--glass-border)',background:'transparent',
                color:'var(--text-secondary)'}}>
              섹터 보고서 전체 →
            </button>
          </div>
          {stockReports.length === 0 ? (
            <div style={{padding:'1rem 0',textAlign:'center',color:'var(--text-secondary)',fontSize:'0.82rem'}}>
              <p>이 종목의 수집된 보고서가 없습니다.</p>
              <p style={{fontSize:'0.72rem',marginTop:'0.3rem',opacity:0.6}}>
                텔레그램 채널에서 종목명이 포함된 보고서가 수집되면 여기에 표시됩니다.
              </p>
            </div>
          ) : (() => {
            const REPORT_LIMIT = 7;
            const displayedReports = reportsExpanded ? stockReports : stockReports.slice(0, REPORT_LIMIT);
            return (
              <>
                <div style={{display:'flex',flexDirection:'column',gap:'0.35rem'}}>
                  {displayedReports.map(r => (
                    <div key={r.id} style={{display:'flex',alignItems:'center',
                      justifyContent:'space-between',padding:'0.5rem 0.75rem',
                      borderRadius:'6px',background:'rgba(255,255,255,0.04)',
                      border:'1px solid var(--glass-border)'}}>
                      <div style={{flex:1,minWidth:0}}>
                        <p style={{fontSize:'0.82rem',fontWeight:600,
                          overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                          {r.file_name}
                        </p>
                        <p style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginTop:'0.1rem'}}>
                          {r.report_date || r.posted_date} | {r.channel_id}
                          {r.file_size ? ` | ${(r.file_size/1024).toFixed(0)}KB` : ''}
                        </p>
                        {r.caption && (
                          <p style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.35)',marginTop:'0.1rem',
                            overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                            {r.caption}
                          </p>
                        )}
                      </div>
                      <a href={API(`/api/reports/download/${r.id}`)} download={r.saved_name}
                        style={{marginLeft:'0.75rem',padding:'0.3rem 0.7rem',borderRadius:'5px',
                          background:'rgba(45,212,191,0.15)',border:'1px solid rgba(45,212,191,0.3)',
                          color:'var(--accent-mint)',fontSize:'0.75rem',textDecoration:'none',
                          whiteSpace:'nowrap',flexShrink:0}}>
                        ⬇ 다운로드
                      </a>
                    </div>
                  ))}
                </div>
                {stockReports.length > REPORT_LIMIT && (
                  <div style={{textAlign:'center',paddingTop:'0.6rem',borderTop:'1px solid var(--glass-border)',marginTop:'0.35rem'}}>
                    <button onClick={() => setReportsExpanded(p => !p)}
                      style={{fontSize:'0.75rem',padding:'0.3rem 1.2rem',borderRadius:'6px',cursor:'pointer',
                        background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',
                        color:'var(--text-secondary)',fontWeight:600}}>
                      {reportsExpanded ? '▲ 접기' : `▼ 전체 보기 (${stockReports.length}건)`}
                    </button>
                  </div>
                )}
              </>
            );
          })()}
        </div>

      </div>
    );
  };

  // ── 스크리너 ─────────────────────────────────────────────────
  const Screener = () => {
    const [screenTab, setScreenTab] = React.useState('combo');  // 기본: AI 적극 검토
    const [trendStocks, setTrendStocks] = React.useState([]);
    const [trendLoading, setTrendLoading] = React.useState(false);
    const [valueCandidates, setValueCandidates] = React.useState([]);
    const [valueLoading, setValueLoading] = React.useState(false);
    const [finStocks, setFinStocks] = React.useState([]);
    const [finLoading, setFinLoading] = React.useState(false);
    const [comboFromServer, setComboFromServer] = React.useState([]);
    const [comboLoading, setComboLoading] = React.useState(false);
    const [showFinLogic, setShowFinLogic] = React.useState(false);
    const [comboFilter, setComboFilter] = React.useState('all'); // 'all'=2개↑, 'triple'=3개
    const [comboLogic, setComboLogic] = React.useState('v1');    // 'v1'=Logic-#1, 'v2'=Logic-#2
    const [comboV2Data, setComboV2Data] = React.useState([]);
    const [comboV2Loading, setComboV2Loading] = React.useState(false);
    const [triggerData, setTriggerData] = React.useState(null);
    const [triggerLoading, setTriggerLoading] = React.useState(false);
    const [screenerMeta, setScreenerMeta] = React.useState(null);
    const [v18Data, setV18Data] = React.useState(null);
    const [v18Loading, setV18Loading] = React.useState(false);
    const stickyRef = React.useRef(null);

    // 스크리너 메타(로직 설명) — signal_logic.py 에서 동적 로드
    React.useEffect(() => {
      fetch(API('/api/screener/meta'))
        .then(r => r.ok ? r.json() : null)
        .then(d => { if(d) setScreenerMeta(d); })
        .catch(() => {});
    }, []);

    // 추세 추종 Leading 스크리너
    const fetchTrendLeading = async () => {
      setTrendLoading(true);
      try {
        const res = await fetch(API('/api/signals/trend-candidates'));
        if (res.ok) setTrendStocks(await res.json());
      } catch(e) { console.error(e); }
      finally { setTrendLoading(false); }
    };

    const fetchValueCandidates = async () => {
      setValueLoading(true);
      try {
        const res = await fetch(API('/api/signals/value-candidates'));
        if (res.ok) setValueCandidates(await res.json());
      } catch(e) { console.error(e); }
      finally { setValueLoading(false); }
    };

    // 재무스크리너: 탭 진입 시 독립 fetch (캐시 우선)
    const fetchFinScreener = async () => {
      setFinLoading(true);
      try {
        // 서버 캐시 우선
        const res = await fetch(API('/api/signals/fin-screener'));
        if (res.ok) {
          const d = await res.json();
          if (d && d.length > 0) { setFinStocks(d); setFinLoading(false); return; }
        }
        // fallback: triple endpoint
        const res2 = await fetch(API('/api/dashboard/screening/triple'));
        if (res2.ok) setFinStocks(await res2.json());
      } catch(e) { console.error(e); }
      finally { setFinLoading(false); }
    };

    // combo: 서버 사전계산 캐시 우선, 없으면 클라이언트 교집합
    const fetchCombo = async () => {
      setComboLoading(true);
      try {
        const res = await fetch(API('/api/signals/combo-candidates'));
        if (res.ok) {
          const data = await res.json();
          if (data && data.length > 0) { setComboFromServer(data); setComboLoading(false); return; }
        }
      } catch(e) {}
      setComboLoading(false);
      // 서버 캐시 없으면 로컬 3종 데이터 모두 로드 (fin 누락 버그 수정)
      if (!trendStocks.length) fetchTrendLeading();
      if (!valueCandidates.length) fetchValueCandidates();
      if (!finStocks.length) fetchFinScreener();
    };

    // Logic-#2: 수급 주도 모멘텀
    const fetchComboV2 = async () => {
      setComboV2Loading(true);
      try {
        const res = await fetch(API('/api/signals/combo-v2'));
        if (res.ok) {
          const data = await res.json();
          if (data) setComboV2Data(data);
        }
      } catch(e) { console.error(e); }
      finally { setComboV2Loading(false); }
    };

    React.useEffect(() => {
      if (screenTab === 'trend' && !trendStocks.length) fetchTrendLeading();
      if (screenTab === 'value' && !valueCandidates.length) fetchValueCandidates();
      if (screenTab === 'ai' && !finStocks.length) fetchFinScreener();
      if (screenTab === 'combo') fetchCombo();
      if (screenTab === 'gpt_v18' && !v18Data) fetchV18();
    }, [screenTab]);

    // 초기(combo) 로드
    React.useEffect(() => { fetchCombo(); }, []);

    const fetchV18 = async () => {
      setV18Loading(true);
      try {
        const res = await fetch(API('/api/trend/v18/recommendations'));
        if (res.ok) setV18Data(await res.json());
      } catch (e) { console.error(e); }
      finally { setV18Loading(false); }
    };

    // 서버 백그라운드 계산 완료 후 자동 재시도 (캐시 cold 상태 대응)
    React.useEffect(() => {
      if (comboFromServer.length > 0) return;
      const timer = setTimeout(async () => {
        try {
          const res = await fetch(API('/api/signals/combo-candidates'));
          if (res.ok) {
            const data = await res.json();
            if (data && data.length > 0) setComboFromServer(data);
          }
        } catch(e) {}
      }, 8000);
      return () => clearTimeout(timer);
    }, []);

    // combo: 서버 사전계산 우선, 없으면 클라이언트 교집합
    const comboStocks = React.useMemo(() => {
      // 서버에서 사전계산된 데이터가 있으면 우선 사용 (3개 중복 → 2개 중복 순 정렬)
      if (comboFromServer.length > 0) {
        return [...comboFromServer].sort((a,b) =>
          (b.match_count||0) - (a.match_count||0) || (b.combined_score||b.trend_score||0) - (a.combined_score||a.trend_score||0)
        );
      }
      // 클라이언트 교집합 계산 (fallback)
      // O(1) lookup maps instead of O(n) .find() per code
      const trendMap2 = Object.fromEntries(trendStocks.map(s => [s.stock_code, s]));
      const valueMap2 = Object.fromEntries(valueCandidates.map(s => [s.stock_code, s]));
      const finMap2   = Object.fromEntries(finStocks.map(s => [s.stock_code, s]));
      const allCodes   = new Set([...Object.keys(trendMap2), ...Object.keys(valueMap2), ...Object.keys(finMap2)]);
      const result = [];
      for (const code of allCodes) {
        const trendS = trendMap2[code];
        const valueS = valueMap2[code];
        const finS   = finMap2[code];
        const cnt = (trendS?1:0) + (valueS?1:0) + (finS?1:0);
        if (cnt < 2) continue;
        const base   = trendS || valueS || finS;
        result.push({
          ...base,
          in_trend: !!trendS, in_value: !!valueS, in_fin: !!finS,
          match_count: cnt,
          trend_score: trendS?.score || 0,
          value_score: valueS?.score || 0,
          fin_score:   finS?.total_score || 0,
          combined_score: (trendS?.score||0)*2 + (valueS?.score||0)*2 + (finS?.total_score||0),
        });
      }
      result.sort((a,b) => b.match_count - a.match_count || b.combined_score - a.combined_score);
      return result;
    }, [comboFromServer, trendStocks, valueCandidates, finStocks]);

    // 진입트리거 TOP20 fetch
    const fetchTriggerRanking = async () => {
      setTriggerLoading(true);
      try {
        const res = await fetch(API('/api/signals/trigger-ranking'));
        if (res.ok) setTriggerData(await res.json());
      } catch(e) { console.error(e); }
      finally { setTriggerLoading(false); }
    };

    React.useEffect(() => {
      if (screenTab === 'trigger' && !triggerData) fetchTriggerRanking();
    }, [screenTab]);

    // CSV 다운로드 헬퍼
    const downloadCSV = (rows, filename) => {
      if (!rows || rows.length === 0) return;
      const BOM = '\uFEFF';
      const keys = Object.keys(rows[0]);
      const header = keys.join(',');
      const body = rows.map(r => keys.map(k => {
        const v = r[k];
        if (v == null) return '';
        const s = String(v);
        return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g,'""')}"` : s;
      }).join(',')).join('\n');
      const blob = new Blob([BOM + header + '\n' + body], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    };

    // ── 공통 로직 설명 패널 — signal_logic.py 에서 받은 items 렌더링 ──
    const LogicPanel = ({ metaKey, accentColor, fallbackTitle }) => {
      const meta = screenerMeta?.screeners?.[metaKey];
      const items = meta?.items;
      const color = accentColor || meta?.accent_color || '#f59e0b';
      const title = meta?.title || fallbackTitle || '로직 원리';
      if (!items || items.length === 0) return null;
      return (
        <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
          background:`rgba(0,0,0,0.25)`,border:`1px solid ${color}25`,
          borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
          <div style={{fontWeight:700,color,marginBottom:'0.5rem',fontSize:'0.78rem'}}>
            📖 {title} — 로직 원리
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
            {items.map(({title: t, desc}) => (
              <div key={t}>
                <span style={{color:`${color}cc`,fontWeight:600}}>{t}</span>
                <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
              </div>
            ))}
          </div>
        </div>
      );
    };

    const DlBtn = ({ onClick }) => (
      <button onClick={onClick} title="엑셀(CSV)로 다운로드" style={{
        padding:'0.2rem 0.55rem',borderRadius:'5px',fontSize:'0.7rem',cursor:'pointer',
        border:'1px solid rgba(45,212,191,0.3)',background:'rgba(45,212,191,0.08)',
        color:'var(--accent-mint)',display:'inline-flex',alignItems:'center',gap:'3px'
      }}>⬇ CSV</button>
    );

    // 시장구분 + 시총 배지 헬퍼
    const MktBadge = ({ market, mktcap }) => {
      const isKospi = market && market.includes('코스피');
      const isKosdaq = market && market.includes('코스닥');
      const mktColor = isKospi ? '#60a5fa' : isKosdaq ? '#34d399' : '#94a3b8';
      const mktLabel = isKospi ? 'KOSPI' : isKosdaq ? 'KOSDAQ' : (market || '');
      const capFmt = (v) => {
        if (!v || v <= 0) return null;
        // market_cap은 원(₩) 단위로 저장 → 억 단위로 변환 후 표시
        const uk = v >= 1e8 ? v / 1e8 : v;  // 이미 억 단위면 그대로, 원 단위면 변환
        if (uk >= 10000) return (uk / 10000).toFixed(1) + '조';
        return Math.round(uk).toLocaleString('ko-KR') + '억';
      };
      const capStr = capFmt(mktcap);
      if (!mktLabel && !capStr) return null;
      return (
        <span style={{display:'inline-flex',alignItems:'center',gap:'3px',
          padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.65rem',
          background:`${mktColor}18`,color:mktColor,border:`1px solid ${mktColor}33`}}>
          {mktLabel}{capStr ? ` ${capStr}` : ''}
        </span>
      );
    };

    const tabBtn = (key, label, badge) => (
      <button key={key} onClick={() => setScreenTab(key)} style={{
        padding: '0.4rem 1.1rem', borderRadius: '8px', fontSize: '0.85rem',
        cursor: 'pointer', fontWeight: screenTab === key ? 700 : 400,
        border:      screenTab === key ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
        background:  screenTab === key ? 'rgba(45,212,191,0.15)' : 'transparent',
        color:       screenTab === key ? 'var(--accent-mint)' : 'var(--text-secondary)',
        position: 'relative',
      }}>
        {label}
        {badge > 0 && (
          <span style={{position:'absolute',top:'-5px',right:'-5px',
            background:'#ef4444',color:'#fff',borderRadius:'10px',
            fontSize:'0.6rem',padding:'0.05rem 0.3rem',fontWeight:700,lineHeight:1.4}}>
            {badge}
          </span>
        )}
      </button>
    );

    const SIG_COLOR = { green:'#22c55e', yellow:'#fbbf24', red:'#ef4444', gray:'#64748b' };
    const SIG_EMOJI = { green:'🟢', yellow:'🟡', red:'🔴', gray:'⚪' };

    return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

      {/* 헤더 — sticky */}
      <div ref={stickyRef} className="glass-panel" style={{
        padding: '0.7rem 1.2rem', display: 'flex', flexDirection:'column',
        gap:'0.5rem',
        position: 'sticky', top: 0, zIndex: 50,
        backdropFilter: 'blur(20px)', borderRadius: '12px',
      }}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            <TrendingUp size={18} color="var(--accent-mint)" />
            <h2 style={{fontSize:'1rem'}}>AI 종목 스크리너</h2>
          </div>
          <div style={{display:'flex',gap:'0.4rem',flexWrap:'wrap'}}>
            {tabBtn('combo', `⭐ AI 적극추천`, comboStocks.length)}
            {tabBtn('gpt_v18', `🤖 GPT추천(V18)`, v18Data?.summary?.buy_count || 0)}
            {tabBtn('trigger', '🎯 진입트리거 TOP20')}
            {tabBtn('value', '💎 가치매수')}
            {tabBtn('ai',    '📊 재무 스크리너')}
            {tabBtn('trend', '📈 추세 Leading')}
          </div>
        </div>
        {/* 경고 배너 */}
        <div style={{padding:'0.4rem 0.8rem',background:'rgba(251,191,36,0.07)',
          border:'1px solid rgba(251,191,36,0.25)',borderRadius:'6px',
          fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.4}}>
          ⚠️ AI가 판단한 각각의 주식투자 기법에 따라 필터링을 통과한 종목으로 검증되지 않았음을 안내 드립니다
        </div>
      </div>

      {/* ══ GPT추천(V18) 탭 ══ */}
      {screenTab === 'gpt_v18' && (
        v18Loading ? (
          <div className="glass-panel" style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>V18 추천 계산 중...</div>
        ) : (
          <div className="glass-panel" style={{overflow:'clip'}}>
            <div style={{padding:'0.7rem 1rem',display:'flex',justifyContent:'space-between',alignItems:'center',gap:'0.5rem',flexWrap:'wrap',borderBottom:'1px solid var(--glass-border)'}}>
              <div style={{fontSize:'0.8rem',color:'var(--text-secondary)',display:'flex',gap:'0.8rem',flexWrap:'wrap',alignItems:'center'}}>
                <span>마지막 계산: {v18Data?.updated_at || '-'}</span>
                <span>매수 {v18Data?.summary?.buy_count ?? 0} · 매도 {v18Data?.summary?.sell_count ?? 0} · 보유관찰 {v18Data?.summary?.watch_count ?? 0}</span>
                {v18Data?.kospi_status && (
                  <span style={{
                    padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.72rem',fontWeight:600,
                    background: v18Data.kospi_status.above_ma60 ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
                    color: v18Data.kospi_status.above_ma60 ? '#22c55e' : '#ef4444',
                    border: `1px solid ${v18Data.kospi_status.above_ma60 ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
                  }}>
                    KOSPI {v18Data.kospi_status.close?.toLocaleString()} {v18Data.kospi_status.above_ma60 ? '▲' : '▼'} MA60 {v18Data.kospi_status.ma60?.toLocaleString()} {v18Data.kospi_status.above_ma60 ? '📈v_anchor ON' : `📉v_anchor OFF(${v18Data.kospi_status.break_days}일)`}
                  </span>
                )}
              </div>
              <div style={{display:'flex',gap:'0.4rem'}}>
                <button onClick={fetchV18} style={{padding:'0.25rem 0.7rem',borderRadius:'6px',border:'1px solid rgba(45,212,191,0.3)',background:'rgba(45,212,191,0.08)',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.75rem'}}>새로고침</button>
                <button onClick={async()=>{await fetch(API('/api/trend/v18/execute'),{method:'POST'}); fetchV18();}} style={{padding:'0.25rem 0.7rem',borderRadius:'6px',border:'1px solid rgba(239,68,68,0.35)',background:'rgba(239,68,68,0.1)',color:'#ef4444',cursor:'pointer',fontSize:'0.75rem'}}>즉시 실행</button>
              </div>
            </div>
            {/* ── 예산 현황 바 ── */}
            {v18Data?.summary && (() => {
              const s = v18Data.summary;
              const usedPct = s.budget_pct_used || 0;
              const invested = s.total_invested || 0;
              const remaining = s.remaining_cash || 0;
              const capital = s.virtual_capital || 100000000;
              const reservePct = s.cash_reserve_pct || 20;
              const maxInvest = capital * (1 - reservePct / 100);
              const investPct = Math.min(100, (invested / maxInvest) * 100);
              const barColor = investPct >= 95 ? '#ef4444' : investPct >= 80 ? '#f59e0b' : '#22c55e';
              return (
                <div style={{margin:'0 0.8rem',padding:'0.6rem 0.9rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.07)'}}>
                  <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.35rem',fontSize:'0.72rem'}}>
                    <span style={{color:'rgba(255,255,255,0.5)',fontWeight:600}}>💰 예산 현황</span>
                    <span style={{color:'rgba(255,255,255,0.4)',fontSize:'0.68rem'}}>
                      총예산 {(capital/1e8).toFixed(0)}억 · 현금유보 {reservePct}% · 투자가능 {(maxInvest/1e8).toFixed(2)}억
                    </span>
                  </div>
                  <div style={{height:'6px',borderRadius:'3px',background:'rgba(255,255,255,0.08)',overflow:'hidden',marginBottom:'0.35rem'}}>
                    <div style={{height:'100%',width:`${investPct}%`,background:barColor,borderRadius:'3px',transition:'width 0.4s'}}/>
                  </div>
                  <div style={{display:'flex',justifyContent:'space-between',fontSize:'0.7rem'}}>
                    <span style={{color:barColor,fontWeight:600}}>투자중 {(invested/1e6).toFixed(0)}만원 ({investPct.toFixed(0)}%)</span>
                    <span style={{color: remaining >= 12000000 ? '#22c55e' : '#ef4444'}}>
                      {remaining >= 12000000 ? `추가매수 가능 ${(remaining/1e6).toFixed(0)}만원` : `⚠️ 예산소진 (잔여 ${(remaining/1e6).toFixed(0)}만원)`}
                    </span>
                  </div>
                </div>
              );
            })()}
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.8rem',padding:'0.8rem'}}>
              <div>
                <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#22c55e'}}>매수 추천</div>
                {(v18Data?.buy_candidates || []).length === 0 ? (
                  <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)',lineHeight:1.6}}>
                    {(() => {
                      const s = v18Data?.summary || {};
                      const remaining = s.remaining_cash || 0;
                      const capital = s.virtual_capital || 100000000;
                      const reservePct = s.cash_reserve_pct || 20;
                      const maxInvest = capital * (1 - reservePct / 100);
                      const invested = s.total_invested || 0;
                      if (remaining < 12000000)
                        return `💰 예산 소진 — 투자 한도(${(maxInvest/1e8).toFixed(2)}억) 도달\n현재 ${(invested/1e6).toFixed(0)}만원 투자 중\n매도 후 현금 확보 시 재진입 가능`;
                      if (!v18Data?.kospi_status?.above_ma60)
                        return `📉 v_anchor OFF — KOSPI < MA60\nKOSPI가 MA60 아래 ${v18Data?.kospi_status?.break_days||0}일 연속\n하락추세 확인 중, 신규 진입 보류`;
                      return `⏳ 조건 대기 중\n현재 보유 종목이 모두 최대 티켓 도달\n또는 피라미딩 최소 보유기간(${2}일) 미충족`;
                    })().split('\n').map((l,i) => <div key={i}>{l}</div>)}
                  </div>
                ) : (
                  <table className="premium-table" style={{width:'100%'}}>
                    <thead><tr><th>종목</th><th style={{textAlign:'right'}}>점수</th><th style={{textAlign:'center'}}>근거</th></tr></thead>
                    <tbody>
                      {(v18Data.buy_candidates).slice(0,10).map((r)=>(
                        <tr key={r.stock_code}>
                          <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                          <td style={{textAlign:'right',color:'#22c55e'}}>{r.score}</td>
                          <td style={{textAlign:'center',fontSize:'0.72rem'}}>{r.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div>
                <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#ef4444'}}>매도 추천</div>
                {(v18Data?.sell_candidates || []).length === 0 ? (
                  <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)'}}>
                    ✅ 매도 조건 미충족 — 전 보유 종목 정상 범위
                  </div>
                ) : (
                  <table className="premium-table" style={{width:'100%'}}>
                    <thead><tr><th>종목</th><th style={{textAlign:'right'}}>수익률</th><th style={{textAlign:'center'}}>사유</th></tr></thead>
                    <tbody>
                      {(v18Data.sell_candidates).slice(0,10).map((r)=>(
                        <tr key={r.stock_code}>
                          <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                          <td style={{textAlign:'right',color:(r.profit_pct||0)>=0?'#ef4444':'#3b82f6'}}>{(r.profit_pct||0).toFixed(2)}%</td>
                          <td style={{textAlign:'center',fontSize:'0.72rem'}}>{r.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
            <div style={{padding:'0 0.8rem 0.8rem'}}>
              <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'rgba(255,255,255,0.78)'}}>보유중 V18 관찰종목</div>
              {(v18Data?.watch_candidates || []).length === 0 ? (
                <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)'}}>
                  현재 V18 보유 종목이 없습니다.
                </div>
              ) : (
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>종목</th><th style={{textAlign:'center'}}>진입일</th><th style={{textAlign:'right'}}>매수가</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>수익률</th><th style={{textAlign:'center'}}>티켓</th></tr></thead>
                  <tbody>
                    {(v18Data.watch_candidates).slice(0,12).map((r)=>(
                      <tr key={`${r.stock_code}_${r.id}`}>
                        <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                        <td style={{textAlign:'center'}}>{r.entry_date || '-'}</td>
                        <td style={{textAlign:'right'}}>{r.buy_price?.toLocaleString?.() ?? '-'}</td>
                        <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.() ?? '-'}</td>
                        <td style={{textAlign:'right',color:(r.profit_pct||0)>=0?'#ef4444':'#3b82f6'}}>{(r.profit_pct||0).toFixed(2)}%</td>
                        <td style={{textAlign:'center'}}>{r.tickets || 1}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            {/* ── V18.1p 전략 설명 ── */}
            <div style={{margin:'0.5rem 0.8rem 0.8rem',padding:'0.9rem 1rem',borderRadius:'10px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.07)'}}>
              <div style={{fontSize:'0.75rem',fontWeight:700,color:'rgba(255,255,255,0.55)',marginBottom:'0.55rem',letterSpacing:'0.04em'}}>📋 V18.1p 전략 로직</div>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.6rem 1.2rem',fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>백테스트 성과 (2020-03 ~ 2025-12)</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.15rem'}}>
                    <span>· 누적수익률 <span style={{color:'#22c55e',fontWeight:700}}>+645%</span> (피라미딩 max=2 적용)</span>
                    <span>· 최대낙폭(MDD) <span style={{color:'#f59e0b'}}>-26.4%</span></span>
                    <span>· 전체 6구간 모두 KOSPI 초과 ✅</span>
                    <span>· 거래비용 35bp에서도 <span style={{color:'#22c55e'}}>+486%</span> 유지</span>
                  </div>
                </div>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>예산 및 포지션 규칙</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.15rem'}}>
                    <span>· 총 예산: <span style={{color:'rgba(255,255,255,0.65)'}}>1억원</span> · 현금 유보: <span style={{color:'#f59e0b'}}>20%</span> (2,000만원 상시 보유)</span>
                    <span>· 종목당 티켓: <span style={{color:'rgba(255,255,255,0.65)'}}>1,200만원</span> · 최대 <span style={{color:'rgba(255,255,255,0.65)'}}>2티켓</span></span>
                    <span>· 피라미딩: 1번째 매수 후 <span style={{color:'rgba(255,255,255,0.65)'}}>2일 이후</span>에만 2번째 허용</span>
                    <span>· 예산 소진 시 추가 매수 자동 차단</span>
                  </div>
                </div>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>매수 신호 조건</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.15rem'}}>
                    <span>· v_anchor: KOSPI&gt;MA60 + 대형주 7종 (삼성/SK하이닉스 등)</span>
                    <span>· combo: 추세·가치·재무 2개↑ 일치 종목</span>
                    <span>· 매도 후 쿨다운: v_anchor 5일 / combo 3일</span>
                  </div>
                </div>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>매도 신호 조건</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.15rem'}}>
                    <span>· v_anchor: 하드스탑 <span style={{color:'#ef4444'}}>-10%</span> 또는 KOSPI&lt;MA60 <span style={{color:'#ef4444'}}>3일 연속</span></span>
                    <span>· combo: 하드스탑 <span style={{color:'#ef4444'}}>-10%</span> 또는 MA20↓+MA60↓ 추세이탈</span>
                    <span>· 10분마다 장중 실시간 체크 (장중 악재 즉시 대응)</span>
                  </div>
                </div>
              </div>
              <div style={{marginTop:'0.5rem',paddingTop:'0.4rem',borderTop:'1px solid rgba(255,255,255,0.06)',fontSize:'0.68rem',color:'rgba(255,255,255,0.3)'}}>
                V18.1p(피라미딩 max=2) · 비용 민감도: 15bp +645% / 25bp +521% / 35bp +486% · 2026-05-20 확정
              </div>
            </div>
          </div>
        )
      )}

      {/* ══ 진입트리거 TOP20 탭 ══ */}
      {screenTab === 'trigger' && (() => {
        const fmtAmt = (v) => {
          if(v == null) return <span style={{color:'rgba(255,255,255,0.2)'}}>-</span>;
          const c = v > 0 ? '#ef4444' : '#3b82f6';
          return <span style={{color:c,fontWeight:600,fontSize:'0.75rem'}}>{v>0?'+':''}{Math.round(Math.abs(v)).toLocaleString()}억</span>;
        };
        const fmtBal = (v) => {
          if(!v) return '-';
          const man = v / 10000;
          return man >= 1 ? man.toFixed(1)+'만' : Math.round(v).toLocaleString();
        };
        const ScorePill = ({score, max, color, label}) => {
          const pct = Math.min(score/max*100, 100);
          return (
            <div title={`${label}: ${score}/${max}`} style={{display:'flex',flexDirection:'column',alignItems:'center',gap:'1px',minWidth:'44px'}}>
              <span style={{fontSize:'0.6rem',color:'rgba(255,255,255,0.4)'}}>{label}</span>
              <div style={{width:'100%',height:'5px',borderRadius:'3px',background:'rgba(255,255,255,0.08)',overflow:'hidden'}}>
                <div style={{width:`${pct}%`,height:'100%',background:color,borderRadius:'3px'}}/>
              </div>
              <span style={{fontSize:'0.68rem',fontWeight:700,color}}>{score}<span style={{fontSize:'0.55rem',opacity:0.6}}>/{max}</span></span>
            </div>
          );
        };
        const BorCell = ({b5, b5p, b10, b30, b30p}) => {
          const lights = [
            {label:'5일', curr:b5,  prev:b30},
            {label:'10일',curr:b10, prev:b30},
            {label:'30일',curr:b30, prev:b30p},
          ];
          if(!lights.some(l=>l.curr)) return <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>-</span>;
          return (
            <div style={{display:'flex',gap:'3px',justifyContent:'center'}}>
              {lights.map(({label,curr,prev})=>{
                const rising = (curr||0) > (prev||0)*1.02;
                const col = rising?'#ef4444':'#22c55e';
                return (
                  <div key={label} title={`${label}평균: ${Math.round(curr||0).toLocaleString()}주`}
                    style={{display:'flex',flexDirection:'column',alignItems:'center',padding:'2px 4px',
                      borderRadius:'4px',background:`${col}18`,border:`1px solid ${col}44`,minWidth:'36px'}}>
                    <span style={{fontSize:'0.52rem',color:'rgba(255,255,255,0.4)'}}>{label}</span>
                    <span style={{fontSize:'0.62rem',fontWeight:700,color:col}}>{fmtBal(curr)}</span>
                    <span style={{fontSize:'0.55rem',color:col}}>{rising?'▲':'▼'}</span>
                  </div>
                );
              })}
            </div>
          );
        };

        if(triggerLoading) return (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid var(--accent-mint)',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>진입트리거 TOP20 계산 중...</p>
          </div>
        );
        if(!triggerData) return (
          <div className="glass-panel" style={{padding:'2rem',textAlign:'center'}}>
            <button onClick={fetchTriggerRanking} style={{padding:'0.6rem 1.4rem',borderRadius:'8px',border:'none',
              background:'var(--accent-mint)',color:'#000',cursor:'pointer',fontWeight:700}}>
              🎯 진입트리거 TOP20 로드
            </button>
          </div>
        );
        const stocks = triggerData.stocks || [];
        const cachedAt = triggerData.cached_at ? new Date(triggerData.cached_at*1000).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'}) : '-';

        return (
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
          <div className="glass-panel" style={{overflow:'clip'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'0.8rem 1rem 0.5rem',flexWrap:'wrap',gap:'0.5rem'}}>
              <div>
                <span style={{fontWeight:700,fontSize:'0.95rem'}}>🎯 3-트랙 종합 진입 TOP20</span>
                <span style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginLeft:'0.8rem'}}>캐시: {cachedAt} (1시간 주기 갱신)</span>
              </div>
              <div style={{display:'flex',gap:'0.4rem'}}>
                <button onClick={fetchTriggerRanking} style={{padding:'0.25rem 0.7rem',borderRadius:'6px',border:'1px solid rgba(45,212,191,0.3)',
                  background:'rgba(45,212,191,0.08)',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.75rem'}}>
                  🔄 새로고침
                </button>
                <button onClick={async () => {
                  try {
                    await fetch('/api/commands/screener-refresh', {method:'POST'});
                    alert('재계산 시작 — 약 60~120초 후 새로고침 버튼을 눌러주세요.');
                  } catch(e) { alert('오류: ' + e.message); }
                }} style={{padding:'0.25rem 0.7rem',borderRadius:'6px',border:'1px solid rgba(245,158,11,0.4)',
                  background:'rgba(245,158,11,0.08)',color:'#f59e0b',cursor:'pointer',fontSize:'0.75rem'}}>
                  ⚡ 재계산
                </button>
              </div>
            </div>
            {/* 점수 범례 */}
            <div style={{padding:'0.3rem 1rem 0.6rem',display:'flex',gap:'1rem',flexWrap:'wrap',fontSize:'0.68rem',color:'rgba(255,255,255,0.45)'}}>
              <span>📈 Track A 추세 0~4점 (Minervini 완성=4)</span>
              <span>💎 Track B 가치 0~3점 (Graham 40%+ 할인=3)</span>
              <span>🌐 Track C 섹터 0~1점</span>
              <span style={{color:'rgba(45,212,191,0.7)'}}>● 종합점수 = A×2 + B×2 + C</span>
            </div>
            <table className="premium-table" style={{width:'100%',fontSize:'0.78rem'}}>
              <thead><tr>
                <th style={{minWidth:'30px',textAlign:'center'}}>#</th>
                <th style={{minWidth:'90px'}}>종목명</th>
                <th style={{textAlign:'center',minWidth:'60px'}}>시장</th>
                <th style={{textAlign:'right',minWidth:'75px'}}>현재가</th>
                <th style={{textAlign:'right',minWidth:'60px'}}>등락률</th>
                <th style={{textAlign:'right',minWidth:'70px'}}>시총(억)</th>
                <th style={{textAlign:'right',minWidth:'45px'}}>PBR</th>
                <th style={{textAlign:'right',minWidth:'45px'}}>PER</th>
                <th style={{textAlign:'center',minWidth:'100px'}}>당일수급(외/기)</th>
                <th style={{textAlign:'center',minWidth:'100px'}}>5일수급(외/기)</th>
                <th style={{textAlign:'center',minWidth:'120px'}}>대차잔고</th>
                <th style={{textAlign:'center',minWidth:'200px'}}>3-트랙 점수</th>
                <th style={{textAlign:'center',minWidth:'80px'}}>AI그룹</th>
                <th></th>
              </tr></thead>
              <tbody>
                {stocks.map((s,i) => {
                  const isAI = s.combo_count >= 2;
                  return (
                    <tr key={s.stock_code} style={{background: isAI ? 'rgba(45,212,191,0.04)' : undefined}}>
                      <td style={{textAlign:'center',color:'rgba(255,255,255,0.3)',fontSize:'0.72rem'}}>{i+1}</td>
                      <td>
                        <div style={{fontWeight:600}}>{s.stock_name}</div>
                        <div style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{s.stock_code}</div>
                      </td>
                      <td style={{textAlign:'center'}}>
                        <span style={{padding:'1px 6px',borderRadius:'4px',fontSize:'0.65rem',fontWeight:700,
                          background: s.market?.includes('코스피') ? 'rgba(59,130,246,0.18)' : 'rgba(34,197,94,0.15)',
                          color:      s.market?.includes('코스피') ? '#60a5fa'                : '#4ade80'}}>
                          {s.market?.includes('코스피') ? 'KOSPI' : 'KOSDAQ'}
                        </span>
                      </td>
                      <td style={{textAlign:'right',fontWeight:600}}>{(s.price||0).toLocaleString()}</td>
                      <td style={{textAlign:'right',color: s.change_pct>=0?'#ef4444':'#3b82f6',fontWeight:600}}>
                        {s.change_pct>=0?'+':''}{s.change_pct?.toFixed(1)}%
                      </td>
                      <td style={{textAlign:'right',color:'var(--text-secondary)',fontSize:'0.72rem'}}>
                        {s.mktcap ? Math.round(s.mktcap/100000000).toLocaleString() : '-'}
                      </td>
                      <td style={{textAlign:'right',color: s.pbr&&s.pbr<1?'#4ade80':'var(--text-secondary)',fontSize:'0.75rem'}}>
                        {s.pbr ? s.pbr.toFixed(1)+'x' : '-'}
                      </td>
                      <td style={{textAlign:'right',color:'var(--text-secondary)',fontSize:'0.75rem'}}>
                        {s.per ? s.per.toFixed(1)+'x' : '-'}
                      </td>
                      <td style={{textAlign:'center'}}>
                        <div style={{display:'flex',flexDirection:'column',gap:'1px',alignItems:'center'}}>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>외</span>{fmtAmt(s.frn_today)}
                          </div>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>기</span>{fmtAmt(s.inst_today)}
                          </div>
                        </div>
                      </td>
                      <td style={{textAlign:'center'}}>
                        <div style={{display:'flex',flexDirection:'column',gap:'1px',alignItems:'center'}}>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>외</span>{fmtAmt(s.frn_5d)}
                          </div>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>기</span>{fmtAmt(s.inst_5d)}
                          </div>
                        </div>
                      </td>
                      <td>
                        <BorCell b5={s.bor_5d} b5p={s.bor_5d_prev} b10={s.bor_10d} b30={s.bor_30d} b30p={s.bor_30d_prev}/>
                      </td>
                      <td>
                        <div style={{display:'flex',gap:'6px',alignItems:'flex-end',justifyContent:'center'}}>
                          <ScorePill score={s.track_a||0} max={4} color='#f59e0b' label='추세'/>
                          <ScorePill score={s.track_b||0} max={3} color='#a78bfa' label='가치'/>
                          <ScorePill score={s.sector_bonus||0} max={1} color='#34d399' label='섹터'/>
                        </div>
                        <div style={{fontSize:'0.6rem',color:'rgba(255,255,255,0.35)',textAlign:'center',marginTop:'3px',maxWidth:'200px'}}>
                          {s.detail}
                        </div>
                        <div style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.25)',textAlign:'center',marginTop:'1px'}}>
                          RSI {s.rsi} | 거래량{s.vol_ratio}x | 고점比{s.from_high}%
                        </div>
                      </td>
                      <td style={{textAlign:'center'}}>
                        <div style={{display:'flex',gap:'3px',justifyContent:'center',flexWrap:'wrap'}}>
                          {[
                            {key:'in_trend', label:'추세', color:'#f59e0b'},
                            {key:'in_value', label:'가치', color:'#a78bfa'},
                            {key:'in_fin',   label:'재무', color:'#38bdf8'},
                          ].map(({key,label,color})=>(
                            <span key={key} style={{padding:'1px 5px',borderRadius:'4px',fontSize:'0.6rem',fontWeight:700,
                              background: s[key] ? `${color}22` : 'rgba(255,255,255,0.04)',
                              color:      s[key] ? color        : 'rgba(255,255,255,0.15)',
                              border:`1px solid ${s[key]?color+'44':'rgba(255,255,255,0.06)'}`}}>
                              {label}
                            </span>
                          ))}
                        </div>
                        {isAI && <div style={{fontSize:'0.58rem',color:'var(--accent-mint)',marginTop:'2px',textAlign:'center'}}>★AI</div>}
                      </td>
                      <td>
                        <button onClick={()=>{changeStock(s.stock_code);changeTab('analysis');}}
                          style={{padding:'0.2rem 0.45rem',borderRadius:'4px',border:'none',
                            background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',
                            cursor:'pointer',fontSize:'0.7rem'}}>분석↗</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div style={{padding:'0.8rem 1rem',fontSize:'0.68rem',color:'rgba(255,255,255,0.3)'}}>
              ★AI = Track A(추세) + Track B(가치) 동시 충족 OR 재무스크리너 포함 종목 → AI 적극추천(1) 후보
            </div>
          </div>

          <LogicPanel metaKey="trigger" accentColor="#f59e0b" fallbackTitle="진입트리거 TOP20 선별 원리 — 3-트랙 독립 판정" />
        </div>
        );
      })()}

      {/* ══ 가치매수 후보 탭 ══ */}
      {screenTab === 'value' && (
        valueLoading ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid var(--accent-mint)',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>Graham 가치 스캔 중...</p>
          </div>
        ) : valueCandidates.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>💎</p>
            <p>현재 Graham 가치매수 조건을 충족하는 종목이 없습니다.</p>
            <p style={{fontSize:'0.8rem',marginTop:'0.4rem'}}>EPS·BPS 데이터가 있는 종목 중 저평가 조건을 스캔합니다.</p>
            <button onClick={fetchValueCandidates} style={{marginTop:'1rem',padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(245,158,11,0.15)',border:'1px solid rgba(245,158,11,0.3)',
              color:'#f59e0b',cursor:'pointer',fontSize:'0.8rem'}}>다시 스캔</button>
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
            {/* 안내 배너 */}
            <div style={{padding:'0.6rem 1rem',background:'rgba(245,158,11,0.08)',
              border:'1px solid rgba(245,158,11,0.25)',borderRadius:'8px',
              display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.75rem',color:'#f59e0b',fontWeight:600}}>
                💎 Graham 가치매수 후보 {valueCandidates.length}종목
              </span>
              <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)'}}>
                조건: Graham 내재가치(√22.5×EPS×BPS) 대비 현재가 할인 15%+ OR (PBR&lt;1 AND PER&lt;15) — 종합점수 높은 순
              </span>
              <DlBtn onClick={() => downloadCSV(valueCandidates, 'value_candidates.csv')} />
              <button onClick={fetchValueCandidates} style={{marginLeft:'auto',padding:'0.25rem 0.7rem',
                borderRadius:'6px',background:'rgba(245,158,11,0.15)',border:'1px solid rgba(245,158,11,0.3)',
                color:'#f59e0b',cursor:'pointer',fontSize:'0.72rem'}}>새로고침</button>
            </div>

            {/* 종목 카드 목록 */}
            {valueCandidates.map(c => {
              const sc = SIG_COLOR[c.signal] || '#64748b';
              const se = SIG_EMOJI[c.signal] || '⚪';
              const isStrong = c.score >= 6;
              return (
                <div key={c.stock_code} style={{
                  padding:'1rem', borderRadius:'10px',
                  background: isStrong ? 'rgba(245,158,11,0.08)' : 'rgba(255,255,255,0.02)',
                  border: `1px solid ${isStrong ? 'rgba(245,158,11,0.35)' : 'rgba(255,255,255,0.08)'}`,
                }}>
                  {/* 종목 헤더 */}
                  <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:'0.5rem',flexWrap:'wrap',gap:'0.4rem'}}>
                    <div style={{display:'flex',alignItems:'center',gap:'0.6rem'}}>
                      <span style={{fontSize:'1rem'}}>{se}</span>
                      <div>
                        <span style={{fontWeight:700,fontSize:'0.95rem'}}>{c.stock_name}</span>
                        <span style={{marginLeft:'0.5rem',fontSize:'0.72rem',color:'var(--text-secondary)'}}>
                          {c.stock_code}
                        </span>
                        {isStrong && (
                          <span style={{marginLeft:'0.5rem',padding:'0.1rem 0.5rem',borderRadius:'4px',
                            background:'rgba(245,158,11,0.2)',color:'#f59e0b',fontSize:'0.68rem',fontWeight:700}}>
                            강력매수
                          </span>
                        )}
                        <span style={{marginLeft:'0.4rem'}}><MktBadge market={c.market} mktcap={c.mktcap} /></span>
                      </div>
                    </div>
                    <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                      {/* 점수 배지 */}
                      <div style={{padding:'0.2rem 0.6rem',borderRadius:'6px',
                        background:`rgba(245,158,11,${0.1 + c.score/9*0.3})`,
                        border:'1px solid rgba(245,158,11,0.3)'}}>
                        <span style={{fontSize:'0.72rem',color:'#f59e0b',fontWeight:700}}>
                          Score {c.score}/9
                        </span>
                      </div>
                      <button onClick={() => { changeStock(c.stock_code); changeTab('analysis'); }}
                        style={{padding:'0.3rem 0.7rem',borderRadius:'6px',border:'none',
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                          cursor:'pointer',fontSize:'0.78rem'}}>
                        분석 보기
                      </button>
                    </div>
                  </div>

                  {/* 핵심 지표 */}
                  <div style={{display:'flex',gap:'0.75rem',flexWrap:'wrap',marginBottom:'0.5rem'}}>
                    {[
                      { label:'현재가', val: c.price ? c.price.toLocaleString('ko-KR')+'원' : '-' },
                      { label:'Graham 내재가', val: c.graham_iv ? c.graham_iv.toLocaleString('ko-KR')+'원' : '-', highlight: true },
                      { label:'Graham 할인율', val: c.discount != null ? c.discount.toFixed(1)+'%' : '-', highlight: true },
                      { label:'PBR', val: c.pbr != null ? c.pbr.toFixed(2) : '-' },
                      { label:'PER', val: c.per != null ? c.per.toFixed(1) : '-' },
                      { label:'EPS', val: c.eps ? c.eps.toLocaleString('ko-KR')+'원' : '-' },
                    ].map(({label, val, highlight}) => (
                      <div key={label} style={{textAlign:'center',minWidth:'70px',
                        padding:'0.3rem 0.5rem',borderRadius:'6px',
                        background: highlight ? 'rgba(245,158,11,0.1)' : 'rgba(255,255,255,0.04)',
                        border: `1px solid ${highlight ? 'rgba(245,158,11,0.25)' : 'rgba(255,255,255,0.07)'}`}}>
                        <div style={{fontSize:'0.6rem',color:'var(--text-secondary)',marginBottom:'0.1rem'}}>{label}</div>
                        <div style={{fontSize:'0.78rem',fontWeight:600,color: highlight ? '#f59e0b' : 'var(--text-primary)'}}>{val}</div>
                      </div>
                    ))}
                  </div>

                  {/* 매수 이유 */}
                  <div style={{padding:'0.4rem 0.7rem',background:'rgba(0,0,0,0.2)',borderRadius:'6px',
                    fontSize:'0.72rem',color:'rgba(255,255,255,0.7)',lineHeight:1.5}}>
                    {c.detail}
                  </div>
                </div>
              );
            })}

            <LogicPanel metaKey="value" accentColor="#f59e0b" fallbackTitle="Graham 가치투자 로직 원리" />
          </div>
        )
      )}

      {/* ══ AI 재무 스크리너 탭 ══ */}
      {screenTab === 'ai' && (() => {
        const gradeColor = (g) => g==='강력매수'?'#22c55e':g==='매수'?'#86efac':'#fbbf24';
        const opColor = (t) => t==='흑자전환'?'#22c55e':t==='적자개선'?'#fbbf24':t==='매출급증(적자)'?'#f59e0b':t==='이익 가속'?'#86efac':'rgba(255,255,255,0.5)';
        const fmtB = (v) => v == null ? '-' : Math.abs(v) >= 1e12 ? (v/1e12).toFixed(1)+'조' : Math.abs(v) >= 1e8 ? (v/1e8).toFixed(0)+'억' : (v/1e6).toFixed(0)+'백만';

        if (finLoading) return (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid #a78bfa',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>재무 스크리닝 중...</p>
          </div>
        );

        return finStocks.length === 0 ? (
          <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <TrendingUp size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.3 }} />
            <p>스크리닝 통과 종목이 없습니다.</p>
            <p style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>데이터를 불러오는 중이거나 조건에 맞는 종목이 없습니다.</p>
            <button onClick={fetchFinScreener} style={{marginTop:'1rem',padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(45,212,191,0.15)',border:'1px solid rgba(45,212,191,0.3)',
              color:'var(--accent-mint)',cursor:'pointer'}}>스크린 실행</button>
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
            {/* 헤더 배너 */}
            <div style={{padding:'0.6rem 1rem',background:'rgba(45,212,191,0.05)',
              border:'1px solid rgba(45,212,191,0.2)',borderRadius:'8px',
              display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.75rem',color:'var(--accent-mint)',fontWeight:700}}>
                🔍 {screenerMeta?.screeners?.ai?.title || '소외 턴어라운드 + 성장 기울기 스크리너'}
              </span>
              <span style={{padding:'0.1rem 0.5rem',background:'rgba(34,197,94,0.15)',
                borderRadius:'20px',fontSize:'0.7rem',color:'#22c55e'}}>
                {finStocks.length}종목 발굴
              </span>
              <span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.4)'}}>
                {screenerMeta?.screeners?.ai?.subtitle || '8축 점수 — 소외도·매출기울기·낙폭·턴어라운드·장기추세·섹터소외·거시기회·실적가속'}
              </span>
              <DlBtn onClick={() => downloadCSV(finStocks, 'fin_screener.csv')} />
              <button onClick={() => setShowFinLogic(v => !v)}
                style={{padding:'0.2rem 0.6rem',borderRadius:'5px',border:'1px solid rgba(245,158,11,0.3)',
                  background:'rgba(245,158,11,0.08)',color:'#f59e0b',cursor:'pointer',fontSize:'0.7rem'}}>
                {showFinLogic ? '로직 접기 ▲' : '📖 로직 원리 보기 ▼'}
              </button>
              <button onClick={fetchFinScreener} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',borderRadius:'5px',
                border:'1px solid rgba(45,212,191,0.3)',background:'rgba(45,212,191,0.1)',
                color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.7rem'}}>새로고침</button>
            </div>

            {/* 로직 원리 패널 */}
            {showFinLogic && (
              <LogicPanel metaKey="ai" accentColor="#a78bfa" fallbackTitle="8축 소외 턴어라운드 + 성장 기울기 전략" />
            )}

            {/* 종목 카드 목록 */}
            {finStocks.map(s => {
              const gc = gradeColor(s.grade);
              const isStrong = s.grade === '강력매수';
              const isBuy    = s.grade === '매수';
              return (
                <div key={s.stock_code} style={{
                  padding:'0.9rem 1rem',borderRadius:'10px',
                  background: isStrong ? 'rgba(34,197,94,0.06)' : isBuy ? 'rgba(134,239,172,0.03)' : 'rgba(255,255,255,0.02)',
                  border:`1px solid ${isStrong ? 'rgba(34,197,94,0.3)' : isBuy ? 'rgba(134,239,172,0.15)' : 'rgba(255,255,255,0.08)'}`,
                }}>
                  {/* 헤더 */}
                  <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.5rem',flexWrap:'wrap'}}>
                    {s.topdown_combo && (
                      <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.7rem',fontWeight:700,
                        background:'rgba(251,191,36,0.15)',color:'#fbbf24',border:'1px solid rgba(251,191,36,0.4)'}}>
                        👑 TopDown콤보
                      </span>
                    )}
                    <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.72rem',fontWeight:700,
                      background:`${gc}22`,color:gc,border:`1px solid ${gc}44`}}>
                      {s.grade}
                    </span>
                    <span style={{fontWeight:700,fontSize:'0.92rem'}}>{s.stock_name}</span>
                    <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{s.stock_code}</span>
                    {s.sector && (
                      <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',
                        background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.45)',
                        border:'1px solid rgba(255,255,255,0.08)'}}>
                        {s.sector}{s.sector_mid ? ' › '+s.sector_mid : ''}
                      </span>
                    )}
                    {s.op_trend && s.op_trend !== '유지' && (
                      <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:700,
                        background:`${opColor(s.op_trend)}18`,color:opColor(s.op_trend),
                        border:`1px solid ${opColor(s.op_trend)}33`}}>
                        {s.op_trend}
                      </span>
                    )}
                    {s.smart_money && (
                      <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:700,
                        background:'rgba(52,211,153,0.12)',color:'#34d399',border:'1px solid rgba(52,211,153,0.3)'}}>
                        🎯 스마트머니
                      </span>
                    )}
                    <MktBadge market={s.market} mktcap={s.mktcap} />
                    <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:'0.4rem'}}>
                      <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)',fontWeight:600}}>
                        {s.total_score}점
                      </span>
                      {/* 8축 + 4필터 미니 점수바 */}
                      <div style={{display:'flex',gap:'2px',alignItems:'center'}}>
                        {[
                          {k:'score_neglect',    label:'소외', color:'#a78bfa'},
                          {k:'score_growth',     label:'성장', color:'#22c55e'},
                          {k:'score_oversold',   label:'낙폭', color:'#f59e0b'},
                          {k:'score_turnaround', label:'전환', color:'#f87171'},
                          {k:'score_longtrend',  label:'장기', color:'#60a5fa'},
                          {k:'score_sector',     label:'섹터', color:'#fbbf24'},
                          {k:'score_macro',      label:'거시', color:'#fb923c'},
                          {k:'score_accel',      label:'가속', color:'#34d399'},
                          {k:'score_ocf',        label:'OCF',  color:'#2dd4bf'},
                          {k:'score_moat',       label:'해자', color:'#818cf8'},
                          {k:'score_catalyst',   label:'촉매', color:'#f472b6'},
                          {k:'score_safety',     label:'안전', color:'#facc15'},
                        ].map(({k, label, color}) => (
                          <div key={k} title={`${label}: ${s[k]||0}`}
                            style={{width:'13px',height:'13px',borderRadius:'2px',
                              background: (s[k]||0) >= 3 ? color : (s[k]||0) >= 2 ? color+'88' : (s[k]||0) >= 1 ? color+'44' : 'rgba(255,255,255,0.08)',
                              border:`1px solid ${color}33`}} />
                        ))}
                      </div>
                      <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                        style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                          cursor:'pointer',fontSize:'0.72rem'}}>
                        분석
                      </button>
                    </div>
                  </div>

                  {/* 핵심 지표 */}
                  <div style={{display:'flex',gap:'0.45rem',flexWrap:'wrap',marginBottom:'0.45rem'}}>
                    {[
                      {label:'현재가',    val: s.price ? s.price.toLocaleString('ko-KR')+'원' : '-'},
                      {label:'고점대비',  val: s.drawdown_pct != null ? s.drawdown_pct+'%' : '-',
                        color: s.drawdown_pct <= -40 ? '#f87171' : s.drawdown_pct <= -25 ? '#fbbf24' : 'rgba(255,255,255,0.6)'},
                      {label:'저점반등',  val: s.from_low_pct != null ? '+'+s.from_low_pct+'%' : '-',
                        color: s.from_low_pct >= 15 ? '#22c55e' : 'rgba(255,255,255,0.6)'},
                      {label:'매출기울기',val: s.revenue_slope_pct != null ? (s.revenue_slope_pct > 0 ? '+' : '')+s.revenue_slope_pct+'%' : '-',
                        color: s.revenue_slope_pct > 15 ? '#22c55e' : s.revenue_slope_pct > 5 ? '#fbbf24' : 'rgba(255,255,255,0.5)'},
                      {label:'매출YoY',  val: s.revenue_yoy_pct != null ? (s.revenue_yoy_pct > 0 ? '+' : '')+s.revenue_yoy_pct+'%' : '-',
                        color: s.revenue_yoy_pct > 30 ? '#22c55e' : s.revenue_yoy_pct > 0 ? '#86efac' : '#f87171'},
                      {label:'최신매출',  val: fmtB(s.latest_revenue)},
                      {label:'영업이익',  val: fmtB(s.latest_profit),
                        color: s.latest_profit > 0 ? '#22c55e' : '#f87171'},
                      {label:'PBR',      val: s.pbr != null ? s.pbr.toFixed(2) : '-',
                        color: s.pbr != null && s.pbr < 1 ? '#f59e0b' : 'rgba(255,255,255,0.6)'},
                      {label:'PER',      val: s.per != null ? s.per.toFixed(1) : '-',
                        color: s.per != null && s.per < 10 ? '#f59e0b' : 'rgba(255,255,255,0.6)'},
                      {label:'Fwd PER',  val: s.forward_per != null ? s.forward_per+'x' : '-',
                        color: s.forward_per != null && s.forward_per < 10 ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
                        title:'선행PER(Forward EPS 추정)'},
                      {label:'PSR',      val: s.psr != null ? s.psr.toFixed(2) : '-',
                        color: s.psr != null && s.psr < 0.5 ? '#facc15' : s.psr != null && s.psr < 1 ? '#fbbf24' : 'rgba(255,255,255,0.5)',
                        title:'주가매출비율(낮을수록 매출 대비 싼 주가)'},
                      {label:'ROE 3y',   val: s.avg_roe_3y != null ? s.avg_roe_3y+'%' : '-',
                        color: s.avg_roe_3y != null && s.avg_roe_3y >= 15 ? '#818cf8' : s.avg_roe_3y != null && s.avg_roe_3y >= 8 ? '#a5b4fc' : 'rgba(255,255,255,0.4)',
                        title:'3년 평균 자기자본이익률(해자 지표)'},
                      {label:'OCF',      val: s.ocf_positive != null ? (s.ocf_positive ? '양(+)' : '음(-)') : '-',
                        color: s.ocf_positive ? '#2dd4bf' : '#f87171',
                        title:'영업활동현금흐름 양/음'},
                      {label:'RS 3M',    val: s.rs3m != null ? (s.rs3m > 0 ? '+' : '')+s.rs3m+'%p' : '-',
                        color: s.rs3m != null && s.rs3m < -10 ? '#fb923c' : s.rs3m != null && s.rs3m > 0 ? '#22c55e' : 'rgba(255,255,255,0.5)'},
                    ].map(({label, val, color, title}) => (
                      <div key={label} title={title||label} style={{textAlign:'center',minWidth:'58px',
                        padding:'0.22rem 0.4rem',borderRadius:'5px',
                        background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.07)'}}>
                        <div style={{fontSize:'0.57rem',color:'var(--text-secondary)',marginBottom:'0.08rem'}}>{label}</div>
                        <div style={{fontSize:'0.74rem',fontWeight:600,color: color || 'var(--text-primary)'}}>{val}</div>
                      </div>
                    ))}
                  </div>

                  {/* 발굴 태그 */}
                  <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                    {(s.tags || []).map((t, i) => (
                      <span key={i} style={{padding:'0.15rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',
                        background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.65)',
                        border:'1px solid rgba(255,255,255,0.08)'}}>
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}

            {/* ── 재무 스크리너 로직 설명 (하단) ── */}
            <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
              background:'rgba(139,92,246,0.03)',border:'1px solid rgba(139,92,246,0.12)',
              borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
              <div style={{fontWeight:700,color:'#a78bfa',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
                📊 재무 스크리너 원리 — 소외 턴어라운드 + 성장 기울기 전략 (8축 최대 32점)
              </div>
              <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
                {[
                  ['① 주가 소외도','PBR<0.5(+2), PBR<1(+1), PER<6(+2), PER<12(+1), 소형주(+1). 낮을수록 시장이 외면 중 → 실적 반전 시 "재발견" 급등 기대.'],
                  ['② 매출 성장 기울기','선형회귀 기울기 >20%/분기(+2), >8%(+1) + YoY >50%(+2), >20%(+1). 주가보다 매출이 먼저 달라진다. 속도와 가속도가 핵심 선행지표.'],
                  ['③ 낙폭 과다','52주 고점 대비 -60%(+3), -40%(+2), -25%(+1) + 저점 반등 +15%(+1). 과도 하락 + 바닥 확인 = 턴어라운드 진입 구간.'],
                  ['④ 턴어라운드','과거 적자→최신 흑자 전환(+4 MAX). 적자 3분기 연속 개선(+2). 매출 급증 중 적자(+1). 시장이 가장 늦게 인식하는 구간.'],
                  ['⑤ 장기 실적 추세','연간 매출 기울기 >15%/년(+2), 성장 가속화(+1), 연간 흑자전환(+1). 분기 노이즈 제거 후 구조적 개선 여부 확인.'],
                  ['⑥ 섹터 소외','섹터 리더 52주선 위인데 개별주 -30%(+3). 섹터 순환 시 소외주에 탄력. 리더 먼저, 졸개 나중 패턴.'],
                  ['⑦ 거시 낙폭 기회','KOSPI 대비 RS -15%p(+3), -8%(+2), -3%(+1). 전쟁·금리 등 거시 폭락 시 실적 종목은 부당하게 빠짐 → 최고의 진입 기회.'],
                  ['⑧ 실적 가속','영업이익 3분기 연속 확대(+2), 레버리지 발현(+2). 고정비 커버 후 추가 매출 전액 이익 → EPS 폭발 구간.'],
                ].map(([title, desc]) => (
                  <div key={title}>
                    <span style={{color:'rgba(139,92,246,0.8)',fontWeight:600}}>{title}</span>
                    <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
                  </div>
                ))}
              </div>
              <div style={{marginTop:'0.6rem',paddingTop:'0.6rem',borderTop:'1px solid rgba(139,92,246,0.1)',
                color:'rgba(255,255,255,0.35)',fontSize:'0.67rem'}}>
                [등급] 22점↑ 강력매수 | 16점↑ 매수 | 12점↑ 관심 &nbsp;·&nbsp;
                미니 색상 바(헤더 우측 8칸): 보라=소외 / 초록=성장 / 주황=낙폭 / 빨강=전환 / 파랑=장기 / 노랑=섹터 / 주황=거시 / 민트=가속
              </div>
            </div>
          </div>
        );
      })()}

      {/* ══ 추세 추종 Leading 탭 ══ */}
      {screenTab === 'trend' && (
        trendLoading ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid var(--accent-mint)',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>추세 스캔 중... (전 종목 스캔, 수십 초 소요)</p>
          </div>
        ) : trendStocks.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>📈</p>
            <p>오늘은 3단계 조건을 모두 충족하는 돌파 종목이 없습니다.</p>
            <p style={{fontSize:'0.78rem',marginTop:'0.4rem',color:'rgba(255,255,255,0.4)'}}>
              거래량 2배↑ + BB 상단 돌파는 실제 돌파일에만 발생합니다.<br/>
              시장이 약하거나 박스권 구간에서는 결과가 없는 것이 정상입니다.
            </p>
            <button onClick={fetchTrendLeading} style={{marginTop:'1rem',padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(45,212,191,0.15)',border:'1px solid rgba(45,212,191,0.3)',
              color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.8rem'}}>다시 스캔</button>
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
            {/* 안내 배너 */}
            <div style={{padding:'0.6rem 1rem',background:'rgba(45,212,191,0.06)',
              border:'1px solid rgba(45,212,191,0.2)',borderRadius:'8px',
              display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.75rem',color:'var(--accent-mint)',fontWeight:600}}>
                📈 {screenerMeta?.screeners?.trend?.title || '미너비니 3단계'} — 오늘의 주도주 {trendStocks.length}종목 (최대 20개)
              </span>
              <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)'}}>
                {screenerMeta?.screeners?.trend?.subtitle || 'MA120/200 정배열 × RSI60↑ × 거래량2배↑'}
              </span>
              <DlBtn onClick={() => downloadCSV(trendStocks, 'trend_leading.csv')} />
              <button onClick={fetchTrendLeading} style={{marginLeft:'auto',padding:'0.25rem 0.7rem',
                borderRadius:'6px',background:'rgba(45,212,191,0.12)',border:'1px solid rgba(45,212,191,0.25)',
                color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.72rem'}}>새로고침</button>
            </div>

            {/* 200주 개념 설명 */}
            {(() => {
              // 현재 종목들의 weekly_label 에서 최대 주봉 수 파악
              const weekNums = trendStocks
                .map(s => { const m = (s.weekly_label||'').match(/(\d+)주선/); return m ? parseInt(m[1]) : 0; })
                .filter(n => n > 0);
              const maxWeeks = weekNums.length > 0 ? Math.max(...weekNums) : 0;
              const is200w   = maxWeeks >= 200;
              return (
                <div style={{padding:'0.5rem 0.8rem',background: is200w ? 'rgba(34,197,94,0.05)' : 'rgba(245,158,11,0.05)',
                  border:`1px solid ${is200w ? 'rgba(34,197,94,0.2)' : 'rgba(245,158,11,0.15)'}`,borderRadius:'6px',fontSize:'0.7rem',
                  color:'rgba(255,255,255,0.55)',lineHeight:1.6}}>
                  {is200w
                    ? <>✅ <strong style={{color:'rgba(34,197,94,0.9)'}}>주봉 장기이평선</strong> — <strong>{maxWeeks}주(약{Math.round(maxWeeks/52)}년)선</strong> 적용 중.
                       섹터 시총 상위주들이 장기 주봉선 위에 있을 때만 그 섹터 개별주에 진입. 섹터가 침묵 중이면 FOMO 주의.</>
                    : <>⚡ <strong style={{color:'rgba(245,158,11,0.8)'}}>주봉 장기이평선</strong> — 200주(5년)선이 목표이며 현재 DB 기준 <strong>{maxWeeks > 0 ? `${maxWeeks}주선` : '52주선'}</strong> 적용 중.
                       역대 데이터 수집 중이며 완료 시 200주선으로 자동 전환됩니다. 섹터가 침묵 중이면 FOMO 주의.</>
                  }
                </div>
              );
            })()}

            {/* 종목 카드 그리드 */}
            <div style={{display:'flex',flexDirection:'column',gap:'0.5rem'}}>
              {trendStocks.map(s => {
                const isStrong = s.label === '강력매수';
                const isBuy    = s.label === '매수';
                const sc = isStrong ? '#22c55e' : isBuy ? '#86efac' : '#fbbf24';
                const sectorActive = s.sector_act >= 0.6;
                const sectorPart   = s.sector_act >= 0.4 && !sectorActive;
                return (
                  <div key={s.stock_code} style={{
                    padding:'0.85rem 1rem',borderRadius:'10px',
                    background: isStrong ? 'rgba(34,197,94,0.06)' : 'rgba(255,255,255,0.02)',
                    border:`1px solid ${isStrong ? 'rgba(34,197,94,0.3)' : isBuy ? 'rgba(134,239,172,0.2)' : 'rgba(255,255,255,0.08)'}`,
                  }}>
                    {/* 헤더 행 */}
                    <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.45rem',flexWrap:'wrap'}}>
                      <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.72rem',
                        fontWeight:700,background:`${sc}22`,color:sc,border:`1px solid ${sc}55`}}>
                        {s.label}
                      </span>
                      <span style={{fontWeight:700,fontSize:'0.92rem'}}>{s.stock_name}</span>
                      <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>
                        {s.stock_code}
                      </span>
                      <MktBadge market={s.market} mktcap={s.mktcap} />
                      {/* 섹터 배지 */}
                      {s.sector && (
                        <span style={{marginLeft:'0.2rem',padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.68rem',
                          background: sectorActive ? 'rgba(245,158,11,0.2)' : sectorPart ? 'rgba(245,158,11,0.1)' : 'rgba(255,255,255,0.05)',
                          color: sectorActive ? '#f59e0b' : sectorPart ? 'rgba(245,158,11,0.7)' : 'rgba(255,255,255,0.4)',
                          border:`1px solid ${sectorActive ? 'rgba(245,158,11,0.4)' : 'rgba(255,255,255,0.1)'}`}}>
                          {sectorActive ? '🔥' : sectorPart ? '⚡' : ''} {s.sector}{s.sector_mid ? ' › '+s.sector_mid : ''}
                        </span>
                      )}
                      {/* 점수 + 분석 버튼 */}
                      <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:'0.4rem'}}>
                        <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.72rem',
                          background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',fontWeight:700}}>
                          {s.score}점
                        </span>
                        <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                          style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                            background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                            cursor:'pointer',fontSize:'0.72rem'}}>
                          분석
                        </button>
                      </div>
                    </div>

                    {/* 핵심 지표 행 */}
                    <div style={{display:'flex',gap:'0.5rem',flexWrap:'wrap',marginBottom:'0.4rem'}}>
                      {[
                        {label:'현재가', val: s.price ? s.price.toLocaleString('ko-KR')+'원' : '-'},
                        {label:'MA20', val: s.ma20 ? s.ma20.toLocaleString('ko-KR') : '-'},
                        {label:'MA60', val: s.ma60 ? s.ma60.toLocaleString('ko-KR') : '-'},
                        {label:'고점대비', val: s.from_high != null ? (s.from_high >= 0 ? '+' : '')+s.from_high+'%' : '-',
                          color: s.from_high >= -5 ? '#22c55e' : s.from_high >= -15 ? '#fbbf24' : 'rgba(255,255,255,0.5)'},
                        {label:'섹터활성', val: s.sector_act != null ? (s.sector_act*100).toFixed(0)+'%' : '-',
                          color: sectorActive ? '#f59e0b' : sectorPart ? 'rgba(245,158,11,0.7)' : 'rgba(255,255,255,0.4)'},
                      ].map(({label,val,color}) => (
                        <div key={label} style={{textAlign:'center',minWidth:'62px',
                          padding:'0.25rem 0.4rem',borderRadius:'5px',
                          background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.07)'}}>
                          <div style={{fontSize:'0.58rem',color:'var(--text-secondary)',marginBottom:'0.1rem'}}>{label}</div>
                          <div style={{fontSize:'0.75rem',fontWeight:600,color: color || 'var(--text-primary)'}}>{val}</div>
                        </div>
                      ))}
                    </div>

                    {/* 매수 근거 태그들 */}
                    <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                      {(s.reasons || []).map((r,i) => (
                        <span key={i} style={{padding:'0.15rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',
                          background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.65)',
                          border:'1px solid rgba(255,255,255,0.08)'}}>
                          {r}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>

            <LogicPanel metaKey="trend" accentColor="#2dd4bf" fallbackTitle="추세추종 로직 원리 — 미너비니(Minervini) 3단계 필터" />
          </div>
        )
      )}

      {/* ══ AI 적극추천 탭 ══ */}
      {screenTab === 'combo' && (() => {
        // 항상 전체 표시 (3관왕 먼저, 2개 충족 다음 — comboStocks 이미 정렬됨)
        const filteredCombo = comboStocks;
        const sigColor = { '강력추천':'#ef4444', '추천':'#f59e0b', '관심':'#22c55e' };
        const sigEmoji = { '강력추천':'🔥', '추천':'⭐', '관심':'👀' };
        return (
        <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
          {/* 로직 선택 드랍다운 */}
          <div style={{display:'flex',alignItems:'center',gap:'0.75rem',padding:'0.55rem 1rem',
            background:'rgba(0,0,0,0.3)',border:'1px solid rgba(255,255,255,0.08)',
            borderRadius:'10px',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.78rem',color:'rgba(255,255,255,0.55)',fontWeight:600}}>📐 로직 선택:</span>
            <div style={{display:'flex',gap:'0.3rem',background:'rgba(0,0,0,0.2)',borderRadius:'8px',padding:'0.2rem'}}>
              {[
                { key:'v1', label:'Logic v1', desc:'추세·가치·재무 교집합 — Minervini 추세 + Graham 가치 + 재무스크리너 3관왕 (백테스트 v5 기준)' },
                { key:'v2', label:'Logic v2', desc:'수급 주도 모멘텀 — 기관·외국인 동반순매수 + 추세 + 실적 복합스코어 (최대 42점)' },
              ].map(opt => (
                <button key={opt.key} title={opt.desc} onClick={() => {
                    setComboLogic(opt.key);
                    if (opt.key === 'v2' && comboV2Data.length === 0) fetchComboV2();
                  }}
                  style={{padding:'0.25rem 1rem',borderRadius:'6px',fontSize:'0.78rem',cursor:'pointer',
                    fontWeight: comboLogic===opt.key ? 700 : 400, border:'none',
                    background: comboLogic===opt.key
                      ? (opt.key==='v2' ? 'rgba(99,102,241,0.4)' : 'rgba(239,68,68,0.35)')
                      : 'transparent',
                    color: comboLogic===opt.key ? '#fff' : 'rgba(255,255,255,0.45)',
                    transition:'all 0.15s'}}>
                  {opt.label}
                </button>
              ))}
            </div>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.35)'}}>
              {comboLogic==='v1'
                ? 'v1: 추세·가치·재무 3관왕 우선 → 2개 충족 종목 순 배치'
                : 'v2: 수급 주도 모멘텀 — 기관·외국인 동반순매수 × 추세 × 실적 복합스코어'}
            </span>
          </div>

          {/* ── Logic-#1 렌더링 ── */}
          {comboLogic === 'v1' && (<>
          {/* 안내 배너 */}
          <div style={{padding:'0.7rem 1rem',
            background:'linear-gradient(135deg, rgba(239,68,68,0.08), rgba(245,158,11,0.08))',
            border:'1px solid rgba(239,68,68,0.3)',borderRadius:'8px',
            display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.8rem',color:'#ef4444',fontWeight:700}}>
              ⭐ Logic v1 — 전체 {comboStocks.length}종목
            </span>
            <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.45)',display:'flex',alignItems:'center',gap:'0.4rem'}}>
              <span style={{padding:'0.1rem 0.4rem',borderRadius:'4px',background:'rgba(239,68,68,0.2)',color:'#ef4444',fontWeight:700,fontSize:'0.68rem'}}>
                🏆 3관왕 {comboStocks.filter(s=>s.match_count>=3).length}종목
              </span>
              <span style={{padding:'0.1rem 0.4rem',borderRadius:'4px',background:'rgba(245,158,11,0.15)',color:'#f59e0b',fontWeight:700,fontSize:'0.68rem'}}>
                ⭐ 2개 충족 {comboStocks.filter(s=>s.match_count===2).length}종목
              </span>
            </span>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
              3관왕(추세+가치+재무) 우선 표시 → 2개 충족 종목 순
            </span>
            <DlBtn onClick={() => downloadCSV(comboStocks, 'ai_combo.csv')} />
          </div>

          {filteredCombo.length === 0 ? (
            <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>⭐</p>
              <p>현재 2개 이상 카테고리를 동시 충족하는 종목이 없습니다.</p>
              <p style={{fontSize:'0.78rem',marginTop:'0.4rem',color:'rgba(255,255,255,0.35)'}}>
                가치매수·재무스크리너·추세 탭을 각각 로드한 후 다시 확인해 주세요.
              </p>
            </div>
          ) : (
            filteredCombo.map(s => {
              const allThree = s.match_count >= 3;
              return (
                <div key={s.stock_code} style={{
                  padding:'1rem',borderRadius:'10px',
                  background: allThree ? 'rgba(239,68,68,0.08)' : 'rgba(245,158,11,0.05)',
                  border:`1px solid ${allThree ? 'rgba(239,68,68,0.35)' : 'rgba(245,158,11,0.25)'}`,
                }}>
                  {/* 헤더 */}
                  <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.6rem',flexWrap:'wrap'}}>
                    <span style={{fontSize:'1.1rem'}}>{allThree ? '🏆' : '⭐'}</span>
                    <span style={{fontWeight:700,fontSize:'0.95rem'}}>{s.stock_name || s.stock_code}</span>
                    <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{s.stock_code}</span>
                    <MktBadge market={s.market} mktcap={s.mktcap} />
                    {allThree && (
                      <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.68rem',fontWeight:700,
                        background:'rgba(239,68,68,0.2)',color:'#ef4444',border:'1px solid rgba(239,68,68,0.4)'}}>
                        3관왕
                      </span>
                    )}
                    <div style={{marginLeft:'auto',display:'flex',gap:'0.4rem',alignItems:'center'}}>
                      {/* 카테고리 배지 */}
                      {s.in_trend && (
                        <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:600,
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',border:'1px solid rgba(45,212,191,0.3)'}}>
                          📈 추세
                        </span>
                      )}
                      {s.in_value && (
                        <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:600,
                          background:'rgba(245,158,11,0.15)',color:'#f59e0b',border:'1px solid rgba(245,158,11,0.3)'}}>
                          💎 가치
                        </span>
                      )}
                      {s.in_fin && (
                        <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:600,
                          background:'rgba(139,92,246,0.15)',color:'#a78bfa',border:'1px solid rgba(139,92,246,0.3)'}}>
                          📊 재무
                        </span>
                      )}
                      <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                        style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                          cursor:'pointer',fontSize:'0.72rem'}}>
                        분석
                      </button>
                    </div>
                  </div>
                  {/* 점수 행 */}

                  {/* 점수 행 */}
                  <div style={{display:'flex',gap:'0.5rem',flexWrap:'wrap',marginBottom:'0.5rem'}}>
                    {s.in_trend && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(45,212,191,0.08)',border:'1px solid rgba(45,212,191,0.2)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>추세점수 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:700,color:'var(--accent-mint)'}}>{s.trend_score}</span>
                      </div>
                    )}
                    {s.in_value && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(245,158,11,0.08)',border:'1px solid rgba(245,158,11,0.2)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>가치점수 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:700,color:'#f59e0b'}}>{s.value_score}/9</span>
                      </div>
                    )}
                    {s.in_fin && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(139,92,246,0.08)',border:'1px solid rgba(139,92,246,0.2)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>재무점수 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:700,color:'#a78bfa'}}>{s.fin_score}</span>
                      </div>
                    )}
                    {s.price && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.08)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>현재가 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:600}}>{s.price.toLocaleString('ko-KR')}원</span>
                      </div>
                    )}
                    {s.sector && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.06)'}}>
                        <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>{s.sector}</span>
                      </div>
                    )}
                  </div>
                  {/* 근거 태그 */}
                  <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                    {(s.reasons || s.tags || []).slice(0,6).map((r,i) => (
                      <span key={i} style={{padding:'0.12rem 0.4rem',borderRadius:'4px',fontSize:'0.65rem',
                        background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.6)',
                        border:'1px solid rgba(255,255,255,0.08)'}}>
                        {r}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })
          )}

          {/* ── AI 적극추천(1) 로직 설명 ── */}
          <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
            background:'rgba(239,68,68,0.03)',border:'1px solid rgba(239,68,68,0.12)',
            borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
            <div style={{fontWeight:700,color:'#ef4444',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
              ⭐ Logic v1 — 선정 원리 (추세·가치·재무 교집합, 백테스트 v5 기준)
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
              {[
                ['매수 조건 [A] 추세 (필수)','Minervini 5필터 전부 충족: ①현재가>MA120>MA200 장기정배열 ②MA20>MA60 단기정배열 ③52주 고점 -20% 이내 ④RSI(14)≥60 ⑤거래량>20일평균×2.0배'],
                ['매수 조건 [B] 가치·수급 (1개 이상)','▸ 가치: Graham 할인≥25% OR (PBR<0.7 AND 0<PER<10), 영업이익>0 ▸ 수급: 기관 5일 순매수>0 AND 외국인 5일 순매수>0 동반 매수'],
                ['시장 필터 (v5 강화)','KOSPI 현재가 > KOSPI MA120. 하락장 진입 시 매수 전면 차단. (v3~v4 MA60 → v5 MA120으로 강화)'],
                ['매도 조건','익절 +15% / 하드손절 -8% / 추적손절(고점 대비) -10% / MA60 붕괴. 최소 보유 5거래일 (단기 노이즈 차단)'],
                ['3관왕 (🏆)','추세+가치+재무 3개 모두 충족. 극히 드문 고확률 후보. 최우선 검토 대상 (목록 상단 표시).'],
                ['교집합 원리','독립적인 3개 스크리너 중 2개 이상 동시 충족 → 개별 조건 대비 오류 확률 대폭 감소. 3관왕 우선 → 2개 충족 순 배치.'],
              ].map(([title, desc]) => (
                <div key={title}>
                  <span style={{color:'rgba(239,68,68,0.8)',fontWeight:600}}>{title}</span>
                  <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
                </div>
              ))}
            </div>
          </div>
          </>)}

          {/* ── AI 적극추천(2): 수급 주도 모멘텀 ── */}
          {comboLogic === 'v2' && (<>
          <div style={{padding:'0.7rem 1rem',
            background:'linear-gradient(135deg, rgba(99,102,241,0.08), rgba(45,212,191,0.06))',
            border:'1px solid rgba(99,102,241,0.35)',borderRadius:'8px',
            display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.8rem',color:'#818cf8',fontWeight:700}}>
              🔥 Logic v2 — 수급 주도 모멘텀 {comboV2Data.length}종목
            </span>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
              기관·외국인 동반순매수 × 추세 × 실적 복합 스코어
            </span>
            {comboV2Data.length > 0 && <DlBtn onClick={() => downloadCSV(comboV2Data, 'combo_v2.csv')} />}
            <button onClick={fetchComboV2} style={{marginLeft:'auto',padding:'0.2rem 0.7rem',
              borderRadius:'6px',border:'1px solid rgba(99,102,241,0.4)',
              background:'rgba(99,102,241,0.1)',color:'#818cf8',cursor:'pointer',fontSize:'0.73rem'}}>
              🔄 새로고침
            </button>
          </div>

          {comboV2Loading ? (
            <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid #818cf8',
                borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
              <p>Logic v2 계산 중... (최초 실행 시 1~2분 소요)</p>
            </div>
          ) : comboV2Data.length === 0 ? (
            <div className="glass-panel" style={{padding:'2.5rem',textAlign:'center'}}>
              <p style={{fontSize:'1.5rem',marginBottom:'0.5rem'}}>📡</p>
              <p style={{color:'var(--text-secondary)',fontSize:'0.85rem'}}>수급 데이터 분석 결과가 없습니다.</p>
              <button onClick={fetchComboV2} style={{marginTop:'1rem',padding:'0.5rem 1.2rem',
                borderRadius:'8px',border:'none',background:'#818cf8',color:'#fff',
                cursor:'pointer',fontWeight:700}}>
                🔥 Logic v2 분석 실행
              </button>
            </div>
          ) : comboV2Data.map(s => {
            const sc = sigColor[s.signal] || '#94a3b8';
            const em = sigEmoji[s.signal] || '📌';
            const score = s.score || 0;
            const maxScore = 42;
            const pct = Math.min(score / maxScore * 100, 100);
            return (
              <div key={s.stock_code} style={{padding:'1rem',borderRadius:'10px',
                background:`${sc}08`,border:`1px solid ${sc}35`}}>
                {/* 헤더 */}
                <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.6rem',flexWrap:'wrap'}}>
                  <span style={{fontSize:'1rem'}}>{em}</span>
                  <span style={{fontWeight:700,fontSize:'0.95rem'}}>{s.stock_name || s.stock_code}</span>
                  <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{s.stock_code}</span>
                  <MktBadge market={s.market} mktcap={s.mktcap} />
                  <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.68rem',fontWeight:700,
                    background:`${sc}25`,color:sc,border:`1px solid ${sc}55`}}>
                    {s.signal}
                  </span>
                  <div style={{marginLeft:'auto',display:'flex',gap:'0.4rem',alignItems:'center'}}>
                    <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                      style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                        background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                        cursor:'pointer',fontSize:'0.72rem'}}>
                      분석
                    </button>
                  </div>
                </div>
                {/* 종합 점수 바 */}
                <div style={{display:'flex',alignItems:'center',gap:'0.6rem',marginBottom:'0.55rem'}}>
                  <span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.45)',whiteSpace:'nowrap'}}>종합점수</span>
                  <div style={{flex:1,height:'7px',borderRadius:'4px',background:'rgba(255,255,255,0.07)',overflow:'hidden'}}>
                    <div style={{width:`${pct}%`,height:'100%',borderRadius:'4px',
                      background:`linear-gradient(90deg, ${sc}, ${sc}aa)`}}/>
                  </div>
                  <span style={{fontSize:'0.82rem',fontWeight:700,color:sc,whiteSpace:'nowrap'}}>
                    {score}<span style={{fontSize:'0.6rem',opacity:0.6}}>/{maxScore}</span>
                  </span>
                </div>
                {/* 트랙 점수 */}
                <div style={{display:'flex',gap:'0.4rem',flexWrap:'wrap',marginBottom:'0.5rem'}}>
                  {[
                    {key:'track_s', label:'📡 수급', max:18, color:'#818cf8'},
                    {key:'track_t', label:'📈 추세', max:12, color:'#2dd4bf'},
                    {key:'track_q', label:'📊 실적', max:8,  color:'#f59e0b'},
                    {key:'track_r', label:'💪 RS',   max:4,  color:'#22c55e'},
                  ].filter(t => s[t.key] != null).map(({key,label,max,color}) => {
                    const v = s[key] || 0;
                    const p2 = Math.min(v/max*100,100);
                    return (
                      <div key={key} style={{padding:'0.25rem 0.6rem',borderRadius:'6px',
                        background:`${color}10`,border:`1px solid ${color}30`,
                        display:'flex',alignItems:'center',gap:'0.35rem'}}>
                        <span style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.5)'}}>{label}</span>
                        <div style={{width:'40px',height:'4px',borderRadius:'2px',background:'rgba(255,255,255,0.06)'}}>
                          <div style={{width:`${p2}%`,height:'100%',borderRadius:'2px',background:color}}/>
                        </div>
                        <span style={{fontSize:'0.75rem',fontWeight:700,color}}>{v}</span>
                      </div>
                    );
                  })}
                  {s.price && (
                    <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                      background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.08)',
                      display:'flex',alignItems:'center',gap:'0.3rem'}}>
                      <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>현재가</span>
                      <span style={{fontSize:'0.78rem',fontWeight:600}}>{s.price.toLocaleString('ko-KR')}원</span>
                    </div>
                  )}
                </div>
                {/* 시그널 태그 */}
                <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                  {(s.reasons || []).slice(0,6).map((r,i) => (
                    <span key={i} style={{padding:'0.12rem 0.4rem',borderRadius:'4px',fontSize:'0.65rem',
                      background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.6)',
                      border:'1px solid rgba(255,255,255,0.08)'}}>
                      {r}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}

          {/* AI 적극추천(2) 원리 설명 — 항상 표시 */}
          <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
            background:'rgba(99,102,241,0.03)',border:'1px solid rgba(99,102,241,0.12)',
            borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
            <div style={{fontWeight:700,color:'#818cf8',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
              📡 Logic v2 — 선정 원리 (수급 주도 모멘텀, 최대 42점)
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
              {[
                ['Track S — 수급 (×3, 최대18점)', '기관+외국인 3일 연속 동반매수(최고등급), 단독 5일, 10일 누적금액 기준으로 수급 강도 점수화. 가장 높은 가중치(3배).'],
                ['Track T — 추세 (×2, 최대12점)', '일봉 정배열(MA5>20>60>120>240) 4점 + 주봉 MA40/MA80 정배열 2점. 단기·중기 동시 추세 확인.'],
                ['Track Q — 실적 (×2, 최대8점)', '최근 분기 YoY 영업이익 성장 연속 유지 여부. 턴어라운드 및 지속 성장주 포착.'],
                ['Track R — 상대강도 (×1, 최대4점)', '21일·63일·126일 3구간에서 KOSPI 대비 초과상승 여부. 3구간 모두 우세 시 최고점.'],
                ['강력추천 조건 (🔥)', '총점 ≥28 + 수급점수(S) ≥12 + 추세점수(T) ≥8. 수급과 추세 모두 강한 경우만 선정.'],
                ['추천 조건 (⭐)', '총점 ≥20 + S≥9 + T≥6. 관심 조건: 총점 ≥13 + S≥6.'],
                ['시장 필터', 'KOSPI MA60 하락장 진입 시 모든 점수 ×0.75 패널티. 하락장에서 자동 등급 하향.'],
                ['Logic v1과의 차이', 'v1은 재무·가치·추세 교집합 → 실적 우량주 중심. v2는 수급 주도 → 세력·기관 매집 포착. 단기 모멘텀에 강함.'],
              ].map(([title, desc]) => (
                <div key={title}>
                  <span style={{color:'rgba(129,140,248,0.9)',fontWeight:600}}>{title}</span>
                  <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
                </div>
              ))}
            </div>
          </div>
          </>)}

        </div>
        );
      })()}

    </div>
    );
  };

  // ── Peak 전략 뷰 (독립 컴포넌트) ─────────────────────────────
  const PeakView = () => {
    const [peakData, setPeakData]     = React.useState({ holdings: [], exits: [] });
    const [summary,  setSummary]      = React.useState(null);
    const [trades,   setTrades]       = React.useState([]);
    const [peakTab,  setPeakTab]      = React.useState('holdings');
    const [loading,  setPeakLoading]  = React.useState(true);
    const [lastSync, setLastSync]     = React.useState('');
    const [strategy, setStrategy]     = React.useState('peak');

    // AI 추천 탭 전용 state — 최상위에 위치해야 hooks 규칙 준수
    const [aiHoldings, setAiHoldings] = React.useState([]);
    const [aiLoading,  setAiLoading]  = React.useState(false);
    const [aiSubTab,   setAiSubTab]   = React.useState('holdings'); // AI탭 서브탭
    const [v18Data, setV18Data] = React.useState(null);

    const loadPeak = async () => {
      setPeakLoading(true);
      try {
        const [hRes, tRes, sRes] = await Promise.all([
          fetch(API('/api/trend/holdings')),
          fetch(API('/api/trend/trades')),
          fetch(API('/api/trend/summary')),
        ]);
        const all    = hRes.ok ? await hRes.json() : [];
        const active = all.filter(h => h.is_active);
        const exited = all.filter(h => !h.is_active);
        setPeakData({ holdings: active, exits: exited });
        if (tRes.ok) setTrades(await tRes.json());
        if (sRes.ok) setSummary(await sRes.json());
        setLastSync(new Date().toLocaleTimeString('ko-KR'));
      } catch(e) { console.error(e); }
      finally { setPeakLoading(false); }
    };

    const loadAiHoldings = () => {
      setAiLoading(true);
      fetch(API('/api/trend/ai-holdings'))
        .then(r => r.ok ? r.json() : [])
        .then(d => setAiHoldings(d))
        .catch(() => {})
        .finally(() => setAiLoading(false));
    };

    React.useEffect(() => {
      loadPeak();
      loadAiHoldings();
      // 장중에만 10분 폴링 (장외·주말엔 데이터 변화 없음)
      const iv = isKRMarketOpen() ? setInterval(loadPeak, 600000) : null;
      return () => { if (iv) clearInterval(iv); };
    }, []);

    const loadV18 = async () => {
      try {
        const r = await fetch(API('/api/trend/v18/recommendations'));
        if (r.ok) setV18Data(await r.json());
      } catch (e) { console.error(e); }
    };

    const fp = (v) => v != null ? Math.round(v).toLocaleString('ko-KR') : '-';
    const pc = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.35)';
    const pf = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + fp(v);

    const tabBtn = (key, label, count) => (
      <button key={key} onClick={() => setPeakTab(key)} style={{
        padding: '0.35rem 0.9rem', borderRadius: '7px', fontSize: '0.82rem',
        cursor: 'pointer', fontWeight: peakTab === key ? 700 : 400,
        border:     peakTab === key ? '1px solid var(--accent-purple)' : '1px solid var(--glass-border)',
        background: peakTab === key ? 'rgba(167,139,250,0.15)' : 'transparent',
        color:      peakTab === key ? 'var(--accent-purple)' : 'var(--text-secondary)',
        display: 'flex', alignItems: 'center', gap: '0.4rem',
      }}>
        {label}
        {count != null && (
          <span style={{ fontSize: '0.7rem', padding: '0.05rem 0.4rem',
            background: 'rgba(167,139,250,0.2)', borderRadius: '10px' }}>{count}</span>
        )}
      </button>
    );

    // 현재 전략에 해당하는 보유/이탈 종목만 필터링
    // ai_rec 탭 선택 시 → AI 탭 전용 (ai_combo strategy)
    // 그 외 전략 → 해당 strategy 종목만 표시 (ai_combo 제외)
    const curHoldings = strategy === 'ai_rec'
      ? aiHoldings.filter(h => h.is_active)
      : peakData.holdings.filter(h => h.strategy === strategy);
    const curExits = strategy === 'ai_rec'
      ? aiHoldings.filter(h => !h.is_active)
      : peakData.exits.filter(h => h.strategy === strategy);
    const curTrades = strategy === 'ai_rec'
      ? trades.filter(t => t.strategy === 'ai_combo')
      : trades.filter(t => t.strategy === strategy);

    // 요약 카드 — 현재 전략 기준 집계
    const SummaryCards = () => {
      const realProfit  = curExits.reduce((s,h)=>s+(h.profit||0),0);
      const wins        = curExits.filter(h=>(h.profit||0)>0).length;
      const winRate     = curExits.length > 0 ? Math.round(wins/curExits.length*100) : null;
      const totalValue  = curHoldings.reduce((s,h)=>s+(h.total_value||(h.buy_price||0)*(h.quantity||0)),0);
      const totalProfit = curHoldings.reduce((s,h)=>s+(h.profit||0),0);
      const costBasis   = totalValue - totalProfit;
      const roi         = costBasis > 0 ? (totalProfit / costBasis * 100) : null;
      return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
        {[
          { label: '투입원금',      val: fp(costBasis)+'원',                            color: 'inherit' },
          { label: '보유 총액',     val: fp(totalValue)+'원',                           color: 'inherit' },
          { label: '평가 손익',     val: pf(totalProfit)+'원',                          color: pc(totalProfit) },
          { label: '수익률',        val: roi != null ? (roi>=0?'+':'')+roi.toFixed(1)+'%' : '-', color: pc(roi||0) },
          { label: '누적 실현 손익', val: pf(realProfit)+'원',                          color: pc(realProfit||0) },
          { label: '승률',          val: winRate != null ? `${winRate}%` : '-',         color: 'var(--accent-purple)' },
        ].map(({ label, val, color }) => (
          <div key={label} className="glass-panel" style={{ padding: '0.9rem 1rem', minWidth: 0 }}>
            <p style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.3rem', whiteSpace:'nowrap' }}>{label}</p>
            <p style={{ fontSize: '0.9rem', fontWeight: 700, color, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{val}</p>
          </div>
        ))}
      </div>
      );
    };

    const STRATEGIES = [
      { key:'peak',     label:'Peak Easy',  color:'#a78bfa' },
      { key:'momentum', label:'모멘텀 Easy', color:'#34d399' },
      { key:'value',    label:'벨류 Easy',   color:'#60a5fa' },
      { key:'ai_rec',   label:'⭐ AI 추천',  color:'#ef4444' },
      { key:'gpt_v18',  label:'🤖 GPT추천(V18)', color:'#22c55e' },
    ];

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        {/* 안내 배너 */}
        <div style={{padding:'0.4rem 0.9rem',background:'rgba(251,191,36,0.07)',
          border:'1px solid rgba(251,191,36,0.25)',borderRadius:'8px',
          fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.4}}>
          ⚠️ Stock Easy 사이트내 전략종목을 파씽해오는 종목임을 안내 드립니다.
        </div>
        {/* 헤더 */}
        <div className="glass-panel" style={{ padding: '1rem 1.4rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap:'wrap', gap:'0.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            {STRATEGIES.map(s => {
              const isAiRec = s.key === 'ai_rec';
              const isActive = strategy === s.key;
              return (
                <button key={s.key} onClick={() => {
                  setStrategy(s.key);
                  // 전략 전환 시 보유종목 탭으로 초기화
                  if (peakTab === 'history') setPeakTab('holdings');
                }} style={{
                  padding: '0.35rem 0.9rem', borderRadius: '7px', fontSize: '0.82rem', cursor: 'pointer',
                  fontWeight: isActive ? 700 : 500,
                  border:     `1px solid ${s.color}${isActive ? 'cc' : '55'}`,
                  background: isActive ? `${s.color}33` : `${s.color}11`,
                  color:      isActive ? '#ffffff' : s.color,
                }}>{s.label}</button>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap:'wrap' }}>
            {tabBtn('holdings', '보유 종목', curHoldings.length)}
            {tabBtn('exits',    '이탈 종목', curExits.length)}
            {tabBtn('history',  '매매 내역', null)}
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:'0.5rem' }}>
            <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>마지막: {lastSync || '로딩중...'}</span>
            <button onClick={loadPeak} style={{
              padding: '0.35rem 0.9rem', borderRadius: '7px', fontSize: '0.8rem',
              background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.35)',
              color: 'var(--accent-purple)', cursor: 'pointer',
            }}>새로고침</button>
          </div>
        </div>
        {/* 요약 카드 */}
        <SummaryCards />

        {loading && (
          <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--accent-purple)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.6rem' }}>
              <div style={{ width: '14px', height: '14px', borderRadius: '50%', border: '2px solid var(--accent-purple)', borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
              Peak 데이터 로딩 중...
            </div>
          </div>
        )}

        {/* ══ 보유 종목 탭 ══ */}
        {!loading && peakTab === 'holdings' && (
          curHoldings.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <TrendingUp size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.3 }} />
              <p style={{ fontSize: '1rem', fontWeight: 600, color: 'rgba(255,255,255,0.5)' }}>현재 [{strategy === 'peak' ? 'Peak Easy' : strategy === 'momentum' ? '모멘텀 Easy' : strategy === 'value' ? '벨류 Easy' : strategy === 'gpt_v18' ? 'GPT추천(V18)' : 'AI 추천'}] 전략 보유 종목이 없습니다.</p>
              <p style={{ fontSize: '0.8rem', marginTop: '0.6rem' }}>{strategy === 'ai_rec' ? 'AI 자동매매 즉시 실행 후 종목이 등록됩니다.' : '추세 매수 시그널 발생 시 자동으로 등록됩니다.'}</p>
              <p style={{ fontSize: '0.75rem', marginTop: '0.3rem', color: 'rgba(255,255,255,0.25)' }}>이탈 종목 {curExits.length}건</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'clip' }}>
              <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--glass-border)', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                updated {lastSync}  ·  가상매수 한도: 1,000만원/종목
              </div>
              <table className="premium-table">
                <thead><tr>
                  <th>종목명</th>
                  <th style={{ textAlign: 'right' }}>매수가</th>
                  <th style={{ textAlign: 'right' }}>현재가</th>
                  <th style={{ textAlign: 'right' }}>편입일</th>
                  <th style={{ textAlign: 'right' }}>보유일</th>
                  <th style={{ textAlign: 'right' }}>수익률</th>
                  <th style={{ textAlign: 'right' }}>수익금</th>
                  <th style={{ textAlign: 'right' }}>평가액</th>
                </tr></thead>
                <tbody>
                  {curHoldings.map(h => {
                    const pct = h.profit_pct || 0;
                    return (
                      <tr key={h.id}>
                        <td style={{ fontWeight: 700 }}>
                          {h.stock_name}
                          {strategy === 'ai_rec' && h.stock_code && h.stock_code !== 'None' && (
                            <button onClick={()=>{changeStock(h.stock_code);changeTab('analysis');}}
                              style={{marginLeft:'0.4rem',padding:'0.1rem 0.35rem',borderRadius:'4px',border:'none',
                                background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.65rem'}}>
                              분석
                            </button>
                          )}
                        </td>
                        <td style={{ textAlign: 'right' }}>{fp(h.buy_price)}</td>
                        <td style={{ textAlign: 'right', color: pc(h.daily_change_pct || 0), fontWeight: 600 }}>
                          {fp(h.current_price)}
                          <span style={{marginLeft:'0.35rem',fontSize:'0.72rem',color:pc(h.daily_change_pct || 0)}}>
                            {(h.daily_change_pct || 0) >= 0 ? '+' : ''}{(h.daily_change_pct || 0).toFixed(1)}%
                          </span>
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                          {h.entry_date ? h.entry_date.slice(5).replace('-','/') : '-'}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <span style={{ padding: '0.1rem 0.5rem', borderRadius: '12px',
                            background: 'rgba(167,139,250,0.12)', fontSize: '0.78rem', color: 'var(--accent-purple)' }}>
                            {h.entry_date
                              ? Math.floor((Date.now() - new Date(h.entry_date).getTime()) / 86400000)
                              : (h.hold_days ?? 0)}일
                          </span>
                        </td>
                        <td style={{ textAlign: 'right', color: pc(pct), fontWeight: 700 }}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </td>
                        <td style={{ textAlign: 'right', color: pc(h.profit), fontWeight: 600 }}>
                          {pf(h.profit)}원
                        </td>
                        <td style={{ textAlign: 'right' }}>{fp(h.total_value)}원</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}



        {/* ══ 이탈 종목 탭 ══ */}
        {!loading && peakTab === 'exits' && (
          curExits.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <p>이탈 종목이 없습니다.</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'clip' }}>
              <table className="premium-table">
                <thead><tr>
                  <th>종목명</th>
                  <th style={{ textAlign: 'right' }}>매수가</th>
                  <th style={{ textAlign: 'right' }}>매도가</th>
                  <th style={{ textAlign: 'right' }}>편입일</th>
                  <th style={{ textAlign: 'right' }}>보유일</th>
                  <th style={{ textAlign: 'right' }}>수익률</th>
                  <th style={{ textAlign: 'right' }}>수익금</th>
                  <th style={{ textAlign: 'right' }}>이탈시각</th>
                </tr></thead>
                <tbody>
                  {curExits.map(h => {
                    const pct = h.profit_pct || 0;
                    const profit = Math.round(((h.sell_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0));
                    return (
                      <tr key={h.id}>
                        <td><span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{h.sector}</span></td>
                        <td style={{ fontWeight: 700 }}>{h.stock_name}</td>
                        <td style={{ textAlign: 'right' }}>{fp(h.buy_price)}</td>
                        <td style={{ textAlign: 'right', fontWeight: 600 }}>{fp(h.sell_price)}</td>
                        <td style={{ textAlign: 'right', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                          {h.entry_date ? h.entry_date.slice(5).replace('-','/') : '-'}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <span style={{ padding: '0.1rem 0.5rem', borderRadius: '12px',
                            background: 'rgba(255,255,255,0.06)', fontSize: '0.78rem' }}>
                            {h.entry_date
                              ? Math.floor((Date.now() - new Date(h.entry_date).getTime()) / 86400000)
                              : (h.hold_days ?? 0)}일
                          </span>
                        </td>
                        <td style={{ textAlign: 'right', color: pc(pct), fontWeight: 700 }}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </td>
                        <td style={{ textAlign: 'right', color: pc(profit), fontWeight: 600 }}>
                          {pf(profit)}원
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                          {h.sold_at || '-'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}

        {/* ══ 상태 확인 탭 — 전체 포지션 현황 ══ */}
        {!loading && peakTab === 'status' && (() => {
          const allPos = [...peakData.holdings, ...peakData.exits]
            .sort((a,b) => (b.entry_date||'').localeCompare(a.entry_date||''));
          return allPos.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <p>포지션 내역이 없습니다.</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'clip' }}>
              <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--glass-border)', display: 'flex', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                <span>전체 {allPos.length}건</span>
                <span style={{ color: '#34d399' }}>보유중 {peakData.holdings.length}건</span>
                <span style={{ color: 'rgba(255,255,255,0.4)' }}>매도완료 {peakData.exits.length}건</span>
              </div>
              <table className="premium-table">
                <thead><tr>
                  <th>상태</th><th>종목명</th>
                  <th style={{ textAlign: 'right' }}>매수가</th>
                  <th style={{ textAlign: 'right' }}>매도가</th>
                  <th style={{ textAlign: 'right' }}>수량</th>
                  <th style={{ textAlign: 'right' }}>수익률</th>
                  <th style={{ textAlign: 'right' }}>수익금</th>
                  <th style={{ textAlign: 'right' }}>편입일</th>
                  <th style={{ textAlign: 'right' }}>이탈/갱신</th>
                </tr></thead>
                <tbody>
                  {allPos.map(h => {
                    const isActive = h.is_active;
                    const pct = h.profit_pct || 0;
                    const profit = isActive
                      ? Math.round(((h.current_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0))
                      : Math.round(((h.sell_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0));
                    return (
                      <tr key={h.id} style={{ opacity: isActive ? 1 : 0.65 }}>
                        <td>
                          <span style={{
                            padding: '0.15rem 0.6rem', borderRadius: '12px', fontSize: '0.72rem', fontWeight: 700,
                            background: isActive ? 'rgba(52,211,153,0.15)' : 'rgba(255,255,255,0.07)',
                            color:      isActive ? '#34d399'               : 'rgba(255,255,255,0.45)',
                            border:     isActive ? '1px solid rgba(52,211,153,0.3)' : '1px solid rgba(255,255,255,0.12)',
                          }}>
                            {isActive ? '보유중' : '매도완료'}
                          </span>
                        </td>
                        <td style={{ fontWeight: 700 }}>{h.stock_name}</td>
                        <td style={{ textAlign: 'right' }}>{fp(h.buy_price)}</td>
                        <td style={{ textAlign: 'right', color: isActive ? 'rgba(255,255,255,0.3)' : 'inherit' }}>
                          {isActive ? fp(h.current_price) : fp(h.sell_price)}
                        </td>
                        <td style={{ textAlign: 'right' }}>{(h.quantity||0).toLocaleString('ko-KR')}주</td>
                        <td style={{ textAlign: 'right', color: pc(pct), fontWeight: 600 }}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </td>
                        <td style={{ textAlign: 'right', color: pc(profit), fontWeight: 600 }}>
                          {pf(profit)}원
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                          {h.entry_date ? h.entry_date.slice(5).replace('-','/') : '-'}
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
                          {isActive ? (h.updated_at||'-') : (h.sold_at||'-')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })()}

        {/* ══ AI 추천 탭 — 안내 배너 (보유/이탈/매매내역은 공통 탭에서 처리) ══ */}
        {!loading && strategy === 'ai_rec' && (
          <div style={{padding:'0.65rem 1rem',
            background:'linear-gradient(135deg,rgba(239,68,68,0.08),rgba(245,158,11,0.06))',
            border:'1px solid rgba(239,68,68,0.25)',borderRadius:'8px',
            fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.7,
            display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
            <span>
              <span style={{fontWeight:700,color:'#ef4444',marginRight:'0.4rem'}}>⭐ AI 적극검토 자동매매</span>
              추세추종+가치매수+재무스크리너 <strong>2개↑</strong> 동시충족 →
              <strong>1,000만원</strong> 가상매수 → MA20 2일연속 이탈 / MA60붕괴 / 하드손절(-20%) 시 매도
            </span>
            <div style={{display:'flex',alignItems:'center',gap:'0.6rem',flexShrink:0}}>
              <button onClick={loadAiHoldings} style={{
                padding:'0.3rem 0.65rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',
              }}>🔄 새로고침</button>
            </div>
          </div>
        )}

        {!loading && strategy === 'gpt_v18' && (
          <>
          <div style={{padding:'0.65rem 1rem',
            background:'linear-gradient(135deg,rgba(34,197,94,0.1),rgba(45,212,191,0.08))',
            border:'1px solid rgba(34,197,94,0.28)',borderRadius:'8px',
            fontSize:'0.72rem',color:'rgba(255,255,255,0.68)',lineHeight:1.7,
            display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
            <span>
              <span style={{fontWeight:700,color:'#22c55e',marginRight:'0.4rem'}}>🤖 GPT추천(V18.1)</span>
              장중 10분마다 매수/매도 추천 및 가상매매 실행 · 종목당 1,200만원 기준 · 하드 스탑 -10%
            </span>
            <div style={{display:'flex',gap:'0.45rem'}}>
              <button onClick={loadV18} style={{
                padding:'0.3rem 0.65rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',
              }}>추천 갱신</button>
              <button onClick={async()=>{await fetch(API('/api/trend/v18/execute'),{method:'POST'}); loadPeak(); loadV18();}} style={{
                padding:'0.3rem 0.65rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                background:'rgba(34,197,94,0.15)',border:'1px solid rgba(34,197,94,0.35)',color:'#22c55e',
              }}>즉시 실행</button>
            </div>
          </div>
          {/* ── V18.1 전략 설명 (가상매매 탭) ── */}
          <div style={{padding:'0.8rem 1rem',borderRadius:'10px',background:'rgba(255,255,255,0.02)',border:'1px solid rgba(255,255,255,0.07)',fontSize:'0.71rem',color:'rgba(255,255,255,0.42)',lineHeight:1.75}}>
            <div style={{fontWeight:700,color:'rgba(255,255,255,0.55)',marginBottom:'0.45rem',fontSize:'0.73rem'}}>📋 V18.1 전략 개요</div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:'0.5rem 1rem'}}>
              <div>
                <span style={{color:'rgba(255,255,255,0.55)',fontWeight:600}}>백테스트 성과</span><br/>
                누적수익 <span style={{color:'#22c55e',fontWeight:700}}>+601%</span> · MDD <span style={{color:'#f59e0b'}}>-26.3%</span><br/>
                KOSPI +110% 대비 <span style={{color:'#22c55e'}}>+491%p 초과</span><br/>
                6/6 구간 전부 시장 초과 달성
              </div>
              <div>
                <span style={{color:'rgba(255,255,255,0.55)',fontWeight:600}}>매수 로직</span><br/>
                추세·가치·재무 중 <strong style={{color:'rgba(255,255,255,0.65)'}}>2개↑ 동시 충족</strong><br/>
                3개 일치(combo_3way) 종목 최우선<br/>
                종합점수 순 최대 15종목 추출
              </div>
              <div>
                <span style={{color:'rgba(255,255,255,0.55)',fontWeight:600}}>매도 로직</span><br/>
                하드 스탑: <span style={{color:'#ef4444',fontWeight:700}}>-10%</span> 손절<br/>
                추세 이탈: MA20↓ + MA20&lt;MA60<br/>
                또는 현재가 &lt; MA60×98.5%
              </div>
            </div>
            <div style={{marginTop:'0.4rem',paddingTop:'0.35rem',borderTop:'1px solid rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.25)',fontSize:'0.66rem'}}>
              Codex 스윕 2,592건 최적화 · dd_cut -10% · ext_ticket 30% · ext_max 60M · 비용 35bp 시뮬에서도 +412% 유지
            </div>
          </div>
          </>
        )}

        {/* ══ 매매 내역 탭 ══ */}
        {!loading && peakTab === 'history' && (
          <div style={{display:'flex',flexDirection:'column',gap:'0.5rem'}}>
            {/* 삭제 버튼 행 */}
            <div style={{display:'flex',justifyContent:'flex-end'}}>
              <button onClick={async () => {
                if (!window.confirm('매매 내역을 모두 삭제하시겠습니까?')) return;
                await fetch(API('/api/trend/trades/all'), { method: 'DELETE' });
                setTrades([]);
              }} style={{padding:'0.3rem 0.8rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                border:'1px solid rgba(239,68,68,0.35)',background:'rgba(239,68,68,0.08)',color:'#ef4444'}}>
                🗑 전체 삭제
              </button>
            </div>
            {(() => {
              const filteredTrades = strategy === 'ai_rec'
                ? trades.filter(t => t.strategy === 'ai_combo')
                : trades.filter(t => t.strategy === strategy || (!t.strategy && strategy === 'peak'));
              return filteredTrades.length === 0 ? (
                <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
                  <p>매매 내역이 없습니다.</p>
                </div>
              ) : (
                <div className="glass-panel" style={{ overflow: 'clip' }}>
                  <table className="premium-table">
                    <thead><tr>
                      <th>구분</th><th>종목명</th>
                      <th style={{ textAlign: 'right' }}>가격</th>
                      <th style={{ textAlign: 'right' }}>수량</th>
                      <th style={{ textAlign: 'right' }}>거래금액</th>
                      <th style={{ textAlign: 'right' }}>손익</th>
                      <th style={{ textAlign: 'right' }}>수익률</th>
                      <th style={{ textAlign: 'right' }}>시각</th>
                    </tr></thead>
                    <tbody>
                      {filteredTrades.map(t => (
                        <tr key={t.id}>
                          <td>
                            <span style={{ padding: '0.15rem 0.6rem', borderRadius: '12px', fontSize: '0.75rem', fontWeight: 700,
                              background: t.tx_type === 'buy' ? 'rgba(239,68,68,0.15)' : 'rgba(59,130,246,0.15)',
                              color:      t.tx_type === 'buy' ? '#ef4444' : '#3b82f6' }}>
                              {t.tx_type === 'buy' ? '매수' : '매도'}
                            </span>
                          </td>
                          <td style={{ fontWeight: 600 }}>{t.stock_name}</td>
                          <td style={{ textAlign: 'right' }}>{fp(t.price)}</td>
                          <td style={{ textAlign: 'right' }}>{(t.quantity||0).toLocaleString('ko-KR')}주</td>
                          <td style={{ textAlign: 'right' }}>{fp(t.total_amount)}원</td>
                          <td style={{ textAlign: 'right', color: pc(t.profit||0), fontWeight: t.profit != null ? 700 : 400 }}>
                            {t.profit != null ? pf(t.profit)+'원' : '-'}
                          </td>
                          <td style={{ textAlign: 'right', color: pc(t.profit_pct||0) }}>
                            {t.profit_pct != null ? (t.profit_pct>=0?'+':'')+t.profit_pct.toFixed(1)+'%' : '-'}
                          </td>
                          <td style={{ textAlign: 'right', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{t.tx_at}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            })()}
          </div>
        )}
      </div>
    );
  };


  // ── 계좌현황 ─────────────────────────────────────────────
  const PortfolioView = () => {
    const [portfolio, setPortfolio]     = React.useState([]);
    const [transactions, setTransactions] = React.useState([]);
    const [tab, setTab]                 = React.useState("holdings");
    const [kakaoText, setKakaoText]     = React.useState("");
    const [parsedTx, setParsedTx]       = React.useState([]);
    const [showModal, setShowModal]     = React.useState(false);
    // 더블클릭 인라인 편집
    const [editRow, setEditRow]         = React.useState(null);
    const [editForm, setEditForm]       = React.useState({});
    const [editCodePreview, setEditCodePreview] = React.useState(null); // 검색된 새 코드 미리보기
    const [form, setForm] = React.useState({
      stock_code:"", stock_name:"", sector:"", tx_type:"buy",
      quantity:"", price:"", tx_date:"", memo:""
    });
    const [autoSummary, setAutoSummary] = React.useState(null);
    const [autoLoading, setAutoLoading] = React.useState(false);
    const [autoError, setAutoError] = React.useState('');

    const load = async () => {
      const p = await fetch(API('/api/portfolio')).then(r=>r.ok?r.json():[]);
      setPortfolio(p);
    };
    const loadTransactions = async () => {
      const t = await fetch(API('/api/portfolio/transactions')).then(r=>r.ok?r.json():[]);
      setTransactions(t);
    };

    // ── 실시간 1분 폴링: /api/realtime/prices 로 현재가·손익만 갱신 ──
    const [realtimeMeta, setRealtimeMeta] = React.useState({ updated_at: '', market_open: false });
    React.useEffect(() => {
      load();

      const applyRealtime = async () => {
        try {
          const res = await fetch(API('/api/realtime/prices'));
          if (!res.ok) return;
          const rt = await res.json();
          setRealtimeMeta({ updated_at: rt.updated_at, market_open: rt.market_open });
          if (rt?.summary) setRtSummary(rt.summary);
          setPortfolio(prev => prev.map(h => {
            const r = rt.holdings[h.stock_code];
            if (!r) return h;
            return { ...h,
              current_price: r.current_price,
              change_pct:    r.change_pct,
              profit:        r.profit,
              profit_pct:    r.profit_pct,
              total_value:   r.total_value,
              buy_total:     r.buy_total,
            };
          }));
        } catch {}
      };

      // 장 시간: 1분 / 장 외: 5분 (초기 1회는 즉시)
      applyRealtime();
      const iv=isKRMarketOpen()?setInterval(applyRealtime,60000):null;
      return ()=>{if(iv)clearInterval(iv);};
    }, []);

    React.useEffect(() => {
      if (tab === 'tx' && transactions.length === 0) {
        loadTransactions();
      }
    }, [tab]);

    // 섹터 그룹핑
    const groups = React.useMemo(()=>{
      const g = {};
      portfolio.forEach(h=>{ const s=h.sector||"기타"; if(!g[s]) g[s]=[]; g[s].push(h); });
      // 섹터 내 종목: 평가금액 내림차순 정렬
      Object.keys(g).forEach(s => g[s].sort((a,b) => b.total_value - a.total_value));
      // 섹터 자체도 섹터 합계 평가금액 내림차순
      return Object.fromEntries(
        Object.entries(g).sort((a,b) => {
          const sa = a[1].reduce((s,h)=>s+h.total_value,0);
          const sb = b[1].reduce((s,h)=>s+h.total_value,0);
          return sb - sa;
        })
      );
    }, [portfolio]);

    // 총합: realtime API summary 우선, 없으면 portfolio 로컬 합산
    const [rtSummary, setRtSummary] = React.useState(null);

    const totalBuy       = rtSummary?.total_buy    ?? portfolio.reduce((s,h)=>s+h.buy_total, 0);
    const totalVal       = rtSummary?.total_value   ?? portfolio.reduce((s,h)=>s+h.total_value, 0);
    const totalProfit    = rtSummary?.total_profit  ?? portfolio.reduce((s,h)=>s+h.profit, 0);
    const totalProfitPct = rtSummary?.total_profit_pct
      ?? (totalBuy > 0 ? ((totalVal-totalBuy)/totalBuy*100).toFixed(2) : 0);
    // ── [버그 ② 수정] 전일 대비 당일 손익 ──────────────────────
    const dailyProfit    = rtSummary?.daily_profit
      ?? portfolio.reduce((s,h) => s + (h.daily_profit ?? 0), 0);
    const dailyProfitPct = rtSummary?.daily_profit_pct
      ?? (totalVal > 0 && dailyProfit !== 0 ? ((dailyProfit / (totalVal - dailyProfit)) * 100).toFixed(2) : null);
    const hasDailyData   = dailyProfit !== 0 || rtSummary?.daily_profit != null;

    const fp  = v => v!=null ? Math.round(v).toLocaleString('ko-KR') : '-';
    const pct = v => v!=null ? `${v>0?'+':''}${v}%` : '-';
    const pc  = v => v>0?'#ef4444':v<0?'#3b82f6':'inherit';

    // ── 더블클릭 편집 ──────────────────────────────────────────
    const startEdit = (h) => {
      setEditRow(h.stock_code);
      setEditCodePreview(null);
      setEditForm({
        stock_name: h.stock_name,
        sector:     h.sector || "",
        avg_price:  h.avg_price,
        quantity:   h.quantity,
      });
    };

    const cancelEdit = () => { setEditRow(null); setEditForm({}); setEditCodePreview(null); };

    // 종목명 변경 시 티커 미리보기
    const handleNameChange = async (name) => {
      setEditForm(p => ({...p, stock_name: name}));
      if (name.length < 2) { setEditCodePreview(null); return; }
      try {
        const r = await fetch(API(`/api/search?q=${encodeURIComponent(name)}`));
        if (r.ok) {
          const results = await r.json();
          setEditCodePreview(results.length > 0 ? results[0] : null);
        }
      } catch {}
    };

    const saveEdit = async (stock_code) => {
      // PUT API: 종목명 변경 시 ticker_mapper로 새 코드 자동 조회
      const res = await fetch(API(`/api/portfolio/${stock_code}`), {
        method: 'PUT',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          stock_name: editForm.stock_name,
          sector:     editForm.sector,
          quantity:   Number(editForm.quantity),
          avg_price:  Number(editForm.avg_price),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        // ── [버그 ① 수정] 저장 즉시 로컬 state 먼저 반영 (load() 재호출로 인한 원복 방지) ──
        const newCode = data.code_changed ? data.new_code : stock_code;
        setPortfolio(prev => prev.map(h => {
          if (h.stock_code !== stock_code) return h;
          const updated = {
            ...h,
            stock_name: data.stock_name || editForm.stock_name,
            sector:     editForm.sector,
            quantity:   Number(editForm.quantity),
            avg_price:  Number(editForm.avg_price),
            stock_code: newCode,
          };
          // 수량 변경 시 buy_total / total_value / profit 재계산
          updated.buy_total   = updated.avg_price * updated.quantity;
          updated.total_value = (h.current_price || h.avg_price) * updated.quantity;
          updated.profit      = updated.total_value - updated.buy_total;
          updated.profit_pct  = updated.buy_total > 0
            ? Number(((updated.total_value - updated.buy_total) / updated.buy_total * 100).toFixed(2))
            : 0;
          return updated;
        }));
        if (data.code_changed) {
          alert(`종목코드가 변경되었습니다: ${stock_code} → ${data.new_code} (${data.stock_name})\n주가 데이터를 백그라운드에서 수집합니다.`);
          fetchWatchlist();
        }
        // 서버 저장 완료 후 1.5초 뒤 재동기화 (즉시 load() 하면 서버 반영 전 원복될 수 있음)
        setTimeout(load, 1500);
      }
      setEditRow(null); setEditForm({}); setEditCodePreview(null);
    };

    // ── 카카오 파싱 ────────────────────────────────────────────
    const handleKakaoParse = async () => {
      const res = await fetch(API('/api/portfolio/kakao-parse'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text: kakaoText}),
      });
      if(res.ok){ const d=await res.json(); setParsedTx(d.parsed||[]); }
    };

    const applyParsed = async (item) => {
      if(!item.valid) return;
      await fetch(API('/api/portfolio/transaction'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({stock_code:item.stock_code, stock_name:item.stock_name,
          tx_type:item.tx_type, quantity:item.quantity, price:item.price, memo:item.raw}),
      });
      load(); setParsedTx(prev=>prev.filter(p=>p!==item));
    };

    const saveTx = async () => {
      if(!form.stock_code || !form.quantity || !form.price) {
        alert('종목코드, 수량, 단가는 필수 입력 항목입니다.');
        return;
      }
      const qty   = Number(form.quantity);
      const price = Number(form.price);
      if(qty <= 0 || price <= 0) {
        alert('수량과 단가는 0보다 커야 합니다.');
        return;
      }
      // 매도 시 보유 수량 확인
      if(form.tx_type === 'sell') {
        const holding = portfolio.find(h => h.stock_code === form.stock_code);
        if(!holding) {
          if(!window.confirm(`${form.stock_code} 종목이 포트폴리오에 없습니다. 계속하시겠습니까?`)) return;
        } else if(holding.quantity < qty) {
          if(!window.confirm(`보유 수량(${Math.round(holding.quantity)}주)보다 매도 수량(${qty}주)이 많습니다. 계속하시겠습니까?`)) return;
        }
      }
      try {
        const res = await fetch(API('/api/portfolio/transaction'), {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({...form, quantity: qty, price: price}),
        });
        if(!res.ok) {
          const err = await res.json().catch(()=>({}));
          alert('저장 실패: ' + (err.detail || res.status));
          return;
        }
        setShowModal(false);
        setForm({stock_code:"",stock_name:"",sector:"",tx_type:"buy",quantity:"",price:"",tx_date:"",memo:""});
        load();
      } catch(e) {
        alert('오류 발생: ' + e.message);
      }
    };

    const deleteHolding = async (stock_code) => {
      if(!window.confirm(`${stock_code} 보유종목을 삭제하시겠습니까?`)) return;
      await fetch(API(`/api/portfolio/${stock_code}`), { method:'DELETE' });
      load();
    };

    const inputStyle = {
      padding:'0.25rem 0.4rem', borderRadius:'4px', fontSize:'0.82rem',
      background:'rgba(255,255,255,0.1)', border:'1px solid var(--accent-mint)',
      color:'#fff', width:'100%', textAlign:'right',
    };

    const collectingCount = portfolio.filter(h => h.collecting).length;
    const noDataCount     = portfolio.filter(h => h.has_price === false).length;
    const loadAutoTrading = async () => {
      try {
        setAutoLoading(true);
        setAutoError('');
        const res = await fetch(API('/api/kis-trading/account/summary'));
        if (!res.ok) {
          const msg = `계좌 요약 조회 실패 (HTTP ${res.status})`;
          setAutoSummary(null);
          setAutoError(msg);
          return;
        }
        const s = await res.json();
        setAutoSummary(s || null);
        if (!s?.summary) setAutoError('계좌 요약 데이터가 비어 있습니다.');
      } catch (e) {
        setAutoSummary(null);
        setAutoError(`계좌 요약 조회 오류: ${e?.message || e}`);
      } finally {
        setAutoLoading(false);
      }
    };
    React.useEffect(() => {
      if (tab === 'auto') loadAutoTrading();
    }, [tab]);

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>

        {/* 실시간 갱신 상태 뱃지 */}
        <div style={{display:'flex',alignItems:'center',gap:'0.6rem',fontSize:'0.75rem',color:'var(--text-secondary)',paddingLeft:'0.2rem'}}>
          <span style={{
            width:'7px', height:'7px', borderRadius:'50%',
            background: realtimeMeta.market_open ? 'var(--accent-mint)' : '#888',
            display:'inline-block',
            animation: realtimeMeta.market_open ? 'spin 2s linear infinite' : 'none',
          }}/>
          {realtimeMeta.market_open
            ? <span style={{color:'var(--accent-mint)',fontWeight:600}}>장 운영 중 — 1분마다 자동 갱신</span>
            : <span>장 마감 (다음 갱신: 09:00)</span>}
          {realtimeMeta.updated_at && (
            <span style={{marginLeft:'0.3rem'}}>· 마지막 업데이트: {realtimeMeta.updated_at}</span>
          )}
        </div>

        {/* 수집 중 배너 */}
        {(collectingCount > 0 || noDataCount > 0) && (
          <div style={{padding:'0.65rem 1rem',background:'rgba(45,212,191,0.08)',border:'1px solid rgba(45,212,191,0.25)',borderRadius:'8px',display:'flex',alignItems:'center',gap:'0.75rem'}}>
            <div style={{width:'10px',height:'10px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',animation:'spin 0.8s linear infinite',flexShrink:0}}/>
            <span style={{fontSize:'0.82rem',color:'var(--accent-mint)',fontWeight:600}}>
              {collectingCount > 0
                ? `${collectingCount}개 종목 실시간 데이터 수집 중... (20초마다 자동 새로고침)`
                : `${noDataCount}개 종목 주가 데이터 없음 — 조회 시 자동 수집됩니다.`}
            </span>
            <button onClick={load} style={{marginLeft:'auto',padding:'0.2rem 0.7rem',borderRadius:'4px',border:'1px solid rgba(45,212,191,0.4)',background:'transparent',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.75rem',whiteSpace:'nowrap'}}>
              지금 새로고침
            </button>
          </div>
        )}

        {/* 요약 카드 — 스크롤해도 상단 고정 */}
        <div style={{
          position:'sticky', top:0, zIndex:10,
          background:'var(--bg-dark)', paddingBottom:'0.5rem',
          marginBottom:'-0.5rem',
        }}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:'0.75rem'}}>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <p style={{fontSize:'0.75rem',color:'var(--text-secondary)',marginBottom:'0.3rem'}}>매입 총액</p>
            <h3 style={{fontSize:'1.05rem'}}>{fp(totalBuy)}원</h3>
          </div>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <p style={{fontSize:'0.75rem',color:'var(--text-secondary)',marginBottom:'0.3rem'}}>평가 총액</p>
            <h3 style={{fontSize:'1.05rem',color:pc(totalVal-totalBuy)}}>{fp(totalVal)}원</h3>
          </div>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <p style={{fontSize:'0.75rem',color:'var(--text-secondary)',marginBottom:'0.3rem'}}>총 손익</p>
            <h3 style={{fontSize:'1.05rem',color:pc(totalProfit)}}>
              {totalProfit>=0?'+':''}{fp(totalProfit)}원
            </h3>
            <p style={{fontSize:'0.78rem',color:pc(totalProfit),marginTop:'0.2rem',fontWeight:600}}>
              {Number(totalProfitPct)>=0?'+':''}{totalProfitPct}%
            </p>
            {/* [버그 ② 수정] 전일 대비 당일 손익 표시 */}
            {hasDailyData && (
              <div style={{marginTop:'0.4rem',paddingTop:'0.4rem',borderTop:'1px solid rgba(255,255,255,0.08)'}}>
                <p style={{fontSize:'0.68rem',color:'var(--text-secondary)',marginBottom:'0.15rem'}}>전일 대비</p>
                <p style={{fontSize:'0.82rem',fontWeight:700,color:pc(dailyProfit)}}>
                  {dailyProfit>=0?'+':''}{fp(dailyProfit)}원
                  {dailyProfitPct != null && (
                    <span style={{marginLeft:'0.3rem',fontSize:'0.75rem'}}>
                      ({Number(dailyProfitPct)>=0?'+':''}{dailyProfitPct}%)
                    </span>
                  )}
                </p>
              </div>
            )}
          </div>
        </div>
        </div>{/* sticky wrapper end */}

        {/* 탭 + 버튼 */}
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <div style={{display:'flex',gap:'0.4rem'}}>
            {[{k:'holdings',l:'보유종목'},{k:'tx',l:'거래내역'},{k:'auto',l:'자동매매'}].map(({k,l})=>(
              <button key={k} onClick={()=>setTab(k)} style={{
                padding:'0.35rem 0.9rem',borderRadius:'6px',fontSize:'0.8rem',cursor:'pointer',fontWeight:600,
                border:tab===k?'1px solid var(--accent-mint)':'1px solid var(--glass-border)',
                background:tab===k?'rgba(45,212,191,0.15)':'transparent',
                color:tab===k?'var(--accent-mint)':'var(--text-secondary)',
              }}>{l}</button>
            ))}
          </div>
          <div style={{display:'flex',gap:'0.5rem',alignItems:'center'}}>
            <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>💡 행 더블클릭 = 수정</span>
            {/* 평균단가 재계산 */}
            <button onClick={async()=>{
              if(!window.confirm('거래내역 기준으로 평균매입가를 재계산합니다.\n(KIS 매도 시 잘못 낮아진 평균단가를 복원)\n계속하시겠습니까?')) return;
              const r = await fetch(API('/api/portfolio/recalculate-avg'), {method:'POST'});
              if(r.ok){
                const d = await r.json();
                alert(`평균단가 재계산 완료\n수정: ${d.fixed}/${d.total}종목\n${d.details.filter(x=>x.fixed).map(x=>`${x.stock_name}: ${Math.round(x.old_avg).toLocaleString()}→${Math.round(x.new_avg).toLocaleString()}원`).join('\n')}`);
                load();
              } else { alert('재계산 실패'); }
            }} style={{padding:'0.4rem 0.8rem',borderRadius:'8px',
              background:'rgba(251,113,133,0.12)',border:'1px solid rgba(251,113,133,0.35)',
              color:'#f87171',cursor:'pointer',fontWeight:600,fontSize:'0.78rem'}}>
              🔄 평단 재계산
            </button>
            {/* 엑셀 다운로드 */}
            <button onClick={()=>{ window.location.href='/api/portfolio/export/excel'; }}
              style={{padding:'0.4rem 0.8rem',borderRadius:'8px',
                background:'rgba(34,197,94,0.15)',border:'1px solid rgba(34,197,94,0.4)',
                color:'#22c55e',cursor:'pointer',fontWeight:600,fontSize:'0.82rem'}}>
              ⬇ 엑셀
            </button>
            {/* 엑셀 업로드 */}
            <label style={{padding:'0.4rem 0.8rem',borderRadius:'8px',
              background:'rgba(251,191,36,0.15)',border:'1px solid rgba(251,191,36,0.4)',
              color:'#fbbf24',cursor:'pointer',fontWeight:600,fontSize:'0.82rem'}}>
              ⬆ 업로드
              <input type="file" accept=".xlsx,.xls" style={{display:'none'}}
                onChange={async(e)=>{
                  const f = e.target.files[0]; if(!f) return;
                  const fd = new FormData(); fd.append('file', f);
                  const r = await fetch(API('/api/portfolio/import/excel'),{method:'POST',body:fd});
                  if(r.ok){
                    const d = await r.json();
                    alert(`업로드 완료\n성공: ${d.success_count}건\n실패: ${d.failed_count}건${d.failed.length>0?'\n실패항목: '+d.failed.map(x=>x.name).join(', '):''}`)
                    load();
                  } else { alert('업로드 실패'); }
                  e.target.value='';
                }}/>
            </label>
            <button onClick={()=>setShowModal(true)} style={{
              padding:'0.4rem 1rem',borderRadius:'8px',background:'var(--accent-purple)',
              border:'none',color:'#fff',cursor:'pointer',fontWeight:600,fontSize:'0.85rem',
            }}>+ 거래 입력</button>
          </div>
        </div>

        {/* 보유종목 탭 */}
        {tab==='holdings' && (
          portfolio.length===0 ? (
            <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <p>보유 종목이 없습니다.</p>
            </div>
          ) : Object.entries(groups).map(([sector, items])=>{
            const sVal    = items.reduce((s,h)=>s+h.total_value,0);
            const sBuy    = items.reduce((s,h)=>s+h.buy_total,0);
            const sProfit = items.reduce((s,h)=>s+h.profit,0);
            const sPct    = sBuy>0?((sVal-sBuy)/sBuy*100).toFixed(1):0;
            return (
              <section key={sector} className="glass-panel" style={{overflow:'clip'}}>
                <div style={{padding:'0.6rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                  <span style={{fontSize:'0.85rem',fontWeight:700,color:'var(--accent-mint)'}}>{sector}</span>
                  <span style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>
                    평가액 {fp(sVal)}원&nbsp;|&nbsp;
                    <span style={{color:pc(sProfit)}}>
                      손익 {sProfit>=0?'+':''}{fp(sProfit)}원 ({Number(sPct)>=0?'+':''}{sPct}%)
                    </span>
                  </span>
                </div>
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr>
                    <th style={{minWidth:'90px'}}>종목명</th>
                    <th style={{textAlign:'center',minWidth:'70px'}}>추세추종 신호</th>
                    <th style={{textAlign:'right',minWidth:'105px',borderLeft:'1px solid rgba(255,255,255,0.12)'}}>주가(%)</th>
                    <th style={{textAlign:'right',minWidth:'105px'}}>매입가(%)</th>
                    <th style={{textAlign:'right',minWidth:'55px'}}>수량</th>
                    <th style={{textAlign:'right',minWidth:'130px',borderLeft:'1px solid rgba(255,255,255,0.12)'}}>평가액 / 손익</th>
                    <th style={{textAlign:'center',minWidth:'110px'}}>5일수급(외/기)</th>
                    <th style={{textAlign:'center',minWidth:'130px'}}>대차잔고</th>
                    <th></th>
                  </tr></thead>
                  <tbody>
                    {items.map(h => editRow===h.stock_code ? (
                      // ── 편집 모드 행 ──────────────────────────
                      <tr key={h.stock_code} style={{background:'rgba(45,212,191,0.07)'}}>
                        <td>
                          <input value={editForm.stock_name}
                            onChange={e => handleNameChange(e.target.value)}
                            style={{...inputStyle,textAlign:'left',width:'100px'}} placeholder="종목명"/>
                          {editCodePreview && editCodePreview.name !== h.stock_name && (
                            <div style={{fontSize:'0.65rem',color:'var(--accent-mint)',marginTop:'2px',whiteSpace:'nowrap'}}>
                              → {editCodePreview.name} ({editCodePreview.code})
                            </div>
                          )}
                          <input value={editForm.sector} onChange={e=>setEditForm(p=>({...p,sector:e.target.value}))}
                            style={{...inputStyle,textAlign:'left',width:'70px',marginTop:'2px'}} placeholder="섹터"/>
                        </td>
                        <td style={{textAlign:'center',color:'var(--text-secondary)'}}>-</td>
                        <td style={{textAlign:'right',color:'var(--text-secondary)',borderLeft:'1px solid rgba(255,255,255,0.08)'}}>{fp(h.current_price)}</td>
                        <td style={{textAlign:'right'}}>
                          <input value={editForm.avg_price} onChange={e=>setEditForm(p=>({...p,avg_price:e.target.value}))}
                            style={inputStyle} placeholder="매입가"/>
                        </td>
                        <td style={{textAlign:'right'}}>
                          <input value={editForm.quantity} onChange={e=>setEditForm(p=>({...p,quantity:e.target.value}))}
                            style={inputStyle} placeholder="수량"/>
                        </td>
                        <td style={{textAlign:'center',borderLeft:'1px solid rgba(255,255,255,0.08)'}}>
                          <div style={{display:'flex',gap:'0.4rem',justifyContent:'center'}}>
                            <button onClick={()=>saveEdit(h.stock_code)} style={{
                              padding:'0.25rem 0.7rem',borderRadius:'5px',border:'none',
                              background:'var(--accent-mint)',color:'#000',cursor:'pointer',fontWeight:700,fontSize:'0.78rem',
                            }}>저장</button>
                            <button onClick={cancelEdit} style={{
                              padding:'0.25rem 0.7rem',borderRadius:'5px',
                              border:'1px solid var(--glass-border)',background:'transparent',
                              color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.78rem',
                            }}>취소</button>
                          </div>
                        </td>
                        <td style={{textAlign:'center',color:'var(--text-secondary)'}}>-</td>
                        <td style={{textAlign:'center',color:'var(--text-secondary)'}}>-</td>
                        <td>
                          <button onClick={()=>deleteHolding(h.stock_code)} style={{
                            padding:'0.2rem 0.5rem',borderRadius:'4px',border:'none',
                            background:'rgba(251,113,133,0.15)',color:'var(--accent-red)',
                            cursor:'pointer',fontSize:'0.75rem',
                          }}>삭제</button>
                        </td>
                      </tr>
                    ) : (
                      // ── 일반 행 (더블클릭 → 편집 모드) ────────
                      <tr key={h.stock_code}
                        onDoubleClick={()=>startEdit(h)}
                        style={{cursor:'pointer', background: h.collecting ? 'rgba(45,212,191,0.03)' : undefined}}
                        title="더블클릭하면 수정 / 종목명 클릭하면 분석">
                        <td onClick={()=>{changeStock(h.stock_code);changeTab('analysis');}}
                          style={{minWidth:'90px',maxWidth:'130px'}}>
                          <div style={{fontWeight:600,display:'flex',alignItems:'center',gap:'0.4rem',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>
                            {h.stock_name || h.stock_code}
                            {(h.avg_price === 0 || h.avg_price == null) && (
                              <span title="평균매입가가 0원입니다. 🔄 평단 재계산 버튼을 눌러 복원하세요"
                                style={{display:'inline-flex',alignItems:'center',fontSize:'0.62rem',color:'#f87171',padding:'1px 5px',border:'1px solid rgba(248,113,133,0.4)',borderRadius:'4px',flexShrink:0,cursor:'help'}}>
                                ⚠ 평단0
                              </span>
                            )}
                            {h.collecting && (
                              <span style={{display:'inline-flex',alignItems:'center',gap:'3px',fontSize:'0.62rem',color:'var(--accent-mint)',padding:'1px 5px',border:'1px solid rgba(45,212,191,0.35)',borderRadius:'4px',flexShrink:0}}>
                                <span style={{width:'5px',height:'5px',borderRadius:'50%',border:'1.5px solid var(--accent-mint)',borderTopColor:'transparent',display:'inline-block',animation:'spin 0.8s linear infinite'}}/>
                                수집중
                              </span>
                            )}
                          </div>
                          <div style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{h.stock_code}</div>
                        </td>
                        {/* 매매 신호 — 4분면(추세×가치) */}
                        <td style={{textAlign:'center'}}>
                          {(() => {
                            const sig = h.trade_signal;
                            if(!sig) return <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>-</span>;
                            const cfg = {
                              'add_buy':     {emoji:'💚', label:'추가매수', color:'#22c55e', bg:'rgba(34,197,94,0.18)'},
                              'strong_buy':  {emoji:'💚', label:'추가매수', color:'#22c55e', bg:'rgba(34,197,94,0.18)'},
                              'hold':        {emoji:'🟡', label:'보유유지', color:'#fbbf24', bg:'rgba(251,191,36,0.1)'},
                              'hold_value':  {emoji:'🔵', label:'홀딩유지', color:'#60a5fa', bg:'rgba(96,165,250,0.13)'},
                              'take_profit': {emoji:'🟠', label:'익절고려', color:'#f97316', bg:'rgba(249,115,22,0.14)'},
                              'caution':     {emoji:'🟠', label:'관망',    color:'#f97316', bg:'rgba(249,115,22,0.1)'},
                              'real_sell':   {emoji:'🔴', label:'진매도',  color:'#ef4444', bg:'rgba(239,68,68,0.18)'},
                              'sell':        {emoji:'🔴', label:'매도검토',color:'#ef4444', bg:'rgba(239,68,68,0.14)'},
                              'cut_loss':    {emoji:'⛔', label:'손절',    color:'#dc2626', bg:'rgba(220,38,38,0.22)'},
                              'strong_sell': {emoji:'⛔', label:'손절',    color:'#dc2626', bg:'rgba(220,38,38,0.22)'},
                            }[sig] || {emoji:'⚪', label:'중립', color:'#64748b', bg:'transparent'};
                            const ts = h.trend_score ?? 0;
                            const vs = h.val_score  ?? 0;
                            return (
                              <div title={h.trade_reason||''} style={{display:'flex',flexDirection:'column',alignItems:'center',
                                padding:'3px 5px',borderRadius:'6px',background:cfg.bg,cursor:'help',gap:'2px'}}>
                                <span style={{fontSize:'0.88rem',lineHeight:1}}>{cfg.emoji}</span>
                                <span style={{fontSize:'0.62rem',color:cfg.color,fontWeight:700}}>{cfg.label}</span>
                                <div style={{display:'flex',gap:'3px'}}>
                                  <span style={{fontSize:'0.5rem',padding:'0 3px',borderRadius:'3px',
                                    background: ts>=2?'rgba(34,197,94,0.2)': ts<=-2?'rgba(239,68,68,0.2)':'rgba(255,255,255,0.08)',
                                    color: ts>=2?'#22c55e': ts<=-2?'#ef4444':'rgba(255,255,255,0.4)'}}>
                                    추{ts>=0?'+':''}{ts}
                                  </span>
                                  <span style={{fontSize:'0.5rem',padding:'0 3px',borderRadius:'3px',
                                    background: vs>=2?'rgba(34,197,94,0.2)': vs<=-1?'rgba(239,68,68,0.2)':'rgba(255,255,255,0.08)',
                                    color: vs>=2?'#22c55e': vs<=-1?'#ef4444':'rgba(255,255,255,0.4)'}}>
                                    가{vs>=0?'+':''}{vs}
                                  </span>
                                </div>
                              </div>
                            );
                          })()}
                        </td>
                        <td style={{textAlign:'right',fontSize:'0.85rem',whiteSpace:'nowrap',borderLeft:'1px solid rgba(255,255,255,0.08)',fontVariantNumeric:'tabular-nums'}}>
                          {h.has_price===false
                            ? <span style={{fontSize:'0.72rem',color:'var(--accent-mint)'}}>{h.collecting?'수집중...':'미수집'}</span>
                            : (
                              <div style={{display:'flex',flexDirection:'column',alignItems:'flex-end',lineHeight:1.15}}>
                                <span>{fp(h.current_price)}</span>
                                <span style={{color:pc(h.change_pct),fontWeight:600}}>({pct(h.change_pct)})</span>
                              </div>
                            )}
                        </td>
                        <td style={{textAlign:'right',fontSize:'0.85rem',whiteSpace:'nowrap',fontVariantNumeric:'tabular-nums'}}>
                          <div style={{display:'flex',flexDirection:'column',alignItems:'flex-end',lineHeight:1.15}}>
                            <span>{fp(h.avg_price)}</span>
                            <span style={{color:h.has_price===false?'var(--text-secondary)':pc(h.profit_pct),fontWeight:600}}>
                              ({h.has_price===false?'-':pct(h.profit_pct)})
                            </span>
                          </div>
                        </td>
                        <td style={{textAlign:'right',fontSize:'0.85rem',whiteSpace:'nowrap'}}>{Math.round(h.quantity).toLocaleString('ko-KR')}</td>
                        <td style={{textAlign:'right',fontWeight:600,fontSize:'0.85rem',whiteSpace:'nowrap',borderLeft:'1px solid rgba(255,255,255,0.08)'}}>
                          <div style={{color:'var(--text-primary)'}}>{fp(h.total_value)}</div>
                          <div style={{color:h.has_price===false?'var(--text-secondary)':pc(h.profit)}}>
                            {h.has_price===false?'-':`(${(h.profit>=0?'+':'')+fp(h.profit)})`}
                          </div>
                        </td>
                        {/* 수급 컬럼 */}
                        <td style={{textAlign:'center',whiteSpace:'nowrap'}}>
                          {(() => {
                            const frn  = h.frn_net_buy;   // null=데이터없음, 0=있지만0
                            const inst = h.inst_net_buy;
                            if(frn == null && inst == null) return <span style={{color:'rgba(255,255,255,0.25)',fontSize:'0.7rem'}}>±0</span>;
                            const fmtAmt = (v) => {
                              if(v == null) return '-';
                              if(v === 0) return '0억';
                              const abs = Math.abs(v);
                              const disp = abs < 10 ? abs.toFixed(1) : Math.round(abs).toLocaleString();
                              return `${v>0?'+':'-'}${disp}억`;
                            };
                            const light = (v) => {
                              if(v == null || v === 0) return {bg:'rgba(148,163,184,0.14)', bd:'rgba(148,163,184,0.35)', fg:'#94a3b8', icon:'●'};
                              if(v > 0) return {bg:'rgba(239,68,68,0.16)', bd:'rgba(239,68,68,0.42)', fg:'#f87171', icon:'▲'};
                              return {bg:'rgba(34,197,94,0.16)', bd:'rgba(34,197,94,0.42)', fg:'#4ade80', icon:'▼'};
                            };
                            const fr = light(frn), ins = light(inst);
                            return (
                              <div style={{display:'flex',gap:'4px',justifyContent:'center'}}>
                                <div style={{minWidth:'54px',padding:'2px 4px',borderRadius:'6px',background:'rgba(20,30,50,0.75)',border:`1px solid ${fr.bd}`,display:'flex',flexDirection:'column',alignItems:'center',lineHeight:1.05}}>
                                  <span style={{fontSize:'0.54rem',color:'rgba(255,255,255,0.5)'}}>외인</span>
                                  <span style={{fontSize:'0.72rem',fontWeight:700,color:fr.fg}}>{fr.icon}</span>
                                  <span style={{fontSize:'0.66rem',fontWeight:700,color:fr.fg}}>{fmtAmt(frn)}</span>
                                </div>
                                <div style={{minWidth:'54px',padding:'2px 4px',borderRadius:'6px',background:'rgba(20,30,50,0.75)',border:`1px solid ${ins.bd}`,display:'flex',flexDirection:'column',alignItems:'center',lineHeight:1.05}}>
                                  <span style={{fontSize:'0.54rem',color:'rgba(255,255,255,0.5)'}}>기관</span>
                                  <span style={{fontSize:'0.72rem',fontWeight:700,color:ins.fg}}>{ins.icon}</span>
                                  <span style={{fontSize:'0.66rem',fontWeight:700,color:ins.fg}}>{fmtAmt(inst)}</span>
                                </div>
                              </div>
                            );
                          })()}
                        </td>
                        {/* 대차잔고 — 개별종목(/short-sell)과 동일 형식 */}
                        <td style={{textAlign:'center',whiteSpace:'nowrap'}}>
                          {(() => {
                            const sd = h.short_data;
                            if(!sd) return <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>-</span>;
                            const fmtBal = (v) => {
                              const man = (v||0) / 10000;
                              return man >= 1 ? man.toFixed(1)+'만' : Math.round(v||0).toLocaleString();
                            };
                            // today vs avg5 (개별종목 API와 동일 기준)
                            const todayRising = (sd.today||0) > (sd.avg5||0) * 1.02;
                            const weekRising  = (sd.avg5||0)  > (sd.avg5_prev||0) * 1.02;
                            const lights = [
                              {label:'당일', val:sd.today,    signal:sd.today_signal, rising:todayRising},
                              {label:'5일↔', val:sd.avg5,     signal:sd.week_signal,  rising:weekRising},
                              {label:'10일', val:sd.avg10 ?? sd.avg5, signal: (sd.avg10||0) > (sd.avg10_prev||0)*1.02 ? 'red':'green', rising: (sd.avg10||0) > (sd.avg10_prev||0)*1.02},
                            ];
                            return (
                              <div style={{display:'flex',gap:'3px',justifyContent:'center'}}>
                                {lights.map(({label,val,rising})=>{
                                  const color = rising ? '#ef4444' : '#22c55e';
                                  return (
                                    <div key={label}
                                      title={`${label}: ${Math.round(val||0).toLocaleString()}주\n${rising?'증가추세(주의)':'감소추세(양호)'}`}
                                      style={{display:'flex',flexDirection:'column',alignItems:'center',
                                        padding:'2px 4px',borderRadius:'4px',cursor:'help',
                                        background:`${color}18`,border:`1px solid ${color}44`,minWidth:'34px'}}>
                                      <span style={{fontSize:'0.5rem',color:'rgba(255,255,255,0.4)',lineHeight:1.2}}>{label}</span>
                                      <span style={{fontSize:'0.6rem',fontWeight:700,color,lineHeight:1.2}}>{fmtBal(val)}</span>
                                      <span style={{fontSize:'0.52rem',color,lineHeight:1}}>{rising?'▲':'▼'}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            );
                          })()}
                        </td>
                        <td>
                          <button onClick={(e)=>{e.stopPropagation();changeStock(h.stock_code);changeTab('analysis');}}
                            style={{padding:'0.2rem 0.5rem',borderRadius:'4px',border:'none',
                              background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',
                              cursor:'pointer',fontSize:'0.72rem',whiteSpace:'nowrap'}}>
                            분석↗
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {/* ── 판단 로직 설명 ── */}
                <div style={{marginTop:'1.5rem',padding:'1.2rem',borderRadius:'10px',
                  background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.08)'}}>
                  <div style={{fontSize:'0.78rem',fontWeight:700,color:'rgba(255,255,255,0.7)',
                    marginBottom:'1rem',display:'flex',alignItems:'center',gap:'0.4rem'}}>
                    🧠 AI 전문가 판단 기준 (추세 × 가치 4분면)
                  </div>

                  {/* 4분면 매트릭스 */}
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.6rem',marginBottom:'1rem'}}>
                    {[
                      {sig:'💚 추가매수', cond:'추세정배열 + 저평가',
                       desc:'이동평균 정배열(현재가>MA5>MA20>MA60)이며 PBR·PER 기준 저평가 상태. 기술적 추세와 내재가치가 모두 지지. 분할 추가매수 유효.',
                       bg:'rgba(34,197,94,0.08)',border:'rgba(34,197,94,0.25)'},
                      {sig:'🟡 보유유지', cond:'추세양호 + 적정가치',
                       desc:'추세는 유지되나 가치평가가 적정 수준. 신규 매수보다 기존 보유 유지가 적합. 손절선 이탈 시 매도로 전환.',
                       bg:'rgba(251,191,36,0.08)',border:'rgba(251,191,36,0.25)'},
                      {sig:'🔵 홀딩유지', cond:'추세이탈 + 저평가',
                       desc:'단기 추세가 무너졌으나 PBR/PER 기준 내재가치가 충분. 손실이 크지 않다면 추세 회복을 기다리는 홀딩 전략이 유리. 추가 매수는 분할로.',
                       bg:'rgba(96,165,250,0.08)',border:'rgba(96,165,250,0.25)'},
                      {sig:'🔴 진매도', cond:'추세역배열 + 고평가',
                       desc:'이동평균 역배열이면서 PBR·PER 기준 고평가. 추세와 가치 모두 하락 압력. 수익 중이라면 익절, 손실 중이라면 손절 집행 검토.',
                       bg:'rgba(239,68,68,0.08)',border:'rgba(239,68,68,0.25)'},
                    ].map(item=>(
                      <div key={item.sig} style={{padding:'0.75rem',borderRadius:'8px',
                        background:item.bg,border:`1px solid ${item.border}`}}>
                        <div style={{display:'flex',alignItems:'center',gap:'0.4rem',marginBottom:'0.3rem'}}>
                          <span style={{fontSize:'0.78rem',fontWeight:700}}>{item.sig}</span>
                          <span style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.45)',padding:'0 5px',
                            borderRadius:'3px',background:'rgba(255,255,255,0.06)'}}>{item.cond}</span>
                        </div>
                        <p style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.55)',lineHeight:1.5,margin:0}}>
                          {item.desc}
                        </p>
                      </div>
                    ))}
                  </div>

                  {/* 점수 계산 기준 */}
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.8rem',marginBottom:'0.8rem'}}>
                    <div style={{padding:'0.7rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',
                      border:'1px solid rgba(255,255,255,0.07)'}}>
                      <div style={{fontSize:'0.7rem',fontWeight:700,color:'rgba(255,255,255,0.6)',marginBottom:'0.4rem'}}>
                        📈 추세 점수 계산 (추세 스코어)
                      </div>
                      <div style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.45)',lineHeight:1.8}}>
                        <div><span style={{color:'#22c55e'}}>+4</span> 완전정배열 (현재가 &gt; MA5 &gt; MA20 &gt; MA60 &gt; MA120)</div>
                        <div><span style={{color:'#22c55e'}}>+3</span> 정배열 (현재가 &gt; MA5 &gt; MA20 &gt; MA60)</div>
                        <div><span style={{color:'#22c55e'}}>+2</span> 중기 정배열 (현재가 &gt; MA20 &gt; MA60)</div>
                        <div><span style={{color:'#22c55e'}}>+1</span> 단기 우위 (현재가 &gt; MA20)</div>
                        <div><span style={{color:'#f97316'}}> 0</span> 중립 (혼재)</div>
                        <div><span style={{color:'#ef4444'}}>-1</span> MA20 이탈</div>
                        <div><span style={{color:'#ef4444'}}>-2</span> 중기 역배열 (현재가 &lt; MA20 &lt; MA60)</div>
                        <div><span style={{color:'#ef4444'}}>-3</span> 역배열 (현재가 &lt; MA5 &lt; MA20 &lt; MA60)</div>
                        <div><span style={{color:'#ef4444'}}>-4</span> 완전역배열</div>
                      </div>
                    </div>
                    <div style={{padding:'0.7rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',
                      border:'1px solid rgba(255,255,255,0.07)'}}>
                      <div style={{fontSize:'0.7rem',fontWeight:700,color:'rgba(255,255,255,0.6)',marginBottom:'0.4rem'}}>
                        💎 가치 점수 계산 (가치 스코어)
                      </div>
                      <div style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.45)',lineHeight:1.8}}>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>PBR</b>: ≤0.5 <span style={{color:'#22c55e'}}>+4</span> / ≤1.0 <span style={{color:'#22c55e'}}>+3</span> / ≤2.0 <span style={{color:'#22c55e'}}>+1</span> / ≤4.0 <span style={{color:'#ef4444'}}>-1</span> / &gt;4 <span style={{color:'#ef4444'}}>-2</span></div>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>PER</b>: ≤6 <span style={{color:'#22c55e'}}>+4</span> / ≤12 <span style={{color:'#22c55e'}}>+3</span> / ≤20 <span style={{color:'#22c55e'}}>+1</span> / ≤35 <span style={{color:'#ef4444'}}>-1</span> / &gt;35 <span style={{color:'#ef4444'}}>-2</span></div>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>ROE</b>: ≥25% <span style={{color:'#22c55e'}}>+3</span> / ≥15% <span style={{color:'#22c55e'}}>+2</span> / ≥8% <span style={{color:'#22c55e'}}>+1</span> / &lt;0% <span style={{color:'#ef4444'}}>-2</span></div>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>ROA</b>: ≥10% <span style={{color:'#22c55e'}}>+1</span> / &lt;0% <span style={{color:'#ef4444'}}>-1</span></div>
                        <div style={{marginTop:'0.3rem',color:'rgba(255,255,255,0.3)'}}>
                          ※ 바이오·신성장 종목은 PER 없음 → 가치데이터없음 처리, 추세만으로 판단
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 보조 지표 */}
                  <div style={{padding:'0.6rem 0.8rem',borderRadius:'7px',background:'rgba(255,255,255,0.02)',
                    border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.65rem',color:'rgba(255,255,255,0.4)',lineHeight:1.8}}>
                    <span style={{color:'rgba(255,255,255,0.5)',fontWeight:600}}>보조 지표 |</span>
                    &nbsp; <b>5일수급</b>: 최근 5거래일 외국인·기관 순매수 합계(억원, KIS amt 기준)
                    &nbsp;·&nbsp; <b>대차잔고</b>: 당일·5일평균·10일평균 차입잔고(주) — 증가(▲) = 공매도 세력 유입 주의
                    &nbsp;·&nbsp; <b>손절기준</b>: ATR(14) × 2 이하 하락 or 손익 -10% 도달
                    &nbsp;·&nbsp; <b>익절고려</b>: 추세양호하나 PBR/PER 고평가 구간 진입 시 또는 수익률 +20% 이상에서 추세 약화
                  </div>
                </div>
              </section>
            );
          })
        )}

        {tab==='auto' && (
          <div style={{display:'flex',flexDirection:'column',gap:'0.9rem'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
              <h3 style={{fontSize:'0.95rem',fontWeight:700,color:'var(--accent-mint)'}}>KIS 자동매매 계좌현황</h3>
              <button onClick={loadAutoTrading} style={{padding:'0.35rem 0.8rem',borderRadius:'8px',background:'rgba(45,212,191,0.15)',border:'1px solid rgba(45,212,191,0.35)',color:'var(--accent-mint)',cursor:'pointer'}}>새로고침</button>
            </div>
            {autoLoading && (
              <div style={{fontSize:'0.78rem',color:'#93c5fd'}}>계좌 예수금/잔고 조회 중...</div>
            )}
            {!!autoError && (
              <div style={{fontSize:'0.78rem',color:'#fca5a5',padding:'0.55rem 0.7rem',border:'1px solid rgba(248,113,113,0.35)',borderRadius:'8px',background:'rgba(248,113,113,0.08)'}}>
                {autoError}
              </div>
            )}
            <div style={{fontSize:'0.76rem',color:'var(--text-secondary)',marginTop:'-0.3rem'}}>
              계좌: <span style={{color:'var(--text-primary)',fontWeight:700}}>{autoSummary?.account_no || autoSummary?.account_no_masked || '-'}</span>
              <span style={{marginLeft:'0.6rem'}}>상품코드: {autoSummary?.account_prod || '-'}</span>
            </div>

            <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:'0.6rem'}}>
              <div className="glass-panel" style={{padding:'0.9rem'}}><p style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>예수금(주문가능)</p><h3>{autoSummary?.summary?.cash_available != null ? `${fp(autoSummary.summary.cash_available)}원` : '-'}</h3></div>
              <div className="glass-panel" style={{padding:'0.9rem'}}><p style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>D+2 예수금</p><h3>{autoSummary?.summary?.cash_d2 != null ? `${fp(autoSummary.summary.cash_d2)}원` : '-'}</h3></div>
              <div className="glass-panel" style={{padding:'0.9rem'}}><p style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>평가금액</p><h3>{autoSummary?.summary?.total_eval != null ? `${fp(autoSummary.summary.total_eval)}원` : '-'}</h3></div>
              <div className="glass-panel" style={{padding:'0.9rem'}}><p style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>평가손익</p><h3 style={{color:pc(autoSummary?.summary?.total_profit || 0)}}>{autoSummary?.summary?.total_profit != null ? `${fp(autoSummary.summary.total_profit)}원` : '-'}</h3></div>
            </div>

            <section className="glass-panel" style={{padding:'0.9rem'}}>
              <div style={{display:'flex',justifyContent:'space-between',marginBottom:'0.5rem'}}>
                <span style={{fontWeight:700,fontSize:'0.82rem'}}>실계좌 보유잔고 ({autoSummary?.holdings_count || 0}종목)</span>
                <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>업데이트: {autoSummary?.updated_at || '-'}</span>
              </div>
              <table className="premium-table" style={{width:'100%'}}>
                <thead><tr><th>종목</th><th style={{textAlign:'right'}}>수량</th><th style={{textAlign:'right'}}>평균단가</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>손익</th><th style={{textAlign:'right'}}>수익률</th></tr></thead>
                <tbody>
                  {(autoSummary?.holdings || []).slice(0, 20).map((h, i) => (
                    <tr key={`${h.stock_code}-${i}`}>
                      <td>{h.stock_name} <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>{h.stock_code}</span></td>
                      <td style={{textAlign:'right'}}>{fp(h.quantity)}</td>
                      <td style={{textAlign:'right'}}>{fp(h.avg_price)}</td>
                      <td style={{textAlign:'right'}}>{fp(h.current_price)}</td>
                      <td style={{textAlign:'right',color:pc(h.profit)}}>{fp(h.profit)}</td>
                      <td style={{textAlign:'right',color:pc(h.profit_pct)}}>{pct(h.profit_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>
        )}

        {/* 거래 내역 탭 */}
        {tab==='tx' && (
          <section className="glass-panel" style={{overflow:'clip'}}>
            <table className="premium-table" style={{width:'100%'}}>
              <thead><tr>
                <th>날짜</th><th>종목</th><th>구분</th>
                <th style={{textAlign:'right'}}>수량</th>
                <th style={{textAlign:'right'}}>단가</th>
                <th style={{textAlign:'right'}}>금액</th>
                <th>메모</th>
              </tr></thead>
              <tbody>
                {transactions.map(t=>(
                  <tr key={t.id}>
                    <td style={{fontSize:'0.8rem',color:'var(--text-secondary)'}}>{t.tx_date}</td>
                    <td style={{fontWeight:600}}>{t.stock_name} <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{t.stock_code}</span></td>
                    <td><span style={{padding:'0.15rem 0.5rem',borderRadius:'4px',fontSize:'0.75rem',
                      background:t.tx_type==='buy'?'rgba(239,68,68,0.15)':'rgba(59,130,246,0.15)',
                      color:t.tx_type==='buy'?'#ef4444':'#3b82f6'}}>
                      {t.tx_type==='buy'?'매수':'매도'}
                    </span></td>
                    <td style={{textAlign:'right'}}>{Math.round(t.quantity).toLocaleString('ko-KR')}</td>
                    <td style={{textAlign:'right'}}>{fp(t.price)}</td>
                    <td style={{textAlign:'right'}}>{fp(t.quantity*t.price)}</td>
                    <td style={{fontSize:'0.75rem',color:'var(--text-secondary)',maxWidth:'180px',overflow:'hidden',textOverflow:'ellipsis'}}>{t.memo}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* 카카오 파싱 탭 — 제거됨 */}
        {false && (
          <div style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
            <div className="glass-panel" style={{padding:'1.2rem'}}>
              <textarea value={kakaoText} onChange={e=>setKakaoText(e.target.value)}/>
            </div>
            {parsedTx.length>0 && (
              <section className="glass-panel" style={{overflow:'clip'}}>
                <table className="premium-table" style={{width:'100%'}}>
                  <tbody>
                    {parsedTx.map((item,i)=>(
                      <tr key={i}>
                        <td>{item.raw}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </div>
        )}

        {/* 거래 입력 모달 */}
        {showModal && (() => {
          // 현재 입력한 종목코드로 보유 종목 찾기
          const matchHolding = portfolio.find(h => h.stock_code === form.stock_code);
          return (
          <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.6)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:100}}
            onClick={(e)=>{if(e.target===e.currentTarget)setShowModal(false);}}>
            <div className="glass-panel" style={{width:'440px',padding:'1.5rem',display:'flex',flexDirection:'column',gap:'0.75rem'}}
              onClick={e=>e.stopPropagation()}>
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <h3 style={{fontSize:'1rem',fontWeight:700}}>거래 입력</h3>
                <button onClick={()=>setShowModal(false)} style={{background:'none',border:'none',color:'rgba(255,255,255,0.4)',cursor:'pointer',fontSize:'1.2rem'}}>×</button>
              </div>

              {/* 구분 선택 (맨 위로) */}
              <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                <label style={{width:'80px',fontSize:'0.8rem',color:'var(--text-secondary)'}}>구분*</label>
                <div style={{display:'flex',gap:'0.5rem'}}>
                  {['buy','sell'].map(t=>(
                    <button key={t} onClick={()=>setForm(p=>({...p,tx_type:t}))} style={{
                      padding:'0.4rem 1.2rem',borderRadius:'6px',cursor:'pointer',fontWeight:700,fontSize:'0.9rem',
                      border:form.tx_type===t?`1px solid ${t==='buy'?'#ef4444':'#3b82f6'}`:'1px solid var(--glass-border)',
                      background:form.tx_type===t?(t==='buy'?'rgba(239,68,68,0.2)':'rgba(59,130,246,0.2)'):'transparent',
                      color:form.tx_type===t?(t==='buy'?'#ef4444':'#3b82f6'):'var(--text-secondary)',
                    }}>{t==='buy'?'매수':'매도'}</button>
                  ))}
                </div>
              </div>

              {/* 종목명 검색 → 코드 자동완성 */}
              <div style={{display:'flex',alignItems:'flex-start',gap:'0.5rem'}}>
                <label style={{width:'80px',fontSize:'0.8rem',color:'var(--text-secondary)',flexShrink:0,paddingTop:'0.4rem'}}>종목명 검색*</label>
                <div style={{flex:1,position:'relative'}}>
                  <input value={form.stock_name}
                    onChange={e=>{
                      const v = e.target.value;
                      setForm(p=>({...p, stock_name:v, stock_code:'', sector:''}));
                    }}
                    placeholder="종목명 입력 (예: 에이엘티, 삼성전자)"
                    style={{width:'100%',padding:'0.4rem 0.7rem',borderRadius:'6px',
                      background:'rgba(255,255,255,0.07)',border:'1px solid rgba(45,212,191,0.5)',
                      color:'#fff',fontSize:'0.85rem'}}/>
                  {/* 보유종목 드롭다운 */}
                  {form.stock_name && !form.stock_code && (() => {
                    const matches = portfolio.filter(h =>
                      h.stock_name?.includes(form.stock_name) ||
                      h.stock_code?.includes(form.stock_name)
                    ).slice(0,6);
                    if(!matches.length) return null;
                    return (
                      <div style={{position:'absolute',top:'100%',left:0,right:0,marginTop:'2px',
                        borderRadius:'6px',background:'#1a1a2e',border:'1px solid var(--glass-border)',
                        zIndex:20,overflow:'hidden',boxShadow:'0 4px 20px rgba(0,0,0,0.5)'}}>
                        {matches.map(h=>(
                          <div key={h.stock_code}
                            onClick={()=>setForm(p=>({...p,
                              stock_code:h.stock_code,
                              stock_name:h.stock_name,
                              sector:h.sector||'',
                              price: String(Math.round(h.current_price||h.avg_price||0))
                            }))}
                            style={{padding:'0.45rem 0.7rem',cursor:'pointer',fontSize:'0.82rem',
                              borderBottom:'1px solid rgba(255,255,255,0.05)',
                              display:'flex',justifyContent:'space-between',alignItems:'center'}}
                            onMouseEnter={e=>e.currentTarget.style.background='rgba(45,212,191,0.1)'}
                            onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
                            <span>
                              <span style={{fontWeight:700,color:'#fff'}}>{h.stock_name}</span>
                              <span style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginLeft:'0.4rem'}}>{h.stock_code}</span>
                            </span>
                            <span style={{fontSize:'0.72rem',color:'var(--accent-mint)'}}>
                              {Math.round(h.quantity).toLocaleString()}주
                            </span>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                  {/* 선택된 종목 표시 */}
                  {form.stock_code && (
                    <div style={{marginTop:'4px',padding:'0.3rem 0.6rem',borderRadius:'4px',
                      background:'rgba(45,212,191,0.12)',border:'1px solid rgba(45,212,191,0.3)',
                      fontSize:'0.72rem',color:'var(--accent-mint)',display:'flex',justifyContent:'space-between'}}>
                      <span>✓ {form.stock_name} ({form.stock_code}) — 보유 {Math.round(matchHolding?.quantity||0).toLocaleString()}주 @ {Math.round(matchHolding?.avg_price||0).toLocaleString()}원</span>
                      {form.tx_type==='sell' && matchHolding && (
                        <span style={{color:'#3b82f6',cursor:'pointer',fontWeight:700,marginLeft:'0.5rem'}}
                          onClick={()=>setForm(p=>({...p,quantity:String(Math.round(matchHolding.quantity))}))}>
                          전량↓
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* 나머지 필드 */}
              {[{label:'날짜',key:'tx_date',ph:'2026-03-31'},
                {label:'수량*',key:'quantity',ph:'100'},
                {label:'단가*',key:'price',ph:'75000'},
                {label:'메모',key:'memo',ph:''}].map(({label,key,ph})=>(
                <div key={key} style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                  <label style={{width:'80px',fontSize:'0.8rem',color:'var(--text-secondary)',flexShrink:0}}>{label}</label>
                  <input value={form[key]} onChange={e=>setForm(p=>({...p,[key]:e.target.value}))}
                    placeholder={ph} style={{flex:1,padding:'0.4rem 0.7rem',borderRadius:'6px',
                      background:'rgba(255,255,255,0.07)',border:'1px solid var(--glass-border)',
                      color:'#fff',fontSize:'0.85rem'}}/>
                </div>
              ))}

              {/* 예상 거래금액 */}
              {form.quantity && form.price && (
                <div style={{padding:'0.4rem 0.7rem',borderRadius:'6px',background:'rgba(255,255,255,0.04)',
                  border:'1px solid var(--glass-border)',fontSize:'0.78rem',color:'var(--text-secondary)'}}>
                  예상 {form.tx_type==='buy'?'매수':'매도'}금액:
                  <span style={{color:'#fff',fontWeight:700,marginLeft:'0.4rem'}}>
                    {(Number(form.quantity)*Number(form.price)).toLocaleString('ko-KR')}원
                  </span>
                </div>
              )}

              <div style={{display:'flex',gap:'0.5rem',marginTop:'0.25rem'}}>
                <button onClick={saveTx} style={{flex:1,padding:'0.55rem',borderRadius:'8px',
                  background:form.tx_type==='buy'?'rgba(239,68,68,0.8)':'rgba(59,130,246,0.8)',
                  border:'none',color:'#fff',cursor:'pointer',fontWeight:700,fontSize:'0.95rem'}}>
                  {form.tx_type==='buy'?'매수 저장':'매도 저장'}
                </button>
                <button onClick={()=>{setShowModal(false);setForm({stock_code:"",stock_name:"",sector:"",tx_type:"buy",quantity:"",price:"",tx_date:"",memo:""}); }}
                  style={{flex:1,padding:'0.55rem',borderRadius:'8px',background:'transparent',
                    border:'1px solid var(--glass-border)',color:'var(--text-secondary)',cursor:'pointer'}}>취소</button>
              </div>
            </div>
          </div>
          );
        })()}
      </div>
    );
  };


  // ── 수출입 분석 2 ────────────────────────────────────────────
  const TradeAnalysis2 = () => {
    const HS_API = (path) => `/hs${path}`;
    const isMobile = useIsMobile();
    const fmt  = (v) => v == null ? '-' : Math.round(v).toLocaleString('ko-KR');
    const fmtB = (v) => v == null ? '-' : `$${(v/1e9).toFixed(2)}B`;
    const fmtM = (v) => v == null ? '-' : `$${(v/1e6).toFixed(2)}M`;
    const fmtAxis = (v) => {
      if (v == null) return '-';
      if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
      if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
      if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
      return `$${Math.round(v)}`;
    };
    const fmtKg = (v) => {
      if (v == null) return '-';
      if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M kg`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K kg`;
      return `${Math.round(v)} kg`;
    };
    const pct  = (v) => v == null ? <span style={{color:'rgba(255,255,255,0.3)'}}>-</span>
                       : <span style={{color: v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.4)', fontWeight:600}}>
                           {v > 0 ? '+' : ''}{v.toFixed(1)}%
                         </span>;
    const formatCompositionLabel = (sectorNames, hsNames) => {
      const sectorParts = String(sectorNames || '')
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean);
      if (sectorParts.length > 0) return sectorParts.join(' / ');
      return String(hsNames || '')
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean)
        .join(' / ');
    };

    const [mainTab, setMainTab]          = React.useState('sector'); // 'sector' | 'company'

    const [sectors, setSectors]         = React.useState([]);
    const [selSector, setSelSector]     = React.useState(null);
    const [companies, setCompanies]     = React.useState([]);
    const [selCompany, setSelCompany]   = React.useState(null);
    const [compTrend, setCompTrend]     = React.useState(null);
    const [sectorHs, setSectorHs]       = React.useState(null);
    const [sectorTab, setSectorTab]     = React.useState('trend');
    const [sectorHsLoading, setSectorHsLoading] = React.useState(false);
    const [sectorPeriod, setSectorPeriod] = React.useState('');
    const [companyHs, setCompanyHs]     = React.useState(null);
    const [companyHsLoading, setCompanyHsLoading] = React.useState(false);
    const [companyPeriod, setCompanyPeriod] = React.useState('');
    const [months, setMonths]           = React.useState(24);
    const [loading, setLoading]         = React.useState(false);
    const [compLoading, setCompLoading] = React.useState(false);
    const [error, setError]             = React.useState('');

    // 기업별 탭 전용 상태
    const [allCompanies, setAllCompanies]     = React.useState([]);
    const [allCompLoading, setAllCompLoading] = React.useState(false);
    const [allCompSearch, setAllCompSearch]   = React.useState('');
    const [allCompMonths, setAllCompMonths]   = React.useState(24);
    const [allCompSector, setAllCompSector]   = React.useState('all');
    const [selAllComp, setSelAllComp]         = React.useState(null);
    const [allCompTrend, setAllCompTrend]     = React.useState(null);
    const [allCompTrendLoading, setAllCompTrendLoading] = React.useState(false);
    const [allCompHs, setAllCompHs]           = React.useState(null);
    const [allCompHsLoading, setAllCompHsLoading] = React.useState(false);
    const [allCompHsPeriod, setAllCompHsPeriod] = React.useState('');

    // 섹터 데이터 로드
    const loadSectors = async () => {
      setLoading(true); setError('');
      try {
        const r = await fetch(HS_API(`/api/analysis2/sectors?months=${months}`));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        setSectors(d);
        if (d.length > 0) setSelSector((prev) => prev && d.find((x) => x.sector_key === prev.sector_key) ? d.find((x) => x.sector_key === prev.sector_key) : d[0]);
      } catch(e) { setError('섹터 데이터 로드 실패: ' + e.message); }
      finally { setLoading(false); }
    };

    // 섹터 선택 → 기업 목록 로드
    const loadCompanies = async (sectorKey) => {
      setSelCompany(null); setCompTrend(null); setCompanies([]); setCompanyHs(null); setCompanyPeriod('');
      try {
        const r = await fetch(HS_API(`/api/analysis2/sector/${sectorKey}/companies`));
        const d = await r.json();
        // latest_period가 없는 기업 필터링 (데이터 없음)
        const filtered = d.filter(c => c.latest_period != null);
        setCompanies(filtered);
        if (filtered.length > 0) {
          setSelCompany(filtered[0].stock_code);
          loadCompanyTrend(filtered[0].stock_code, sectorKey);
        }
      } catch {}
    };

    const loadSectorHs = async (sectorKey, periodYm = '') => {
      setSectorHsLoading(true);
      try {
        const qs = new URLSearchParams();
        if (periodYm) qs.set('period_ym', periodYm);
        const r = await fetch(HS_API(`/api/analysis2/sector/${sectorKey}/hs-breakdown?${qs.toString()}`));
        const d = await r.json();
        setSectorHs(d);
        setSectorPeriod(d?.period_ym || '');
      } catch {
        setSectorHs(null);
      } finally {
        setSectorHsLoading(false);
      }
    };

    // 기업 추세 로드
    const loadCompanyTrend = async (stockCode, sectorKey = selSector?.sector_key) => {
      setSelCompany(stockCode); setCompLoading(true);
      try {
        const qs = new URLSearchParams({ months: String(months) });
        if (sectorKey) qs.set('sector_key', sectorKey);
        const r = await fetch(HS_API(`/api/analysis2/company/${stockCode}/trend?${qs.toString()}`));
        const d = await r.json();
        setCompTrend(d);
        loadCompanyHs(stockCode, sectorKey, d?.latest_period || '');
      } catch {}
      finally { setCompLoading(false); }
    };

    const loadCompanyHs = async (stockCode, sectorKey = selSector?.sector_key, periodYm = '') => {
      if (!stockCode || !sectorKey) return;
      setCompanyHsLoading(true);
      try {
        const qs = new URLSearchParams({ sector_key: sectorKey });
        if (periodYm) qs.set('period_ym', periodYm);
        const r = await fetch(HS_API(`/api/analysis2/company/${stockCode}/hs-breakdown?${qs.toString()}`));
        const d = await r.json();
        setCompanyHs(d);
        setCompanyPeriod(d?.period_ym || '');
      } catch {
        setCompanyHs(null);
      } finally {
        setCompanyHsLoading(false);
      }
    };

    // 기업별 탭: 전체 기업 목록 로드
    const loadAllCompanies = async (m = allCompMonths) => {
      setAllCompLoading(true);
      try {
        const r = await fetch(HS_API(`/api/analysis2/companies?months=${m}`));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        setAllCompanies(d);
        if (d.length > 0 && !selAllComp) {
          setSelAllComp(d[0].stock_code);
          loadAllCompTrend(d[0].stock_code, m);
        }
      } catch(e) { console.error('기업 목록 로드 실패', e); }
      finally { setAllCompLoading(false); }
    };

    const loadAllCompTrend = async (stockCode, m = allCompMonths) => {
      setSelAllComp(stockCode);
      setAllCompTrendLoading(true);
      setAllCompHs(null); setAllCompHsPeriod('');
      try {
        const r = await fetch(HS_API(`/api/analysis2/company/${stockCode}/trend?months=${m}`));
        const d = await r.json();
        setAllCompTrend(d);
        if (d?.latest_period) loadAllCompHs(stockCode, d.latest_period);
      } catch(e) { console.error('기업 트렌드 로드 실패', e); }
      finally { setAllCompTrendLoading(false); }
    };

    const loadAllCompHs = async (stockCode, periodYm = '') => {
      setAllCompHsLoading(true);
      try {
        const qs = new URLSearchParams();
        if (periodYm) qs.set('period_ym', periodYm);
        const r = await fetch(HS_API(`/api/analysis2/company/${stockCode}/by-product?${qs.toString()}`));
        const d = await r.json();
        setAllCompHs(d);
        setAllCompHsPeriod(d?.period_ym || periodYm);
      } catch(e) { console.error('기업 HS 로드 실패', e); }
      finally { setAllCompHsLoading(false); }
    };

    React.useEffect(() => { loadSectors(); }, [months]);
    React.useEffect(() => {
      if (selSector) {
        setSectorTab('trend');
        setSectorPeriod('');
        loadCompanies(selSector.sector_key);
        loadSectorHs(selSector.sector_key);
      }
    }, [selSector, months]);
    React.useEffect(() => {
      if (mainTab === 'company' && allCompanies.length === 0) {
        loadAllCompanies(allCompMonths);
      }
    }, [mainTab]);

    const TabButton = ({ active, onClick, children }) => (
      <button
        onClick={onClick}
        style={{
          padding:'0.35rem 0.8rem',
          borderRadius:'999px',
          fontSize:'0.76rem',
          cursor:'pointer',
          fontWeight: active ? 700 : 500,
          border: active ? '1px solid rgba(167,139,250,0.45)' : '1px solid var(--glass-border)',
          background: active ? 'rgba(167,139,250,0.14)' : 'rgba(255,255,255,0.04)',
          color: active ? 'var(--accent-purple)' : 'var(--text-secondary)',
        }}
      >
        {children}
      </button>
    );

    const BreakdownTable = ({ items, type = 'sector' }) => {
      if (!items || items.length === 0) {
        return (
          <div style={{padding:'2rem', textAlign:'center', color:'var(--text-secondary)'}}>
            선택 기간의 HS 구성 데이터가 없습니다.
          </div>
        );
      }
      return (
        <div style={{overflowX:'auto', maxHeight:'360px', overflowY:'auto'}}>
          <table className="premium-table" style={{minWidth:'1120px'}}>
            <thead>
              <tr>
                <th>HS 코드</th>
                <th>구분</th>
                <th>품목명</th>
                <th>매핑 상태</th>
                <th style={{textAlign:'right'}}>수출액</th>
                <th style={{textAlign:'right'}}>수출 비중</th>
                <th style={{textAlign:'right'}}>수출중량</th>
                <th style={{textAlign:'right'}}>수입액</th>
                <th style={{textAlign:'right'}}>수입 비중</th>
                <th style={{textAlign:'right'}}>수입중량</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={`${type}-${item.flow_type || 'export'}-${item.hs_code}`}>
                  <td style={{fontFamily:'monospace', fontWeight:700}}>{item.hs_code}</td>
                  <td>
                    <span style={{
                      fontSize:'0.68rem',
                      padding:'0.14rem 0.45rem',
                      borderRadius:'999px',
                      background: item.flow_type === 'import' ? 'rgba(96,165,250,0.14)' : 'rgba(248,113,113,0.14)',
                      color: item.flow_type === 'import' ? '#93c5fd' : '#fca5a5',
                      border:'1px solid rgba(255,255,255,0.12)'
                    }}>
                      {item.flow_type === 'import' ? '수입' : '수출'}
                    </span>
                  </td>
                  <td style={{maxWidth:'320px'}}>{item.hs_name}</td>
                  <td>
                    <span style={{
                      fontSize:'0.68rem',
                      padding:'0.14rem 0.45rem',
                      borderRadius:'999px',
                      background:
                        item.mapping_status === 'exact' ? 'rgba(52,211,153,0.14)' :
                        item.mapping_status === 'composite' ? 'rgba(250,204,21,0.14)' :
                        'rgba(248,113,113,0.14)',
                      color:
                        item.mapping_status === 'exact' ? '#34d399' :
                        item.mapping_status === 'composite' ? '#facc15' :
                        '#f87171',
                      border:'1px solid rgba(255,255,255,0.12)'
                    }}>
                      {item.mapping_status}
                    </span>
                  </td>
                  <td style={{textAlign:'right', fontWeight:700}}>{fmt(item.export_val)}</td>
                  <td style={{textAlign:'right', color:'#facc15'}}>{item.export_share == null ? '-' : `${item.export_share.toFixed(2)}%`}</td>
                  <td style={{textAlign:'right'}}>{fmt(item.export_kg)}</td>
                  <td style={{textAlign:'right', color:'#93c5fd'}}>{fmt(item.import_val)}</td>
                  <td style={{textAlign:'right', color:'#93c5fd'}}>{item.import_share == null ? '-' : `${item.import_share.toFixed(2)}%`}</td>
                  <td style={{textAlign:'right', color:'rgba(255,255,255,0.72)'}}>{fmt(item.import_kg)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    };

    // 미니 바 차트 렌더러
    const SparkBar = ({ monthly, color = '#a78bfa' }) => {
      if (!monthly || monthly.length === 0) return <span style={{color:'rgba(255,255,255,0.2)'}}>-</span>;
      const vals = monthly.map(m => m.export_val);
      const max = Math.max(...vals) || 1;
      const show = vals.slice(-12);
      const smax = Math.max(...show) || 1;
      return (
        <div style={{display:'flex', alignItems:'flex-end', gap:'1px', height:'28px', padding:'2px 0'}}>
          {show.map((v, i) => (
            <div key={i} style={{
              width: '6px', borderRadius: '2px 2px 0 0',
              background: i === show.length-1 ? '#f59e0b' : color,
              height: `${Math.max(3, (v / smax) * 24)}px`,
              opacity: 0.7 + i * 0.025,
            }} />
          ))}
        </div>
      );
    };

    const SectorChart = ({ sector }) => {
      if (!sector || !sector.monthly || sector.monthly.length === 0) {
        return (
          <div style={{padding:'2.5rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
            섹터 데이터를 선택하면 추세 차트가 표시됩니다.
          </div>
        );
      }
      const monthly = sector.monthly.slice(-24);
      const exports = monthly.map((m) => m.export_val || 0);
      const maxExport = Math.max(...exports, 1);
      const unitPrices = monthly.map((m) => (m.export_kg ? (m.export_val / m.export_kg) : null));
      const validUnitPrices = unitPrices.filter((v) => v != null);
      const maxUnitPrice = Math.max(...(validUnitPrices.length ? validUnitPrices : [1]));
      const minUnitPrice = Math.min(...(validUnitPrices.length ? validUnitPrices : [0]));

      return (
        <div style={{display:'flex', flexDirection:'column', gap:'1rem'}}>
          <div style={{display:'grid', gridTemplateColumns:isMobile ? '1fr 1fr' : 'repeat(6, minmax(0, 1fr))', gap:'0.75rem'}}>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>선택 섹터</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{sector.label}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>최신 수출액</p>
              <button
                onClick={() => {
                  setSectorTab('hs');
                  loadSectorHs(sector.sector_key, sector.latest_period || '');
                }}
                style={{fontSize:'0.95rem', fontWeight:800, color:'#a78bfa', background:'transparent', border:'none', padding:0, cursor:'pointer'}}
                title="클릭하면 최신 수출액의 HS 구성표를 확인합니다"
              >
                {fmtB(sector.export_latest)}
              </button>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>수출 전월 대비</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{pct(sector.export_mom)}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>수출 전년 동월</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{pct(sector.export_yoy)}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>최신 수입액</p>
              <p style={{fontSize:'0.95rem', fontWeight:800, color:'#60a5fa'}}>{fmtB(sector.import_latest)}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>수입 전월 대비</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{pct(sector.import_mom)}</p>
            </div>
          </div>

          <div className="glass-panel" style={{padding:'0.75rem 1rem', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.78rem', color:'#fff'}}>
              <span style={{width:'10px', height:'10px', borderRadius:'3px', background:'rgba(250,204,21,0.52)', display:'inline-block'}} />
              수출 금액
            </span>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.78rem', color:'#fff'}}>
              <span style={{width:'10px', height:'10px', borderRadius:'3px', background:'rgba(96,165,250,0.42)', display:'inline-block'}} />
              수입 금액
            </span>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.78rem', color:'#fff'}}>
              <span style={{width:'18px', height:'2px', background:'#93c5fd', display:'inline-block'}} />
              수출 평균단가
            </span>
            <span style={{fontSize:'0.75rem', color:'var(--text-secondary)', marginLeft:'auto'}}>
              수입은 원자재/부품 선행 신호로 분리 표시됩니다
            </span>
          </div>

          <div className="glass-panel" style={{padding:'1rem', overflowX:'auto'}}>
            <svg viewBox={`0 0 ${monthly.length * 28 + 60} 380`} style={{width:'100%', minWidth:`${monthly.length * 28 + 60}px`, height:'380px'}}>
              {[0,1,2,3,4].map((i) => (
                <line key={i} x1="40" x2={monthly.length * 28 + 40} y1={20 + i * 60} y2={20 + i * 60} stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
              ))}
              {monthly.map((m, i) => {
                const exportH = Math.max(4, (m.export_val / maxExport) * 275);
                const importH = Math.max(2, ((m.import_val || 0) / maxExport) * 275);
                const x = 42 + i * 28;
                return (
                  <g key={m.period_ym}>
                    <rect x={x} y={320 - exportH} width={12} height={exportH}
                      fill={i >= monthly.length - 3 ? 'rgba(52,211,153,0.48)' : 'rgba(250,204,21,0.52)'}
                      rx="3">
                      <title>{`${m.period_ym} | 수출액 ${fmt(m.export_val)} | 수출중량 ${fmt(m.export_kg)}kg`}</title>
                    </rect>
                    <rect x={x + 13} y={320 - importH} width={8} height={importH}
                      fill="rgba(96,165,250,0.42)" rx="3">
                      <title>{`${m.period_ym} | 수입액 ${fmt(m.import_val)} | 수입중량 ${fmt(m.import_kg)}kg`}</title>
                    </rect>
                  </g>
                );
              })}
              {monthly.length > 1 && validUnitPrices.length > 0 && (
                <polyline
                  fill="none"
                  stroke="#93c5fd"
                  strokeWidth="2.4"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  points={monthly.map((m, i) => {
                    const unit = m.export_kg ? (m.export_val / m.export_kg) : minUnitPrice;
                    const y = 320 - (((unit - minUnitPrice) / ((maxUnitPrice - minUnitPrice) || 1)) * 275);
                    return `${42 + i * 28 + 9},${y}`;
                  }).join(' ')}
                />
              )}
              {monthly.filter((_, i) => i % 3 === 0 || i === monthly.length - 1).map((m) => {
                const i = monthly.findIndex((x) => x.period_ym === m.period_ym);
                return (
                  <text key={m.period_ym} x={42 + i * 28 + 9} y={338} fontSize="8" fill="rgba(255,255,255,0.45)" textAnchor="middle">
                    {m.period_ym.slice(2)}
                  </text>
                );
              })}
              {[0,1,2,3,4].map((i) => {
                const value = maxExport - ((maxExport / 4) * i);
                return (
                  <text key={i} x="34" y={24 + i * 60} fontSize="9" fill="rgba(255,255,255,0.42)" textAnchor="end">
                    {fmtAxis(value)}
                  </text>
                );
              })}
              <text x={monthly.length * 28 + 48} y="18" fontSize="9" fill="rgba(255,255,255,0.42)">${maxUnitPrice.toFixed(0)}</text>
              <text x={monthly.length * 28 + 48} y="322" fontSize="9" fill="rgba(255,255,255,0.28)">${minUnitPrice.toFixed(0)}</text>
            </svg>
          </div>
        </div>
      );
    };

    // 기업 라인 차트
    const CompanyChart = ({ data }) => {
      if (!data || !data.monthly || data.monthly.length === 0) {
        return <div style={{padding:'3rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>데이터 없음</div>;
      }
      const monthly = data.monthly;
      const exportVals = monthly.map(m => m.export_val || 0);
      const importVals = monthly.map(m => m.import_val || 0);
      const maxV = Math.max(...exportVals, ...importVals) || 1;
      const minV = 0;

      return (
        <div style={{width:'100%'}}>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.5rem'}}>
            <div style={{display:'flex', gap:'1rem', flexWrap:'wrap'}}>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'220px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>선택 기업</p>
                <p style={{fontSize:'1rem', fontWeight:800, color:'#fff'}}>
                  {data.stock_name} <span style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>({data.stock_code})</span>
                </p>
                <div style={{display:'flex', alignItems:'center', gap:'0.45rem', marginTop:'0.35rem'}}>
                  <span style={{fontSize:'0.7rem', color:'var(--text-secondary)'}}>HS 기준월</span>
                  <select
                    value={companyPeriod || companyHs?.period_ym || data.latest_period || ''}
                    onChange={(e) => loadCompanyHs(selCompany, selSector?.sector_key, e.target.value)}
                    style={{
                      padding:'0.22rem 0.5rem', borderRadius:'6px', fontSize:'0.72rem',
                      background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)', color:'#fff',
                      maxWidth:'110px'
                    }}
                  >
                    {(companyHs?.periods || [data.latest_period].filter(Boolean)).map((period) => (
                      <option key={period} value={period} style={{background:'#1a1a2e'}}>{period}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'120px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>최신 수출액</p>
                <p style={{fontSize:'1rem', fontWeight:700, color:'#a78bfa'}}>{fmtB(data.export_latest)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'100px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>수출 전월 대비</p>
                <p style={{fontSize:'1rem', fontWeight:700}}>{pct(data.export_mom)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'100px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>수출 전년 동월</p>
                <p style={{fontSize:'1rem', fontWeight:700}}>{pct(data.export_yoy)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'120px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>관련 수입액</p>
                <p style={{fontSize:'1rem', fontWeight:700, color:'#60a5fa'}}>{fmtM(data.import_latest)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'100px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>수입 전월 대비</p>
                <p style={{fontSize:'1rem', fontWeight:700}}>{pct(data.import_mom)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>관련 HS 섹터</p>
                <p style={{fontSize:'0.75rem', color:'var(--text-secondary)', maxWidth:'280px', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                  {data.hs_names || monthly[monthly.length-1]?.hs_names || '-'}
                </p>
              </div>
              {data.provisional_10day && (
                <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'190px', borderColor:'rgba(52,211,153,0.28)'}}>
                  <p style={{fontSize:'0.65rem', color:'#86efac'}}>
                    {data.provisional_10day.period_ym} {data.provisional_10day.period_day} 잠정
                  </p>
                  <p style={{fontSize:'0.92rem', fontWeight:800, color:'#34d399'}}>
                    {data.provisional_10day.export_category || '섹터'} {fmtB(data.provisional_10day.export_value || 0)}
                  </p>
                  <p style={{fontSize:'0.68rem', color:'var(--text-secondary)', marginTop:'0.1rem'}}>
                    HS/기업별 확정치 전 대분류 프록시
                  </p>
                </div>
              )}
            </div>
          </div>
          {/* 범례 */}
          <div style={{display:'flex', gap:'1.2rem', flexWrap:'wrap', marginBottom:'0.5rem', padding:'0.4rem 0'}}>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.74rem', color:'rgba(255,255,255,0.7)'}}>
              <span style={{width:'12px', height:'12px', borderRadius:'3px', background:'rgba(167,139,250,0.45)', display:'inline-block'}} />
              수출 (확정)
            </span>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.74rem', color:'rgba(255,255,255,0.7)'}}>
              <span style={{width:'12px', height:'12px', borderRadius:'3px', background:'rgba(249,115,22,0.45)', border:'1.5px dashed rgba(249,115,22,0.9)', display:'inline-block'}} />
              수출 (잠정·추정)
            </span>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.74rem', color:'rgba(255,255,255,0.7)'}}>
              <span style={{width:'12px', height:'12px', borderRadius:'3px', background:'rgba(96,165,250,0.55)', display:'inline-block'}} />
              수입 (확정)
            </span>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.74rem', color:'rgba(255,255,255,0.7)'}}>
              <span style={{width:'18px', height:'2px', background:'#a78bfa', display:'inline-block'}} />
              수출 추세선
            </span>
          </div>
          {/* 바+라인 차트 — 28px/월로 넓게, X축 매월 표시 */}
          <div style={{overflowX:'auto'}}>
            <svg viewBox={`0 0 ${monthly.length * 28 + 50} 370`} style={{width:'100%', minWidth:`${monthly.length * 28 + 50}px`, height:'370px'}}>
              {/* 그리드 라인 */}
              {[0,1,2,3,4].map(i => (
                <line key={i} x1="42" x2={monthly.length * 28 + 42} y1={10 + i*60} y2={10 + i*60}
                  stroke="rgba(255,255,255,0.05)" strokeWidth="1"/>
              ))}
              {/* 바 */}
              {monthly.map((m, i) => {
                const exportH = Math.max(2, ((m.export_val - minV) / (maxV - minV || 1)) * 300);
                const importH = Math.max(0, ((m.import_val - minV) / (maxV - minV || 1)) * 300);
                const x = 42 + i * 28;
                const isLatest = i === monthly.length - 1;
                const isProv = m.is_provisional;
                return (
                  <g key={i}>
                    <rect x={x+1} y={330 - exportH} width={16} height={exportH}
                      fill={isLatest ? 'rgba(245,158,11,0.6)' : isProv ? 'rgba(249,115,22,0.4)' : 'rgba(167,139,250,0.4)'}
                      stroke={isProv ? 'rgba(249,115,22,0.9)' : isLatest ? 'rgba(245,158,11,0.8)' : 'none'}
                      strokeWidth={isProv || isLatest ? '1.5' : '0'}
                      strokeDasharray={isProv ? '4 2' : 'none'}
                      rx="2">
                      <title>{`${m.period_ym}${isProv ? ' ⚠️잠정' : ' ✓확정'}: 수출 ${fmtM(m.export_val)}`}</title>
                    </rect>
                    {/* 잠정 표식 (상단 점) */}
                    {isProv && <circle cx={x+9} cy={330 - exportH - 4} r="2.5" fill="rgba(249,115,22,0.9)" />}
                    <rect x={x+18} y={330 - importH} width={6} height={importH}
                      fill="rgba(96,165,250,0.55)" rx="2">
                      <title>{`${m.period_ym}${isProv ? ' ⚠️잠정' : ' ✓확정'}: 수입 ${fmtM(m.import_val)}`}</title>
                    </rect>
                  </g>
                );
              })}
              {/* 라인 */}
              {monthly.length > 1 && (
                <polyline
                  fill="none"
                  stroke="#a78bfa"
                  strokeWidth="2"
                  strokeLinejoin="round"
                  points={monthly.map((m, i) => {
                    const exportH = ((m.export_val - minV) / (maxV - minV || 1)) * 300;
                    return `${42 + i * 28 + 9},${330 - exportH}`;
                  }).join(' ')}
                />
              )}
              {/* X축 라벨: 매월, 연도 변경 시 굵게 */}
              {monthly.map((m, i) => {
                const isYearStart = m.period_ym.endsWith('-01');
                const label = isYearStart ? m.period_ym.slice(0, 4) : m.period_ym.slice(5, 7) + '월';
                return (
                  <text key={i} x={42 + i * 28 + 9} y={isYearStart ? 349 : 346} fontSize={isYearStart ? '8' : '7'}
                    fill={isYearStart ? 'rgba(255,255,255,0.7)' : 'rgba(255,255,255,0.38)'}
                    fontWeight={isYearStart ? '700' : '400'}
                    textAnchor="middle">
                    {label}
                  </text>
                );
              })}
              {/* 연도 구분선 */}
              {monthly.map((m, i) => m.period_ym.endsWith('-01') && (
                <line key={`yr-${i}`} x1={42 + i * 28} x2={42 + i * 28} y1={10} y2={332}
                  stroke="rgba(255,255,255,0.1)" strokeWidth="1" strokeDasharray="4 3"/>
              ))}
              {/* Y축 */}
              {[0,1,2,3,4].map(i => {
                const val = maxV - (maxV / 4) * i;
                return (
                  <text key={i} x="38" y={14 + i * 60} fontSize="8" fill="rgba(255,255,255,0.35)" textAnchor="end">
                    {fmtAxis(val)}
                  </text>
                );
              })}
            </svg>
          </div>
        </div>
      );
    };

    const sectorColors = {
      semiconductors: '#a78bfa', autos: '#60a5fa', batteries: '#34d399',
      biotech: '#f87171', consumer: '#fb923c', shipbuilding: '#38bdf8', energy_materials: '#facc15',
    };
    const companyExportItems = companyHs?.export_items || (companyHs?.items || []).filter((item) => item.flow_type !== 'import');
    const companyImportItems = companyHs?.import_items || (companyHs?.items || []).filter((item) => item.flow_type === 'import');

    const SECTOR_ORDER = ['반도체','자동차/부품','이차전지','조선/기계','바이오/헬스케어','화장품/소비재','에너지/소재'];

    const filteredAllCompanies = allCompanies.filter(c => {
      const matchSearch = !allCompSearch || c.stock_name?.includes(allCompSearch) || c.stock_code?.includes(allCompSearch);
      const matchSector = allCompSector === 'all' || (c.sector_labels && c.sector_labels.some(l => l === allCompSector));
      return matchSearch && matchSector;
    });

    // 섹터별 그룹화 (검색 중이거나 특정 섹터 선택 시 그룹 헤더 숨김)
    const groupedAllCompanies = React.useMemo(() => {
      if (allCompSearch || allCompSector !== 'all') return [{ sectorLabel: null, companies: filteredAllCompanies }];
      const groups = {};
      filteredAllCompanies.forEach(c => {
        const label = (c.sector_labels && c.sector_labels[0]) || '기타';
        if (!groups[label]) groups[label] = [];
        groups[label].push(c);
      });
      const sorted = [...SECTOR_ORDER.filter(l => groups[l]), ...Object.keys(groups).filter(l => !SECTOR_ORDER.includes(l)).sort()];
      return sorted.map(label => ({ sectorLabel: label, companies: groups[label] }));
    }, [filteredAllCompanies, allCompSearch, allCompSector]);

    // 기업별 탭 섹터 목록 (전체 기업 기반)
    const allCompSectors = React.useMemo(() => {
      const labels = new Set();
      allCompanies.forEach(c => (c.sector_labels || []).forEach(l => l && labels.add(l)));
      return ['all', ...SECTOR_ORDER.filter(l => labels.has(l)), ...[...labels].filter(l => !SECTOR_ORDER.includes(l)).sort()];
    }, [allCompanies]);

    return (
      <div style={{padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1.5rem'}}>
        {/* 헤더 */}
        <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:'0.75rem'}}>
          <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
            {mainTab === 'sector' && (<>
              <span style={{fontSize:'0.75rem', color:'var(--text-secondary)'}}>기간:</span>
              {[12,24,36].map(m => (
                <button key={m} onClick={() => setMonths(m)} style={{
                  padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.75rem', cursor:'pointer',
                  fontWeight: months === m ? 700 : 400,
                  border: months === m ? '1px solid var(--accent-purple)' : '1px solid var(--glass-border)',
                  background: months === m ? 'rgba(167,139,250,0.15)' : 'transparent',
                  color: months === m ? 'var(--accent-purple)' : 'var(--text-secondary)',
                }}>{m}개월</button>
              ))}
              <button onClick={loadSectors} disabled={loading}
                style={{padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.75rem', cursor:'pointer',
                  border:'1px solid var(--glass-border)', background:'rgba(255,255,255,0.05)', color:'var(--text-secondary)'}}>
                {loading ? '⏳' : '🔄'}
              </button>
            </>)}
            {mainTab === 'company' && (<>
              <span style={{fontSize:'0.75rem', color:'var(--text-secondary)'}}>기간:</span>
              {[12,24,36].map(m => (
                <button key={m} onClick={() => { setAllCompMonths(m); loadAllCompanies(m); }} style={{
                  padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.75rem', cursor:'pointer',
                  fontWeight: allCompMonths === m ? 700 : 400,
                  border: allCompMonths === m ? '1px solid var(--accent-purple)' : '1px solid var(--glass-border)',
                  background: allCompMonths === m ? 'rgba(167,139,250,0.15)' : 'transparent',
                  color: allCompMonths === m ? 'var(--accent-purple)' : 'var(--text-secondary)',
                }}>{m}개월</button>
              ))}
              <button onClick={() => loadAllCompanies(allCompMonths)} disabled={allCompLoading}
                style={{padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.75rem', cursor:'pointer',
                  border:'1px solid var(--glass-border)', background:'rgba(255,255,255,0.05)', color:'var(--text-secondary)'}}>
                {allCompLoading ? '⏳' : '🔄'}
              </button>
            </>)}
          </div>
        </div>

        {/* 메인 탭 */}
        <div style={{display:'flex', gap:'0.5rem', borderBottom:'1px solid var(--glass-border)', paddingBottom:'0.75rem'}}>
          {[['sector','🏭 섹터별'],['company','🏢 기업별']].map(([key, label]) => (
            <button key={key} onClick={() => setMainTab(key)} style={{
              padding:'0.5rem 1.2rem', borderRadius:'8px', fontSize:'0.9rem', cursor:'pointer',
              fontWeight: mainTab === key ? 700 : 400,
              border: mainTab === key ? '1px solid rgba(167,139,250,0.45)' : '1px solid var(--glass-border)',
              background: mainTab === key ? 'rgba(167,139,250,0.14)' : 'rgba(255,255,255,0.04)',
              color: mainTab === key ? 'var(--accent-purple)' : 'var(--text-secondary)',
            }}>{label}</button>
          ))}
        </div>

        {error && (
          <div style={{padding:'0.75rem 1rem', background:'rgba(239,68,68,0.12)', border:'1px solid rgba(239,68,68,0.3)',
            borderRadius:'10px', color:'#f87171', fontSize:'0.8rem'}}>⚠️ {error}</div>
        )}

        {/* ── 섹터별 탭 ── */}
        {mainTab === 'sector' && (<>

        {/* ── 상단: 섹터별 수출 추세 표 ── */}
        <div className="glass-panel" style={{overflow:'clip'}}>
          <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.5rem'}}>
            <span style={{fontSize:'1rem'}}>🏭</span>
            <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>섹터별 수출 추세</h2>
            <span style={{fontSize:'0.72rem', color:'var(--text-secondary)', marginLeft:'auto'}}>
              클릭하면 해당 섹터 기업이 아래에 표시됩니다
            </span>
          </div>
          {loading ? (
            <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
              <div style={{width:'28px', height:'28px', border:'2px solid rgba(167,139,250,0.3)', borderTop:'2px solid var(--accent-purple)',
                borderRadius:'50%', animation:'spin 0.8s linear infinite', margin:'0 auto 0.5rem'}} />
              데이터 로딩 중...
            </div>
          ) : (
            <div style={{overflowX:'auto', overflowY:'clip'}}>
              <table className="premium-table" style={{minWidth:'930px'}}>
                <thead><tr>
                  <th>섹터</th>
                  <th style={{textAlign:'right'}}>최신 수출액</th>
                  <th style={{textAlign:'right'}}>최신 수입액</th>
                  <th style={{textAlign:'center'}}>수출 MoM</th>
                  <th style={{textAlign:'center'}}>수입 MoM</th>
                  <th style={{textAlign:'center'}}>수출 YoY</th>
                  <th style={{textAlign:'center'}}>최근 12개월 추세</th>
                </tr></thead>
                <tbody>
                  {sectors.map(s => {
                    const color = sectorColors[s.sector_key] || '#a78bfa';
                    const isSelected = selSector?.sector_key === s.sector_key;
                    return (
                      <tr key={s.sector_key}
                        onClick={() => { setSelSector(s); }}
                        style={{
                          cursor:'pointer',
                          background: isSelected ? 'rgba(167,139,250,0.1)' : undefined,
                          borderLeft: isSelected ? `3px solid ${color}` : '3px solid transparent',
                          transition:'background 0.15s',
                        }}>
                        <td>
                          <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
                            <span style={{width:'8px', height:'8px', borderRadius:'50%', background:color, display:'inline-block', flexShrink:0}} />
                            <span style={{fontWeight:600}}>{s.label}</span>
                          </div>
                        </td>
                        <td style={{textAlign:'right', fontWeight:700, color:'#fff'}}>
                          {fmtB(s.export_latest)}
                        </td>
                        <td style={{textAlign:'right', fontWeight:700, color:'#93c5fd'}}>
                          {fmtB(s.import_latest)}
                        </td>
                        <td style={{textAlign:'center'}}>{pct(s.export_mom)}</td>
                        <td style={{textAlign:'center'}}>{pct(s.import_mom)}</td>
                        <td style={{textAlign:'center'}}>{pct(s.export_yoy)}</td>
                        <td style={{textAlign:'center'}}>
                          <SparkBar monthly={s.monthly} color={color} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="glass-panel" style={{overflow:'clip'}}>
          <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
            <span style={{fontSize:'1rem'}}>📈</span>
            <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>
              선택 섹터 상세 분석
              {selSector && <span style={{color:'var(--accent-purple)', marginLeft:'0.5rem'}}>{selSector.label}</span>}
            </h2>
          </div>

          <div style={{padding:'1rem 1.2rem'}}>
            {!selSector ? (
              <div style={{padding:'3rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
                <p style={{fontSize:'2rem', marginBottom:'0.5rem'}}>☝️</p>
                <p>위 섹터 표에서 관심 섹터를 클릭하세요</p>
              </div>
            ) : (
              <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.75rem', flexWrap:'wrap', marginBottom:'1rem'}}>
                <div style={{display:'flex', gap:'0.45rem', flexWrap:'wrap'}}>
                  <TabButton active={sectorTab === 'trend'} onClick={() => setSectorTab('trend')}>월별 추세</TabButton>
                </div>
                {sectorTab === 'hs' && (
                  <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
                    <span style={{fontSize:'0.74rem', color:'var(--text-secondary)'}}>기준월</span>
                    <select
                      value={sectorPeriod || sectorHs?.period_ym || ''}
                      onChange={(e) => loadSectorHs(selSector.sector_key, e.target.value)}
                      style={{
                        padding:'0.3rem 0.65rem', borderRadius:'7px', fontSize:'0.78rem',
                        background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)', color:'#fff'
                      }}
                    >
                      {(sectorHs?.periods || []).map((period) => (
                        <option key={period} value={period} style={{background:'#1a1a2e'}}>{period}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            )}
            {!selSector ? null : sectorTab === 'trend' ? (
              <div style={{display:'flex', flexDirection:'column', gap:'1rem'}}>
                <SectorChart sector={selSector} />
                <div style={{
                  border:'1px solid var(--glass-border)',
                  borderRadius:'12px',
                  background:'rgba(255,255,255,0.03)',
                  overflow:'hidden'
                }}>
                  <div style={{
                    padding:'0.8rem 1rem',
                    borderBottom:'1px solid var(--glass-border)',
                    display:'flex',
                    justifyContent:'space-between',
                    alignItems:'center',
                    gap:'0.75rem',
                    flexWrap:'wrap'
                  }}>
                    <div>
                      <div style={{fontSize:'0.9rem', fontWeight:700, color:'#fff'}}>해당 수출액을 구성하는 HS 상세</div>
                      <div style={{fontSize:'0.74rem', color:'var(--text-secondary)', marginTop:'0.15rem'}}>
                        선택 섹터의 최신 기준월 HS 코드별 수출/수입 비중
                      </div>
                    </div>
                    <button
                      onClick={() => {
                        setSectorTab('hs');
                        if (!sectorHs) loadSectorHs(selSector.sector_key, selSector.latest_period || '');
                      }}
                      style={{
                        padding:'0.35rem 0.75rem',
                        borderRadius:'999px',
                        fontSize:'0.75rem',
                        border:'1px solid rgba(167,139,250,0.35)',
                        background:'rgba(167,139,250,0.12)',
                        color:'var(--accent-purple)',
                        cursor:'pointer'
                      }}
                    >
                      전체 HS 구성 보기
                    </button>
                  </div>
                  {sectorHsLoading ? (
                    <div style={{padding:'1.5rem', textAlign:'center', color:'var(--text-secondary)'}}>HS 상세를 불러오는 중...</div>
                  ) : (
                    <BreakdownTable items={(sectorHs?.items || []).slice(0, 8)} type="sector" />
                  )}
                </div>
              </div>
            ) : sectorHsLoading ? (
              <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                HS 구성 데이터를 불러오는 중...
              </div>
            ) : (
              <BreakdownTable items={sectorHs?.items || []} type="sector" />
            )}
          </div>
        </div>

        {/* ── 하단: 기업별 수출 추세 ── */}
        <div className="glass-panel" style={{overflow:'clip'}}>
          <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
            <span style={{fontSize:'1rem'}}>🏢</span>
            <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>
              기업별 수출 추세
              {selSector && <span style={{color:'var(--accent-purple)', marginLeft:'0.5rem'}}>{selSector.label}</span>}
            </h2>
            {/* 기업 드롭다운 */}
            {companies.length > 0 && (
              <select
                value={selCompany || ''}
                onChange={e => loadCompanyTrend(e.target.value)}
                style={{
                  padding:'0.3rem 0.65rem', borderRadius:'7px', fontSize:'0.82rem',
                  background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)',
                  color:'#fff', cursor:'pointer', outline:'none', marginLeft:'auto',
                }}>
                {companies.map(c => (
                  <option key={c.stock_code} value={c.stock_code} style={{background:'#1a1a2e'}}>
                    {c.stock_name} ({c.stock_code}){formatCompositionLabel(c.sector_name, c.hs_names) ? ` - ${formatCompositionLabel(c.sector_name, c.hs_names)}` : ''}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div style={{padding:'1rem 1.2rem'}}>
            {!selSector ? (
              <div style={{padding:'3rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
                <p style={{fontSize:'2rem', marginBottom:'0.5rem'}}>☝️</p>
                <p>위 섹터 표에서 관심 섹터를 클릭하세요</p>
              </div>
            ) : compLoading ? (
              <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                <div style={{width:'28px', height:'28px', border:'2px solid rgba(167,139,250,0.3)', borderTop:'2px solid var(--accent-purple)',
                  borderRadius:'50%', animation:'spin 0.8s linear infinite', margin:'0 auto 0.5rem'}} />
                기업 데이터 로딩 중...
              </div>
            ) : compTrend ? (
              <div>
                <div style={{display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'1rem'}}>
                  <span style={{fontSize:'1.1rem', fontWeight:800, color:'#fff'}}>{compTrend.stock_name}</span>
                  <span style={{fontSize:'0.75rem', color:'var(--text-secondary)'}}>({compTrend.stock_code})</span>
                  {String(compTrend.sector_name || '')
                    .split(',')
                    .filter(Boolean)
                    .map((sectorName) => (
                      <span key={sectorName} style={{fontSize:'0.72rem', padding:'0.1rem 0.5rem', borderRadius:'10px',
                        background:'rgba(167,139,250,0.15)', color:'var(--accent-purple)', border:'1px solid rgba(167,139,250,0.3)'}}>
                        {sectorName}
                      </span>
                    ))}
                  <span style={{fontSize:'0.68rem', padding:'0.12rem 0.45rem', borderRadius:'999px',
                    background:
                      compTrend.mapping_status === 'exact' ? 'rgba(52,211,153,0.14)' :
                      compTrend.mapping_status === 'composite' ? 'rgba(250,204,21,0.14)' :
                      'rgba(248,113,113,0.14)',
                    color:
                      compTrend.mapping_status === 'exact' ? '#34d399' :
                      compTrend.mapping_status === 'composite' ? '#facc15' :
                      '#f87171',
                    border:'1px solid rgba(255,255,255,0.12)'}}>
                    {compTrend.mapping_status === 'exact' ? 'exact' : compTrend.mapping_status === 'composite' ? 'composite' : 'provisional'}
                  </span>
                </div>
                <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.75rem', flexWrap:'wrap', marginBottom:'1rem'}}>
                  <div style={{display:'flex', gap:'0.45rem', flexWrap:'wrap'}}>
                    {companyExportItems.slice(0, 4).map((item) => (
                      <span key={`export-chip-${item.hs_code}`} style={{
                        fontSize:'0.72rem',
                        padding:'0.28rem 0.55rem',
                        borderRadius:'999px',
                        background:'rgba(255,255,255,0.05)',
                        border:'1px solid var(--glass-border)',
                        color:'#e5e7eb'
                      }}>
                        수출 {item.hs_name} {item.export_share != null ? `(${item.export_share.toFixed(1)}%)` : ''}
                      </span>
                    ))}
                    {companyImportItems.slice(0, 3).map((item) => (
                      <span key={`import-chip-${item.hs_code}`} style={{
                        fontSize:'0.72rem',
                        padding:'0.28rem 0.55rem',
                        borderRadius:'999px',
                        background:'rgba(96,165,250,0.1)',
                        border:'1px solid rgba(96,165,250,0.28)',
                        color:'#bfdbfe'
                      }}>
                        수입 {item.hs_name} {item.import_share != null ? `(${item.import_share.toFixed(1)}%)` : ''}
                      </span>
                    ))}
                  </div>
                  <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
                    <span style={{fontSize:'0.74rem', color:'var(--text-secondary)'}}>HS 기준월</span>
                    <select
                      value={companyPeriod || companyHs?.period_ym || ''}
                      onChange={(e) => loadCompanyHs(selCompany, selSector?.sector_key, e.target.value)}
                      style={{
                        padding:'0.3rem 0.65rem', borderRadius:'7px', fontSize:'0.78rem',
                        background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)', color:'#fff'
                      }}
                    >
                      {(companyHs?.periods || []).map((period) => (
                        <option key={period} value={period} style={{background:'#1a1a2e'}}>{period}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div style={{
                  marginBottom:'1rem',
                  padding:'0.9rem 1rem',
                  borderRadius:'12px',
                  border:'1px solid var(--glass-border)',
                  background:'rgba(255,255,255,0.04)'
                }}>
                  <div style={{fontSize:'0.78rem', color:'var(--text-secondary)', marginBottom:'0.2rem'}}>구성 요약</div>
                  <div style={{fontSize:'0.95rem', fontWeight:700, color:'#fff'}}>
                    {formatCompositionLabel(compTrend.sector_name, compTrend.hs_names) || '-'}
                  </div>
                  <div style={{fontSize:'0.74rem', color:'var(--text-secondary)', marginTop:'0.35rem'}}>
                    {String(compTrend.hs_names || '').split(',').map((v) => v.trim()).filter(Boolean).length > 1
                      ? `현재 선택 기업은 ${String(compTrend.hs_names || '').split(',').map((v) => v.trim()).filter(Boolean).length}개 HS 코드 합산으로 계산됩니다.`
                      : '현재 선택 기업은 단일 HS 코드 기준으로 계산됩니다.'}
                  </div>
                </div>
                <CompanyChart data={compTrend} />
                <div style={{
                  marginTop:'1rem',
                  border:'1px solid var(--glass-border)',
                  borderRadius:'12px',
                  background:'rgba(255,255,255,0.03)',
                  overflow:'hidden'
                }}>
                  <div style={{
                    padding:'0.8rem 1rem',
                    borderBottom:'1px solid var(--glass-border)',
                    display:'flex',
                    justifyContent:'space-between',
                    alignItems:'center',
                    gap:'0.75rem',
                    flexWrap:'wrap'
                  }}>
                    <div>
                      <div style={{fontSize:'0.9rem', fontWeight:700, color:'#fff'}}>기업 HS 비중 상세</div>
                      <div style={{fontSize:'0.74rem', color:'var(--text-secondary)', marginTop:'0.15rem'}}>
                        {compTrend.stock_name}의 선택 섹터 내 HS 코드별 수출/수입 비중
                      </div>
                    </div>
                  </div>
                  {companyHsLoading ? (
                    <div style={{padding:'1.5rem', textAlign:'center', color:'var(--text-secondary)'}}>HS 비중 데이터를 불러오는 중...</div>
                  ) : (
                    <div style={{display:'flex', flexDirection:'column', gap:'1rem', padding:'1rem'}}>
                      <div style={{border:'1px solid rgba(248,113,113,0.16)', borderRadius:'10px', overflow:'hidden', background:'rgba(248,113,113,0.03)'}}>
                        <div style={{padding:'0.7rem 0.9rem', borderBottom:'1px solid rgba(248,113,113,0.16)', display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
                          <div>
                            <div style={{fontSize:'0.86rem', fontWeight:700, color:'#fecaca'}}>수출 품목</div>
                            <div style={{fontSize:'0.72rem', color:'var(--text-secondary)', marginTop:'0.12rem'}}>기업명 = 수출 제품명 기준으로 매핑된 HS 코드</div>
                          </div>
                          <span style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>{companyExportItems.length}개</span>
                        </div>
                        <BreakdownTable items={companyExportItems} type="company-export" />
                      </div>
                      <div style={{border:'1px solid rgba(96,165,250,0.2)', borderRadius:'10px', overflow:'hidden', background:'rgba(96,165,250,0.04)'}}>
                        <div style={{padding:'0.7rem 0.9rem', borderBottom:'1px solid rgba(96,165,250,0.2)', display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
                          <div>
                            <div style={{fontSize:'0.86rem', fontWeight:700, color:'#bfdbfe'}}>수입 품목</div>
                            <div style={{fontSize:'0.72rem', color:'var(--text-secondary)', marginTop:'0.12rem'}}>장비·원재료 등 수입 실적으로 별도 확인해야 하는 HS 코드</div>
                          </div>
                          <span style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>{companyImportItems.length}개</span>
                        </div>
                        <BreakdownTable items={companyImportItems} type="company-import" />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : companies.length === 0 && selSector ? (
              <div style={{padding:'2rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
                이 섹터에 매핑된 기업이 없습니다.
              </div>
            ) : null}
          </div>
        </div>

        </>)}

        {/* ── 기업별 탭 ── */}
        {mainTab === 'company' && (
          <div style={{display:'flex', flexDirection:'column', gap:'1.5rem'}}>
            {/* 기업 목록 + 검색 */}
            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
                <span style={{fontSize:'1rem'}}>🏢</span>
                <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>전체 기업 수출 현황</h2>
                <span style={{fontSize:'0.72rem', color:'var(--text-secondary)', marginLeft:'auto'}}>
                  {allCompanies.length}개 기업 · 클릭하면 하단에 상세 표시
                </span>
              </div>
              {/* 섹터 선택 탭 */}
              <div style={{padding:'0.6rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', gap:'0.4rem', flexWrap:'wrap', alignItems:'center'}}>
                {allCompSectors.map(s => (
                  <button key={s} onClick={() => { setAllCompSector(s); setAllCompSearch(''); }}
                    style={{
                      padding:'0.25rem 0.65rem', borderRadius:'999px', fontSize:'0.74rem', cursor:'pointer',
                      fontWeight: allCompSector === s ? 700 : 400,
                      border: allCompSector === s ? '1px solid rgba(167,139,250,0.5)' : '1px solid var(--glass-border)',
                      background: allCompSector === s ? 'rgba(167,139,250,0.18)' : 'rgba(255,255,255,0.04)',
                      color: allCompSector === s ? 'var(--accent-purple)' : 'var(--text-secondary)',
                    }}>
                    {s === 'all' ? '전체' : s}
                  </button>
                ))}
              </div>
              <div style={{padding:'0.6rem 1.2rem', borderBottom:'1px solid var(--glass-border)'}}>
                <input
                  type="text"
                  value={allCompSearch}
                  onChange={e => setAllCompSearch(e.target.value)}
                  placeholder="기업명 또는 종목코드 검색..."
                  style={{
                    width:'100%', padding:'0.5rem 0.85rem', borderRadius:'8px', fontSize:'0.85rem',
                    background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)', color:'#fff', outline:'none',
                    boxSizing:'border-box'
                  }}
                />
              </div>
              {allCompLoading ? (
                <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                  <div style={{width:'28px', height:'28px', border:'2px solid rgba(167,139,250,0.3)', borderTop:'2px solid var(--accent-purple)',
                    borderRadius:'50%', animation:'spin 0.8s linear infinite', margin:'0 auto 0.5rem'}} />
                  기업 목록 로딩 중...
                </div>
              ) : (
                <div style={{overflowX:'auto', maxHeight:'320px', overflowY:'auto'}}>
                  <table className="premium-table" style={{minWidth:'700px'}}>
                    <thead><tr>
                      <th>기업명</th>
                      <th style={{textAlign:'right'}}>최신 수출액</th>
                      <th style={{textAlign:'center'}}>수출 MoM</th>
                      <th style={{textAlign:'center'}}>수출 YoY</th>
                      <th style={{textAlign:'right'}}>최신 수입액</th>
                      <th style={{textAlign:'center'}}>수입 MoM</th>
                      <th style={{textAlign:'center'}}>최근 추세</th>
                    </tr></thead>
                    <tbody>
                      {groupedAllCompanies.map(({ sectorLabel, companies: grpComps }) => {
                        const color = sectorLabel ? (sectorColors[
                          sectorLabel === '반도체' ? 'semiconductors' :
                          sectorLabel === '자동차/부품' ? 'autos' :
                          sectorLabel === '이차전지' ? 'batteries' :
                          sectorLabel === '바이오/헬스케어' ? 'biotech' :
                          sectorLabel === '화장품/소비재' ? 'consumer' :
                          sectorLabel === '조선/기계' ? 'shipbuilding' :
                          sectorLabel === '에너지/소재' ? 'energy_materials' : ''
                        ] || '#94a3b8') : null;
                        return (
                          <React.Fragment key={sectorLabel || 'all'}>
                            {sectorLabel && (
                              <tr style={{background:'rgba(255,255,255,0.03)', pointerEvents:'none'}}>
                                <td colSpan={7} style={{
                                  padding:'0.4rem 0.8rem',
                                  fontSize:'0.72rem', fontWeight:700,
                                  color: color, letterSpacing:'0.03em',
                                  borderLeft: `3px solid ${color}`,
                                  borderBottom:'1px solid rgba(255,255,255,0.06)'
                                }}>
                                  ▸ {sectorLabel} <span style={{fontWeight:400, color:'rgba(255,255,255,0.4)', marginLeft:'0.4rem'}}>{grpComps.length}개사</span>
                                </td>
                              </tr>
                            )}
                            {grpComps.map(c => {
                              const isSelected = selAllComp === c.stock_code;
                              return (
                                <tr key={c.stock_code}
                                  onClick={() => { loadAllCompTrend(c.stock_code, allCompMonths); }}
                                  style={{
                                    cursor:'pointer',
                                    background: isSelected ? 'rgba(167,139,250,0.1)' : undefined,
                                    borderLeft: isSelected ? `3px solid ${color || 'var(--accent-purple)'}` : '3px solid transparent',
                                    transition:'background 0.15s',
                                  }}>
                                  <td>
                                    <div style={{fontWeight:600}}>{c.stock_name}</div>
                                    <div style={{fontSize:'0.7rem', color:'var(--text-secondary)'}}>{c.stock_code}
                                      {c.mapping_status === 'provisional' && (
                                        <span style={{marginLeft:'0.3rem', fontSize:'0.62rem', color:'#f59e0b'}}>추정</span>
                                      )}
                                    </div>
                                  </td>
                                  <td style={{textAlign:'right', fontWeight:700}}>{c.total_export ? fmtM(c.total_export) : '-'}</td>
                                  <td style={{textAlign:'center'}}>{pct(c.export_mom)}</td>
                                  <td style={{textAlign:'center'}}>{pct(c.export_yoy)}</td>
                                  <td style={{textAlign:'right', color:'#93c5fd'}}>{fmtM(c.import_latest)}</td>
                                  <td style={{textAlign:'center'}}>{pct(c.import_mom)}</td>
                                  <td style={{textAlign:'center'}}>
                                    <SparkBar monthly={c.monthly} color={color || '#a78bfa'} />
                                  </td>
                                </tr>
                              );
                            })}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* 선택 기업 상세 */}
            {selAllComp && (
              <div className="glass-panel" style={{overflow:'clip'}}>
                <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
                  <span style={{fontSize:'1rem'}}>📈</span>
                  <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>
                    기업 수출입 상세
                    {allCompTrend && <span style={{color:'var(--accent-purple)', marginLeft:'0.5rem'}}>{allCompTrend.stock_name}</span>}
                  </h2>
                  {allCompHs?.periods?.length > 0 && (
                    <div style={{display:'flex', alignItems:'center', gap:'0.5rem', marginLeft:'auto'}}>
                      <span style={{fontSize:'0.74rem', color:'var(--text-secondary)'}}>HS 기준월</span>
                      <select
                        value={allCompHsPeriod || allCompHs?.period_ym || ''}
                        onChange={e => { setAllCompHsPeriod(e.target.value); loadAllCompHs(selAllComp, e.target.value); }}
                        style={{
                          padding:'0.3rem 0.65rem', borderRadius:'7px', fontSize:'0.78rem',
                          background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)', color:'#fff'
                        }}
                      >
                        {allCompHs.periods.map(p => (
                          <option key={p} value={p} style={{background:'#1a1a2e'}}>{p}</option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>
                <div style={{padding:'1rem 1.2rem'}}>
                  {allCompTrendLoading ? (
                    <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                      <div style={{width:'28px', height:'28px', border:'2px solid rgba(167,139,250,0.3)', borderTop:'2px solid var(--accent-purple)',
                        borderRadius:'50%', animation:'spin 0.8s linear infinite', margin:'0 auto 0.5rem'}} />
                      기업 데이터 로딩 중...
                    </div>
                  ) : allCompTrend ? (
                    <div style={{display:'flex', flexDirection:'column', gap:'1.5rem'}}>
                      <CompanyChart data={allCompTrend} />
                      {/* 월별 데이터 테이블 */}
                      <div style={{overflowX:'auto', maxHeight:'300px', overflowY:'auto'}}>
                        <table className="premium-table ta2-sticky" style={{fontSize:'0.78rem', minWidth:'700px'}}>
                          <thead><tr>
                            <th>기간</th>
                            <th style={{textAlign:'left', fontSize:'0.68rem', color:'rgba(255,255,255,0.5)', fontWeight:400}}>확정/잠정</th>
                            <th style={{textAlign:'right'}}>수출액</th>
                            <th style={{textAlign:'right'}}>수입액</th>
                            <th style={{textAlign:'right'}}>수출 MoM</th>
                            <th style={{textAlign:'right'}}>수출 YoY</th>
                            <th style={{textAlign:'right'}}>수입 MoM</th>
                            <th style={{textAlign:'left'}}>주요 HS</th>
                          </tr></thead>
                          <tbody>
                            {[...allCompTrend.monthly].reverse().map((m, i, arr) => {
                              const prev1  = arr[i + 1];
                              const prev12 = arr[i + 12];
                              const exportMom = prev1  ? (m.export_val - prev1.export_val)  / (prev1.export_val  || 1) * 100 : null;
                              const exportYoy = prev12 ? (m.export_val - prev12.export_val) / (prev12.export_val || 1) * 100 : null;
                              const importMom = prev1  ? (m.import_val - prev1.import_val)  / (prev1.import_val  || 1) * 100 : null;
                              return (
                                <tr key={m.period_ym} style={{opacity: i === 0 ? 1 : 0.9, background: m.is_provisional ? 'rgba(249,115,22,0.04)' : undefined}}>
                                  <td style={{fontWeight: i === 0 ? 700 : 400}}>{m.period_ym}</td>
                                  <td>
                                    {m.is_provisional
                                      ? <span style={{fontSize:'0.65rem', padding:'0.1rem 0.4rem', borderRadius:'4px', background:'rgba(249,115,22,0.18)', color:'#fb923c', border:'1px dashed rgba(249,115,22,0.5)'}}>잠정</span>
                                      : <span style={{fontSize:'0.65rem', padding:'0.1rem 0.4rem', borderRadius:'4px', background:'rgba(52,211,153,0.12)', color:'#34d399', border:'1px solid rgba(52,211,153,0.3)'}}>확정</span>
                                    }
                                  </td>
                                  <td style={{textAlign:'right', fontWeight: i === 0 ? 700 : 400, color: m.is_provisional ? '#fb923c' : undefined}}>
                                    ${(m.export_val / 1e6).toFixed(2)}M
                                  </td>
                                  <td style={{textAlign:'right', color:'#93c5fd'}}>
                                    ${(m.import_val / 1e6).toFixed(2)}M
                                  </td>
                                  <td style={{textAlign:'right'}}>{pct(exportMom == null ? null : parseFloat(exportMom.toFixed(1)))}</td>
                                  <td style={{textAlign:'right'}}>{pct(exportYoy == null ? null : parseFloat(exportYoy.toFixed(1)))}</td>
                                  <td style={{textAlign:'right'}}>{pct(importMom == null ? null : parseFloat(importMom.toFixed(1)))}</td>
                                  <td style={{fontSize:'0.72rem', color:'var(--text-secondary)', maxWidth:'280px'}}>{m.hs_names || '-'}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      {/* HS 품목별 구성 */}
                      {allCompHsLoading ? (
                        <div style={{padding:'1.5rem', textAlign:'center', color:'var(--text-secondary)'}}>HS 구성 로딩 중...</div>
                      ) : allCompHs?.items?.length > 0 ? (
                        <div style={{border:'1px solid var(--glass-border)', borderRadius:'12px', overflow:'hidden', background:'rgba(255,255,255,0.03)'}}>
                          <div style={{padding:'0.8rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem'}}>
                            <div>
                              <div style={{fontSize:'0.9rem', fontWeight:700, color:'#fff'}}>HS 품목별 구성</div>
                              <div style={{fontSize:'0.74rem', color:'var(--text-secondary)', marginTop:'0.12rem'}}>
                                최근 3개월 합산 기준
                              </div>
                            </div>
                          </div>
                          <div style={{overflowX:'auto', maxHeight:'360px', overflowY:'auto'}}>
                            <table className="premium-table" style={{minWidth:'900px'}}>
                              <thead><tr>
                                <th>HS 코드</th>
                                <th>구분</th>
                                <th>품목명</th>
                                <th style={{textAlign:'right'}}>수출액(3M)</th>
                                <th style={{textAlign:'right'}}>수출 비중</th>
                                <th style={{textAlign:'right'}}>평균단가/kg</th>
                                <th style={{textAlign:'right'}}>수입액(3M)</th>
                                <th style={{textAlign:'center'}}>최근 추세</th>
                              </tr></thead>
                              <tbody>
                                {allCompHs.items.map((item, idx) => (
                                  <tr key={`byproduct-${item.hs_code}-${idx}`}>
                                    <td style={{fontFamily:'monospace', fontWeight:700}}>{item.hs_code}</td>
                                    <td>
                                      <span style={{
                                        fontSize:'0.68rem', padding:'0.14rem 0.45rem', borderRadius:'999px',
                                        background: item.flow_type === 'import' ? 'rgba(96,165,250,0.14)' : 'rgba(248,113,113,0.14)',
                                        color: item.flow_type === 'import' ? '#93c5fd' : '#fca5a5',
                                        border:'1px solid rgba(255,255,255,0.12)'
                                      }}>{item.flow_type === 'import' ? '수입' : '수출'}</span>
                                    </td>
                                    <td style={{maxWidth:'280px'}}>{item.hs_name}</td>
                                    <td style={{textAlign:'right', fontWeight:700}}>{fmt(item.export_3m)}</td>
                                    <td style={{textAlign:'right', color:'#facc15'}}>
                                      {item.export_share != null ? `${item.export_share.toFixed(1)}%` : '-'}
                                    </td>
                                    <td style={{textAlign:'right', color:'rgba(255,255,255,0.72)'}}>
                                      {item.avg_unit_price_kg != null ? `$${item.avg_unit_price_kg.toLocaleString()}` : '-'}
                                    </td>
                                    <td style={{textAlign:'right', color:'#93c5fd'}}>{fmt(item.import_3m)}</td>
                                    <td style={{textAlign:'center'}}>
                                      <SparkBar monthly={item.monthly || []} color="#a78bfa" />
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            )}
          </div>
        )}

      </div>
    );
  };


  // ── 섹터 보고서 페이지 ────────────────────────────────────────
  const SectorReports = () => {
    const isMobile = useIsMobile();
    const [sectors,  setSectors]  = React.useState([]);
    const [selected, setSelected] = React.useState('');
    const [reports,  setReports]  = React.useState([]);
    const [loading,  setLoading]  = React.useState(false);

    React.useEffect(() => {
      fetch(API('/api/reports/sectors'))
        .then(r => r.ok ? r.json() : [])
        .then(d => { setSectors(d); if(d.length>0) setSelected(d[0].sector); })
        .catch(() => {});
    }, []);

    React.useEffect(() => {
      if (!selected) return;
      setLoading(true);
      fetch(API(`/api/reports/sector/${encodeURIComponent(selected)}`))
        .then(r => r.ok ? r.json() : [])
        .then(d => { setReports(d||[]); setLoading(false); })
        .catch(() => setLoading(false));
    }, [selected]);

    const ICONS = {
      '반도체':'💾','IT/전자':'📱','2차전지/EV':'🔋','자동차':'🚗',
      '정유/화학':'🛢️','바이오/제약':'💊','금융':'🏦','통신':'📡',
      '건설/부동산':'🏗️','철강/소재':'⚙️','조선/기계':'🚢','유통/소비재':'🛍️',
      '게임/엔터':'🎮','해운/물류':'📦','전력/신재생':'⚡',
      '코스피시장':'📈','코스닥시장':'📊','해외/글로벌':'🌏',
    };

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <h2 style={{fontSize:'1rem',fontWeight:700}}>📄 섹터별 보고서</h2>
          <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>
            총 {sectors.reduce((s,x)=>s+x.count,0)}건
          </span>
        </div>
        {/* 섹터 탭 */}
        <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem'}}>
          {sectors.map(s => (
            <button key={s.sector} onClick={()=>setSelected(s.sector)} style={{
              padding:'0.35rem 0.8rem',borderRadius:'20px',fontSize:'0.78rem',cursor:'pointer',
              fontWeight: selected===s.sector?700:400,
              border: selected===s.sector?'1px solid var(--accent-mint)':'1px solid var(--glass-border)',
              background: selected===s.sector?'rgba(45,212,191,0.2)':'rgba(255,255,255,0.04)',
              color: selected===s.sector?'var(--accent-mint)':'var(--text-secondary)',
            }}>
              {ICONS[s.sector]||'📄'} {s.sector}
              <span style={{marginLeft:'0.3rem',fontSize:'0.7rem',opacity:0.6}}>{s.count}</span>
            </button>
          ))}
        </div>
        {/* 보고서 목록 */}
        <div className="glass-panel" style={{padding:'1rem'}}>
          {loading ? (
            <div style={{textAlign:'center',padding:'2rem',color:'var(--accent-mint)'}}>로딩 중...</div>
          ) : reports.length===0 ? (
            <p style={{color:'var(--text-secondary)',textAlign:'center',padding:'2rem',fontSize:'0.85rem'}}>
              보고서가 없습니다.
            </p>
          ) : (
            <div style={{display:'flex',flexDirection:'column',gap:'0.35rem'}}>
              {reports.map(r => (
                <div key={r.id} style={{display:'flex',alignItems:'center',
                  justifyContent:'space-between',padding:'0.5rem 0.75rem',
                  borderRadius:'6px',background:'rgba(255,255,255,0.03)',
                  border:'1px solid rgba(255,255,255,0.06)'}}>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{display:'flex',alignItems:'center',gap:'0.4rem',flexWrap:'wrap'}}>
                      <p style={{fontSize:'0.82rem',fontWeight:600,
                        overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',maxWidth:'100%'}}>
                        {r.file_name}
                      </p>
                      {r.stock_code && (
                        <button onClick={()=>{ changeStock(r.stock_code); setActiveTab('analysis'); }}
                          style={{flexShrink:0,padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.68rem',
                            border:'1px solid rgba(96,165,250,0.4)',background:'rgba(96,165,250,0.12)',
                            color:'#93c5fd',cursor:'pointer',whiteSpace:'nowrap'}}>
                          📊 {r.stock_name||r.stock_code}
                        </button>
                      )}
                    </div>
                    <p style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginTop:'0.1rem'}}>
                      {r.report_date} | {r.channel_id}
                      {r.file_size?` | ${(r.file_size/1024).toFixed(0)}KB`:''}
                    </p>
                    {r.caption && (
                      <p style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)',
                        overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                        {r.caption}
                      </p>
                    )}
                  </div>
                  <a href={API(`/api/reports/download/${r.id}`)} download={r.saved_name}
                    style={{marginLeft:'0.75rem',padding:'0.3rem 0.7rem',borderRadius:'5px',
                      background:'rgba(45,212,191,0.12)',border:'1px solid rgba(45,212,191,0.25)',
                      color:'var(--accent-mint)',fontSize:'0.72rem',textDecoration:'none',
                      whiteSpace:'nowrap',flexShrink:0}}>
                    ⬇
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  };


  // ── 시그널 설정 관리 ─────────────────────────────────────────
  const SignalSettings = () => {
    const [configs, setConfigs] = React.useState([]);
    const [editId,  setEditId]  = React.useState(null);
    const [editForm,setEditForm]= React.useState({});
    const [adding,  setAdding]  = React.useState(false);
    const [newForm, setNewForm] = React.useState({
      scope:'stock', label:'', description:'', logic_type:'manual', params:'{}',
    });
    const [manualVals, setManualVals] = React.useState({});

    const load = () => fetch(API('/api/signals/config'))
      .then(r=>r.ok?r.json():[]).then(setConfigs).catch(()=>{});

    React.useEffect(() => { load(); }, []);

    const SCOPE_LABEL = { market:'종합현황', stock:'개별종목' };
    const LOGIC_LABEL = {
      supply_trend:'수급추세', threshold:'임계값', ma_trend:'이평선추세',
      ma_position:'이평선위치', financial:'재무', price_position:'주가위치', manual:'수동입력',
    };
    const SIG_EMOJI = { green:'🟢', yellow:'🟡', red:'🔴' };

    const saveEdit = async () => {
      await fetch(API(`/api/signals/config/${editId}`), {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(editForm),
      });
      setEditId(null); load();
    };

    const toggleActive = async (id, current) => {
      await fetch(API(`/api/signals/config/${id}`), {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({is_active: current ? 0 : 1}),
      });
      load();
    };

    const deleteConfig = async (id) => {
      await fetch(API(`/api/signals/config/${id}`), { method:'DELETE' });
      load();
    };

    const addConfig = async () => {
      await fetch(API('/api/signals/config'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(newForm),
      });
      setAdding(false);
      setNewForm({scope:'stock',label:'',description:'',logic_type:'manual',params:'{}'});
      load();
    };

    const setManual = async (id, sig, val, desc) => {
      await fetch(API(`/api/signals/manual/${id}`), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({signal:sig, value:parseFloat(val)||0, description:desc}),
      });
    };

    const inputS = {
      padding:'0.3rem 0.5rem', borderRadius:'5px', fontSize:'0.8rem',
      background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)',
      color:'#fff',
    };

    const marketCfgs = configs.filter(c=>c.scope==='market');
    const stockCfgs  = configs.filter(c=>c.scope==='stock');

    const renderGroup = (title, cfgs) => (
      <div className="glass-panel" style={{padding:'1rem',marginBottom:'0.75rem'}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.75rem'}}>
          <h4 style={{fontSize:'0.85rem',fontWeight:700,color:'var(--accent-mint)'}}>{title}</h4>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:'0.4rem'}}>
          {cfgs.map(c => editId === c.id ? (
            <div key={c.id} style={{padding:'0.6rem',borderRadius:'6px',background:'rgba(45,212,191,0.05)',border:'1px solid rgba(45,212,191,0.2)'}}>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.4rem',marginBottom:'0.4rem'}}>
                <input value={editForm.label||''} onChange={e=>setEditForm(p=>({...p,label:e.target.value}))}
                  placeholder="표시명" style={inputS}/>
                <input value={editForm.description||''} onChange={e=>setEditForm(p=>({...p,description:e.target.value}))}
                  placeholder="설명" style={inputS}/>
              </div>
              <input value={editForm.params||''} onChange={e=>setEditForm(p=>({...p,params:e.target.value}))}
                placeholder='파라미터 JSON (예: {"days":5})' style={{...inputS,width:'100%',marginBottom:'0.4rem'}}/>
              {c.logic_type === 'manual' && (
                <div style={{display:'flex',gap:'0.4rem',marginBottom:'0.4rem',alignItems:'center'}}>
                  <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>수동값:</span>
                  <input value={manualVals[c.id]?.val||''} onChange={e=>setManualVals(p=>({...p,[c.id]:{...p[c.id],val:e.target.value}}))}
                    placeholder="값" style={{...inputS,width:'80px'}}/>
                  <input value={manualVals[c.id]?.desc||''} onChange={e=>setManualVals(p=>({...p,[c.id]:{...p[c.id],desc:e.target.value}}))}
                    placeholder="설명" style={{...inputS,flex:1}}/>
                  {['green','yellow','red'].map(s=>(
                    <button key={s} onClick={()=>setManual(c.id,s,manualVals[c.id]?.val||0,manualVals[c.id]?.desc||'')}
                      style={{padding:'0.2rem 0.5rem',borderRadius:'4px',cursor:'pointer',fontSize:'0.75rem',
                        background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',color:'#fff'}}>
                      {SIG_EMOJI[s]}
                    </button>
                  ))}
                </div>
              )}
              <div style={{display:'flex',gap:'0.4rem'}}>
                <button onClick={saveEdit} style={{padding:'0.25rem 0.7rem',borderRadius:'5px',background:'var(--accent-mint)',border:'none',color:'#000',cursor:'pointer',fontSize:'0.75rem',fontWeight:700}}>저장</button>
                <button onClick={()=>setEditId(null)} style={{padding:'0.25rem 0.7rem',borderRadius:'5px',background:'transparent',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.75rem'}}>취소</button>
              </div>
            </div>
          ) : (
            <div key={c.id} style={{display:'flex',alignItems:'center',gap:'0.5rem',padding:'0.4rem 0.6rem',borderRadius:'6px',background:'rgba(255,255,255,0.03)',border:'1px solid var(--glass-border)',opacity:c.is_active?1:0.45}}>
              <span style={{fontSize:'0.78rem',fontWeight:600,flex:1}}>{c.label}</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',padding:'0.1rem 0.4rem',background:'rgba(255,255,255,0.05)',borderRadius:'4px'}}>{LOGIC_LABEL[c.logic_type]||c.logic_type}</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',maxWidth:'160px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{c.description}</span>
              <button onClick={()=>{setEditId(c.id);setEditForm({label:c.label,description:c.description,params:c.params,is_active:c.is_active?1:0});}}
                style={{padding:'0.15rem 0.5rem',borderRadius:'4px',background:'rgba(45,212,191,0.1)',border:'1px solid rgba(45,212,191,0.3)',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.7rem'}}>수정</button>
              <button onClick={()=>toggleActive(c.id,c.is_active)}
                style={{padding:'0.15rem 0.5rem',borderRadius:'4px',background:c.is_active?'rgba(251,191,36,0.1)':'rgba(255,255,255,0.05)',border:'1px solid rgba(255,255,255,0.15)',color:c.is_active?'#fbbf24':'#64748b',cursor:'pointer',fontSize:'0.7rem'}}>
                {c.is_active?'활성':'비활성'}
              </button>
              <button onClick={()=>deleteConfig(c.id)}
                style={{padding:'0.15rem 0.5rem',borderRadius:'4px',background:'rgba(239,68,68,0.1)',border:'1px solid rgba(239,68,68,0.3)',color:'#ef4444',cursor:'pointer',fontSize:'0.7rem'}}>삭제</button>
            </div>
          ))}
        </div>
      </div>
    );

    return (
      <div className="glass-panel" style={{padding:'1.2rem'}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'1rem'}}>
          <h3 style={{fontSize:'0.9rem',fontWeight:700,color:'var(--accent-purple)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
            📊 시그널 보드 설정
          </h3>
          <button onClick={()=>setAdding(v=>!v)}
            style={{padding:'0.3rem 0.8rem',borderRadius:'6px',background:'rgba(167,139,250,0.15)',border:'1px solid rgba(167,139,250,0.4)',color:'var(--accent-purple)',cursor:'pointer',fontSize:'0.8rem',fontWeight:600}}>
            + 시그널 추가
          </button>
        </div>

        {adding && (
          <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(167,139,250,0.05)',border:'1px solid rgba(167,139,250,0.2)',marginBottom:'0.75rem'}}>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'0.4rem',marginBottom:'0.4rem'}}>
              <select value={newForm.scope} onChange={e=>setNewForm(p=>({...p,scope:e.target.value}))}
                style={{...inputS}}>
                <option value="market">종합현황</option>
                <option value="stock">개별종목</option>
              </select>
              <input value={newForm.label} onChange={e=>setNewForm(p=>({...p,label:e.target.value}))}
                placeholder="표시명" style={inputS}/>
              <select value={newForm.logic_type} onChange={e=>setNewForm(p=>({...p,logic_type:e.target.value}))}
                style={inputS}>
                <option value="manual">수동입력</option>
                <option value="threshold">임계값</option>
                <option value="supply_trend">수급추세</option>
                <option value="ma_trend">이평선추세</option>
                <option value="financial">재무</option>
              </select>
            </div>
            <input value={newForm.description} onChange={e=>setNewForm(p=>({...p,description:e.target.value}))}
              placeholder="설명" style={{...inputS,width:'100%',marginBottom:'0.4rem'}}/>
            <input value={newForm.params} onChange={e=>setNewForm(p=>({...p,params:e.target.value}))}
              placeholder='파라미터 JSON' style={{...inputS,width:'100%',marginBottom:'0.4rem'}}/>
            <div style={{display:'flex',gap:'0.4rem'}}>
              <button onClick={addConfig} style={{padding:'0.3rem 0.8rem',borderRadius:'5px',background:'var(--accent-purple)',border:'none',color:'#fff',cursor:'pointer',fontSize:'0.78rem',fontWeight:700}}>추가</button>
              <button onClick={()=>setAdding(false)} style={{padding:'0.3rem 0.8rem',borderRadius:'5px',background:'transparent',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.78rem'}}>취소</button>
            </div>
          </div>
        )}

        {renderGroup('📊 종합현황 시그널', marketCfgs)}
        {renderGroup('🔍 개별종목 시그널', stockCfgs)}

        <p style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.3)',marginTop:'0.5rem'}}>
          🟢 매수/보유 &nbsp; 🟡 관망/주의 &nbsp; 🔴 매도/회피 &nbsp;
          수동입력 시그널은 수정 버튼 클릭 후 값 입력
        </p>
      </div>
    );
  };

  // ── AI 리포트 ─────────────────────────────────────────────────
  // [버그 ④ 수정] insight 탭 컴포넌트 구현 및 return에 연결
  const AIInsight = () => {
    const [generating, setGenerating] = React.useState(false);

    const handleGenerate = async () => {
      setGenerating(true);
      try {
        const res = await fetch(API(`/api/reports/generate/${selectedStock}`), { method: 'POST' });
        if (res.ok) setAiReport(await res.json());
      } catch (e) { console.error("Report generate error", e); }
      finally { setGenerating(false); }
    };

    const stockName = watchlist.find(i => i.stock_code === selectedStock)?.stock_name || selectedStock;

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '1.2rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            <Cpu size={20} color="var(--accent-purple)" />
            <h2>AI 분석 리포트</h2>
            <span style={{ marginLeft: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{stockName} ({selectedStock})</span>
          </div>
          <button onClick={handleGenerate} disabled={generating}
            style={{ padding: '0.45rem 1rem', borderRadius: '8px', background: generating ? 'rgba(167,139,250,0.3)' : 'var(--accent-purple)', border: 'none', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}>
            {generating ? '생성 중...' : '리포트 생성'}
          </button>
        </div>

        {aiReport ? (
          <div className="glass-panel" style={{ padding: '1.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
              <FileText size={16} color="var(--accent-purple)" />
              <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                생성일시: {aiReport.report_date ? new Date(aiReport.report_date).toLocaleString('ko-KR') : '-'}
              </p>
            </div>
            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', lineHeight: 1.8, color: 'var(--text-primary)', fontSize: '0.9rem' }}>
              {aiReport.content}
            </pre>
          </div>
        ) : (
          <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <Cpu size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.3 }} />
            <p>아직 생성된 리포트가 없습니다.</p>
            <p style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>위 버튼을 눌러 AI 분석 리포트를 생성하세요.</p>
          </div>
        )}
      </div>
    );
  };


  // ── DART 수주공시 알림 뷰 ────────────────────────────────────
  const DartContractView = () => {
    const [contracts, setContracts] = React.useState([]);
    const [stats, setStats] = React.useState(null);
    const [loading, setLoading] = React.useState(false);
    const [selected, setSelected] = React.useState(null);
    const [detail, setDetail]     = React.useState(null);
    const [filters, setFilters]   = React.useState({
      days: 30, min_signal: 1, signal_type: '', is_overseas: -1
    });

    const SIGNAL_COLOR = { '강한매수':'#ef4444','매수':'#22c55e','관망':'#f59e0b','주의':'#94a3b8' };
    const SIGNAL_EMOJI = { '강한매수':'🚀','매수':'📈','관망':'👀','주의':'⚠️' };
    const STARS = n => '★'.repeat(n||0) + '☆'.repeat(5-(n||0));

    const load = async () => {
      setLoading(true);
      try {
        const qs = new URLSearchParams({
          days: filters.days, min_signal: filters.min_signal, limit: 100,
          ...(filters.signal_type && { signal_type: filters.signal_type }),
          ...(filters.is_overseas >= 0 && { is_overseas: filters.is_overseas }),
        });
        const [listRes, statsRes] = await Promise.all([
          fetch(`/api/dart-contracts/list?${qs}`).then(r=>r.json()),
          fetch('/api/dart-contracts/stats').then(r=>r.json()),
        ]);
        setContracts(Array.isArray(listRes) ? listRes : []);
        setStats(statsRes);
      } catch(e) { console.error(e); }
      setLoading(false);
    };

    const loadDetail = async (rcept_no) => {
      if (selected === rcept_no) { setSelected(null); setDetail(null); return; }
      setSelected(rcept_no);
      try {
        const d = await fetch(`/api/dart-contracts/${rcept_no}`).then(r=>r.json());
        setDetail(d);
      } catch(e) { console.error(e); }
    };

    const doRefresh = async () => {
      await fetch('/api/dart-contracts/refresh', { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}' });
      setTimeout(load, 3000);
    };

    React.useEffect(() => { load(); }, []);

    const thS = { background:'var(--bg-dark)', color:'var(--text-secondary)', fontSize:'0.72rem', padding:'0.5rem 0.6rem', textAlign:'left', whiteSpace:'nowrap' };
    const tdS = { padding:'0.55rem 0.6rem', fontSize:'0.82rem', borderBottom:'1px solid rgba(255,255,255,0.05)' };

    return (
      <div style={{display:'flex',flexDirection:'column',gap:'1rem'}}>

        {/* 헤더 + 통계 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
          <div style={{display:'flex',alignItems:'center',gap:'1rem',flexWrap:'wrap',marginBottom:'0.8rem'}}>
            <h2 style={{margin:0,fontSize:'1.1rem',fontWeight:700}}>📋 DART 수주·공급계약 공시 알림</h2>
            <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>
              매출액 대비 주요 계약 공시를 AI가 분석하여 매수 시그널을 평가합니다
            </span>
            <div style={{marginLeft:'auto',display:'flex',gap:'0.5rem'}}>
              <button onClick={doRefresh} style={{padding:'0.35rem 0.8rem',background:'rgba(59,130,246,0.2)',border:'1px solid rgba(59,130,246,0.4)',borderRadius:'6px',color:'#60a5fa',fontSize:'0.78rem',cursor:'pointer'}}>
                ⟳ 즉시 수집
              </button>
              <button onClick={load} style={{padding:'0.35rem 0.8rem',background:'rgba(34,197,94,0.15)',border:'1px solid rgba(34,197,94,0.3)',borderRadius:'6px',color:'#4ade80',fontSize:'0.78rem',cursor:'pointer'}}>
                새로고침
              </button>
            </div>
          </div>

          {/* 통계 카드 */}
          {stats && (
            <div style={{display:'flex',gap:'0.8rem',flexWrap:'wrap',marginBottom:'0.8rem'}}>
              <div style={{padding:'0.4rem 0.8rem',background:'rgba(255,255,255,0.04)',borderRadius:'6px',fontSize:'0.78rem'}}>
                전체 <strong>{stats.total || 0}</strong>건
              </div>
              {Object.entries(stats.by_signal || {}).map(([sig,cnt]) => (
                <div key={sig} style={{padding:'0.4rem 0.8rem',background:'rgba(255,255,255,0.04)',borderRadius:'6px',fontSize:'0.78rem',color:SIGNAL_COLOR[sig]||'inherit'}}>
                  {SIGNAL_EMOJI[sig]||''} {sig} <strong>{cnt}</strong>건
                </div>
              ))}
              {Object.entries(stats.by_strength || {}).map(([k,cnt]) => cnt > 0 && (
                <div key={k} style={{padding:'0.4rem 0.8rem',background:'rgba(255,255,255,0.04)',borderRadius:'6px',fontSize:'0.78rem',color:'#fbbf24'}}>
                  {k} <strong>{cnt}</strong>건
                </div>
              ))}
            </div>
          )}

          {/* 필터 */}
          <div style={{display:'flex',gap:'0.6rem',flexWrap:'wrap',alignItems:'center'}}>
            {[{label:'7일',v:7},{label:'30일',v:30},{label:'90일',v:90}].map(o=>(
              <button key={o.v} onClick={()=>{setFilters(p=>({...p,days:o.v}));setTimeout(load,50);}}
                style={{padding:'0.25rem 0.65rem',borderRadius:'5px',fontSize:'0.75rem',cursor:'pointer',
                  background:filters.days===o.v?'rgba(99,102,241,0.25)':'transparent',
                  border:`1px solid ${filters.days===o.v?'rgba(99,102,241,0.6)':'rgba(255,255,255,0.15)'}`,
                  color:filters.days===o.v?'#a5b4fc':'var(--text-secondary)'}}>
                {o.label}
              </button>
            ))}
            <select value={filters.signal_type}
              onChange={e=>{setFilters(p=>({...p,signal_type:e.target.value}));setTimeout(load,50);}}
              style={{background:'var(--bg-dark)',border:'1px solid rgba(255,255,255,0.15)',borderRadius:'5px',color:'var(--text-primary)',fontSize:'0.75rem',padding:'0.25rem 0.5rem'}}>
              <option value="">전체 시그널</option>
              <option value="강한매수">🚀 강한매수</option>
              <option value="매수">📈 매수</option>
              <option value="관망">👀 관망</option>
            </select>
            <select value={filters.is_overseas}
              onChange={e=>{setFilters(p=>({...p,is_overseas:Number(e.target.value)}));setTimeout(load,50);}}
              style={{background:'var(--bg-dark)',border:'1px solid rgba(255,255,255,0.15)',borderRadius:'5px',color:'var(--text-primary)',fontSize:'0.75rem',padding:'0.25rem 0.5rem'}}>
              <option value={-1}>전체(해외+국내)</option>
              <option value={1}>🌏 해외계약만</option>
              <option value={0}>🏠 국내계약만</option>
            </select>
            <select value={filters.min_signal}
              onChange={e=>{setFilters(p=>({...p,min_signal:Number(e.target.value)}));setTimeout(load,50);}}
              style={{background:'var(--bg-dark)',border:'1px solid rgba(255,255,255,0.15)',borderRadius:'5px',color:'var(--text-primary)',fontSize:'0.75rem',padding:'0.25rem 0.5rem'}}>
              <option value={1}>★1 이상</option>
              <option value={2}>★★2 이상</option>
              <option value={3}>★★★3 이상</option>
              <option value={4}>★★★★4 이상</option>
            </select>
          </div>
        </div>

        {/* 공시 목록 */}
        <div className="glass-panel" style={{overflow:'clip'}}>
          {loading ? (
            <div style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>분석 중…</div>
          ) : contracts.length === 0 ? (
            <div style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <div style={{fontSize:'2rem',marginBottom:'0.5rem'}}>📭</div>
              <div>해당 기간에 수주공시가 없습니다</div>
              <div style={{fontSize:'0.8rem',marginTop:'0.5rem',color:'#6b7280'}}>
                "즉시 수집" 버튼으로 DART 공시를 새로 가져오거나,<br/>
                백필: <code>python3 collectors/dart_contract_collector.py --backfill 30</code>
              </div>
            </div>
          ) : (
            <table className="premium-table" style={{width:'100%'}}>
              <thead><tr>
                <th style={thS}>시그널</th>
                <th style={thS}>종목</th>
                <th style={thS}>공시 제목</th>
                <th style={thS}>계약금액</th>
                <th style={thS}>매출비중</th>
                <th style={thS}>상대방</th>
                <th style={thS}>구분</th>
                <th style={thS}>AI 요약</th>
                <th style={thS}>공시일</th>
              </tr></thead>
              <tbody>
                {contracts.map(c => (
                  <React.Fragment key={c.rcept_no}>
                    <tr onClick={()=>loadDetail(c.rcept_no)}
                      style={{cursor:'pointer', background:selected===c.rcept_no?'rgba(99,102,241,0.08)':'transparent',
                        transition:'background 0.15s'}}>
                      <td style={{...tdS,textAlign:'center'}}>
                        <div style={{color:SIGNAL_COLOR[c.ai_signal]||'#94a3b8',fontWeight:700,fontSize:'0.85rem'}}>
                          {SIGNAL_EMOJI[c.ai_signal]||''} {c.ai_signal||'관망'}
                        </div>
                        <div style={{fontSize:'0.72rem',color:'#fbbf24',letterSpacing:'-1px'}}>{c.signal_stars}</div>
                      </td>
                      <td style={tdS}>
                        <div style={{fontWeight:600}}>{c.stock_name}</div>
                        <div style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>{c.stock_code||'-'}</div>
                      </td>
                      <td style={{...tdS,maxWidth:'240px'}}>
                        <div style={{fontSize:'0.78rem',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                          title={c.report_nm}>{c.report_nm}</div>
                        <div style={{fontSize:'0.7rem',color:c.contract_type?.includes('기술')? '#a78bfa':'#38bdf8'}}>
                          {c.contract_type||''}
                        </div>
                      </td>
                      <td style={{...tdS,textAlign:'right',whiteSpace:'nowrap'}}>
                        {c.contract_amount ? `${c.contract_amount.toLocaleString('ko-KR')}${c.contract_unit||''}` : '-'}
                      </td>
                      <td style={{...tdS,textAlign:'right'}}>
                        {c.contract_ratio_pct != null ? (
                          <span style={{color:c.contract_ratio_pct>=20?'#ef4444':c.contract_ratio_pct>=10?'#f59e0b':'inherit',fontWeight:c.contract_ratio_pct>=10?700:400}}>
                            {c.contract_ratio_pct.toFixed(1)}%
                          </span>
                        ) : '-'}
                      </td>
                      <td style={tdS}>
                        <div style={{fontSize:'0.78rem',maxWidth:'100px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                          {c.counterparty||'-'}
                        </div>
                        {c.is_overseas ? <span style={{fontSize:'0.68rem',color:'#34d399'}}>🌏{c.counterparty_country||'해외'}</span> : null}
                      </td>
                      <td style={{...tdS,textAlign:'center'}}>
                        {c.telegram_sent ? <span style={{fontSize:'0.72rem',color:'#38bdf8'}}>✈ 발송</span> : '—'}
                      </td>
                      <td style={{...tdS,fontSize:'0.75rem',maxWidth:'200px',color:'var(--text-secondary)'}}>
                        {c.ai_summary||'-'}
                      </td>
                      <td style={{...tdS,fontSize:'0.72rem',color:'var(--text-secondary)',whiteSpace:'nowrap'}}>
                        {c.disclosed_at||'-'}
                      </td>
                    </tr>

                    {/* 상세 펼치기 */}
                    {selected === c.rcept_no && detail && (
                      <tr><td colSpan={9} style={{padding:'0.8rem 1rem',background:'rgba(15,23,42,0.6)'}}>
                        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'1rem'}}>
                          {/* AI 분석 */}
                          <div style={{background:'rgba(99,102,241,0.06)',borderRadius:'8px',padding:'0.8rem',border:'1px solid rgba(99,102,241,0.15)'}}>
                            <div style={{fontWeight:700,marginBottom:'0.5rem',color:'#a5b4fc'}}>🧠 AI 매수 분석</div>
                            <div style={{fontSize:'0.82rem',lineHeight:1.7}}>
                              <div><strong>시그널:</strong> <span style={{color:SIGNAL_COLOR[c.ai_signal]}}>{c.ai_signal}</span> (스코어 {c.ai_score||0}/100)</div>
                              <div style={{margin:'0.4rem 0'}}><strong>요약:</strong> {c.ai_summary||'-'}</div>
                              {/* 전략 매칭 */}
                              <div style={{marginTop:'0.5rem',display:'flex',gap:'0.4rem',flexWrap:'wrap'}}>
                                {['V8','V10','V11','V4'].map(s => (
                                  <span key={s} style={{padding:'0.15rem 0.5rem',borderRadius:'4px',fontSize:'0.7rem',
                                    background:'rgba(99,102,241,0.15)',border:'1px solid rgba(99,102,241,0.3)',color:'#a5b4fc'}}>
                                    {s}
                                  </span>
                                ))}
                              </div>
                            </div>
                          </div>
                          {/* 수급/수출 데이터 */}
                          <div style={{background:'rgba(34,197,94,0.04)',borderRadius:'8px',padding:'0.8rem',border:'1px solid rgba(34,197,94,0.12)'}}>
                            <div style={{fontWeight:700,marginBottom:'0.5rem',color:'#4ade80'}}>📊 선행지표 연계</div>
                            <div style={{fontSize:'0.8rem',lineHeight:1.8}}>
                              <div>🏦 공시전 10일 기관: <strong style={{color:'#4ade80'}}>{detail.pre_inst_amt_억 != null ? `${detail.pre_inst_amt_억}억원` : '-'}</strong></div>
                              <div>🌏 공시전 10일 외인: <strong style={{color:'#38bdf8'}}>{detail.pre_frn_amt_억 != null ? `${detail.pre_frn_amt_억}억원` : '-'}</strong></div>
                              {detail.export_trend?.length > 0 && (
                                <div style={{marginTop:'0.4rem'}}>
                                  <div style={{color:'var(--text-secondary)',fontSize:'0.75rem'}}>HS 수출 추이 (최근 3개월)</div>
                                  {detail.export_trend.slice(0,3).map(e=>(
                                    <div key={e.period_ym} style={{fontSize:'0.75rem'}}>
                                      {e.period_ym}: {(e.total_exp/1e8).toFixed(1)}억원
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                        {/* 전략 연계 안내 */}
                        <div style={{marginTop:'0.6rem',padding:'0.5rem 0.8rem',background:'rgba(251,191,36,0.06)',borderRadius:'6px',border:'1px solid rgba(251,191,36,0.15)',fontSize:'0.75rem',color:'#fbbf24'}}>
                          💡 이 공시가 포착되면: V8(수출선행)에서 {c.is_overseas?'해외계약 가중치 +1★':'국내계약 포함'} |
                          V11(흑자전환)에서 {c.contract_ratio_pct>=10?'우선순위 상향':'참고 데이터'} |
                          V10(이익폭발)에서 {c.contract_type?.includes('기술')? '기술이전 고마진 가산':'매출기여 추적'}
                        </div>
                      </td></tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    );
  };

  // ── 대세종목 발굴 뷰 (V10/V11/V12) ─────────────────────────
  const MegatrendView = () => {
    const [tab,   setTab]   = React.useState('v12');  // v10 | v11 | v12
    const [data,  setData]  = React.useState({v10:[], v11:[], v12:[]});
    const [loading, setLoading] = React.useState({v10:false, v11:false, v12:false});

    const ENDPOINTS = {
      v10: '/api/signals/v10-earnings-explosion',
      v11: '/api/signals/v11-turnaround',
      v12: '/api/signals/v12-sector-megatrend',
    };
    const TAB_INFO = {
      v10: { label: 'V10 이익 폭발', emoji: '🚀', desc: '영업이익YoY>80% + 매출YoY>30% + 이익 가속화 (에이피알·삼양식품 유형)' },
      v11: { label: 'V11 흑자전환', emoji: '🔄', desc: '적자→흑자 전환 + 이익 증가세 + 매출YoY>15% (이수페타시스·엘앤에프 유형)' },
      v12: { label: 'V12 섹터 대세', emoji: '⚡', desc: 'KOSPI 초과 +15% 이상 대세 섹터 내 RS 상위 종목 (효성중공업·LS Electric 유형)' },
    };

    const loadTab = async (t) => {
      if (data[t]?.length > 0) return;  // 이미 로드됨
      setLoading(p => ({...p, [t]: true}));
      try {
        const r = await fetch(API(ENDPOINTS[t]));
        const d = await r.json();
        setData(p => ({...p, [t]: Array.isArray(d) ? d : []}));
      } catch(e) {}
      setLoading(p => ({...p, [t]: false}));
    };

    React.useEffect(() => { loadTab('v12'); }, []);
    React.useEffect(() => { loadTab(tab); }, [tab]);

    const pct = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%';
    const fmtMc = (v) => !v ? '-' : v >= 10000 ? (v/10000).toFixed(1)+'조' : v.toLocaleString()+'억';
    const pcol = (v) => !v ? '#aaa' : v > 0 ? '#ef4444' : '#3b82f6';

    const rows = data[tab] || [];
    const info = TAB_INFO[tab];

    return (
      <div style={{display:'flex', flexDirection:'column', gap:'1rem'}}>
        {/* 헤더 */}
        <div style={{background:'rgba(255,255,255,0.04)', borderRadius:'12px', padding:'16px 20px',
            border:'1px solid rgba(255,255,255,0.08)'}}>
          <h2 style={{margin:0, fontSize:'1.1rem', color:'#f1f5f9', fontWeight:700}}>
            📈 대세종목 발굴 — 3~5배 상승 주도 종목을 사전에 발굴
          </h2>
          <p style={{margin:'6px 0 0', fontSize:'0.78rem', color:'#94a3b8', lineHeight:1.5}}>
            <b style={{color:'#fbbf24'}}>V10</b> 이익 폭발: 에이피알·삼양식품 유형 |&nbsp;
            <b style={{color:'#34d399'}}>V11</b> 흑자전환: 이수페타시스·엘앤에프 유형 |&nbsp;
            <b style={{color:'#60a5fa'}}>V12</b> 섹터 대세: 효성중공업·LS Electric 유형
          </p>
        </div>

        {/* 탭 */}
        <div style={{display:'flex', gap:'8px', flexWrap:'wrap'}}>
          {Object.entries(TAB_INFO).map(([k, v]) => (
            <button key={k} onClick={() => setTab(k)}
              style={{padding:'8px 18px', borderRadius:'8px', border:'1px solid',
                background: tab===k ? 'rgba(59,130,246,0.25)' : 'rgba(255,255,255,0.04)',
                borderColor: tab===k ? '#3b82f6' : 'rgba(255,255,255,0.1)',
                color: tab===k ? '#93c5fd' : '#94a3b8', cursor:'pointer', fontSize:'13px',
                fontWeight: tab===k ? 700 : 400, transition:'all .15s'}}>
              {v.emoji} {v.label}
            </button>
          ))}
          <button onClick={() => {
            setData(p=>({...p,[tab]:[]}));
            setTimeout(()=>loadTab(tab),50);
          }} style={{marginLeft:'auto', padding:'8px 14px', borderRadius:'8px',
            background:'rgba(255,255,255,0.04)', border:'1px solid rgba(255,255,255,0.1)',
            color:'#94a3b8', cursor:'pointer', fontSize:'12px'}}>🔄 새로고침</button>
        </div>

        {/* 전략 설명 */}
        <div style={{background:'rgba(251,191,36,0.06)', border:'1px solid rgba(251,191,36,0.2)',
            borderRadius:'8px', padding:'10px 14px', fontSize:'12px', color:'#fbbf24'}}>
          <b>{info.emoji} {info.label}:</b> {info.desc}
        </div>

        {/* 대세 섹터 뱃지 (V12만) */}
        {tab === 'v12' && rows.length > 0 && (() => {
          const hotSectors = [...new Set(rows.map(r => r.sector))].slice(0, 8);
          const kospi3m = rows[0]?.kospi_3m_ret;
          return (
            <div style={{display:'flex', flexWrap:'wrap', gap:'8px', alignItems:'center'}}>
              <span style={{fontSize:'12px', color:'#94a3b8'}}>대세 섹터:</span>
              {hotSectors.map(s => (
                <span key={s} style={{background:'rgba(251,191,36,0.12)', border:'1px solid rgba(251,191,36,0.3)',
                    borderRadius:'20px', padding:'3px 10px', fontSize:'11px', color:'#fbbf24'}}>
                  {s}
                </span>
              ))}
              {kospi3m != null && (
                <span style={{marginLeft:'auto', fontSize:'11px', color:'#94a3b8'}}>
                  KOSPI 3개월: <b style={{color: pcol(kospi3m)}}>{pct(kospi3m)}</b>
                </span>
              )}
            </div>
          );
        })()}

        {/* 테이블 */}
        {loading[tab] ? (
          <div style={{textAlign:'center', padding:'40px', color:'#94a3b8'}}>⏳ 분석 중... (30초 소요)</div>
        ) : rows.length === 0 ? (
          <div style={{textAlign:'center', padding:'40px', color:'#94a3b8'}}>데이터 없음</div>
        ) : (
          <div style={{background:'rgba(255,255,255,0.03)', borderRadius:'10px',
              border:'1px solid rgba(255,255,255,0.07)', overflow:'auto'}}>
            <table style={{width:'100%', borderCollapse:'collapse', fontSize:'12px'}}>
              <thead>
                <tr style={{background:'rgba(255,255,255,0.06)', borderBottom:'1px solid rgba(255,255,255,0.1)'}}>
                  <th style={{padding:'10px 12px', textAlign:'left', color:'#94a3b8', fontWeight:600}}>순위</th>
                  <th style={{padding:'10px 12px', textAlign:'left', color:'#94a3b8'}}>종목명</th>
                  <th style={{padding:'10px 12px', textAlign:'left', color:'#94a3b8'}}>섹터</th>
                  <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>시총</th>
                  {tab === 'v10' && <>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#fbbf24'}}>영업이익YoY</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>매출YoY</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>이익률</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>Q0영업이익</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>수급5일</th>
                  </>}
                  {tab === 'v11' && <>
                    <th style={{padding:'10px 8px', textAlign:'center', color:'#34d399'}}>Q3→Q2→Q1→Q0 이익</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>매출YoY</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>전환강도</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>수급20일</th>
                  </>}
                  {tab === 'v12' && <>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#60a5fa'}}>3개월수익</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>섹터평균</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#fbbf24'}}>RS초과</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>52주대비</th>
                    <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>수급20일</th>
                  </>}
                  <th style={{padding:'10px 8px', textAlign:'right', color:'#94a3b8'}}>점수</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s, i) => (
                  <tr key={s.stock_code} onClick={() => { setStockCode(s.stock_code); setActiveTab('analysis'); }}
                    style={{borderBottom:'1px solid rgba(255,255,255,0.04)',
                      background: i%2===0?'transparent':'rgba(255,255,255,0.015)',
                      cursor:'pointer', transition:'background .1s'}}
                    onMouseEnter={e=>e.currentTarget.style.background='rgba(59,130,246,0.07)'}
                    onMouseLeave={e=>e.currentTarget.style.background=i%2===0?'transparent':'rgba(255,255,255,0.015)'}>
                    <td style={{padding:'9px 12px', color:'#64748b'}}>{i+1}</td>
                    <td style={{padding:'9px 12px'}}>
                      <span style={{fontWeight:600, color:'#f1f5f9'}}>{s.stock_name}</span>
                      <br/><span style={{fontSize:'10px', color:'#64748b'}}>{s.stock_code}</span>
                    </td>
                    <td style={{padding:'9px 12px', color:'#94a3b8', fontSize:'11px'}}>{s.sector}</td>
                    <td style={{padding:'9px 8px', textAlign:'right', color:'#cbd5e1'}}>{fmtMc(s.market_cap_억)}</td>
                    {tab === 'v10' && <>
                      <td style={{padding:'9px 8px', textAlign:'right', fontWeight:700,
                          color: s.op_yoy > 200 ? '#ef4444' : '#f97316'}}>{pct(s.op_yoy)}</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color: pcol(s.rev_yoy)}}>{pct(s.rev_yoy)}</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color:'#a78bfa'}}>{s.op_margin}%</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color:'#e2e8f0'}}>{(s.op_q0_억||0).toLocaleString()}억</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color: pcol(s.supply_5d_억)}}>{(s.supply_5d_억||0)>0?'+':''}{(s.supply_5d_억||0).toLocaleString()}억</td>
                    </>}
                    {tab === 'v11' && <>
                      <td style={{padding:'9px 8px', textAlign:'center'}}>
                        {[s.op_q3_억, s.op_q2_억, s.op_q1_억, s.op_q0_억].map((v,j) => (
                          <span key={j} style={{
                            color: v < 0 ? '#ef4444' : v < 20 ? '#f59e0b' : '#10b981',
                            fontWeight: j===3?700:400, marginRight:'4px', fontSize:'11px'}}>
                            {v != null ? (v > 0 ? '+' : '') + v + '억' : '-'}
                            {j < 3 ? '→' : ''}
                          </span>
                        ))}
                      </td>
                      <td style={{padding:'9px 8px', textAlign:'right', color: pcol(s.rev_yoy)}}>{pct(s.rev_yoy)}</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color:'#fbbf24'}}>{(s.reversal_power||0).toLocaleString()}억</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color: pcol(s.supply_20d_억)}}>{(s.supply_20d_억||0)>0?'+':''}{(s.supply_20d_억||0).toLocaleString()}억</td>
                    </>}
                    {tab === 'v12' && <>
                      <td style={{padding:'9px 8px', textAlign:'right', fontWeight:700, color: pcol(s.ret_3m_pct)}}>{pct(s.ret_3m_pct)}</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color:'#94a3b8'}}>{pct(s.sector_avg_ret)}</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color:'#fbbf24', fontWeight:700}}>+{pct(s.rs_excess)}</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color: s.high52_pct > -5 ? '#10b981' : '#94a3b8'}}>{pct(s.high52_pct)}</td>
                      <td style={{padding:'9px 8px', textAlign:'right', color: pcol(s.supply_20d_억)}}>{(s.supply_20d_억||0)>0?'+':''}{(s.supply_20d_억||0).toLocaleString()}억</td>
                    </>}
                    <td style={{padding:'9px 8px', textAlign:'right'}}>
                      <span style={{background:`hsl(${Math.min(s.score/1.2,120)},70%,40%)`,
                          borderRadius:'4px', padding:'2px 6px', color:'#fff', fontWeight:700, fontSize:'11px'}}>
                        {Math.round(s.score)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  };


  // ── 텐버거 헌터 뷰 ────────────────────────────────────────
  const TenbaggerView = () => {
    const [latest,   setLatest]   = React.useState(null);   // {run_time, results, count}
    const [history,  setHistory]  = React.useState([]);      // 회차 목록
    const [selRun,   setSelRun]   = React.useState(null);    // 선택된 회차 run_time
    const [selData,  setSelData]  = React.useState(null);    // 선택된 회차 상세
    const [status,   setStatus]   = React.useState(null);
    const [running,  setRunning]  = React.useState(false);
    const [expanded, setExpanded] = React.useState({});      // {id: bool} AI 분석 펼침
    
    // 탭 관련 상태 ('main' | 'filter' | 'undervalued' | 'turnaround' | 'ai_leaders')
    const [viewMode, setViewMode] = React.useState('main');
    // 특수 필터
    const [filterLoading, setFilterLoading] = React.useState(false);
    const [filterError, setFilterError] = React.useState('');
    const [filterData, setFilterData] = React.useState(null);
    const [filterSettings, setFilterSettings] = React.useState({
      opm_threshold: 3.0,
      depr_threshold: 40.0,
      emp_months: 3,
      min_score: 2
    });
    // 저평가 종목
    const [undervaluedData, setUndervaluedData] = React.useState(null);
    const [undervaluedLoading, setUndervaluedLoading] = React.useState(false);
    const [undervaluedError, setUndervaluedError] = React.useState('');
    // 텐어라운드
    const [turnaroundData, setTurnaroundData] = React.useState(null);
    const [turnaroundLoading, setTurnaroundLoading] = React.useState(false);
    const [turnaroundError, setTurnaroundError] = React.useState('');
    // AI 섹터 선도
    const [aiLeadersData, setAiLeadersData] = React.useState(null);
    const [aiLeadersLoading, setAiLeadersLoading] = React.useState(false);
    const [aiLeadersError, setAiLeadersError] = React.useState('');

    const load = async () => {
      try {
        const [r1, r2, r3] = await Promise.all([
          fetch(API('/api/tenbagger/results')),
          fetch(API('/api/tenbagger/history')),
          fetch(API('/api/tenbagger/status')),
        ]);
        if (r1.ok) setLatest(await r1.json());
        if (r2.ok) setHistory(await r2.json());
        if (r3.ok) setStatus(await r3.json());
      } catch(e) {}
    };

    const loadFilterData = React.useCallback(async () => {
      setFilterLoading(true); setFilterError('');
      try {
        const params = new URLSearchParams({
          opm_threshold: (filterSettings.opm_threshold / 100).toString(),
          depr_threshold: (filterSettings.depr_threshold / 100).toString(),
          emp_months: filterSettings.emp_months.toString(),
          min_score: filterSettings.min_score.toString()
        });
        const r = await fetch(API(`/api/sector-define/special-filter?${params}`));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        setFilterData(d.stocks);
      } catch (e) { setFilterError('필터 데이터 로드 실패: ' + e.message); }
      finally { setFilterLoading(false); }
    }, [API, filterSettings]);

    const loadUndervalued = React.useCallback(async () => {
      setUndervaluedLoading(true); setUndervaluedError('');
      try {
        const r = await fetch(API('/api/tenbagger/undervalued-filter?min_score=2&limit=100'));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setUndervaluedData(await r.json());
      } catch (e) { setUndervaluedError('로드 실패: ' + e.message); }
      finally { setUndervaluedLoading(false); }
    }, [API]);

    const loadTurnaround = React.useCallback(async () => {
      setTurnaroundLoading(true); setTurnaroundError('');
      try {
        const r = await fetch(API('/api/tenbagger/turnaround-filter?min_score=3&limit=100'));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setTurnaroundData(await r.json());
      } catch (e) { setTurnaroundError('로드 실패: ' + e.message); }
      finally { setTurnaroundLoading(false); }
    }, [API]);

    const loadAiLeaders = React.useCallback(async () => {
      setAiLeadersLoading(true); setAiLeadersError('');
      try {
        const r = await fetch(API('/api/tenbagger/sector-ai-leaders'));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setAiLeadersData(await r.json());
      } catch (e) { setAiLeadersError('로드 실패: ' + e.message); }
      finally { setAiLeadersLoading(false); }
    }, [API]);

    React.useEffect(() => { load(); }, []);
    React.useEffect(() => { if (viewMode === 'filter') loadFilterData(); }, [viewMode, loadFilterData]);
    React.useEffect(() => { if (viewMode === 'undervalued') loadUndervalued(); }, [viewMode, loadUndervalued]);
    React.useEffect(() => { if (viewMode === 'turnaround') loadTurnaround(); }, [viewMode, loadTurnaround]);
    React.useEffect(() => { if (viewMode === 'ai_leaders') loadAiLeaders(); }, [viewMode, loadAiLeaders]);

    // 상태 폴링 (수동 실행 중일 때)
    React.useEffect(() => {
      if (!running) return;
      const iv = setInterval(async () => {
        try {
          const r = await fetch(API('/api/tenbagger/status'));
          if (r.ok) {
            const s = await r.json();
            setStatus(s);
            if (!s.running) { setRunning(false); load(); clearInterval(iv); }
          }
        } catch(e) {}
      }, 3000);
      return () => clearInterval(iv);
    }, [running]);

    const handleRun = async () => {
      setRunning(true);
      try {
        await fetch(API('/api/tenbagger/run'), { method: 'POST' });
      } catch(e) { setRunning(false); }
    };

    const loadRunDetail = async (runTime) => {
      setSelRun(runTime);
      try {
        const r = await fetch(API(`/api/tenbagger/run-history?run_time=${encodeURIComponent(runTime)}`));
        if (r.ok) setSelData(await r.json());
      } catch(e) {}
    };

    const displayData = selData || latest;
    const displayRows = displayData?.results || [];

    const fmtScore = (s) => {
      if (s >= 80) return { color: '#f59e0b', label: '★★★' };
      if (s >= 65) return { color: '#2dd4bf', label: '★★☆' };
      return { color: '#94a3b8', label: '★☆☆' };
    };

    const ScoreBar = ({ label, val, max, color }) => (
      <div style={{ display:'flex', alignItems:'center', gap:'0.4rem', fontSize:'0.72rem' }}>
        <span style={{ width:'70px', color:'var(--text-secondary)', flexShrink:0 }}>{label}</span>
        <div style={{ flex:1, height:'5px', background:'rgba(255,255,255,0.08)', borderRadius:'3px', overflow:'hidden' }}>
          <div style={{ width:`${Math.max(0,val/max*100)}%`, height:'100%', background: color || 'var(--accent-mint)', borderRadius:'3px', transition:'width 0.4s' }}/>
        </div>
        <span style={{ width:'28px', textAlign:'right', color: color || 'var(--accent-mint)', fontWeight:700 }}>{val?.toFixed(0) ?? '-'}</span>
      </div>
    );

    return (
      <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>

        {/* 헤더 */}
        <div className="glass-panel" style={{ padding:'1.2rem', display:'flex', alignItems:'center', justifyContent:'space-between', flexWrap:'wrap', gap:'0.8rem' }}>
          <div className="section-title" style={{ marginBottom:0 }}>
            <span style={{ fontSize:'1.3rem' }}>💎</span>
            <h2 style={{ fontSize:'1.1rem' }}>텐버거 헌터</h2>
            <div style={{ display:'flex', background:'rgba(255,255,255,0.05)', borderRadius:'8px', padding:'2px', marginLeft:'1rem' }}>
              <button onClick={() => setViewMode('main')} style={{
                padding:'0.3rem 0.8rem', borderRadius:'6px', fontSize:'0.75rem', fontWeight:600, border:'none', cursor:'pointer',
                background: viewMode === 'main' ? 'var(--accent-mint)' : 'transparent',
                color: viewMode === 'main' ? 'black' : 'var(--text-secondary)',
                transition:'all 0.2s'
              }}>💎 발굴 결과</button>
              <button onClick={() => setViewMode('filter')} style={{
                padding:'0.3rem 0.8rem', borderRadius:'6px', fontSize:'0.75rem', fontWeight:600, border:'none', cursor:'pointer',
                background: viewMode === 'filter' ? 'var(--accent-mint)' : 'transparent',
                color: viewMode === 'filter' ? 'black' : 'var(--text-secondary)',
                transition:'all 0.2s'
              }}>✨ 특수 필터</button>
              <button onClick={() => setViewMode('undervalued')} style={{
                padding:'0.3rem 0.8rem', borderRadius:'6px', fontSize:'0.75rem', fontWeight:600, border:'none', cursor:'pointer',
                background: viewMode === 'undervalued' ? '#34d399' : 'transparent',
                color: viewMode === 'undervalued' ? 'black' : 'var(--text-secondary)',
                transition:'all 0.2s'
              }}>📉 저평가 종목</button>
              <button onClick={() => setViewMode('turnaround')} style={{
                padding:'0.3rem 0.8rem', borderRadius:'6px', fontSize:'0.75rem', fontWeight:600, border:'none', cursor:'pointer',
                background: viewMode === 'turnaround' ? '#f87171' : 'transparent',
                color: viewMode === 'turnaround' ? 'black' : 'var(--text-secondary)',
                transition:'all 0.2s'
              }}>🔄 텐어라운드</button>
              <button onClick={() => setViewMode('ai_leaders')} style={{
                padding:'0.3rem 0.8rem', borderRadius:'6px', fontSize:'0.75rem', fontWeight:600, border:'none', cursor:'pointer',
                background: viewMode === 'ai_leaders' ? '#a78bfa' : 'transparent',
                color: viewMode === 'ai_leaders' ? 'black' : 'var(--text-secondary)',
                transition:'all 0.2s'
              }}>🤖 AI 섹터 선도</button>
            </div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:'0.8rem' }}>
            {viewMode === 'main' && (
              <>
                {status && (
                  <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>
                    마지막 실행: {status.last_run ? status.last_run.slice(0,16) : '없음'}
                    {status.last_count > 0 && ` (${status.last_count}종목)`}
                  </span>
                )}
                <button onClick={handleRun} disabled={running}
                  style={{ padding:'0.4rem 1rem', borderRadius:'8px', background: running ? 'rgba(245,158,11,0.3)' : 'rgba(245,158,11,0.15)',
                    border:'1px solid #f59e0b', color:'#f59e0b', cursor: running ? 'not-allowed' : 'pointer',
                    fontWeight:700, fontSize:'0.83rem', display:'flex', alignItems:'center', gap:'0.4rem' }}>
                  {running ? <><span style={{ animation:'spin 1s linear infinite', display:'inline-block' }}>⟳</span> 발굴 중...</> : '▶ 즉시 발굴'}
                </button>
              </>
            )}
            <button onClick={() => {
              if (viewMode === 'main') load();
              else if (viewMode === 'filter') loadFilterData();
              else if (viewMode === 'undervalued') loadUndervalued();
              else if (viewMode === 'turnaround') loadTurnaround();
              else loadAiLeaders();
            }} style={{ padding:'0.4rem 0.7rem', borderRadius:'8px', background:'rgba(255,255,255,0.05)',
                border:'1px solid var(--glass-border)', color:'var(--text-secondary)', cursor:'pointer', fontSize:'0.83rem' }}>
              새로고침
            </button>
          </div>
        </div>

        {viewMode === 'main' && (
          <div style={{ display:'flex', gap:'1rem', flexWrap:'wrap' }}>

            {/* 이력 사이드바 */}
            <div style={{ width:'220px', flexShrink:0, display:'flex', flexDirection:'column', gap:'0.5rem' }}>
              <div className="glass-panel" style={{ padding:'0.8rem' }}>
                <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', fontWeight:700, marginBottom:'0.5rem' }}>발굴 이력</p>
                {history.length === 0 ? (
                  <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', textAlign:'center', padding:'1rem 0' }}>아직 발굴 이력 없음</p>
                ) : (
                  history.map(h => (
                    <button key={h.run_time} onClick={() => loadRunDetail(h.run_time)}
                      style={{ width:'100%', textAlign:'left', padding:'0.45rem 0.6rem', marginBottom:'0.2rem',
                        borderRadius:'6px', border:'none', cursor:'pointer',
                        background: selRun === h.run_time ? 'rgba(245,158,11,0.15)' : 'transparent',
                        borderLeft: selRun === h.run_time ? '2px solid #f59e0b' : '2px solid transparent',
                      }}>
                      <div style={{ fontSize:'0.72rem', color:'var(--text-primary)', fontWeight: selRun===h.run_time ? 700 : 400 }}>
                        {h.run_time.slice(0,16)}
                      </div>
                      <div style={{ fontSize:'0.68rem', color:'var(--text-secondary)', marginTop:'0.1rem' }}>
                        {{'morning':'🌅 오전','noon':'☀️ 정오','afternoon':'🌇 오후','manual':'🔍 수동'}[h.run_type] || h.run_type}
                        &nbsp;· {h.count}종목 · 최고 {h.max_score?.toFixed(0)}점
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>

            {/* 메인 결과 */}
            <div style={{ flex:1, minWidth:0, display:'flex', flexDirection:'column', gap:'0.8rem' }}>

              {/* 헤더 정보 */}
              {displayData?.run_time && (
                <div style={{ display:'flex', alignItems:'center', gap:'0.6rem', flexWrap:'wrap' }}>
                  <span style={{ fontSize:'0.8rem', color:'var(--text-secondary)' }}>
                    기준 시각: <b style={{ color:'var(--text-primary)' }}>{displayData.run_time?.slice(0,16)}</b>
                  </span>
                  <span style={{ fontSize:'0.8rem', color:'var(--accent-mint)' }}>
                    총 {displayData.count}종목 선정
                  </span>
                  {selRun && (
                    <button onClick={() => { setSelRun(null); setSelData(null); }}
                      style={{ fontSize:'0.72rem', padding:'0.2rem 0.5rem', borderRadius:'4px',
                        background:'rgba(255,255,255,0.05)', border:'1px solid var(--glass-border)',
                        color:'var(--text-secondary)', cursor:'pointer' }}>
                      최신으로 돌아가기
                    </button>
                  )}
                </div>
              )}

              {displayRows.length === 0 ? (
                <div className="glass-panel" style={{ padding:'3rem', textAlign:'center', color:'var(--text-secondary)' }}>
                  <div style={{ fontSize:'2.5rem', marginBottom:'0.8rem' }}>💎</div>
                  <p style={{ fontWeight:600 }}>아직 발굴된 종목이 없습니다.</p>
                  <p style={{ fontSize:'0.8rem', marginTop:'0.4rem' }}>
                    매일 평일 09:00 / 12:00 / 15:00에 자동 발굴됩니다.<br/>
                    또는 위 [즉시 발굴] 버튼을 눌러 수동으로 실행하세요.
                  </p>
                </div>
              ) : (
                displayRows.map((s, idx) => {
                  const star = fmtScore(s.total_score);
                  const isOpen = expanded[s.id];
                  return (
                    <div key={s.id || idx} className="glass-panel"
                      style={{ padding:'1rem', borderLeft:`3px solid ${star.color}` }}>

                      {/* 종목명 헤더 */}
                      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', flexWrap:'wrap', gap:'0.5rem', marginBottom:'0.7rem' }}>
                        <div style={{ display:'flex', alignItems:'center', gap:'0.6rem' }}>
                          <span style={{ fontSize:'0.9rem', fontWeight:700, color: star.color }}>{star.label}</span>
                          <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                            style={{ background:'none', border:'none', cursor:'pointer', padding:0,
                              fontSize:'1rem', fontWeight:700, color:'var(--text-primary)', textDecoration:'underline dotted' }}>
                            {s.stock_name}
                          </button>
                          <span style={{ fontSize:'0.78rem', color:'var(--text-secondary)' }}>({s.stock_code})</span>
                        </div>
                        <div style={{ display:'flex', alignItems:'center', gap:'1rem', flexWrap:'wrap' }}>
                          <span style={{ fontSize:'0.85rem', fontWeight:700, color: star.color }}>
                            {s.total_score?.toFixed(1)}점
                          </span>
                          {s.current_price && (
                            <span style={{ fontSize:'0.82rem', color:'var(--text-primary)' }}>
                              {s.current_price.toLocaleString('ko-KR')}원
                            </span>
                          )}
                          {s.market_cap && (
                            <span style={{ fontSize:'0.78rem', color:'var(--text-secondary)' }}>
                              시총 {s.market_cap.toLocaleString('ko-KR', { maximumFractionDigits:0 })}억
                            </span>
                          )}
                        </div>
                      </div>

                      {/* 주요 지표 */}
                      <div style={{ display:'flex', gap:'1.5rem', flexWrap:'wrap', marginBottom:'0.7rem' }}>
                        {[
                          { label:'PER',   val: s.per,   fmt: v => `${v.toFixed(1)}배` },
                          { label:'PBR',   val: s.pbr,   fmt: v => `${v.toFixed(1)}배` },
                          { label:'ROE',   val: s.roe,   fmt: v => `${v.toFixed(1)}%` },
                          { label:'매출성장', val: s.revenue_growth, fmt: v => `${v > 0 ? '+' : ''}${v.toFixed(1)}%` },
                          { label:'영익성장', val: s.op_growth, fmt: v => `${v > 0 ? '+' : ''}${v.toFixed(1)}%` },
                          { label:'영익률', val: s.op_margin, fmt: v => `${v.toFixed(1)}%` },
                        ].filter(x => x.val != null).map(x => (
                          <div key={x.label} style={{ textAlign:'center' }}>
                            <div style={{ fontSize:'0.68rem', color:'var(--text-secondary)' }}>{x.label}</div>
                            <div style={{ fontSize:'0.85rem', fontWeight:700, color:'var(--text-primary)' }}>{x.fmt(x.val)}</div>
                          </div>
                        ))}
                      </div>

                      {/* 점수 바 */}
                      {s.score_detail && Object.keys(s.score_detail).length > 0 && (
                        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.3rem 1.2rem', marginBottom:'0.7rem' }}>
                          <ScoreBar label="매출성장" val={s.score_detail.growth_rev} max={20} color="#2dd4bf" />
                          <ScoreBar label="영익성장" val={s.score_detail.growth_op}  max={20} color="#2dd4bf" />
                          <ScoreBar label="수익성"   val={s.score_detail.profit}     max={15} color="#a78bfa" />
                          <ScoreBar label="추세"     val={s.score_detail.trend}      max={15} color="#60a5fa" />
                          <ScoreBar label="수급"     val={s.score_detail.supply}     max={15} color="#f59e0b" />
                          <ScoreBar label="밸류"     val={s.score_detail.value}      max={15} color="#34d399" />
                        </div>
                      )}

                      {/* 선정 사유 */}
                      {s.reasons?.length > 0 && (
                        <div style={{ marginBottom:'0.6rem' }}>
                          <p style={{ fontSize:'0.72rem', color:'var(--text-secondary)', fontWeight:700, marginBottom:'0.3rem' }}>📌 선정 사유</p>
                          <div style={{ display:'flex', flexWrap:'wrap', gap:'0.3rem' }}>
                            {s.reasons.map((r, i) => (
                              <span key={i} style={{ fontSize:'0.72rem', padding:'0.15rem 0.5rem', borderRadius:'12px',
                                background:'rgba(255,255,255,0.06)', border:'1px solid var(--glass-border)',
                                color:'var(--text-primary)' }}>
                                {r}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* AI 분석 */}
                      {s.ai_analysis && (
                        <div>
                          <button onClick={() => setExpanded(p => ({ ...p, [s.id]: !isOpen }))}
                            style={{ fontSize:'0.75rem', background:'none', border:'none', cursor:'pointer',
                              color:'var(--accent-purple)', padding:0, fontWeight:600 }}>
                            🤖 AI 분석 {isOpen ? '▲ 접기' : '▼ 보기'}
                          </button>
                          {isOpen && (
                            <div style={{ marginTop:'0.5rem', padding:'0.8rem', borderRadius:'8px',
                              background:'rgba(167,139,250,0.07)', border:'1px solid rgba(167,139,250,0.2)',
                              fontSize:'0.82rem', lineHeight:1.7, color:'var(--text-primary)', whiteSpace:'pre-wrap' }}>
                              {s.ai_analysis}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {viewMode === 'filter' && (
          <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'0.8rem' }}>
            <div className="glass-panel" style={{ padding:'1rem', display:'flex', gap:'1.2rem', flexWrap:'wrap', alignItems:'flex-end' }}>
              <div style={{ display:'flex', flexDirection:'column', gap:'0.3rem' }}>
                <label style={{ fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600 }}>영업이익률 임계치 (≤ %)</label>
                <input type="number" step="0.1" value={filterSettings.opm_threshold} 
                  onChange={e => setFilterSettings({...filterSettings, opm_threshold: parseFloat(e.target.value)})}
                  style={{ width:'70px', padding:'0.3rem', borderRadius:'4px', border:'1px solid var(--glass-border)', background:'rgba(0,0,0,0.2)', color:'white', fontSize:'0.8rem' }} />
              </div>
              <div style={{ display:'flex', flexDirection:'column', gap:'0.3rem' }}>
                <label style={{ fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600 }}>감가상각비율 (≥ %)</label>
                <input type="number" step="1" value={filterSettings.depr_threshold} 
                  onChange={e => setFilterSettings({...filterSettings, depr_threshold: parseFloat(e.target.value)})}
                  style={{ width:'70px', padding:'0.3rem', borderRadius:'4px', border:'1px solid var(--glass-border)', background:'rgba(0,0,0,0.2)', color:'white', fontSize:'0.8rem' }} />
              </div>
              <div style={{ display:'flex', flexDirection:'column', gap:'0.3rem' }}>
                <label style={{ fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600 }}>고용 확인 기간 (개월)</label>
                <select value={filterSettings.emp_months} 
                  onChange={e => setFilterSettings({...filterSettings, emp_months: parseInt(e.target.value)})}
                  style={{ width:'80px', padding:'0.3rem', borderRadius:'4px', border:'1px solid var(--glass-border)', background:'rgba(0,0,0,0.2)', color:'white', fontSize:'0.8rem' }}>
                  <option value={3}>3개월</option>
                  <option value={6}>6개월</option>
                  <option value={12}>12개월</option>
                </select>
              </div>
              <div style={{ display:'flex', flexDirection:'column', gap:'0.3rem' }}>
                <label style={{ fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600 }}>최소 점수</label>
                <select value={filterSettings.min_score} 
                  onChange={e => setFilterSettings({...filterSettings, min_score: parseInt(e.target.value)})}
                  style={{ width:'80px', padding:'0.3rem', borderRadius:'4px', border:'1px solid var(--glass-border)', background:'rgba(0,0,0,0.2)', color:'white', fontSize:'0.8rem' }}>
                  <option value={1}>1점+</option>
                  <option value={2}>2점+</option>
                  <option value={3}>3점+</option>
                  <option value={4}>4점 전체</option>
                </select>
              </div>
              <button onClick={loadFilterData} disabled={filterLoading} style={{ padding:'0.4rem 1rem', borderRadius:'4px', background:'var(--accent-mint)', color:'black', border:'none', fontWeight:700, cursor:'pointer', fontSize:'0.8rem' }}>
                {filterLoading ? '로딩...' : '필터 적용'}
              </button>
            </div>

            {filterError && <div style={{ color:'#f87171', fontSize:'0.8rem', padding:'0.5rem' }}>{filterError}</div>}

            <div className="glass-panel" style={{ overflow:'auto', padding:'0' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.8rem' }}>
                <thead>
                  <tr style={{ background:'rgba(0,0,0,0.2)', borderBottom:'1px solid var(--glass-border)' }}>
                    <th style={{ padding:'0.6rem', textAlign:'center' }}>점수</th>
                    <th style={{ padding:'0.6rem', textAlign:'left' }}>종목명</th>
                    <th style={{ padding:'0.6rem', textAlign:'right' }}>현재가</th>
                    <th style={{ padding:'0.6rem', textAlign:'center' }}>재무(OPM)</th>
                    <th style={{ padding:'0.6rem', textAlign:'center' }}>감가상각</th>
                    <th style={{ padding:'0.6rem', textAlign:'center' }}>고용</th>
                    <th style={{ padding:'0.6rem', textAlign:'center' }}>수출</th>
                  </tr>
                </thead>
                <tbody>
                  {filterData?.map((s, i) => (
                    <tr key={i} style={{ borderBottom:'1px solid rgba(255,255,255,0.03)' }}>
                      <td style={{ padding:'0.5rem', textAlign:'center' }}>
                        <span style={{ display:'inline-block', width:'22px', height:'22px', lineHeight:'22px', borderRadius:'50%', background: s.score >= 3 ? '#10b981' : '#3b82f6', color:'white', fontWeight:700, fontSize:'0.7rem' }}>{s.score}</span>
                      </td>
                      <td style={{ padding:'0.5rem', fontWeight:600 }}>
                        <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }} style={{ background:'none', border:'none', color:'var(--text-primary)', cursor:'pointer', padding:0, fontSize:'0.8rem', fontWeight:600 }}>{s.stock_name}</button>
                        <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)', marginLeft:'0.3rem' }}>({s.stock_code})</span>
                      </td>
                      <td style={{ padding:'0.5rem', textAlign:'right' }}>
                        <div style={{ display:'flex', flexDirection:'column', alignItems:'flex-end' }}>
                          <span>{s.price?.toLocaleString()}원</span>
                          <span style={{ fontSize:'0.7rem', color: s.chg_pct > 0 ? '#ef4444' : s.chg_pct < 0 ? '#3b82f6' : 'inherit' }}>{s.chg_pct > 0 ? '+' : ''}{s.chg_pct?.toFixed(1)}%</span>
                        </div>
                      </td>
                      <td style={{ padding:'0.5rem', textAlign:'center' }}>{s.cond2 ? <span style={{ color:'#ef4444' }}>✅ {s.details.opm != null ? s.details.opm + '%' : '적자'}</span> : '-'}</td>
                      <td style={{ padding:'0.5rem', textAlign:'center' }}>{s.cond1 ? <span style={{ color:'#f59e0b' }}>✅ {s.details.depr_ratio}%</span> : '-'}</td>
                      <td style={{ padding:'0.5rem', textAlign:'center' }}>{s.cond3 ? <span style={{ color:'#10b981' }}>✅ +{s.details.emp_inc}</span> : '-'}</td>
                      <td style={{ padding:'0.5rem', textAlign:'center' }}>{s.cond4 ? <span style={{ color:'#a855f7' }}>✅ +{s.details.export_inc_pct}%</span> : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(!filterData || filterData.length === 0) && !filterLoading && (
                <div style={{ padding:'2rem', textAlign:'center', color:'var(--text-secondary)' }}>결과가 없습니다.</div>
              )}
            </div>
          </div>
        )}

        {/* 저평가 종목 탭 */}
        {viewMode === 'undervalued' && (
          <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'0.8rem' }}>
            {undervaluedLoading && <div style={{ padding:'2rem', textAlign:'center', color:'var(--text-secondary)' }}>로딩 중...</div>}
            {undervaluedError && <div style={{ color:'#f87171', padding:'0.5rem', fontSize:'0.8rem' }}>{undervaluedError}</div>}
            {undervaluedData && (
              <>
                <div style={{ fontSize:'0.78rem', color:'var(--text-secondary)', padding:'0 0.2rem' }}>
                  <span style={{ color:'#34d399', fontWeight:700 }}>PBR≤1 · PER≤12 · 매출 연속 성장</span> 조건 기반 저평가 종목 — {undervaluedData.count}종목
                </div>
                <div className="glass-panel" style={{ overflow:'auto', padding:0 }}>
                  <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.8rem' }}>
                    <thead>
                      <tr style={{ background:'rgba(0,0,0,0.2)', borderBottom:'1px solid var(--glass-border)' }}>
                        <th style={{ padding:'0.6rem', textAlign:'center', whiteSpace:'nowrap' }}>점수</th>
                        <th style={{ padding:'0.6rem', textAlign:'left' }}>종목명</th>
                        <th style={{ padding:'0.6rem', textAlign:'center' }}>섹터</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>현재가</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>시총(억)</th>
                        <th style={{ padding:'0.6rem', textAlign:'right' }}>PER</th>
                        <th style={{ padding:'0.6rem', textAlign:'right' }}>PBR</th>
                        <th style={{ padding:'0.6rem', textAlign:'right' }}>ROE</th>
                        <th style={{ padding:'0.6rem', textAlign:'center', whiteSpace:'nowrap' }}>매출QoQ</th>
                        <th style={{ padding:'0.6rem', textAlign:'center', whiteSpace:'nowrap' }}>매출YoY</th>
                        <th style={{ padding:'0.6rem', textAlign:'left' }}>충족 조건</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(undervaluedData.stocks || []).map((s, i) => (
                        <tr key={i} style={{ borderBottom:'1px solid rgba(255,255,255,0.03)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                          <td style={{ padding:'0.5rem', textAlign:'center' }}>
                            <span style={{ display:'inline-block', width:'22px', height:'22px', lineHeight:'22px', borderRadius:'50%', background: s.score >= 3 ? '#34d399' : '#60a5fa', color:'black', fontWeight:700, fontSize:'0.72rem' }}>{s.score}</span>
                          </td>
                          <td style={{ padding:'0.5rem' }}>
                            <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }} style={{ background:'none', border:'none', color:'var(--text-primary)', cursor:'pointer', padding:0, fontSize:'0.8rem', fontWeight:600 }}>{s.stock_name}</button>
                            <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)', marginLeft:'0.3rem' }}>{s.stock_code}</span>
                          </td>
                          <td style={{ padding:'0.5rem', textAlign:'center', fontSize:'0.72rem', color:'var(--text-secondary)', whiteSpace:'nowrap' }}>{s.sector || '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', whiteSpace:'nowrap' }}>{s.current_price?.toLocaleString('ko-KR')}원</td>
                          <td style={{ padding:'0.5rem', textAlign:'right' }}>{s.market_cap_억?.toLocaleString('ko-KR')}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.per != null && s.per <= 12 ? '#34d399' : 'inherit' }}>{s.per != null ? s.per.toFixed(1) : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.pbr != null && s.pbr <= 1 ? '#34d399' : 'inherit' }}>{s.pbr != null ? s.pbr.toFixed(2) : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.roe != null && s.roe >= 10 ? '#34d399' : 'inherit' }}>{s.roe != null ? s.roe.toFixed(1) + '%' : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'center', color: (s.qoq_streak || 0) >= 2 ? '#34d399' : 'var(--text-secondary)' }}>{s.qoq_streak}분기↑</td>
                          <td style={{ padding:'0.5rem', textAlign:'center', color: (s.yoy_streak || 0) >= 2 ? '#34d399' : 'var(--text-secondary)' }}>{s.yoy_streak}분기↑</td>
                          <td style={{ padding:'0.5rem' }}>
                            <div style={{ display:'flex', flexWrap:'wrap', gap:'0.2rem' }}>
                              {(s.matched_indicators || []).map((m, j) => (
                                <span key={j} style={{ fontSize:'0.68rem', padding:'0.1rem 0.35rem', borderRadius:'10px', background:'rgba(52,211,153,0.15)', color:'#34d399', border:'1px solid rgba(52,211,153,0.3)' }}>{m}</span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {(!undervaluedData.stocks || undervaluedData.stocks.length === 0) && (
                    <div style={{ padding:'2rem', textAlign:'center', color:'var(--text-secondary)' }}>조건에 맞는 종목이 없습니다.</div>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {/* 텐어라운드 탭 */}
        {viewMode === 'turnaround' && (
          <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'0.8rem' }}>
            {turnaroundLoading && <div style={{ padding:'2rem', textAlign:'center', color:'var(--text-secondary)' }}>로딩 중...</div>}
            {turnaroundError && <div style={{ color:'#f87171', padding:'0.5rem', fontSize:'0.8rem' }}>{turnaroundError}</div>}
            {turnaroundData && (
              <>
                <div style={{ fontSize:'0.78rem', color:'var(--text-secondary)', padding:'0 0.2rem' }}>
                  <span style={{ color:'#f87171', fontWeight:700 }}>적자 + 흑자전환 가능성</span> 종목 (감가상각 레버리지 · 매출성장 · 수출·고용·수주 복합) — {turnaroundData.count}종목
                </div>
                <div className="glass-panel" style={{ overflow:'auto', padding:0 }}>
                  <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.8rem' }}>
                    <thead>
                      <tr style={{ background:'rgba(0,0,0,0.2)', borderBottom:'1px solid var(--glass-border)' }}>
                        <th style={{ padding:'0.6rem', textAlign:'center' }}>점수</th>
                        <th style={{ padding:'0.6rem', textAlign:'left' }}>종목명</th>
                        <th style={{ padding:'0.6rem', textAlign:'center' }}>섹터</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>현재가</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>영업손실(억)</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>손실축소</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>감가상각률</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>수출증가</th>
                        <th style={{ padding:'0.6rem', textAlign:'center', whiteSpace:'nowrap' }}>고용</th>
                        <th style={{ padding:'0.6rem', textAlign:'left' }}>전환 근거</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(turnaroundData.stocks || []).map((s, i) => (
                        <tr key={i} style={{ borderBottom:'1px solid rgba(255,255,255,0.03)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                          <td style={{ padding:'0.5rem', textAlign:'center' }}>
                            <span style={{ display:'inline-block', width:'22px', height:'22px', lineHeight:'22px', borderRadius:'50%', background: s.score >= 5 ? '#f87171' : s.score >= 4 ? '#fb923c' : '#60a5fa', color:'black', fontWeight:700, fontSize:'0.72rem' }}>{s.score}</span>
                          </td>
                          <td style={{ padding:'0.5rem' }}>
                            <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }} style={{ background:'none', border:'none', color:'var(--text-primary)', cursor:'pointer', padding:0, fontSize:'0.8rem', fontWeight:600 }}>{s.stock_name}</button>
                            <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)', marginLeft:'0.3rem' }}>{s.stock_code}</span>
                          </td>
                          <td style={{ padding:'0.5rem', textAlign:'center', fontSize:'0.72rem', color:'var(--text-secondary)', whiteSpace:'nowrap' }}>{s.sector || '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', whiteSpace:'nowrap' }}>{s.current_price?.toLocaleString('ko-KR')}원</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color:'#f87171', whiteSpace:'nowrap' }}>{s.op_loss_억 != null ? s.op_loss_억.toFixed(0) + '억' : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.loss_improve_pct != null && s.loss_improve_pct > 0 ? '#34d399' : 'inherit', whiteSpace:'nowrap' }}>
                            {s.loss_improve_pct != null ? (s.loss_improve_pct > 0 ? '+' : '') + s.loss_improve_pct.toFixed(1) + '%' : '-'}
                          </td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.depr_ratio != null && s.depr_ratio >= 20 ? '#f59e0b' : 'inherit' }}>{s.depr_ratio != null ? s.depr_ratio.toFixed(1) + '%' : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.export_growth_pct != null && s.export_growth_pct > 0 ? '#34d399' : 'var(--text-secondary)' }}>
                            {s.export_growth_pct != null ? (s.export_growth_pct > 0 ? '+' : '') + s.export_growth_pct.toFixed(1) + '%' : '-'}
                          </td>
                          <td style={{ padding:'0.5rem', textAlign:'center', color: (s.emp_change_count || 0) > 0 ? '#34d399' : 'var(--text-secondary)' }}>
                            {(s.emp_change_count || 0) > 0 ? `+${s.emp_change_count}명` : s.emp_change_count < 0 ? `${s.emp_change_count}명` : '-'}
                          </td>
                          <td style={{ padding:'0.5rem' }}>
                            <div style={{ display:'flex', flexWrap:'wrap', gap:'0.2rem' }}>
                              {(s.reasons || []).map((r, j) => (
                                <span key={j} style={{ fontSize:'0.68rem', padding:'0.1rem 0.35rem', borderRadius:'10px', background:'rgba(248,113,113,0.12)', color:'#f87171', border:'1px solid rgba(248,113,113,0.3)' }}>{r}</span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {(!turnaroundData.stocks || turnaroundData.stocks.length === 0) && (
                    <div style={{ padding:'2rem', textAlign:'center', color:'var(--text-secondary)' }}>조건에 맞는 종목이 없습니다.</div>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {/* AI 섹터 선도 탭 */}
        {viewMode === 'ai_leaders' && (
          <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'0.8rem' }}>
            {aiLeadersLoading && <div style={{ padding:'2rem', textAlign:'center', color:'var(--text-secondary)' }}>AI 분석 중...</div>}
            {aiLeadersError && <div style={{ color:'#f87171', padding:'0.5rem', fontSize:'0.8rem' }}>{aiLeadersError}</div>}
            {aiLeadersData && (
              <>
                {/* AI 시황 요약 */}
                {(aiLeadersData.ai_summary || aiLeadersData.market_view) && (
                  <div className="glass-panel" style={{ padding:'1rem', borderLeft:'3px solid #a78bfa' }}>
                    <p style={{ fontSize:'0.72rem', color:'#a78bfa', fontWeight:700, marginBottom:'0.4rem' }}>🤖 AI 시황 ({aiLeadersData.as_of})</p>
                    {aiLeadersData.ai_summary && <p style={{ fontSize:'0.85rem', color:'var(--text-primary)', marginBottom:'0.3rem' }}>{aiLeadersData.ai_summary}</p>}
                    {aiLeadersData.market_view && <p style={{ fontSize:'0.8rem', color:'var(--text-secondary)' }}>{aiLeadersData.market_view}</p>}
                  </div>
                )}
                {/* 주도 섹터 카드 */}
                {(aiLeadersData.sectors || []).length > 0 && (
                  <div style={{ display:'flex', gap:'0.8rem', flexWrap:'wrap' }}>
                    {(aiLeadersData.sectors || []).map((sec, i) => (
                      <div key={i} className="glass-panel" style={{ flex:'1', minWidth:'200px', padding:'0.8rem', borderTop:`3px solid ${['#a78bfa','#60a5fa','#34d399'][i] || '#a78bfa'}` }}>
                        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.4rem' }}>
                          <span style={{ fontWeight:700, fontSize:'0.9rem' }}>{sec.kr || sec.ticker}</span>
                          <span style={{ fontSize:'0.72rem', color:'var(--text-secondary)' }}>{sec.ticker}</span>
                        </div>
                        {sec.chg_5d != null && (
                          <div style={{ fontSize:'0.75rem', color: sec.chg_5d > 0 ? '#34d399' : '#f87171', marginBottom:'0.3rem' }}>
                            5일 수익률 {sec.chg_5d > 0 ? '+' : ''}{sec.chg_5d?.toFixed(2)}%
                          </div>
                        )}
                        {sec.ai_reason && <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', lineHeight:1.5, marginBottom:'0.3rem' }}>{sec.ai_reason}</p>}
                        {sec.ai_risk && <p style={{ fontSize:'0.72rem', color:'#fbbf24', lineHeight:1.5 }}>⚠️ {sec.ai_risk}</p>}
                      </div>
                    ))}
                  </div>
                )}
                {/* 국내 선도 종목 */}
                <div className="glass-panel" style={{ overflow:'auto', padding:0 }}>
                  <p style={{ padding:'0.7rem 1rem', fontSize:'0.75rem', color:'var(--text-secondary)', fontWeight:700, borderBottom:'1px solid var(--glass-border)', margin:0 }}>
                    🇰🇷 국내 관련 선도주 ({aiLeadersData.count}종목)
                  </p>
                  <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.8rem' }}>
                    <thead>
                      <tr style={{ background:'rgba(0,0,0,0.2)', borderBottom:'1px solid var(--glass-border)' }}>
                        <th style={{ padding:'0.6rem', textAlign:'left' }}>종목명</th>
                        <th style={{ padding:'0.6rem', textAlign:'center' }}>관련 섹터</th>
                        <th style={{ padding:'0.6rem', textAlign:'right', whiteSpace:'nowrap' }}>현재가</th>
                        <th style={{ padding:'0.6rem', textAlign:'right' }}>PER</th>
                        <th style={{ padding:'0.6rem', textAlign:'right' }}>PBR</th>
                        <th style={{ padding:'0.6rem', textAlign:'right' }}>ROE</th>
                        <th style={{ padding:'0.6rem', textAlign:'left' }}>선정 근거</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(aiLeadersData.stocks || []).map((s, i) => (
                        <tr key={i} style={{ borderBottom:'1px solid rgba(255,255,255,0.03)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                          <td style={{ padding:'0.5rem' }}>
                            <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }} style={{ background:'none', border:'none', color:'var(--text-primary)', cursor:'pointer', padding:0, fontSize:'0.8rem', fontWeight:600 }}>{s.stock_name}</button>
                            <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)', marginLeft:'0.3rem' }}>{s.stock_code}</span>
                          </td>
                          <td style={{ padding:'0.5rem', textAlign:'center', fontSize:'0.75rem' }}>
                            <span style={{ padding:'0.15rem 0.5rem', borderRadius:'10px', background:'rgba(167,139,250,0.15)', color:'#a78bfa', border:'1px solid rgba(167,139,250,0.3)' }}>{s.source_sector || '-'}</span>
                          </td>
                          <td style={{ padding:'0.5rem', textAlign:'right', whiteSpace:'nowrap' }}>{s.close != null ? Number(s.close).toLocaleString('ko-KR') + '원' : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.per != null && s.per <= 20 ? '#34d399' : 'inherit' }}>{s.per != null ? Number(s.per).toFixed(1) : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.pbr != null && s.pbr <= 2 ? '#34d399' : 'inherit' }}>{s.pbr != null ? Number(s.pbr).toFixed(2) : '-'}</td>
                          <td style={{ padding:'0.5rem', textAlign:'right', color: s.roe != null && s.roe >= 10 ? '#34d399' : 'inherit' }}>{s.roe != null ? Number(s.roe).toFixed(1) + '%' : '-'}</td>
                          <td style={{ padding:'0.5rem', fontSize:'0.72rem', color:'var(--text-secondary)' }}>{s.source_sector_reason || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {(!aiLeadersData.stocks || aiLeadersData.stocks.length === 0) && (
                    <div style={{ padding:'2rem', textAlign:'center', color:'var(--text-secondary)' }}>데이터가 없습니다. 새로고침을 눌러보세요.</div>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {/* 발굴 기준 안내 */}
        <div className="glass-panel" style={{ padding:'1rem' }}>
          <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', fontWeight:700, marginBottom:'0.5rem' }}>💡 발굴 기준 (6축 스코어링, 100점 만점 · 55점 이상 선정)</p>
          <div style={{ display:'flex', flexWrap:'wrap', gap:'0.5rem 2rem' }}>
            {[
              ['매출 성장 (20점)', '연간 매출 YoY 15%+ 기준'],
              ['영업이익 성장 (20점)', '연간 영업이익 YoY 20%+ 기준'],
              ['수익성 (15점)', 'ROE 10%+, 영업이익률 8%+'],
              ['기술적 추세 (15점)', 'MA 정배열, 52주 고점 근접'],
              ['수급 모멘텀 (15점)', '최근 10일 기관/외국인 순매수'],
              ['밸류에이션 (15점)', 'PER ≤ 30배, PBR ≤ 5배'],
            ].map(([t, d]) => (
              <div key={t} style={{ fontSize:'0.72rem' }}>
                <span style={{ color:'var(--accent-mint)', fontWeight:700 }}>{t}</span>
                <span style={{ color:'var(--text-secondary)', marginLeft:'0.3rem' }}>— {d}</span>
              </div>
            ))}
          </div>
          <p style={{ fontSize:'0.7rem', color:'var(--text-secondary)', marginTop:'0.5rem' }}>
            ※ 시가총액 500억~3조 · 5일 거래대금 3억+ 기본 필터 적용 · 투자 판단 및 손익은 투자자 본인 책임
          </p>
        </div>
      </div>
    );
  };

  // ── 백테스트 뷰 ───────────────────────────────────────────
  const BacktestView = () => {
    const [list,       setList]       = React.useState([]);
    const [detail,     setDetail]     = React.useState(null);
    const [running,    setRunning]    = React.useState(false);
    const [pollId,     setPollId]     = React.useState(null);
    const [viewMode,   setViewMode]   = React.useState('matrix'); // 'matrix' | 'list'
    const [matrixData, setMatrixData] = React.useState(null);
    const [form, setForm] = React.useState({
      start_date: '2018-01-01',
      end_date:   '2025-12-31',
      per_stock:  10000000,
      name:       '',
      strategy:   'v4',
    });

    const loadList = async () => {
      try {
        const r = await fetch(API('/api/backtest/list'));
        if (r.ok) setList(await r.json());
      } catch(e) {}
    };

    const loadMatrix = async () => {
      try {
        const r = await fetch(API('/api/backtest/matrix'));
        if (r.ok) setMatrixData(await r.json());
      } catch(e) {}
    };

    React.useEffect(() => {
      loadList();
      loadMatrix();
    }, []);

    const strategyEndpoints = {
      v1_value: '/api/backtest/run-v1-value',
      v2:       '/api/backtest/run-v2',
      v4:       '/api/backtest/run',
      v5:       '/api/backtest/run-v5',
      v8:       '/api/backtest/run-v8',
      v10:      '/api/backtest/run-v10',
      v10_hs:   '/api/backtest/run-v10-hs',
      v11:      '/api/backtest/run-v11',
      v11_hs:   '/api/backtest/run-v11-hs',
      v12:      '/api/backtest/run-v12',
      v_trend:  '/api/backtest/run-v1',
      v_dart:   '/api/backtest/run-v1-dart',
    };

    const startBacktest = async () => {
      setRunning(true);
      try {
        const endpoint = strategyEndpoints[form.strategy];
        if (!endpoint) { alert(`지원하지 않는 전략: ${form.strategy}`); setRunning(false); return; }
        const stratLabels = { v1_value:'V1 가치매수', v2:'V2 재무스크리너', v4:'V4 복합콤보', v5:'V5 수급모멘텀', v8:'V8 수출선행', v10:'V10 이익폭발', v10_hs:'V10+HS수출', v11:'V11 흑자전환', v11_hs:'V11+HS수출', v12:'V12 섹터대세', v_trend:'VT MA정배열', v_dart:'VT+DART필터' };
        const r = await fetch(API(endpoint), {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            start_date: form.start_date,
            end_date:   form.end_date,
            per_stock:  Number(form.per_stock),
            name:       form.name || `${stratLabels[form.strategy]||'백테스트'} ${form.start_date.slice(0,7)}~${form.end_date.slice(0,7)}`,
          }),
        });
        if (!r.ok) { setRunning(false); return; }
        const { run_id } = await r.json();
        // 완료될 때까지 폴링
        const iv = setInterval(async () => {
          await loadList();
          const r2 = await fetch(API(`/api/backtest/${run_id}`));
          if (r2.ok) {
            const d = await r2.json();
            if (d.status === 'done' || d.status === 'error') {
              clearInterval(iv);
              setRunning(false);
              setDetail(d);
              loadList();
            }
          }
        }, 3000);
        setPollId(iv);
      } catch(e) { setRunning(false); }
    };

    const loadDetail = async (run_id) => {
      const r = await fetch(API(`/api/backtest/${run_id}`));
      if (r.ok) setDetail(await r.json());
    };

    const deleteRun = async (run_id) => {
      await fetch(API(`/api/backtest/${run_id}`), { method: 'DELETE' });
      setList(prev => prev.filter(x => x.run_id !== run_id));
      if (detail && detail.run_id === run_id) setDetail(null);
    };

    const fmtAmt = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('ko-KR') + '원';
    const clr = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.4)';
    const inputS = {
      padding:'0.35rem 0.7rem', borderRadius:'6px', fontSize:'0.82rem',
      background:'rgba(255,255,255,0.06)', border:'1px solid var(--glass-border)',
      color:'#fff',
    };

    // 매트릭스 셀 색상
    const cellBg = (cagr) => {
      if (cagr == null) return 'rgba(255,255,255,0.02)';
      if (cagr <= 0) return 'rgba(59,130,246,0.1)';
      if (cagr >= 10) return 'rgba(239,68,68,0.2)';
      if (cagr >= 5) return 'rgba(239,68,68,0.12)';
      return 'rgba(239,68,68,0.06)';
    };
    const cellClr = (cagr) => {
      if (cagr == null) return 'rgba(255,255,255,0.2)';
      return cagr > 0 ? '#f87171' : '#60a5fa';
    };

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
        {/* 헤더 + 뷰 토글 */}
        <div className="glass-panel" style={{padding:'0.8rem 1.2rem'}}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem',marginBottom:'0.5rem'}}>
            <Activity size={18} color="#f59e0b" />
            <h2 style={{fontSize:'1rem',fontWeight:700}}>📊 백테스트 & 전략 비교</h2>
            <div style={{marginLeft:'auto',display:'flex',gap:'0.4rem'}}>
              {['matrix','list'].map(m => (
                <button key={m} onClick={() => setViewMode(m)} style={{
                  padding:'0.25rem 0.7rem', borderRadius:'6px', fontSize:'0.75rem',
                  cursor:'pointer', fontWeight: viewMode===m ? 700 : 400,
                  background: viewMode===m ? 'rgba(245,158,11,0.2)' : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${viewMode===m ? 'rgba(245,158,11,0.5)' : 'var(--glass-border)'}`,
                  color: viewMode===m ? '#f59e0b' : 'var(--text-secondary)',
                }}>
                  {m === 'matrix' ? '📊 전략 비교' : '📋 목록'}
                </button>
              ))}
            </div>
          </div>
          <div style={{padding:'0.5rem 0.8rem',background:'rgba(251,191,36,0.07)',
            border:'1px solid rgba(251,191,36,0.2)',borderRadius:'6px',
            fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.6}}>
            ⚠️ 과거 데이터 기준 시뮬레이션 — <strong>미래 수익 보장 불가</strong>.
            V1~V8: 2018-2025 기준 실제 백테스트 결과 (종목당 1천만원 가상 투자)
          </div>
        </div>

        {/* ══ 전략 비교 매트릭스 뷰 ══ */}
        {viewMode === 'matrix' && matrixData && (() => {
          const periods = matrixData.period_order || [];
          const stratOrder = matrixData.strategy_order || [];
          // 핵심 전략만 표시 (strategy_order에 포함된 것만, 순서대로)
          const strategies = stratOrder
            .map(sk => (matrixData.strategies || []).find(s => s.strategy === sk))
            .filter(Boolean);

          // 전략 설명
          // 설명은 백엔드 desc 사용 (stratDesc는 fallback용)
          const stratDescFallback = {
            v1: 'Graham 내재가치 할인 + 수급 보조',
            v2: '재무 성장/수익성 스코어링 ★ 장기 최고 CAGR',
            v4: 'AI 적극검토 콤보 (추세+가치+수급+KOSPI필터)',
            v11: '흑자전환 (적자→흑자 2분기 연속) ★2022-23 최고',
            v_trend: 'MA정배열(20>60>120) + RSI42-72 + 거래량×1.3배',
          };

          return (
            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.7rem 1rem',borderBottom:'1px solid var(--glass-border)',
                display:'flex',alignItems:'center',gap:'1rem',flexWrap:'wrap'}}>
                <span style={{fontWeight:700,fontSize:'0.88rem'}}>전략 × 기간 성과 매트릭스</span>
                <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
                  셀: CAGR(연복리) | 괄호: MDD | 빨강=수익 파랑=손실
                </span>
                <button onClick={loadMatrix} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',
                  borderRadius:'5px',fontSize:'0.7rem',cursor:'pointer',
                  border:'1px solid var(--glass-border)',background:'transparent',
                  color:'var(--text-secondary)'}}>↻ 새로고침</button>
              </div>
              <div style={{overflowX:'auto',overflowY:'clip'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.78rem'}}>
                  <thead>
                    <tr>
                      <th style={{padding:'0.5rem 0.8rem',background:'rgba(30,58,138,0.4)',
                        borderBottom:'2px solid rgba(59,130,246,0.4)',textAlign:'left',
                        position:'sticky',top:0,zIndex:5,whiteSpace:'nowrap',minWidth:'120px'}}>전략</th>
                      <th style={{padding:'0.5rem 0.8rem',background:'rgba(30,58,138,0.4)',
                        borderBottom:'2px solid rgba(59,130,246,0.4)',
                        position:'sticky',top:0,zIndex:5,fontSize:'0.7rem',
                        color:'rgba(255,255,255,0.5)',textAlign:'left',minWidth:'180px'}}>설명</th>
                      {periods.map(p => (
                        <th key={p} style={{padding:'0.5rem 0.6rem',background:'rgba(30,58,138,0.4)',
                          borderBottom:'2px solid rgba(59,130,246,0.4)',textAlign:'center',
                          position:'sticky',top:0,zIndex:5,whiteSpace:'nowrap',minWidth:'90px'}}>
                          {p}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {strategies.map((s, si) => {
                      // 전략별 최고 CAGR 기간 찾기
                      const cagrVals = periods.map(p => s.periods[p]?.cagr ?? s.periods[p]?.ann_return_pct ?? null);
                      const maxCagr = Math.max(...cagrVals.filter(v => v != null));
                      return (
                        <tr key={s.strategy}
                          style={{background: si%2===0 ? 'transparent' : 'rgba(255,255,255,0.015)'}}
                          onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                          onMouseOut={e => e.currentTarget.style.background= si%2===0 ? 'transparent' : 'rgba(255,255,255,0.015)'}>
                          <td style={{padding:'0.5rem 0.8rem',fontWeight:700,
                            borderBottom:'1px solid rgba(255,255,255,0.04)',
                            color: ['v2','v_trend'].includes(s.strategy) ? '#fbbf24' : '#e2e8f0',
                            whiteSpace:'nowrap'}}>
                            {s.label}
                          </td>
                          <td style={{padding:'0.4rem 0.8rem',fontSize:'0.7rem',
                            color:'rgba(255,255,255,0.45)',
                            borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                            {s.desc || stratDescFallback[s.strategy] || ''}
                          </td>
                          {periods.map(p => {
                            const pd = s.periods[p];
                            const cagr = pd?.cagr ?? pd?.ann_return_pct ?? null;
                            const mdd = pd?.mdd;
                            const tc = pd?.trade_count;
                            const isBest = cagr != null && cagr === maxCagr && cagr > 0;
                            const isZero = tc === 0 || (!pd);
                            return (
                              <td key={p} style={{
                                padding:'0.4rem 0.6rem',
                                textAlign:'center',
                                background: isZero ? 'rgba(255,255,255,0.01)' : cellBg(cagr),
                                borderBottom:'1px solid rgba(255,255,255,0.04)',
                                borderLeft:'1px solid rgba(255,255,255,0.04)',
                              }}>
                                {isZero ? (
                                  <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.68rem'}}>
                                    {!pd ? '-' : '0건'}
                                  </span>
                                ) : (
                                  <div>
                                    <div style={{
                                      fontWeight: isBest ? 700 : 600,
                                      color: cellClr(cagr),
                                      fontSize: isBest ? '0.85rem' : '0.8rem',
                                    }}>
                                      {isBest && '★ '}
                                      {cagr != null ? (cagr>0?'+':'')+cagr.toFixed(1)+'%' : '-'}
                                    </div>
                                    {mdd != null && (
                                      <div style={{fontSize:'0.65rem',color:'rgba(248,113,113,0.7)',marginTop:'0.1rem'}}>
                                        MDD {mdd.toFixed(1)}%
                                      </div>
                                    )}
                                    {tc != null && (
                                      <div style={{fontSize:'0.62rem',color:'rgba(255,255,255,0.25)'}}>
                                        {tc}건
                                      </div>
                                    )}
                                  </div>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {/* 하단 해설 */}
              <div style={{padding:'0.7rem 1rem',borderTop:'1px solid var(--glass-border)',
                display:'flex',flexWrap:'wrap',gap:'1rem',fontSize:'0.72rem',
                color:'rgba(255,255,255,0.5)',lineHeight:1.6}}>
                <span>⭐ V2 재무성장: 단일 팩터 장기 최고 CAGR</span>
                <span>⭐ VT MA정배열: AI/반도체 랠리(23.11~24.12) CAGR +82.5% 최강</span>
                <span>⭐ V11 흑자전환: 하락장(21.12~22.10) CAGR +75.7% — 시장필터 없음</span>
                <span>🚢 V8 수출선행: HS무역통계 실제 수출데이터 연동 — 94개 수출기업 한정</span>
                <span>⚠️ V3/V5: 단독 비권장 | V12: 섹터알파 후행성 | VT+DART: 수익 저하로 비권장</span>
              </div>
            </div>
          );
        })()}

        {/* 매트릭스 뷰일 때 신규 실행 패널은 접기 */}
        {viewMode === 'list' && (
          <>
        {/* 설정 패널 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
          <div style={{fontWeight:700,marginBottom:'0.7rem',fontSize:'0.85rem',color:'var(--accent-mint)'}}>
            🔧 백테스트 신규 실행
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(180px,1fr))',gap:'0.6rem',marginBottom:'0.8rem'}}>
            {[
              { label:'시작일', key:'start_date', type:'date' },
              { label:'종료일', key:'end_date',   type:'date' },
              { label:'종목당 투자금(원)', key:'per_stock', type:'number' },
              { label:'실행명(선택)', key:'name', type:'text', placeholder:'예: 2023년 전략 테스트' },
            ].map(({ label, key, type, placeholder }) => (
              <div key={key}>
                <div style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginBottom:'0.25rem'}}>{label}</div>
                <input type={type} value={form[key]}
                  placeholder={placeholder || ''}
                  onChange={e => setForm(p => ({...p, [key]: e.target.value}))}
                  style={{...inputS, width:'100%', boxSizing:'border-box'}} />
              </div>
            ))}
            <div>
              <div style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginBottom:'0.25rem'}}>전략 선택</div>
              <select value={form.strategy}
                onChange={e => setForm(p => ({...p, strategy: e.target.value}))}
                style={{...inputS, width:'100%', boxSizing:'border-box', cursor:'pointer'}}>
                <optgroup label="── 가치/재무 발굴 ──">
                  <option value="v1_value">V1 가치매수 (Graham 저평가)</option>
                  <option value="v2">V2 재무스크리너 (수익성 스코어)</option>
                </optgroup>
                <optgroup label="── 추세/콤보 ──">
                  <option value="v4">V4 복합콤보 ★추천 (AI 삼중필터)</option>
                  <option value="v_trend">VT MA정배열 ★추천</option>
                  <option value="v_dart">VT+DART필터</option>
                </optgroup>
                <optgroup label="── 수급 모멘텀 ──">
                  <option value="v5">V5 수급모멘텀 (기관+외인 동반)</option>
                </optgroup>
                <optgroup label="── 실적/전환 ──">
                  <option value="v10">V10 이익폭발 (고성장 2분기)</option>
                  <option value="v10_hs">V10+HS수출</option>
                  <option value="v11">V11 흑자전환 ★하락장강세</option>
                  <option value="v11_hs">V11+HS수출</option>
                </optgroup>
                <optgroup label="── 섹터/수출 ──">
                  <option value="v8">V8 수출선행 (HS무역통계)</option>
                  <option value="v12">V12 섹터대세</option>
                </optgroup>
              </select>
            </div>
          </div>
          <button onClick={startBacktest} disabled={running} style={{
            padding:'0.5rem 1.4rem', borderRadius:'8px', fontWeight:700, cursor: running ? 'not-allowed' : 'pointer',
            background: running ? 'rgba(100,116,139,0.2)' : 'rgba(245,158,11,0.2)',
            border: `1px solid ${running ? 'rgba(100,116,139,0.3)' : 'rgba(245,158,11,0.5)'}`,
            color: running ? 'var(--text-secondary)' : '#f59e0b', fontSize:'0.88rem',
          }}>
            {running ? '⏳ 백테스트 실행 중...' : '▶ 백테스트 실행'}
          </button>
          {running && (
            <div style={{marginTop:'0.5rem',fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>
              전 종목 스캔 중입니다. 수십 초 ~ 수 분 소요될 수 있습니다.
            </div>
          )}
        </div>
          </>
        )}

        {/* 결과 목록 — list 모드만 */}
        {viewMode === 'list' && list.length > 0 && (
          <div className="glass-panel" style={{overflow:'clip'}}>
            <div style={{padding:'0.6rem 1rem',borderBottom:'1px solid var(--glass-border)',
              display:'flex',alignItems:'center',gap:'0.5rem'}}>
              <span style={{fontWeight:700,fontSize:'0.85rem'}}>📋 저장된 백테스트 결과</span>
              <button onClick={loadList} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',borderRadius:'5px',
                fontSize:'0.7rem',cursor:'pointer',border:'1px solid var(--glass-border)',
                background:'transparent',color:'var(--text-secondary)'}}>새로고침</button>
            </div>
            <table className="premium-table">
              <thead><tr>
                <th>실행명</th>
                <th>기간</th>
                <th style={{textAlign:'right'}}>총수익률</th>
                <th style={{textAlign:'right'}}>CAGR</th>
                <th style={{textAlign:'right'}}>승률</th>
                <th style={{textAlign:'right'}}>손익비</th>
                <th style={{textAlign:'right'}}>샤프</th>
                <th style={{textAlign:'right'}}>거래수</th>
                <th style={{textAlign:'right'}}>최대낙폭</th>
                <th>상태</th>
                <th></th>
              </tr></thead>
              <tbody>
                {list.map(r => {
                  const rd = (() => { try { return r.trades_json ? JSON.parse(r.trades_json) : null; } catch { return null; } })();
                  return (
                  <tr key={r.run_id} style={{cursor:'pointer'}} onClick={() => loadDetail(r.run_id)}>
                    <td style={{fontWeight:600}}>{r.name}</td>
                    <td style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>{r.start_date} ~ {r.end_date}</td>
                    <td style={{textAlign:'right',fontWeight:700,color:clr(r.total_return_pct||0)}}>
                      {r.total_return_pct != null ? (r.total_return_pct>=0?'+':'')+r.total_return_pct+'%' : '-'}
                    </td>
                    <td style={{textAlign:'right',color:clr(rd?.cagr??r.ann_return_pct??0)}}>
                      {rd?.cagr != null ? (rd.cagr>=0?'+':'')+rd.cagr+'%' : r.ann_return_pct != null ? (r.ann_return_pct>=0?'+':'')+r.ann_return_pct+'%' : '-'}
                    </td>
                    <td style={{textAlign:'right'}}>{r.win_rate != null ? r.win_rate+'%' : '-'}</td>
                    <td style={{textAlign:'right',color:'var(--accent-purple)'}}>
                      {rd?.pl_ratio != null ? rd.pl_ratio+'배' : '-'}
                    </td>
                    <td style={{textAlign:'right',color:rd?.sharpe>=1?'#22c55e':rd?.sharpe>=0?'#f59e0b':'#f87171'}}>
                      {rd?.sharpe != null ? rd.sharpe : '-'}
                    </td>
                    <td style={{textAlign:'right'}}>{r.total_trades ?? '-'}건</td>
                    <td style={{textAlign:'right',color:'#f87171'}}>
                      {r.max_drawdown_pct != null ? r.max_drawdown_pct+'%' : '-'}
                    </td>
                    <td>
                      <span style={{padding:'0.1rem 0.4rem',borderRadius:'4px',fontSize:'0.68rem',
                        background: r.status==='done' ? 'rgba(34,197,94,0.15)' :
                                    r.status==='running' ? 'rgba(251,191,36,0.15)' : 'rgba(239,68,68,0.15)',
                        color:      r.status==='done' ? '#22c55e' :
                                    r.status==='running' ? '#fbbf24' : '#ef4444'}}>
                        {r.status==='done' ? '완료' : r.status==='running' ? '실행중' : '오류'}
                      </span>
                    </td>
                    <td onClick={e => { e.stopPropagation(); deleteRun(r.run_id); }}
                      style={{cursor:'pointer',color:'#ef4444',fontSize:'0.8rem',padding:'0.3rem 0.6rem'}}>
                      ✕
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* 상세 결과 — list 모드만 */}
        {viewMode === 'list' && detail && detail.status === 'done' && (
          <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
            {/* 요약 카드 */}
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:'0.6rem'}}>
              {[
                { label:'총수익률',     val:(detail.total_return_pct>=0?'+':'')+detail.total_return_pct+'%',  color:clr(detail.total_return_pct) },
                { label:'CAGR(연복리)', val:detail.cagr!=null ? (detail.cagr>=0?'+':'')+detail.cagr+'%' : (detail.ann_return_pct>=0?'+':'')+detail.ann_return_pct+'%', color:clr(detail.cagr??detail.ann_return_pct) },
                { label:'승률',         val:detail.win_rate+'%',          color:'var(--accent-mint)' },
                { label:'손익비',       val:detail.pl_ratio!=null ? detail.pl_ratio+'배' : '-', color:'var(--accent-purple)' },
                { label:'샤프지수',     val:detail.sharpe!=null ? detail.sharpe : '-', color:detail.sharpe>=1?'#22c55e':detail.sharpe>=0?'#f59e0b':'#f87171' },
                { label:'최대낙폭(MDD)',val:detail.max_drawdown_pct+'%',  color:'#f87171' },
                { label:'총 거래수',    val:detail.total_trades+'건',     color:'var(--text-primary)' },
                { label:'총손익',       val:fmtAmt(detail.total_profit_amt), color:clr(detail.total_profit_amt||0) },
              ].map(({ label, val, color }) => (
                <div key={label} className="glass-panel" style={{padding:'0.7rem 0.9rem'}}>
                  <div style={{fontSize:'0.67rem',color:'var(--text-secondary)',marginBottom:'0.2rem'}}>{label}</div>
                  <div style={{fontSize:'0.95rem',fontWeight:700,color}}>{val}</div>
                </div>
              ))}
            </div>

            {/* 월별 손익 */}
            {detail.monthly && detail.monthly.length > 0 && (
              <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
                <div style={{fontWeight:700,fontSize:'0.82rem',marginBottom:'0.6rem',color:'var(--accent-mint)'}}>
                  📅 월별 손익
                </div>
                <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem'}}>
                  {detail.monthly.map(m => (
                    <div key={m.month} style={{padding:'0.3rem 0.6rem',borderRadius:'5px',
                      background: m.profit>=0 ? 'rgba(239,68,68,0.12)' : 'rgba(59,130,246,0.12)',
                      border:`1px solid ${m.profit>=0 ? 'rgba(239,68,68,0.25)' : 'rgba(59,130,246,0.25)'}`,
                      textAlign:'center',minWidth:'80px'}}>
                      <div style={{fontSize:'0.65rem',color:'var(--text-secondary)'}}>{m.month}</div>
                      <div style={{fontSize:'0.78rem',fontWeight:700,
                        color: m.profit>=0 ? '#ef4444' : '#3b82f6'}}>
                        {m.profit>=0?'+':''}{Math.round(m.profit/10000).toLocaleString()}만
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 상위/하위 종목 */}
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.75rem'}}>
              {[
                { label:'🏆 수익 상위 종목', data: detail.top_winners || [], color:'#ef4444' },
                { label:'💀 손실 종목', data: detail.top_losers || [], color:'#3b82f6' },
              ].map(({ label, data, color }) => (
                <div key={label} className="glass-panel" style={{padding:'0.8rem 1rem'}}>
                  <div style={{fontWeight:700,fontSize:'0.8rem',marginBottom:'0.5rem',color}}>{label}</div>
                  {data.length === 0 ? <div style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>없음</div> :
                    data.map(d => (
                      <div key={d.name} style={{display:'flex',justifyContent:'space-between',
                        padding:'0.2rem 0',borderBottom:'1px solid rgba(255,255,255,0.04)',
                        fontSize:'0.78rem'}}>
                        <span>{d.name}</span>
                        <span style={{fontWeight:700,color}}>{fmtAmt(d.profit)}</span>
                      </div>
                    ))
                  }
                </div>
              ))}
            </div>

            {/* 매매 내역 테이블 */}
            {detail.trades && detail.trades.length > 0 && (
              <div className="glass-panel" style={{overflow:'clip'}}>
                <div style={{padding:'0.6rem 1rem',borderBottom:'1px solid var(--glass-border)',
                  display:'flex',alignItems:'center',gap:'0.5rem'}}>
                  <span style={{fontWeight:700,fontSize:'0.82rem'}}>📋 매매 내역 (최근 200건)</span>
                  <button onClick={() => {
                    const rows = detail.trades.map(t => ({
                      종목코드:t.stock_code, 종목명:t.stock_name||'',
                      매수일:t.entry_date, 매도일:t.exit_date,
                      매수가:t.entry_price, 매도가:t.exit_price,
                      수량:t.qty, 수익률:t.profit_pct, 손익금:t.profit_amt,
                      매도사유:t.exit_reason
                    }));
                    const BOM='\uFEFF', ks=Object.keys(rows[0]);
                    const csv=BOM+ks.join(',')+'\n'+rows.map(r=>ks.map(k=>r[k]).join(',')).join('\n');
                    const a=document.createElement('a');
                    a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8;'}));
                    a.download='backtest_trades.csv'; a.click();
                  }} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',borderRadius:'5px',fontSize:'0.7rem',
                    cursor:'pointer',border:'1px solid rgba(45,212,191,0.3)',background:'rgba(45,212,191,0.08)',
                    color:'var(--accent-mint)'}}>⬇ CSV</button>
                </div>
                <table className="premium-table">
                  <thead><tr>
                    <th>종목명</th>
                    <th>매수일</th><th>매도일</th>
                    <th style={{textAlign:'right'}}>매수가</th>
                    <th style={{textAlign:'right'}}>매도가</th>
                    <th style={{textAlign:'right'}}>수익률</th>
                    <th style={{textAlign:'right'}}>손익금</th>
                    <th>매도사유</th>
                  </tr></thead>
                  <tbody>
                    {detail.trades.map((t, i) => (
                      <tr key={i}>
                        <td style={{fontWeight:600}}>{t.stock_name || t.stock_code}</td>
                        <td style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>{t.entry_date}</td>
                        <td style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>{t.exit_date}</td>
                        <td style={{textAlign:'right'}}>{Math.round(t.entry_price).toLocaleString()}</td>
                        <td style={{textAlign:'right'}}>{Math.round(t.exit_price).toLocaleString()}</td>
                        <td style={{textAlign:'right',fontWeight:700,color:clr(t.profit_pct)}}>
                          {t.profit_pct>=0?'+':''}{Number(t.profit_pct).toFixed(1)}%
                        </td>
                        <td style={{textAlign:'right',color:clr(t.profit_amt)}}>
                          {fmtAmt(t.profit_amt)}
                        </td>
                        <td style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {detail && detail.status === 'error' && (
          <div className="glass-panel" style={{padding:'1.5rem',color:'#f87171'}}>
            ⚠️ 백테스트 오류: {detail.summary_text}
          </div>
        )}

        {/* ══ 전략 상세 설명 ══ */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
          <div style={{fontWeight:700,fontSize:'0.9rem',marginBottom:'0.8rem',color:'var(--accent-mint)',
            borderBottom:'1px solid var(--glass-border)',paddingBottom:'0.5rem'}}>
            📘 전략별 상세 설명
          </div>
          {[
            {
              key:'V1 가치매수',
              badge:'V1',
              color:'#34d399',
              summary:'Graham 내재가치 + 수급 기반 저평가 발굴',
              buy:[
                'Graham 내재가치(√22.5×EPS×BPS) 대비 25% 이상 할인 OR PBR<0.7 AND PER<10',
                '기관·외국인 5일 동반 순매수',
                'RS 상대강도 > 0.7 (시장 대비 강세)',
                'KOSPI MA120 상단',
                '시총 1000억원 이상',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                'MA60 붕괴',
              ],
              note:'전 기간 안정적 수익. MDD -8~-14%로 낮음. 장세 무관하게 꾸준히 작동.',
            },
            {
              key:'V2 재무성장',
              badge:'V2',
              color:'#fbbf24',
              summary:'재무성장+수익성+추세 복합 스코어 — 단일팩터 최강 CAGR',
              buy:[
                '매출 YoY > 15% + 영업이익 YoY > 20%',
                'ROE > 10% + 영업이익률 > 8%',
                '부채비율 < 150%',
                '현재가 > MA60 (중기 추세 확인)',
                '거래량 > 20일 평균 1.5배',
              ],
              sell:['손절: -8%', '추적손절: -10%', 'MA60 붕괴'],
              note:'2020~2021 CAGR 32.7%. 재무 성장주 집중. MDD -11~-17% 수준.',
            },
            {
              key:'V3 추세단독',
              badge:'V3',
              color:'#94a3b8',
              summary:'Graham 극단 저평가 단독 — 손절 빈도 높아 비권장',
              buy:[
                'PBR < 0.5 AND PER < 8 (극단 저평가)',
                '기관·외국인 동반 순매수',
              ],
              sell:['손절: -8%', 'MA60 붕괴'],
              note:'⚠️ 저평가 함정(value trap) 위험. V1보다 조건 완화, 손실 빈도 높음. V1과 조합 시 활용.',
            },
            {
              key:'V4 복합콤보',
              badge:'V4',
              color:'#a78bfa',
              summary:'Minervini 추세 + Graham 가치 + 수급 삼중 필터',
              buy:[
                'MA20>MA60>MA120 정배열 (Minervini)',
                '52주 고점 -20% 이내',
                'RSI ≥ 60',
                '거래량 > 20일 평균 2.0배',
                'Graham 내재가치 할인 25%+ OR (PBR<0.7 AND PER<10) [가치 조건]',
                '기관·외국인 5일 동반 순매수 [수급 조건]',
                'RS 상대강도 보조 [위 두 조건 중 하나 이상]',
                'KOSPI MA120 상단',
              ],
              sell:['손절: -6%', '추적손절: -10%', '익절: +15%', 'MA60 붕괴', '최소 5일 보유 후 적용'],
              note:'삼중 필터로 조건 엄격, 매수 건수 적음. 2023~2025 CAGR +23%. 하락장 방어력 우수.',
            },
            {
              key:'V5 수급모멘텀',
              badge:'V5',
              color:'#94a3b8',
              summary:'단기 기관·외국인 동반 순매수 모멘텀 — 단독 비권장',
              buy:[
                '기관 5일 순매수 > 0 AND 외국인 5일 순매수 > 0',
                '거래량 20일 평균 1.5배 이상',
                '현재가 > MA20',
              ],
              sell:['손절: -5%', '추적손절: -8%', '5일 후 수급 반전 시'],
              note:'⚠️ 수급 데이터가 57일치로 제한되어 백테스트 신뢰도 낮음. 장기 독립 운용 비권장.',
            },
            {
              key:'V6 추세+재무',
              badge:'V6',
              color:'#38bdf8',
              summary:'MA정배열 추세 + 재무성장 이중 필터 (V2에 추세 조건 추가)',
              buy:[
                'MA20 > MA60 정배열',
                '매출 YoY > 10% + 영업이익 YoY > 15%',
                'ROE > 8%',
                '거래량 > 20일 평균 1.3배',
              ],
              sell:['손절: -8%', '추적손절: -10%', 'MA60 붕괴'],
              note:'V2에 추세 조건 추가. 진입 타이밍 개선, 필터 강화. V2보다 매수 건수 적음.',
            },
            {
              key:'V7 가치+모멘텀',
              badge:'V7',
              color:'#38bdf8',
              summary:'저평가(PBR<1) 가치주 + 단기 모멘텀 필터 복합',
              buy:[
                'PBR < 1.0 AND PER < 15',
                '3개월 수익률 > 0% (모멘텀 확인)',
                '기관 또는 외국인 순매수',
                '거래량 > 15일 평균 1.2배',
              ],
              sell:['손절: -7%', '추적손절: -10%', 'MA60 붕괴'],
              note:'가치주의 단기 모멘텀 반등을 포착. V1 대비 모멘텀 가중 높음.',
            },
            {
              key:'V8 수출선행',
              badge:'V8',
              color:'#06b6d4',
              summary:'HS무역통계 월별수출 변곡점 + MA60 — 실제 펀더멘탈 선행지표',
              buy:[
                '수출 YoY ≥ 8% (최근 3개월 평균)',
                '수출 가속도: 최근 YoY가 이전 YoY 대비 +20%p 이상 반등',
                'HS무역통계 발표 2개월 지연 보정 (미래 참조 방지)',
                '현재가 MA60 ± 20% 범위',
                'RSI 42~65',
                '영업이익 > 0 (흑자 기업)',
              ],
              sell:['손절: -8%', '수출 YoY < -5% 2개월 연속 시 청산 (수출감소청산)', '추적손절: -12%'],
              note:'★ 실제 수출 데이터 연동. 유니버스 94개 수출주 한정. 소형 유니버스로 기간별 편차 존재.',
            },
            {
              key:'V10 이익폭발',
              badge:'V10',
              color:'#f97316',
              summary:'영업이익 YoY 80%+ + 매출 YoY 30%+ 고성장주',
              buy:[
                '영업이익 YoY ≥ 80% (전년동기대비)',
                '매출 YoY ≥ 30%',
                '연속 2분기 이상 성장 확인',
                '현재가 > MA60',
                '거래량 > 10일 평균 1.3배',
                'KOSPI MA120 상단',
              ],
              sell:['손절: -10%', '익절: +30%', '추적손절: -12%'],
              note:'고성장 국면 포착. 2022~2023 엔터·방산·조선주 유형. 성장이 꺾이면 빠른 손절 필요.',
            },
            {
              key:'V11 흑자전환',
              badge:'V11',
              color:'#4ade80',
              summary:'적자→흑자 전환 + 대규모 이익 폭발 포착 — 하락장에서도 매수 (시장필터 없음)',
              buy:[
                '현재 2분기 연속 영업이익 ≥ 20억',
                '1년 전 동일분기 OP < 10억 (순수 흑자전환) OR 현재 OP가 1년 전 대비 5배 이상 (이익폭발)',
                '2년 전 데이터 fallback (신규상장주 등)',
                '매출 YoY ≥ 10%',
                '현재가 ≥ MA120 × 0.97',
                '거래량 ≥ 10일 평균 1.5배',
                '⚠️ KOSPI 시장필터 없음 — 흑자전환은 하락장에서도 매수 (구조적 전략)',
              ],
              sell:['손절: -10%', '익절: +30%', '추적손절: -12%', 'MA60 붕괴 (5일 이후)'],
              note:'★ 22.11~23.10 CAGR +41.1%. 하락장(21.12~22.10) CAGR +75.7% (시장필터 제거 효과). 20.3~21.11 CAGR +36.6%.',
            },
            {
              key:'V12 섹터대세',
              badge:'V12',
              color:'#94a3b8',
              summary:'섹터 알파 + 강세 종목 진입 — 후행성 주의',
              buy:[
                'KOSPI 3개월 대비 섹터 alpha ≥ 15%',
                '해당 섹터 내 개별종목 거래량 폭증',
                '현재가 > MA60',
              ],
              sell:['손절: -8%', '추적손절: -10%', '섹터 알파 반전 시'],
              note:'⚠️ 섹터 대세 신호는 후행성 — 섹터가 이미 상승한 후 진입. MDD 주의. 다른 전략과 조합 권장.',
            },
            {
              key:'VT MA정배열',
              badge:'VT',
              color:'#fbbf24',
              summary:'Minervini 추세추종 — 정배열 진입, KOSPI 시장필터',
              buy:[
                'MA20 > MA60 > MA120 세 이평선 정배열',
                'RSI 42~72 (과매도/과매수 중간 영역)',
                '거래량 5일 평균 대비 1.3배 이상',
                '52주 고점 대비 -30% 이내 (신고가 근접)',
                'KOSPI MA120 상단 (하락장 매수 차단)',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10% (최소 +3% 수익, 5일 이후)',
                'MA60 붕괴 시 청산',
              ],
              note:'★ 23.11~24.12 CAGR +82.5%(AI/반도체 랠리). 22.11~23.10 +20.9%. 하락장(21.12~22.10) -23.5%. 최근(24.6~25.5) +5.8%.',
            },
            {
              key:'공통 규칙',
              badge:'common',
              color:'rgba(255,255,255,0.5)',
              summary:'모든 전략 공통 적용 사항',
              buy:[
                '시총 1000억원 이상 (소형주 제외)',
                '월별 신규 매수 최대 10개 종목 (점수순 우선선택)',
                '동시 보유 최대 10개 종목',
                '진입 점수 = 거래량비율×0.4 + RSI점수×0.4 + 진입품질×0.2',
                '재무 데이터 공시 지연 반영 (Q1→5월, Q2→8월, Q3→11월, 연간→익년3월)',
              ],
              sell:[
                '강제 청산: 시뮬레이션 종료일에 전 포지션 청산',
              ],
              note:'수급 데이터는 최근 57일치만 수집됨. 오래된 백테스트 구간에서는 수급 시그널 0값 처리.',
            },
          ].map(s => (
            <div key={s.key} style={{marginBottom:'1rem',paddingBottom:'1rem',
              borderBottom:'1px solid rgba(255,255,255,0.05)'}}>
              <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.4rem'}}>
                <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.7rem',fontWeight:700,
                  background:`${s.color}22`,border:`1px solid ${s.color}55`,color:s.color}}>
                  {s.badge.toUpperCase()}
                </span>
                <span style={{fontWeight:700,fontSize:'0.85rem'}}>{s.key}</span>
                <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.45)',marginLeft:'0.3rem'}}>
                  — {s.summary}
                </span>
              </div>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.5rem',marginBottom:'0.35rem'}}>
                <div>
                  <div style={{fontSize:'0.68rem',color:'rgba(34,197,94,0.7)',fontWeight:600,marginBottom:'0.2rem'}}>
                    📈 매수 조건
                  </div>
                  {s.buy.map((b,i) => (
                    <div key={i} style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.6)',
                      paddingLeft:'0.5rem',marginBottom:'0.12rem'}}>
                      • {b}
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{fontSize:'0.68rem',color:'rgba(248,113,113,0.7)',fontWeight:600,marginBottom:'0.2rem'}}>
                    📉 매도 조건
                  </div>
                  {s.sell.map((sl,i) => (
                    <div key={i} style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.6)',
                      paddingLeft:'0.5rem',marginBottom:'0.12rem'}}>
                      • {sl}
                    </div>
                  ))}
                </div>
              </div>
              {s.note && (
                <div style={{fontSize:'0.68rem',color:'rgba(251,191,36,0.75)',
                  padding:'0.2rem 0.5rem',background:'rgba(251,191,36,0.06)',
                  borderRadius:'4px',borderLeft:'2px solid rgba(251,191,36,0.3)'}}>
                  💡 {s.note}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  };

  // ── 설정 ──────────────────────────────────────────────────
  const SettingsView = () => {
    const [cfg, setCfg] = React.useState({
      price_interval:  60,    // 주가 수집 주기 (초)
      supply_interval: 300,   // 수급 수집 주기 (초)
      supply_after_close: 1800, // 장마감 후 수급 주기 (초)
      kis_enabled:     true,
      naver_enabled:   true,
      dart_enabled:    true,
    });
    const [saved, setSaved] = React.useState(false);
    const [sysInfo, setSysInfo] = React.useState(null);

    React.useEffect(() => {
      fetch(API('/api/system/status')).then(r=>r.ok?r.json():null).then(d=>setSysInfo(d)).catch(()=>{});
    }, []);

    const fmtSec = (s) => s >= 3600 ? (s/3600).toFixed(0)+'시간' : s >= 60 ? (s/60).toFixed(0)+'분' : s+'초';

    const sources = [
      { key:'kis_enabled',   name:'KIS API',    desc:'체결내역·주가 실시간', color:'#facc15' },
      { key:'naver_enabled', name:'네이버 금융', desc:'수급·시장정보·종목정보', color:'#34d399' },
      { key:'dart_enabled',  name:'DART',        desc:'재무제표 (자정 배치)', color:'#60a5fa' },
    ];

    const intervals = [
      { key:'price_interval',       label:'주가 수집 주기',      unit:'초', min:30,  max:300 },
      { key:'supply_interval',      label:'장중 수급 주기',      unit:'초', min:60,  max:600 },
      { key:'supply_after_close',   label:'장마감 후 수급 주기', unit:'초', min:600, max:3600 },
    ];

    return (
      <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1.2rem', maxWidth:'800px' }}>
        <div className="section-title">
          <Database size={20} color="var(--accent-purple)" />
          <h2>시스템 설정</h2>
        </div>

        {/* 데이터 소스 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)' }}>데이터 소스</h3>
          <div style={{ display:'flex', flexDirection:'column', gap:'0.8rem' }}>
            {sources.map(s => (
              <div key={s.key} style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
                padding:'0.8rem 1rem', borderRadius:'8px', background:'rgba(255,255,255,0.03)',
                border:'1px solid var(--glass-border)' }}>
                <div>
                  <span style={{ fontWeight:700, color:s.color }}>{s.name}</span>
                  <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginLeft:'0.8rem' }}>{s.desc}</span>
                </div>
                <button onClick={() => setCfg(p => ({...p, [s.key]: !p[s.key]}))} style={{
                  width:'44px', height:'24px', borderRadius:'12px', border:'none', cursor:'pointer',
                  background: cfg[s.key] ? 'var(--accent-mint)' : 'rgba(255,255,255,0.15)',
                  transition:'background 0.2s', position:'relative',
                }}>
                  <div style={{ position:'absolute', top:'3px', left: cfg[s.key]?'23px':'3px',
                    width:'18px', height:'18px', borderRadius:'50%', background:'white', transition:'left 0.2s' }}/>
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* 수집 주기 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)' }}>데이터 수집 주기</h3>
          <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
            {intervals.map(iv => (
              <div key={iv.key} style={{ display:'grid', gridTemplateColumns:'1fr auto auto', alignItems:'center', gap:'1rem' }}>
                <div>
                  <p style={{ fontWeight:600, fontSize:'0.85rem' }}>{iv.label}</p>
                  <p style={{ fontSize:'0.72rem', color:'var(--text-secondary)' }}>현재: {fmtSec(cfg[iv.key])}</p>
                </div>
                <input type="range" min={iv.min} max={iv.max} step={iv.min}
                  value={cfg[iv.key]} onChange={e => setCfg(p => ({...p, [iv.key]: Number(e.target.value)}))}
                  style={{ width:'160px', accentColor:'var(--accent-mint)' }}/>
                <span style={{ fontSize:'0.8rem', color:'var(--accent-mint)', minWidth:'50px', textAlign:'right' }}>
                  {fmtSec(cfg[iv.key])}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* 시스템 현황 요약 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)' }}>시스템 현황</h3>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:'0.75rem' }}>
            {[
              { label:'백엔드',    val:'FastAPI (Python 3.11)', color:'var(--accent-mint)' },
              { label:'데이터베이스', val:'SQLite',             color:'#60a5fa' },
              { label:'외부 접속', val:'stock.leanguy.cloud',   color:'#a78bfa' },
              { label:'장 상태',   val: sysInfo?.market_open ? '🟢 장중' : '🔴 장마감', color:'inherit' },
              { label:'주가 수집', val:`${cfg.price_interval}초 주기`, color:'inherit' },
              { label:'수급 수집', val:`${fmtSec(cfg.supply_interval)} 주기`, color:'inherit' },
            ].map(item => (
              <div key={item.label} style={{ padding:'0.8rem', borderRadius:'8px', background:'rgba(255,255,255,0.03)', border:'1px solid var(--glass-border)' }}>
                <p style={{ fontSize:'0.7rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>{item.label}</p>
                <p style={{ fontSize:'0.85rem', fontWeight:600, color:item.color }}>{item.val}</p>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display:'flex', justifyContent:'flex-end', gap:'0.5rem' }}>
          {saved && <span style={{ color:'var(--accent-mint)', fontSize:'0.8rem', alignSelf:'center' }}>✓ 저장됨 (다음 재시작 시 적용)</span>}
          <button onClick={() => { setSaved(true); setTimeout(()=>setSaved(false),3000); }}
            style={{ padding:'0.5rem 1.5rem', borderRadius:'8px', background:'rgba(45,212,191,0.2)',
              border:'1px solid var(--accent-mint)', color:'var(--accent-mint)', cursor:'pointer', fontWeight:700 }}>
            설정 저장
          </button>
        </div>
        {/* 추세추종 필터 파라미터 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)',
            display:'flex', alignItems:'center', gap:'0.5rem' }}>
            <TrendingUp size={16} color="var(--accent-mint)"/> 추세추종 필터 파라미터 (미너비니 3단계)
          </h3>
          <div style={{ display:'flex', flexDirection:'column', gap:'0.5rem' }}>
            {[
              { stage:'[1단계] 유동성',    params:[
                  { label:'시가총액 최소',      value:'1,000억 이상',    desc:'잡주·소형주 제외' },
                  { label:'거래대금 최소',      value:'5일 평균 100억↑', desc:'volume×close 기준' },
              ]},
              { stage:'[2단계] 추세 템플릿', params:[
                  { label:'MA120/200 조건',   value:'현재가 > MA120, MA200', desc:'장기 추세 위' },
                  { label:'장기 정배열',       value:'MA120 > MA200',   desc:'골든크로스 확인' },
                  { label:'신고가 근접',       value:'52주 고점 -20% 이내', desc:'매물대 없는 구간' },
                  { label:'단기 정배열',       value:'현재가>MA5>MA20>MA60', desc:'완전정배열 필수(부분 허용)' },
              ]},
              { stage:'[3단계] 진입 트리거', params:[
                  { label:'RSI(14) 최소',     value:'60 이상 (필수)',   desc:'상승 모멘텀 확인' },
                  { label:'거래량 폭발 기준', value:'20일 평균 × 2.0배', desc:'+3점, 1.5배=+2점' },
                  { label:'BB 스퀴즈',        value:'밴드폭 최소 × 1.5 이내 + BB 상단 돌파', desc:'+3점' },
              ]},
              { stage:'등급 임계점',         params:[
                  { label:'강력매수',         value:'점수 ≥ 20점',     desc:'' },
                  { label:'매수',             value:'점수 ≥ 14점',     desc:'' },
                  { label:'관심',             value:'점수 ≥ 10점',     desc:'' },
              ]},
            ].map(group => (
              <div key={group.stage} style={{ padding:'0.75rem', borderRadius:'8px',
                background:'rgba(255,255,255,0.02)', border:'1px solid var(--glass-border)' }}>
                <p style={{ fontSize:'0.78rem', fontWeight:700, color:'var(--accent-mint)', marginBottom:'0.5rem' }}>
                  {group.stage}
                </p>
                <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))', gap:'0.3rem 1rem' }}>
                  {group.params.map(p => (
                    <div key={p.label} style={{ display:'flex', alignItems:'baseline', gap:'0.4rem' }}>
                      <span style={{ fontSize:'0.72rem', color:'rgba(255,255,255,0.45)', minWidth:'120px' }}>{p.label}</span>
                      <span style={{ fontSize:'0.72rem', color:'rgba(255,255,255,0.85)', fontWeight:600 }}>{p.value}</span>
                      {p.desc && <span style={{ fontSize:'0.65rem', color:'rgba(255,255,255,0.3)' }}>({p.desc})</span>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <TelegramSettings />
        <SignalSettings />
      </div>
    );
  };


  // ── 텔레그램 모니터 설정 ─────────────────────────────────────
  const TelegramSettings = () => {
    const [channels,   setChannels]   = React.useState([]);
    const [newChannel, setNewChannel] = React.useState('');
    const [schedule,   setSchedule]   = React.useState({ hour1: 9, hour2: 21 });
    const [apiKeys,    setApiKeys]    = React.useState({ openai_key:'', bot_token:'', chat_id:'' });
    const [loading,    setLoading]    = React.useState(true);
    const [msg,        setMsg]        = React.useState('');

    React.useEffect(() => {
      fetch(API('/api/telegram/settings')).then(r=>r.ok?r.json():null).then(d=>{
        if (d) {
          setChannels(d.channels||[]);
          setSchedule({ hour1: d.hour1??9, hour2: d.hour2??21 });
          setApiKeys({ openai_key:d.openai_key||'', bot_token:d.bot_token||'', chat_id:d.chat_id||'' });
        }
        setLoading(false);
      }).catch(()=>setLoading(false));
    }, []);

    const saveSettings = async () => {
      const res = await fetch(API('/api/telegram/settings'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ channels, hour1:schedule.hour1, hour2:schedule.hour2, ...apiKeys }),
      });
      if (res.ok) { setMsg('✓ 저장됨 — cron 자동 업데이트'); setTimeout(()=>setMsg(''),3000); }
      else { setMsg('저장 실패'); setTimeout(()=>setMsg(''),2000); }
    };

    const addChannel = () => {
      let ch = newChannel.trim();
      if (!ch) return;
      if (!ch.startsWith('@')) ch = '@' + ch;
      if (channels.includes(ch)) { setMsg('이미 등록된 채널'); setTimeout(()=>setMsg(''),2000); return; }
      setChannels(prev=>[...prev, ch]);
      setNewChannel('');
    };

    const HOURS = Array.from({length:24},(_,i)=>i);
    if (loading) return null;

    return (
      <div className="glass-panel" style={{padding:'1.2rem'}}>
        <h3 style={{fontSize:'0.9rem',fontWeight:700,marginBottom:'1.2rem',color:'var(--text-secondary)',
          display:'flex',alignItems:'center',gap:'0.5rem'}}>
          <Globe size={16} color="#38bdf8"/> 텔레그램 모니터 설정
        </h3>

        {/* 수집 시간 */}
        <div style={{marginBottom:'1.2rem',paddingBottom:'1.2rem',borderBottom:'1px solid var(--glass-border)'}}>
          <p style={{fontSize:'0.8rem',fontWeight:600,marginBottom:'0.7rem',color:'var(--accent-mint)'}}>📅 수집 시간 (하루 2회)</p>
          <div style={{display:'flex',gap:'1.5rem',alignItems:'center',flexWrap:'wrap'}}>
            {[{label:'1회차',key:'hour1'},{label:'2회차',key:'hour2'}].map(({label,key})=>(
              <div key={key} style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                <span style={{fontSize:'0.8rem',color:'var(--text-secondary)',minWidth:'40px'}}>{label}</span>
                <select value={schedule[key]} onChange={e=>setSchedule(p=>({...p,[key]:Number(e.target.value)}))}
                  style={{padding:'0.3rem 0.6rem',borderRadius:'6px',background:'rgba(255,255,255,0.08)',
                    border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.85rem'}}>
                  {HOURS.map(h=><option key={h} value={h} style={{background:'#1a1a2e'}}>{String(h).padStart(2,'0')}:00</option>)}
                </select>
              </div>
            ))}
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.3)'}}>※ 저장 시 cron 자동 업데이트</span>
          </div>
        </div>

        {/* 채널 관리 */}
        <div style={{marginBottom:'1.2rem',paddingBottom:'1.2rem',borderBottom:'1px solid var(--glass-border)'}}>
          <p style={{fontSize:'0.8rem',fontWeight:600,marginBottom:'0.7rem',color:'var(--accent-mint)'}}>📡 모니터링 채널</p>
          <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem',marginBottom:'0.7rem',minHeight:'32px'}}>
            {channels.map(ch=>(
              <div key={ch} style={{display:'flex',alignItems:'center',gap:'0.3rem',padding:'0.2rem 0.6rem',
                borderRadius:'20px',background:'rgba(56,189,248,0.12)',border:'1px solid rgba(56,189,248,0.3)'}}>
                <span style={{fontSize:'0.8rem',color:'#38bdf8',fontWeight:600}}>{ch}</span>
                <button onClick={()=>setChannels(p=>p.filter(c=>c!==ch))}
                  style={{background:'none',border:'none',color:'rgba(255,255,255,0.5)',
                    cursor:'pointer',padding:'0 2px',fontSize:'1rem',lineHeight:1}}>×</button>
              </div>
            ))}
            {channels.length===0 && <span style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>등록된 채널 없음</span>}
          </div>
          <div style={{display:'flex',gap:'0.5rem'}}>
            <input value={newChannel} onChange={e=>setNewChannel(e.target.value)}
              onKeyDown={e=>e.key==='Enter'&&addChannel()}
              placeholder="@채널명 입력 후 Enter 또는 추가 버튼"
              style={{flex:1,padding:'0.4rem 0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.06)',
                border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.85rem'}}/>
            <button onClick={addChannel} style={{padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(56,189,248,0.15)',border:'1px solid rgba(56,189,248,0.4)',
              color:'#38bdf8',cursor:'pointer',fontWeight:600,fontSize:'0.82rem',whiteSpace:'nowrap'}}>+ 추가</button>
          </div>
        </div>

        {/* API 키 */}
        <div style={{marginBottom:'1.2rem'}}>
          <p style={{fontSize:'0.8rem',fontWeight:600,marginBottom:'0.7rem',color:'var(--accent-mint)'}}>🔑 API 키 설정</p>
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
            {[
              {key:'openai_key', label:'OpenAI API Key',      placeholder:'sk-proj-...'},
              {key:'bot_token',  label:'텔레그램 봇 토큰',    placeholder:'1234567890:AAF...'},
              {key:'chat_id',    label:'결과 전송 채널 ID',   placeholder:'-1001234567890'},
            ].map(({key,label,placeholder})=>(
              <div key={key} style={{display:'grid',gridTemplateColumns:'160px 1fr',alignItems:'center',gap:'0.75rem'}}>
                <span style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>{label}</span>
                <input type="password" value={apiKeys[key]}
                  onChange={e=>setApiKeys(p=>({...p,[key]:e.target.value}))}
                  placeholder={placeholder}
                  style={{padding:'0.35rem 0.7rem',borderRadius:'6px',background:'rgba(255,255,255,0.06)',
                    border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.82rem',width:'100%'}}/>
              </div>
            ))}
          </div>
        </div>

        {/* 저장 버튼 */}
        <div style={{display:'flex',justifyContent:'flex-end',alignItems:'center',gap:'0.75rem'}}>
          {msg && <span style={{fontSize:'0.8rem',color:msg.includes('실패')?'#ef4444':'var(--accent-mint)'}}>{msg}</span>}
          <button onClick={saveSettings} style={{padding:'0.45rem 1.2rem',borderRadius:'8px',
            background:'rgba(56,189,248,0.15)',border:'1px solid rgba(56,189,248,0.4)',
            color:'#38bdf8',cursor:'pointer',fontWeight:700,fontSize:'0.85rem'}}>
            💾 텔레그램 설정 저장
          </button>
        </div>
      </div>
    );
  };


  // ── 텔레그램 종목 언급 순위 ─────────────────────────────────
  const TelegramMentions = () => {
    const [allData,  setAllData]  = React.useState({ dates: [], stocks: [] });
    const [weekly,   setWeekly]   = React.useState([]);
    const [monthly,  setMonthly]  = React.useState([]);
    const [loading,  setLoading]  = React.useState(true);
    const [activeDay, setActiveDay] = React.useState(null); // null = 전체보기

    // 이번주 월~일 7일 고정 날짜 계산
    const getWeekDates = () => {
      const today  = new Date();
      const monday = new Date(today);
      monday.setDate(today.getDate() - today.getDay() + 1); // 이번주 월요일
      return Array.from({length: 7}, (_, i) => {
        const d = new Date(monday);
        d.setDate(monday.getDate() + i);
        return d.toISOString().slice(0, 10);
      });
    };
    const WEEK_DATES = React.useMemo(() => getWeekDates(), []);
    const DAYS_KO    = ['월','화','수','목','금','토','일'];

    React.useEffect(() => {
      const load = async () => {
        setLoading(true);
        try {
          const [d, w, m] = await Promise.all([
            fetch(API('/api/telegram/mentions/daily')).then(r => r.ok ? r.json() : { dates:[], stocks:[] }),
            fetch(API('/api/telegram/mentions/weekly')).then(r => r.ok ? r.json() : []),
            fetch(API('/api/telegram/mentions/monthly')).then(r => r.ok ? r.json() : []),
          ]);
          setAllData(d); setWeekly(w); setMonthly(m);
          setActiveDay(null);
        } catch(e) { console.error(e); }
        finally { setLoading(false); }
      };
      load();
    }, []);

    const marketColor = (m) => m === 'KOSPI' ? '#3b82f6' : m === 'KOSDAQ' ? '#22c55e' : '#94a3b8';
    const marketTag   = (m) => m === 'KOSPI' ? '🔵' : m === 'KOSDAQ' ? '🟢' : '⚪';

    // 선택된 요일 또는 전체 기준으로 TOP 20 계산
    const displayStocks = React.useMemo(() => {
      if (!allData.stocks.length) return [];
      if (activeDay === null) {
        // 전체: 7일 합계 기준 정렬
        return [...allData.stocks]
          .sort((a, b) => b.total - a.total)
          .slice(0, 20);
      } else {
        // 특정 날짜: 해당 날 언급 횟수 기준 정렬
        return [...allData.stocks]
          .map(s => ({...s, dayCount: s.daily[activeDay] || 0}))
          .filter(s => s.dayCount > 0)
          .sort((a, b) => b.dayCount - a.dayCount)
          .slice(0, 20);
      }
    }, [allData, activeDay]);

    const maxCnt = displayStocks.length
      ? Math.max(...displayStocks.map(s => activeDay ? s.dayCount : s.total), 1)
      : 1;

    if (loading) return (
      <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:'300px',color:'var(--accent-mint)'}}>
        데이터 로딩 중...
      </div>
    );

    const today = new Date().toISOString().slice(0,10);

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1.5rem'}}>

        {/* ── 일별 TOP 20 테이블 (요일 탭 고정) ── */}
        <div className="glass-panel" style={{overflow:'clip'}}>
          {/* 헤더 */}
          <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
            <Globe size={16} color="#38bdf8"/>
            <span style={{fontWeight:700,color:'#38bdf8'}}>일별 언급 종목 TOP 20</span>
            <span style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginLeft:'auto'}}>이번주 · 오전/오후 합산</span>
          </div>

          {/* 가로 7컬럼 테이블 */}
          {allData.stocks.length === 0 ? (
            <div style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              아직 수집된 데이터가 없습니다.<br/>
              <span style={{fontSize:'0.8rem'}}>telegram_monitor.py를 실행하면 데이터가 쌓입니다.</span>
            </div>
          ) : (
            <div style={{overflowX:'auto'}}>
              <table className="premium-table" style={{width:'100%',minWidth:'700px'}}>
                <thead>
                  <tr>
                    <th style={{minWidth:'28px',position:'sticky',left:0,background:'var(--bg-dark)'}}>#</th>
                    <th style={{minWidth:'90px',position:'sticky',left:'28px',background:'var(--bg-dark)'}}>종목명</th>
                    <th style={{minWidth:'55px'}}>시장</th>
                    {WEEK_DATES.map((date, i) => {
                      const isToday   = date === today;
                      const hasFuture = date > today;
                      return (
                        <th key={date} style={{
                          textAlign:'center', minWidth:'52px',
                          color: hasFuture ? 'rgba(255,255,255,0.2)' : isToday ? '#38bdf8' : 'var(--text-secondary)',
                          fontSize:'0.7rem',
                        }}>
                          {DAYS_KO[i]}
                          {isToday && <span style={{display:'block',fontSize:'0.55rem',color:'#38bdf8'}}>오늘</span>}
                        </th>
                      );
                    })}
                    <th style={{textAlign:'right',minWidth:'55px',color:'#38bdf8'}}>합계</th>
                  </tr>
                </thead>
                <tbody>
                  {allData.stocks.slice(0,20).map((s, i) => {
                    const maxDay = Math.max(...WEEK_DATES.map(d => s.daily[d] || 0), 1);
                    return (
                      <tr key={s.stock_name}>
                        <td style={{color:'var(--text-secondary)',fontSize:'0.78rem',position:'sticky',left:0,background:'var(--bg-dark)'}}>{i+1}</td>
                        <td style={{position:'sticky',left:'28px',background:'var(--bg-dark)'}}>
                          <span style={{fontWeight:600,cursor:'pointer',fontSize:'0.85rem'}}
                            onClick={()=>{changeStock(s.stock_name);changeTab('analysis');}}>
                            {s.stock_name}
                          </span>
                        </td>
                        <td>
                          <span style={{fontSize:'0.7rem',color:marketColor(s.market)}}>
                            {marketTag(s.market)} {s.market}
                          </span>
                        </td>
                        {WEEK_DATES.map(date => {
                          const cnt       = s.daily[date] || 0;
                          const hasFuture = date > today;
                          return (
                            <td key={date} style={{textAlign:'center',padding:'0.6rem 0.3rem'}}>
                              {hasFuture ? (
                                <span style={{color:'rgba(255,255,255,0.1)'}}>-</span>
                              ) : cnt > 0 ? (
                                <div style={{display:'flex',flexDirection:'column',alignItems:'center',gap:'2px'}}>
                                  <span style={{
                                    fontSize:'0.82rem', fontWeight:700,
                                    color: cnt>=5?'#f59e0b':cnt>=3?'#38bdf8':'var(--text-primary)',
                                  }}>{cnt}</span>
                                  <div style={{width:'28px',height:'3px',borderRadius:'2px',background:'rgba(255,255,255,0.07)'}}>
                                    <div style={{
                                      width:`${(cnt/maxDay)*100}%`, height:'100%', borderRadius:'2px',
                                      background: cnt>=5?'#f59e0b':cnt>=3?'#38bdf8':'rgba(45,212,191,0.6)',
                                    }}/>
                                  </div>
                                </div>
                              ) : (
                                <span style={{color:'rgba(255,255,255,0.15)',fontSize:'0.75rem'}}>-</span>
                              )}
                            </td>
                          );
                        })}
                        <td style={{textAlign:'right',fontWeight:700,color:'#38bdf8',fontSize:'0.88rem'}}>{s.total}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── 주간 / 월간 테이블 ── */}
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'1rem'}}>

          {/* 주간 TOP 20 */}
          <div className="glass-panel" style={{overflow:'clip'}}>
            <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
              <TrendingUp size={15} color="#a78bfa"/>
              <span style={{fontWeight:700,color:'#a78bfa',fontSize:'0.9rem'}}>최근 6일 TOP 20</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',marginLeft:'auto'}}>최근 6일 rolling</span>
            </div>
            {weekly.length === 0 ? (
              <div style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)',fontSize:'0.8rem'}}>데이터 없음</div>
            ) : (
              <table className="premium-table" style={{width:'100%'}}>
                <thead><tr>
                  <th style={{width:'28px'}}>#</th>
                  <th>종목명</th>
                  <th style={{minWidth:'55px'}}>시장</th>
                  <th style={{textAlign:'right'}}>언급</th>
                </tr></thead>
                <tbody>
                  {weekly.map((s, i) => (
                    <tr key={s.stock_name}>
                      <td style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>{i+1}</td>
                      <td style={{fontWeight:600,cursor:'pointer'}}
                        onClick={()=>{changeStock(s.stock_name);changeTab('analysis');}}>
                        {s.stock_name}
                      </td>
                      <td style={{fontSize:'0.72rem',color:marketColor(s.market)}}>{marketTag(s.market)} {s.market}</td>
                      <td style={{textAlign:'right',fontWeight:700,color:'#a78bfa'}}>{s.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* 월간 TOP 20 */}
          <div className="glass-panel" style={{overflow:'clip'}}>
            <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
              <BarChart3 size={15} color="#f59e0b"/>
              <span style={{fontWeight:700,color:'#f59e0b',fontSize:'0.9rem'}}>이번달 TOP 20</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',marginLeft:'auto'}}>1일~오늘</span>
            </div>
            {monthly.length === 0 ? (
              <div style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)',fontSize:'0.8rem'}}>데이터 없음</div>
            ) : (
              <table className="premium-table" style={{width:'100%'}}>
                <thead><tr>
                  <th style={{width:'28px'}}>#</th>
                  <th>종목명</th>
                  <th style={{minWidth:'55px'}}>시장</th>
                  <th style={{textAlign:'right'}}>언급</th>
                </tr></thead>
                <tbody>
                  {monthly.map((s, i) => (
                    <tr key={s.stock_name}>
                      <td style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>{i+1}</td>
                      <td style={{fontWeight:600,cursor:'pointer'}}
                        onClick={()=>{changeStock(s.stock_name);changeTab('analysis');}}>
                        {s.stock_name}
                      </td>
                      <td style={{fontSize:'0.72rem',color:marketColor(s.market)}}>{marketTag(s.market)} {s.market}</td>
                      <td style={{textAlign:'right',fontWeight:700,color:'#f59e0b'}}>{s.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    );
  };

  // ── 고용 정보 ─────────────────────────────────────────────────
  const AnnualEmploymentByReportView = () => {
    // ── 연간 고용인원 추이 탭 (사업보고서 기준) ──
    const [annualQ, setAnnualQ]           = React.useState('');
    const [annualResults, setAnnualResults] = React.useState([]);
    const [annualLoading, setAnnualLoading] = React.useState(false);
    const [selectedCompany, setSelectedCompany] = React.useState(null);
    const [years, setYears]               = React.useState('3');
    const [topRows, setTopRows]           = React.useState([]);
    const [topLoading, setTopLoading]     = React.useState(true);
    const [topSort, setTopSort]           = React.useState('latest');
    const [topShowAll, setTopShowAll]     = React.useState(false);
    const [topSearch, setTopSearch]       = React.useState('');

    React.useEffect(() => {
      setTopLoading(true);
      setTopShowAll(false);
      fetch(`/api/employment-v2/annual-top?sort_by=${topSort}`)
        .then(r => r.json())
        .then(d => { setTopRows(d.rows || []); setTopLoading(false); })
        .catch(() => setTopLoading(false));
    }, [topSort]);

    const searchAnnual = async () => {
      if (!annualQ.trim()) return;
      setAnnualLoading(true);
      try {
        const d = await fetch(`/api/employment-v2/annual-trend?q=${encodeURIComponent(annualQ)}`).then(r => r.json());
        setAnnualResults(d.results || []);
        if (d.results?.length === 1) setSelectedCompany(d.results[0]);
        else setSelectedCompany(null);
      } catch {}
      setAnnualLoading(false);
    };

    const filterHistory = (history) => {
      if (!history) return [];
      const cutYear = years === '1' ? '2025' : years === '2' ? '2024' : '2023';
      return history.filter(h => h.ym >= cutYear);
    };

    const fmtWc = (n) => n != null ? n.toLocaleString('ko-KR') + '명' : '-';
    const diffColor2 = (v) => v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : 'rgba(255,255,255,0.4)';
    const fmtDiff2 = (v) => v != null ? (v > 0 ? '+' : '') + v.toLocaleString('ko-KR') : '-';

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '0.7rem 1.2rem', display: 'flex', gap: '1.5rem', flexWrap: 'wrap', fontSize: '0.78rem', alignItems: 'center' }}>
          <span>📊 기업별 고용인원 연간 추이 — <strong style={{color:'#34d399'}}>사업보고서</strong> 기준 (2023~2025년 연말 기준)</span>
          <span style={{color:'var(--text-secondary)'}}>• 직접 고용인원만 집계 (자회사 제외)</span>
          <span style={{color:'var(--text-secondary)'}}>• 482개 상장기업 대상</span>
          <span style={{color:'#f59e0b', marginLeft:'auto'}}>⚠️ 국민연금과 다른 기준의 사업보고서 데이터</span>
        </div>

        <div className="glass-panel" style={{ overflow: 'hidden' }}>
          <div style={{ padding: '0.8rem 1.2rem', borderBottom: '1px solid rgba(255,255,255,0.07)', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, color: '#fff', fontSize: '0.88rem' }}>📋 전체 기업 연간 인원 현황 (사업보고서 기준, 2025-12)</span>
            <input placeholder="종목명 검색..." value={topSearch} onChange={e => { setTopSearch(e.target.value); setTopShowAll(false); }}
              style={{ padding:'0.22rem 0.6rem', borderRadius:'5px', background:'rgba(255,255,255,0.07)', border:'1px solid rgba(255,255,255,0.12)', color:'#fff', fontSize:'0.78rem', width:'140px' }} />
            <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
              {[['latest','최신인원순'],['growth','1년 증가순'],['name','이름순']].map(([k,l]) => (
                <button key={k} onClick={() => setTopSort(k)} style={{
                  padding: '0.22rem 0.6rem', borderRadius: '5px', fontSize: '0.75rem', cursor: 'pointer',
                  fontWeight: topSort === k ? 700 : 400,
                  background: topSort === k ? 'rgba(45,212,191,0.15)' : 'transparent',
                  color: topSort === k ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
                  border: `1px solid ${topSort === k ? '#2dd4bf' : 'rgba(255,255,255,0.15)'}`,
                }}>{l}</button>
              ))}
            </div>
          </div>
          {topLoading ? (
            <div style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>로딩 중...</div>
          ) : (() => {
            const filteredTop = topSearch
              ? topRows.filter(r => r.stock_name?.includes(topSearch) || r.stock_code?.includes(topSearch))
              : topRows;
            const visibleTop = topShowAll ? filteredTop : filteredTop.slice(0, 15);
            return (
            <div style={{ overflowX: 'auto', overflowY: 'clip' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.81rem' }}>
                <thead>
                  <tr>
                    {['#','종목명','섹터','2025년말','2024년말','2023년말','1년 증감','2년 증감'].map((h,i) => (
                      <th key={i} style={{
                        padding: '0.55rem 0.8rem', textAlign: i <= 2 ? 'left' : 'right',
                        color: '#e2e8f0', borderBottom: '2px solid rgba(59,130,246,0.5)',
                        fontWeight: 600, background: 'rgba(30,58,138,0.4)',
                        whiteSpace: 'nowrap', position: 'sticky', top: 0, zIndex: 5,
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleTop.map((r, i) => (
                    <tr key={r.stock_code} style={{ cursor: 'pointer', transition: 'background 0.12s' }}
                      onClick={() => { setAnnualQ(r.stock_name); setSelectedCompany({ stock_code: r.stock_code, stock_name: r.stock_name, history: [{ ym: '2023-12', worker_count: r.cnt_2023 }, { ym: '2024-12', worker_count: r.cnt_2024 }, { ym: '2025-12', worker_count: r.cnt_2025 }].filter(h => h.worker_count != null) }); }}
                      onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                      onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'center', color:'rgba(255,255,255,0.35)', fontSize:'0.73rem' }}>{i+1}</td>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', fontWeight:600 }}>
                        {r.market && <span style={{ fontSize:'0.6rem', padding:'0.08rem 0.3rem', borderRadius:'3px', marginRight:'0.3rem',
                          background: (r.market==='유가증권'||r.market==='KOSPI') ? 'rgba(59,130,246,0.18)' : 'rgba(16,185,129,0.18)',
                          color: (r.market==='유가증권'||r.market==='KOSPI') ? '#93c5fd' : '#6ee7b7',
                          border: `1px solid ${(r.market==='유가증권'||r.market==='KOSPI') ? 'rgba(59,130,246,0.3)' : 'rgba(16,185,129,0.3)'}`
                        }}>{(r.market==='유가증권')?'KOSPI':(r.market==='코스닥')?'KOSDAQ':r.market}</span>}
                        {r.stock_name}
                      </td>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', color:'rgba(255,255,255,0.4)', fontSize:'0.74rem' }}>{r.sector||'-'}</td>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontWeight:700, color:'#34d399' }}>{fmtWc(r.cnt_2025)}</td>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', color:'rgba(255,255,255,0.55)' }}>{fmtWc(r.cnt_2024)}</td>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', color:'rgba(255,255,255,0.4)', fontSize:'0.78rem' }}>{fmtWc(r.cnt_2023)}</td>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontWeight:600, color:diffColor2(r.diff_1y) }}>{fmtDiff2(r.diff_1y)}</td>
                      <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontSize:'0.78rem', color:diffColor2(r.diff_2y) }}>{fmtDiff2(r.diff_2y)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!topShowAll && filteredTop.length > 15 && (
                <div style={{ padding:'0.8rem', textAlign:'center', borderTop:'1px solid rgba(255,255,255,0.06)' }}>
                  <button onClick={() => setTopShowAll(true)} style={{ padding:'0.35rem 1.2rem', borderRadius:'7px', fontSize:'0.8rem', cursor:'pointer', background:'rgba(255,255,255,0.07)', color:'rgba(255,255,255,0.6)', border:'1px solid rgba(255,255,255,0.15)' }}>
                    전체 보기 ({filteredTop.length - 15}개 더)
                  </button>
                </div>
              )}
            </div>
            );
          })()}
          <div style={{ padding:'0.5rem 1rem', borderTop:'1px solid rgba(255,255,255,0.05)', fontSize:'0.67rem', color:'rgba(255,255,255,0.28)' }}>
            📋 사업보고서 기준 직접 고용인원 · 행 클릭 시 추이 차트 표시
          </div>
        </div>
      </div>
    );
  };

  // ── 기업별 인원 그래프 (월별 추이) ────────────────────────────
  const CompanyChartView = () => {
    const [input, setInput] = React.useState('');
    const [suggestions, setSuggestions] = React.useState([]);
    const [chartData, setChartData] = React.useState(null);
    const [loading, setLoading] = React.useState(false);
    const [notFound, setNotFound] = React.useState(false);

    const doSearch = async (codeOrName) => {
      const raw = (codeOrName || input).trim();
      if (!raw) return;
      setLoading(true); setNotFound(false); setSuggestions([]);

      // 6자리 숫자면 코드로 직접 조회, 아니면 종목명 검색 먼저
      let code = raw;
      if (!/^\d{6}$/.test(raw)) {
        try {
          const rs = await fetch(`/api/search?q=${encodeURIComponent(raw)}`).then(r => r.json());
          const items = rs.results || rs || [];
          if (items.length === 0) { setNotFound(true); setLoading(false); return; }
          if (items.length > 1) { setSuggestions(items.slice(0, 8)); setLoading(false); return; }
          code = items[0].stock_code || items[0].code;
        } catch { setNotFound(true); setLoading(false); return; }
      }

      try {
        const d = await fetch(`/api/employment-v2/insurance/chart?code=${code}`).then(r => r.json());
        if (d.notFound || !d.history?.length) { setNotFound(true); setChartData(null); }
        else { setChartData(d); }
      } catch { setNotFound(true); }
      setLoading(false);
    };

    const maxW = chartData ? Math.max(...chartData.history.map(h => h.worker_count || 0)) : 1;

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '1rem 1.4rem', display: 'flex', gap: '0.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>📈 기업별 인원 월별 추이</span>
          <input value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            placeholder="종목명 또는 종목코드 입력"
            style={{ padding: '0.35rem 0.8rem', borderRadius: '7px', border: '1px solid rgba(255,255,255,0.18)', background: 'rgba(255,255,255,0.07)', color: '#fff', fontSize: '0.82rem', width: '220px' }} />
          <button onClick={() => doSearch()} style={{ padding: '0.35rem 1rem', borderRadius: '7px', background: 'rgba(45,212,191,0.2)', border: '1px solid var(--accent-mint)', color: 'var(--accent-mint)', fontSize: '0.82rem', cursor: 'pointer' }}>조회</button>
        </div>
        <div className="glass-panel" style={{ padding: '0.55rem 1rem', fontSize: '0.72rem', color: '#fbbf24', background: 'rgba(251,191,36,0.08)' }}>
          안내: 피보험자 수는 특수고용직/사업장 집계를 포함할 수 있어 사업보고서 인원보다 크게 보일 수 있습니다.
        </div>
        {suggestions.length > 0 && (
          <div className="glass-panel" style={{ padding: '0.8rem 1.2rem' }}>
            <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>종목을 선택하세요:</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
              {suggestions.map(s => (
                <button key={s.stock_code || s.code} onClick={() => { setInput(s.stock_name || s.name); doSearch(s.stock_code || s.code); }}
                  style={{ padding: '0.3rem 0.7rem', borderRadius: '6px', fontSize: '0.78rem', cursor: 'pointer', background: 'rgba(45,212,191,0.12)', border: '1px solid rgba(45,212,191,0.3)', color: '#fff' }}>
                  {s.stock_code || s.code} {s.stock_name || s.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {loading && <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>로딩 중...</div>}
        {notFound && <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>수집된 데이터가 없습니다.</div>}

        {chartData && (() => {
          const hist = chartData.history || [];
          const lineData = hist.map(h => ({
            ym: h.ym,
            label: h.ym ? h.ym.replace(/(\d{4})(\d{2})/, '$1-$2') : '',
            workers: h.worker_count != null ? h.worker_count : null,
            workersActual: h.is_actual ? h.worker_count : null,
            workersEst: !h.is_actual ? h.worker_count : null,
            netChange: h.net_change,
            newHires: h.new_hires,
            terminations: h.terminations,
            isActual: h.is_actual,
          }));
          const allW = lineData.map(d => d.workers).filter(v => v != null);
          const minV = allW.length ? Math.min(...allW) : 0;
          const maxV = allW.length ? Math.max(...allW) : 100;
          const pad = Math.max((maxV - minV) * 0.1, 10);
          return (
            <div className="glass-panel" style={{ padding: '1.2rem' }}>
              <div style={{ fontWeight: 700, fontSize: '0.92rem', marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.8rem', flexWrap: 'wrap' }}>
                <span>{chartData.stock_code} · {chartData.stock_name} 피보험자 추이 (25.05~26.05)</span>
                <span style={{ fontSize: '0.78rem', color: '#c4b5fd', fontWeight: 600 }}>
                  기준 인원(사업보고서 12월): {chartData.report_workers != null ? chartData.report_workers.toLocaleString('ko-KR') : '-'}명
                </span>
                <span style={{ display: 'flex', gap: '0.5rem', fontSize: '0.73rem', fontWeight: 400 }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                    <span style={{ display: 'inline-block', width: 20, height: 2, background: '#2dd4bf' }}></span>
                    <span style={{ color: '#2dd4bf' }}>고용보험 실측</span>
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                    <span style={{ display: 'inline-block', width: 20, height: 2, background: '#a78bfa', borderTop: '2px dashed #a78bfa' }}></span>
                    <span style={{ color: '#a78bfa' }}>NPS 증감기반 추정치</span>
                  </span>
                </span>
              </div>
              <ResponsiveContainer width="100%" height={270}>
                <LineChart data={lineData} margin={{ top: 10, right: 20, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)" />
                  <XAxis dataKey="label" tick={{ fill: 'rgba(255,255,255,0.45)', fontSize: 11 }} interval={0} angle={-30} textAnchor="end" height={40} />
                  <YAxis domain={[Math.max(0, minV - pad), maxV + pad]}
                    tickFormatter={v => v >= 10000 ? (v/10000).toFixed(1)+'만' : v.toLocaleString('ko-KR')}
                    tick={{ fill: 'rgba(255,255,255,0.45)', fontSize: 11 }} width={65} />
                  <Tooltip
                    contentStyle={{ background: 'rgba(15,15,25,0.95)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: '8px', fontSize: '0.8rem' }}
                    formatter={(v, name) => {
                      if (v == null) return ['-', ''];
                      const label = name === 'workersActual' ? '피보험자(실측)' : '피보험자(추정)';
                      return [v.toLocaleString('ko-KR') + '명', label];
                    }}
                    labelFormatter={l => `기준: ${l}`}
                  />
                  <Line type="monotone" dataKey="workersActual" stroke="#2dd4bf" strokeWidth={2.5} connectNulls={false}
                    dot={props => {
                      const { cx, cy, payload } = props;
                      if (payload.workersActual == null) return null;
                      return <circle key={payload.ym} cx={cx} cy={cy} r={5} fill="#2dd4bf" stroke="#fff" strokeWidth={1.5} />;
                    }}
                    activeDot={{ r: 6, fill: '#fff', stroke: '#2dd4bf', strokeWidth: 2 }} />
                  <Line type="monotone" dataKey="workersEst" stroke="#a78bfa" strokeWidth={2} strokeDasharray="5 3" connectNulls={false}
                    dot={props => {
                      const { cx, cy, payload } = props;
                      if (payload.workersEst == null) return null;
                      return <circle key={payload.ym} cx={cx} cy={cy} r={3} fill="#a78bfa" strokeWidth={0} />;
                    }}
                    activeDot={{ r: 5, fill: '#fff', stroke: '#a78bfa', strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
              <div style={{ overflowX: 'auto', marginTop: '0.8rem' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                  <thead><tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)' }}>
                    {['기준월', '피보험자', '신규취득(국연)', '상실(국연)', '순증감', '출처'].map(h => (
                      <th key={h} style={{ padding: '0.3rem 0.6rem', textAlign: h === '기준월' || h === '출처' ? 'left' : 'right', color: 'rgba(255,255,255,0.5)', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {[...lineData].reverse().map(h => (
                      <tr key={h.ym} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', background: h.isActual ? 'rgba(45,212,191,0.05)' : 'transparent' }}>
                        <td style={{ padding: '0.28rem 0.6rem', color: h.isActual ? '#2dd4bf' : 'rgba(255,255,255,0.6)', fontWeight: h.isActual ? 600 : 400 }}>{h.label}</td>
                        <td style={{ padding: '0.28rem 0.6rem', textAlign: 'right', fontWeight: 600, color: h.isActual ? '#34d399' : '#a78bfa' }}>
                          {h.workers != null ? h.workers.toLocaleString('ko-KR') + '명' : '-'}
                          {!h.isActual && <span style={{ fontSize: '0.7rem', color: 'rgba(167,139,250,0.6)', marginLeft: '0.2rem' }}>(추정)</span>}
                        </td>
                        <td style={{ padding: '0.28rem 0.6rem', textAlign: 'right', color: 'rgba(255,255,255,0.55)' }}>
                          {h.newHires != null ? h.newHires.toLocaleString('ko-KR') : '-'}
                        </td>
                        <td style={{ padding: '0.28rem 0.6rem', textAlign: 'right', color: 'rgba(255,255,255,0.55)' }}>
                          {h.terminations != null ? h.terminations.toLocaleString('ko-KR') : '-'}
                        </td>
                        <td style={{ padding: '0.28rem 0.6rem', textAlign: 'right', color: h.netChange > 0 ? '#f87171' : h.netChange < 0 ? '#60a5fa' : 'rgba(255,255,255,0.35)' }}>
                          {h.netChange != null ? (h.netChange > 0 ? '+' : '') + h.netChange.toLocaleString('ko-KR') : '-'}
                        </td>
                        <td style={{ padding: '0.28rem 0.6rem', color: h.isActual ? '#2dd4bf' : '#a78bfa', fontSize: '0.72rem' }}>
                          {h.isActual ? '고용보험(실측)' : 'NPS 증감기반 추정'}
                        </td>
                      </tr>
                    ))}
                    <tr>
                      <td colSpan={6} style={{ padding: '0.55rem 0.65rem', color: 'rgba(255,255,255,0.62)', fontSize: '0.72rem', lineHeight: 1.45, borderTop: '1px dashed rgba(255,255,255,0.14)' }}>
                        ※ 설명: 보라색 구간은 국민연금의 월별 취득·상실(증감) 데이터를 이용한 추정치입니다.
                        기준 인원은 사업보고서 12월 값이며, 해당 기준점에서 월별 NPS 순증감을 가감해 월별 흐름을 추정합니다
                        (국민연금 절대 인원으로 직접 표기한 값이 아닙니다).
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          );
        })()}
      </div>
    );
  };

  const EmploymentView = () => {
    const [empTab, setEmpTab] = React.useState('company');

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '0.8rem 1.4rem', display: 'flex', alignItems: 'center', gap: '0.5rem', overflowX: 'auto', flexShrink: 0 }}>
          {[['company','🏭 국민연금 현황'],['nps_trend','👥 기업별 국민연금'],['company_chart','📈 기업별 그래프'],['nps','📋 사업보고서 인원']].map(([k, lbl]) => (
            <button key={k} onClick={() => setEmpTab(k)} style={{
              padding: '0.35rem 0.9rem', borderRadius: '7px', fontSize: '0.82rem', cursor: 'pointer',
              fontWeight: empTab === k ? 700 : 500,
              border: `1px solid ${empTab===k ? 'var(--accent-mint)' : 'var(--glass-border)'}`,
              background: empTab === k ? 'rgba(45,212,191,0.15)' : 'transparent',
              color: empTab === k ? 'var(--accent-mint)' : 'var(--text-secondary)',
            }}>{lbl}</button>
          ))}
        </div>

        <div className="glass-panel" style={{
          padding: '0.75rem 1rem',
          border: '1px solid rgba(245,158,11,0.35)',
          background: 'rgba(245,158,11,0.08)',
          fontSize: '0.76rem',
          lineHeight: 1.55,
          color: 'rgba(255,255,255,0.88)'
        }}>
          <div style={{ fontWeight: 700, color: '#fbbf24', marginBottom: '0.22rem' }}>고용정보 해석 한계점</div>
          <div>1) 사업보고서 인원은 연말 직접고용 기준이며, WLB/NPS는 사업장·피보험자 기준으로 집계되어 모수가 다를 수 있습니다.</div>
          <div>2) NPS는 월별 취득/상실(증감) 흐름 데이터로, 절대 인원 1:1 비교 지표가 아닙니다.</div>
          <div>3) 보험/건설/유통 등 일부 업종은 특고·사업장 구조 영향으로 괴리가 크게 발생할 수 있어 추세 중심으로 해석해야 합니다.</div>
        </div>

        {empTab === 'company' && <EmploymentYearlyView />}
        {empTab === 'nps_trend' && <NpsTrendView />}
        {empTab === 'company_chart' && <CompanyChartView />}
        {empTab === 'nps' && <AnnualEmploymentByReportView />}
        {false && (() => {
          // ── [DISABLED: hooks-in-conditional 위반 방지, AnnualEmploymentByReportView로 이동] ──
          const [annualQ, setAnnualQ]           = React.useState('');
          const [annualResults, setAnnualResults] = React.useState([]);
          const [annualLoading, setAnnualLoading] = React.useState(false);
          const [selectedCompany, setSelectedCompany] = React.useState(null);
          const [years, setYears]               = React.useState('3');   // 1/2/3
          const [topRows, setTopRows]           = React.useState([]);
          const [topLoading, setTopLoading]     = React.useState(true);
          const [topSort, setTopSort]           = React.useState('latest');

          // 상위 기업 목록 로드
          React.useEffect(() => {
            setTopLoading(true);
            fetch(`/api/employment-v2/annual-top?limit=200&sort_by=${topSort}`)
              .then(r => r.json())
              .then(d => { setTopRows(d.rows || []); setTopLoading(false); })
              .catch(() => setTopLoading(false));
          }, [topSort]);

          // 기업 검색
          const searchAnnual = async () => {
            if (!annualQ.trim()) return;
            setAnnualLoading(true);
            try {
              const d = await fetch(`/api/employment-v2/annual-trend?q=${encodeURIComponent(annualQ)}`).then(r => r.json());
              setAnnualResults(d.results || []);
              if (d.results?.length === 1) setSelectedCompany(d.results[0]);
              else setSelectedCompany(null);
            } catch {}
            setAnnualLoading(false);
          };

          // 기간 필터링
          const filterHistory = (history) => {
            if (!history) return [];
            const cutYear = years === '1' ? '2025' : years === '2' ? '2024' : '2023';
            return history.filter(h => h.ym >= cutYear);
          };

          const YM_COLORS = ['#60a5fa','#34d399','#f59e0b','#f87171','#a78bfa'];

          const fmtWc = (n) => n != null ? n.toLocaleString('ko-KR') + '명' : '-';
          const diffColor2 = (v) => v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : 'rgba(255,255,255,0.4)';
          const fmtDiff2 = (v) => v != null ? (v > 0 ? '+' : '') + v.toLocaleString('ko-KR') : '-';

          return (
            <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              {/* 안내 배너 */}
              <div className="glass-panel" style={{ padding: '0.7rem 1.2rem', display: 'flex', gap: '1.5rem', flexWrap: 'wrap', fontSize: '0.78rem', alignItems: 'center' }}>
                <span>📊 기업별 고용인원 연간 추이 — <strong style={{color:'#34d399'}}>사업보고서</strong> 기준 (2023~2025년 연말 기준)</span>
                <span style={{color:'var(--text-secondary)'}}>• 직접 고용인원만 집계 (자회사 제외)</span>
                <span style={{color:'var(--text-secondary)'}}>• 482개 상장기업 대상</span>
                <span style={{color:'#f59e0b', marginLeft:'auto'}}>⚠️ 국민연금과 다른 기준의 사업보고서 데이터</span>
              </div>

              {/* 전체 기업 연간 랭킹 테이블 */}
              <div className="glass-panel" style={{ overflow: 'hidden' }}>
                <div style={{ padding: '0.8rem 1.2rem', borderBottom: '1px solid rgba(255,255,255,0.07)', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600, color: '#fff', fontSize: '0.88rem' }}>📋 전체 기업 연간 인원 현황 (사업보고서 기준, 2025-12)</span>
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
                    {[['latest','최신인원순'],['growth','1년 증가순'],['name','이름순']].map(([k,l]) => (
                      <button key={k} onClick={() => setTopSort(k)} style={{
                        padding: '0.22rem 0.6rem', borderRadius: '5px', fontSize: '0.75rem', cursor: 'pointer',
                        fontWeight: topSort === k ? 700 : 400,
                        background: topSort === k ? 'rgba(45,212,191,0.15)' : 'transparent',
                        color: topSort === k ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
                        border: `1px solid ${topSort === k ? '#2dd4bf' : 'rgba(255,255,255,0.15)'}`,
                      }}>{l}</button>
                    ))}
                  </div>
                </div>
                {topLoading ? (
                  <div style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>로딩 중...</div>
                ) : (
                  <div style={{ overflowX: 'auto', overflowY: 'clip' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.81rem' }}>
                      <thead>
                        <tr>
                          {['#','종목명','섹터','2025년말','2024년말','2023년말','1년 증감','2년 증감'].map((h,i) => (
                            <th key={i} style={{
                              padding: '0.55rem 0.8rem', textAlign: i <= 2 ? 'left' : 'right',
                              color: '#e2e8f0', borderBottom: '2px solid rgba(59,130,246,0.5)',
                              fontWeight: 600, background: 'rgba(30,58,138,0.4)',
                              whiteSpace: 'nowrap', position: 'sticky', top: 0, zIndex: 5,
                            }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {topRows.map((r, i) => (
                          <tr key={r.stock_code}
                            style={{ cursor: 'pointer', transition: 'background 0.12s' }}
                            onClick={() => { setAnnualQ(r.stock_name); setSelectedCompany({ stock_code: r.stock_code, stock_name: r.stock_name, history: [{ ym: '2023-12', worker_count: r.cnt_2023 }, { ym: '2024-12', worker_count: r.cnt_2024 }, { ym: '2025-12', worker_count: r.cnt_2025 }].filter(h => h.worker_count != null) }); }}
                            onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                            onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'center', color:'rgba(255,255,255,0.35)', fontSize:'0.73rem' }}>{i+1}</td>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', fontWeight:600 }}>
                              {r.market && <span style={{ fontSize:'0.6rem', padding:'0.08rem 0.3rem', borderRadius:'3px', marginRight:'0.3rem', background: (r.market==='유가증권'||r.market==='KOSPI') ? 'rgba(59,130,246,0.18)' : 'rgba(16,185,129,0.18)', color: (r.market==='유가증권'||r.market==='KOSPI') ? '#93c5fd' : '#6ee7b7', border: `1px solid ${(r.market==='유가증권'||r.market==='KOSPI') ? 'rgba(59,130,246,0.3)' : 'rgba(16,185,129,0.3)'}` }}>{(r.market==='유가증권')?'KOSPI':(r.market==='코스닥')?'KOSDAQ':r.market}</span>}
                              {r.stock_name}
                            </td>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', color:'rgba(255,255,255,0.4)', fontSize:'0.74rem' }}>{r.sector||'-'}</td>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontWeight:700, color:'#34d399' }}>{fmtWc(r.cnt_2025)}</td>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', color:'rgba(255,255,255,0.55)' }}>{fmtWc(r.cnt_2024)}</td>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', color:'rgba(255,255,255,0.4)', fontSize:'0.78rem' }}>{fmtWc(r.cnt_2023)}</td>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontWeight:600, color:diffColor2(r.diff_1y) }}>{fmtDiff2(r.diff_1y)}</td>
                            <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontSize:'0.78rem', color:diffColor2(r.diff_2y) }}>{fmtDiff2(r.diff_2y)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div style={{ padding:'0.5rem 1rem', borderTop:'1px solid rgba(255,255,255,0.05)', fontSize:'0.67rem', color:'rgba(255,255,255,0.28)' }}>
                  📋 사업보고서 기준 직접 고용인원 · 행 클릭 시 추이 차트 표시
                </div>
              </div>
            </div>
          );
        })()}

      </div>
    );
  };

  // ── 시스템 상태 ──────────────────────────────────────────────
  const SystemStatus = () => {
    const fr = sysStats?.data_freshness || {};
    const fmtYm = (ym) => ym ? `${ym.slice(0,4)}.${ym.slice(4,6)}` : '-';

    const DATA_SOURCES = [
      { category: '주가 (한국)', source: 'KIS API', data: '일별 OHLCV (전 종목)', schedule: '장중 1분마다 (09:00-15:30)', table: 'price_history', status: '수집 중', color: '#34d399', freshness: fr.kr_price_latest ? `최신: ${fr.kr_price_latest}` : null },
      { category: '주가 (한국)', source: 'KRX API', data: '전 종목 일별 OHLCV + 지수(코스피/코스닥/200/150)', schedule: '18:00 (영업일)', table: 'price_history', status: '수집 중', color: '#34d399' },
      { category: '주가 (한국)', source: 'Yahoo Finance', data: '해외지수(NASDAQ, S&P500, DOW)', schedule: '00:10 (nightly)', table: 'price_history', status: '수집 중', color: '#34d399' },
      { category: '주가 (미국)', source: 'yfinance', data: 'S&P500+NASDAQ-100 516종목 일별 OHLCV 5년치', schedule: '매일 자동 갱신', table: 'us_price_history (us_market.db)', status: '수집 중', color: '#34d399', freshness: fr.us_price_latest ? `최신: ${fr.us_price_latest} · ${fr.us_stock_count ?? '-'}종목` : null },
      { category: '수급', source: 'KIS API', data: '기관/외국인/개인 순매수(금액·수량)', schedule: '장중 5분마다 + 17:30(보완)', table: 'price_history (inst/frn/ind_net_buy)', status: '수집 중', color: '#34d399' },
      { category: '수급', source: '네이버금융 스크래핑', data: '3~20년치 기관/외국인 순매수 (백필)', schedule: '1회성 수집(collect_naver_investor.py)', table: 'price_history', status: '완료', color: '#60a5fa' },
      { category: '기업정보', source: 'KIS API', data: '종목 마스터 (섹터, 시총, PER, PBR, ROE)', schedule: '매월 1일 03:00', table: 'stock_universe', status: '수집 중', color: '#34d399' },
      { category: '재무제표', source: 'DART API', data: '분기/연간 재무제표 (매출, 영업이익, 당기순이익, EPS)', schedule: '03:30 (공시 확인 후)', table: 'financial_data', status: '수집 중', color: '#34d399' },
      { category: '재무제표', source: 'DART API', data: '현금흐름표 (전 종목 배치)', schedule: '1회성 배치 수집', table: 'financial_data', status: '완료', color: '#60a5fa' },
      { category: '밸류에이션', source: '네이버금융 스크래핑', data: 'PER, PBR (개별종목 조회 시)', schedule: '요청 시 실시간 + 캐시', table: 'stock_universe (갱신)', status: '수집 중', color: '#34d399' },
      { category: '공시', source: 'DART API', data: '전 종목 공시 목록', schedule: '03:30 (daily)', table: '-', status: '수집 중', color: '#34d399' },
      { category: '대차잔고', source: 'KIS API', data: '대차잔고, 공매도 잔고', schedule: '장중 5분마다', table: 'short_sell_daily', status: '수집 중', color: '#34d399' },
      { category: '해외 주가', source: 'Yahoo Finance', data: '해외 반도체 섹터 종목 주가', schedule: '30분마다 캐시 갱신', table: 'radar_price_cache', status: '수집 중', color: '#34d399' },
      { category: '수출입 (확정)', source: '관세청 수출입무역통계 (KITA)', data: '품목별 월간 확정 수출입 통계 — 전월 확정치', schedule: '월 1회 확정 (hs_trade_lab 배치)', table: 'customs_monthly_record (hs_trade_lab.db)', status: '자동수집', color: '#34d399', freshness: fr.hs_confirmed_latest ? `최신 확정: ${fmtYm(fr.hs_confirmed_latest)}` : null },
      { category: '수출입 (추정)', source: '관세청 10일 단위 잠정치', data: '품목별 10일 단위 수출 잠정 추이 — 당월 추정치', schedule: '10일 단위 (hs_trade_lab 배치)', table: 'trade_series_cache (hs_trade_lab.db)', status: '자동수집', color: '#34d399', freshness: fr.hs_estimated_latest ? `최신 추정: ${fmtYm(fr.hs_estimated_latest)}` : null },
      { category: 'Stock Easy', source: 'StockEasy 사이트 파싱', data: '전략 종목 (가상매매)', schedule: '수동 파싱', table: 'peak_holding', status: '수동', color: '#fbbf24' },
      { category: '국민연금 고용현황', source: '근로복지공단 고용보험 API', data: '상장사 사업장별 피보험자수 + 사업장수 (전체 스캔)', schedule: '매일 20:30 변화감지 → 수집 (~14분)', table: 'wlb_monthly (employment.db)', status: '자동수집', color: '#34d399', freshness: fr.wlb_collected_at ? `수집: ${fr.wlb_collected_at} · 기준: ${fmtYm(fr.wlb_data_ym)}` : null },
      { category: '텔레그램', source: 'Telegram API', data: '채널별 종목 언급 수집', schedule: '수동 수집', table: 'telegram_channels', status: '수동', color: '#fbbf24' },
      { category: '섹터분석', source: '블로그 파싱 (Sector_define)', data: '핫 섹터 분석 포스트 + 종목', schedule: '매일 07:00 자동', table: 'sector_posts / sector_stocks (stock.db)', status: '자동수집', color: '#34d399' },
    ];

    const thSt = { padding:'0.55rem 0.7rem', fontSize:'0.72rem', color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', background:'rgba(0,0,0,0.25)', borderBottom:'1px solid var(--glass-border)', textAlign:'left' };
    const tdSt = { padding:'0.45rem 0.7rem', fontSize:'0.78rem', borderBottom:'1px solid rgba(255,255,255,0.04)', verticalAlign:'top' };

    return (
    <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1.5rem' }}>
      {/* 요약 카드 — 기존 3개 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '1.2rem' }}>
          <p style={{ color: 'var(--text-secondary)', fontSize:'0.75rem' }}>수집된 기업 수</p>
          <h3 style={{ fontSize: '1.8rem', marginTop:'0.3rem' }}>{sysStats?.stock_count ?? '-'} 개</h3>
        </div>
        <div className="glass-panel" style={{ padding: '1.2rem' }}>
          <p style={{ color: 'var(--text-secondary)', fontSize:'0.75rem' }}>총 주가 데이터</p>
          <h3 style={{ fontSize: '1.8rem', marginTop:'0.3rem' }}>{sysStats?.price_records?.toLocaleString() ?? '-'} 건</h3>
        </div>
        <div className="glass-panel" style={{ padding: '1.2rem' }}>
          <p style={{ color: 'var(--text-secondary)', fontSize:'0.75rem' }}>마지막 업데이트</p>
          <h3 style={{ fontSize: '0.95rem', marginTop:'0.5rem', color:'var(--accent-mint)' }}>{sysStats?.last_update ?? '-'}</h3>
        </div>
      </div>

      {/* 데이터 최신성 카드 */}
      <div className="glass-panel" style={{ padding: '1rem' }}>
        <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.8rem' }}>
          <span style={{ fontSize:'1rem' }}>📡</span>
          <span style={{ fontWeight:700, fontSize:'0.9rem' }}>데이터 최신성</span>
          <span style={{ fontSize:'0.72rem', color:'var(--text-secondary)', marginLeft:'auto' }}>각 소스별 최신 수집 기준일</span>
        </div>
        <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(200px, 1fr))', gap:'0.6rem' }}>
          {[
            { label:'🇰🇷 주가 (한국)', val: fr.kr_price_latest || '-', color:'#34d399' },
            { label:'🇺🇸 주가 (미국)', val: fr.us_price_latest ? `${fr.us_price_latest} (${fr.us_stock_count ?? '-'}종목)` : '-', color:'#60a5fa' },
            { label:'📦 수출입 확정치', val: fr.hs_confirmed_latest ? fmtYm(fr.hs_confirmed_latest) : '-', color:'#fbbf24' },
            { label:'📊 수출입 추정치', val: fr.hs_estimated_latest ? fmtYm(fr.hs_estimated_latest) : '-', color:'#f97316' },
            { label:'🏭 국민연금 고용현황', val: fr.wlb_collected_at ? `수집: ${fr.wlb_collected_at}` : '-', color:'#a78bfa' },
            { label:'📅 국민연금 기준월', val: fr.wlb_data_ym ? fmtYm(fr.wlb_data_ym) : '-', color:'#a78bfa' },
          ].map(c => (
            <div key={c.label} style={{ background:'rgba(255,255,255,0.03)', borderRadius:'8px', padding:'0.6rem 0.8rem', border:'1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ fontSize:'0.7rem', color:'var(--text-secondary)', marginBottom:'0.2rem' }}>{c.label}</div>
              <div style={{ fontSize:'0.85rem', fontWeight:700, color: c.color }}>{c.val}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 데이터 소스 테이블 */}
      <div className="glass-panel" style={{ padding:0, overflow:'hidden' }}>
        <div style={{ padding:'0.8rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.5rem' }}>
          <Database size={16} color="var(--accent-purple)" />
          <span style={{ fontWeight:700, fontSize:'0.9rem' }}>데이터 수집 현황</span>
          <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginLeft:'0.5rem' }}>
            <span style={{color:'#34d399'}}>● 자동수집</span>{'  '}
            <span style={{color:'#60a5fa'}}>● 완료(배치)</span>{'  '}
            <span style={{color:'#fbbf24'}}>● 수동/별도</span>
          </span>
        </div>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead>
              <tr>
                <th style={thSt}>카테고리</th>
                <th style={thSt}>출처</th>
                <th style={thSt}>수집 데이터</th>
                <th style={thSt}>주기</th>
                <th style={thSt}>최신 기준</th>
                <th style={thSt}>저장 테이블</th>
                <th style={{...thSt, textAlign:'center'}}>상태</th>
              </tr>
            </thead>
            <tbody>
              {DATA_SOURCES.map((row, i) => (
                <tr key={i} onMouseOver={e=>e.currentTarget.style.background='rgba(255,255,255,0.02)'}
                    onMouseOut={e=>e.currentTarget.style.background='transparent'}>
                  <td style={{...tdSt, fontWeight:700, color:'var(--accent-mint)', whiteSpace:'nowrap'}}>{row.category}</td>
                  <td style={{...tdSt, whiteSpace:'nowrap', fontWeight:600}}>{row.source}</td>
                  <td style={{...tdSt, color:'var(--text-secondary)', fontSize:'0.75rem', maxWidth:'260px'}}>{row.data}</td>
                  <td style={{...tdSt, whiteSpace:'nowrap', fontSize:'0.75rem', color:'var(--text-secondary)'}}>{row.schedule}</td>
                  <td style={{...tdSt, fontSize:'0.7rem', color: row.freshness ? '#fbbf24' : 'rgba(255,255,255,0.25)', whiteSpace:'nowrap'}}>{row.freshness || '-'}</td>
                  <td style={{...tdSt, fontSize:'0.7rem', color:'rgba(255,255,255,0.35)', fontFamily:'monospace'}}>{row.table}</td>
                  <td style={{...tdSt, textAlign:'center'}}>
                    <span style={{ color: row.color, fontSize:'0.75rem', fontWeight:600 }}>● {row.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* DB 경로 */}
      <div className="glass-panel" style={{ padding:'1rem' }}>
        <p style={{ color:'var(--text-secondary)', fontSize:'0.75rem', marginBottom:'0.4rem' }}>메인 DB 경로</p>
        <code style={{ fontSize:'0.9rem', color:'var(--accent-mint)' }}>{sysStats?.db_path || 'stock.db'}</code>
      </div>
    </div>
    );
  };

  // ── 수출경쟁력 ──────────────────────────────────────────────
  const ExportHealthView = () => {
    const [data, setData]           = React.useState(null);
    const [loading, setLoading]     = React.useState(true);
    const [viewTab, setViewTab]     = React.useState('sector');
    const [selHs, setSelHs]         = React.useState(null);
    const [hsCompData, setHsCompData] = React.useState(null);
    const [hsCompLoading, setHsCompLoading] = React.useState(false);

    React.useEffect(() => {
      fetch('/hs/api/analysis2/export-health')
        .then(r => r.json())
        .then(d => { setData(d); setLoading(false); })
        .catch(() => setLoading(false));
    }, []);

    const loadHsCompanies = async (hsCode) => {
      setSelHs(hsCode);
      setHsCompLoading(true);
      try {
        const r = await fetch(`/hs/api/analysis2/hs/${hsCode}/companies`);
        setHsCompData(await r.json());
      } finally {
        setHsCompLoading(false);
      }
    };

    const healthIcon  = (s) => s === 'good' ? '🟢' : s === 'bad' ? '🔴' : '🟡';
    const healthColor = (s) => s === 'good' ? '#34d399' : s === 'bad' ? '#f87171' : '#fbbf24';
    const fmtPct = (v) => { if (v == null) return '-'; return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`; };

    if (loading) return <div style={{ padding: '2rem', color: 'var(--text-secondary)', textAlign: 'center' }}>데이터 로딩 중...</div>;
    if (!data)   return <div style={{ padding: '2rem', color: '#f87171' }}>수출경쟁력 데이터를 불러오지 못했습니다.</div>;

    const sectors   = data.sectors   || [];
    const companies = data.companies || [];
    const sharedHs  = data.shared_hs || [];
    const sortedComp = [...companies].sort((a, b) => (b.export_yoy ?? -999) - (a.export_yoy ?? -999));
    const gainers = sortedComp.filter(c => c.export_yoy != null).slice(0, 10);
    const losers  = [...sortedComp].reverse().filter(c => c.export_yoy != null).slice(0, 10);

    return (
      <div style={{ padding: '1.5rem', maxWidth: 1200, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem' }}>
          <span style={{ fontSize: '1.5rem' }}>🌐</span>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.3rem', color: 'var(--text-primary)' }}>수출경쟁력 분석</h2>
            <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              관세청 통관 데이터 기반 · 한국 수출입 트렌드 · {data.updated_at || ''}
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: '0.5rem' }}>
          {[['sector','🏭 섹터 건강도'],['company','🏢 기업 순위'],['shared','🔗 공유 HS 코드']].map(([k, lbl]) => (
            <button key={k} onClick={() => setViewTab(k)} style={{
              padding: '0.4rem 1rem', borderRadius: 6, border: 'none', cursor: 'pointer',
              background: viewTab === k ? 'var(--accent-mint)' : 'rgba(255,255,255,0.06)',
              color: viewTab === k ? '#0f172a' : 'var(--text-secondary)', fontWeight: viewTab === k ? 700 : 400,
              fontSize: '0.85rem'
            }}>{lbl}</button>
          ))}
        </div>

        {viewTab === 'sector' && (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '1rem' }}>
              {sectors.map(s => {
                const momColor = v => v == null ? '#888' : v >= 5 ? '#34d399' : v >= -5 ? '#fbbf24' : '#f87171';
                return (
                  <div key={s.sector_key} className="glass-panel" style={{ padding: '1rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                      <span style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--text-primary)' }}>{s.sector_label}</span>
                      <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <span title={`수출 ${s.export_health}`}>{healthIcon(s.export_health)}</span>
                        <span title={`수입 ${s.import_health}`} style={{ opacity: 0.7, fontSize: '0.8rem' }}>수입{healthIcon(s.import_health)}</span>
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', fontSize: '0.8rem' }}>
                      {[['수출 전월비', s.export_mom],['수출 전년비', s.export_yoy],['수입 전월비', s.import_mom],['수입 전년비', s.import_yoy]].map(([lbl,v]) => (
                        <div key={lbl} style={{ background: 'rgba(255,255,255,0.04)', borderRadius: 6, padding: '0.4rem 0.6rem' }}>
                          <div style={{ color: 'var(--text-secondary)' }}>{lbl}</div>
                          <div style={{ color: momColor(v), fontWeight: 600 }}>{fmtPct(v)}</div>
                        </div>
                      ))}
                    </div>
                    {s.monthly_export && s.monthly_export.length > 1 && (() => {
                      const bars = s.monthly_export.slice(-6);
                      const mx = Math.max(...bars.map(b => b.value || 0), 1);
                      const W = 280, H = 36, bw = Math.floor(W / bars.length) - 2;
                      return (
                        <svg width={W} height={H} style={{ marginTop: '0.6rem', display: 'block' }}>
                          {bars.map((b, i) => {
                            const h = Math.max(2, ((b.value || 0) / mx) * (H - 12));
                            const isProv = b.is_provisional;
                            return (
                              <g key={i}>
                                <rect x={i*(bw+2)} y={H-12-h} width={bw} height={h}
                                  fill={isProv ? 'rgba(249,115,22,0.32)' : 'rgba(52,211,153,0.55)'}
                                  stroke={isProv ? '#f97316' : 'none'} strokeDasharray={isProv ? '3,2' : '0'} rx={2} />
                                <text x={i*(bw+2)+bw/2} y={H} textAnchor="middle" fontSize={7} fill="#888">
                                  {(b.period_ym || '').slice(5)}
                                </text>
                              </g>
                            );
                          })}
                        </svg>
                      );
                    })()}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {viewTab === 'company' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
            {[['📈 수출 증가 상위 10', gainers, '#34d399'],['📉 수출 감소 상위 10', losers, '#f87171']].map(([title, list, col]) => (
              <div key={title} className="glass-panel" style={{ padding: '1rem' }}>
                <h4 style={{ margin: '0 0 0.75rem', color: col, fontSize: '0.95rem' }}>{title}</h4>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                      {['기업','전년비','섹터'].map(h => (
                        <th key={h} style={{ padding: '0.3rem 0.4rem', color: 'var(--text-secondary)', textAlign: h === '기업' ? 'left' : 'right', fontWeight: 500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {list.map(c => (
                      <tr key={c.stock_code} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                        <td style={{ padding: '0.35rem 0.4rem' }}>
                          <button onClick={() => { changeStock(c.stock_code); changeTab('analysis'); }}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#93c5fd', fontSize: '0.82rem', padding: 0 }}>
                            {c.stock_name}
                          </button>
                        </td>
                        <td style={{ padding: '0.35rem 0.4rem', textAlign: 'right', color: col, fontWeight: 600 }}>{fmtPct(c.export_yoy)}</td>
                        <td style={{ padding: '0.35rem 0.4rem', textAlign: 'right', color: 'var(--text-secondary)' }}>{c.sector_label || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
            <div className="glass-panel" style={{ padding: '1rem', gridColumn: '1 / -1' }}>
              <h4 style={{ margin: '0 0 0.75rem', color: 'var(--text-primary)', fontSize: '0.95rem' }}>📊 전체 기업 수출입 현황</h4>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                      {['기업명','섹터','수출 전월비','수출 전년비','수입 전월비','수입 전년비','수출','수입'].map(h => (
                        <th key={h} style={{ padding: '0.35rem 0.5rem', color: 'var(--text-secondary)', fontWeight: 500, textAlign: h.startsWith('기') || h === '섹터' ? 'left' : 'right', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {companies.map(c => {
                      const mc = v => v == null ? '#888' : v >= 5 ? '#34d399' : v >= -5 ? '#fbbf24' : '#f87171';
                      return (
                        <tr key={c.stock_code} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                          <td style={{ padding: '0.3rem 0.5rem' }}>
                            <button onClick={() => { changeStock(c.stock_code); changeTab('analysis'); }}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#93c5fd', fontSize: '0.8rem', padding: 0 }}>
                              {c.stock_name}
                            </button>
                          </td>
                          <td style={{ padding: '0.3rem 0.5rem', color: 'var(--text-secondary)' }}>{c.sector_label || '-'}</td>
                          <td style={{ padding: '0.3rem 0.5rem', textAlign: 'right', color: mc(c.export_mom) }}>{fmtPct(c.export_mom)}</td>
                          <td style={{ padding: '0.3rem 0.5rem', textAlign: 'right', color: mc(c.export_yoy) }}>{fmtPct(c.export_yoy)}</td>
                          <td style={{ padding: '0.3rem 0.5rem', textAlign: 'right', color: mc(c.import_mom) }}>{fmtPct(c.import_mom)}</td>
                          <td style={{ padding: '0.3rem 0.5rem', textAlign: 'right', color: mc(c.import_yoy) }}>{fmtPct(c.import_yoy)}</td>
                          <td style={{ padding: '0.3rem 0.5rem', textAlign: 'right' }}>{healthIcon(c.export_health)}</td>
                          <td style={{ padding: '0.3rem 0.5rem', textAlign: 'right' }}>{healthIcon(c.import_health)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {viewTab === 'shared' && (
          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: '1rem' }}>
            <div className="glass-panel" style={{ padding: '1rem', height: 'fit-content' }}>
              <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.9rem', color: 'var(--text-secondary)' }}>다수 기업 공유 HS 코드</h4>
              {sharedHs.map(h => (
                <div key={h.hs_code} onClick={() => loadHsCompanies(h.hs_code)}
                  style={{
                    padding: '0.5rem 0.6rem', borderRadius: 6, marginBottom: 4, cursor: 'pointer',
                    background: selHs === h.hs_code ? 'rgba(52,211,153,0.15)' : 'rgba(255,255,255,0.04)',
                    border: selHs === h.hs_code ? '1px solid #34d399' : '1px solid transparent'
                  }}>
                  <div style={{ fontWeight: 600, fontSize: '0.82rem', color: 'var(--text-primary)' }}>{h.hs_name || h.hs_code}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 2 }}>
                    {h.hs_code} · {h.company_count}개 기업
                  </div>
                </div>
              ))}
              {sharedHs.length === 0 && <div style={{ color: 'var(--text-secondary)', fontSize: '0.82rem' }}>공유 HS 코드 없음</div>}
            </div>
            <div className="glass-panel" style={{ padding: '1rem' }}>
              {!selHs && <div style={{ color: 'var(--text-secondary)', padding: '2rem', textAlign: 'center' }}>왼쪽에서 HS 코드를 선택하세요</div>}
              {hsCompLoading && <div style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '2rem' }}>로딩 중...</div>}
              {selHs && hsCompData && !hsCompLoading && (() => {
                const comps = hsCompData.companies || [];
                const hsInfo = hsCompData.hs_info || {};
                const totalShare = comps.reduce((s, c) => s + (c.market_share_pct || 0), 0);
                const colors = ['#34d399','#60a5fa','#f59e0b','#f87171','#a78bfa','#fb923c'];
                return (
                  <div>
                    <div style={{ marginBottom: '1rem' }}>
                      <div style={{ fontWeight: 700, fontSize: '1rem', color: 'var(--text-primary)' }}>{hsInfo.hs_name || selHs}</div>
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 2 }}>HS {selHs} · {comps.length}개 기업</div>
                    </div>
                    <div style={{ height: 28, borderRadius: 6, overflow: 'hidden', display: 'flex', background: 'rgba(255,255,255,0.06)', marginBottom: '0.5rem' }}>
                      {comps.map((c, i) => {
                        const pct = totalShare > 0 ? ((c.market_share_pct || 0) / totalShare) * 100 : (100 / comps.length);
                        return (
                          <div key={c.stock_code} title={`${c.stock_name}: ${pct.toFixed(1)}%`}
                            style={{ width: `${pct}%`, background: colors[i % colors.length], display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.72rem', fontWeight: 700, color: '#0f172a', overflow: 'hidden', whiteSpace: 'nowrap' }}>
                            {pct > 8 ? c.stock_name : ''}
                          </div>
                        );
                      })}
                    </div>
                    <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
                      {comps.map((c, i) => {
                        const pct = totalShare > 0 ? ((c.market_share_pct || 0) / totalShare) * 100 : (100 / comps.length);
                        return (
                          <div key={c.stock_code} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.78rem' }}>
                            <div style={{ width: 10, height: 10, borderRadius: 2, background: colors[i % colors.length] }} />
                            <span style={{ color: 'var(--text-primary)' }}>{c.stock_name}</span>
                            <span style={{ color: 'var(--text-secondary)' }}>{pct.toFixed(1)}%</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
            </div>
          </div>
        )}

        <div style={{ marginTop: '1.5rem', padding: '0.75rem 1rem', background: 'rgba(255,255,255,0.03)', borderRadius: 8, borderLeft: '3px solid rgba(52,211,153,0.4)', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--text-primary)' }}>데이터 방법론</strong> · 관세청 통관 HS 코드 → 텔레그램 검증 매핑 → 기업별 시장비율 분배(삼성/SK하이닉스 메모리 TrendForce 2024 기준, 그 외 균등 분할) ·
          건강도 기준: 전월비 ≥+5% 또는 전년비 ≥+10% → 🟢, 전월비 ≤-5% 또는 전년비 ≤-10% → 🔴, 그 외 → 🟡
        </div>
      </div>
    );
  };

  // ── 메인 렌더 ────────────────────────────────────────────────
  const [sidebarOpen, setSidebarOpen] = React.useState(() => window.innerWidth >= 768);

  const NAV_ITEMS = [
    // ── 상단 섹션 (시황) ───────────────────────────
    { key: 'macro',            icon: <LayoutDashboard size={17} />,                            label: '주요 지표' },
    { key: 'analysis',         icon: <BarChart3 size={17} />,                                 label: '개별 종목' },
    { key: 'stock_rs',         icon: <BarChart3 size={17} style={{color:'#a78bfa'}} />,        label: '종합 RS' },
    { key: 'market_radar',     icon: <span style={{fontSize:'14px',lineHeight:1}}>🛰</span>,   label: '섹터 지표' },
    { key: 'semiconductor_sector', icon: <Cpu size={17} style={{color:'#60a5fa'}} />,         label: '반도체 섹터' },
    { key: 'hot_sector',       icon: <span style={{fontSize:'14px',lineHeight:1}}>🎯</span>,   label: 'Hot 섹터' },
    { key: 'market_indicators',icon: <Globe size={17} style={{color:'#fbbf24'}} />,           label: '수급 현황' },
    null,
    // ── 중간 섹션 (발굴/매매) ──────────────────────
    { key: 'screener',         icon: <Cpu size={17} style={{color:'#2dd4bf'}} />,              label: 'AI 종목 발굴' },
    { key: 'tenbagger',        icon: <span style={{fontSize:'14px',lineHeight:1}}>💎</span>,   label: '텐버거 헌터' },
    { key: 'dart_contracts',   icon: <span style={{fontSize:'14px',lineHeight:1}}>📋</span>,   label: '수주공시 알림' },
    { key: 'megatrend',        icon: <span style={{fontSize:'14px',lineHeight:1}}>🚀</span>,   label: '대세 종목 발굴' },
    { key: 'trend',            icon: <TrendingUp size={17} style={{color:'#a78bfa'}} />,       label: '가상 매매' },
    { key: 'reports',          icon: <Newspaper size={17} style={{color:'#34d399'}} />,        label: '섹터 보고서' },
    { key: 'telegram',         icon: <Send size={17} style={{color:'#38bdf8'}} />,             label: '텔레그램 종목' },
    { key: 'backtest',         icon: <FlaskConical size={17} style={{color:'#f59e0b'}} />,     label: '백테스트' },
    { key: 'hs_trade2',        icon: <Ship size={17} style={{color:'#93c5fd'}} />,             label: '수출입분석' },
    { key: 'export_health',    icon: <Globe size={17} style={{color:'#34d399'}} />,            label: '🌐 수출경쟁력' },
    { key: 'employment',       icon: <Users size={17} style={{color:'#86efac'}} />,            label: '고용 정보' },
    { key: 'etf_check',        icon: <span style={{fontSize:'14px',lineHeight:1}}>📊</span>,   label: 'ETF 모니터링' },
    null,
    // ── 하단 섹션 (포트폴리오) ─────────────────────
    { key: 'buy_candidates',   icon: <Target size={17} style={{color:'#f59e0b'}} />,           label: '매수후보' },
    { key: 'portfolio',        icon: <Wallet size={17} style={{color:'#c084fc'}} />,           label: '계좌현황 🔒' },
    null,
    { key: 'settings',         icon: <Settings size={17} style={{color:'#94a3b8'}} />,         label: '⚙ 설정' },
  ];

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      {isMobile && sidebarOpen && (
        <div onClick={()=>setSidebarOpen(false)}
          style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.5)',zIndex:19}}/>
      )}
      {isMobile && (
        <button onClick={()=>setSidebarOpen(v=>!v)} style={{
          position:'fixed',top:'0.75rem',left:'0.75rem',zIndex:25,
          background:'var(--bg-dark)',border:'1px solid var(--glass-border)',
          borderRadius:'8px',padding:'0.4rem 0.6rem',cursor:'pointer',
          color:'var(--text-primary)',fontSize:'1.2rem',lineHeight:1,
        }}>☰</button>
      )}
      <aside
        style={{
          width: sidebarOpen?'210px':(isMobile?'0':'50px'),
          minWidth: sidebarOpen?'210px':(isMobile?'0':'50px'),
          background:'var(--bg-dark)',
          borderRight: sidebarOpen?'1px solid var(--glass-border)':'none',
          display:'flex',flexDirection:'column',
          padding: sidebarOpen?'1.2rem 0.5rem':'0',
          transition:'width 0.25s ease,min-width 0.25s ease,padding 0.25s ease',
          overflowX:'hidden', overflowY:'auto', flexShrink:0, zIndex:20,
          ...(isMobile?{position:'fixed',top:0,left:0,height:'100vh'}:{}),
        }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1.8rem', paddingLeft: '0.3rem', whiteSpace: 'nowrap' }}>
          <Activity color="var(--accent-mint)" size={22} style={{ flexShrink: 0 }} />
          {sidebarOpen && <h1 className="neon-text" style={{ fontSize: '1rem', fontWeight: 800 }}>주식분석</h1>}
        </div>
        <nav style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.2rem', minHeight: 0, overflowY: 'auto', paddingRight: sidebarOpen ? '0.15rem' : 0 }}>
          {NAV_ITEMS.map((item, i) =>
            item === null ? (
              <div key={`div-${i}`} style={{ margin: '0.5rem 0', borderTop: '1px solid var(--glass-border)' }} />
            ) : (
              <button key={item.key} onClick={() => {
                if (item.key === 'portfolio' && !portfolioAuth) {
                  const pw = window.prompt('계좌현황 비밀번호를 입력하세요:');
                  if (pw === '5133') { setPortfolioAuth(true); changeTab(item.key); }
                  else if (pw !== null) window.alert('비밀번호가 틀렸습니다.');
                } else { changeTab(item.key); }
              }}
                className={`nav-item ${activeTab === item.key ? 'active' : ''}`}
                title={item.label}
                style={{ justifyContent: 'flex-start', padding: '0.5rem 0.6rem', whiteSpace: 'nowrap', overflow: 'hidden' }}>
                <span style={{ flexShrink: 0, display: 'flex' }}>{item.icon}</span>
                {sidebarOpen && <span style={{ marginLeft: '0.6rem' }}>{item.label}</span>}
              </button>
            )
          )}
        </nav>
        {sidebarOpen && (
          <div style={{ padding: '0.6rem', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', fontSize: '0.72rem', marginTop: '0.5rem', whiteSpace: 'nowrap' }}>
            <p style={{ color: 'var(--text-secondary)' }}>서버 상태</p>
            <p style={{ color: 'var(--accent-mint)', fontWeight: 700 }}>● Operational</p>
          </div>
        )}
      </aside>

      {/* Main */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <header style={{height:'52px',borderBottom:'1px solid var(--glass-border)',
          display:'flex',alignItems:'center',justifyContent:'space-between',
          padding:isMobile?'0 0.75rem 0 3.5rem':'0 2rem',flexShrink:0}}>
          <h2 style={{fontSize:isMobile?'0.9rem':'1rem',fontWeight:600}}>{TAB_TITLES[activeTab]}</h2>
          <form onSubmit={handleSearch} style={{position:'relative',width:isMobile?'160px':'300px'}}>
            <input type="text" placeholder={isMobile?'검색...':'종목명/코드 검색...'}
              value={searchQuery}
              onChange={e=>setSearchQuery(e.target.value)}
              onBlur={()=>setTimeout(()=>setShowSearchDrop(false),150)}
              onFocus={()=>{ if(searchResults.length>0) setShowSearchDrop(true); }}
              style={{width:'100%',padding:'0.4rem 0.8rem 0.4rem 2rem',borderRadius:'8px',
                background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',
                color:'#fff',fontSize:isMobile?'0.85rem':'0.9rem'}}/>
            <Search size={14} style={{position:'absolute',left:'0.6rem',top:'50%',
              transform:'translateY(-50%)',color:'var(--text-secondary)'}}/>
            {showSearchDrop && searchResults.length > 0 && (
              <div style={{position:'absolute',top:'calc(100% + 4px)',left:0,right:0,
                background:'rgba(15,15,25,0.97)',backdropFilter:'blur(10px)',
                border:'1px solid var(--glass-border)',borderRadius:'8px',
                zIndex:100,boxShadow:'0 8px 24px rgba(0,0,0,0.5)',overflow:'hidden'}}>
                {searchResults.map((item, idx) => (
                  <div key={idx}
                    onMouseDown={()=>{ setSearchQuery(item.name); handleSearch(null, item.code); }}
                    style={{padding:'0.55rem 0.9rem',cursor:'pointer',display:'flex',
                      justifyContent:'space-between',alignItems:'center',
                      borderBottom: idx<searchResults.length-1?'1px solid rgba(255,255,255,0.05)':'none',
                      transition:'background 0.1s'}}
                    onMouseOver={e=>e.currentTarget.style.background='rgba(45,212,191,0.1)'}
                    onMouseOut={e=>e.currentTarget.style.background='transparent'}>
                    <span style={{fontWeight:600,fontSize:'0.85rem'}}>{item.name}</span>
                    <span style={{fontSize:'0.75rem',color:'var(--text-secondary)',
                      fontFamily:'monospace'}}>{item.code}</span>
                  </div>
                ))}
              </div>
            )}
          </form>
        </header>

        <div id="main-scroll" style={{flex:1,padding:isMobile?'1rem 0.75rem':'1.5rem',overflowY:'auto'}}>
          {/* [버그 ② 수정] screener / insight 탭 렌더링 연결 */}
          {activeTab === 'macro'             && <MacroDashboard />}
          <div style={{display: activeTab === 'market_indicators' ? 'block' : 'none'}}><MarketIndicatorsView onChangeStock={changeStock} onChangeTab={changeTab} /></div>
          {activeTab === 'market_radar'       && <MarketRadarView />}
          {activeTab === 'analysis'          && <StockAnalysis />}
          {activeTab === 'stock_rs'          && <StockAnalysisRsView />}
          {activeTab === 'semiconductor_sector' && (
            <div className="glass-panel fade-in" style={{padding:'0.6rem 0.8rem', height:'calc(100vh - 110px)', overflowY:'auto'}}>
              <SemiconductorView />
            </div>
          )}
          {activeTab === 'buy_candidates' && <BuyCandidateView />}
          {activeTab === 'watchlist' && <WatchlistView />}
          {activeTab === 'portfolio' && portfolioAuth && <PortfolioView />}
          {activeTab === 'screener'   && <Screener />}
          {activeTab === 'tenbagger' && <TenbaggerView />}
          {activeTab === 'dart_contracts' && <DartContractView />}
          {activeTab === 'megatrend' && <MegatrendView />}
          {activeTab === 'trend'     && <PeakView />}
          {activeTab === 'reports'   && <SectorReports />}
          {activeTab === 'insight'   && <AIInsight />}
          {activeTab === 'system'    && <SystemStatus />}
          {activeTab === 'export_health' && <ExportHealthView />}
          {activeTab === 'telegram'  && <TelegramMentions />}
          {activeTab === 'settings'  && <SettingsView />}
          {activeTab === 'backtest'  && <BacktestView />}
          {activeTab === 'hs_trade2' && <TradeAnalysis2 />}
          {activeTab === 'employment' && <EmploymentView />}
          {activeTab === 'etf_check' && <EtfCheckView />}
          {activeTab === 'hot_sector' && <SectorFollowupView />}
        </div>
      </div>

      {/* 로딩 오버레이 */}
      {loading && (
        <div className="loading-overlay">
          <div style={{ textAlign: 'center' }}>
            <div style={{ width: '40px', height: '40px', border: '3px solid rgba(45,212,191,0.2)', borderTop: '3px solid var(--accent-mint)', borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 1rem' }} />
            <p style={{ color: 'var(--accent-mint)' }}>데이터 로딩 중...</p>
          </div>
        </div>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
};

export default App;
