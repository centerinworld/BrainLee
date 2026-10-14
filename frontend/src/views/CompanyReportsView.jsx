/**
 * CompanyReportsView — AGI-06 기업 보고서 공개 화면 (2026-10-14 신규)
 * 종목별로 재무·사업구성·최근공시·마진 괴리·업종 고유 섹션을 한 화면에 모아 보여준다.
 * 숫자은 기존 운영 테이블을 그대로 조립한 것이라 다른 화면과 동일하게 공개한다.
 *
 * API: GET /api/agi/company-reports            (목록)
 *      GET /api/agi/company-reports/{code}      (1건, 섹션별 최신 revision)
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { API } from '../utils';

const TEMPLATE_LABELS = {
  semiconductor: '반도체',
  financial_holding: '금융지주',
  medtech: '의료기기',
  order_backlog_industry: '수주산업',
  dividend_utility: '배당·유틸리티',
  holding_sotp: '지주(SOTP)',
  materials_value_chain: '소재·밸류체인',
  generic: '일반',
};

const CHANGE_LABELS = {
  new: '신규',
  updated: '갱신',
  unchanged: '변경 없음',
};

const card = (style = {}) => ({
  background: 'var(--surface)',
  border: '1px solid var(--line)',
  borderRadius: 'var(--radius)',
  boxShadow: 'var(--shadow-sm)',
  padding: '1rem',
  ...style,
});

const fmtEok = (v) => (v === null || v === undefined) ? '-' : `${Number(v).toLocaleString()}억`;
const fmtNum = (v, digits = 2) => (v === null || v === undefined) ? '-' : Number(v).toFixed(digits);
const fmtPct = (v) => (v === null || v === undefined) ? '-' : `${v > 0 ? '+' : ''}${v}%`;
const fmtPrice = (v) => (v === null || v === undefined) ? '-' : `${Number(v).toLocaleString()}원`;

function SectionCard({ title, children }) {
  return (
    <div style={card({ marginBottom: '0.75rem' })}>
      <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '0.5rem' }}>{title}</div>
      {children}
    </div>
  );
}

function HeaderSection({ content }) {
  if (!content) return null;
  const rows = [
    ['업종', [content.sector_large, content.sector_mid].filter(Boolean).join(' / ') || '-'],
    ['시가총액', fmtEok(content.market_cap_억)],
    ['PER', fmtNum(content.per)],
    ['PBR', fmtNum(content.pbr)],
    ['ROE', content.roe !== null && content.roe !== undefined ? `${fmtNum(content.roe, 1)}%` : '-'],
    ['목표가', fmtPrice(content.target_price)],
    ['의견', content.opinion || '-'],
  ];
  return (
    <SectionCard title="개요">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '0.5rem' }}>
        {rows.map(([label, value]) => (
          <div key={label}>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{label}</div>
            <div style={{ fontSize: '14px', color: 'var(--text)' }}>{value}</div>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function FinancialsSection({ content }) {
  if (!content || !content.quarters || content.quarters.length === 0) {
    return <SectionCard title="실적(최근 분기)"><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>데이터 없음</div></SectionCard>;
  }
  return (
    <SectionCard title={`실적(최근 분기, ${content.basis === 'OFS' ? '별도' : '연결'})`}>
      <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--line)' }}>
            {['분기', '매출', 'QoQ', '영업이익', '순이익'].map((h) => (
              <th key={h} style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {content.quarters.map((q) => (
            <tr key={`${q.year}-${q.quarter}`} style={{ borderBottom: '1px solid var(--line)' }}>
              <td style={{ padding: '4px 6px', color: 'var(--text)' }}>{q.year}Q{q.quarter}</td>
              <td style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--text)' }}>{fmtEok(q.revenue_억)}</td>
              <td style={{ textAlign: 'right', padding: '4px 6px', color: q.revenue_qoq_pct > 0 ? 'var(--success)' : q.revenue_qoq_pct < 0 ? 'var(--danger)' : 'var(--text-muted)' }}>
                {q.revenue_qoq_pct !== undefined ? fmtPct(q.revenue_qoq_pct) : '-'}
              </td>
              <td style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--text)' }}>{fmtEok(q.operating_profit_억)}</td>
              <td style={{ textAlign: 'right', padding: '4px 6px', color: 'var(--text)' }}>{fmtEok(q.net_income_억)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  );
}

function BusinessMixSection({ content }) {
  if (!content || !content.items || content.items.length === 0) {
    return <SectionCard title="사업 구성"><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>데이터 없음</div></SectionCard>;
  }
  return (
    <SectionCard title={`사업 구성(${content.year}년)${content.is_multi_segment ? ' — 복합 사업' : ''}`}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        {content.items.map((it, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '13px' }}>
            <div style={{ width: '120px', color: 'var(--text-muted)', flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{it.category}</div>
            <div style={{ flex: 1, background: 'var(--surface-2)', borderRadius: '4px', height: '8px', overflow: 'hidden' }}>
              <div style={{ width: `${Math.min(100, it.revenue_pct)}%`, background: 'var(--primary)', height: '100%' }} />
            </div>
            <div style={{ width: '48px', textAlign: 'right', color: 'var(--text)' }}>{fmtNum(it.revenue_pct, 1)}%</div>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function DisclosuresSection({ content }) {
  if (!content || !content.items || content.items.length === 0) {
    return <SectionCard title="최근 공시"><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>데이터 없음</div></SectionCard>;
  }
  return (
    <SectionCard title="최근 공시">
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
        {content.items.map((d, i) => (
          <div key={i} style={{ display: 'flex', gap: '0.75rem', fontSize: '13px' }}>
            <div style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{d.rcept_dt}</div>
            <div style={{ color: 'var(--text)' }}>{d.report_nm}</div>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function MarginGapSection({ content }) {
  if (!content || !content.flag) return null;
  return (
    <SectionCard title="⚠ 마진 괴리 감지">
      <div style={{ fontSize: '13px', color: 'var(--text)' }}>
        영업이익 QoQ {fmtPct(content.operating_profit_qoq_pct)} · 순이익 QoQ {fmtPct(content.net_income_qoq_pct)}
      </div>
      <div style={{ fontSize: '12px', color: 'var(--warn)', marginTop: '0.3rem' }}>{content.note}</div>
    </SectionCard>
  );
}

function SectorSpecificSection({ content, templateKind }) {
  if (!content) return null;
  if (templateKind === 'semiconductor' && content.peer_members) {
    return (
      <SectionCard title={content.peer_group || '업종 고유'}>
        {!content.is_member && content.note && (
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '0.4rem' }}>{content.note}</div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
          {content.peer_members.map((p) => (
            <span key={p.stock_code} style={{
              fontSize: '12px', padding: '2px 8px', borderRadius: '999px',
              background: p.stock_code === content._self_code ? 'var(--primary-soft)' : 'var(--surface-2)',
              color: 'var(--text)', border: '1px solid var(--line)',
            }}>{p.stock_name}{p.status === 'inferred' ? ' (추정)' : ''}</span>
          ))}
        </div>
      </SectionCard>
    );
  }
  if (content.note) {
    return (
      <SectionCard title="업종 고유">
        <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{content.note}</div>
      </SectionCard>
    );
  }
  return (
    <SectionCard title="업종 고유">
      <pre style={{ fontSize: '12px', color: 'var(--text)', whiteSpace: 'pre-wrap', margin: 0 }}>
        {JSON.stringify(content, null, 2)}
      </pre>
    </SectionCard>
  );
}

export default function CompanyReportsView() {
  const [items, setItems] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState(null);
  const [query, setQuery] = useState('');
  const [selectedCode, setSelectedCode] = useState(null);
  const [report, setReport] = useState(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [reportError, setReportError] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(API('/api/agi/company-reports'));
        const d = await r.json();
        if (!alive) return;
        if (!r.ok) {
          setListError('목록을 불러오지 못했습니다.');
        } else {
          setItems(d.items || []);
          if ((d.items || []).length > 0) setSelectedCode(d.items[0].stock_code);
        }
      } catch (e) {
        if (alive) setListError('요청 실패: ' + e.message);
      }
      if (alive) setLoadingList(false);
    })();
    return () => { alive = false; };
  }, []);

  const loadReport = useCallback(async (code) => {
    setLoadingReport(true);
    setReportError(null);
    try {
      const r = await fetch(API(`/api/agi/company-reports/${code}`));
      const d = await r.json();
      if (!r.ok) {
        setReportError(d.detail?.reason === 'company_report_not_found' ? '보고서가 없습니다.' : '불러오지 못했습니다.');
        setReport(null);
      } else {
        setReport(d);
      }
    } catch (e) {
      setReportError('요청 실패: ' + e.message);
    }
    setLoadingReport(false);
  }, []);

  useEffect(() => {
    if (selectedCode) loadReport(selectedCode);
  }, [selectedCode, loadReport]);

  const filteredItems = useMemo(() => {
    const q = query.trim();
    if (!q) return items;
    return items.filter((it) => it.stock_code.includes(q) || (TEMPLATE_LABELS[it.template_kind] || it.template_kind || '').includes(q));
  }, [items, query]);

  const headerContent = report?.sections?.header?.content;
  const stockName = headerContent?.stock_name || selectedCode;

  return (
    <div style={{ display: 'flex', gap: '1rem', height: '100%', minHeight: '480px' }}>
      <div style={{ width: '260px', flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
        <input
          type="text"
          placeholder="종목코드·업종 검색"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)', border: '1px solid var(--line)',
            background: 'var(--surface)', color: 'var(--text)', fontSize: '13px', marginBottom: '0.5rem',
          }}
        />
        {loadingList && <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>불러오는 중...</div>}
        {listError && <div style={{ color: 'var(--danger)', fontSize: '13px' }}>{listError}</div>}
        <div style={{ overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
          {filteredItems.map((it) => (
            <button
              key={it.stock_code}
              onClick={() => setSelectedCode(it.stock_code)}
              style={{
                textAlign: 'left', padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)',
                border: it.stock_code === selectedCode ? '1px solid var(--primary)' : '1px solid var(--line)',
                background: it.stock_code === selectedCode ? 'var(--primary-soft)' : 'var(--surface)',
                cursor: 'pointer', fontSize: '13px', color: 'var(--text)',
              }}
            >
              <div style={{ fontWeight: 600 }}>{it.stock_code}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                {TEMPLATE_LABELS[it.template_kind] || it.template_kind}
              </div>
            </button>
          ))}
          {!loadingList && filteredItems.length === 0 && (
            <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>일치하는 종목이 없습니다.</div>
          )}
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {!selectedCode && <div style={{ color: 'var(--text-muted)' }}>왼쪽에서 종목을 선택하세요.</div>}
        {loadingReport && <div style={{ color: 'var(--text-muted)' }}>불러오는 중...</div>}
        {reportError && <div style={{ color: 'var(--danger)' }}>{reportError}</div>}
        {report && !loadingReport && (
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', marginBottom: '0.75rem' }}>
              <div style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text)' }}>{stockName}</div>
              <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{report.stock_code}</div>
              <div style={{
                fontSize: '11px', padding: '1px 8px', borderRadius: '999px',
                background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--line)',
              }}>{TEMPLATE_LABELS[report.template_kind] || report.template_kind}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>갱신 {report.updated_at}</div>
            </div>
            <HeaderSection content={headerContent} />
            <FinancialsSection content={report.sections?.financials?.content} />
            <MarginGapSection content={report.sections?.margin_gap?.content} />
            <BusinessMixSection content={report.sections?.business_mix?.content} />
            <DisclosuresSection content={report.sections?.recent_disclosures?.content} />
            <SectorSpecificSection content={report.sections?.sector_specific?.content} templateKind={report.template_kind} />
          </div>
        )}
      </div>
    </div>
  );
}
