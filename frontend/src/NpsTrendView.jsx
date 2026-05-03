import React, { useState, useEffect } from 'react';

const NpsTrendView = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [date, setDate] = useState('');
  const [sortConfig, setSortConfig] = useState({ key: 'diff_1m', direction: 'desc' });

  useEffect(() => {
    setLoading(true);
    // Fetch top 500 for a general sortable view
    fetch(`/api/employment-v2/trend?sort_by=1m&limit=500`)
      .then(r => r.json())
      .then(d => {
        setRows(d.rows || []);
        setDate(d.date || '');
        setLoading(false);
      })
      .catch(e => {
        console.error("Trend data error", e);
        setLoading(false);
      });
  }, []);

  const handleSort = (key) => {
    let direction = 'desc';
    if (sortConfig.key === key && sortConfig.direction === 'desc') {
      direction = 'asc';
    }
    setSortConfig({ key, direction });
  };

  const sortedRows = React.useMemo(() => {
    let sortableItems = [...rows];
    if (sortConfig !== null) {
      sortableItems.sort((a, b) => {
        const valA = a[sortConfig.key] || 0;
        const valB = b[sortConfig.key] || 0;
        if (valA < valB) return sortConfig.direction === 'asc' ? -1 : 1;
        if (valA > valB) return sortConfig.direction === 'asc' ? 1 : -1;
        return 0;
      });
    }
    return sortableItems;
  }, [rows, sortConfig]);

  const containerStyle = {
    padding: '0',
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: '16px',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    color: '#fff',
    fontFamily: 'inherit',
    overflow: 'hidden'
  };

  const tableStyle = {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '0.85rem'
  };

  const thStyle = {
    padding: '0.7rem 0.8rem',
    textAlign: 'center',
    color: '#e2e8f0',
    borderBottom: '2px solid rgba(59,130,246,0.5)',
    fontWeight: 600,
    background: 'rgba(30, 58, 138, 0.4)',
    whiteSpace: 'nowrap',
    cursor: 'pointer',
    userSelect: 'none'
  };

  const tdStyle = {
    padding: '0.6rem 0.8rem',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
    color: 'rgba(255,255,255,0.85)',
    verticalAlign: 'middle'
  };
  
  const badgeStyle = (market) => {
    const isKospi = market === 'KOSPI' || market === '유가증권';
    return {
      display: 'inline-block',
      fontSize: '0.65rem',
      padding: '0.15rem 0.4rem',
      borderRadius: '4px',
      marginRight: '0.5rem',
      background: isKospi ? 'rgba(59, 130, 246, 0.2)' : 'rgba(16, 185, 129, 0.2)',
      color: isKospi ? '#93c5fd' : '#6ee7b7',
      border: `1px solid ${isKospi ? 'rgba(59, 130, 246, 0.3)' : 'rgba(16, 185, 129, 0.3)'}`
    };
  };

  const diffColor = (diff) => diff > 0 ? '#ff4d4f' : diff < 0 ? '#60a5fa' : 'rgba(255,255,255,0.5)';

  const getSortIndicator = (key) => {
    if (sortConfig.key === key) {
      return sortConfig.direction === 'desc' ? ' ↓' : ' ↑';
    }
    return '';
  };

  return (
    <div className="fade-in" style={containerStyle}>
      <div style={{ padding: '1.5rem', borderBottom: '1px solid rgba(255, 255, 255, 0.08)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>🔥 기업별 순증가 랭킹 (국민연금)</h2>
        {date && <span style={{ color: '#60a5fa', fontWeight: 500, fontSize: '0.85rem' }}>업데이트: {date}</span>}
      </div>
      {loading ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>데이터 로딩 중...</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={{...thStyle, width: '50px'}}>순위</th>
                <th style={{...thStyle, textAlign: 'left', borderRight: '1px solid rgba(59,130,246,0.3)'}}>종목명</th>
                <th style={{...thStyle, textAlign: 'left'}}>섹터</th>
                <th style={{...thStyle, textAlign: 'right'}} onClick={() => handleSort('diff_0m')}>
                  이번달 순증가{getSortIndicator('diff_0m')}
                </th>
                <th style={{...thStyle, textAlign: 'right'}} onClick={() => handleSort('diff_1m')}>
                  1개월 전 순증가{getSortIndicator('diff_1m')}
                </th>
                <th style={{...thStyle, textAlign: 'right'}} onClick={() => handleSort('diff_3m')}>
                  3개월 전 순증가{getSortIndicator('diff_3m')}
                </th>
                <th style={{...thStyle, textAlign: 'right'}} onClick={() => handleSort('diff_6m')}>
                  6개월 전 순증가{getSortIndicator('diff_6m')}
                </th>
                <th style={{...thStyle, textAlign: 'right'}} onClick={() => handleSort('diff_1y')}>
                  1년 전 순증가{getSortIndicator('diff_1y')}
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row, i) => {
                return (
                  <tr 
                    key={row.stock_code} 
                    style={{ transition: 'background 0.2s' }}
                    onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                    onMouseOut={e => e.currentTarget.style.background='transparent'}
                  >
                    <td style={{...tdStyle, textAlign: 'center', color: 'rgba(255,255,255,0.5)'}}>{i + 1}</td>
                    <td style={{...tdStyle, textAlign: 'left', fontWeight: 600, borderRight: '1px solid rgba(59,130,246,0.2)'}}>
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        {row.market && <span style={badgeStyle(row.market)}>{row.market}</span>}
                        {row.stock_name}
                      </div>
                    </td>
                    <td style={{...tdStyle, textAlign: 'left', color: 'rgba(255,255,255,0.6)', fontSize: '0.8rem'}}>
                      {row.sector || '-'}
                    </td>
                    <td style={{...tdStyle, textAlign: 'right', color: diffColor(row.diff_0m), fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem'}}>
                      {row.diff_0m > 0 ? '+' : ''}{row.diff_0m != null ? row.diff_0m.toLocaleString() : '-'}
                    </td>
                    <td style={{...tdStyle, textAlign: 'right', color: diffColor(row.diff_1m), fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem'}}>
                      {row.diff_1m > 0 ? '+' : ''}{row.diff_1m != null ? row.diff_1m.toLocaleString() : '-'}
                    </td>
                    <td style={{...tdStyle, textAlign: 'right', color: diffColor(row.diff_3m), fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem'}}>
                      {row.diff_3m > 0 ? '+' : ''}{row.diff_3m != null ? row.diff_3m.toLocaleString() : '-'}
                    </td>
                    <td style={{...tdStyle, textAlign: 'right', color: diffColor(row.diff_6m), fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem'}}>
                      {row.diff_6m > 0 ? '+' : ''}{row.diff_6m != null ? row.diff_6m.toLocaleString() : '-'}
                    </td>
                    <td style={{...tdStyle, textAlign: 'right', color: diffColor(row.diff_1y), fontWeight: 600, fontFamily: 'monospace', fontSize: '0.9rem'}}>
                      {row.diff_1y > 0 ? '+' : ''}{row.diff_1y != null ? row.diff_1y.toLocaleString() : '-'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default NpsTrendView;
