import React from 'react';
import { TrendingUp } from 'lucide-react';

const API = (path) => path;

const Screener = ({ changeTab = () => {}, changeStock = () => {} }) => {
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
  }, [screenTab]);

  // 초기(combo) 로드
  React.useEffect(() => { fetchCombo(); }, []);

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
    // 서버에서 사전계산된 데이터가 있으면 우선 사용
    if (comboFromServer.length > 0) return comboFromServer;
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
          {tabBtn('combo', `⭐ AI 적극 검토`, comboStocks.length)}
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
        <div className="glass-panel" style={{overflow:'auto'}}>
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
                      {s.change_pct>=0?'+':''}{s.change_pct?.toFixed(2)}%
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
            ★AI = Track A(추세) + Track B(가치) 동시 충족 OR 재무스크리너 포함 종목 → AI 적극검토 후보
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

    {/* ══ AI 적극 검토 탭 ══ */}
    {screenTab === 'combo' && (() => {
      const filteredCombo = comboFilter === 'triple'
        ? comboStocks.filter(s => s.match_count >= 3)
        : comboStocks;
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
              { key:'v1', label:'Logic-#1', desc:'Minervini 추세 + Graham 가치 복합 스크리너' },
              { key:'v2', label:'Logic-#2', desc:'수급 주도 모멘텀 (기관+외국인 동반매수)' },
            ].map(opt => (
              <button key={opt.key} title={opt.desc} onClick={() => {
                  setComboLogic(opt.key);
                  if (opt.key === 'v2' && comboV2Data.length === 0) fetchComboV2();
                }}
                style={{padding:'0.25rem 0.9rem',borderRadius:'6px',fontSize:'0.78rem',cursor:'pointer',
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
              ? 'Minervini 추세 + Graham 가치 + 재무스크리너 교집합'
              : '수급 주도 모멘텀 — 기관·외국인 동반순매수 + 추세 + 실적'}
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
            ⭐ Logic-#1 AI 적극 검토 — {filteredCombo.length}종목
          </span>
          {/* 필터 선택 버튼 */}
          <div style={{display:'flex',gap:'0.3rem',background:'rgba(0,0,0,0.25)',
            borderRadius:'8px',padding:'0.2rem'}}>
            {[
              { key:'all',    label:'2개↑ 충족', desc:'추세·가치·재무 중 2개 이상' },
              { key:'triple', label:'🏆 3개 모두', desc:'추세·가치·재무 모두 충족' },
            ].map(opt => (
              <button key={opt.key} onClick={() => setComboFilter(opt.key)} title={opt.desc}
                style={{padding:'0.2rem 0.65rem',borderRadius:'6px',fontSize:'0.72rem',
                  cursor:'pointer',fontWeight: comboFilter===opt.key ? 700 : 400,
                  border:'none',
                  background: comboFilter===opt.key
                    ? (opt.key==='triple' ? 'rgba(239,68,68,0.35)' : 'rgba(245,158,11,0.3)')
                    : 'transparent',
                  color: comboFilter===opt.key ? '#fff' : 'rgba(255,255,255,0.45)',
                  transition:'all 0.15s',
                }}>
                {opt.label}
                <span style={{marginLeft:'0.3rem',opacity:0.7,fontSize:'0.65rem'}}>
                  ({opt.key==='triple'
                    ? comboStocks.filter(s=>s.match_count>=3).length
                    : comboStocks.length}개)
                </span>
              </button>
            ))}
          </div>
          <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
            {comboFilter==='triple'
              ? '추세추종 + 가치매수 + 재무스크리너 3개 전부 충족'
              : '추세추종 + 가치매수 + 재무스크리너 중 2개 이상 충족'}
          </span>
          <DlBtn onClick={() => downloadCSV(filteredCombo, 'ai_combo.csv')} />
        </div>

        {filteredCombo.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>
              {comboFilter==='triple' ? '🏆' : '⭐'}
            </p>
            <p>
              {comboFilter==='triple'
                ? '현재 3개 카테고리를 모두 충족하는 종목이 없습니다.'
                : '현재 2개 이상 카테고리를 동시 충족하는 종목이 없습니다.'}
            </p>
            <p style={{fontSize:'0.78rem',marginTop:'0.4rem',color:'rgba(255,255,255,0.35)'}}>
              {comboFilter==='triple'
                ? '"2개↑ 충족" 으로 전환하면 더 많은 종목을 볼 수 있습니다.'
                : '가치매수·재무스크리너·추세 탭을 각각 로드한 후 다시 확인해 주세요.'}
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

        {/* ── AI 적극 검토 로직 설명 ── */}
        <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
          background:'rgba(239,68,68,0.03)',border:'1px solid rgba(239,68,68,0.12)',
          borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
          <div style={{fontWeight:700,color:'#ef4444',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
            ⭐ Logic-#1 선정 원리
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
            {[
              ['교집합 원리','추세추종(주봉선·섹터·정배열), 가치매수(Graham·PBR·PER), 재무스크리너(성장기울기·턴어라운드) 3개 카테고리 중 2개 이상에서 독립적으로 조건을 충족한 종목만 수록.'],
              ['확률의 곱셈','개별 카테고리 발굴 확률 50%가 2개 독립 조건을 동시 충족하면 25%로 좁혀짐. 이 구간이 가장 높은 확률의 매수 구간.'],
              ['3관왕 (🏆)','3개 카테고리 모두 충족 = 추세 + 저평가 + 실적 개선. 극히 드문 경우로 최우선 검토 대상. 단, 소형주·유동성 위험은 별도 확인.'],
              ['동적 업데이트','각 탭(추세·가치·재무) 데이터를 로드하면 자동으로 교집합이 재계산됨. 새로고침 없이 실시간 반영.'],
            ].map(([title, desc]) => (
              <div key={title}>
                <span style={{color:'rgba(239,68,68,0.8)',fontWeight:600}}>{title}</span>
                <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
              </div>
            ))}
          </div>
        </div>
        </>)}

        {/* ── Logic-#2: 수급 주도 모멘텀 ── */}
        {comboLogic === 'v2' && (<>
        <div style={{padding:'0.7rem 1rem',
          background:'linear-gradient(135deg, rgba(99,102,241,0.08), rgba(45,212,191,0.06))',
          border:'1px solid rgba(99,102,241,0.35)',borderRadius:'8px',
          display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
          <span style={{fontSize:'0.8rem',color:'#818cf8',fontWeight:700}}>
            🔥 Logic-#2 수급 주도 모멘텀 — {comboV2Data.length}종목
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
            <p>Logic-#2 계산 중... (최초 실행 시 1~2분 소요)</p>
          </div>
        ) : comboV2Data.length === 0 ? (
          <div className="glass-panel" style={{padding:'2.5rem',textAlign:'center'}}>
            <p style={{fontSize:'1.5rem',marginBottom:'0.5rem'}}>📡</p>
            <p style={{color:'var(--text-secondary)',fontSize:'0.85rem'}}>수급 데이터 분석 결과가 없습니다.</p>
            <button onClick={fetchComboV2} style={{marginTop:'1rem',padding:'0.5rem 1.2rem',
              borderRadius:'8px',border:'none',background:'#818cf8',color:'#fff',
              cursor:'pointer',fontWeight:700}}>
              🔥 Logic-#2 분석 실행
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

        {/* Logic-#2 원리 설명 */}
        {comboV2Data.length > 0 && (
        <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
          background:'rgba(99,102,241,0.03)',border:'1px solid rgba(99,102,241,0.12)',
          borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
          <div style={{fontWeight:700,color:'#818cf8',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
            📡 Logic-#2 수급 주도 모멘텀 — 로직 원리
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
            {[
              ['Track S — 수급 (×3, 최대18점)', '기관+외국인 3일 연속 동반매수(최고등급), 단독 5일, 10일 누적금액 기준으로 수급 강도를 점수화. 가장 높은 가중치.'],
              ['Track T — 추세 (×2, 최대12점)', '일봉 정배열(MA5>20>60>120>240) 4점 + 주봉 MA40/MA80 기준 주봉 정배열 2점. 단기·중기 동시 추세 확인.'],
              ['Track Q — 실적 (×2, 최대8점)', '최근 분기 YoY 영업이익 성장이 연속으로 유지될수록 가산. 턴어라운드 및 지속 성장주 포착.'],
              ['Track R — 상대강도 (×1, 최대4점)', '21일·63일·126일 3구간에서 KOSPI 대비 초과상승 여부 평가. 3구간 모두 우세 시 최고점.'],
              ['강력추천 조건', '총점 28점 이상 + 수급점수 12점↑ + 추세점수 8점↑. 수급과 추세가 동시에 강한 경우만 선정.'],
              ['시장 필터', 'KOSPI MA60 하락장 진입 시 모든 점수 ×0.75 패널티. 하락장에서는 높은 점수에도 자동 등급 하향.'],
            ].map(([title, desc]) => (
              <div key={title}>
                <span style={{color:'rgba(129,140,248,0.9)',fontWeight:600}}>{title}</span>
                <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
              </div>
            ))}
          </div>
        </div>
        )}
        </>)}

      </div>
      );
    })()}

  </div>
  );
};

// ── Peak 전략 뷰 (독립 컴포넌트) ─────────────────────────────

export default Screener;
