/**
 * Screener.jsx
 * AI 종목 스크리너 — App.jsx에서 분리 (2026-09-03, 토큰 최적화)
 * 원본은 App.jsx에 인라인으로 정의되어 있던 컴포넌트를 그대로 이관. 로직/JSX 변경 없음.
 * 전략센터(StrategyHub)에 내장되거나, /screener 탭에서 단독으로도 쓰인다.
 *
 * [2026-09-03 수정] 이 컴포넌트 본문은 `fmtKrw(...)`를 호출하는데, fmtKrw는 원래
 * App.jsx의 SignalBoard 인접 모듈 스코프에 선언된 함수라 별도 파일로 분리된 Screener
 * 모듈에서는 참조 불가능했다(ReferenceError). App.jsx와 동일한 구현을 `frontend/src/utils.js`의
 * 공용 헬퍼로 옮기고 여기서 import하도록 수정.
 */
import React from 'react';
import { TrendingUp } from 'lucide-react';
import { API, fmtKrw } from '../utils';

// ── Screener (module-level: App 재렌더와 무관하게 안정적인 컴포넌트 identity 유지, changeStock/changeTab 등은 props로 전달) ──
  const Screener = ({ defaultTab = 'combo', hideTabBar = false, defaultComboLogic = 'v1', changeStock, changeTab }) => {
    const [screenTab, setScreenTab] = React.useState(defaultTab);  // 기본: AI 적극 검토
    const [trendStocks, setTrendStocks] = React.useState([]);
    const [trendLoading, setTrendLoading] = React.useState(false);
    const [valueCandidates, setValueCandidates] = React.useState([]);
    const [valueLoading, setValueLoading] = React.useState(false);
    const [finStocks, setFinStocks] = React.useState([]);
    const [finLoading, setFinLoading] = React.useState(false);
    const [highProfitStocks, setHighProfitStocks] = React.useState([]);
    const [highProfitLoading, setHighProfitLoading] = React.useState(false);
    const [comboFromServer, setComboFromServer] = React.useState([]);
    const [comboLoading, setComboLoading] = React.useState(false);
    const [showFinLogic, setShowFinLogic] = React.useState(false);
    const [comboFilter, setComboFilter] = React.useState('all'); // 'all'=2개↑, 'triple'=3개
    const [comboLogic, setComboLogic] = React.useState(defaultComboLogic);  // 'v1'=V5복합콤보, 'v2'=V4수급모멘텀, 'kiwoom'=키움조건식
    const [comboV2Data, setComboV2Data] = React.useState([]);
    const [comboV2Loading, setComboV2Loading] = React.useState(false);
    const [kiwoomCondData, setKiwoomCondData] = React.useState(null);
    const [kiwoomCondLoading, setKiwoomCondLoading] = React.useState(false);
    const [kiwoomCondTab, setKiwoomCondTab] = React.useState('value_blue');
    const [triggerData, setTriggerData] = React.useState(null);
    const [triggerLoading, setTriggerLoading] = React.useState(false);
    const [screenerMeta, setScreenerMeta] = React.useState(null);
    const [v18Data, setV18Data] = React.useState(null);
    const [v18Loading, setV18Loading] = React.useState(false);
    const [gcData, setGcData] = React.useState(null);
    const [gcLoading, setGcLoading] = React.useState(false);
    const [recData, setRecData] = React.useState(null);
    const [recLoading, setRecLoading] = React.useState(false);
    const stickyRef = React.useRef(null);

    // 스크리너 메타(로직 설명) — signal_logic.py 에서 동적 로드
    React.useEffect(() => {
      fetch(API('/api/signals/meta'))
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
        // 서버가 cold 상태면 분석 완료 때까지 가벼운 캐시 조회만 재시도한다.
        for (let attempt = 0; attempt < 18; attempt += 1) {
          const res = await fetch(API('/api/signals/fin-screener'));
          const data = res.ok ? await res.json() : [];
          if (data?.length > 0) {
            setFinStocks(data);
            return;
          }
          await new Promise(resolve => setTimeout(resolve, 5000));
        }
      } catch(e) { console.error(e); }
      finally { setFinLoading(false); }
    };

    const fetchHighProfitCandidates = async (refresh=false) => {
      setHighProfitLoading(true);
      try {
        const res = await fetch(API(`/api/signals/high-profit-candidates?limit=80${refresh ? '&refresh=true' : ''}`));
        if (res.ok) setHighProfitStocks(await res.json());
      } catch(e) { console.error(e); }
      finally { setHighProfitLoading(false); }
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
        for (let attempt = 0; attempt < 18; attempt += 1) {
          const res = await fetch(API('/api/signals/combo-v2'));
          const data = res.ok ? await res.json() : [];
          if (data?.length > 0) {
            setComboV2Data(data);
            return;
          }
          await new Promise(resolve => setTimeout(resolve, 5000));
        }
      } catch(e) { console.error(e); }
      finally { setComboV2Loading(false); }
    };

    // 키움조건식 5가지 전략
    const fetchKiwoomCond = async (refresh=false) => {
      setKiwoomCondLoading(true);
      try {
        const res = await fetch(API(`/api/signals/kiwoom-conditions${refresh?'?refresh=true':''}`));
        if (res.ok) {
          const data = await res.json();
          if (data && Object.keys(data).length > 0) setKiwoomCondData(data);
        }
      } catch(e) { console.error(e); }
      finally { setKiwoomCondLoading(false); }
    };

    React.useEffect(() => {
      if (screenTab === 'trend' && !trendStocks.length) fetchTrendLeading();
      if (screenTab === 'value' && !valueCandidates.length) fetchValueCandidates();
      if (screenTab === 'ai' && !finStocks.length) fetchFinScreener();
      if (screenTab === 'high_profit' && !highProfitStocks.length) fetchHighProfitCandidates();
      if (screenTab === 'combo') fetchCombo();
      if (screenTab === 'gpt_v18' && !v18Data) fetchV18();
      if (screenTab === 'v_gc' && !gcData) fetchGC();
      if (screenTab === 'v_rec' && !recData) fetchRec();
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

    const fetchGC = async () => {
      setGcLoading(true);
      try {
        const res = await fetch(API('/api/trend/gc/recommendations'));
        if (res.ok) setGcData(await res.json());
      } catch (e) { console.error(e); }
      finally { setGcLoading(false); }
    };

    const fetchRec = async () => {
      setRecLoading(true);
      try {
        const res = await fetch(API('/api/trend/rec/recommendations'));
        if (res.ok) setRecData(await res.json());
      } catch (e) { console.error(e); }
      finally { setRecLoading(false); }
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

      {/* 헤더 — sticky (hideTabBar 시 숨김) */}
      {!hideTabBar && (
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
            {tabBtn('high_profit', `🏆 고수익 집중`, highProfitStocks.length)}
            {tabBtn('gpt_v18', `🤖 GPT추천(V18)`, v18Data?.summary?.buy_count || 0)}
            {tabBtn('v_gc', `📊 V12 골든크로스`, gcData?.summary?.active_positions ?? 0)}
            {tabBtn('v_rec', `🩹 V-RECOVERY 낙폭반등`, recData?.summary?.active_positions ?? 0)}
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
      )}

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
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>백테스트 검증 상태</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.15rem'}}>
                    <span style={{color:'#fbbf24'}}>· 실행 명세와 run hash가 없는 기존 성과값은 표시하지 않습니다.</span>
                    <span>· 체결 시점·유니버스·수수료·자본배분을 고정한 재검증 후 API 결과로 교체합니다.</span>
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

      {/* ══ V12 골든크로스 가상매매 탭 ══ */}
      {screenTab === 'v_gc' && (
        gcLoading ? (
          <div className="glass-panel" style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>V12 골든크로스 계산 중...</div>
        ) : (
          <div className="glass-panel" style={{overflow:'clip'}}>
            {/* 헤더 */}
            <div style={{padding:'0.7rem 1rem',display:'flex',justifyContent:'space-between',alignItems:'center',gap:'0.5rem',flexWrap:'wrap',borderBottom:'1px solid var(--glass-border)'}}>
              <div style={{fontSize:'0.8rem',color:'var(--text-secondary)',display:'flex',gap:'0.8rem',flexWrap:'wrap',alignItems:'center'}}>
                <span>마지막 계산: {gcData?.updated_at || '-'}</span>
                <span>보유 {gcData?.summary?.active_positions ?? 0}종목 · 매수후보 {gcData?.summary?.buy_count ?? 0}종목 · 매도후보 {gcData?.summary?.sell_count ?? 0}종목</span>
              </div>
              <div style={{display:'flex',gap:'0.5rem'}}>
                <button onClick={async()=>{
                  setGcLoading(true);
                  try{
                    const r=await fetch(API('/api/trend/gc/execute'),{method:'POST'});
                    if(r.ok){const d=await r.json();alert(`V12 실행완료: 매도 ${d.sold}건 · 매수 ${d.bought}건`);}
                  }catch(e){console.error(e);}
                  finally{setGcLoading(false);fetchGC();}
                }} style={{padding:'0.35rem 0.9rem',borderRadius:'6px',border:'none',background:'rgba(34,197,94,0.18)',color:'#22c55e',fontSize:'0.76rem',fontWeight:600,cursor:'pointer'}}>
                  ▶ 즉시 실행
                </button>
                <button onClick={fetchGC} style={{padding:'0.35rem 0.9rem',borderRadius:'6px',border:'1px solid rgba(255,255,255,0.1)',background:'transparent',color:'var(--text-secondary)',fontSize:'0.76rem',cursor:'pointer'}}>
                  새로고침
                </button>
              </div>
            </div>

            {/* 매도 후보 */}
            {(gcData?.sell_candidates || []).length > 0 && (
              <div style={{padding:'0.6rem 0.8rem',borderBottom:'1px solid var(--glass-border)'}}>
                <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#ef4444'}}>🔴 매도 후보</div>
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>종목</th><th style={{textAlign:'center'}}>진입일</th><th style={{textAlign:'right'}}>매수가</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>수익률</th><th>사유</th></tr></thead>
                  <tbody>
                    {gcData.sell_candidates.map((r)=>(
                      <tr key={r.stock_code}>
                        <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                        <td style={{textAlign:'center'}}>{r.entry_date||'-'}</td>
                        <td style={{textAlign:'right'}}>{r.buy_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right',color:(r.profit_pct||0)>=0?'#22c55e':'#ef4444'}}>{((r.profit_pct||0)).toFixed(2)}%</td>
                        <td style={{fontSize:'0.7rem',color:'rgba(239,68,68,0.8)'}}>{r.reason||'-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* 매수 후보 */}
            <div style={{padding:'0.6rem 0.8rem',borderBottom:'1px solid var(--glass-border)'}}>
              <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#22c55e'}}>🟢 매수 후보 (골든크로스 감지)</div>
              {(gcData?.buy_candidates || []).length === 0 ? (
                <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)'}}>
                  현재 골든크로스 신호 없음 (MA20이 MA60 상향돌파 15일 이내 + 거래량확인 + RS6M 조건)
                </div>
              ) : (
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>종목</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>MA20</th><th style={{textAlign:'right'}}>MA60</th><th style={{textAlign:'right'}}>RS6M</th><th style={{textAlign:'right'}}>시총(억)</th></tr></thead>
                  <tbody>
                    {(gcData.buy_candidates).slice(0,15).map((r)=>(
                      <tr key={r.stock_code}>
                        <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                        <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right'}}>{r.ma20?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right'}}>{r.ma60?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right',color:(r.rs6m||0)>=0?'#22c55e':'#ef4444'}}>{(r.rs6m??0).toFixed(1)}%</td>
                        <td style={{textAlign:'right'}}>{r.mktcap_억?.toLocaleString?.()??'-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* 현재 보유 */}
            <div style={{padding:'0.6rem 0.8rem',borderBottom:'1px solid var(--glass-border)'}}>
              <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'rgba(255,255,255,0.78)'}}>📋 보유중 V12 종목</div>
              {(gcData?.holdings || []).length === 0 ? (
                <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)'}}>
                  현재 V12 보유 종목이 없습니다.
                </div>
              ) : (
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>종목</th><th style={{textAlign:'center'}}>진입일</th><th style={{textAlign:'right'}}>매수가</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>수익률</th><th style={{textAlign:'right'}}>보유일</th></tr></thead>
                  <tbody>
                    {gcData.holdings.map((r)=>(
                      <tr key={r.stock_code}>
                        <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                        <td style={{textAlign:'center'}}>{r.entry_date||'-'}</td>
                        <td style={{textAlign:'right'}}>{r.buy_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right',color:(r.profit_pct||0)>=0?'#22c55e':'#ef4444'}}>{((r.profit_pct||0)).toFixed(2)}%</td>
                        <td style={{textAlign:'right'}}>{r.hold_days??'-'}일</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* 전략 설명 */}
            <div style={{margin:'0.5rem 0.8rem 0.8rem',padding:'0.9rem 1rem',borderRadius:'10px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.07)'}}>
              <div style={{fontSize:'0.75rem',fontWeight:700,color:'rgba(255,255,255,0.55)',marginBottom:'0.55rem',letterSpacing:'0.04em'}}>📋 V12 골든크로스 전략 로직</div>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.6rem 1.2rem',fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>백테스트 검증</div>
                  <div>성과 수치는 전략센터에서 선택된 run hash와 자동 검증 등급으로 확인합니다.</div>
                </div>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>진입 · 매도 조건</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.15rem'}}>
                    <span>· MA20이 MA60 상향돌파 (15일 이내)</span>
                    <span>· 거래량 5일평균 ÷ 20일평균 ≥ 1.2배</span>
                    <span>· RS6M(KOSPI 대비 6개월 상대강도) &gt; -20%</span>
                    <span>· 시총 4,000억원 이상 (중대형주, 2026-08-10 상향)</span>
                    <span>· Trail-25% / 대박 Trail-30% / 손절-12% / 최대 300일</span>
                    <span>· 총 1억원, 종목당 1,000만원, 최대 8종목</span>
                  </div>
                </div>
              </div>
              <div style={{marginTop:'0.5rem',paddingTop:'0.4rem',borderTop:'1px solid rgba(255,255,255,0.06)',fontSize:'0.68rem',color:'rgba(255,255,255,0.3)'}}>
                20분마다 장중 자동 실행 · 백테스트 성과는 전략센터 선택 run 기준
              </div>
            </div>
          </div>
        )
      )}

      {/* ══ V-RECOVERY 낙폭반등 가상매매 탭 ══ */}
      {screenTab === 'v_rec' && (
        recLoading ? (
          <div className="glass-panel" style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>V-RECOVERY 계산 중...</div>
        ) : (
          <div className="glass-panel" style={{overflow:'clip'}}>
            <div style={{padding:'0.7rem 1rem',display:'flex',justifyContent:'space-between',alignItems:'center',gap:'0.5rem',flexWrap:'wrap',borderBottom:'1px solid var(--glass-border)'}}>
              <div style={{fontSize:'0.8rem',color:'var(--text-secondary)',display:'flex',gap:'0.8rem',flexWrap:'wrap',alignItems:'center'}}>
                <span>마지막 계산: {recData?.updated_at || '-'}</span>
                <span>보유 {recData?.summary?.active_positions ?? 0}종목 · 매수후보 {recData?.summary?.buy_count ?? 0}종목 · 매도후보 {recData?.summary?.sell_count ?? 0}종목</span>
              </div>
              <div style={{display:'flex',gap:'0.5rem'}}>
                <button onClick={async()=>{
                  setRecLoading(true);
                  try{
                    const r=await fetch(API('/api/trend/rec/execute'),{method:'POST'});
                    if(r.ok){const d=await r.json();alert(`V-RECOVERY 실행완료: 매도 ${d.sold}건 · 매수 ${d.bought}건`);}
                  }catch(e){console.error(e);}
                  finally{setRecLoading(false);fetchRec();}
                }} style={{padding:'0.35rem 0.9rem',borderRadius:'6px',border:'none',background:'rgba(251,113,133,0.18)',color:'#fb7185',fontSize:'0.76rem',fontWeight:600,cursor:'pointer'}}>
                  ▶ 즉시 실행
                </button>
                <button onClick={fetchRec} style={{padding:'0.35rem 0.9rem',borderRadius:'6px',border:'1px solid rgba(255,255,255,0.1)',background:'transparent',color:'var(--text-secondary)',fontSize:'0.76rem',cursor:'pointer'}}>
                  새로고침
                </button>
              </div>
            </div>

            {(recData?.sell_candidates || []).length > 0 && (
              <div style={{padding:'0.6rem 0.8rem',borderBottom:'1px solid var(--glass-border)'}}>
                <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#ef4444'}}>🔴 매도 후보</div>
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>종목</th><th style={{textAlign:'center'}}>진입일</th><th style={{textAlign:'right'}}>매수가</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>수익률</th><th>사유</th></tr></thead>
                  <tbody>
                    {recData.sell_candidates.map((r)=>(
                      <tr key={r.stock_code}>
                        <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                        <td style={{textAlign:'center'}}>{r.entry_date||'-'}</td>
                        <td style={{textAlign:'right'}}>{r.buy_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right',color:(r.profit_pct||0)>=0?'#22c55e':'#ef4444'}}>{((r.profit_pct||0)).toFixed(2)}%</td>
                        <td style={{fontSize:'0.7rem',color:'rgba(239,68,68,0.8)'}}>{r.reason||'-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div style={{padding:'0.6rem 0.8rem',borderBottom:'1px solid var(--glass-border)'}}>
              <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#fb7185'}}>🩹 매수 후보 (낙폭과대 반등 감지)</div>
              {(recData?.buy_candidates || []).length === 0 ? (
                <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)'}}>
                  현재 반등 신호 없음 (MA60 -20~-65% 낙폭 + 52주저점 +40%이내 + 당일 거래량 2배 + 3일중 2일 상승)
                </div>
              ) : (
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>종목</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>MA60낙폭</th><th style={{textAlign:'right'}}>저점대비</th><th style={{textAlign:'right'}}>거래량</th><th style={{textAlign:'center'}}>흑자전환</th><th style={{textAlign:'center'}}>수급</th><th style={{textAlign:'right'}}>점수</th></tr></thead>
                  <tbody>
                    {(recData.buy_candidates).slice(0,15).map((r)=>(
                      <tr key={r.stock_code}>
                        <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                        <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right',color:'#ef4444'}}>{(r.depth_pct??0).toFixed(1)}%</td>
                        <td style={{textAlign:'right'}}>+{(r.pct_from_low??0).toFixed(1)}%</td>
                        <td style={{textAlign:'right'}}>×{(r.vol_x??0).toFixed(1)}</td>
                        <td style={{textAlign:'center'}}>{r.turnaround?<span style={{color:'#22c55e',fontWeight:700}}>●</span>:'-'}</td>
                        <td style={{textAlign:'center'}}>{r.flow?<span style={{color:'#60a5fa',fontWeight:700}}>◆</span>:'-'}</td>
                        <td style={{textAlign:'right',fontWeight:700}}>{(r.score??0).toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div style={{padding:'0.6rem 0.8rem',borderBottom:'1px solid var(--glass-border)'}}>
              <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'rgba(255,255,255,0.78)'}}>📋 보유중 V-RECOVERY 종목</div>
              {(recData?.holdings || []).length === 0 ? (
                <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)'}}>
                  현재 V-RECOVERY 보유 종목이 없습니다.
                </div>
              ) : (
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>종목</th><th style={{textAlign:'center'}}>진입일</th><th style={{textAlign:'right'}}>매수가</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>수익률</th><th style={{textAlign:'right'}}>보유일</th></tr></thead>
                  <tbody>
                    {recData.holdings.map((r)=>(
                      <tr key={r.stock_code}>
                        <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                        <td style={{textAlign:'center'}}>{r.entry_date||'-'}</td>
                        <td style={{textAlign:'right'}}>{r.buy_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                        <td style={{textAlign:'right',color:(r.profit_pct||0)>=0?'#22c55e':'#ef4444'}}>{((r.profit_pct||0)).toFixed(2)}%</td>
                        <td style={{textAlign:'right'}}>{r.hold_days??'-'}일</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div style={{margin:'0.5rem 0.8rem 0.8rem',padding:'0.9rem 1rem',borderRadius:'10px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.07)'}}>
              <div style={{fontSize:'0.75rem',fontWeight:700,color:'rgba(255,255,255,0.55)',marginBottom:'0.55rem',letterSpacing:'0.04em'}}>📋 V-RECOVERY 낙폭반등 전략 로직 (2026-07-12 흑자전환보너스 채택)</div>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.6rem 1.2rem',fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>백테스트 검증</div>
                  <div>성과 수치는 전략센터에서 선택된 run hash와 자동 검증 등급으로 확인합니다.</div>
                </div>
                <div>
                  <div style={{fontWeight:600,color:'rgba(255,255,255,0.6)',marginBottom:'0.25rem'}}>진입 · 매도 조건</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.15rem'}}>
                    <span>· MA60 대비 -20~-65% 낙폭 + 52주저점 +40% 이내</span>
                    <span>· 당일 거래량 ≥ 20일평균 ×2.0 + 3일중 2일 상승</span>
                    <span>· 흑자전환 +20pt ● / 기관+외인 5일 순매수 +20pt ◆</span>
                    <span>· Trail-20% / 대박 Trail-25% / 손절-12% / 익절+80% / 240일</span>
                    <span>· 총 1억원, 종목당 1,000만원, 최대 10종목, 회당 최대 3매수</span>
                  </div>
                </div>
              </div>
              <div style={{marginTop:'0.5rem',paddingTop:'0.4rem',borderTop:'1px solid rgba(255,255,255,0.06)',fontSize:'0.68rem',color:'rgba(255,255,255,0.3)'}}>
                20분마다 장중 자동 실행 · KOSPI &lt; MA120×0.85 패닉장은 신규매수 스킵
              </div>
            </div>
          </div>
        )
      )}

      {/* ══ 진입트리거 TOP20 탭 ══ */}
      {screenTab === 'trigger' && (() => {
        const fmtAmt = (v) => {
          if(v == null) return <span style={{color:'rgba(255,255,255,0.2)'}}>-</span>;
          const c = v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : '#94a3b8';
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
                const rising = curr != null && prev != null && Number(curr) > Number(prev);
                const falling = curr != null && prev != null && Number(curr) < Number(prev);
                const col = rising ? '#ef4444' : falling ? '#22c55e' : '#94a3b8';
                return (
                  <div key={label} title={`${label}평균: ${Math.round(curr||0).toLocaleString()}주`}
                    style={{display:'flex',flexDirection:'column',alignItems:'center',padding:'2px 4px',
                      borderRadius:'4px',background:`${col}18`,border:`1px solid ${col}44`,minWidth:'36px'}}>
                    <span style={{fontSize:'0.52rem',color:'rgba(255,255,255,0.4)'}}>{label}</span>
                    <span style={{fontSize:'0.62rem',fontWeight:700,color:col}}>{fmtBal(curr)}</span>
                    <span style={{fontSize:'0.55rem',color:col}}>{rising?'▲':falling?'▼':'●'}</span>
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

      {/* ══ 고수익 집중형 후보 탭 ══ */}
      {screenTab === 'high_profit' && (
        highProfitLoading ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid #22c55e',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>고수익 집중형 후보 스캔 중...</p>
          </div>
        ) : highProfitStocks.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>🏆</p>
            <p>현재 고수익 집중형 조건을 모두 충족하는 종목이 없습니다.</p>
            <p style={{fontSize:'0.8rem',marginTop:'0.4rem'}}>핵심 성장 섹터, 20일 거래대금 20억, 52주 고점 80%, 내부자 매수, 수주 신호를 동시에 봅니다.</p>
            <button onClick={() => fetchHighProfitCandidates(true)} style={{marginTop:'1rem',padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(34,197,94,0.15)',border:'1px solid rgba(34,197,94,0.3)',
              color:'#22c55e',cursor:'pointer',fontSize:'0.8rem'}}>다시 스캔</button>
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
            <div style={{padding:'0.6rem 1rem',background:'rgba(34,197,94,0.08)',
              border:'1px solid rgba(34,197,94,0.25)',borderRadius:'8px',
              display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.75rem',color:'#22c55e',fontWeight:700}}>
                🏆 고수익 집중형 후보 {highProfitStocks.length}종목
              </span>
              <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)'}}>
                핵심 성장 섹터 × 거래대금 20억↑ × 52주 고점 80%↑ × 내부자 매수 × 수주잔고/신규수주
              </span>
              <DlBtn onClick={() => downloadCSV(highProfitStocks, 'high_profit_candidates.csv')} />
              <button onClick={() => fetchHighProfitCandidates(true)} style={{marginLeft:'auto',padding:'0.25rem 0.7rem',
                borderRadius:'6px',background:'rgba(34,197,94,0.15)',border:'1px solid rgba(34,197,94,0.3)',
                color:'#22c55e',cursor:'pointer',fontSize:'0.72rem'}}>새로고침</button>
            </div>

            <div className="glass-panel" style={{overflow:'clip'}}>
              <table className="premium-table" style={{width:'100%',fontSize:'0.78rem'}}>
                <thead>
                  <tr>
                    <th>종목</th>
                    <th style={{textAlign:'center'}}>시장/섹터</th>
                    <th style={{textAlign:'right'}}>점수</th>
                    <th style={{textAlign:'right'}}>52주 위치</th>
                    <th style={{textAlign:'right'}}>20일 거래대금</th>
                    <th style={{textAlign:'right'}}>내부자 순매수</th>
                    <th style={{textAlign:'right'}}>수주잔고/매출</th>
                    <th style={{textAlign:'right'}}>부채비율</th>
                    <th>핵심 근거</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {highProfitStocks.map(s => {
                    const gradeColor = s.grade === 'Strong' ? '#22c55e' : s.grade === 'Buy' ? '#86efac' : '#fbbf24';
                    return (
                      <tr key={s.stock_code}>
                        <td>
                          <div style={{fontWeight:700}}>{s.stock_name}</div>
                          <div style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{s.stock_code}</div>
                        </td>
                        <td style={{textAlign:'center'}}>
                          <MktBadge market={s.market} mktcap={s.market_cap} />
                          <div style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.45)',marginTop:'2px'}}>{s.sector}{s.theme ? ` · ${s.theme}` : ''}</div>
                        </td>
                        <td style={{textAlign:'right'}}>
                          <span style={{padding:'0.12rem 0.5rem',borderRadius:'4px',fontWeight:700,
                            background:`${gradeColor}18`,color:gradeColor,border:`1px solid ${gradeColor}44`}}>
                            {s.score}
                          </span>
                        </td>
                        <td style={{textAlign:'right',color:'#22c55e',fontWeight:700}}>{s.near_high52_pct?.toFixed?.(1) ?? s.near_high52_pct}%</td>
                        <td style={{textAlign:'right'}}>{s.avg_turnover20_억?.toLocaleString?.() ?? s.avg_turnover20_억}억</td>
                        <td style={{textAlign:'right',color:'#a7f3d0'}}>{Math.round(s.insider_net_qty || 0).toLocaleString()}주</td>
                        <td style={{textAlign:'right'}}>{s.backlog_to_rev != null ? `${s.backlog_to_rev}배` : '-'}</td>
                        <td style={{textAlign:'right',color:(s.debt_ratio||0)>300?'#fbbf24':'rgba(255,255,255,0.65)'}}>
                          {s.debt_ratio != null ? `${s.debt_ratio}%` : '-'}
                        </td>
                        <td style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.55)',lineHeight:1.45}}>
                          {(s.reasons || []).slice(0,3).join(' · ')}
                        </td>
                        <td>
                          <button onClick={()=>{changeStock(s.stock_code);changeTab('analysis');}}
                            style={{padding:'0.2rem 0.45rem',borderRadius:'4px',border:'none',
                              background:'rgba(34,197,94,0.12)',color:'#22c55e',
                              cursor:'pointer',fontSize:'0.7rem'}}>분석↗</button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <LogicPanel metaKey="high_profit" accentColor="#22c55e" fallbackTitle="고수익 집중형 로직 원리" />
          </div>
        )
      )}

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
                      { label:'현재가', val: c.price ? fmtKrw(c.price) : '-' },
                      { label:'Graham 내재가', val: c.graham_iv ? fmtKrw(c.graham_iv) : '-', highlight: true },
                      { label:'Graham 할인율', val: c.discount != null ? c.discount.toFixed(1)+'%' : '-', highlight: true },
                      { label:'PBR', val: c.pbr != null ? c.pbr.toFixed(2) : '-' },
                      { label:'PER', val: c.per != null ? c.per.toFixed(1) : '-' },
                      { label:'EPS', val: c.eps ? fmtKrw(c.eps) : '-' },
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
                      {label:'현재가',    val: s.price ? fmtKrw(s.price) : '-'},
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
                        {label:'현재가', val: s.price ? fmtKrw(s.price) : '-'},
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
          {/* 로직 선택 드롭다운 (StrategyHub 내장 시 hideTabBar=true → 숨김) */}
          {!hideTabBar && <div style={{display:'flex',alignItems:'center',gap:'0.75rem',padding:'0.5rem 0.9rem',
            background:'rgba(0,0,0,0.25)',border:'1px solid rgba(255,255,255,0.07)',
            borderRadius:'10px',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.78rem',color:'rgba(255,255,255,0.5)',fontWeight:600,whiteSpace:'nowrap'}}>📐 로직 선택</span>
            <select
              value={comboLogic}
              onChange={e => {
                const v = e.target.value;
                setComboLogic(v);
                if (v === 'v2' && comboV2Data.length === 0) fetchComboV2();
                if (v === 'kiwoom' && !kiwoomCondData) fetchKiwoomCond();
              }}
              style={{padding:'0.3rem 0.7rem',borderRadius:'8px',fontSize:'0.8rem',
                background:'rgba(30,41,59,0.9)',color:'rgba(255,255,255,0.85)',
                border:'1px solid var(--glass-border)',cursor:'pointer',outline:'none',minWidth:'200px'}}
            >
              <option value="v1">⭐ v5 복합콤보 (AI 적극추천)</option>
              <option value="v2">📡 v4 수급모멘텀 (수급 주도)</option>
              <option value="kiwoom">🎯 키움조건식 (5가지 퀀트)</option>
            </select>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.3)'}}>
              {comboLogic==='v1' && 'v5: Minervini추세 + Graham가치 + 재무스크리너 3관왕 우선'}
              {comboLogic==='v2' && 'v4: 기관·외국인 동반순매수 × 추세 × 실적 복합스코어 (최대 42점)'}
              {comboLogic==='kiwoom' && '키움조건식: 가치우량주·수급폭발·성장저평가·신고가돌파·역발상저가'}
            </span>
          </div>}

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
                        <span style={{fontSize:'0.78rem',fontWeight:600}}>{fmtKrw(s.price)}</span>
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
                      <span style={{fontSize:'0.78rem',fontWeight:600}}>{fmtKrw(s.price)}</span>
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

          {/* ══ 키움조건식 렌더링 ══ */}
          {comboLogic === 'kiwoom' && (() => {
            const STRATS = [
              { key:'value_blue',      label:'🏦 가치우량주', color:'#22c55e',  desc:'저PBR·저PER·고ROE + 외국인 우호. 안정적 배당 기반 우량주.' },
              { key:'supply_momentum', label:'🚀 수급폭발',   color:'#818cf8',  desc:'기관+외국인 동반 순매수 + 거래량 급증. 단기 모멘텀 포착.' },
              { key:'growth_garp',     label:'📈 성장저평가', color:'#f59e0b',  desc:'매출 YoY 15%+ 고성장 + 합리적 PEG. 성장주이지만 저평가.' },
              { key:'high52_break',    label:'🎯 신고가돌파', color:'#ef4444',  desc:'52주 고점 8% 이내. 추세 최강 종목. 상승 돌파 직전.' },
              { key:'contrarian',      label:'🔄 역발상저가', color:'#06b6d4',  desc:'52주 저점 근처 + PBR<0.6 심각저평가. 기관 매집 시작 포착.' },
            ];
            const cur = STRATS.find(s => s.key === kiwoomCondTab) || STRATS[0];
            const stocks = (kiwoomCondData && kiwoomCondData[cur.key] && kiwoomCondData[cur.key].stocks) || [];
            const sigColors = { strong:'#ef4444', buy:'#22c55e', watch:'#f59e0b' };
            const sigEmojis = { strong:'🔥', buy:'⭐', watch:'👀' };

            // 전략별 표시할 메트릭 정의
            const metricDefs = {
              value_blue:      [['PBR','pbr'],['PER','per'],['ROE','roe'],['기관10일','inst_10d_억','억'],['외인10일','frn_10d_억','억']],
              supply_momentum: [['기관5일','inst_5d_억','억'],['외인5일','frn_5d_억','억'],['거래량비','vol_ratio','배'],['기관10일','inst_10d_억','억'],['외인10일','frn_10d_억','억']],
              growth_garp:     [['매출YoY','rev_yoy','%'],['ROE','roe','%'],['PEG유사','peg_like'],['PER','per'],['외인10일','frn_10d_억','억']],
              high52_break:    [['고점대비','pct_from_high52','%'],['거래량비','vol_ratio','배'],['기관5일','inst_5d_억','억'],['외인5일','frn_5d_억','억'],['PER','per']],
              contrarian:      [['PBR','pbr'],['저점대비','pct_from_low52','%'],['기관5일','inst_5d_억','억'],['외인5일','frn_5d_억','억'],['ROE','roe','%']],
            };

            return (<>
              {/* 안내 헤더 */}
              <div style={{padding:'0.7rem 1rem',
                background:'linear-gradient(135deg, rgba(245,158,11,0.06), rgba(251,191,36,0.04))',
                border:'1px solid rgba(245,158,11,0.3)',borderRadius:'8px',
                display:'flex',alignItems:'center',gap:'0.7rem',flexWrap:'wrap'}}>
                <span style={{fontSize:'0.85rem',color:'#f59e0b',fontWeight:700}}>🎯 키움조건식</span>
                <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)'}}>
                  키움증권 HTS 조건식 스타일 5가지 퀀트 전략 — 매일 자동 계산
                </span>
                {kiwoomCondData && (
                  <span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.35)',marginLeft:'auto'}}>
                    총 {Object.values(kiwoomCondData).reduce((a,v)=>a+(v.stocks||[]).length,0)}종목 선별
                  </span>
                )}
                <button onClick={() => fetchKiwoomCond(true)}
                  style={{padding:'0.2rem 0.6rem',borderRadius:'5px',border:'none',
                    background:'rgba(245,158,11,0.2)',color:'#f59e0b',cursor:'pointer',fontSize:'0.72rem'}}>
                  🔄 새로고침
                </button>
              </div>

              {/* 전략 탭 버튼 */}
              <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                {STRATS.map(s => {
                  const cnt = (kiwoomCondData && kiwoomCondData[s.key] && kiwoomCondData[s.key].stocks) ? kiwoomCondData[s.key].stocks.length : 0;
                  const isAct = kiwoomCondTab === s.key;
                  return (
                    <button key={s.key} onClick={() => setKiwoomCondTab(s.key)}
                      style={{padding:'0.3rem 0.75rem',borderRadius:'7px',fontSize:'0.78rem',cursor:'pointer',
                        fontWeight: isAct ? 700 : 400, border:`1px solid ${isAct ? s.color : 'rgba(255,255,255,0.1)'}`,
                        background: isAct ? `${s.color}22` : 'transparent',
                        color: isAct ? s.color : 'rgba(255,255,255,0.5)',transition:'all 0.15s'}}>
                      {s.label} {cnt > 0 && <span style={{fontSize:'0.68rem',opacity:0.7}}>({cnt})</span>}
                    </button>
                  );
                })}
              </div>

              {/* 현재 전략 설명 */}
              <div style={{padding:'0.5rem 0.9rem',borderRadius:'7px',fontSize:'0.72rem',
                background:`${cur.color}08`,border:`1px solid ${cur.color}25`,
                color:'rgba(255,255,255,0.55)'}}>
                <strong style={{color:cur.color}}>{cur.label}</strong> — {cur.desc}
              </div>

              {/* 종목 카드 */}
              {kiwoomCondLoading && !kiwoomCondData ? (
                <div style={{padding:'2rem',textAlign:'center',color:'rgba(255,255,255,0.4)'}}>
                  <p>🎯 키움조건식 분석 중... (최초 실행 시 30초 소요)</p>
                </div>
              ) : stocks.length === 0 ? (
                <div className="glass-panel" style={{padding:'2.5rem',textAlign:'center'}}>
                  <p style={{fontSize:'1.5rem',marginBottom:'0.5rem'}}>{cur.label.split(' ')[0]}</p>
                  <p style={{color:'var(--text-secondary)',fontSize:'0.85rem'}}>조건에 맞는 종목이 없습니다.</p>
                  <button onClick={() => fetchKiwoomCond()} style={{marginTop:'1rem',padding:'0.5rem 1.2rem',
                    borderRadius:'8px',border:'none',background:cur.color,color:'#fff',
                    cursor:'pointer',fontWeight:700}}>
                    🔄 분석 실행
                  </button>
                </div>
              ) : stocks.map(s => {
                const sc = sigColors[s.signal] || '#94a3b8';
                const em = sigEmojis[s.signal] || '📌';
                const metrics = metricDefs[cur.key] || [];
                return (
                  <div key={s.stock_code} style={{padding:'0.85rem 1rem',borderRadius:'10px',
                    background:`${sc}08`,border:`1px solid ${sc}30`}}>
                    {/* 헤더 */}
                    <div style={{display:'flex',alignItems:'center',gap:'0.45rem',marginBottom:'0.55rem',flexWrap:'wrap'}}>
                      <span style={{fontSize:'0.9rem'}}>{em}</span>
                      <span style={{fontWeight:700,fontSize:'0.92rem'}}>{s.stock_name || s.stock_code}</span>
                      <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{s.stock_code}</span>
                      <MktBadge market={s.market} mktcap={s.market_cap_억} />
                      <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:700,
                        background:`${sc}22`,color:sc,border:`1px solid ${sc}50`}}>
                        {s.signal==='strong'?'강력추천':s.signal==='buy'?'추천':'관심'}
                      </span>
                      {/* 점수 + 분석 */}
                      <div style={{marginLeft:'auto',display:'flex',gap:'0.35rem',alignItems:'center'}}>
                        <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.72rem',
                          background:`${cur.color}18`,color:cur.color,fontWeight:700}}>
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
                    {/* 핵심 지표 칩 */}
                    <div style={{display:'flex',gap:'0.4rem',flexWrap:'wrap',marginBottom:'0.45rem'}}>
                      <div style={{textAlign:'center',minWidth:'64px',padding:'0.2rem 0.4rem',
                        borderRadius:'5px',background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.07)'}}>
                        <div style={{fontSize:'0.57rem',color:'var(--text-secondary)',marginBottom:'0.1rem'}}>현재가</div>
                        <div style={{fontSize:'0.74rem',fontWeight:600}}>{(s.current_price||0).toLocaleString('ko-KR')}</div>
                      </div>
                      <div style={{textAlign:'center',minWidth:'58px',padding:'0.2rem 0.4rem',
                        borderRadius:'5px',background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.07)'}}>
                        <div style={{fontSize:'0.57rem',color:'var(--text-secondary)',marginBottom:'0.1rem'}}>시총</div>
                        <div style={{fontSize:'0.74rem',fontWeight:600}}>{s.market_cap_억 > 10000 ? (s.market_cap_억/10000).toFixed(1)+'조' : s.market_cap_억+'억'}</div>
                      </div>
                      {metrics.map(([lbl, field, unit='']) => {
                        const val = s[field];
                        if (val == null) return null;
                        const disp = typeof val === 'number'
                          ? (Math.abs(val) >= 100 ? Math.round(val) : val.toFixed(1)) + unit
                          : val + unit;
                        const vcolor = (field==='pct_from_high52' && val >= -3) ? '#22c55e'
                          : (field==='pct_from_low52' && val <= 10) ? '#ef4444'
                          : (field.includes('inst') || field.includes('frn')) ? (val > 0 ? '#34d399' : '#f87171')
                          : 'var(--text-primary)';
                        return (
                          <div key={field} style={{textAlign:'center',minWidth:'54px',padding:'0.2rem 0.4rem',
                            borderRadius:'5px',background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.07)'}}>
                            <div style={{fontSize:'0.57rem',color:'var(--text-secondary)',marginBottom:'0.1rem'}}>{lbl}</div>
                            <div style={{fontSize:'0.74rem',fontWeight:600,color:vcolor}}>{disp}</div>
                          </div>
                        );
                      })}
                    </div>
                    {/* 이유 태그 */}
                    <div style={{display:'flex',gap:'0.25rem',flexWrap:'wrap'}}>
                      {(s.reasons||[]).slice(0,6).map((r,i) => (
                        <span key={i} style={{padding:'0.1rem 0.38rem',borderRadius:'4px',fontSize:'0.64rem',
                          background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.6)',
                          border:'1px solid rgba(255,255,255,0.08)'}}>
                          {r}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}

              {/* 전략 설명 박스 */}
              <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
                background:'rgba(245,158,11,0.02)',border:'1px solid rgba(245,158,11,0.1)',
                borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.55)',lineHeight:1.9}}>
                <div style={{fontWeight:700,color:'#f59e0b',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
                  🎯 키움조건식 — 5가지 전략 원리
                </div>
                <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))',gap:'0.5rem 1.2rem'}}>
                  {[
                    ['🏦 가치우량주',  'PBR<1.0 + PER<15 + ROE≥8% + MA60 유지. 외국인 순매수 선호. 안정적 우량주 장기보유 전략.'],
                    ['🚀 수급폭발',    '기관+외국인 5일 합산 순매수 ≥20억. MA20>MA60 추세 확인. 단기 급등 모멘텀 포착.'],
                    ['📈 성장저평가',  '최근 분기 매출 YoY ≥15% + ROE ≥10%. PEG 유사값(PER÷성장률) 낮을수록 고점수.'],
                    ['🎯 신고가돌파',  '52주 고점 -8% 이내 근접. 거래량 급증 + 기관·외인 수급 양호. 신고가 돌파 임박 포착.'],
                    ['🔄 역발상저가',  'PBR<0.6 심각 저평가 + 52주 저점 25% 이내. 기관 순매수 양전환 시 반등 기대.'],
                    ['공통 필터',      '시총 500억~1000억 이상 (전략별 상이). 지수·ETF·선물 제외. 일별 자동 재계산.'],
                  ].map(([t,d]) => (
                    <div key={t}>
                      <span style={{color:'#f59e0b',fontWeight:600}}>{t}</span>
                      <span style={{color:'rgba(255,255,255,0.45)'}}> — {d}</span>
                    </div>
                  ))}
                </div>
              </div>
            </>);
          })()}

        </div>
        );
      })()}

    </div>
    );
  };

export default Screener;
