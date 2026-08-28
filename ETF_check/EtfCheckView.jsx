import React, { useState, useEffect } from 'react';

const EtfCheckView = () => {
  const [activeTab, setActiveTab] = useState(1);
  const [subTab1, setSubTab1] = useState('kospi'); // 'kospi' | 'kosdaq'
  const [subTab2, setSubTab2] = useState('1d'); // '1d' | '5d'
  const [subTab3, setSubTab3] = useState('1d'); // '1d' | '5d'

  // Tab5 검색 상태
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [etfListData, setEtfListData] = useState(null);   // 선택 종목 ETF 목록
  const [etfListLoading, setEtfListLoading] = useState(false);
  const [etfListCode, setEtfListCode] = useState(null);   // 현재 조회 중인 종목코드
  
  const [data, setData] = useState({
    tab1: null,
    tab2: null,
    tab3: null,
    tab4: null,
    loading: true,
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
        
        setData({
          tab1: res1,
          tab2: res2,
          tab3: res3,
          tab4: res4,
          loading: false
        });
      } catch (err) {
        console.error("ETF Check 데이터 로드 실패", err);
        setData(prev => ({ ...prev, loading: false }));
      }
    };
    fetchData();
  }, []);

  // 공통 스타일
  const containerStyle = {
    padding: '1rem',
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: '16px',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    color: '#fff',
    fontFamily: 'inherit',
    minHeight: '400px'
  };

  const mainTabContainerStyle = {
    display: 'flex',
    gap: '0.8rem',
    overflowX: 'auto',
    paddingBottom: '1rem',
    marginBottom: '1rem',
    borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
    scrollbarWidth: 'thin'
  };

  const subTabContainerStyle = {
    display: 'flex',
    gap: '0.5rem',
    marginBottom: '1rem'
  };

  const tabStyle = (isActive) => ({
    padding: '0.6rem 1.2rem',
    borderRadius: '8px',
    fontSize: '0.9rem',
    fontWeight: isActive ? 600 : 400,
    whiteSpace: 'nowrap',
    cursor: 'pointer',
    transition: 'all 0.2s',
    background: isActive ? 'rgba(45, 212, 191, 0.15)' : 'transparent',
    color: isActive ? '#2dd4bf' : 'rgba(255, 255, 255, 0.6)',
    borderBottom: isActive ? '2px solid #2dd4bf' : '2px solid transparent'
  });

  const subTabStyle = (isActive) => ({
    padding: '0.4rem 0.8rem',
    borderRadius: '20px',
    fontSize: '0.8rem',
    cursor: 'pointer',
    transition: 'all 0.2s',
    background: isActive ? 'rgba(45, 212, 191, 0.2)' : 'rgba(255, 255, 255, 0.05)',
    color: isActive ? '#2dd4bf' : 'rgba(255, 255, 255, 0.7)',
    border: isActive ? '1px solid #2dd4bf' : '1px solid transparent'
  });

  const tableStyle = {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '0.85rem'
  };

  const thStyle = {
    padding: '0.6rem 0.5rem',
    textAlign: 'right',
    color: 'var(--text-secondary, rgba(255,255,255,0.5))',
    borderBottom: '1px solid rgba(255,255,255,0.1)',
    fontWeight: 500
  };

  const tdStyle = {
    padding: '0.6rem 0.5rem',
    textAlign: 'right',
    color: 'rgba(255,255,255,0.85)',
    borderBottom: '1px solid rgba(255,255,255,0.05)'
  };

  const formatNumber = (num) => num ? num.toLocaleString() : '-';
  const formatRatio = (num) => num ? num.toFixed(2) + '%' : '-';

  const handleSearch = async (q) => {
    const query = (q || searchQuery).trim();
    if (!query) return;
    setSearchLoading(true);
    setSearchResults(null);
    setEtfListData(null);
    setEtfListCode(null);
    try {
      const res = await fetch(`/api/etf-check/search?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      setSearchResults(data);
    } catch (e) {
      console.error('검색 실패', e);
    } finally {
      setSearchLoading(false);
    }
  };

  const handleFetchEtfList = async (stockCode) => {
    if (etfListCode === stockCode && etfListData) return; // 이미 조회됨
    setEtfListCode(stockCode);
    setEtfListLoading(true);
    setEtfListData(null);
    try {
      const res = await fetch(`/api/etf-check/etf-list/${stockCode}`);
      const data = await res.json();
      setEtfListData(data);
    } catch (e) {
      setEtfListData({ error: '조회 실패', etf_list: [] });
    } finally {
      setEtfListLoading(false);
    }
  };

  if (data.loading) {
    return <div style={containerStyle}>데이터를 불러오는 중입니다...</div>;
  }

  return (
    <div style={containerStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>📊 ETF Check 대시보드</h2>
        <span style={{ fontSize: '0.8rem', color: 'rgba(255, 255, 255, 0.4)' }}>
          최근 수집일: {data.tab1?.date || '-'}
        </span>
      </div>

      <div style={mainTabContainerStyle}>
        <div style={tabStyle(activeTab === 1)} onClick={() => setActiveTab(1)}>ETF 편입액 기준</div>
        <div style={tabStyle(activeTab === 2)} onClick={() => setActiveTab(2)}>ETF 편입액 증가</div>
        <div style={tabStyle(activeTab === 3)} onClick={() => setActiveTab(3)}>시총대비 증가%</div>
        <div style={tabStyle(activeTab === 4)} onClick={() => setActiveTab(4)}>시총대비 비중%</div>
        <div style={tabStyle(activeTab === 5)} onClick={() => setActiveTab(5)}>종목 검색</div>
      </div>

      {/* 탭 1 */}
      {activeTab === 1 && (
        <div className="fade-in">
          <div style={subTabContainerStyle}>
            <div style={subTabStyle(subTab1 === 'kospi')} onClick={() => setSubTab1('kospi')}>코스피</div>
            <div style={subTabStyle(subTab1 === 'kosdaq')} onClick={() => setSubTab1('kosdaq')}>코스닥</div>
          </div>
          <div style={{overflowX: 'auto'}}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left'}}>종목명</th>
                  <th style={thStyle}>현재가</th>
                  <th style={thStyle}>ETF 편입금액(억)</th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>시총대비 비중</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab1?.[subTab1] || []).map((row, i) => (
                  <tr key={row.stock_code}>
                    <td style={{...tdStyle, textAlign:'left'}}>
                      <div>{row.stock_name}</div>
                      <div style={{fontSize:'0.7rem', color:'rgba(255,255,255,0.4)'}}>{row.stock_code}</div>
                    </td>
                    <td style={tdStyle}>{formatNumber(row.current_price)}</td>
                    <td style={{...tdStyle, color:'#2dd4bf', fontWeight:600}}>{formatNumber(row.etf_amount)}</td>
                    <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                    <td style={tdStyle}>{formatRatio(row.mktcap_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 2 */}
      {activeTab === 2 && (() => {
        const d2 = data.tab2?.dates || {};
        const prevDate2 = subTab2 === '1d' ? d2['1d'] : d2['5d'];
        const curDate2  = d2.latest;
        return (
          <div className="fade-in">
            <div style={{ display:'flex', alignItems:'center', gap:'1rem', flexWrap:'wrap', marginBottom:'0.5rem' }}>
              <div style={subTabContainerStyle}>
                <div style={subTabStyle(subTab2 === '1d')} onClick={() => setSubTab2('1d')}>1일 전 대비</div>
                <div style={subTabStyle(subTab2 === '5d')} onClick={() => setSubTab2('5d')}>5일 전 대비</div>
              </div>
              {prevDate2 && curDate2 && (
                <span style={{ fontSize:'0.78rem', color:'rgba(255,255,255,0.45)', marginLeft:'0.5rem' }}>
                  <span style={{ color:'rgba(255,255,255,0.3)' }}>{prevDate2}</span>
                  <span style={{ margin:'0 0.4rem', color:'rgba(255,255,255,0.2)' }}>→</span>
                  <span style={{ color:'#2dd4bf', fontWeight:600 }}>{curDate2}</span>
                  <span style={{ marginLeft:'0.4rem', color:'rgba(255,255,255,0.3)' }}>기준</span>
                </span>
              )}
            </div>
            <div style={{overflowX: 'auto'}}>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={{...thStyle, textAlign:'left'}}>종목명</th>
                    <th style={thStyle}>과거 편입금액(억)<br/><span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.35)',fontWeight:400}}>{prevDate2||'-'}</span></th>
                    <th style={thStyle}>현재 편입금액(억)<br/><span style={{fontSize:'0.68rem',color:'#2dd4bf',fontWeight:400}}>{curDate2||'-'}</span></th>
                    <th style={thStyle}>증가액(억)</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.tab2?.[subTab2] || []).map((row) => (
                    <tr key={row.stock_code}>
                      <td style={{...tdStyle, textAlign:'left'}}>
                        <div>{row.stock_name}</div>
                        <div style={{fontSize:'0.7rem', color:'rgba(255,255,255,0.4)'}}>{row.stock_code}</div>
                      </td>
                      <td style={tdStyle}>{formatNumber(row.prev_amount)}</td>
                      <td style={tdStyle}>{formatNumber(row.current_amount)}</td>
                      <td style={{...tdStyle, color: row.amount_diff > 0 ? '#ff4d4f' : '#2dd4bf', fontWeight:600}}>
                        {row.amount_diff > 0 ? '+' : ''}{formatNumber(row.amount_diff)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })()}

      {/* 탭 3 */}
      {activeTab === 3 && (() => {
        const d3 = data.tab3?.dates || {};
        const prevDate3 = subTab3 === '1d' ? d3['1d'] : d3['5d'];
        const curDate3  = d3.latest;
        return (
          <div className="fade-in">
            <div style={{ display:'flex', alignItems:'center', gap:'1rem', flexWrap:'wrap', marginBottom:'0.5rem' }}>
              <div style={subTabContainerStyle}>
                <div style={subTabStyle(subTab3 === '1d')} onClick={() => setSubTab3('1d')}>1일 전 대비</div>
                <div style={subTabStyle(subTab3 === '5d')} onClick={() => setSubTab3('5d')}>5일 전 대비</div>
              </div>
              {prevDate3 && curDate3 && (
                <span style={{ fontSize:'0.78rem', color:'rgba(255,255,255,0.45)', marginLeft:'0.5rem' }}>
                  <span style={{ color:'rgba(255,255,255,0.3)' }}>{prevDate3}</span>
                  <span style={{ margin:'0 0.4rem', color:'rgba(255,255,255,0.2)' }}>→</span>
                  <span style={{ color:'#2dd4bf', fontWeight:600 }}>{curDate3}</span>
                  <span style={{ marginLeft:'0.4rem', color:'rgba(255,255,255,0.3)' }}>기준</span>
                </span>
              )}
            </div>
            <div style={{overflowX: 'auto'}}>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={{...thStyle, textAlign:'left'}}>종목명</th>
                    <th style={thStyle}>시가총액(억)</th>
                    <th style={thStyle}>편입금액 증가액(억)<br/><span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.35)',fontWeight:400}}>{prevDate3||'-'} → {curDate3||'-'}</span></th>
                    <th style={thStyle}>시총대비 증가율</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.tab3?.[subTab3] || []).map((row) => (
                    <tr key={row.stock_code}>
                      <td style={{...tdStyle, textAlign:'left'}}>
                        <div>{row.stock_name}</div>
                        <div style={{fontSize:'0.7rem', color:'rgba(255,255,255,0.4)'}}>{row.stock_code}</div>
                      </td>
                      <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                      <td style={tdStyle}>
                        {row.amount_diff > 0 ? '+' : ''}{formatNumber(row.amount_diff)}
                      </td>
                      <td style={{...tdStyle, color: row.ratio_increase > 0 ? '#ff4d4f' : '#2dd4bf', fontWeight:600}}>
                        {row.ratio_increase > 0 ? '+' : ''}{formatRatio(row.ratio_increase)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })()}

      {/* 탭 4 */}
      {activeTab === 4 && (
        <div className="fade-in">
          <div style={{overflowX: 'auto', marginTop: '1rem'}}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left'}}>종목명</th>
                  <th style={thStyle}>현재가</th>
                  <th style={thStyle}>ETF 편입금액(억)</th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>시총대비 비중</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab4?.top || []).map((row, i) => (
                  <tr key={row.stock_code}>
                    <td style={{...tdStyle, textAlign:'left'}}>
                      <div>{row.stock_name}</div>
                      <div style={{fontSize:'0.7rem', color:'rgba(255,255,255,0.4)'}}>{row.stock_code}</div>
                    </td>
                    <td style={tdStyle}>{formatNumber(row.current_price)}</td>
                    <td style={tdStyle}>{formatNumber(row.etf_amount)}</td>
                    <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                    <td style={{...tdStyle, color:'#2dd4bf', fontWeight:600}}>{formatRatio(row.calc_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 5: 종목 검색 */}
      {activeTab === 5 && (
        <div className="fade-in">
          {/* 검색 입력 */}
          <div style={{ display:'flex', gap:'0.5rem', marginBottom:'1.2rem' }}>
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              placeholder="종목명 또는 종목코드 입력 (예: 삼성전자, 005930)"
              style={{
                flex: 1, padding: '0.6rem 1rem', borderRadius: '8px',
                background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)',
                color: '#fff', fontSize: '0.9rem', outline: 'none'
              }}
            />
            <button
              onClick={() => handleSearch()}
              disabled={searchLoading}
              style={{
                padding: '0.6rem 1.4rem', borderRadius: '8px', border: 'none',
                background: '#2dd4bf', color: '#000', fontWeight: 600,
                cursor: searchLoading ? 'wait' : 'pointer', fontSize: '0.9rem'
              }}
            >
              {searchLoading ? '검색 중...' : '검색'}
            </button>
          </div>

          {/* 검색 결과 테이블 */}
          {searchResults && (
            <div style={{ marginBottom: '1.5rem' }}>
              <div style={{ fontSize:'0.8rem', color:'rgba(255,255,255,0.45)', marginBottom:'0.5rem' }}>
                검색 결과 {searchResults.rows?.length || 0}건 | 기준일: {searchResults.date || '-'}
                {searchResults.compare_date && ` | 비교일: ${searchResults.compare_date}`}
              </div>
              <div style={{overflowX: 'auto'}}>
                <table style={tableStyle}>
                  <thead>
                    <tr>
                      <th style={{...thStyle, textAlign:'left'}}>종목명</th>
                      <th style={thStyle}>현재가</th>
                      <th style={thStyle}>ETF 편입금액(억)</th>
                      <th style={thStyle}>시총대비 비중</th>
                      <th style={thStyle}>편입액 변화(억)</th>
                      <th style={{...thStyle, textAlign:'center'}}>편입 ETF 목록</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(searchResults.rows || []).map(row => (
                      <tr key={row.stock_code}
                        style={{ background: etfListCode === row.stock_code ? 'rgba(45,212,191,0.06)' : 'transparent' }}>
                        <td style={{...tdStyle, textAlign:'left'}}>
                          <div style={{fontWeight:600}}>{row.stock_name}</div>
                          <div style={{fontSize:'0.7rem', color:'rgba(255,255,255,0.4)'}}>{row.stock_code}</div>
                        </td>
                        <td style={tdStyle}>
                          {formatNumber(row.current_price)}
                          {row.price_change_pct != null && (
                            <span style={{
                              marginLeft:'0.3rem', fontSize:'0.75rem',
                              color: row.price_change_pct > 0 ? '#ff4d4f' : row.price_change_pct < 0 ? '#2dd4bf' : 'rgba(255,255,255,0.4)'
                            }}>
                              {row.price_change_pct > 0 ? '+' : ''}{row.price_change_pct?.toFixed(2)}%
                            </span>
                          )}
                        </td>
                        <td style={{...tdStyle, color:'#2dd4bf', fontWeight:600}}>{formatNumber(row.etf_amount)}</td>
                        <td style={tdStyle}>{formatRatio(row.mktcap_ratio)}</td>
                        <td style={{
                          ...tdStyle,
                          color: row.amount_diff == null ? 'rgba(255,255,255,0.3)' : row.amount_diff > 0 ? '#ff4d4f' : '#2dd4bf',
                          fontWeight: row.amount_diff != null ? 600 : 400
                        }}>
                          {row.amount_diff == null ? '-' : `${row.amount_diff > 0 ? '+' : ''}${formatNumber(row.amount_diff)}`}
                        </td>
                        <td style={{...tdStyle, textAlign:'center'}}>
                          <button
                            onClick={() => handleFetchEtfList(row.stock_code)}
                            disabled={etfListLoading && etfListCode === row.stock_code}
                            style={{
                              padding: '0.25rem 0.7rem', borderRadius: '12px', border: 'none',
                              background: etfListCode === row.stock_code ? '#2dd4bf' : 'rgba(45,212,191,0.2)',
                              color: etfListCode === row.stock_code ? '#000' : '#2dd4bf',
                              fontSize: '0.78rem', cursor: 'pointer', fontWeight: 600
                            }}
                          >
                            {etfListLoading && etfListCode === row.stock_code ? '조회 중...' : 'ETF 보기'}
                          </button>
                        </td>
                      </tr>
                    ))}
                    {searchResults.rows?.length === 0 && (
                      <tr><td colSpan={6} style={{...tdStyle, textAlign:'center', color:'rgba(255,255,255,0.4)'}}>검색 결과 없음</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ETF 목록 패널 */}
          {etfListData && (
            <div style={{
              background: 'rgba(45,212,191,0.06)', borderRadius: '10px',
              border: '1px solid rgba(45,212,191,0.2)', padding: '1rem', marginTop: '0.5rem'
            }}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.8rem' }}>
                <div>
                  <span style={{ fontWeight:700, color:'#2dd4bf', fontSize:'1rem' }}>
                    {etfListData.stock_name || etfListCode}
                  </span>
                  <span style={{ marginLeft:'0.5rem', fontSize:'0.8rem', color:'rgba(255,255,255,0.4)' }}>
                    {etfListCode}
                  </span>
                  {etfListData.etf_count != null && (
                    <span style={{
                      marginLeft:'0.8rem', padding:'0.15rem 0.5rem', borderRadius:'10px',
                      background:'rgba(45,212,191,0.2)', color:'#2dd4bf', fontSize:'0.78rem'
                    }}>
                      약 {etfListData.etf_count}개 ETF 편입
                    </span>
                  )}
                  {etfListData.etf_amount_total != null && (
                    <span style={{ marginLeft:'0.6rem', fontSize:'0.8rem', color:'rgba(255,255,255,0.5)' }}>
                      총 {formatNumber(etfListData.etf_amount_total)}억원
                    </span>
                  )}
                </div>
                <button
                  onClick={() => { setEtfListData(null); setEtfListCode(null); }}
                  style={{ background:'none', border:'none', color:'rgba(255,255,255,0.4)', cursor:'pointer', fontSize:'1.1rem' }}
                >✕</button>
              </div>

              {etfListData.error ? (
                <div style={{ color:'#ff4d4f', fontSize:'0.85rem' }}>{etfListData.error}</div>
              ) : etfListData.etf_list?.length > 0 ? (
                <div style={{ display:'flex', flexWrap:'wrap', gap:'0.5rem' }}>
                  {etfListData.etf_list.map((etf, i) => (
                    <div key={i} style={{
                      padding:'0.5rem 0.9rem', borderRadius:'10px',
                      background:'rgba(255,255,255,0.07)', border:'1px solid rgba(255,255,255,0.12)',
                      fontSize:'0.85rem'
                    }}>
                      <div style={{ fontSize:'0.72rem', color:'rgba(255,255,255,0.4)', marginBottom:'0.2rem' }}>
                        {etf.label}
                      </div>
                      <div style={{ color:'#fff', fontWeight:600 }}>{etf.name}</div>
                      {etf.value && (
                        <div style={{ color:'#2dd4bf', fontSize:'0.78rem', marginTop:'0.15rem' }}>
                          {etf.type === 'ratio' ? '비중 ' : '편입금액 '}{etf.value}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ color:'rgba(255,255,255,0.4)', fontSize:'0.85rem' }}>
                  TOP ETF 정보를 파싱하지 못했습니다.
                  <br/>
                  <span style={{ fontSize:'0.78rem', color:'rgba(255,255,255,0.3)' }}>
                    DB 기준 약 {etfListData.etf_count || '?'}개 ETF가 편입 중
                  </span>
                </div>
              )}
              <div style={{ marginTop:'0.7rem', fontSize:'0.72rem', color:'rgba(255,255,255,0.25)' }}>
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
