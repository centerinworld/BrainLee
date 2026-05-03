import React, { useState, useEffect } from 'react';

const EtfCheckView = () => {
  const [activeTab, setActiveTab] = useState(1);
  const [subTab1, setSubTab1] = useState('kospi'); // 'kospi' | 'kosdaq'
  const [subTab2, setSubTab2] = useState('1d'); // '1d' | '5d'
  const [subTab3, setSubTab3] = useState('1d'); // '1d' | '5d'
  
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
    color: '#e2e8f0',
    borderBottom: '2px solid rgba(59,130,246,0.5)',
    fontWeight: 600,
    background: 'rgba(30, 58, 138, 0.4)',
    whiteSpace: 'nowrap'
  };

  const tdStyle = {
    padding: '0.6rem 0.5rem',
    textAlign: 'right',
    color: 'rgba(255,255,255,0.85)',
    borderBottom: '1px solid rgba(255,255,255,0.05)'
  };

  const formatNumber = (num) => num ? num.toLocaleString() : '-';
  const formatRatio = (num) => num ? num.toFixed(2) + '%' : '-';

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
                  <th style={{...thStyle, textAlign:'left', borderRight: '1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>현재가</th>
                  <th style={thStyle}>ETF 편입금액(억)</th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>시총대비 비중</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab1?.[subTab1] || []).map((row, i) => (
                  <tr key={row.stock_code}>
                    <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight: '1px solid rgba(59,130,246,0.2)'}}>
                      <div>{row.stock_name}</div>
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
      {activeTab === 2 && (
        <div className="fade-in">
          <div style={subTabContainerStyle}>
            <div style={subTabStyle(subTab2 === '1d')} onClick={() => setSubTab2('1d')}>1일 전 대비</div>
            <div style={subTabStyle(subTab2 === '5d')} onClick={() => setSubTab2('5d')}>5일 전 대비</div>
          </div>
          <div style={{overflowX: 'auto'}}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight: '1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>과거 편입금액(억)</th>
                  <th style={thStyle}>현재 편입금액(억)</th>
                  <th style={thStyle}>증가액(억)</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab2?.[subTab2] || []).length > 0 ? (
                  (data.tab2?.[subTab2] || []).map((row, i) => (
                    <tr key={row.stock_code}>
                      <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight: '1px solid rgba(59,130,246,0.2)'}}>
                        <div>{row.stock_name}</div>
                      </td>
                      <td style={tdStyle}>{formatNumber(row.prev_amount)}</td>
                      <td style={tdStyle}>{formatNumber(row.current_amount)}</td>
                      <td style={{...tdStyle, color: row.amount_diff > 0 ? '#ff4d4f' : '#2dd4bf', fontWeight:600}}>
                        {row.amount_diff > 0 ? '+' : ''}{formatNumber(row.amount_diff)}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="4" style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>
                      과거 데이터가 부족하여 증가분을 계산할 수 없습니다. (최소 2일치 수집 필요)
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 3 */}
      {activeTab === 3 && (
        <div className="fade-in">
          <div style={subTabContainerStyle}>
            <div style={subTabStyle(subTab3 === '1d')} onClick={() => setSubTab3('1d')}>1일 전 대비</div>
            <div style={subTabStyle(subTab3 === '5d')} onClick={() => setSubTab3('5d')}>5일 전 대비</div>
          </div>
          <div style={{overflowX: 'auto'}}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight: '1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>편입금액 증가액(억)</th>
                  <th style={thStyle}>시총대비 증가율</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab3?.[subTab3] || []).length > 0 ? (
                  (data.tab3?.[subTab3] || []).map((row, i) => (
                    <tr key={row.stock_code}>
                      <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight: '1px solid rgba(59,130,246,0.2)'}}>
                        <div>{row.stock_name}</div>
                      </td>
                      <td style={tdStyle}>{formatNumber(row.market_cap)}</td>
                      <td style={tdStyle}>
                        {row.amount_diff > 0 ? '+' : ''}{formatNumber(row.amount_diff)}
                      </td>
                      <td style={{...tdStyle, color: row.ratio_increase > 0 ? '#ff4d4f' : '#2dd4bf', fontWeight:600}}>
                        {row.ratio_increase > 0 ? '+' : ''}{formatRatio(row.ratio_increase)}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="4" style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>
                      과거 데이터가 부족하여 증가분을 계산할 수 없습니다. (최소 2일치 수집 필요)
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 탭 4 */}
      {activeTab === 4 && (
        <div className="fade-in">
          <div style={{overflowX: 'auto', marginTop: '1rem'}}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left', borderRight: '1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                  <th style={thStyle}>현재가</th>
                  <th style={thStyle}>ETF 편입금액(억)</th>
                  <th style={thStyle}>시가총액(억)</th>
                  <th style={thStyle}>시총대비 비중</th>
                </tr>
              </thead>
              <tbody>
                {(data.tab4?.top || []).map((row, i) => (
                  <tr key={row.stock_code}>
                    <td style={{...tdStyle, textAlign:'left', fontWeight:600, borderRight: '1px solid rgba(59,130,246,0.2)'}}>
                      <div>{row.stock_name}</div>
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
    </div>
  );
};

export default EtfCheckView;
