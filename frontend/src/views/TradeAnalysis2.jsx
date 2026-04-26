import React from 'react';

const useIsMobile = () => {
  const [isMobile, setIsMobile] = React.useState(window.innerWidth < 768);
  React.useEffect(() => {
    const h = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', h);
    return () => window.removeEventListener('resize', h);
  }, []);
  return isMobile;
};

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
      setCompanies(d);
      if (d.length > 0) {
        setSelCompany(d[0].stock_code);
        loadCompanyTrend(d[0].stock_code, sectorKey);
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

  const [signals, setSignals]         = React.useState(null);
  const [signalLoading, setSignalLoading] = React.useState(false);
  const [mainTab, setMainTab]          = React.useState('sectors'); // 'sectors' | 'signals'
  const [sigScope, setSigScope]        = React.useState('all');
  const [sigCategory, setSigCategory]  = React.useState('all');

  const loadSignals = async () => {
    setSignalLoading(true);
    try {
      const r = await fetch(HS_API(`/api/analysis2/signals?months=36&scope=${sigScope}`));
      if (r.ok) setSignals(await r.json());
    } catch {}
    finally { setSignalLoading(false); }
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
  React.useEffect(() => { if (mainTab === 'signals') loadSignals(); }, [mainTab, sigScope]);

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
              <tr key={`${type}-${item.hs_code}`}>
                <td style={{fontFamily:'monospace', fontWeight:700}}>{item.hs_code}</td>
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
          <svg viewBox={`0 0 ${monthly.length * 28 + 60} 230`} style={{width:'100%', minWidth:`${monthly.length * 28 + 60}px`, height:'230px'}}>
            {[0,1,2,3,4].map((i) => (
              <line key={i} x1="40" x2={monthly.length * 28 + 40} y1={20 + i * 40} y2={20 + i * 40} stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
            ))}
            {monthly.map((m, i) => {
              const exportH = Math.max(4, (m.export_val / maxExport) * 165);
              const importH = Math.max(2, ((m.import_val || 0) / maxExport) * 165);
              const x = 42 + i * 28;
              return (
                <g key={m.period_ym}>
                  <rect x={x} y={190 - exportH} width={12} height={exportH}
                    fill={i >= monthly.length - 3 ? 'rgba(52,211,153,0.48)' : 'rgba(250,204,21,0.52)'}
                    rx="3">
                    <title>{`${m.period_ym} | 수출액 ${fmt(m.export_val)} | 수출중량 ${fmt(m.export_kg)}kg`}</title>
                  </rect>
                  <rect x={x + 13} y={190 - importH} width={8} height={importH}
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
                  const y = 190 - (((unit - minUnitPrice) / ((maxUnitPrice - minUnitPrice) || 1)) * 165);
                  return `${42 + i * 28 + 9},${y}`;
                }).join(' ')}
              />
            )}
            {monthly.filter((_, i) => i % 3 === 0 || i === monthly.length - 1).map((m) => {
              const i = monthly.findIndex((x) => x.period_ym === m.period_ym);
              return (
                <text key={m.period_ym} x={42 + i * 28 + 9} y={208} fontSize="8" fill="rgba(255,255,255,0.45)" textAnchor="middle">
                  {m.period_ym.slice(2)}
                </text>
              );
            })}
            {[0,1,2,3,4].map((i) => {
              const value = maxExport - ((maxExport / 4) * i);
              return (
                <text key={i} x="34" y={24 + i * 40} fontSize="9" fill="rgba(255,255,255,0.42)" textAnchor="end">
                  {fmtAxis(value)}
                </text>
              );
            })}
            <text x={monthly.length * 28 + 48} y="18" fontSize="9" fill="rgba(255,255,255,0.42)">${maxUnitPrice.toFixed(0)}</text>
            <text x={monthly.length * 28 + 48} y="192" fontSize="9" fill="rgba(255,255,255,0.28)">${minUnitPrice.toFixed(0)}</text>
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
          </div>
        </div>
        {/* 바+라인 차트 */}
        <div style={{overflowX:'auto'}}>
          <svg viewBox={`0 0 ${monthly.length * 20 + 40} 200`} style={{width:'100%', minWidth:`${monthly.length * 20 + 40}px`, height:'200px'}}>
            {/* 그리드 라인 */}
            {[0,1,2,3].map(i => (
              <line key={i} x1="35" x2={monthly.length * 20 + 35} y1={10 + i*45} y2={10 + i*45}
                stroke="rgba(255,255,255,0.05)" strokeWidth="1"/>
            ))}
            {/* 바 */}
            {monthly.map((m, i) => {
              const exportH = Math.max(2, ((m.export_val - minV) / (maxV - minV || 1)) * 160);
              const importH = Math.max(0, ((m.import_val - minV) / (maxV - minV || 1)) * 160);
              const x = 35 + i * 20;
              const isLatest = i === monthly.length - 1;
              return (
                <g key={i}>
                  <rect x={x+1} y={190 - exportH} width={11} height={exportH}
                    fill={isLatest ? 'rgba(245,158,11,0.5)' : 'rgba(167,139,250,0.25)'}
                    rx="2">
                    <title>{`${m.period_ym}: 수출 ${(m.export_val/1e6).toFixed(1)}M`}</title>
                  </rect>
                  <rect x={x+13} y={190 - importH} width={4} height={importH}
                    fill="rgba(96,165,250,0.55)" rx="2">
                    <title>{`${m.period_ym}: 수입 ${(m.import_val/1e6).toFixed(1)}M`}</title>
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
                  const exportH = ((m.export_val - minV) / (maxV - minV || 1)) * 160;
                  return `${35 + i * 20 + 8},${190 - exportH}`;
                }).join(' ')}
              />
            )}
            {/* X축 라벨 (6개월 간격) */}
            {monthly.filter((_, i) => i % 6 === 0).map((m, idx) => {
              const origI = monthly.findIndex(x => x.period_ym === m.period_ym);
              return (
                <text key={idx} x={35 + origI * 20 + 8} y={198} fontSize="7" fill="rgba(255,255,255,0.4)" textAnchor="middle">
                  {m.period_ym.slice(2)}
                </text>
              );
            })}
            {/* Y축 */}
            <text x="30" y="15" fontSize="7" fill="rgba(255,255,255,0.3)" textAnchor="end">
              {(maxV/1e6).toFixed(0)}M
            </text>
            <text x="30" y="190" fontSize="7" fill="rgba(255,255,255,0.3)" textAnchor="end">
              {(minV/1e6).toFixed(0)}M
            </text>
          </svg>
        </div>
      </div>
    );
  };

  const sectorColors = {
    semiconductors: '#a78bfa', autos: '#60a5fa', batteries: '#34d399',
    biotech: '#f87171', consumer: '#fb923c', shipbuilding: '#38bdf8', energy_materials: '#facc15',
  };

  return (
    <div style={{padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1.5rem'}}>
      {/* 헤더 */}
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:'0.75rem'}}>
        <div>
          <h1 style={{fontSize:'1.4rem', fontWeight:800, color:'#fff', margin:0}}>📦 수출입 분석 II</h1>
          <p style={{fontSize:'0.78rem', color:'var(--text-secondary)', marginTop:'0.2rem'}}>
            섹터별 수출 추세 분석 → 관련 기업 투자 기회 탐색
          </p>
        </div>
        <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
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
        </div>
      </div>

      {/* ── 메인 탭 ── */}
      <div style={{display:'flex', gap:'0.5rem', borderBottom:'1px solid var(--glass-border)', paddingBottom:'0.5rem'}}>
        {[['sectors','🏭 섹터·기업 분석'],['signals','⚡ 투자 시그널 보드']].map(([key, label]) => (
          <button key={key} onClick={() => setMainTab(key)} style={{
            padding:'0.4rem 1rem', borderRadius:'8px 8px 0 0', fontSize:'0.8rem', cursor:'pointer',
            fontWeight: mainTab === key ? 700 : 400,
            border: mainTab === key ? '1px solid rgba(167,139,250,0.4)' : '1px solid transparent',
            borderBottom: mainTab === key ? '2px solid #a78bfa' : '1px solid transparent',
            background: mainTab === key ? 'rgba(167,139,250,0.12)' : 'transparent',
            color: mainTab === key ? '#a78bfa' : 'var(--text-secondary)',
          }}>{label}</button>
        ))}
      </div>

      {error && (
        <div style={{padding:'0.75rem 1rem', background:'rgba(239,68,68,0.12)', border:'1px solid rgba(239,68,68,0.3)',
          borderRadius:'10px', color:'#f87171', fontSize:'0.8rem'}}>⚠️ {error}</div>
      )}

      {/* ── 시그널 보드 탭 ── */}
      {mainTab === 'signals' && (() => {
        const SIG_META = {
          ATH_EXPORT:       {emoji:'🔴', label:'역대 최고 수출', cat:'강세', color:'#ef4444', bg:'rgba(239,68,68,0.1)'},
          NEAR_ATH_EXPORT:  {emoji:'🟠', label:'역대급 수출 (95%+)', cat:'강세', color:'#f97316', bg:'rgba(249,115,22,0.1)'},
          ATH_IMPORT:       {emoji:'🔵', label:'역대 최고 수입', cat:'수주급증', color:'#3b82f6', bg:'rgba(59,130,246,0.1)'},
          SURGE_EXPORT_50:  {emoji:'🔴', label:'수출 폭증 +50%', cat:'강세', color:'#ef4444', bg:'rgba(239,68,68,0.12)'},
          SURGE_EXPORT_30:  {emoji:'🟡', label:'수출 급증 +30%', cat:'강세', color:'#f59e0b', bg:'rgba(245,158,11,0.1)'},
          IMPORT_SURGE_50:  {emoji:'💥', label:'수입 폭증 +50%', cat:'수주폭증', color:'#8b5cf6', bg:'rgba(139,92,246,0.12)'},
          IMPORT_SURGE_30:  {emoji:'🔵', label:'수입 급증 +30%', cat:'수주증가', color:'#60a5fa', bg:'rgba(96,165,250,0.1)'},
          CONSEC_GROWTH_6M: {emoji:'🟢', label:'6개월 연속 수출↑', cat:'강세', color:'#34d399', bg:'rgba(52,211,153,0.1)'},
          CONSEC_GROWTH_3M: {emoji:'🟡', label:'3개월 연속 수출↑', cat:'강세', color:'#fbbf24', bg:'rgba(251,191,36,0.1)'},
          ACCELERATION:     {emoji:'⚡', label:'수출 성장 가속', cat:'강세', color:'#a78bfa', bg:'rgba(167,139,250,0.1)'},
          REBOUND:          {emoji:'📈', label:'수출 바닥 반등', cat:'반등', color:'#34d399', bg:'rgba(52,211,153,0.08)'},
          DECLINE_30:       {emoji:'🔻', label:'수출 급감 -30%', cat:'약세', color:'#64748b', bg:'rgba(100,116,139,0.1)'},
          DECLINE_20:       {emoji:'🔽', label:'수출 감소 -20%', cat:'약세', color:'#94a3b8', bg:'rgba(148,163,184,0.08)'},
          REVERSAL_DOWN:    {emoji:'⚠️', label:'수출 고점 반락', cat:'약세', color:'#f87171', bg:'rgba(248,113,113,0.08)'},
        };
        const cats = ['all','강세','수주증가','수주폭증','반등','약세'];
        const allSigs = (signals?.signals || []).filter(s => sigCategory === 'all' || s.category === sigCategory);
        return (
          <div style={{display:'flex', flexDirection:'column', gap:'1rem'}}>
            {/* 필터 바 */}
            <div className="glass-panel" style={{padding:'0.8rem 1.2rem', display:'flex', alignItems:'center', gap:'1rem', flexWrap:'wrap'}}>
              <div style={{display:'flex', gap:'0.4rem', alignItems:'center'}}>
                <span style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>범위:</span>
                {[['all','전체'],['sector','섹터'],['company','기업']].map(([k,l]) => (
                  <button key={k} onClick={() => setSigScope(k)} style={{
                    padding:'0.2rem 0.6rem', borderRadius:'6px', fontSize:'0.72rem', cursor:'pointer',
                    fontWeight: sigScope===k ? 700 : 400,
                    border: sigScope===k ? '1px solid #a78bfa' : '1px solid var(--glass-border)',
                    background: sigScope===k ? 'rgba(167,139,250,0.14)' : 'transparent',
                    color: sigScope===k ? '#a78bfa' : 'var(--text-secondary)',
                  }}>{l}</button>
                ))}
              </div>
              <div style={{display:'flex', gap:'0.4rem', alignItems:'center'}}>
                <span style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>카테고리:</span>
                {cats.map(c => (
                  <button key={c} onClick={() => setSigCategory(c)} style={{
                    padding:'0.2rem 0.6rem', borderRadius:'6px', fontSize:'0.72rem', cursor:'pointer',
                    fontWeight: sigCategory===c ? 700 : 400,
                    border: sigCategory===c ? '1px solid #a78bfa' : '1px solid var(--glass-border)',
                    background: sigCategory===c ? 'rgba(167,139,250,0.14)' : 'transparent',
                    color: sigCategory===c ? '#a78bfa' : 'var(--text-secondary)',
                  }}>{c === 'all' ? '전체' : c}</button>
                ))}
              </div>
              <button onClick={loadSignals} disabled={signalLoading} style={{
                marginLeft:'auto', padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.75rem',
                cursor:'pointer', border:'1px solid var(--glass-border)', background:'rgba(255,255,255,0.05)', color:'var(--text-secondary)'}}>
                {signalLoading ? '⏳' : '🔄 새로고침'}
              </button>
              {signals?.generated_at && (
                <span style={{fontSize:'0.65rem', color:'rgba(255,255,255,0.3)'}}>산출: {signals.generated_at}</span>
              )}
            </div>

            {/* 시그널 요약 카운트 */}
            {signals && (() => {
              const cnt = {};
              (signals.signals||[]).forEach(s => { cnt[s.category] = (cnt[s.category]||0)+1; });
              return (
                <div style={{display:'flex', gap:'0.6rem', flexWrap:'wrap'}}>
                  {Object.entries(cnt).map(([cat, n]) => (
                    <div key={cat} onClick={() => setSigCategory(cat === sigCategory ? 'all' : cat)}
                      style={{padding:'0.4rem 0.9rem', borderRadius:'8px', fontSize:'0.75rem', cursor:'pointer',
                        background:'rgba(255,255,255,0.05)', border:'1px solid var(--glass-border)',
                        color: cat==='약세'?'#64748b': cat==='반등'?'#34d399': cat.includes('수주')?'#60a5fa':'#f59e0b',
                        fontWeight:700}}>
                      {cat} <span style={{marginLeft:'0.3rem', opacity:0.7}}>{n}</span>
                    </div>
                  ))}
                </div>
              );
            })()}

            {/* 시그널 카드 그리드 */}
            {signalLoading ? (
              <div className="glass-panel" style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                <div style={{width:'28px',height:'28px',border:'2px solid rgba(167,139,250,0.3)',borderTop:'2px solid #a78bfa',
                  borderRadius:'50%',animation:'spin 0.8s linear infinite',margin:'0 auto 0.5rem'}}/>
                시그널 산출 중...
              </div>
            ) : !signals ? (
              <div className="glass-panel" style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                새로고침 버튼을 눌러 시그널을 불러오세요
              </div>
            ) : allSigs.length === 0 ? (
              <div className="glass-panel" style={{padding:'2rem', textAlign:'center', color:'var(--text-secondary)'}}>
                해당 카테고리 시그널 없음
              </div>
            ) : (
              <div style={{display:'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(280px, 1fr))', gap:'0.75rem'}}>
                {allSigs.map((sig, i) => {
                  const meta = SIG_META[sig.signal_type] || {emoji:'•', label:sig.label, color:'#94a3b8', bg:'rgba(148,163,184,0.08)'};
                  const isStrong = sig.score >= 85;
                  return (
                    <div key={i} className="glass-panel" style={{
                      padding:'0.9rem 1.1rem',
                      border: isStrong ? `1px solid ${meta.color}55` : '1px solid var(--glass-border)',
                      background: meta.bg,
                      position:'relative',
                      overflow:'hidden',
                    }}>
                      {isStrong && <div style={{position:'absolute', top:0, left:0, width:'3px', height:'100%', background:meta.color}} />}
                      <div style={{display:'flex', alignItems:'flex-start', justifyContent:'space-between', marginBottom:'0.5rem'}}>
                        <div>
                          <div style={{fontSize:'0.68rem', color:meta.color, fontWeight:700, letterSpacing:'0.05em', textTransform:'uppercase'}}>
                            {meta.emoji} {meta.label}
                          </div>
                          <div style={{fontSize:'0.95rem', fontWeight:800, color:'#fff', marginTop:'0.15rem'}}>
                            {sig.scope_type === 'sector' ? '🏭 ' : '🏢 '}{sig.scope_name}
                          </div>
                        </div>
                        <div style={{textAlign:'right', flexShrink:0}}>
                          <div style={{fontSize:'0.62rem', color:'rgba(255,255,255,0.4)', marginBottom:'0.1rem'}}>{sig.period}</div>
                          <div style={{fontSize:'0.72rem', fontWeight:700, color:meta.color, background:`${meta.color}22`,
                            padding:'0.1rem 0.4rem', borderRadius:'4px'}}>
                            {sig.score}점
                          </div>
                        </div>
                      </div>
                      <div style={{display:'flex', gap:'0.5rem', flexWrap:'wrap', marginTop:'0.4rem'}}>
                        {sig.export_value > 0 && (
                          <div style={{fontSize:'0.72rem', color:'rgba(255,255,255,0.6)'}}>
                            📤 수출 <span style={{color:'#f59e0b', fontWeight:700}}>${(sig.export_value/1e6).toFixed(0)}M</span>
                          </div>
                        )}
                        {sig.yoy_pct != null && (
                          <div style={{fontSize:'0.72rem', color:'rgba(255,255,255,0.6)'}}>
                            YoY <span style={{color: sig.yoy_pct>=0?'#ef4444':'#3b82f6', fontWeight:700}}>
                              {sig.yoy_pct>=0?'+':''}{sig.yoy_pct.toFixed(1)}%
                            </span>
                          </div>
                        )}
                        {sig.import_value > 0 && (sig.signal_type==='ATH_IMPORT'||sig.signal_type?.includes('IMPORT')) && (
                          <div style={{fontSize:'0.72rem', color:'rgba(255,255,255,0.6)'}}>
                            📥 수입 <span style={{color:'#60a5fa', fontWeight:700}}>${(sig.import_value/1e6).toFixed(0)}M</span>
                          </div>
                        )}
                        {sig.yoy_imp_pct != null && sig.signal_type?.includes('IMPORT') && (
                          <div style={{fontSize:'0.72rem', color:'rgba(255,255,255,0.6)'}}>
                            YoY <span style={{color:'#60a5fa', fontWeight:700}}>
                              {sig.yoy_imp_pct>=0?'+':''}{sig.yoy_imp_pct.toFixed(1)}%
                            </span>
                          </div>
                        )}
                        {sig.mom_pct != null && (
                          <div style={{fontSize:'0.72rem', color:'rgba(255,255,255,0.6)'}}>
                            MoM <span style={{color: sig.mom_pct>=0?'#ef4444':'#3b82f6', fontWeight:700}}>
                              {sig.mom_pct>=0?'+':''}{sig.mom_pct.toFixed(1)}%
                            </span>
                          </div>
                        )}
                      </div>
                      <div style={{marginTop:'0.4rem', fontSize:'0.68rem', color:'rgba(255,255,255,0.35)'}}>
                        {sig.scope_type === 'company' ? '기업 수출입' : '섹터 합산'} • {sig.category}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* 범례 */}
            <div className="glass-panel" style={{padding:'0.75rem 1.2rem', display:'flex', gap:'1rem', flexWrap:'wrap', alignItems:'center'}}>
              <span style={{fontSize:'0.68rem', color:'rgba(255,255,255,0.4)', fontWeight:600}}>시그널 해석:</span>
              <span style={{fontSize:'0.68rem', color:'#f59e0b'}}>🟡 수출증가 = 매출확대</span>
              <span style={{fontSize:'0.68rem', color:'#60a5fa'}}>🔵 수입급증 = 수주증가·준비</span>
              <span style={{fontSize:'0.68rem', color:'#34d399'}}>📈 반등 = 저점탈출</span>
              <span style={{fontSize:'0.68rem', color:'#94a3b8'}}>🔻 급감 = 업황둔화 주의</span>
              <span style={{fontSize:'0.65rem', color:'rgba(255,255,255,0.25)', marginLeft:'auto'}}>관세청 HS코드 기반 · BeOn 채널 교차검증</span>
            </div>
          </div>
        );
      })()}

      {/* ── 섹터·기업 분석 탭 ── */}
      {mainTab === 'sectors' && <>

      {/* ── 상단: 섹터별 수출 추세 표 ── */}
      <div className="glass-panel" style={{overflow:'hidden'}}>
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
          <div style={{overflowX:'auto'}}>
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

      <div className="glass-panel" style={{overflow:'hidden'}}>
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
                <TabButton active={sectorTab === 'hs'} onClick={() => {
                  setSectorTab('hs');
                  if (!sectorHs) loadSectorHs(selSector.sector_key, selSector.latest_period || '');
                }}>HS 구성</TabButton>
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
      <div className="glass-panel" style={{overflow:'hidden'}}>
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
                  {(companyHs?.items || []).slice(0, 4).map((item) => (
                    <span key={item.hs_code} style={{
                      fontSize:'0.72rem',
                      padding:'0.28rem 0.55rem',
                      borderRadius:'999px',
                      background:'rgba(255,255,255,0.05)',
                      border:'1px solid var(--glass-border)',
                      color:'#e5e7eb'
                    }}>
                      {item.hs_name} {item.export_share != null ? `(${item.export_share.toFixed(1)}%)` : ''}
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
              <div style={{marginTop:'1rem', overflowX:'auto', maxHeight:'260px', overflowY:'auto'}}>
                <table className="premium-table" style={{fontSize:'0.78rem', minWidth:'980px'}}>
                  <thead><tr>
                    <th>기간</th>
                    <th style={{textAlign:'right'}}>수출액</th>
                    <th style={{textAlign:'right'}}>수입액</th>
                    <th style={{textAlign:'right'}}>수출 MoM</th>
                    <th style={{textAlign:'right'}}>수출 YoY</th>
                    <th style={{textAlign:'right'}}>수입 MoM</th>
                    <th style={{textAlign:'left'}}>주요 HS</th>
                  </tr></thead>
                  <tbody>
                    {[...compTrend.monthly].reverse().map((m, i, arr) => {
                      const prev1  = arr[i + 1];
                      const prev12 = arr[i + 12];
                      const exportMom = prev1  ? (m.export_val - prev1.export_val)  / (prev1.export_val  || 1) * 100 : null;
                      const exportYoy = prev12 ? (m.export_val - prev12.export_val) / (prev12.export_val || 1) * 100 : null;
                      const importMom = prev1  ? (m.import_val - prev1.import_val)  / (prev1.import_val  || 1) * 100 : null;
                      return (
                        <tr key={m.period_ym} style={{opacity: i === 0 ? 1 : 0.9}}>
                          <td style={{fontWeight: i === 0 ? 700 : 400}}>{m.period_ym}</td>
                          <td style={{textAlign:'right', fontWeight: i === 0 ? 700 : 400}}>
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
                  <BreakdownTable items={companyHs?.items || []} type="company" />
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

      </>}
    </div>
  );
};

export default TradeAnalysis2;
