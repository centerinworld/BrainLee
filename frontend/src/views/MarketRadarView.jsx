import React from 'react';
import { API } from '../utils.js';

const MarketRadarView = React.memo(() => {
  const RADAR_SECTORS = [
    { key: 'semiconductor', name: '반도체/IT',     emoji: '💾' },
    { key: 'battery',       name: '2차전지',        emoji: '🔋' },
    { key: 'power_infra',   name: '전력산업',       emoji: '⚡' },
    { key: 'nuclear',       name: '원자력',          emoji: '☢️' },
    { key: 'defense',       name: 'K방산',           emoji: '🚀' },
    { key: 'construction',  name: '산업재/건설',     emoji: '🏗️' },
    { key: 'shipbuilding',  name: '조선',            emoji: '🚢' },
    { key: 'shipping',      name: '해운',            emoji: '🛳️' },
    { key: 'automotive',    name: '자동차',          emoji: '🚗' },
    { key: 'pharma',        name: '바이오/헬스케어', emoji: '💊' },
    { key: 'energy',        name: '소재/화학',       emoji: '⛽' },
    { key: 'steel',         name: '철강/비철금속',   emoji: '⚙️' },
    { key: 'it_hardware',   name: 'IT/하드웨어',     emoji: '💻' },
    { key: 'telecom',       name: '통신/플랫폼',     emoji: '📡' },
    { key: 'finance',       name: '금융/지주',       emoji: '🏦' },
  ];
  const [activeSector, setActiveSector] = React.useState('semiconductor');
  const [data,      setData]      = React.useState(null);
  const [loading,   setLoading]   = React.useState(false);
  const [error,     setError]     = React.useState('');
  const [importing, setImporting] = React.useState(false);
  const fileInputRef = React.useRef(null);

  const load = React.useCallback(async (sector) => {
    setLoading(true); setError('');
    try {
      const r = await fetch(API(`/api/market-radar/sector/${sector}/detail`));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch(e) { setError('데이터 로드 실패: ' + e.message); }
    finally { setLoading(false); }
  }, []);

  React.useEffect(() => { load(activeSector); }, [activeSector, load]);

  /* ── 포맷터 ──────────────────────────────────────────────────── */
  /* 시총: 국가별 통화기호 포함 (KR=조원/억원, JP=¥T/B, TW=NT$B, 기타=$T/B) */
  const fmtMktCap = (v_krw) => {
    if (v_krw == null || v_krw === 0) return '-';
    const n = Number(v_krw);
    if (isNaN(n) || n <= 0) return '-';
    if (n >= 1e12) return `${(n/1e12).toFixed(1)}조원`;
    if (n >= 1e8)  return `${Math.round(n/1e8).toLocaleString('ko-KR')}억원`;
    return Math.round(n/1e6).toLocaleString('ko-KR') + '백만원';
  };

  /* 주가: 통화기호 없이 숫자만 (국가 플래그로 통화 구별) */
  const fmtP = (price, country) => {
    if (price == null) return null;
    const n = Number(price);
    if (country === 'KR') return n.toLocaleString('ko-KR', {maximumFractionDigits:0});
    if (country === 'JP') return n.toLocaleString('en-US', {maximumFractionDigits:0});
    return n.toLocaleString('en-US', {maximumFractionDigits:2});
  };

  /* ── CSV 내보내기 ────────────────────────────────────────────── */
  const handleExport = () => {
    const a = document.createElement('a');
    a.href = API(`/api/market-radar/export-csv?sector=${activeSector}`);
    a.download = `${activeSector}_radar.csv`;
    a.click();
  };

  /* ── CSV 가져오기 ────────────────────────────────────────────── */
  const handleImport = async (e) => {
    const file = e.target.files?.[0]; if (!file) return;
    setImporting(true);
    const form = new FormData();
    form.append('file', file);
    form.append('sector', activeSector);
    try {
      const r   = await fetch(API('/api/market-radar/import-csv'), { method:'POST', body:form });
      const res = await r.json();
      if (r.ok) { alert(`${res.inserted||0}건 추가, ${res.updated||0}건 수정됨`); load(activeSector); }
      else alert('가져오기 실패: ' + (res.detail || r.statusText));
    } catch(e2) { alert('오류: ' + e2.message); }
    finally { setImporting(false); if (fileInputRef.current) fileInputRef.current.value=''; }
  };

  /* ── 그룹 빌더: lv2 연속 그룹 → KR / 해외 분리 ──────────────── */
  const buildGroups = (stocks) => {
    if (!stocks?.length) return [];
    const result = [];
    let i = 0;
    while (i < stocks.length) {
      const lv2 = (stocks[i].lv2 || '').trim();
      if (!lv2) {
        result.push({ lv2:'', single: stocks[i] });
        i++; continue;
      }
      let j = i;
      while (j < stocks.length && (stocks[j].lv2||'').trim() === lv2) j++;
      const grp  = stocks.slice(i, j);
      const ovs  = grp.filter(s => s.country !== 'KR');
      const kr   = grp.filter(s => s.country === 'KR');
      const lv2_view = grp.find(s => s.lv2_view)?.lv2_view || null;
      result.push({ lv2, kr, ovs, total: grp.length, lv2_view });
      i = j;
    }
    return result;
  };

  /* lv2 그룹의 신호 집계 (과반수) */
  const gSig = (stocks) => {
    if (!stocks?.length) return { sig_5d:'neutral', sig_10d:'neutral', sig_30d:'neutral' };
    const vote = (k) => {
      const u = stocks.filter(s => s[k] === 'up').length;
      const d = stocks.filter(s => s[k] === 'dn').length;
      return u > d ? 'up' : d > u ? 'dn' : 'neutral';
    };
    return { sig_5d: vote('sig_5d'), sig_10d: vote('sig_10d'), sig_30d: vote('sig_30d') };
  };

  /* 신호 점 3개 (5/10/30일) */
  const SigDots = ({ gs }) => gs ? (
    <span style={{display:'flex', gap:'3px', alignItems:'center', justifyContent:'center'}}>
      <span style={{color: gs.sig_5d  === 'up' ? '#ef4444' : gs.sig_5d  === 'dn' ? '#3b82f6' : 'var(--text-secondary)', fontSize:'0.82rem'}}>●</span>
      <span style={{fontSize:'0.6rem', color:'var(--text-secondary)'}}>5</span>
      <span style={{color: gs.sig_10d === 'up' ? '#ef4444' : gs.sig_10d === 'dn' ? '#3b82f6' : 'var(--text-secondary)', fontSize:'0.82rem'}}>●</span>
      <span style={{fontSize:'0.6rem', color:'var(--text-secondary)'}}>10</span>
      <span style={{color: gs.sig_30d === 'up' ? '#ef4444' : gs.sig_30d === 'dn' ? '#3b82f6' : 'var(--text-secondary)', fontSize:'0.82rem'}}>●</span>
      <span style={{fontSize:'0.6rem', color:'var(--text-secondary)'}}>30</span>
    </span>
  ) : null;

  /* ── 스타일 상수 ─────────────────────────────────────────────── */
  const thSt = {
    padding:'0.4rem 0.5rem', fontSize:'0.7rem', color:'var(--text-secondary)',
    fontWeight:600, whiteSpace:'nowrap', background:'rgba(0,0,0,0.3)',
    borderBottom:'1px solid var(--glass-border)',
  };
  const tdSt = {
    padding:'0.32rem 0.5rem', fontSize:'0.78rem',
    borderBottom:'1px solid rgba(255,255,255,0.04)', verticalAlign:'middle',
  };
  /* Level2 구분선: 파란 좌측 테두리 */
  const lv2TdSt = {
    ...tdSt, fontSize:'0.7rem', color:'var(--text-secondary)', verticalAlign:'middle',
    background:'rgba(59,130,246,0.06)',
    borderLeft:'3px solid rgba(59,130,246,0.7)',
    borderRight:'1px solid rgba(59,130,246,0.15)',
    paddingLeft:'0.65rem',
  };
  const sigTdSt = {
    ...tdSt, textAlign:'center', verticalAlign:'middle',
    background:'rgba(59,130,246,0.03)',
  };
  /* Level2 그룹 간 구분선 — 굵고 뚜렷한 파란 실선 */
  const LV2_BORDER  = '2px solid rgba(59,130,246,0.85)';
  /* 해외 ↔ KR 구분선 (Level2 내부) — LV2_BORDER보다 얇지만 뚜렷한 실선 */
  const KR_OVS_BORDER = '1.5px solid rgba(80,140,255,0.75)';

  /* 주가 셀: 가격 위 / % 아래 */
  const PCell = ({ price, chg, bold, country }) => (
    <td style={{...tdSt, textAlign:'right'}}>
      {price != null ? (
        <div style={{display:'flex', flexDirection:'column', alignItems:'flex-end', gap:'1px'}}>
          <span style={{fontWeight:bold?700:500, fontSize:'0.78rem', whiteSpace:'nowrap'}}>{fmtP(price, country)}</span>
          {chg != null
            ? <span style={{fontSize:'0.67rem', fontWeight:600, color: chg>0?'#ef4444':chg<0?'#3b82f6':'var(--text-secondary)'}}>
                {chg>0?'+':''}{chg.toFixed(1)}%
              </span>
            : <span style={{fontSize:'0.67rem', color:'var(--text-secondary)'}}>-</span>}
        </div>
      ) : <span style={{color:'var(--text-secondary)'}}>-</span>}
    </td>
  );

  /* PBR/PER 셀 */
  const ValCell = ({ v, isPbr }) => (
    <td style={{...tdSt, textAlign:'right', fontSize:'0.72rem'}}>
      {v != null
        ? <span style={{color: isPbr && v<1 ? '#34d399' : 'var(--text-secondary)'}}>{v.toFixed(isPbr?2:1)}</span>
        : <span style={{color:'rgba(255,255,255,0.18)', fontSize:'0.68rem'}}>-</span>}
    </td>
  );

  /* 개별 종목 앞 3개 셀 (국가, 종목명, 시총) */
  const StockDataCells = ({ s }) => {
    const tip = [s.name, s.lv2 ? `[${s.lv2}]` : '', s.desc||''].filter(Boolean).join(' — ');
    return <>
      <td style={{...tdSt, textAlign:'center', fontSize:'1.1rem', lineHeight:1}}>{s.country_flag||s.country}</td>
      <td style={{...tdSt, fontWeight:600, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}
          title={tip}>{s.name}</td>
      <td style={{...tdSt, textAlign:'right', fontSize:'0.72rem', color:'var(--text-secondary)', whiteSpace:'nowrap'}}>
        {fmtMktCap(s.market_cap_krw ?? s.market_cap)}
      </td>
    </>;
  };

  const PriceCells = ({ s }) => <>
    <PCell price={s.price}     chg={s.chg_1d}  bold={true} country={s.country} />
    <PCell price={s.price_5d}  chg={s.chg_5d}  country={s.country} />
    <PCell price={s.price_10d} chg={s.chg_10d} country={s.country} />
    <PCell price={s.price_30d} chg={s.chg_30d} country={s.country} />
    <PCell price={s.price_1y}  chg={s.chg_1y}  country={s.country} />
    <ValCell v={s.pbr} isPbr={true} />
    <ValCell v={s.per} isPbr={false} />
  </>;

  /* ── 섹션 행 렌더 ─────────────────────────────────────────────── */
  const renderGroups = (groups) => groups.flatMap((g, gi) => {
    /* Level2 그룹 경계: 굵은 파란 실선 */
    const groupBorderTop = gi > 0 ? LV2_BORDER : undefined;

    /* lv2 없는 개별 종목 */
    if (g.single) {
      const s = g.single;
      return [(
        <tr key={s.symbol} style={{borderTop: groupBorderTop}}
            onMouseOver={e=>e.currentTarget.style.background='rgba(255,255,255,0.03)'}
            onMouseOut={e=>e.currentTarget.style.background='transparent'}>
          <StockDataCells s={s}/>
          <td style={{...lv2TdSt}}>-</td>
          <td style={{...sigTdSt}}><SigDots gs={gSig([s])}/></td>
          <PriceCells s={s}/>
        </tr>
      )];
    }

    const { lv2, kr, ovs, total, lv2_view } = g;
    const hasKR  = kr.length  > 0;
    const hasOvs = ovs.length > 0;
    const krSig  = hasKR  ? gSig(kr)  : null;
    const ovsSig = hasOvs ? gSig(ovs) : null;

    const rows = [];

    /* LV2 설명 행 (있을 때만) */
    if (lv2_view) {
      rows.push(
        <tr key={`${lv2}-lv2desc`} style={{borderTop: groupBorderTop}}>
          <td colSpan={12} style={{
            padding: '0.28rem 1rem 0.3rem 1.4rem',
            background: 'rgba(99,102,241,0.07)',
            borderBottom: '1px solid rgba(99,102,241,0.2)',
            fontSize: '0.72rem', color: 'rgba(199,210,254,0.82)',
            lineHeight: 1.5, fontStyle: 'italic',
          }}>
            📌 <span style={{fontWeight:600, color:'rgba(199,210,254,1)', marginRight:'0.3rem'}}>{lv2}</span>{lv2_view}
          </td>
        </tr>
      );
    }

    /* 해외 행 먼저 (lv2 rowspan 여기서 시작) */
    ovs.forEach((s, oi) => {
      const isFirst = oi === 0;
      rows.push(
        <tr key={s.symbol}
            style={{borderTop: isFirst && !lv2_view ? groupBorderTop : undefined}}
            onMouseOver={e=>e.currentTarget.style.background='rgba(255,255,255,0.03)'}
            onMouseOut={e=>e.currentTarget.style.background='transparent'}>
          <StockDataCells s={s}/>
          {/* lv2 셀: 그룹 전체 rowspan — 첫 해외 행에만 */}
          {isFirst && (
            <td rowSpan={total} style={{...lv2TdSt, verticalAlign:'middle'}}>{lv2}</td>
          )}
          {/* 해외 신호 셀 */}
          {isFirst && (
            <td rowSpan={ovs.length} style={{...sigTdSt}}><SigDots gs={ovsSig}/></td>
          )}
          <PriceCells s={s}/>
        </tr>
      );
    });

    /* KR 행 */
    kr.forEach((s, ki) => {
      const isFirst = ki === 0;
      rows.push(
        <tr key={s.symbol}
            style={{
              borderTop: isFirst && !hasOvs && !lv2_view ? groupBorderTop : undefined,
              background: 'rgba(34,197,94,0.03)',  /* KR 행 연한 녹색 배경 */
            }}
            onMouseOver={e=>e.currentTarget.style.background='rgba(34,197,94,0.08)'}
            onMouseOut={e=>e.currentTarget.style.background='rgba(34,197,94,0.03)'}>
          <StockDataCells s={s}/>
          {/* lv2 셀: 해외 없을 때만 */}
          {isFirst && !hasOvs && (
            <td rowSpan={kr.length} style={{...lv2TdSt, verticalAlign:'middle'}}>{lv2}</td>
          )}
          {/* KR 신호 셀 */}
          {isFirst && (
            <td rowSpan={kr.length} style={{...sigTdSt, background:'rgba(34,197,94,0.05)'}}><SigDots gs={krSig}/></td>
          )}
          <PriceCells s={s}/>
        </tr>
      );
    });

    return rows;
  });

  /* ── 렌더 ────────────────────────────────────────────────────── */
  /* ── sticky 헤더 높이 상수 (tabs + thead) ──────────────────────── */
  const STICKY_TABS_H  = 86;  /* px: 제목행 + 탭바 합산 */
  const STICKY_HEAD_H  = STICKY_TABS_H + 34; /* + thead */
  const STICKY_SECT_H  = STICKY_HEAD_H + 34; /* + 섹션 타이틀 */

  return (
    <div style={{padding:'0 0.5rem'}}>
      {/* ── sticky 영역: 제목행 + 탭 ─────────────────────────────── */}
      <div style={{
        position:'sticky', top:0, zIndex:40,
        background:'rgba(10,10,22,0.97)', backdropFilter:'blur(14px)',
        margin:'0 -0.5rem', padding:'0.5rem 0.5rem 0.4rem',
        borderBottom:'1px solid rgba(59,130,246,0.18)',
      }}>
        {/* 제목 + CSV 버튼 */}
        <div style={{display:'flex', alignItems:'center', justifyContent:'space-between', gap:'0.8rem', marginBottom:'0.5rem', flexWrap:'wrap'}}>
          <div style={{display:'flex', alignItems:'center', gap:'0.8rem'}}>
            <h2 style={{margin:0, fontSize:'1.05rem', fontWeight:700}}>🛰 섹터 지표</h2>
            <span style={{fontSize:'0.78rem', color:'var(--text-secondary)'}}>
              글로벌 선행지표 — 해외 대표 기업 시세로 섹터 방향성 포착
            </span>
          </div>
          <div style={{display:'flex', gap:'0.5rem', alignItems:'center'}}>
            <button onClick={handleExport} style={{
              padding:'0.28rem 0.7rem', borderRadius:'6px', fontSize:'0.74rem', cursor:'pointer',
              background:'rgba(45,212,191,0.12)', border:'1px solid rgba(45,212,191,0.35)',
              color:'var(--accent-mint)', fontWeight:600,
            }}>⬇ CSV</button>
            <label style={{
              padding:'0.28rem 0.7rem', borderRadius:'6px', fontSize:'0.74rem', cursor:'pointer',
              background:'rgba(251,191,36,0.12)', border:'1px solid rgba(251,191,36,0.35)',
              color:'#fbbf24', fontWeight:600,
            }}>
              {importing ? '업로드 중…' : '⬆ CSV'}
              <input ref={fileInputRef} type="file" accept=".csv" style={{display:'none'}} onChange={handleImport}/>
            </label>
          </div>
        </div>

        {/* 섹터 탭 — 한 줄 가로 스크롤 */}
        <div style={{
          display:'flex', gap:'0.4rem',
          overflowX:'auto', flexWrap:'nowrap', scrollbarWidth:'none',
        }}>
          {RADAR_SECTORS.map(s => (
            <button key={s.key} onClick={() => setActiveSector(s.key)} style={{
              flexShrink:0,
              padding:'0.32rem 0.75rem', borderRadius:'20px', fontSize:'0.76rem', fontWeight:600,
              cursor:'pointer', transition:'all 0.15s', whiteSpace:'nowrap',
              border: activeSector === s.key ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
              background: activeSector === s.key ? 'rgba(45,212,191,0.15)' : 'transparent',
              color: activeSector === s.key ? 'var(--accent-mint)' : 'var(--text-secondary)',
            }}>
              {s.emoji} {s.name}
            </button>
          ))}
        </div>
      </div>

      {loading && <div style={{color:'var(--text-secondary)', padding:'2rem', textAlign:'center'}}>로딩 중...</div>}
      {error   && <div style={{color:'#f87171', padding:'1rem'}}>{error}</div>}
      {!loading && !error && data?.sections?.length === 0 && (
        <div className="glass-panel" style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
          <p>데이터를 불러오는 중입니다. 잠시 후 다시 시도해 주세요.</p>
        </div>
      )}

      {/* ── LV0 섹터 개요 패널 ─────────────────────────────────────── */}
      {!loading && data?.sector_overview && (
        <div style={{
          margin:'0.6rem 0', padding:'0.7rem 1rem',
          background:'rgba(59,130,246,0.07)',
          border:'1px solid rgba(59,130,246,0.25)',
          borderRadius:'8px', fontSize:'0.8rem',
          color:'rgba(186,221,255,0.9)', lineHeight:1.6,
        }}>
          <span style={{fontWeight:700, color:'#93c5fd', marginRight:'0.5rem'}}>
            {data.emoji} {data.sector_name} 섹터 개요
          </span>
          {data.sector_overview}
        </div>
      )}

      {/* 단일 테이블 (섹션 경계 = shaded title row) */}
      {!loading && data?.sections?.length > 0 && (
        <>
        {data.updated_date && (
          <div style={{textAlign:'right', marginBottom:'0.3rem', fontSize:'0.7rem',
            color:'rgba(147,197,253,0.6)', paddingRight:'0.3rem'}}>
            가격 업데이트: {data.updated_date}
          </div>
        )}
        <div className="glass-panel" style={{padding:'0', overflowX:'clip'}}>
          <table style={{width:'100%', borderCollapse:'collapse', tableLayout:'fixed'}}>
            <colgroup>
              <col style={{width:'36px'}}/>   {/* 국가 */}
              <col style={{width:'118px'}}/>  {/* 종목명 */}
              <col style={{width:'62px'}}/>   {/* 시총 */}
              <col style={{width:'88px'}}/>   {/* Level2 */}
              <col style={{width:'76px'}}/>   {/* 신호 */}
              <col style={{width:'82px'}}/>   {/* 현재(1일) */}
              <col style={{width:'74px'}}/>   {/* 5일 */}
              <col style={{width:'74px'}}/>   {/* 10일 */}
              <col style={{width:'74px'}}/>   {/* 30일 */}
              <col style={{width:'74px'}}/>   {/* 1년 */}
              <col style={{width:'46px'}}/>   {/* PBR */}
              <col style={{width:'46px'}}/>   {/* PER */}
            </colgroup>
            <thead>
              <tr>
                {[
                  {label:'국가',      align:'center'},
                  {label:'종목명',    align:'left'},
                  {label:'시총',      align:'right'},
                  {label:'Level2',   align:'left', pl:'0.7rem'},
                  {label:'신호',      align:'center'},
                  {label:'현재(1일)', align:'right'},
                  {label:'5일',       align:'right'},
                  {label:'10일',      align:'right'},
                  {label:'30일',      align:'right'},
                  {label:'1년',       align:'right'},
                  {label:'PBR',       align:'right'},
                  {label:'PER',       align:'right'},
                ].map(h => (
                  <th key={h.label} style={{
                    ...thSt, textAlign:h.align,
                    paddingLeft: h.pl || undefined,
                    position:'sticky', top:`${STICKY_TABS_H}px`, zIndex:20,
                    background:'rgba(8,8,20,0.98)',
                    boxShadow:'0 1px 0 rgba(59,130,246,0.3)',
                  }}>{h.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.sections.map((section, si) => (
                <React.Fragment key={section.name}>
                  {/* 섹션 타이틀 행 — 파란 음영 + sticky */}
                  <tr>
                    <td colSpan={12} style={{
                      padding:'0.42rem 0.8rem 0.35rem',
                      background:'rgba(12,18,40,0.97)',
                      backgroundImage:'linear-gradient(rgba(59,130,246,0.13),rgba(59,130,246,0.13))',
                      borderTop: si > 0 ? '2px solid rgba(59,130,246,0.85)' : undefined,
                      borderBottom: section.desc ? '1px solid rgba(59,130,246,0.3)' : '2px solid rgba(59,130,246,0.7)',
                      position:'sticky', top:`${STICKY_HEAD_H}px`, zIndex:15,
                    }}>
                      <div style={{display:'flex', alignItems:'baseline', gap:'0.5rem', flexWrap:'wrap'}}>
                        <span style={{fontSize:'0.82rem', fontWeight:700, color:'#93c5fd'}}>{section.name}</span>
                        {section.avg_1d != null && (
                          <span style={{fontSize:'0.72rem', fontWeight:600,
                            color: section.avg_1d > 0 ? '#ef4444' : '#3b82f6'}}>
                            평균 {section.avg_1d > 0 ? '+' : ''}{section.avg_1d.toFixed(1)}%
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                  {/* LV1 섹션 설명 행 */}
                  {section.desc && (
                    <tr>
                      <td colSpan={12} style={{
                        padding:'0.38rem 1rem 0.42rem 1.2rem',
                        background:'rgba(59,130,246,0.05)',
                        borderBottom:'2px solid rgba(59,130,246,0.7)',
                        fontSize:'0.74rem', color:'rgba(186,221,255,0.8)',
                        lineHeight:1.55, fontStyle:'italic',
                      }}>
                        💡 {section.desc}
                      </td>
                    </tr>
                  )}
                  {renderGroups(buildGroups(section.stocks || []))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}
    </div>
  );
});

export default MarketRadarView;
