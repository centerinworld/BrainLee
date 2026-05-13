import React, { useState, useEffect } from 'react';

const EtfCheckView = () => {
  const [activeTab, setActiveTab] = useState(1);
  const [subTab1, setSubTab1] = useState('kospi');
  const [subTab2, setSubTab2] = useState('1d');
  const [tab2Dir, setTab2Dir]   = useState('inc'); // 'inc' | 'dec'
  const [subTab3, setSubTab3] = useState('1d');
  const [tab3Dir, setTab3Dir]   = useState('inc');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchData, setSearchData] = useState({ rows: [], date: null, compare_date: null });
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [etfListData, setEtfListData] = useState(null);
  const [etfListLoading, setEtfListLoading] = useState(false);
  const [etfListCode, setEtfListCode] = useState(null);

  const [data, setData] = useState({
    tab1: null, tab2: null, tab3: null, tab4: null, loading: true,
  });

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [res1, res2, res3, res4] = await Promise.all([
          fetch('/api/etf-check/tab1').then(r => r.json()),
          fetch('/api/etf-check/tab2').then(r => r.json()),
          fetch('/api/etf-check/tab3').then(r => r.json()),
          fetch('/api/etf-check/tab4').then(r => r.json())
        ]);
        setData({ tab1: res1, tab2: res2, tab3: res3, tab4: res4, loading: false });
      } catch (err) {
        console.error("ETF Check 데이터 로드 실패", err);
        setData(prev => ({ ...prev, loading: false }));
      }
    };
    fetchData();
  }, []);

  const containerStyle = {
    padding: '1rem',
    background: 'rgba(255,255,255,0.02)',
    borderRadius: '16px',
    border: '1px solid rgba(255,255,255,0.08)',
    color: '#fff',
    fontFamily: 'inherit',
    minHeight: '400px'
  };
  const mainTabContainerStyle = {
    display: 'flex', gap: '0.8rem', overflowX: 'auto',
    paddingBottom: '1rem', marginBottom: '1rem',
    borderBottom: '1px solid rgba(255,255,255,0.1)', scrollbarWidth: 'thin'
  };
  const subTabContainerStyle = { display: 'flex', gap: '0.5rem', marginBottom: '1rem' };
  const tabStyle = (isActive) => ({
    padding: '0.6rem 1.2rem', borderRadius: '8px', fontSize: '0.9rem',
    fontWeight: isActive ? 600 : 400, whiteSpace: 'nowrap', cursor: 'pointer',
    transition: 'all 0.2s',
    background: isActive ? 'rgba(45,212,191,0.15)' : 'rgba(255,255,255,0.04)',
    border: isActive ? '1px solid rgba(45,212,191,0.5)' : '1px solid rgba(255,255,255,0.08)',
    color: isActive ? '#2dd4bf' : 'rgba(255,255,255,0.6)',
  });
  const subTabStyle = (isActive) => ({
    padding: '0.4rem 0.9rem', borderRadius: '6px', fontSize: '0.82rem',
    cursor: 'pointer', fontWeight: isActive ? 600 : 400,
    background: isActive ? 'rgba(99,102,241,0.2)' : 'rgba(255,255,255,0.04)',
    border: isActive ? '1px solid rgba(99,102,241,0.5)' : '1px solid rgba(255,255,255,0.1)',
    color: isActive ? '#a5b4fc' : 'rgba(255,255,255,0.5)',
  });
  const tableStyle = { width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' };
  const thStyle = {
    padding: '0.6rem 0.5rem', textAlign: 'right', color: '#e2e8f0',
    borderBottom: '2px solid rgba(59,130,246,0.5)', fontWeight: 600,
    background: 'rgba(10,18,50,0.98)', whiteSpace: 'nowrap',
    position: 'sticky', top: 0, zIndex: 10,
  };
  const tdStyle = {
    padding: '0.6rem 0.5rem', textAlign: 'right',
    color: 'rgba(255,255,255,0.85)', borderBottom: '1px solid rgba(255,255,255,0.05)'
  };
  // 구분선 th/td (현재가~시가총액 사이)
  const separatorTh = { ...thStyle, width: '4px', padding: '0', borderLeft: '2px solid rgba(99,102,241,0.4)', borderRight: '2px solid rgba(99,102,241,0.4)' };
  const separatorTd = { ...tdStyle, width: '4px', padding: '0', borderLeft: '2px solid rgba(99,102,241,0.15)', borderRight: '2px solid rgba(99,102,241,0.15)' };

  const formatNumber = (num) => num ? num.toLocaleString() : '-';
  const formatRatio = (num) => num ? num.toFixed(2) + '%' : '-';
  const formatSignedNumber = (num) => {
    if (num === null || num === undefined) return '-';
    return `${num > 0 ? '+' : ''}${num.toLocaleString()}`;
  };
  const formatPct = (pct) => {
    if (pct === null || pct === undefined) return '-';
    const color = pct > 0 ? '#ff4d4f' : pct < 0 ? '#60a5fa' : 'rgba(255,255,255,0.5)';
    return <span style={{ color, fontWeight: 600 }}>{pct > 0 ? '+' : ''}{pct.toFixed(2)}%</span>;
  };
  const formatPriceCell = (price, pct) => {
    const priceText = formatNumber(price);
    if (pct === null || pct === undefined) return priceText;
    return `${priceText} (${pct > 0 ? '+' : ''}${pct.toFixed(2)}%)`;
  };

  const DirToggle = ({ dir, onToggle }) => (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        onClick={onToggle}
        style={{
          padding: '0.35rem 0.7rem', borderRadius: '6px', fontSize: '0.8rem',
          cursor: 'pointer', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.3rem',
          background: dir === 'inc' ? 'rgba(255,77,79,0.15)' : 'rgba(45,212,191,0.15)',
          border: dir === 'inc' ? '1px solid rgba(255,77,79,0.45)' : '1px solid rgba(45,212,191,0.45)',
          color: dir === 'inc' ? '#ff4d4f' : '#2dd4bf',
        }}
      >
        {dir === 'inc' ? '▲ 증가 순' : '▼ 감소 순'}
      </button>
    </div>
  );

  const handleSearch = async (event) => {
    event?.preventDefault();
    const q = searchQuery.trim();
    if (!q) return;
    setSearchLoading(true); setSearchError('');
    setEtfListData(null); setEtfListCode(null);
    try {
      const res = await fetch(`/api/etf-check/search?q=${encodeURIComponent(q)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSearchData(await res.json());
    } catch (err) {
      console.error("ETF 종목 검색 실패", err);
      setSearchError('검색 데이터를 불러오지 못했습니다.');
    } finally { setSearchLoading(false); }
  };

  const handleFetchEtfList = async (stockCode) => {
    if (etfListCode === stockCode && etfListData) return;
    setEtfListCode(stockCode);
    setEtfListLoading(true);
    setEtfListData(null);
    try {
      const res = await fetch(`/api/etf-check/etf-list/${stockCode}`);
      setEtfListData(await res.json());
    } catch (e) {
      setEtfListData({ error: '조회 실패', etf_list: [] });
    } finally { setEtfListLoading(false); }
  };

  if (data.loading) return <div style={containerStyle}>데이터를 불러오는 중입니다...</div>;

  // tab2 데이터 키 선택
  const tab2Key = subTab2 + (tab2Dir === 'dec' ? '_dec' : '');
  const tab3Key = subTab3 + (tab3Dir === 'dec' ? '_dec' : '');

  return (
    <div style={containerStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>📊 ETF Check 대시보드</h2>
        <span style={{ fontSize: '0.8rem', color: 'rgba(255,255,255,0.4)' }}>
          최근 수집일: {data.tab1?.date || '-'}
        </span>
      </div>

      <div style={mainTabContainerStyle}>
        <div style={tabStyle(activeTab === 1)} onClick={() => setActiveTab(1)}>ETF 편입액 기준</div>
        <div style={tabStyle(activeTab === 2)} onClick={() => setActiveTab(2)}>ETF 편입액 증감</div>
        <div style={tabStyle(activeTab === 3)} onClick={() => setActiveTab(3)}>시총대비 증감%</div>
        <div style={tabStyle(activeTab === 4)} onClick={() => setActiveTab(4)}>시총대비 비중%</div>
        <div style={tabStyle(activeTab === 5)} onClick={() => setActiveTab(5)}>종목 검색</div>
      </div>

      {/* 탭 1 — ETF 편입액 기준 */}
      {activeTab === 1 && (
        <div className="fade-in">
          <div style={subTabContainerStyle}>
            <div style={subTabStyle(subTab1 === 'kospi')} onClick={() => setSubTab1('kospi')}>코스피</div>
            <div style={subTabStyle(subTab1 === 'kosdaq')} onClick={() => setSubTab1('kosdaq')}>코스닥</div>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight:'1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>현재가</th>
                  <th style={thStyle}>등락률</th>
                  <th style={separatorTh}></th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>ETF 편입금액(억)</th>
                  <th style={thStyle}>시총대비 비중</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab1?.[subTab1] || []).map((row) => (
                  <tr key={row.stock_code}>
                    <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight:'1px solid rgba(59,130,246,0.2)'}}>
                      {row.stock_name}
                    </td>
                    <td style={tdStyle}>{formatNumber(row.current_price)}</td>
                    <td style={{...tdStyle, padding:'0.6rem 0.7rem'}}>
                      {formatPct(row.price_change_pct)}
                    </td>
                    <td style={separatorTd}></td>
                    <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                    <td style={{...tdStyle, color:'#2dd4bf', fontWeight:600}}>{formatNumber(row.etf_amount)}</td>
                    <td style={tdStyle}>{formatRatio(row.mktcap_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 2 — ETF 편입액 증감 */}
      {activeTab === 2 && (
        <div className="fade-in">
          <div style={{ display:'flex', alignItems:'center', gap:'0.8rem', marginBottom:'1rem', flexWrap:'wrap' }}>
            <div style={{ display:'flex', gap:'0.5rem' }}>
              <div style={subTabStyle(subTab2 === '1d')} onClick={() => setSubTab2('1d')}>1일 전 대비</div>
              <div style={subTabStyle(subTab2 === '5d')} onClick={() => setSubTab2('5d')}>5일 전 대비</div>
            </div>
            <DirToggle dir={tab2Dir} onToggle={() => setTab2Dir(d => d === 'inc' ? 'dec' : 'inc')} />
            {data.tab2?.dates && (
              <span style={{ fontSize:'0.78rem', color:'rgba(255,255,255,0.45)' }}>
                비교기준: {data.tab2.dates[subTab2] || '-'} → {data.tab2.dates.latest || '-'}
              </span>
            )}
          </div>
          <div style={{ overflowX:'auto' }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight:'1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>과거 편입금액(억)<br/><span style={{fontSize:'0.7rem',fontWeight:400,color:'rgba(255,255,255,0.45)'}}>{data.tab2?.dates?.[subTab2] || '-'}</span></th>
                  <th style={thStyle}>현재 편입금액(억)<br/><span style={{fontSize:'0.7rem',fontWeight:400,color:'rgba(255,255,255,0.45)'}}>{data.tab2?.dates?.latest || '-'}</span></th>
                  <th style={thStyle}>증감액(억)</th>
                  <th style={thStyle}>증감률</th>
                  <th style={thStyle}>시총대비 비중</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab2?.[tab2Key] || []).length > 0 ? (
                  (data.tab2?.[tab2Key] || []).map((row) => {
                    const chgPct = row.prev_amount > 0 ? (row.amount_diff / row.prev_amount * 100) : null;
                    const mktPct = row.market_cap > 0 ? (row.amount_diff / row.market_cap * 100) : null;
                    const diffColor = row.amount_diff > 0 ? '#ff4d4f' : row.amount_diff < 0 ? '#60a5fa' : 'rgba(255,255,255,0.5)';
                    return (
                      <tr key={row.stock_code}>
                        <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight:'1px solid rgba(59,130,246,0.2)'}}>
                          {row.stock_name}
                        </td>
                        <td style={tdStyle}>{formatNumber(row.prev_amount)}</td>
                        <td style={tdStyle}>{formatNumber(row.current_amount)}</td>
                        <td style={{...tdStyle, color: diffColor, fontWeight:600}}>
                          {row.amount_diff > 0 ? '+' : ''}{formatNumber(row.amount_diff)}
                        </td>
                        <td style={{...tdStyle, fontWeight:600}}>
                          {chgPct != null ? formatPct(chgPct) : '-'}
                        </td>
                        <td style={{...tdStyle, fontWeight:600}}>
                          {mktPct != null ? formatPct(mktPct) : '-'}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="6" style={{ padding:'2rem', textAlign:'center', color:'rgba(255,255,255,0.4)' }}>
                      과거 데이터가 부족하여 증감분을 계산할 수 없습니다. (최소 2일치 수집 필요)
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 3 — 시총대비 증감% */}
      {activeTab === 3 && (
        <div className="fade-in">
          <div style={{ display:'flex', alignItems:'center', gap:'0.8rem', marginBottom:'1rem', flexWrap:'wrap' }}>
            <div style={{ display:'flex', gap:'0.5rem' }}>
              <div style={subTabStyle(subTab3 === '1d')} onClick={() => setSubTab3('1d')}>1일 전 대비</div>
              <div style={subTabStyle(subTab3 === '5d')} onClick={() => setSubTab3('5d')}>5일 전 대비</div>
            </div>
            <DirToggle dir={tab3Dir} onToggle={() => setTab3Dir(d => d === 'inc' ? 'dec' : 'inc')} />
            {data.tab3?.dates && (
              <span style={{ fontSize:'0.78rem', color:'rgba(255,255,255,0.45)' }}>
                비교기준: {data.tab3.dates[subTab3] || '-'} → {data.tab3.dates.latest || '-'}
              </span>
            )}
          </div>
          <div style={{ overflowX:'auto' }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight:'1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>편입금액 증감액(억)</th>
                  <th style={thStyle}>시총대비 증감율</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab3?.[tab3Key] || []).length > 0 ? (
                  (data.tab3?.[tab3Key] || []).map((row) => (
                    <tr key={row.stock_code}>
                      <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight:'1px solid rgba(59,130,246,0.2)'}}>
                        {row.stock_name}
                      </td>
                      <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                      <td style={{...tdStyle, color: row.amount_diff > 0 ? '#ff4d4f' : row.amount_diff < 0 ? '#60a5fa' : undefined, fontWeight:600}}>
                        {row.amount_diff > 0 ? '+' : ''}{formatNumber(row.amount_diff)}
                      </td>
                      <td style={{...tdStyle, fontWeight:600}}>
                        {formatPct(row.ratio_increase)}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="4" style={{ padding:'2rem', textAlign:'center', color:'rgba(255,255,255,0.4)' }}>
                      과거 데이터가 부족하여 증감분을 계산할 수 없습니다. (최소 2일치 수집 필요)
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 4 — 시총대비 비중% */}
      {activeTab === 4 && (
        <div className="fade-in">
          <div style={{ overflowX:'auto', marginTop:'1rem' }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight:'1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>현재가</th>
                  <th style={thStyle}>등락률</th>
                  <th style={separatorTh}></th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>ETF 편입금액(억)</th>
                  <th style={{...thStyle, color:'#2dd4bf'}}>시총대비 비중</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab4?.top || []).map((row) => (
                  <tr key={row.stock_code}>
                    <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight:'1px solid rgba(59,130,246,0.2)'}}>
                      {row.stock_name}
                    </td>
                    <td style={tdStyle}>{formatNumber(row.current_price)}</td>
                    <td style={{...tdStyle, padding:'0.6rem 0.7rem'}}>
                      {formatPct(row.price_change_pct)}
                    </td>
                    <td style={separatorTd}></td>
                    <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                    <td style={tdStyle}>{formatNumber(row.etf_amount)}</td>
                    <td style={{...tdStyle, color:'#2dd4bf', fontWeight:600}}>{formatRatio(row.calc_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 5 — 종목 검색 */}
      {activeTab === 5 && (
        <div className="fade-in">
          <form onSubmit={handleSearch} style={{ display:'flex', gap:'0.5rem', marginBottom:'1rem', alignItems:'center' }}>
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="종목명/코드 검색"
              style={{
                width:'220px', padding:'0.55rem 0.7rem', borderRadius:'8px',
                border:'1px solid rgba(255,255,255,0.16)', background:'rgba(15,23,42,0.9)',
                color:'#fff', outline:'none'
              }}
            />
            <button
              type="submit"
              disabled={searchLoading || !searchQuery.trim()}
              style={{
                padding:'0.55rem 1rem', borderRadius:'8px',
                border:'1px solid rgba(45,212,191,0.55)',
                background: searchLoading ? 'rgba(45,212,191,0.12)' : 'rgba(45,212,191,0.85)',
                color: searchLoading ? '#94a3b8' : '#071014',
                fontWeight:700, cursor: searchLoading ? 'default' : 'pointer'
              }}
            >조회</button>
            <span style={{ fontSize:'0.8rem', color:'rgba(255,255,255,0.45)' }}>
              기준일: {searchData.date || data.tab1?.date || '-'}
            </span>
          </form>
          {searchError && <div style={{ marginBottom:'0.8rem', color:'#ff6b6b', fontSize:'0.85rem' }}>{searchError}</div>}
          <div style={{ overflowX:'auto' }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight:'1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>주가(%)</th>
                  <th style={thStyle}>시가총액</th>
                  <th style={thStyle}>편입액</th>
                  <th style={thStyle}>5일 전대비 차이</th>
                  <th style={{...thStyle, textAlign:'center'}}>편입 ETF</th>
                </tr>
              </thead>
              <tbody>
                {searchLoading ? (
                  <tr><td colSpan="6" style={{ padding:'2rem', textAlign:'center', color:'rgba(255,255,255,0.4)' }}>검색 중입니다...</td></tr>
                ) : (searchData.rows || []).length > 0 ? (
                  (searchData.rows || []).map((row) => (
                    <tr key={row.stock_code}
                      style={{ background: etfListCode === row.stock_code ? 'rgba(45,212,191,0.05)' : 'transparent' }}>
                      <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight:'1px solid rgba(59,130,246,0.2)'}}>
                        {row.stock_name} <span style={{ color:'rgba(255,255,255,0.35)', fontWeight:500 }}>{row.stock_code}</span>
                      </td>
                      <td style={{ ...tdStyle, padding:'0.6rem 0.7rem' }}>
                        {formatPriceCell(row.current_price, row.price_change_pct)}
                      </td>
                      <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                      <td style={{...tdStyle, color:'#2dd4bf', fontWeight:600}}>{formatNumber(row.etf_amount)}</td>
                      <td style={{...tdStyle, color: row.amount_diff > 0 ? '#ff4d4f' : row.amount_diff < 0 ? '#60a5fa' : 'rgba(255,255,255,0.85)', fontWeight:600}}>
                        {formatSignedNumber(row.amount_diff)}
                      </td>
                      <td style={{...tdStyle, textAlign:'center'}}>
                        <button
                          onClick={() => handleFetchEtfList(row.stock_code)}
                          disabled={etfListLoading && etfListCode === row.stock_code}
                          style={{
                            padding:'0.25rem 0.65rem', borderRadius:'12px', border:'none',
                            background: etfListCode === row.stock_code ? '#2dd4bf' : 'rgba(45,212,191,0.18)',
                            color: etfListCode === row.stock_code ? '#071014' : '#2dd4bf',
                            fontSize:'0.78rem', cursor:'pointer', fontWeight:600, whiteSpace:'nowrap'
                          }}
                        >
                          {etfListLoading && etfListCode === row.stock_code ? '조회 중...' : 'ETF 보기'}
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan="6" style={{ padding:'2rem', textAlign:'center', color:'rgba(255,255,255,0.4)' }}>검색 결과가 없습니다.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* ETF TOP 패널 */}
          {etfListData && (
            <div style={{
              marginTop:'1rem', background:'rgba(45,212,191,0.06)', borderRadius:'10px',
              border:'1px solid rgba(45,212,191,0.2)', padding:'1rem'
            }}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.8rem' }}>
                <div style={{ display:'flex', alignItems:'center', gap:'0.6rem', flexWrap:'wrap' }}>
                  <span style={{ fontWeight:700, color:'#2dd4bf', fontSize:'1rem' }}>
                    {etfListData.stock_name || etfListCode}
                  </span>
                  <span style={{ fontSize:'0.78rem', color:'rgba(255,255,255,0.35)' }}>{etfListCode}</span>
                  {etfListData.etf_count != null && (
                    <span style={{
                      padding:'0.15rem 0.5rem', borderRadius:'10px',
                      background:'rgba(45,212,191,0.2)', color:'#2dd4bf', fontSize:'0.78rem', fontWeight:600
                    }}>
                      총 {etfListData.etf_count}개 ETF 편입
                    </span>
                  )}
                  {etfListData.etf_amount_total != null && (
                    <span style={{ fontSize:'0.78rem', color:'rgba(255,255,255,0.45)' }}>
                      합계 {formatNumber(etfListData.etf_amount_total)}억원
                    </span>
                  )}
                </div>
                <button onClick={() => { setEtfListData(null); setEtfListCode(null); }}
                  style={{ background:'none', border:'none', color:'rgba(255,255,255,0.35)', cursor:'pointer', fontSize:'1.1rem' }}>✕</button>
              </div>

              {etfListData.error ? (
                <div style={{ color:'#ff6b6b', fontSize:'0.85rem' }}>{etfListData.error}</div>
              ) : etfListData.etf_list?.length > 0 ? (
                <div style={{ display:'flex', flexWrap:'wrap', gap:'0.6rem' }}>
                  {etfListData.etf_list.map((etf, i) => (
                    <div key={i} style={{
                      padding:'0.5rem 0.9rem', borderRadius:'10px',
                      background:'rgba(255,255,255,0.07)', border:'1px solid rgba(255,255,255,0.1)'
                    }}>
                      <div style={{ fontSize:'0.7rem', color:'rgba(255,255,255,0.4)', marginBottom:'0.15rem' }}>{etf.label}</div>
                      <div style={{ color:'#fff', fontWeight:600, fontSize:'0.88rem' }}>{etf.name}</div>
                      {etf.value && (
                        <div style={{ color:'#2dd4bf', fontSize:'0.78rem', marginTop:'0.1rem' }}>
                          {etf.type === 'ratio' ? '비중 ' : '편입금액 '}{etf.value}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ color:'rgba(255,255,255,0.4)', fontSize:'0.85rem' }}>
                  TOP ETF 정보를 가져오지 못했습니다.
                  {etfListData.etf_count && ` (DB 기준 ${etfListData.etf_count}개 편입 중)`}
                </div>
              )}
              <div style={{ marginTop:'0.6rem', fontSize:'0.72rem', color:'rgba(255,255,255,0.22)' }}>
                {etfListData.note}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default EtfCheckView;
