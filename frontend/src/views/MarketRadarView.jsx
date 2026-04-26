import React from 'react';

const API = (path) => path;

const useIsMobile = () => {
  const [isMobile, setIsMobile] = React.useState(window.innerWidth < 768);
  React.useEffect(() => {
    const h = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', h);
    return () => window.removeEventListener('resize', h);
  }, []);
  return isMobile;
};

const MarketRadarView = () => {
  const SECTOR_KEYS = [
    { key: 'semiconductor', name: '반도체',     emoji: '💾' },
    { key: 'battery',       name: '2차전지',    emoji: '🔋' },
    { key: 'power_infra',   name: '전력인프라',  emoji: '⚡' },
    { key: 'pharma',        name: '제약/바이오', emoji: '💊' },
    { key: 'defense',       name: 'K방산/우주',  emoji: '🚀' },
    { key: 'shipbuilding',  name: '조선/해운',   emoji: '🚢' },
    { key: 'energy',        name: '에너지/소재', emoji: '⛽' },
  ];
  const SIG = {
    green:       { color: '#22c55e', label: '🟢 매수 우호',  bg: 'rgba(34,197,94,0.13)' },
    yellow_up:   { color: '#fbbf24', label: '🟡 약 상승',    bg: 'rgba(251,191,36,0.13)' },
    neutral:     { color: '#94a3b8', label: '⚪ 중립',        bg: 'rgba(148,163,184,0.10)' },
    yellow_down: { color: '#f97316', label: '🟠 약 하락',    bg: 'rgba(249,115,22,0.13)' },
    red:         { color: '#ef4444', label: '🔴 하락 경계',  bg: 'rgba(239,68,68,0.13)' },
  };
  const FLAG = { US:'🇺🇸', JP:'🇯🇵', TW:'🇹🇼', CN:'🇨🇳', KR:'🇰🇷' };

  const [sector, setSector]       = React.useState('semiconductor');
  const [level, setLevel]         = React.useState(1);           // 1 or 2
  const [l1Data, setL1Data]       = React.useState(null);
  const [l2Data, setL2Data]       = React.useState(null);
  const [allSigs, setAllSigs]     = React.useState({});
  const [loading, setLoading]     = React.useState(false);
  const [tooltip, setTooltip]     = React.useState(null);       // {text,x,y}

  // 전체 섹터 시그널 로드 (탭 뱃지용)
  React.useEffect(() => {
    fetch(API('/api/market-radar/all'))
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.sectors) {
          const m = {};
          d.sectors.forEach(s => { m[s.key] = s; });
          setAllSigs(m);
        }
      });
  }, []);

  // 섹터/레벨 변경 시 데이터 로드
  React.useEffect(() => {
    setLoading(true);
    setL1Data(null); setL2Data(null);
    const url = level === 1
      ? `/api/market-radar/sector/${sector}`
      : `/api/market-radar/sector/${sector}/detail`;
    fetch(API(url))
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (level === 1) setL1Data(d);
        else             setL2Data(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [sector, level]);

  // 툴팁 외부 클릭 시 닫기
  React.useEffect(() => {
    const close = () => setTooltip(null);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, []);

  const fmtChg = (v) => {
    if (v == null) return <span style={{color:'#4b5563'}}>-</span>;
    const s = v > 0 ? '+' : '';
    return <span style={{color: v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : '#64748b', fontWeight:600}}>{s}{v.toFixed(2)}%</span>;
  };
  const fmtPrice = (v, country) => {
    if (v == null || v === 0) return <span style={{color:'#4b5563'}}>-</span>;
    const pfx = country === 'KR' ? '₩' : '$';
    return `${pfx}${country === 'KR' ? Math.round(v).toLocaleString() : v.toLocaleString()}`;
  };
  const fmtMktcap = (v) => {
    if (!v) return '-';
    if (v >= 1e12) return `${(v/1e12).toFixed(1)}조`;
    if (v >= 1e8)  return `${Math.round(v/1e8)}억`;
    return `${Math.round(v/1e6)}백만`;
  };
  const sigStyle = (key) => SIG[key] || SIG.neutral;
  const dot = (key) => {
    const c = sigStyle(key).color;
    return <span style={{width:'8px',height:'8px',borderRadius:'50%',background:c,display:'inline-block',flexShrink:0}} />;
  };

  // 종목명 툴팁 트리거
  const NameCell = ({ name, desc, country, symbol }) => {
    if (!desc) return <span style={{fontSize:'0.82rem'}}>{name}</span>;
    return (
      <span
        style={{cursor:'help',borderBottom:'1px dotted rgba(148,163,184,0.5)',fontSize:'0.82rem'}}
        onMouseEnter={(e) => {
          e.stopPropagation();
          setTooltip({ text: desc, x: e.clientX, y: e.clientY });
        }}
        onMouseLeave={() => setTooltip(null)}
        onClick={(e) => {
          e.stopPropagation();
          setTooltip(t => t?.text === desc ? null : { text: desc, x: e.clientX, y: e.clientY });
        }}
      >{name}</span>
    );
  };

  const fmtNum = (v, decimals=1) => v == null ? <span style={{color:'#4b5563'}}>-</span> : v.toFixed(decimals);
  const fmtMktcapUSD = (v) => {
    if (!v) return <span style={{color:'#4b5563'}}>-</span>;
    if (v >= 1e12) return `$${(v/1e12).toFixed(2)}T`;
    if (v >= 1e9)  return `$${(v/1e9).toFixed(1)}B`;
    return `$${(v/1e6).toFixed(0)}M`;
  };

  // 공통 종목 테이블 렌더 (LEVEL 2용 — 해외: 시총/PER/PBR 추가)
  const StockTable = ({ stocks }) => {
    const hasOverseas = stocks.some(s => s.country !== 'KR');
    const headers = hasOverseas
      ? ['국가','심볼','종목명','현재가','전일','주간','시총','PER','PBR']
      : ['국가','심볼','종목명','현재가','전일','주간'];
    const leftCols = new Set(['국가','심볼','종목명']);
    return (
      <div style={{overflowX:'auto',WebkitOverflowScrolling:'touch'}}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:isMobile?'0.73rem':'0.82rem',minWidth: isMobile ? '480px' : '100%'}}>
          <thead>
            <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
              {headers.map(h=>(
                <th key={h} style={{padding:'0.35rem 0.45rem',textAlign:leftCols.has(h)?'left':'right',
                  color:'var(--text-secondary)',fontWeight:600,whiteSpace:'nowrap'}}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stocks.map((s,i)=>(
              <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                <td style={{padding:'0.3rem 0.45rem'}}>{FLAG[s.country]||s.country}</td>
                <td style={{padding:'0.3rem 0.45rem',fontWeight:700,color:'var(--accent-mint)',whiteSpace:'nowrap'}}>{s.symbol}</td>
                <td style={{padding:'0.3rem 0.45rem'}}>
                  <NameCell name={s.name} desc={s.desc} country={s.country} symbol={s.symbol} />
                </td>
                <td style={{padding:'0.3rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtPrice(s.price, s.country)}</td>
                <td style={{padding:'0.3rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtChg(s.chg_1d)}</td>
                <td style={{padding:'0.3rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtChg(s.chg_1w)}</td>
                {hasOverseas && <>
                  <td style={{padding:'0.3rem 0.45rem',textAlign:'right',whiteSpace:'nowrap',fontSize:'0.75rem'}}>
                    {s.country !== 'KR' ? fmtMktcapUSD(s.market_cap) : '-'}
                  </td>
                  <td style={{padding:'0.3rem 0.45rem',textAlign:'right',whiteSpace:'nowrap',fontSize:'0.75rem'}}>
                    {s.country !== 'KR' && s.per ? fmtNum(s.per) : '-'}
                  </td>
                  <td style={{padding:'0.3rem 0.45rem',textAlign:'right',whiteSpace:'nowrap',fontSize:'0.75rem'}}>
                    {s.country !== 'KR' && s.pbr ? fmtNum(s.pbr) : '-'}
                  </td>
                </>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  // 시그널 배지 헤더
  const SignalHeader = ({ data, labelKey, avgKey }) => {
    const sig = sigStyle(data?.signal);
    const avg = data?.[avgKey ?? 'overseas_avg_1d'];
    return (
      <div className="glass-panel" style={{padding:'0.8rem 1rem',display:'flex',alignItems:'center',gap:'1rem',flexWrap:'wrap',marginBottom:0}}>
        <div style={{flex:1}}>
          <span style={{fontSize:isMobile?'1rem':'1.1rem',fontWeight:800}}>{data?.emoji||''} {data?.sector_name||data?.name||''}</span>
          <span style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginLeft:'0.6rem'}}>5분 캐시</span>
        </div>
        <div style={{display:'flex',alignItems:'center',gap:'0.75rem',flexWrap:'wrap'}}>
          <div style={{padding:'0.35rem 0.9rem',borderRadius:'8px',background:sig.bg,border:`1px solid ${sig.color}`,
            fontSize:'0.88rem',fontWeight:700,color:sig.color,whiteSpace:'nowrap'}}>{sig.label}</div>
          <div style={{textAlign:'center'}}>
            <div style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>평균 1D</div>
            <div style={{fontWeight:700}}>{fmtChg(avg)}</div>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div style={{display:'flex',flexDirection:'column',gap:'0.75rem',position:'relative'}}>
      {/* 툴팁 오버레이 */}
      {tooltip && (
        <div style={{
          position:'fixed', top: tooltip.y+14, left: Math.min(tooltip.x, window.innerWidth-260),
          background:'#1e293b', border:'1px solid var(--glass-border)',
          padding:'0.5rem 0.8rem', borderRadius:'8px', fontSize:'0.78rem',
          color:'var(--text-primary)', zIndex:9999, maxWidth:'250px',
          boxShadow:'0 4px 20px rgba(0,0,0,0.4)', pointerEvents:'none',
        }}>{tooltip.text}</div>
      )}

      {/* 섹터 탭 — 모바일에서 수평 스크롤 */}
      <div style={{overflowX:'auto',WebkitOverflowScrolling:'touch',paddingBottom:'2px'}}>
        <div style={{display:'flex',gap:'0.35rem',flexWrap:'nowrap',minWidth:'max-content'}}>
          {SECTOR_KEYS.map(s => {
            const sc = allSigs[s.key] ? sigStyle(allSigs[s.key].signal).color : 'transparent';
            const active = sector === s.key;
            return (
              <button key={s.key} onClick={() => setSector(s.key)} style={{
                padding: isMobile ? '0.35rem 0.6rem' : '0.4rem 0.85rem',
                borderRadius:'8px', border:'none', cursor:'pointer',
                fontSize: isMobile ? '0.78rem' : '0.82rem', fontWeight:600,
                background: active ? 'var(--accent-mint)' : 'var(--glass-bg)',
                color: active ? '#000' : 'var(--text-primary)',
                display:'flex', alignItems:'center', gap:'0.3rem', whiteSpace:'nowrap',
              }}>
                <span>{s.emoji}</span>
                {!isMobile && <span>{s.name}</span>}
                {isMobile && <span style={{fontSize:'0.7rem'}}>{s.name}</span>}
                <span style={{width:'6px',height:'6px',borderRadius:'50%',background:sc,display:'inline-block'}} />
              </button>
            );
          })}
        </div>
      </div>

      {/* Level 토글 */}
      <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
        {[1,2].map(l => (
          <button key={l} onClick={() => setLevel(l)} style={{
            padding:'0.3rem 0.9rem', borderRadius:'6px', border:'none', cursor:'pointer',
            fontSize:'0.8rem', fontWeight:600,
            background: level===l ? 'var(--accent-mint)' : 'var(--glass-bg)',
            color: level===l ? '#000' : 'var(--text-secondary)',
          }}>LEVEL {l}</button>
        ))}
        <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
          {level===1 ? '해외 선행지표 요약' : '서브섹터별 세부 종목'}
        </span>
      </div>

      {loading && (
        <div style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)',fontSize:'0.85rem'}}>
          <div style={{marginBottom:'0.5rem'}}>⏳ 데이터 로딩 중…</div>
          <div style={{fontSize:'0.75rem'}}>yfinance 해외 시세 조회 중 (약 5~20초)</div>
        </div>
      )}

      {/* ──── LEVEL 1 ──── */}
      {!loading && level === 1 && l1Data && (
        <>
          <SignalHeader data={l1Data} avgKey="overseas_avg_1d" />

          <div className="glass-panel" style={{padding:'1rem'}}>
            <div style={{fontSize:'0.85rem',fontWeight:700,marginBottom:'0.6rem'}}>🌐 해외 선행지표 종목</div>
            <div style={{overflowX:'auto',WebkitOverflowScrolling:'touch'}}>
              <table style={{width:'100%',borderCollapse:'collapse',fontSize:isMobile?'0.75rem':'0.82rem',minWidth:isMobile?'400px':'100%'}}>
                <thead>
                  <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
                    {['국가','심볼','종목명','현재가','전일','주간'].map(h=>(
                      <th key={h} style={{padding:'0.35rem 0.45rem',textAlign:['국가','심볼','종목명'].includes(h)?'left':'right',
                        color:'var(--text-secondary)',fontWeight:600,whiteSpace:'nowrap'}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {l1Data.overseas.map((s,i)=>(
                    <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                      <td style={{padding:'0.28rem 0.45rem'}}>{FLAG[s.country]||s.country}</td>
                      <td style={{padding:'0.28rem 0.45rem',fontWeight:700,color:'var(--accent-mint)',whiteSpace:'nowrap'}}>{s.symbol}</td>
                      <td style={{padding:'0.28rem 0.45rem',color:'var(--text-secondary)'}}>{s.name}</td>
                      <td style={{padding:'0.28rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtPrice(s.price,s.country)}</td>
                      <td style={{padding:'0.28rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtChg(s.chg_1d)}</td>
                      <td style={{padding:'0.28rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtChg(s.chg_1w)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="glass-panel" style={{padding:'1rem'}}>
            <div style={{fontSize:'0.85rem',fontWeight:700,marginBottom:'0.6rem'}}>🇰🇷 국내 섹터 종목 (시총 상위)</div>
            {l1Data.korean.length === 0
              ? <p style={{color:'var(--text-secondary)',fontSize:'0.82rem'}}>해당 섹터 종목 없음</p>
              : (
                <div style={{overflowX:'auto',WebkitOverflowScrolling:'touch'}}>
                  <table style={{width:'100%',borderCollapse:'collapse',fontSize:isMobile?'0.75rem':'0.82rem',minWidth:isMobile?'380px':'100%'}}>
                    <thead>
                      <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
                        {['코드','종목명','섹터','시총','현재가','전일'].map(h=>(
                          <th key={h} style={{padding:'0.35rem 0.45rem',textAlign:['코드','종목명','섹터'].includes(h)?'left':'right',
                            color:'var(--text-secondary)',fontWeight:600,whiteSpace:'nowrap'}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {l1Data.korean.map((s,i)=>(
                        <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                          <td style={{padding:'0.28rem 0.45rem',fontWeight:700,color:'var(--accent-mint)',whiteSpace:'nowrap'}}>{s.stock_code}</td>
                          <td style={{padding:'0.28rem 0.45rem'}}>{s.stock_name}</td>
                          <td style={{padding:'0.28rem 0.45rem',color:'var(--text-secondary)',fontSize:'0.72rem'}}>{s.sector}</td>
                          <td style={{padding:'0.28rem 0.45rem',textAlign:'right',fontSize:'0.75rem'}}>{fmtMktcap(s.mktcap)}</td>
                          <td style={{padding:'0.28rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtPrice(s.close,'KR')}</td>
                          <td style={{padding:'0.28rem 0.45rem',textAlign:'right',whiteSpace:'nowrap'}}>{fmtChg(s.chg_1d)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </div>
        </>
      )}

      {/* ──── LEVEL 2 ──── */}
      {!loading && level === 2 && l2Data && (
        <>
          {/* 섹터 전체 시그널 */}
          <div className="glass-panel" style={{padding:'0.75rem 1rem',display:'flex',alignItems:'center',gap:'1rem',flexWrap:'wrap'}}>
            <span style={{fontSize:'1rem',fontWeight:800}}>{l2Data.emoji} {l2Data.sector_name}</span>
            <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
              {dot(l2Data.signal)}
              <span style={{fontSize:'0.85rem',fontWeight:700,color:sigStyle(l2Data.signal).color}}>
                {sigStyle(l2Data.signal).label}
              </span>
            </div>
            <span style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>
              전체 평균 1D: <strong>{l2Data.avg_1d > 0 ? '+' : ''}{l2Data.avg_1d?.toFixed(2)}%</strong>
            </span>
          </div>

          {/* 서브섹터 카드 */}
          {l2Data.subsectors?.map((ss, si) => (
            <div key={si} className="glass-panel" style={{padding:'0.85rem 1rem'}}>
              {/* 서브섹터 헤더 */}
              <div style={{display:'flex',alignItems:'center',gap:'0.6rem',marginBottom:'0.6rem',flexWrap:'wrap'}}>
                {dot(ss.signal)}
                <span style={{fontWeight:700,fontSize:'0.9rem'}}>{ss.name}</span>
                <span style={{fontSize:'0.78rem',color:sigStyle(ss.signal).color,fontWeight:600,
                  background:sigStyle(ss.signal).bg,padding:'0.15rem 0.5rem',borderRadius:'5px'}}>
                  {sigStyle(ss.signal).label}
                </span>
                <span style={{fontSize:'0.75rem',color:'var(--text-secondary)',marginLeft:'auto'}}>
                  avg 1D: {fmtChg(ss.avg_1d)}
                </span>
              </div>
              <StockTable stocks={ss.stocks} />
            </div>
          ))}
        </>
      )}
    </div>
  );
};

export default MarketRadarView;
