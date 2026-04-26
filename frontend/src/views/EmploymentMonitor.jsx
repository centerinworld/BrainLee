import React from 'react';

const API = (path) => path;

const EmploymentMonitor = () => {
  const [tab, setTab]         = React.useState('monthly');   // monthly | trend | summary
  const [fg, setFg]           = React.useState('3');         // 3=고용, 1=산재
  const [wkFg, setWkFg]       = React.useState('근로자');
  const [months, setMonths]   = React.useState(24);
  const [data, setData]       = React.useState([]);
  const [summary, setSummary] = React.useState(null);
  const [trendSector, setTrendSector] = React.useState('jejoeop');
  const [trendData, setTrendData]     = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr]         = React.useState('');
  const [collecting, setCollecting] = React.useState(false);

  const SECTOR_LABELS = {
    agri_fore_fis:'농업·임업·어업', agri_hunt_fore:'농업·수렵·임업',
    ahou_gyhwaldong:'가구내고용활동', art_sport_yeoga:'예술·스포츠·여가',
    bogun_shoe_bokji:'보건·사회복지', budongsanImdae:'부동산임대',
    budongsanImdaeSaeop:'부동산·사업서비스', edu_service:'교육서비스',
    fis:'어업', fnn_boheom:'금융·보험', geonseoleop:'건설',
    inte_foreign_gigwan:'국제·외국기관', jejoeop:'제조',
    jeongi_gas_step:'전기·가스·수도', jeongi_gas_tw:'전기·가스·상수도',
    jeonmun_sci:'전문·과학·기술', minind:'광업',
    publ_aim_brd:'출판·방송·정보', pub_hjng_shoe:'공공행정·국방',
    saeop_siseol_jiwon:'사업시설관리', sew_smat_rmatrev:'하수·폐기물·환경',
    sukbak_aeh:'숙박·음식점', unsu:'운수', unsu_changgo_tongsin:'운수·창고·통신',
    whol_ret:'도소매', whol_ret_cons:'도소매·소비자용품',
  };
  const MAIN_SECTORS = ['jejoeop','geonseoleop','fnn_boheom','bogun_shoe_bokji',
    'whol_ret','edu_service','unsu','sukbak_aeh','jeonmun_sci','publ_aim_brd'];

  const clr = (v) => v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : 'inherit';
  const pctFmt = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%';

  React.useEffect(() => {
    fetch(API('/api/employment/summary'))
      .then(r => r.ok ? r.json() : null).then(d => setSummary(d)).catch(() => {});
  }, []);

  React.useEffect(() => {
    if (tab !== 'monthly') return;
    setLoading(true); setErr('');
    fetch(API(`/api/employment/monthly?sj_gy_fg=${fg}&saeopjang_wk_fg=${encodeURIComponent(wkFg)}&months=${months}`))
      .then(r => r.ok ? r.json() : null)
      .then(d => { setData(d?.data || []); setLoading(false); })
      .catch(e => { setErr('데이터 로드 실패: ' + e.message); setLoading(false); });
  }, [tab, fg, wkFg, months]);

  React.useEffect(() => {
    if (tab !== 'trend') return;
    setLoading(true); setErr('');
    fetch(API(`/api/employment/trend/${trendSector}?sj_gy_fg=${fg}&saeopjang_wk_fg=${encodeURIComponent(wkFg)}&months=36`))
      .then(r => r.ok ? r.json() : null)
      .then(d => { setTrendData(d); setLoading(false); })
      .catch(e => { setErr('트렌드 로드 실패'); setLoading(false); });
  }, [tab, trendSector, fg, wkFg]);

  const doCollect = () => {
    setCollecting(true);
    fetch(API('/api/employment/collect?months_back=3'), { method: 'POST' })
      .then(r => r.json()).then(() => { setCollecting(false); })
      .catch(() => setCollecting(false));
  };

  const noData = data.length === 0 && !loading;

  return (
    <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
      {/* 헤더 */}
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
        <div>
          <h2 style={{fontSize:'1rem',fontWeight:700,margin:0}}>📊 고용/산재보험 업종별 현황</h2>
          {summary?.latest_ym && (
            <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
              최신 데이터: {summary.latest_ym}
            </span>
          )}
        </div>
        <div style={{display:'flex',gap:'0.5rem',alignItems:'center',flexWrap:'wrap'}}>
          {/* 보험 종류 */}
          {['3','1'].map(v => (
            <button key={v} onClick={() => setFg(v)} style={{
              padding:'0.25rem 0.7rem',borderRadius:'20px',fontSize:'0.75rem',cursor:'pointer',
              background: fg===v ? 'rgba(45,212,191,0.2)' : 'rgba(255,255,255,0.04)',
              border: fg===v ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
              color: fg===v ? 'var(--accent-mint)' : 'var(--text-secondary)',
            }}>{v==='3'?'고용보험':'산재보험'}</button>
          ))}
          {/* 사업장/근로자 */}
          {['근로자','사업장'].map(v => (
            <button key={v} onClick={() => setWkFg(v)} style={{
              padding:'0.25rem 0.7rem',borderRadius:'20px',fontSize:'0.75rem',cursor:'pointer',
              background: wkFg===v ? 'rgba(168,139,250,0.2)' : 'rgba(255,255,255,0.04)',
              border: wkFg===v ? '1px solid #a78bfa' : '1px solid var(--glass-border)',
              color: wkFg===v ? '#a78bfa' : 'var(--text-secondary)',
            }}>{v}</button>
          ))}
          <button onClick={doCollect} disabled={collecting} style={{
            padding:'0.25rem 0.7rem',borderRadius:'20px',fontSize:'0.72rem',cursor:collecting?'wait':'pointer',
            background:'rgba(251,191,36,0.1)',border:'1px solid rgba(251,191,36,0.3)',
            color:'#fbbf24',opacity:collecting?0.5:1,
          }}>{collecting?'수집 중...':'🔄 수집'}</button>
        </div>
      </div>

      {/* 탭 */}
      <div style={{display:'flex',gap:'0.4rem',borderBottom:'1px solid var(--glass-border)',paddingBottom:'0.4rem'}}>
        {[['monthly','업종별 현황'],['trend','업종 추이'],['summary','요약']].map(([k,l]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding:'0.3rem 0.9rem',borderRadius:'6px 6px 0 0',fontSize:'0.8rem',cursor:'pointer',
            background: tab===k ? 'rgba(45,212,191,0.15)' : 'transparent',
            border: tab===k ? '1px solid var(--accent-mint)' : '1px solid transparent',
            borderBottom: tab===k ? '1px solid var(--bg-dark)' : '1px solid transparent',
            color: tab===k ? 'var(--accent-mint)' : 'var(--text-secondary)',
          }}>{l}</button>
        ))}
      </div>

      {err && <div style={{color:'#f87171',fontSize:'0.8rem',padding:'0.5rem',background:'rgba(239,68,68,0.1)',borderRadius:'6px'}}>{err}</div>}

      {/* ── 업종별 현황 탭 ── */}
      {tab === 'monthly' && (
        <div className="glass-panel" style={{overflow:'auto'}}>
          <div style={{display:'flex',alignItems:'center',gap:'1rem',padding:'0.7rem 1rem',borderBottom:'1px solid var(--glass-border)'}}>
            <span style={{fontWeight:700,fontSize:'0.85rem'}}>업종별 {wkFg} 수 (단위: 명/개소)</span>
            <span style={{marginLeft:'auto',fontSize:'0.72rem',color:'var(--text-secondary)'}}>최근 {months}개월</span>
            <select value={months} onChange={e=>setMonths(Number(e.target.value))}
              style={{padding:'0.2rem 0.5rem',borderRadius:'5px',fontSize:'0.75rem',
                background:'rgba(255,255,255,0.07)',border:'1px solid var(--glass-border)',color:'#fff'}}>
              {[6,12,24,36,60].map(m=><option key={m} value={m} style={{background:'#1a1a2e'}}>{m}개월</option>)}
            </select>
          </div>
          {loading ? (
            <div style={{textAlign:'center',padding:'3rem',color:'var(--accent-mint)'}}>로딩 중...</div>
          ) : noData ? (
            <div style={{textAlign:'center',padding:'3rem',color:'var(--text-secondary)'}}>
              <p style={{fontSize:'0.9rem',marginBottom:'0.5rem'}}>수집된 데이터가 없습니다</p>
              <p style={{fontSize:'0.75rem',opacity:0.6}}>
                ⚠️ data.go.kr API 키에 서버 IP 허용 등록이 필요합니다.<br/>
                등록 후 상단 🔄 수집 버튼을 클릭하세요.
              </p>
            </div>
          ) : (
            <div style={{overflowX:'auto'}}>
              <table className="premium-table" style={{minWidth:'800px'}}>
                <thead>
                  <tr>
                    <th style={{position:'sticky',left:0,background:'var(--bg-dark)',minWidth:'120px'}}>업종</th>
                    {data.slice(0,12).map(d => (
                      <th key={d.ym} style={{textAlign:'right',minWidth:'70px',fontSize:'0.7rem'}}>{d.ym}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {MAIN_SECTORS.map(col => {
                    const vals = data.slice(0,12).map(d => d.sectors?.[col]?.value);
                    const latest = vals[0];
                    const prev   = vals[1];
                    const yoy    = vals[11];
                    const momPct = (latest != null && prev != null && prev !== 0)
                      ? (latest - prev) / prev * 100 : null;
                    const yoyPct = (latest != null && yoy != null && yoy !== 0)
                      ? (latest - yoy) / yoy * 100 : null;
                    return (
                      <tr key={col} style={{cursor:'pointer'}} onClick={() => { setTrendSector(col); setTab('trend'); }}>
                        <td style={{position:'sticky',left:0,background:'var(--bg-dark)',fontWeight:600,fontSize:'0.8rem'}}>
                          {SECTOR_LABELS[col]}
                        </td>
                        {data.slice(0,12).map((d, i) => {
                          const v = d.sectors?.[col]?.value;
                          return (
                            <td key={d.ym} style={{textAlign:'right',fontSize:'0.77rem',
                              color: i===0 && momPct != null ? clr(momPct) : 'inherit'}}>
                              {v != null ? Math.round(v).toLocaleString() : '-'}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── 업종 추이 탭 ── */}
      {tab === 'trend' && (
        <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
          <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem'}}>
            {MAIN_SECTORS.map(col => (
              <button key={col} onClick={() => setTrendSector(col)} style={{
                padding:'0.25rem 0.7rem',borderRadius:'20px',fontSize:'0.74rem',cursor:'pointer',
                background: trendSector===col ? 'rgba(45,212,191,0.2)' : 'rgba(255,255,255,0.04)',
                border: trendSector===col ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
                color: trendSector===col ? 'var(--accent-mint)' : 'var(--text-secondary)',
              }}>{SECTOR_LABELS[col]}</button>
            ))}
          </div>
          {loading ? (
            <div style={{textAlign:'center',padding:'3rem',color:'var(--accent-mint)'}}>로딩 중...</div>
          ) : !trendData || trendData.data?.length === 0 ? (
            <div style={{textAlign:'center',padding:'3rem',color:'var(--text-secondary)'}}>데이터 없음</div>
          ) : (
            <div className="glass-panel" style={{overflow:'auto'}}>
              <div style={{padding:'0.7rem 1rem',borderBottom:'1px solid var(--glass-border)',fontWeight:700,fontSize:'0.85rem'}}>
                {trendData.label} ({trendData.saeopjang_wk_fg}) 추이
              </div>
              <table className="premium-table">
                <thead>
                  <tr>
                    <th>연월</th>
                    <th style={{textAlign:'right'}}>인원/개소</th>
                    <th style={{textAlign:'right'}}>MoM</th>
                    <th style={{textAlign:'right'}}>YoY</th>
                  </tr>
                </thead>
                <tbody>
                  {trendData.data.map((d, i) => (
                    <tr key={d.ym} style={{background: i===0 ? 'rgba(45,212,191,0.05)' : 'transparent'}}>
                      <td style={{fontWeight: i===0 ? 700 : 400}}>{d.ym}</td>
                      <td style={{textAlign:'right'}}>{Math.round(d.value).toLocaleString()}</td>
                      <td style={{textAlign:'right',color:clr(d.mom_pct)}}>{pctFmt(d.mom_pct)}</td>
                      <td style={{textAlign:'right',color:clr(d.yoy_pct),fontWeight: Math.abs(d.yoy_pct||0)>=3?700:400}}>{pctFmt(d.yoy_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── 요약 탭 ── */}
      {tab === 'summary' && (
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(240px,1fr))',gap:'0.75rem'}}>
          {summary?.data?.length === 0 && (
            <div style={{gridColumn:'1/-1',textAlign:'center',padding:'3rem',color:'var(--text-secondary)'}}>
              수집된 데이터가 없습니다
            </div>
          )}
          {(summary?.data||[]).map((d, i) => (
            <div key={i} className="glass-panel" style={{padding:'1rem'}}>
              <div style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginBottom:'0.3rem'}}>
                {d.ym} · {d.sj_gy_fg==='3'?'고용보험':'산재보험'} · {d.saeopjang_wk_fg}
              </div>
              <div style={{fontSize:'1.3rem',fontWeight:700,color:'var(--accent-mint)'}}>
                {d.total != null ? Math.round(d.total).toLocaleString() : '-'}
              </div>
              <div style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginTop:'0.2rem'}}>명 / 개소</div>
            </div>
          ))}
        </div>
      )}

      {/* 안내 */}
      <div className="glass-panel" style={{padding:'0.8rem 1rem',fontSize:'0.72rem',color:'rgba(255,255,255,0.35)'}}>
        💡 데이터 출처: 고용노동부 (data.go.kr) · 업종별 고용/산재보험 월별 통계 ·
        API 사용을 위해 서버 IP를 data.go.kr 포털에서 허용 IP로 등록 필요
      </div>
    </div>
  );
};

export default EmploymentMonitor;
