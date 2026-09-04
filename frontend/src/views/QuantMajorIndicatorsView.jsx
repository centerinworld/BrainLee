import React from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  Database,
  Layers3,
  Search,
  ShieldCheck,
  Target,
} from 'lucide-react';
import { API } from '../utils.js';
import { QuantCafeSignalsPanel } from './CafeSignalsView.jsx';

const CATEGORY_LABELS = {
  0: '자동차/모빌리티',
  1: '원자재/철강',
  2: '소비/커머스',
  3: '여행/레저',
  4: '석탄/에너지',
  6: '반도체/전력',
  7: '해운/운임',
  8: '뷰티/화장품',
  9: '카지노/관광',
  10: '미디어/IPTV',
  11: '수산/식품',
  12: '의류/패션',
  13: '카드소비',
  15: '베트남 IT',
  16: '헬스케어 소비',
  17: '후판/조선',
  19: '에너지 장비',
  20: '거시경제지표',
  21: '시장폭/거래량',
  22: '교통/대중교통',
  23: '수출입/섹터',
  24: '글로벌 매크로/원자재',
  34: '카페 지표 후보',
};

const STATUS_META = {
  ready_existing: {
    label: '연결완료',
    shortLabel: '완료',
    color: '#34d399',
    bg: 'rgba(16,185,129,0.14)',
    border: 'rgba(52,211,153,0.28)',
  },
  ready_existing_partial: {
    label: '부분연결',
    shortLabel: '부분',
    color: '#fbbf24',
    bg: 'rgba(251,191,36,0.14)',
    border: 'rgba(251,191,36,0.28)',
  },
  partial_existing: {
    label: '부분연결',
    shortLabel: '부분',
    color: '#fbbf24',
    bg: 'rgba(251,191,36,0.14)',
    border: 'rgba(251,191,36,0.28)',
  },
  derivable_after_new_collector: {
    label: '계산대기',
    shortLabel: '계산',
    color: '#60a5fa',
    bg: 'rgba(96,165,250,0.14)',
    border: 'rgba(96,165,250,0.28)',
  },
  new_collector_needed: {
    label: '수집대기중',
    shortLabel: '대기',
    color: '#f87171',
    bg: 'rgba(248,113,113,0.14)',
    border: 'rgba(248,113,113,0.28)',
  },
  source_discontinued: {
    label: '원천중단',
    shortLabel: '중단',
    color: '#94a3b8',
    bg: 'rgba(148,163,184,0.14)',
    border: 'rgba(148,163,184,0.28)',
  },
};

const PRIORITY_META = {
  p1: { label: '1순위', color: '#2dd4bf' },
  p2: { label: '2순위', color: '#93c5fd' },
  p3: { label: '3순위', color: '#c084fc' },
};

const STATUS_ORDER = ['ready_existing', 'ready_existing_partial', 'partial_existing', 'derivable_after_new_collector', 'new_collector_needed', 'source_discontinued'];
const SERIES_COLORS = ['#2dd4bf', '#60a5fa', '#f59e0b', '#f472b6', '#a78bfa', '#34d399'];

const QUANT_EXTENSION_PLAN = [
  {
    group: '1. 스타일 팩터 스코어',
    tone: '#2dd4bf',
    why: '퀀트 전문가들이 가장 기본으로 보는 value, quality, momentum, size, low volatility 축입니다. 종목 발굴 점수의 뼈대가 됩니다.',
    items: [
      { name: 'Value composite', formula: 'PER/PBR/EV-EBITDA/FCF yield/배당수익률 z-score', source: 'DB 재무제표 + price_history + valuation_history', readiness: '대부분 보유', priority: 'P1' },
      { name: 'Quality composite', formula: 'ROE/ROA/OPM/부채비율/이익안정성/현금전환율', source: 'canonical financial/cashflow', readiness: '부분 보유', priority: 'P1' },
      { name: 'Momentum composite', formula: '1M/3M/6M/12M 수익률, 52주 신고가 거리, RS percentile', source: 'price_history, stock_rs', readiness: '보유', priority: 'P1' },
      { name: 'Low volatility / downside risk', formula: '60/120일 변동성, 하락변동성, MDD, beta', source: 'price_history + KOSPI/KOSDAQ', readiness: '계산 가능', priority: 'P1' },
      { name: 'Size / liquidity', formula: '시총, 거래대금, 회전율, 거래대금 안정성', source: 'stock_universe, price_history', readiness: '보유', priority: 'P1' },
    ],
  },
  {
    group: '2. 실적 가속·턴어라운드',
    tone: '#60a5fa',
    why: '텐버거 후보는 단순 저평가보다 실적 변화율이 중요합니다. 분기 실적의 방향 전환을 조기에 잡는 탭입니다.',
    items: [
      { name: 'Earnings acceleration', formula: '매출/영업이익/순이익 YoY, QoQ, 2분기 연속 개선', source: 'canonical financial_data', readiness: '보유/검증 필요', priority: 'P1' },
      { name: 'Margin inflection', formula: 'OPM/GPM 개선폭, 판관비율 하락, 원가율 개선', source: '재무제표 + cost_structure', readiness: '부분 보유', priority: 'P1' },
      { name: 'Cash conversion quality', formula: 'OCF/순이익, FCF/매출, 운전자본 부담', source: 'cash_flow_data', readiness: '부분 보유', priority: 'P1' },
      { name: 'CapEx expansion signal', formula: 'CapEx YoY, D&A 대비 CapEx, 수주잔고 동반 여부', source: 'cashflow + backlog', readiness: '부분 보유', priority: 'P2' },
      { name: 'Inventory / backlog cycle', formula: '재고자산 증가율, 수주잔고 증가율, 매출 대비 재고', source: 'inventory, order_backlog', readiness: '수집 확장 중', priority: 'P2' },
    ],
  },
  {
    group: '3. 수급·시장 미세구조',
    tone: '#f59e0b',
    why: '실제 매매에서는 좋은 기업도 수급이 붙어야 움직입니다. 장중 자동매매와 연결될 후보군입니다.',
    items: [
      { name: 'Volume breakout quality', formula: '20일 평균 대비 거래량/거래대금, 장대양봉, 고가권 돌파', source: 'price_history, intraday/tick', readiness: '일봉 보유, 틱 확인 필요', priority: 'P1' },
      { name: 'Investor flow persistence', formula: '외국인/기관 5·20일 순매수, 연속성, 거래대금 대비 비율', source: 'KIS/Kiwoom investor flow', readiness: '부분 보유', priority: 'P1' },
      { name: 'Short balance squeeze', formula: '대차잔고/공매도잔고 감소 + 거래량 급증', source: 'short_sell_daily + price_history', readiness: '부분 보유', priority: 'P2' },
      { name: 'ETF / passive flow', formula: 'ETF 편입/비중 변화, 섹터 ETF 수급', source: 'ETF tables', readiness: '부분 보유', priority: 'P2' },
      { name: 'Intraday execution health', formula: '체결강도, VWAP 이탈, 분봉 추세, 호가 스프레드', source: 'Kiwoom tick/minute/orderbook', readiness: '수집 점검 필요', priority: 'P1' },
    ],
  },
  {
    group: '4. 이벤트·공시 팩터',
    tone: '#c084fc',
    why: '리포트나 단순 재무제표보다 빠르게 주가를 움직이는 재료성 이벤트를 점수화합니다.',
    items: [
      { name: 'Order backlog surprise', formula: '수주잔고 YoY/QoQ, 매출 대비 수주잔고, 신규수주 공시', source: 'DART business report + contracts', readiness: '수집 확장 중', priority: 'P1' },
      { name: 'Dilution / overhang risk', formula: 'CB/BW/유증/스톡옵션 희석 가능 주식수', source: 'dart_dilution_collector', readiness: '보유', priority: 'P1' },
      { name: 'Buyback / insider alignment', formula: '자사주 취득·소각, 최대주주/임원 지분 변화', source: 'treasury_buyback, insider holdings', readiness: '부분 보유', priority: 'P2' },
      { name: 'Analyst revision / consensus surprise', formula: '목표가/추정 EPS 상향, 컨센서스 괴리', source: 'consensus DB / 한경·FnGuide 후보', readiness: '정비 필요', priority: 'P2' },
      { name: 'Disclosure sentiment', formula: '사업보고서의 설비·인력·원재료·재고 변화 문장 점수화', source: 'DART text parser + LLM verifier', readiness: '신규 설계', priority: 'P2' },
    ],
  },
  {
    group: '5. 섹터/매크로 로테이션',
    tone: '#34d399',
    why: '개별 종목 점수가 좋아도 해당 섹터 사이클이 꺾이면 실패 확률이 높습니다. 섹터 총량 지표와 종목 점수를 연결합니다.',
    items: [
      { name: 'HS export momentum', formula: '기업 매핑 HS의 수출액/단가/중량 YoY, MoM', source: 'hs_trade_lab + company_hs_map', readiness: '보유/확장', priority: 'P1' },
      { name: 'Sector breadth', formula: '섹터 내 상승종목비율, 52주 신고가 비율, RS median', source: 'price_history + sector map', readiness: '계산 가능', priority: 'P1' },
      { name: 'Macro regime overlay', formula: '금리/환율/유동성/CSI/ESI/제조업BSI 레짐', source: 'ECOS + market indicators', readiness: '보유', priority: 'P2' },
      { name: 'Commodity input spread', formula: '제품 수출단가 - 원재료 수입단가, 마진 proxy', source: 'public:23 customs sectors', readiness: '보유', priority: 'P2' },
      { name: 'Global peer confirmation', formula: '미국/대만/중국 동종 기업 RS와 실적 방향', source: 'US stock DB + Yahoo/SEC', readiness: '부분 보유', priority: 'P3' },
    ],
  },
  {
    group: '6. 카페 주요글 반영 확장',
    tone: '#f472b6',
    why: '지표상회 카페 주요글과 지표 제안 게시판에서 반복 등장한 업종을 수출입·매크로 지표와 연결합니다.',
    items: [
      { name: 'Construction cycle', formula: '주택착공/건설기성/건설수주 + 건설기계·철강재 수출입', source: 'ECOS/KOSIS 후보 + public:23:38/39', readiness: '부분 보유', priority: 'P1' },
      { name: 'Semiconductor special gases', formula: '네온/제논/크립톤/특수가스 수출입과 반도체 사이클', source: 'public:23:32 + HS company map', readiness: '보유/확장', priority: 'P1' },
      { name: 'Power equipment cycle', formula: '변압기/차단기/배전반/전력선 수출입 + 전력기기 종목 매핑', source: 'public:23:36 + HS company map', readiness: '보유/확장', priority: 'P1' },
      { name: 'Aerospace / defense flow', formula: '항공기 부분품·무인기·전차·레이더·탄약 HS 수출입', source: 'public:23:37', readiness: '보유/확장', priority: 'P1' },
      { name: 'Alcohol / media / game demand', formula: '맥주·소주 수출입, 영화관/IPTV/게임은 신규 수집 후보', source: 'public:23:31 + 신규 수집 후보', readiness: '부분 보유', priority: 'P2' },
      { name: 'Materials spread', formula: '스테인리스/타이어코드/윤활기유/비료·칼륨 원재료 사이클', source: 'public:23:33/34/35/28', readiness: '보유/확장', priority: 'P2' },
    ],
  },
];

const PLAN_STATUS_META = {
  '대부분 보유': { color: '#34d399', bg: 'rgba(52,211,153,0.14)' },
  보유: { color: '#34d399', bg: 'rgba(52,211,153,0.14)' },
  '계산 가능': { color: '#60a5fa', bg: 'rgba(96,165,250,0.14)' },
  '보유/확장': { color: '#60a5fa', bg: 'rgba(96,165,250,0.14)' },
  '부분 보유': { color: '#fbbf24', bg: 'rgba(251,191,36,0.14)' },
  '보유/검증 필요': { color: '#fbbf24', bg: 'rgba(251,191,36,0.14)' },
  '수집 확장 중': { color: '#f59e0b', bg: 'rgba(245,158,11,0.14)' },
  '정비 필요': { color: '#f87171', bg: 'rgba(248,113,113,0.14)' },
  '신규 설계': { color: '#c084fc', bg: 'rgba(192,132,252,0.14)' },
  '수집 점검 필요': { color: '#f87171', bg: 'rgba(248,113,113,0.14)' },
};

const normalizePriority = (priority) => String(priority || '').toLowerCase();
const normalizePeriod = (period) => String(period || '').replace(/-01$/, '').replace(/-00$/, '');

const fmtValue = (value, unit = '') => {
  if (value == null || Number.isNaN(Number(value))) return '-';
  const n = Number(value);
  if (unit.includes('%')) return `${n.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}%`;
  if (unit.includes('억원')) {
    if (Math.abs(n) >= 10000) return `${(n / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}조원`;
    return `${Math.round(n).toLocaleString('ko-KR')}억원`;
  }
  if (unit.includes('백만원')) {
    if (Math.abs(n) >= 100000) return `${(n / 100000).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억원`;
    return `${Math.round(n).toLocaleString('ko-KR')}백만원`;
  }
  return n.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) + (unit && unit !== '-' ? ` ${unit}` : '');
};

const buildChartRows = (items) => {
  const byPeriod = new Map();
  [...items].reverse().forEach((row) => {
    const key = normalizePeriod(row.period);
    if (!byPeriod.has(key)) byPeriod.set(key, { period: key });
    byPeriod.get(key)[row.series_name] = Number(row.value);
  });
  return [...byPeriod.values()];
};

const countStatuses = (items) => {
  const counts = { total: items.length, ready: 0, partial: 0, derivable: 0, waiting: 0 };
  items.forEach((item) => {
    if (item.status === 'ready_existing') counts.ready += 1;
    else if (item.status === 'ready_existing_partial') counts.partial += 1;
    else if (item.status === 'derivable_after_new_collector') counts.derivable += 1;
    else counts.waiting += 1;
  });
  return counts;
};

const isConnectedStatus = (status) => ['ready_existing', 'ready_existing_partial', 'partial_existing'].includes(status);

const MiniStatusBar = ({ counts }) => {
  const total = Math.max(counts.total || 0, 1);
  const segments = [
    { key: 'ready', value: counts.ready, color: '#34d399' },
    { key: 'partial', value: counts.partial, color: '#fbbf24' },
    { key: 'derivable', value: counts.derivable, color: '#60a5fa' },
    { key: 'waiting', value: counts.waiting, color: '#f87171' },
  ].filter((segment) => segment.value > 0);

  return (
    <div style={{ display: 'flex', height: 4, overflow: 'hidden', borderRadius: 999, background: 'rgba(255,255,255,0.06)' }}>
      {segments.map((segment) => (
        <div
          key={segment.key}
          style={{ width: `${(segment.value / total) * 100}%`, background: segment.color }}
        />
      ))}
    </div>
  );
};

const QuantExpansionPlan = () => {
  const groups = React.useMemo(() => QUANT_EXTENSION_PLAN.map((group) => ({
    ...group,
    items: group.items.filter((item) => !['보유', '대부분 보유', '계산 가능'].includes(item.readiness)),
  })).filter((group) => group.items.length), []);
  const summary = React.useMemo(() => {
    const flat = groups.flatMap((group) => group.items);
    return {
      total: flat.length,
      needsWork: flat.filter((item) => !['보유', '대부분 보유', '계산 가능'].includes(item.readiness)).length,
    };
  }, [groups]);

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <div className="glass-panel" style={{ padding: '1rem 1.1rem', border: '1px solid rgba(45,212,191,0.16)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 1fr) minmax(360px, 0.9fr)', gap: '1rem', alignItems: 'start' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.55rem', marginBottom: '0.45rem' }}>
              <Target size={18} style={{ color: '#2dd4bf' }} />
              <h3 style={{ fontSize: '1.05rem' }}>퀀트 지표 확장계획</h3>
            </div>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.82rem', lineHeight: 1.65 }}>
              EPIC 대체지표를 넘어, 실제 종목 발굴·가상매매·자동매매에 연결하기 위한 팩터맵입니다.
              값이 이미 있는 지표와 새로 정비해야 할 지표를 분리해, 무리한 프록시 적재 없이 단계적으로 붙입니다.
            </p>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '0.55rem' }}>
            {[
              { label: '미완료', value: summary.total, color: '#fbbf24' },
              { label: '정비', value: summary.needsWork, color: '#fbbf24' },
            ].map((card) => (
              <div key={card.label} style={{
                padding: '0.68rem 0.72rem',
                borderRadius: 12,
                background: 'rgba(255,255,255,0.035)',
                border: '1px solid rgba(255,255,255,0.08)',
              }}>
                <div style={{ color: card.color, fontSize: '0.72rem', fontWeight: 800, marginBottom: '0.24rem' }}>{card.label}</div>
                <div style={{ fontSize: '1.08rem', fontWeight: 850 }}>{card.value.toLocaleString('ko-KR')}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="glass-panel" style={{ padding: '1rem 1.1rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '0.75rem' }}>
          {[
            {
              icon: <ShieldCheck size={16} />,
              title: '원칙 1 · 검증 우선',
              desc: '투자 로직에 들어가는 지표는 source_name, source_detail, quality가 남아야 하며 partial은 exact처럼 사용하지 않습니다.',
            },
            {
              icon: <BarChart3 size={16} />,
              title: '원칙 2 · 팩터 합성',
              desc: '단일 지표가 아니라 가치·퀄리티·모멘텀·수급·이벤트를 z-score로 합산해 과최적화를 줄입니다.',
            },
            {
              icon: <Database size={16} />,
              title: '원칙 3 · 수집 주기 분리',
              desc: '재무/공시는 일·월·분기, 수급/분봉은 장중, 매크로/관세청은 월간으로 분리해 비용과 안정성을 맞춥니다.',
            },
          ].map((item) => (
            <div key={item.title} style={{ padding: '0.9rem', borderRadius: 13, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', color: '#2dd4bf', fontWeight: 850, marginBottom: '0.45rem', fontSize: '0.83rem' }}>
                {item.icon}{item.title}
              </div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.76rem', lineHeight: 1.55 }}>{item.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {groups.map((group) => (
        <div key={group.group} className="glass-panel" style={{ padding: '1rem 1.1rem', border: `1px solid ${group.tone}30` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'flex-start', marginBottom: '0.8rem', flexWrap: 'wrap' }}>
            <div>
              <h3 style={{ color: group.tone, fontSize: '0.98rem', marginBottom: '0.35rem' }}>{group.group}</h3>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem', lineHeight: 1.55, maxWidth: 900 }}>{group.why}</div>
            </div>
            <span style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem', borderRadius: 999, background: `${group.tone}1e`, color: group.tone, border: `1px solid ${group.tone}55`, fontWeight: 800 }}>
              {group.items.length}개 후보
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem', minWidth: 960 }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.045)', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                  {['지표', '계산/정의', '원천 후보', '준비상태'].map((head) => (
                    <th key={head} style={{ padding: '0.55rem 0.6rem', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 800 }}>{head}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {group.items.map((item) => {
                  const meta = PLAN_STATUS_META[item.readiness] || { color: '#94a3b8', bg: 'rgba(148,163,184,0.14)' };
                  return (
                    <tr key={item.name} style={{ borderBottom: '1px solid rgba(255,255,255,0.055)' }}>
                      <td style={{ padding: '0.58rem 0.6rem', fontWeight: 850, color: 'var(--text-primary)' }}>{item.name}</td>
                      <td style={{ padding: '0.58rem 0.6rem', color: 'var(--text-secondary)', lineHeight: 1.45 }}>{item.formula}</td>
                      <td style={{ padding: '0.58rem 0.6rem', color: '#93c5fd', lineHeight: 1.45 }}>{item.source}</td>
                      <td style={{ padding: '0.58rem 0.6rem' }}>
                        <span style={{ display: 'inline-flex', padding: '0.16rem 0.48rem', borderRadius: 999, color: meta.color, background: meta.bg, border: `1px solid ${meta.color}44`, fontWeight: 850, whiteSpace: 'nowrap' }}>
                          {item.readiness}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
};

const QuantMajorIndicatorsView = React.memo(() => {
  const [viewMode, setViewMode] = React.useState('indicators');
  const [catalog, setCatalog] = React.useState([]);
  const [statusFilter, setStatusFilter] = React.useState('all');
  const [searchText, setSearchText] = React.useState('');
  const [selectedCategory, setSelectedCategory] = React.useState('all');
  const [selectedIndicator, setSelectedIndicator] = React.useState(null);
  const [seriesDetail, setSeriesDetail] = React.useState(null);
  const [crossContext, setCrossContext] = React.useState(null);
  const [loadingCatalog, setLoadingCatalog] = React.useState(false);
  const [loadingSeries, setLoadingSeries] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    setLoadingCatalog(true);
    fetch(API('/api/quant-major-indicators/catalog'))
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((catalogResp) => {
        if (cancelled) return;
        const rows = Array.isArray(catalogResp?.items) ? catalogResp.items : [];
        setCatalog(rows.map((row) => ({ ...row, priority: normalizePriority(row.priority) })));
      })
      .finally(() => {
        if (!cancelled) setLoadingCatalog(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const totalCounts = React.useMemo(() => countStatuses(catalog), [catalog]);
  const unresolvedCatalog = React.useMemo(
    () => catalog.filter((item) => !isConnectedStatus(item.status)),
    [catalog],
  );
  const unresolvedCounts = React.useMemo(() => countStatuses(unresolvedCatalog), [unresolvedCatalog]);
  const connectedCount = totalCounts.ready + totalCounts.partial;
  const connectedPct = totalCounts.total ? Math.round((connectedCount / totalCounts.total) * 1000) / 10 : 0;

  const filteredCatalog = React.useMemo(() => {
    const q = searchText.trim().toLowerCase();
    return catalog.filter((item) => {
      if (statusFilter !== 'all' && item.status !== statusFilter) return false;
      if (!q) return true;
      return [
        item.epic_indicator_name,
        item.source_system,
        item.replacement_family,
        CATEGORY_LABELS[item.epic_category_code],
        item.notes,
      ].some((value) => String(value || '').toLowerCase().includes(q));
    });
  }, [catalog, searchText, statusFilter]);

  const categoryTabs = React.useMemo(() => {
    const grouped = new Map();
    filteredCatalog.forEach((item) => {
      const code = item.epic_category_code;
      if (!grouped.has(code)) grouped.set(code, []);
      grouped.get(code).push(item);
    });
    const categories = [...grouped.entries()]
      .map(([code, items]) => ({
        code,
        label: CATEGORY_LABELS[code] || `카테고리 ${code}`,
        ...countStatuses(items),
      }))
      .sort((a, b) => Number(a.code) - Number(b.code));
    return [{ code: 'all', label: '전체 지표', ...countStatuses(filteredCatalog) }, ...categories];
  }, [filteredCatalog]);

  React.useEffect(() => {
    if (!categoryTabs.some((category) => category.code === selectedCategory)) {
      setSelectedCategory('all');
    }
  }, [categoryTabs, selectedCategory]);

  const categoryIndicators = React.useMemo(() => {
    const rows = selectedCategory === 'all'
      ? filteredCatalog
      : filteredCatalog.filter((item) => item.epic_category_code === selectedCategory);
    return [...rows].sort((a, b) => {
      const priorityDiff = (normalizePriority(a.priority) || '').localeCompare(normalizePriority(b.priority) || '');
      if (priorityDiff) return priorityDiff;
      const catDiff = Number(a.epic_category_code || 0) - Number(b.epic_category_code || 0);
      if (catDiff) return catDiff;
      return Number(a.epic_sub_code || 0) - Number(b.epic_sub_code || 0);
    });
  }, [filteredCatalog, selectedCategory]);

  React.useEffect(() => {
    if (!categoryIndicators.length) {
      setSelectedIndicator(null);
      return;
    }
    if (!selectedIndicator || !categoryIndicators.some((item) => item.indicator_key === selectedIndicator)) {
      const firstReady = categoryIndicators.find((item) => item.status !== 'new_collector_needed');
      setSelectedIndicator((firstReady || categoryIndicators[0]).indicator_key);
    }
  }, [categoryIndicators, selectedIndicator]);

  React.useEffect(() => {
    if (!selectedIndicator) {
      setSeriesDetail(null);
      return;
    }
    let cancelled = false;
    setLoadingSeries(true);
    fetch(API(`/api/quant-major-indicators/series/${encodeURIComponent(selectedIndicator)}?limit=500`))
      .then((r) => (r.ok ? r.json() : null))
      .then((payload) => {
        if (!cancelled) setSeriesDetail(payload);
      })
      .finally(() => {
        if (!cancelled) setLoadingSeries(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIndicator]);

  React.useEffect(() => {
    if (!selectedIndicator) {
      setCrossContext(null);
      return;
    }
    let cancelled = false;
    fetch(API(`/api/quant-major-indicators/cross-context/${encodeURIComponent(selectedIndicator)}?limit=60`))
      .then((r) => (r.ok ? r.json() : null))
      .then((payload) => { if (!cancelled) setCrossContext(payload); })
      .catch(() => { if (!cancelled) setCrossContext(null); });
    return () => { cancelled = true; };
  }, [selectedIndicator]);

  const selectedMeta = React.useMemo(
    () => categoryIndicators.find((item) => item.indicator_key === selectedIndicator) || null,
    [categoryIndicators, selectedIndicator],
  );

  const chartRows = React.useMemo(() => buildChartRows(seriesDetail?.items || []), [seriesDetail]);

  const latestRows = React.useMemo(() => {
    if (!seriesDetail?.items?.length) return [];
    const latestPeriod = seriesDetail.items[0].period;
    return seriesDetail.items.filter((item) => item.period === latestPeriod);
  }, [seriesDetail]);

  // 차트에는 최대 8개 시리즈만 표시 (시리즈 과부하 방지)
  const MAX_CHART_SERIES = 8;
  const chartSeriesRows = React.useMemo(() => latestRows.slice(0, MAX_CHART_SERIES), [latestRows]);
  const extraSeriesCount = Math.max(0, latestRows.length - MAX_CHART_SERIES);

  const selectedStatusMeta = STATUS_META[selectedMeta?.status] || STATUS_META.new_collector_needed;
  const hasSeries = !!seriesDetail?.items?.length && selectedMeta?.status !== 'new_collector_needed';

  return (
    <div className="fade-in" style={{ display: 'grid', gap: '1rem' }}>
      <div className="glass-panel" style={{ padding: '1rem 1.1rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 1fr) minmax(360px, 0.95fr)', gap: '1rem', alignItems: 'start' }}>
          <div>
            <h3 style={{ fontSize: '1.08rem', marginBottom: '0.35rem' }}>퀀트 주요지표</h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.82rem', lineHeight: 1.6 }}>
              EPIC식 메뉴 구조를 우리 대시보드 스타일로 재정리했습니다. 산업별 카테고리에서 지표를 바로 고르고, 연결 상태와 수집대기 항목을 한 화면에서 확인합니다.
            </p>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: '0.55rem' }}>
            {[
              { label: '전체', value: totalCounts.total, icon: <Database size={14} />, color: '#93c5fd' },
              { label: `연결 ${connectedPct}%`, value: connectedCount, icon: <ShieldCheck size={14} />, color: '#34d399' },
              { label: '부분', value: totalCounts.partial, icon: <Layers3 size={14} />, color: '#fbbf24' },
              { label: '미연결', value: unresolvedCounts.waiting, icon: <AlertTriangle size={14} />, color: '#f87171' },
            ].filter((card) => card.value > 0).map((card) => (
              <div key={card.label} style={{
                padding: '0.68rem 0.72rem',
                borderRadius: 12,
                background: 'rgba(255,255,255,0.035)',
                border: '1px solid rgba(255,255,255,0.08)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: card.color, fontSize: '0.72rem', marginBottom: '0.28rem' }}>
                  {card.icon}<span>{card.label}</span>
                </div>
                <div style={{ fontSize: '1.14rem', fontWeight: 850 }}>{card.value.toLocaleString('ko-KR')}</div>
              </div>
            ))}
          </div>
        </div>

        <div style={{ marginTop: '0.95rem', display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) minmax(150px, 220px)', gap: '0.7rem', alignItems: 'center' }}>
          <label style={{
            display: 'flex', alignItems: 'center', gap: '0.45rem', padding: '0.58rem 0.75rem',
            borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
          }}>
            <Search size={15} style={{ color: 'var(--text-secondary)' }} />
            <input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="지표명, 소스, 카테고리 검색"
              style={{ flex: 1, minWidth: 120, border: 0, outline: 0, background: 'transparent', color: 'var(--text-primary)', fontSize: '0.82rem' }}
            />
          </label>
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            style={{
              padding: '0.5rem 0.75rem', borderRadius: 10, minWidth: 132,
              background: 'rgba(255,255,255,0.04)', color: 'var(--text-primary)',
              border: '1px solid rgba(255,255,255,0.12)', outline: 0,
            }}
          >
            <option value="all">전체 상태</option>
            {STATUS_ORDER.map((key) => <option key={key} value={key}>{STATUS_META[key].label}</option>)}
          </select>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '0.55rem', flexWrap: 'wrap' }}>
        {[
          { key: 'indicators', label: '지표별', icon: <Database size={14} /> },
          { key: 'sectors', label: '섹터별', icon: <Layers3 size={14} /> },
          { key: 'stocks', label: '종목별', icon: <BarChart3 size={14} /> },
          { key: 'plan', label: '확장계획·미수집', icon: <Target size={14} /> },
        ].map((tab) => {
          const active = viewMode === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setViewMode(tab.key)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.35rem',
                padding: '0.46rem 0.78rem',
                borderRadius: 999,
                cursor: 'pointer',
                border: active ? '1px solid rgba(45,212,191,0.58)' : '1px solid rgba(255,255,255,0.12)',
                background: active ? 'rgba(45,212,191,0.14)' : 'rgba(255,255,255,0.035)',
                color: active ? '#2dd4bf' : 'var(--text-secondary)',
                fontSize: '0.78rem',
                fontWeight: 850,
              }}
            >
              {tab.icon}
              {tab.label}
            </button>
          );
        })}
      </div>

      {viewMode === 'plan' ? (
        <QuantExpansionPlan />
      ) : viewMode === 'sectors' ? (
        <QuantCafeSignalsPanel mode="sector" />
      ) : viewMode === 'stocks' ? (
        <QuantCafeSignalsPanel mode="stock" />
      ) : (
        <>
      <div className="glass-panel" style={{ padding:'0.9rem 1rem', overflowX:'auto' }}>
        <div style={{ display:'flex', justifyContent:'space-between', gap:'0.7rem', alignItems:'center', flexWrap:'wrap', marginBottom:'0.65rem' }}>
          <div style={{ fontWeight:850 }}>분류별 전체 지표 현황</div>
          <div style={{ color:'var(--text-secondary)', fontSize:'0.74rem' }}>
            전체 {totalCounts.total.toLocaleString('ko-KR')}개 · 연결 {connectedCount.toLocaleString('ko-KR')}개 · 미연결 {unresolvedCounts.waiting.toLocaleString('ko-KR')}개
          </div>
        </div>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.78rem', minWidth:620 }}>
          <thead><tr style={{ color:'var(--text-secondary)', borderBottom:'1px solid rgba(255,255,255,0.1)' }}>
            {['분류','전체','연결완료','부분연결','계산대기','수집·원천대기'].map((head) => <th key={head} style={{ padding:'0.5rem', textAlign:head === '분류' ? 'left' : 'right' }}>{head}</th>)}
          </tr></thead>
          <tbody>{categoryTabs.filter((category) => category.code !== 'all').map((category) => (
            <tr key={category.code} onClick={() => setSelectedCategory(category.code)} style={{ borderBottom:'1px solid rgba(255,255,255,0.05)', cursor:'pointer', background:selectedCategory === category.code ? 'rgba(45,212,191,0.08)' : 'transparent' }}>
              <td style={{ padding:'0.52rem', fontWeight:800, color:selectedCategory === category.code ? '#2dd4bf' : 'inherit' }}>{category.label}</td>
              <td style={{ padding:'0.52rem', textAlign:'right' }}>{category.total}</td>
              <td style={{ padding:'0.52rem', textAlign:'right', color:'#34d399' }}>{category.ready}</td>
              <td style={{ padding:'0.52rem', textAlign:'right', color:'#fbbf24' }}>{category.partial}</td>
              <td style={{ padding:'0.52rem', textAlign:'right', color:'#60a5fa' }}>{category.derivable}</td>
              <td style={{ padding:'0.52rem', textAlign:'right', color:'#f87171' }}>{category.waiting}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="glass-panel" style={{ padding: '0.95rem 1rem' }}>
        {loadingCatalog && <div style={{ color: 'var(--text-secondary)', padding: '1rem 0.2rem' }}>지표 목록 불러오는 중...</div>}
        {!loadingCatalog && !categoryIndicators.length && (
          <div style={{ color: 'var(--text-secondary)', padding: '1rem 0.2rem' }}>조건에 맞는 지표가 없습니다.</div>
        )}
        {!loadingCatalog && !!categoryIndicators.length && (
          <div style={{ display:'grid', gridTemplateColumns:'minmax(180px, 0.45fr) minmax(280px, 1fr)', gap:'0.75rem' }}>
            <label style={{ display:'grid', gap:'0.35rem' }}>
              <span style={{ color:'var(--text-secondary)', fontSize:'0.74rem' }}>분류</span>
              <select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value === 'all' ? 'all' : Number(e.target.value))} style={{ padding:'0.62rem 0.72rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}>
                {categoryTabs.map((category) => <option key={category.code} value={category.code}>{category.label} ({category.total})</option>)}
              </select>
            </label>
            <label style={{ display:'grid', gap:'0.35rem' }}>
              <span style={{ color:'var(--text-secondary)', fontSize:'0.74rem' }}>세부 지표</span>
              <select value={selectedIndicator || ''} onChange={(e) => setSelectedIndicator(e.target.value)} style={{ padding:'0.62rem 0.72rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}>
                {categoryIndicators.map((item) => <option key={item.indicator_key} value={item.indicator_key}>{item.epic_indicator_name} · {(STATUS_META[item.status] || STATUS_META.new_collector_needed).label}</option>)}
              </select>
            </label>
          </div>
        )}
      </div>

      <div className="glass-panel" style={{ padding: '1rem 1.1rem' }}>
        {!selectedMeta && <div style={{ color: 'var(--text-secondary)' }}>지표를 선택하세요.</div>}
        {selectedMeta && (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap', marginBottom: '0.8rem' }}>
              <div>
                <div style={{ display: 'flex', gap: '0.55rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '0.45rem' }}>
                  <h3 style={{ fontSize: '1.05rem' }}>{selectedMeta.epic_indicator_name}</h3>
                  <span style={{ fontSize: '0.68rem', padding: '0.18rem 0.48rem', borderRadius: 999, background: selectedStatusMeta.bg, border: `1px solid ${selectedStatusMeta.border}`, color: selectedStatusMeta.color, fontWeight: 800 }}>{selectedStatusMeta.label}</span>
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
                  {CATEGORY_LABELS[selectedMeta.epic_category_code] || `카테고리 ${selectedMeta.epic_category_code}`} · {selectedMeta.source_system || '-'}
                </div>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '0.8rem', marginBottom: '1rem' }}>
              <div style={{ padding: '0.8rem', borderRadius: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.72rem', marginBottom: '0.2rem' }}>정확도 정책</div>
                <div style={{ fontWeight: 750 }}>{selectedMeta.exactness || '-'}</div>
              </div>
              <div style={{ padding: '0.8rem', borderRadius: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.72rem', marginBottom: '0.2rem' }}>기본 단위</div>
                <div style={{ fontWeight: 750 }}>{selectedMeta.base_unit || '-'}</div>
              </div>
              <div style={{ padding: '0.8rem', borderRadius: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.72rem', marginBottom: '0.2rem' }}>업데이트</div>
                <div style={{ fontWeight: 750 }}>{selectedMeta.updated_at || '-'}</div>
              </div>
            </div>

            {crossContext && (
              <div style={{ borderTop:'1px solid rgba(255,255,255,0.08)', borderBottom:'1px solid rgba(255,255,255,0.08)', padding:'0.8rem 0', marginBottom:'1rem' }}>
                <div style={{ display:'flex', alignItems:'center', gap:'0.55rem', flexWrap:'wrap', marginBottom:'0.65rem' }}>
                  <ShieldCheck size={16} color="#2dd4bf" />
                  <strong style={{ fontSize:'0.84rem' }}>HS Trade 교차검증</strong>
                  <span style={{ fontSize:'0.7rem', color:'#2dd4bf' }}>양쪽 확인 {crossContext.summary?.cross_confirmed || 0}종목</span>
                  {!!crossContext.summary?.quant_only && <span style={{ fontSize:'0.7rem', color:'#fbbf24' }}>퀀트만 {crossContext.summary.quant_only}종목</span>}
                  {crossContext.latest_signal && (
                    <span style={{ marginLeft:'auto', fontSize:'0.7rem', color:['positive','green','buy','spike_up'].includes(crossContext.latest_signal.signal_type) ? '#34d399' : ['negative','red','sell','spike_down'].includes(crossContext.latest_signal.signal_type) ? '#f87171' : '#fbbf24' }}>
                      최근 신호 {crossContext.latest_signal.signal_type} · {crossContext.latest_signal.period}
                    </span>
                  )}
                </div>
                {crossContext.items?.length ? (
                  <div style={{ overflowX:'auto', maxHeight:230, overflowY:'auto' }}>
                    <table style={{ width:'100%', minWidth:760, borderCollapse:'collapse', fontSize:'0.75rem' }}>
                      <thead><tr style={{ color:'var(--text-secondary)', borderBottom:'1px solid rgba(255,255,255,0.08)' }}>
                        {['종목','퀀트 섹터','검증','관련 HS 품목','매출/이익 비중'].map((h) => <th key={h} style={{ padding:'0.42rem', textAlign:'left' }}>{h}</th>)}
                      </tr></thead>
                      <tbody>{crossContext.items.map((item) => (
                        <tr key={item.stock_code} style={{ borderBottom:'1px solid rgba(255,255,255,0.045)' }}>
                          <td style={{ padding:'0.45rem', fontWeight:800 }}>{item.stock_name} <span style={{ color:'var(--text-secondary)' }}>({item.stock_code})</span></td>
                          <td style={{ padding:'0.45rem' }}>{item.sector_name || '-'}</td>
                          <td style={{ padding:'0.45rem', color:item.cross_validation === 'cross_confirmed' ? '#34d399' : '#fbbf24' }}>{item.cross_validation === 'cross_confirmed' ? '교차확인' : 'HS 미확인'}</td>
                          <td style={{ padding:'0.45rem', maxWidth:320 }}>{(item.hs_mappings || []).slice(0,3).map((h) => `${h.hs_code} ${h.display_name || h.hs_name}`).join(' · ') || '-'}</td>
                          <td style={{ padding:'0.45rem' }}>{item.revenue_exposure_pct != null ? `매출 ${item.revenue_exposure_pct}%` : item.profit_exposure_pct != null ? `이익 ${item.profit_exposure_pct}%` : '미확정'}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                ) : <div style={{ color:'var(--text-secondary)', fontSize:'0.76rem' }}>이 지표에 확정된 종목 매핑이 아직 없습니다.</div>}
              </div>
            )}

            {loadingSeries && <div style={{ color: 'var(--text-secondary)', padding: '1rem 0.2rem' }}>시계열 불러오는 중...</div>}

            {!loadingSeries && !hasSeries && (
              <div style={{ padding: '1rem 1.05rem', borderRadius: 12, background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.22)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#f87171', fontWeight: 800, marginBottom: '0.6rem' }}>
                  <AlertTriangle size={16} /> 수집대기중
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.82rem', lineHeight: 1.65 }}>
                  {selectedMeta.notes || '아직 데이터 원천이 연결되지 않았습니다.'}
                </div>
                <div style={{ marginTop: '0.8rem', display: 'grid', gap: '0.35rem', fontSize: '0.8rem' }}>
                  <div><span style={{ color: 'var(--text-secondary)' }}>필요 소스:</span> <span>{selectedMeta.source_system || '-'}</span></div>
                  <div><span style={{ color: 'var(--text-secondary)' }}>연결 방식:</span> <span>{selectedMeta.replacement_family || '-'}</span></div>
                </div>
              </div>
            )}

            {!loadingSeries && hasSeries && (
              <div style={{ display: 'grid', gap: '1rem' }}>
                <div style={{ height: 320, borderRadius: 12, padding: '0.8rem', background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)' }}>
                  {extraSeriesCount > 0 && (
                    <div style={{ fontSize: '0.72rem', color: '#fbbf24', marginBottom: '0.4rem' }}>
                      차트: 상위 {MAX_CHART_SERIES}개 시리즈 표시 (전체 {latestRows.length}개 중)
                    </div>
                  )}
                  <ResponsiveContainer width="100%" height={extraSeriesCount > 0 ? '90%' : '100%'}>
                    <LineChart data={chartRows} margin={{ top: 8, right: 18, left: 4, bottom: 8 }}>
                      <CartesianGrid stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
                      <XAxis dataKey="period" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                      <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} />
                      <Tooltip contentStyle={{ background: 'rgba(15,23,42,0.94)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10 }} />
                      <Legend />
                      {chartSeriesRows.map((row, idx) => (
                        <Line key={row.series_name} type="monotone" dataKey={row.series_name} stroke={SERIES_COLORS[idx % SERIES_COLORS.length]} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(4, Math.min(latestRows.length, MAX_CHART_SERIES) || 1)}, minmax(0, 1fr))`, gap: '0.8rem' }}>
                  {chartSeriesRows.map((row, idx) => (
                    <div key={`${row.period}-${row.series_name}`} style={{ padding: '0.85rem 0.9rem', borderRadius: 12, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)' }}>
                      <div style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', marginBottom: '0.28rem' }}>{row.series_name}</div>
                      <div style={{ fontSize: '1.2rem', fontWeight: 850, color: SERIES_COLORS[idx % SERIES_COLORS.length] }}>{fmtValue(row.value, row.unit)}</div>
                      <div style={{ marginTop: '0.28rem', fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{row.period} · {row.source_name}</div>
                    </div>
                  ))}
                </div>

                <div style={{ padding: '0.9rem', borderRadius: 12, background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', marginBottom: '0.7rem', color: 'var(--text-secondary)', fontSize: '0.82rem' }}>
                    <Activity size={15} /> 최근 시계열
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                          {['기간', '시리즈', '값', '단위', '소스', '품질'].map((head) => (
                            <th key={head} style={{ padding: '0.42rem 0.5rem', textAlign: head === '값' ? 'right' : 'left', color: 'var(--text-secondary)', fontWeight: 650 }}>{head}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {seriesDetail.items.slice(0, 18).map((row) => (
                          <tr key={`${row.period}-${row.series_name}-${row.source_name}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                            <td style={{ padding: '0.42rem 0.5rem' }}>{row.period}</td>
                            <td style={{ padding: '0.42rem 0.5rem' }}>{row.series_name}</td>
                            <td style={{ padding: '0.42rem 0.5rem', textAlign: 'right', fontWeight: 750 }}>{fmtValue(row.value, row.unit)}</td>
                            <td style={{ padding: '0.42rem 0.5rem' }}>{row.unit || '-'}</td>
                            <td style={{ padding: '0.42rem 0.5rem', color: '#93c5fd' }}>{row.source_name}</td>
                            <td style={{ padding: '0.42rem 0.5rem', color: 'var(--text-secondary)' }}>{row.quality || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
        </>
      )}
    </div>
  );
});

export default QuantMajorIndicatorsView;
