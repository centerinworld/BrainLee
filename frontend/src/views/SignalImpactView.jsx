/**
 * SignalImpactView.jsx
 * 매매 시그널 유형별 주가 영향성 분석 리포트 (2015~2026 event study)
 * PDF 인쇄 지원
 */
import React, { useEffect, useState, useRef } from 'react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from 'recharts';

// ── 분석 결과 데이터 (2026-06-21 event study 결과) ─────────────────────────
const PROG_DATA = [
  { period: '1일', buy: 0.32, sell: 0.28, base: 0.11 },
  { period: '5일', buy: 1.69, sell: 1.58, base: 0.52 },
  { period: '10일', buy: 2.65, sell: 3.29, base: 0.97 },
  { period: '20일', buy: 5.47, sell: 6.16, base: 1.98 },
];

const SUPPLY_DATA = [
  { period: '1일',  inst: 0.16, frn: 0.11, both: 0.18, base: 0.16 },
  { period: '5일',  inst: 0.47, frn: 0.44, both: 0.50, base: 1.51 },
  { period: '10일', inst: 0.90, frn: 0.85, both: 0.95, base: 1.81 },
  { period: '20일', inst: 1.82, frn: 1.70, both: 2.05, base: 2.85 },
  { period: '60일', inst: 5.20, frn: 4.85, both: 5.83, base: 4.78 },
];

const TREND_DATA = [
  { period: '1일',  highs: 0.12,  vol3x: 0.52,  golden: -0.15, base: 0.14 },
  { period: '5일',  highs: 1.07,  vol3x: 1.14,  golden:  0.20, base: 0.71 },
  { period: '10일', highs: 2.40,  vol3x: 1.67,  golden:  0.98, base: 1.35 },
  { period: '20일', highs: 4.92,  vol3x: 3.48,  golden:  2.35, base: 2.71 },
  { period: '60일', highs: 13.30, vol3x: 10.51, golden:  7.75, base: 8.71 },
];

const EXCESS_ROWS = [
  { signal: '52주 신고가 돌파', n1: '-0.02', n5: '+0.36', n20: '+2.21', n60: '+4.59', wr: '57.0%', grade: 'A' },
  { signal: '거래량 3배 폭발', n1: '+0.38', n5: '+0.43', n20: '+0.77', n60: '+1.80', wr: '53.2%', grade: 'B' },
  { signal: '프로그램 대규모 순매수', n1: '+0.21', n5: '+1.17', n20: '+3.49', n60: 'N/A', wr: '65.0%', grade: 'A' },
  { signal: '기관+외국인 동반 순매수', n1: '+0.02', n5: '-1.01', n20: '-0.80', n60: '+1.05', wr: '47.1%', grade: 'C' },
  { signal: '기관 단독 순매수 상위5%', n1: '0.00', n5: '-1.04', n20: '-1.03', n60: '+0.42', wr: '46.8%', grade: 'C' },
  { signal: '골든크로스(MA5{'>'} MA20)', n1: '-0.29', n5: '-0.51', n20: '-0.36', n60: '-0.96', wr: '48.0%', grade: 'D' },
  { signal: '프로그램 대규모 순매도', n1: '+0.17', n5: '+1.06', n20: '+4.18', n60: 'N/A', wr: '66.0%', grade: 'B' },
];

const YEARLY_TRIPLE_WINNERS = {
  2026: [
    { code: '106520', name: '노블엠앤비', market: 'KOSDAQ', sector: 'IT', theme: '하드웨어', multiple: 19.57, volPeak: 27.9, amountPeak: 25.6, reason: '소형 하드웨어주에 단기 수급이 집중되며 거래량과 거래대금이 동시에 급증. 저점권 가격대에서 테마성 매수세가 붙은 케이스.' },
    { code: '270520', name: '앱튼', market: 'KOSDAQ', sector: '에너지', theme: '에너지', multiple: 19.50, volPeak: 11.4, amountPeak: 12.6, reason: '에너지 테마와 저유동성 종목 특성이 결합. 평균 대비 10배 이상 거래가 붙으면서 가격 탄력이 확대.' },
    { code: '027040', name: '서울전자통신', market: 'KOSDAQ', sector: 'IT', theme: '하드웨어', multiple: 18.79, volPeak: 32.3, amountPeak: 28.2, reason: '전자부품/하드웨어 기대감과 거래량 폭발이 동반. 상승 초기부터 시장 관심도가 빠르게 커진 패턴.' },
    { code: '115160', name: '휴맥스', market: 'KOSDAQ', sector: 'IT', theme: '하드웨어', multiple: 18.17, volPeak: 33.7, amountPeak: 32.0, reason: '하드웨어 업종 내 구조 변화 기대와 강한 회전율이 맞물림. 거래대금 피크가 평균 대비 32배까지 확대.' },
  ],
  2025: [
    { code: '030530', name: '원익홀딩스', market: 'KOSDAQ', sector: 'IT', theme: '반도체', multiple: 19.46, volPeak: 8.5, amountPeak: 13.6, reason: '반도체 사이클 회복 기대와 지주/장비 밸류에이션 재평가가 동반. 거래대금 증가가 가격 재평가를 확인.' },
    { code: '458870', name: '씨어스', market: 'KOSDAQ', sector: '의료', theme: '의료장비/서비스', multiple: 18.41, volPeak: 7.8, amountPeak: 17.9, reason: '의료기기 성장 기대와 신규 상장주 성격의 변동성이 결합. 거래대금 피크가 평균 대비 크게 확대.' },
    { code: '347850', name: '디앤디파마텍', market: 'KOSDAQ', sector: '의료', theme: '제약/바이오', multiple: 16.79, volPeak: 9.4, amountPeak: 9.9, reason: '바이오 파이프라인 기대감과 섹터 순환매가 반영. 이벤트성 모멘텀에 따라 주가 탄력이 커진 사례.' },
    { code: '108490', name: '로보티즈', market: 'KOSDAQ', sector: '산업재', theme: '로봇/자본재', multiple: 13.15, volPeak: 6.3, amountPeak: 7.5, reason: '로봇 산업 성장 기대와 정책/투자 테마가 결합. 거래대금 증가와 함께 고멀티플 리레이팅이 나타남.' },
  ],
  2024: [
    { code: '323280', name: '태성', market: 'KOSDAQ', sector: 'IT', theme: '하드웨어', multiple: 14.03, volPeak: 15.1, amountPeak: 10.1, reason: 'AI/반도체 공급망 기대가 하드웨어 종목으로 확산. 거래량 피크가 평균 대비 15배까지 증가.' },
    { code: '452430', name: '사피엔반도체', market: 'KOSDAQ', sector: 'IT', theme: '반도체', multiple: 12.87, volPeak: 16.8, amountPeak: 24.3, reason: '반도체 설계/IP 기대와 신규 성장주 프리미엄이 맞물림. 거래대금 피크가 강하게 확인된 케이스.' },
    { code: '084180', name: '수성웹툰', market: 'KOSDAQ', sector: '경기소비재', theme: '미디어', multiple: 17.43, volPeak: 50.8, amountPeak: 46.2, reason: '웹툰·콘텐츠 테마 수급이 집중. 평균 대비 50배 수준의 거래량 급증이 가격 상승을 견인.' },
    { code: '032800', name: '판타지오', market: 'KOSDAQ', sector: '경기소비재', theme: '미디어', multiple: 16.33, volPeak: 28.6, amountPeak: 21.1, reason: '엔터/콘텐츠 재평가와 저가주 수급이 결합. 거래량 급증으로 단기 모멘텀이 강화.' },
  ],
  2023: [
    { code: '086520', name: '에코프로', market: 'KOSDAQ', sector: 'IT', theme: '2차전지/하드웨어', multiple: 14.57, volPeak: 3.9, amountPeak: 5.4, reason: '2차전지 소재 밸류체인 대표주로 기관·개인 관심이 집중. 실적 성장 기대와 섹터 주도주 프리미엄이 상승을 설명.' },
    { code: '022100', name: '포스코DX', market: 'KOSPI', sector: '미분류', theme: '스마트팩토리/그룹 IT', multiple: 13.68, volPeak: 8.8, amountPeak: 6.6, reason: '그룹사 자동화·스마트팩토리 기대와 2차전지 밸류체인 수혜 인식이 결합. 대형 테마 안에서 재평가.' },
    { code: '322510', name: '제이엘케이', market: 'KOSDAQ', sector: '의료', theme: 'AI 의료장비', multiple: 12.52, volPeak: 9.9, amountPeak: 12.3, reason: 'AI 의료 진단 테마와 의료기기 성장 기대가 동반. 거래대금 증가가 투자자 관심 확대를 확인.' },
    { code: '096610', name: '알에프세미', market: 'KOSDAQ', sector: 'IT', theme: '반도체', multiple: 13.60, volPeak: 9.3, amountPeak: 12.3, reason: '반도체/전력 관련 기대감과 개별 이슈성 수급이 결합. 높은 변동성 속에서 단기 재평가가 발생.' },
  ],
  2022: [
    { code: '241820', name: '피씨엘', market: 'KOSDAQ', sector: '의료', theme: '진단/의료장비', multiple: 17.86, volPeak: 14.8, amountPeak: 23.5, reason: '진단키트/의료기기 모멘텀과 이벤트성 기대가 반영. 거래대금 피크가 평균 대비 23배 이상 확대.' },
    { code: '079970', name: '투비소프트', market: 'KOSDAQ', sector: 'IT', theme: '소프트웨어', multiple: 18.49, volPeak: 50.3, amountPeak: 21.2, reason: '소프트웨어 저가주에 테마성 수급이 유입. 거래량 피크가 평균 대비 50배까지 커진 고변동성 사례.' },
    { code: '900260', name: '로스웰', market: 'KOSDAQ', sector: 'IT', theme: '하드웨어', multiple: 18.72, volPeak: 18.6, amountPeak: 18.4, reason: '중국계 상장주와 전장/하드웨어 테마 수급이 결합. 낮은 가격대에서 회전율이 크게 상승.' },
  ],
  2021: [
    { code: '112040', name: '위메이드', market: 'KOSDAQ', sector: 'IT', theme: '게임/블록체인', multiple: 13.86, volPeak: 8.5, amountPeak: 7.2, reason: '게임 흥행과 블록체인/P2E 기대가 동시에 반영. 실적 기대와 테마 리레이팅이 대표적으로 결합.' },
    { code: '194480', name: '데브시스터즈', market: 'KOSDAQ', sector: 'IT', theme: '게임', multiple: 14.46, volPeak: 8.6, amountPeak: 11.7, reason: '신작 흥행으로 실적 추정치가 급격히 상향. 게임 섹터 내 실적 이벤트가 주가를 크게 밀어 올린 사례.' },
    { code: '256840', name: '한국비엔씨', market: 'KOSDAQ', sector: '의료', theme: '의료장비/바이오', multiple: 16.83, volPeak: 9.9, amountPeak: 9.3, reason: '바이오 신약/치료제 기대감과 팬데믹 관련 테마가 결합. 이벤트성 재료에 강한 수급이 붙음.' },
    { code: '053290', name: 'NE능률', market: 'KOSDAQ', sector: '중견기업부', theme: '교육/출판', multiple: 11.20, volPeak: 14.2, amountPeak: 11.0, reason: '교육주에 정치·정책 테마 수급이 집중. 평균 대비 거래량 급증이 상승의 핵심 단서.' },
  ],
  2020: [
    { code: '019170', name: '신풍제약', market: 'KOSPI', sector: '의료', theme: '제약/바이오', multiple: 34.02, volPeak: 7.5, amountPeak: 7.7, reason: '팬데믹 치료제 기대감이 제약주 전반으로 확산. 개별 임상/재료 기대와 강한 개인 수급이 결합.' },
    { code: '950130', name: '엑세스바이오', market: 'KOSDAQ', sector: '의료', theme: '진단키트', multiple: 32.26, volPeak: 10.3, amountPeak: 14.3, reason: '코로나 진단키트 수요 급증과 실적 기대가 직접 반영. 의료장비 섹터 내 실적 모멘텀형 급등 사례.' },
    { code: '205470', name: '휴마시스', market: 'KOSDAQ', sector: '의료', theme: '진단/의료장비', multiple: 16.11, volPeak: 10.1, amountPeak: 11.9, reason: '진단키트 수요와 수출 기대가 주가 재평가를 견인. 거래대금 확대가 실적 기대 확산을 뒷받침.' },
    { code: '080580', name: '오킨스전자', market: 'KOSDAQ', sector: 'IT', theme: '반도체', multiple: 15.92, volPeak: 9.6, amountPeak: 10.6, reason: '반도체 부품/테스트 관련 기대감과 소형주 수급이 결합. 섹터 순환매 속에서 상승 탄력이 커짐.' },
  ],
};

const YEARLY_TRIPLE_SUMMARY = Object.entries(YEARLY_TRIPLE_WINNERS)
  .sort(([a], [b]) => Number(b) - Number(a))
  .map(([year, rows]) => ({
    year,
    count: rows.length,
    topMultiple: Math.max(...rows.map((r) => r.multiple)),
    sectors: [...new Set(rows.map((r) => r.sector))].slice(0, 3).join(' · '),
  }));

const GRADE_COLOR = { A: '#4ade80', B: '#34d399', C: '#fbbf24', D: '#f87171' };
const GRADE_BG   = { A: 'rgba(74,222,128,0.12)', B: 'rgba(52,211,153,0.10)', C: 'rgba(251,191,36,0.10)', D: 'rgba(248,113,113,0.10)' };

const pctFmt = (v) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;

const TOOLTIP_STYLE = {
  contentStyle: { background: '#1e293b', border: '1px solid rgba(255,255,255,0.12)', borderRadius: '8px', fontSize: '0.8rem' },
  labelStyle: { color: '#94a3b8' },
};

const verdictStyle = (verdict) => ({
  color: verdict === 'rejected' ? '#f87171' : '#4ade80',
  background: verdict === 'rejected' ? 'rgba(248,113,113,0.10)' : 'rgba(74,222,128,0.10)',
  border: `1px solid ${verdict === 'rejected' ? 'rgba(248,113,113,0.35)' : 'rgba(74,222,128,0.35)'}`,
  borderRadius: '6px', padding: '0.22rem 0.6rem', fontSize: '0.72rem', fontWeight: 800,
});

function DeepDrawdownReport({ report, loading, error }) {
  if (loading) return <div style={{ padding: '3rem', textAlign: 'center', color: '#94a3b8' }}>연구 결과를 불러오는 중...</div>;
  if (error || !report) return <div style={{ padding: '2rem', color: '#f87171' }}>{error || '연구 결과가 없습니다.'}</div>;
  const s = report.summary || {};
  const first = s.first_event_only || {};
  const cards = [
    ['분석 사건', Number(s.all_events || 0).toLocaleString('ko-KR') + '건', `${Number(s.all_stocks || 0).toLocaleString('ko-KR')}종목`, '#38bdf8'],
    ['252일 중앙수익', `${Number(s.median_returns_from_trigger_pct?.['252'] || 0).toFixed(2)}%`, '낙폭 조건 즉시 진입', '#f87171'],
    ['252일 플러스', `${Number(s.positive_252d_rate_pct || 0).toFixed(2)}%`, `${Number(s.observed_252d_events || 0).toLocaleString('ko-KR')}건 관찰`, '#fbbf24'],
    ['종목당 첫 사건', `${Number(first.median_252d_return_pct || 0).toFixed(2)}%`, `플러스 ${Number(first.positive_252d_rate_pct || 0).toFixed(1)}%`, '#f87171'],
    ['추가 하락 중앙값', `${Number(s.median_additional_loss_after_trigger_pct || 0).toFixed(2)}%`, '진입 이후 저점까지', '#fb7185'],
    ['30% 기술적 반등', `${Number(s.recovery_rate_pct || 0).toFixed(2)}%`, '투자 승률과 다름', '#a78bfa'],
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <section style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.8rem', flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontSize: '1rem', fontWeight: 800, color: '#f1f5f9' }}>{report.title}</div>
            <div style={{ fontSize: '0.76rem', color: '#94a3b8', lineHeight: 1.6, marginTop: '0.35rem', maxWidth: '920px' }}><strong style={{ color: '#cbd5e1' }}>가설:</strong> {report.hypothesis}</div>
          </div>
          <span style={verdictStyle(report.verdict)}>{report.verdict_label}</span>
        </div>
        <div style={{ marginTop: '0.8rem', padding: '0.7rem 0.85rem', background: 'rgba(248,113,113,0.07)', borderLeft: '3px solid #f87171', borderRadius: '6px', color: '#cbd5e1', fontSize: '0.8rem', lineHeight: 1.6 }}>
          낙폭만으로 매수하는 전략은 유효하지 않았습니다. 낙폭이 깊을수록 252일 수익과 플러스 비율이 악화됐고, 저점 대비 30% 반등을 확인한 뒤에도 장기 중앙수익은 음수였습니다.
        </div>
      </section>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(155px, 1fr))', gap: '0.6rem' }}>
        {cards.map(([label, value, sub, color]) => <div key={label} style={{ background: 'rgba(255,255,255,0.035)', border: `1px solid ${color}30`, borderRadius: '8px', padding: '0.75rem' }}>
          <div style={{ fontSize: '0.68rem', color: '#64748b' }}>{label}</div><div style={{ fontSize: '1.18rem', fontWeight: 900, color, marginTop: '0.2rem' }}>{value}</div><div style={{ fontSize: '0.68rem', color: '#94a3b8', marginTop: '0.15rem' }}>{sub}</div>
        </div>)}
      </div>

      <section style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '1rem', overflowX: 'auto' }}>
        <div style={{ fontSize: '0.9rem', fontWeight: 800, color: '#f1f5f9', marginBottom: '0.25rem' }}>낙폭 구간별 실제 결과</div>
        <div style={{ fontSize: '0.72rem', color: '#64748b', marginBottom: '0.7rem' }}>반등률보다 진입 후 252거래일 수익과 플러스 비율을 우선해서 봅니다.</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '760px', fontSize: '0.76rem' }}><thead><tr style={{ color: '#94a3b8', borderBottom: '1px solid rgba(255,255,255,0.12)' }}>
          {['저점 낙폭','사건','30% 반등률','추가하락','진입 252일','252일 플러스','확인 후 252일'].map((h,i)=><th key={h} style={{ padding: '0.48rem', textAlign: i===0?'left':'right' }}>{h}</th>)}
        </tr></thead><tbody>{(s.by_trough_drawdown || []).map((r,i)=><tr key={r.bin} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', background: i%2?'rgba(255,255,255,0.015)':'transparent' }}>
          <td style={{ padding: '0.48rem', fontWeight: 700, color: '#e2e8f0' }}>{r.bin}%</td><td style={{ padding: '0.48rem', textAlign: 'right' }}>{r.events}</td><td style={{ padding: '0.48rem', textAlign: 'right', color: '#a78bfa' }}>{r.recovery_rate_pct}%</td><td style={{ padding: '0.48rem', textAlign: 'right', color: '#f87171' }}>{r.median_additional_loss_pct}%</td><td style={{ padding: '0.48rem', textAlign: 'right', color: r.median_252d_return_from_trigger_pct>=0?'#4ade80':'#f87171' }}>{r.median_252d_return_from_trigger_pct}%</td><td style={{ padding: '0.48rem', textAlign: 'right' }}>{r.positive_252d_rate_pct}%</td><td style={{ padding: '0.48rem', textAlign: 'right', color: r.median_252d_return_after_confirmation_pct>=0?'#4ade80':'#f87171' }}>{r.median_252d_return_after_confirmation_pct}%</td>
        </tr>)}</tbody></table>
      </section>

      <section style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '1rem', overflowX: 'auto' }}>
        <div style={{ fontSize: '0.9rem', fontWeight: 800, color: '#f1f5f9', marginBottom: '0.7rem' }}>반등 확인 시 동반 신호</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '520px', fontSize: '0.76rem' }}><thead><tr style={{ color: '#94a3b8', borderBottom: '1px solid rgba(255,255,255,0.12)' }}><th style={{ padding: '0.45rem', textAlign: 'left' }}>신호</th><th style={{ padding: '0.45rem', textAlign: 'right' }}>관찰</th><th style={{ padding: '0.45rem', textAlign: 'right' }}>252일 중앙수익</th><th style={{ padding: '0.45rem', textAlign: 'right' }}>플러스</th></tr></thead>
          <tbody>{(s.cause_outcomes_after_confirmation || []).map(r=><tr key={r.cause} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}><td style={{ padding: '0.45rem', color: '#e2e8f0' }}>{r.label}</td><td style={{ padding: '0.45rem', textAlign: 'right' }}>{r.events}</td><td style={{ padding: '0.45rem', textAlign: 'right', color: r.median_252d_return_after_confirmation_pct>=0?'#4ade80':'#f87171' }}>{r.median_252d_return_after_confirmation_pct}%</td><td style={{ padding: '0.45rem', textAlign: 'right' }}>{r.positive_252d_rate_pct}%</td></tr>)}</tbody>
        </table>
      </section>

      <section style={{ background: 'rgba(45,212,191,0.05)', border: '1px solid rgba(45,212,191,0.18)', borderRadius: '8px', padding: '1rem' }}>
        <div style={{ fontSize: '0.9rem', fontWeight: 800, color: '#2dd4bf', marginBottom: '0.55rem' }}>실전 판정</div>
        {['낙폭만으로 매수하지 않는다.','-60% 이하 급락은 기회가 아니라 위험 경고로 우선 해석한다.','사업구조 변화와 이익 추정치 상향이 실제 공시·실적으로 확인된 종목만 별도 후보로 분류한다.','증자·CB·감사의견·거래정지 위험을 선제적으로 제외한다.'].map((t,i)=><div key={t} style={{ display: 'flex', gap: '0.5rem', color: '#cbd5e1', fontSize: '0.78rem', lineHeight: 1.6 }}><span style={{ color: '#2dd4bf', fontWeight: 800 }}>{i+1}.</span><span>{t}</span></div>)}
        <div style={{ fontSize: '0.66rem', color: '#64748b', marginTop: '0.65rem' }}>업데이트: {report.updated_at?.replace('T',' ')} · {report.methodology?.event_period}</div>
      </section>
    </div>
  );
}

export default function SignalImpactView() {
  const [progTab, setProgTab] = useState('chart');
  const [winnerYear, setWinnerYear] = useState('2026');
  const [triggerTab, setTriggerTab] = useState(1);
  const [winnerData, setWinnerData] = useState(null);
  const [winnerLoading, setWinnerLoading] = useState(false);
  const [winnerError, setWinnerError] = useState('');
  const [triggerAnalysis, setTriggerAnalysis] = useState(null);
  const [triggerAnalysisError, setTriggerAnalysisError] = useState('');
  const [researchTab, setResearchTab] = useState('signal_event_study');
  const [hypothesisReports, setHypothesisReports] = useState([]);
  const [hypothesisLoading, setHypothesisLoading] = useState(true);
  const [hypothesisError, setHypothesisError] = useState('');
  const reportRef = useRef(null);

  useEffect(() => {
    let alive = true;
    fetch('/api/dashboard/hypothesis-reports')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => { if (alive) setHypothesisReports(Array.isArray(d?.reports) ? d.reports : []); })
      .catch(e => { if (alive) setHypothesisError(e?.message || '가설 보고서 조회 실패'); })
      .finally(() => { if (alive) setHypothesisLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    setWinnerLoading(true);
    setWinnerError('');
    fetch('/api/tenbagger/triple-winners-by-year?start_year=2020&end_year=2026&limit_per_year=80&min_multiple=3&max_multiple=50&min_price=100')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => {
        if (!alive) return;
        setWinnerData(data);
        const years = Object.keys(data?.years || {}).sort((a, b) => Number(b) - Number(a));
        if (years.length && !data.years[winnerYear]) setWinnerYear(years[0]);
      })
      .catch((err) => {
        if (!alive) return;
        setWinnerError(err?.message || '3배주 API 조회 실패');
      })
      .finally(() => {
        if (alive) setWinnerLoading(false);
      });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    setTriggerAnalysisError('');
    fetch('/api/tenbagger/triple-trigger-analysis')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => { if (alive) setTriggerAnalysis(data); })
      .catch((err) => { if (alive) setTriggerAnalysisError(err?.message || '트리거 통계 API 조회 실패'); });
    return () => { alive = false; };
  }, []);

  const yearlyWinners = winnerData?.years && Object.keys(winnerData.years).length
    ? winnerData.years
    : YEARLY_TRIPLE_WINNERS;
  const winnerYears = Object.keys(yearlyWinners).sort((a, b) => Number(b) - Number(a));
  const selectedWinnerRows = yearlyWinners[winnerYear] || [];
  const winnerSummary = winnerData?.summary?.length
    ? winnerData.summary
    : YEARLY_TRIPLE_SUMMARY.map((s) => ({
      year: s.year,
      count: s.count,
      shown: s.count,
      top_multiple: s.topMultiple,
      sectors: s.sectors.split(' · '),
    }));
  const patternStats = winnerData?.pattern_stats || null;
  const triggerScope = triggerAnalysis?.scope || null;
  const triggerRows = triggerAnalysis?.trigger_stats || [];
  const comboRows = triggerAnalysis?.combo_stats || [];
  const failureRows = triggerAnalysis?.failure_stats || [];
  const fmtStat = (v, suffix = '') => (v == null ? '-' : `${Number(v).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}${suffix}`);
  const getVolPeak = (stock) => Number(stock.volPeak ?? stock.vol_peak_x ?? 0);
  const getAmountPeak = (stock) => Number(stock.amountPeak ?? stock.amount_peak_x ?? 0);
  const triggerByRank = new Map((patternStats?.triggers || []).map((t) => [t.rank, t]));
  const triggerDetails = [
    {
      rank: 1,
      title: '거래대금 급증',
      subtitle: '돈이 실제로 들어온 흔적',
      metric: fmtStat(triggerByRank.get(1)?.hit_rate, '%'),
      criteria: '연중 거래대금 피크가 해당 종목의 평균 거래대금 대비 10배 이상',
      result: `정상 필터 통과 3배주 중 ${fmtStat(triggerByRank.get(1)?.hit_rate, '%')}가 이 조건을 만족했습니다. 거래대금 피크 중앙값은 ${fmtStat(patternStats?.metrics?.amount_peak_x?.median, '배')}이고, 상위 10%는 ${fmtStat(patternStats?.metrics?.amount_peak_x?.p90, '배')}까지 올라갑니다.`,
      interpretation: '3배주는 단순히 가격만 오른 종목이 아니라 시장 참여자의 자금 배분이 갑자기 바뀐 종목입니다. 거래량보다 거래대금이 더 중요합니다. 작은 종목에서 거래량만 늘어도 금액이 작으면 실제 매수 여력이 약할 수 있기 때문입니다.',
      use: ['평균 거래대금 대비 10배 이상을 1차 트리거로 둡니다.', '가격 상승률보다 거래대금 증가가 먼저 나타나는 종목을 우선 봅니다.', '거래대금 증가가 1일짜리로 끝나지 않고 3~5거래일 이어지는지 확인합니다.'],
      caution: '급등 후 고점에서 터진 거래대금은 매집이 아니라 분산일 수 있습니다. 첫 거래대금 폭발 이후 종가가 고가권을 유지하는지 함께 봐야 합니다.',
      color: '#2dd4bf',
    },
    {
      rank: 2,
      title: '거래량 급증',
      subtitle: '관심 종목으로 편입되는 순간',
      metric: fmtStat(triggerByRank.get(2)?.hit_rate, '%'),
      criteria: '연중 거래량 피크가 해당 종목의 평균 거래량 대비 10배 이상',
      result: `정상 필터 통과 3배주 중 ${fmtStat(triggerByRank.get(2)?.hit_rate, '%')}가 조건을 만족했습니다. 거래량 피크 중앙값은 ${fmtStat(patternStats?.metrics?.vol_peak_x?.median, '배')}, 상위 10%는 ${fmtStat(patternStats?.metrics?.vol_peak_x?.p90, '배')}입니다.`,
      interpretation: '거래량 폭발은 종목이 시장의 레이더에 들어왔다는 뜻입니다. 다만 7개년 데이터에서는 거래량보다 거래대금 조건이 약간 더 강했습니다. 즉 수량 증가보다 실제 자금 유입 규모가 더 좋은 트리거입니다.',
      use: ['거래량 10배 이상과 거래대금 10배 이상이 동시에 나타나면 신뢰도를 높입니다.', '거래량 급증 후 20일선 또는 전고점 부근에서 눌림이 짧으면 추세 지속 가능성이 커집니다.', '거래량 급증일의 윗꼬리와 종가 위치를 함께 봅니다.'],
      caution: '저가주에서는 소액 자금만으로도 거래량 배수가 크게 보일 수 있습니다. 그래서 100원 미만과 비정상 배율 종목은 제외했습니다.',
      color: '#4ade80',
    },
    {
      rank: 3,
      title: 'KOSDAQ 중소형주',
      subtitle: '작은 시총이 만드는 가격 탄력',
      metric: fmtStat(triggerByRank.get(3)?.hit_rate, '%'),
      criteria: 'KOSDAQ 상장 + 최신 stock_universe 기준 시가총액 3,000억 이하',
      result: `3배주 전체의 KOSDAQ 비중은 ${fmtStat(patternStats?.shares?.kosdaq?.pct, '%')}이고, KOSDAQ 중소형 조건은 ${fmtStat(triggerByRank.get(3)?.hit_rate, '%')}입니다. 시총 중앙값은 ${fmtStat(patternStats?.metrics?.market_cap?.median, '억')} 수준입니다.`,
      interpretation: '3배 이상 상승은 대형주보다 중소형주에서 훨씬 자주 발생합니다. 같은 300억 원의 자금 유입도 30조 기업보다 3,000억 기업의 가격을 훨씬 크게 움직입니다.',
      use: ['시총 3,000억 이하를 기본 후보군으로 두고, 1,000억 전후 종목은 변동성 관리 기준을 더 엄격히 둡니다.', 'KOSDAQ 종목 중 거래대금이 새로 붙기 시작한 종목을 우선 정렬합니다.', '시총이 작아도 거래대금이 너무 얇은 종목은 제외합니다.'],
      caution: '중소형주는 상승 탄력만큼 하락 탄력도 큽니다. 이 조건은 수익 가능성 조건이지 안전 조건이 아닙니다.',
      color: '#38bdf8',
    },
    {
      rank: 4,
      title: '성장/테마 섹터',
      subtitle: '7개년 반복 주도 섹터',
      metric: fmtStat(triggerByRank.get(4)?.hit_rate, '%'),
      criteria: 'IT·의료·경기소비재·산업재 중 하나',
      result: `핵심 4섹터 비중은 ${fmtStat(patternStats?.shares?.core_sectors?.pct, '%')}입니다. 상위 섹터는 IT ${fmtStat(patternStats?.sector?.find((s) => s.name === 'IT')?.pct, '%')}, 의료 ${fmtStat(patternStats?.sector?.find((s) => s.name === '의료')?.pct, '%')}, 경기소비재 ${fmtStat(patternStats?.sector?.find((s) => s.name === '경기소비재')?.pct, '%')} 순으로 반복됩니다.`,
      interpretation: '연도별 주도 테마는 코로나, 게임, 2차전지, AI, 반도체, 로봇처럼 계속 바뀌지만 큰 그릇은 비슷합니다. 시장은 늘 새로운 성장 내러티브가 붙는 섹터를 먼저 재평가했습니다.',
      use: ['섹터 지표에서 5일·20일 상대강도가 동시에 개선되는 섹터를 우선 봅니다.', '같은 섹터 안에서 거래대금이 새로 붙는 2~3등주를 찾습니다.', '테마명보다 실제 거래대금 확산 여부를 우선합니다.'],
      caution: '테마는 빠르게 소멸합니다. 섹터 전체가 오르지 않고 한 종목만 급등하면 개별 재료 또는 작전성 수급일 수 있습니다.',
      color: '#a78bfa',
    },
    {
      rank: 5,
      title: '고변동 반복 종목군',
      subtitle: '여러 해 다시 튀는 종목',
      metric: fmtStat(triggerByRank.get(5)?.hit_rate, '%'),
      criteria: '2020~2026 중 2개 연도 이상 3배 구간 재진입',
      result: `정상 필터 통과 표본 중 ${fmtStat(triggerByRank.get(5)?.hit_rate, '%')}가 반복 출현 종목군에 속합니다. 반복 종목은 사업 안정성보다 테마 민감도와 유동성 회전이 강한 경우가 많습니다.`,
      interpretation: '반복 출현은 “좋은 회사”라는 뜻보다 “시장이 다시 건드리기 쉬운 구조”라는 뜻에 가깝습니다. 낮은 시총, 익숙한 테마, 과거 급등 기억, 개인 수급 친화성이 결합합니다.',
      use: ['과거 3배 이력이 있는 종목은 감시 리스트에 넣되, 현재 거래대금 재점화가 있을 때만 후보로 봅니다.', '반복 종목은 급등 후 되돌림도 크므로 분할 진입/분할 청산 룰이 필요합니다.', '같은 테마가 재부상할 때 과거 대장주와 2등주를 같이 비교합니다.'],
      caution: '반복 급등주는 재무 개선이 아닌 수급 재활용일 수 있습니다. 공시, CB/BW, 감자/증자 이력 확인이 필수입니다.',
      color: '#fb7185',
    },
  ];
  const activeTrigger = triggerDetails.find((t) => t.rank === triggerTab) || triggerDetails[0];

  const handlePrint = () => {
    const el = reportRef.current;
    if (!el) return;
    const html = el.innerHTML;
    const win = window.open('', '_blank', 'width=900,height=700');
    win.document.write(`<!DOCTYPE html><html><head>
      <title>매매 시그널 영향성 분석 리포트 — 2026-06-21</title>
      <meta charset="utf-8"/>
      <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #fff; color: #111; margin: 2rem; }
        h1,h2,h3 { color: #1e293b; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; }
        th, td { border: 1px solid #e2e8f0; padding: 0.4rem 0.7rem; font-size: 0.8rem; }
        th { background: #f1f5f9; font-weight: 600; }
        .grade-a { color: #15803d; font-weight: 700; }
        .grade-b { color: #047857; font-weight: 700; }
        .grade-c { color: #b45309; font-weight: 700; }
        .grade-d { color: #b91c1c; font-weight: 700; }
        .pos { color: #15803d; }
        .neg { color: #b91c1c; }
        .section { margin-bottom: 1.5rem; border-top: 2px solid #e2e8f0; padding-top: 1rem; }
        @media print { body { margin: 0.5rem; } }
      </style>
    </head><body>${html}</body></html>`);
    win.document.close();
    win.focus();
    setTimeout(() => { win.print(); }, 500);
  };

  const sectionStyle = { background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px', padding: '1.2rem', display: 'flex', flexDirection: 'column', gap: '0.8rem' };
  const sectionTitle = { fontSize: '0.95rem', fontWeight: 700, color: '#f1f5f9', marginBottom: '0.25rem' };
  const sectionSub = { fontSize: '0.78rem', color: '#94a3b8', lineHeight: 1.6 };
  const tabBtn = (active) => ({
    padding: '0.3rem 0.85rem', borderRadius: '6px', cursor: 'pointer', fontSize: '0.78rem', fontWeight: active ? 700 : 400,
    background: active ? 'rgba(45,212,191,0.18)' : 'rgba(255,255,255,0.05)',
    border: active ? '1px solid rgba(45,212,191,0.35)' : '1px solid rgba(255,255,255,0.1)',
    color: active ? '#2dd4bf' : '#94a3b8',
  });
  const yearTabBtn = (active) => ({
    ...tabBtn(active),
    minWidth: '58px',
    textAlign: 'center',
    background: active ? 'rgba(251,191,36,0.16)' : 'rgba(255,255,255,0.05)',
    border: active ? '1px solid rgba(251,191,36,0.42)' : '1px solid rgba(255,255,255,0.1)',
    color: active ? '#fbbf24' : '#94a3b8',
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem' }}>

      {/* 상단 헤더 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div>
          <div style={{ fontSize: '1rem', fontWeight: 700, color: '#f1f5f9' }}>매매 시그널 영향성 분석</div>
          <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '0.15rem' }}>
            분석 기간: 2015~2026 · event study 방식 (시그널 발생 후 N영업일 평균 수익률)
          </div>
        </div>
        <button onClick={handlePrint}
          style={{ padding: '0.45rem 1.1rem', borderRadius: '8px', border: '1px solid rgba(99,102,241,0.4)',
            background: 'rgba(99,102,241,0.15)', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.82rem', fontWeight: 600 }}>
          📄 PDF 저장
        </button>
      </div>

      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', borderBottom: '1px solid rgba(255,255,255,0.10)', paddingBottom: '0.65rem' }}>
        <button style={tabBtn(researchTab === 'signal_event_study')} onClick={() => setResearchTab('signal_event_study')}>시그널 이벤트 연구</button>
        {hypothesisReports.map(report => (
          <button key={report.id} style={tabBtn(researchTab === report.id)} onClick={() => setResearchTab(report.id)}>
            {report.short_title || report.title}
            <span style={{ marginLeft: '0.35rem', color: report.verdict === 'rejected' ? '#f87171' : '#4ade80' }}>●</span>
          </button>
        ))}
        <span style={{ alignSelf: 'center', fontSize: '0.68rem', color: '#64748b', marginLeft: '0.25rem' }}>검증된 가설은 이곳에 계속 추가됩니다.</span>
      </div>

      {/* ── 인쇄 대상 영역 ─────────────────────────────────────── */}
      <div ref={reportRef}>

      {researchTab === 'signal_event_study' ? <>

        {/* 데이터 수집 현황 */}
        <div style={{ ...sectionStyle, marginBottom: '1.2rem' }}>
          <div style={sectionTitle}>데이터 수집 현황</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.6rem' }}>
            {[
              { name: '프로그램 매매 (시장)', status: '✅', detail: '2020~2026, 4,004행 (KOSPI/KOSDAQ)', color: '#4ade80' },
              { name: '프로그램 매매 (종목별)', status: '⚠️', detail: '2026-05-21~ 이후만 (52,801행)', color: '#fbbf24' },
              { name: '기관/외국인 수급', status: '✅', detail: '2020~2026, 738K행 (Kiwoom)', color: '#4ade80' },
              { name: '신용잔고', status: '✅', detail: '2019~2026, 3.5M행 (2224종목)', color: '#4ade80' },
              { name: 'OHLCV 일봉 (추세분석)', status: '✅', detail: '2010~2026, 5.89M행', color: '#4ade80' },
              { name: '재무 팩터', status: '✅', detail: '2015~2026, 191K행', color: '#4ade80' },
              { name: '외국인 지분율 히스토리', status: '⚠️', detail: '2026-03-12~ 이후만 (130K행)', color: '#fbbf24' },
              { name: '컨센서스', status: '⚠️', detail: '2024~2026만 (797종목)', color: '#fbbf24' },
              { name: '공매도/대차잔고', status: '✅', detail: '2020~2026, 70K행', color: '#4ade80' },
            ].map(({ name, status, detail, color }) => (
              <div key={name} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '8px', padding: '0.55rem 0.75rem' }}>
                <div style={{ fontSize: '0.78rem', fontWeight: 600, color }}>{status} {name}</div>
                <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '0.15rem' }}>{detail}</div>
              </div>
            ))}
          </div>
        </div>

        {/* 연도별 3배 이상 상승 종목 */}
        <div style={{ ...sectionStyle, marginBottom: '1.2rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.7rem' }}>
            <div>
              <div style={sectionTitle}>연도별 3배 이상 상승 종목</div>
              <div style={sectionSub}>
                내부 가격 DB 기준 연중 저가 대비 고가 3배+ 종목입니다. 보통주가 아닌 증권, 종목명 없는 코드, 100원 미만 저가/권리락 의심 데이터, 50배 초과 이상치는 제외합니다.
              </div>
            </div>
            <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              {winnerYears.map((year) => (
                <button key={year} style={yearTabBtn(winnerYear === year)} onClick={() => setWinnerYear(year)}>
                  {year}
                </button>
              ))}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.6rem' }}>
            {winnerSummary.map((s) => (
              <button key={s.year} onClick={() => setWinnerYear(s.year)}
                style={{ textAlign: 'left', cursor: 'pointer', background: winnerYear === s.year ? 'rgba(251,191,36,0.10)' : 'rgba(255,255,255,0.03)',
                  border: winnerYear === s.year ? '1px solid rgba(251,191,36,0.35)' : '1px solid rgba(255,255,255,0.08)',
                  borderRadius: '8px', padding: '0.7rem 0.8rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#f1f5f9' }}>{s.year}</span>
                  <span style={{ fontSize: '0.72rem', color: '#fbbf24', fontWeight: 700 }}>{s.count}종목</span>
                </div>
                <div style={{ fontSize: '1.05rem', fontWeight: 800, color: '#fbbf24', marginTop: '0.25rem' }}>{Number(s.top_multiple || 0).toFixed(1)}x</div>
                <div style={{ fontSize: '0.68rem', color: '#64748b', marginTop: '0.2rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {(Array.isArray(s.sectors) ? s.sectors : []).join(' · ')}
                </div>
                {s.shown != null && s.shown < s.count && (
                  <div style={{ fontSize: '0.64rem', color: '#475569', marginTop: '0.15rem' }}>상위 {s.shown}개 표시</div>
                )}
              </button>
            ))}
          </div>

          {(winnerLoading || winnerError || winnerData?.notice) && (
            <div style={{ ...sectionSub, background: winnerError ? 'rgba(248,113,113,0.07)' : 'rgba(251,191,36,0.06)', borderRadius: '8px', padding: '0.6rem 0.85rem', borderLeft: `3px solid ${winnerError ? 'rgba(248,113,113,0.45)' : 'rgba(251,191,36,0.35)'}` }}>
              {winnerLoading ? '연도별 3배주 전체 목록을 불러오는 중...' : winnerError ? `API 조회 실패로 샘플 데이터를 표시 중입니다: ${winnerError}` : winnerData.notice}
            </div>
          )}

          {patternStats && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '0.6rem' }}>
                {[
                  { label: '분석 표본', val: fmtStat(patternStats.total, '건'), sub: '7개년 정상 필터 통과', color: '#fbbf24' },
                  { label: 'KOSDAQ 비중', val: fmtStat(patternStats.shares?.kosdaq?.pct, '%'), sub: `${patternStats.shares?.kosdaq?.count?.toLocaleString('ko-KR') || '-'}건`, color: '#38bdf8' },
                  { label: '핵심 4섹터 비중', val: fmtStat(patternStats.shares?.core_sectors?.pct, '%'), sub: 'IT·의료·경기소비재·산업재', color: '#a78bfa' },
                  { label: '거래량 10배+', val: fmtStat(patternStats.shares?.volume_peak_10x?.pct, '%'), sub: '평균 대비 피크 기준', color: '#4ade80' },
                  { label: '거래대금 10배+', val: fmtStat(patternStats.shares?.amount_peak_10x?.pct, '%'), sub: '관심 유입 확인 신호', color: '#2dd4bf' },
                  { label: '시총 3천억 이하', val: fmtStat(patternStats.shares?.['small_cap_3000억']?.pct, '%'), sub: '최신 stock_universe 기준', color: '#fb7185' },
                ].map((card) => (
                  <div key={card.label} style={{ background: 'rgba(255,255,255,0.035)', border: `1px solid ${card.color}30`, borderRadius: '8px', padding: '0.75rem 0.85rem' }}>
                    <div style={{ fontSize: '0.68rem', color: '#64748b' }}>{card.label}</div>
                    <div style={{ fontSize: '1.25rem', fontWeight: 900, color: card.color, marginTop: '0.18rem' }}>{card.val}</div>
                    <div style={{ fontSize: '0.68rem', color: '#94a3b8', marginTop: '0.12rem' }}>{card.sub}</div>
                  </div>
                ))}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '0.75rem' }}>
                <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '8px', padding: '0.9rem' }}>
                  <div style={{ fontSize: '0.88rem', fontWeight: 800, color: '#f1f5f9', marginBottom: '0.65rem' }}>7개년 공통 트리거</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.55rem' }}>
                    {(patternStats.triggers || []).map((t) => (
                      <button key={t.rank} onClick={() => setTriggerTab(t.rank)} style={{ display: 'grid', gridTemplateColumns: '28px 1fr auto', gap: '0.6rem', alignItems: 'flex-start', padding: '0.6rem', background: triggerTab === t.rank ? 'rgba(251,191,36,0.10)' : 'rgba(15,23,42,0.45)', borderRadius: '8px', border: triggerTab === t.rank ? '1px solid rgba(251,191,36,0.35)' : '1px solid rgba(255,255,255,0.06)', cursor: 'pointer', textAlign: 'left' }}>
                        <div style={{ width: '24px', height: '24px', borderRadius: '6px', background: 'rgba(251,191,36,0.13)', color: '#fbbf24', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.75rem', fontWeight: 900 }}>{t.rank}</div>
                        <div>
                          <div style={{ fontSize: '0.8rem', color: '#e2e8f0', fontWeight: 800 }}>{t.name}</div>
                          <div style={{ fontSize: '0.72rem', color: '#93c5fd', marginTop: '0.12rem' }}>{t.signal}</div>
                          <div style={{ fontSize: '0.7rem', color: '#94a3b8', lineHeight: 1.45, marginTop: '0.22rem' }}>{t.why}</div>
                        </div>
                        <div style={{ fontSize: '0.82rem', color: '#4ade80', fontWeight: 900, whiteSpace: 'nowrap' }}>{fmtStat(t.hit_rate, '%')}</div>
                      </button>
                    ))}
                  </div>
                </div>

                <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '8px', padding: '0.9rem' }}>
                  <div style={{ fontSize: '0.88rem', fontWeight: 800, color: '#f1f5f9', marginBottom: '0.65rem' }}>분포 핵심값</div>
                  {[
                    ['상승배율 중앙값', patternStats.metrics?.multiple?.median, 'x', '평균 ' + fmtStat(patternStats.metrics?.multiple?.avg, 'x')],
                    ['거래량 피크 중앙값', patternStats.metrics?.vol_peak_x?.median, 'x', '상위10% ' + fmtStat(patternStats.metrics?.vol_peak_x?.p90, 'x')],
                    ['거래대금 피크 중앙값', patternStats.metrics?.amount_peak_x?.median, 'x', '상위10% ' + fmtStat(patternStats.metrics?.amount_peak_x?.p90, 'x')],
                    ['시총 중앙값', patternStats.metrics?.market_cap?.median, '억', '상위25% ' + fmtStat(patternStats.metrics?.market_cap?.p75, '억')],
                  ].map(([label, val, suffix, sub]) => (
                    <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: '0.7rem', padding: '0.48rem 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                      <div>
                        <div style={{ fontSize: '0.74rem', color: '#94a3b8' }}>{label}</div>
                        <div style={{ fontSize: '0.66rem', color: '#64748b', marginTop: '0.08rem' }}>{sub}</div>
                      </div>
                      <div style={{ fontSize: '1rem', color: '#fbbf24', fontWeight: 900 }}>{fmtStat(val, suffix)}</div>
                    </div>
                  ))}

                  <div style={{ fontSize: '0.78rem', fontWeight: 800, color: '#f1f5f9', margin: '0.8rem 0 0.45rem' }}>상위 섹터/테마</div>
                  {(patternStats.sector || []).slice(0, 5).map((s) => (
                    <div key={s.name} style={{ display: 'grid', gridTemplateColumns: '76px 1fr 44px', gap: '0.45rem', alignItems: 'center', marginBottom: '0.35rem' }}>
                      <span style={{ fontSize: '0.7rem', color: '#cbd5e1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                      <div style={{ height: '7px', background: 'rgba(255,255,255,0.08)', borderRadius: '99px', overflow: 'hidden' }}>
                        <div style={{ width: `${Math.min(100, s.pct * 2)}%`, height: '100%', background: 'linear-gradient(90deg, #2dd4bf, #60a5fa)' }} />
                      </div>
                      <span style={{ fontSize: '0.68rem', color: '#94a3b8', textAlign: 'right' }}>{s.pct}%</span>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ background: 'rgba(255,255,255,0.035)', border: `1px solid ${activeTrigger.color}33`, borderRadius: '8px', padding: '0.95rem', display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '0.8rem', flexWrap: 'wrap' }}>
                  <div>
                    <div style={{ fontSize: '0.72rem', color: activeTrigger.color, fontWeight: 800 }}>트리거 {activeTrigger.rank}</div>
                    <div style={{ fontSize: '1rem', color: '#f8fafc', fontWeight: 900, marginTop: '0.1rem' }}>{activeTrigger.title}</div>
                    <div style={{ fontSize: '0.74rem', color: '#94a3b8', marginTop: '0.14rem' }}>{activeTrigger.subtitle}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '1.35rem', color: activeTrigger.color, fontWeight: 900 }}>{activeTrigger.metric}</div>
                    <div style={{ fontSize: '0.66rem', color: '#64748b' }}>조건 충족률</div>
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                  {triggerDetails.map((t) => (
                    <button key={t.rank} onClick={() => setTriggerTab(t.rank)}
                      style={{ padding: '0.32rem 0.72rem', borderRadius: '6px', cursor: 'pointer', fontSize: '0.74rem', fontWeight: triggerTab === t.rank ? 800 : 500,
                        background: triggerTab === t.rank ? `${t.color}20` : 'rgba(255,255,255,0.04)',
                        border: triggerTab === t.rank ? `1px solid ${t.color}66` : '1px solid rgba(255,255,255,0.08)',
                        color: triggerTab === t.rank ? t.color : '#94a3b8' }}>
                      {t.rank}. {t.title}
                    </button>
                  ))}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '0.75rem' }}>
                  {[
                    ['판정 기준', activeTrigger.criteria],
                    ['분석 결과', activeTrigger.result],
                    ['의미 해석', activeTrigger.interpretation],
                    ['주의점', activeTrigger.caution],
                  ].map(([label, body]) => (
                    <div key={label} style={{ background: 'rgba(15,23,42,0.46)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: '8px', padding: '0.75rem' }}>
                      <div style={{ fontSize: '0.7rem', color: activeTrigger.color, fontWeight: 800, marginBottom: '0.35rem' }}>{label}</div>
                      <div style={{ fontSize: '0.74rem', color: '#cbd5e1', lineHeight: 1.6 }}>{body}</div>
                    </div>
                  ))}
                </div>

                <div style={{ background: `${activeTrigger.color}10`, border: `1px solid ${activeTrigger.color}2e`, borderRadius: '8px', padding: '0.75rem' }}>
                  <div style={{ fontSize: '0.72rem', color: activeTrigger.color, fontWeight: 900, marginBottom: '0.4rem' }}>실전 활용 체크리스트</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.45rem' }}>
                    {activeTrigger.use.map((item, idx) => (
                      <div key={item} style={{ display: 'grid', gridTemplateColumns: '22px 1fr', gap: '0.45rem', alignItems: 'flex-start' }}>
                        <span style={{ width: '18px', height: '18px', borderRadius: '5px', background: `${activeTrigger.color}20`, color: activeTrigger.color, fontSize: '0.64rem', fontWeight: 900, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{idx + 1}</span>
                        <span style={{ fontSize: '0.73rem', color: '#cbd5e1', lineHeight: 1.5 }}>{item}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div style={{ background: 'rgba(45,212,191,0.06)', border: '1px solid rgba(45,212,191,0.18)', borderRadius: '8px', padding: '0.75rem 0.9rem', fontSize: '0.76rem', color: '#94a3b8', lineHeight: 1.6 }}>
                <strong style={{ color: '#2dd4bf' }}>요약:</strong> 7개년 공통 신호는 “좋은 재무제표 하나”가 아니라
                <strong style={{ color: '#e2e8f0' }}> KOSDAQ 중소형주 + 성장/테마 섹터 + 거래량·거래대금 10배 이상 폭증</strong>입니다.
                후보 발굴에서는 가격이 이미 3배 오른 뒤가 아니라, 거래대금 피크가 생기는 초입과 섹터 주도력 변화를 같이 봐야 합니다.
              </div>
            </div>
          )}

          <div style={{ background: 'rgba(15,23,42,0.42)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: '8px', padding: '0.95rem', display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontSize: '0.94rem', fontWeight: 900, color: '#f8fafc' }}>20년 이후 3배주 트리거 통계</div>
                <div style={{ ...sectionSub, marginTop: '0.15rem' }}>
                  보통주-연도별 저점 코호트 기준으로, 같은 신호가 잡힌 종목 중 실제 3배 이상 상승한 비율과 실패 종목의 우세 하락 신호를 계산합니다.
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(88px, 1fr))', gap: '0.45rem', minWidth: '290px' }}>
                {[
                  ['표본', triggerScope?.sample_count, '건', '#e2e8f0'],
                  ['3배주', triggerScope?.winner_count, '건', '#4ade80'],
                  ['기본확률', triggerScope?.base_winner_rate, '%', '#fbbf24'],
                ].map(([label, value, suffix, color]) => (
                  <div key={label} style={{ background: 'rgba(255,255,255,0.035)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '7px', padding: '0.52rem 0.6rem', textAlign: 'right' }}>
                    <div style={{ fontSize: '0.65rem', color: '#64748b' }}>{label}</div>
                    <div style={{ fontSize: '0.96rem', color, fontWeight: 900, marginTop: '0.08rem' }}>{fmtStat(value, suffix)}</div>
                  </div>
                ))}
              </div>
            </div>

            {triggerAnalysisError && (
              <div style={{ fontSize: '0.74rem', color: '#fca5a5', background: 'rgba(248,113,113,0.08)', borderRadius: '7px', padding: '0.55rem 0.7rem' }}>
                트리거 통계를 불러오지 못했습니다: {triggerAnalysisError}
              </div>
            )}

            {triggerRows.length > 0 && (
              <>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.75rem' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: '#64748b' }}>
                        {['시그널', '분류', '신호종목', '3배주', '적중률', '커버리지', '기본대비', '실패'].map((h) => (
                          <th key={h} style={{ padding: '0.42rem 0.55rem', textAlign: ['신호종목', '3배주', '적중률', '커버리지', '기본대비', '실패'].includes(h) ? 'right' : 'left', fontWeight: 600 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {triggerRows.slice(0, 14).map((r, i) => (
                        <tr key={r.key} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', background: i % 2 ? 'rgba(255,255,255,0.018)' : 'transparent' }}>
                          <td style={{ padding: '0.5rem 0.55rem', color: '#e2e8f0', fontWeight: 800 }}>
                            {r.name}
                            <div style={{ fontSize: '0.64rem', color: '#64748b', fontWeight: 500, marginTop: '0.12rem', lineHeight: 1.35 }}>{r.definition}</div>
                          </td>
                          <td style={{ padding: '0.5rem 0.55rem', color: '#93c5fd', whiteSpace: 'nowrap' }}>{r.category}</td>
                          <td style={{ padding: '0.5rem 0.55rem', textAlign: 'right', color: '#cbd5e1' }}>{fmtStat(r.signal_count)}</td>
                          <td style={{ padding: '0.5rem 0.55rem', textAlign: 'right', color: '#4ade80', fontWeight: 800 }}>{fmtStat(r.winner_count)}</td>
                          <td style={{ padding: '0.5rem 0.55rem', textAlign: 'right', color: '#fbbf24', fontWeight: 900 }}>{fmtStat(r.winner_rate, '%')}</td>
                          <td style={{ padding: '0.5rem 0.55rem', textAlign: 'right', color: '#a78bfa' }}>{fmtStat(r.winner_coverage, '%')}</td>
                          <td style={{ padding: '0.5rem 0.55rem', textAlign: 'right', color: r.lift_vs_base >= 1.3 ? '#4ade80' : '#94a3b8', fontWeight: 800 }}>{fmtStat(r.lift_vs_base, 'x')}</td>
                          <td style={{ padding: '0.5rem 0.55rem', textAlign: 'right', color: '#fca5a5' }}>{fmtStat(r.failed_count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '0.75rem' }}>
                  <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '8px', padding: '0.8rem' }}>
                    <div style={{ fontSize: '0.82rem', color: '#f1f5f9', fontWeight: 900, marginBottom: '0.55rem' }}>텐버거 후보로 올릴 복합 신호</div>
                    {comboRows.slice(0, 8).map((r, idx) => (
                      <div key={r.keys.join('-')} style={{ display: 'grid', gridTemplateColumns: '22px 1fr auto', gap: '0.55rem', alignItems: 'center', padding: '0.45rem 0', borderBottom: idx < 7 ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                        <span style={{ width: '20px', height: '20px', borderRadius: '6px', background: 'rgba(74,222,128,0.12)', color: '#4ade80', fontSize: '0.66rem', fontWeight: 900, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{idx + 1}</span>
                        <div>
                          <div style={{ fontSize: '0.74rem', color: '#e2e8f0', fontWeight: 800 }}>{r.signals.join(' + ')}</div>
                          <div style={{ fontSize: '0.64rem', color: '#64748b', marginTop: '0.08rem' }}>n={fmtStat(r.count)} · 3배 {fmtStat(r.winner_count)}건 · 중앙 {fmtStat(r.median_multiple, 'x')}</div>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <div style={{ fontSize: '0.9rem', color: '#4ade80', fontWeight: 900 }}>{fmtStat(r.winner_rate, '%')}</div>
                          <div style={{ fontSize: '0.62rem', color: '#64748b' }}>{fmtStat(r.lift_vs_base, 'x')}</div>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '8px', padding: '0.8rem' }}>
                    <div style={{ fontSize: '0.82rem', color: '#f1f5f9', fontWeight: 900, marginBottom: '0.55rem' }}>신호가 떠도 내려야 할 실패 신호</div>
                    {failureRows.slice(0, 6).map((r, idx) => {
                      const top = r.dominant_failure_signals?.[0];
                      return (
                        <div key={r.trigger_key} style={{ padding: '0.48rem 0', borderBottom: idx < 5 ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.6rem' }}>
                            <span style={{ fontSize: '0.74rem', color: '#e2e8f0', fontWeight: 800 }}>{r.trigger_name}</span>
                            <span style={{ fontSize: '0.72rem', color: '#fbbf24', fontWeight: 800 }}>{fmtStat(r.winner_rate, '%')}</span>
                          </div>
                          <div style={{ fontSize: '0.68rem', color: '#fca5a5', marginTop: '0.18rem', lineHeight: 1.45 }}>
                            {top ? `${top.name}: 실패군 ${fmtStat(top.failed_rate, '%')} vs 성공군 ${fmtStat(top.winner_rate, '%')} (${fmtStat(top.gap, '%p')} 차이)` : '실패 신호 부족'}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div style={{ background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.18)', borderRadius: '8px', padding: '0.72rem 0.85rem', fontSize: '0.75rem', color: '#94a3b8', lineHeight: 1.6 }}>
                  <strong style={{ color: '#fbbf24' }}>판정 규칙:</strong> 후보 상향은 거래대금 10배 이상에 기관/외국인 순매수 또는 60일 신고가 재돌파가 붙을 때 우선합니다.
                  반대로 신호가 있어도 20거래일 후속 상승이 약하고 거래대금 5배 미만으로 식으면 후보 강등 신호로 봅니다.
                </div>
              </>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '0.75rem' }}>
            {selectedWinnerRows.map((stock) => (
              <div key={`${winnerYear}-${stock.code}`} style={{ background: 'rgba(255,255,255,0.035)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: '8px', padding: '0.85rem', display: 'flex', flexDirection: 'column', gap: '0.55rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.6rem', alignItems: 'flex-start' }}>
                  <div>
                    <div style={{ fontSize: '0.92rem', fontWeight: 800, color: '#f8fafc' }}>{stock.name}</div>
                    <div style={{ fontSize: '0.72rem', color: '#64748b', marginTop: '0.1rem' }}>{stock.code} · {stock.market}</div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: '1rem', fontWeight: 900, color: '#4ade80' }}>{stock.multiple.toFixed(2)}x</div>
                    <div style={{ fontSize: '0.66rem', color: '#64748b' }}>저가→고가</div>
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                  <span style={{ padding: '0.16rem 0.5rem', borderRadius: '99px', fontSize: '0.68rem', color: '#93c5fd', background: 'rgba(96,165,250,0.10)', border: '1px solid rgba(96,165,250,0.22)' }}>{stock.sector}</span>
                  <span style={{ padding: '0.16rem 0.5rem', borderRadius: '99px', fontSize: '0.68rem', color: '#c4b5fd', background: 'rgba(167,139,250,0.10)', border: '1px solid rgba(167,139,250,0.22)' }}>{stock.theme}</span>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.45rem' }}>
                  <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '6px', padding: '0.45rem 0.55rem' }}>
                    <div style={{ fontSize: '0.66rem', color: '#64748b' }}>거래량 피크</div>
                    <div style={{ fontSize: '0.86rem', fontWeight: 700, color: '#e2e8f0' }}>{getVolPeak(stock).toFixed(1)}배</div>
                  </div>
                  <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '6px', padding: '0.45rem 0.55rem' }}>
                    <div style={{ fontSize: '0.66rem', color: '#64748b' }}>거래대금 피크</div>
                    <div style={{ fontSize: '0.86rem', fontWeight: 700, color: '#e2e8f0' }}>{getAmountPeak(stock).toFixed(1)}배</div>
                  </div>
                </div>

                <div style={{ fontSize: '0.76rem', color: '#94a3b8', lineHeight: 1.55 }}>
                  <strong style={{ color: '#fbbf24' }}>상승 이유:</strong> {stock.reason}
                </div>
              </div>
            ))}
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: '#64748b' }}>
                  {['종목', '시장', '섹터', '테마', '상승배율', '거래량', '거래대금'].map((h) => (
                    <th key={h} style={{ padding: '0.4rem 0.6rem', textAlign: ['상승배율', '거래량', '거래대금'].includes(h) ? 'right' : 'left', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {selectedWinnerRows.map((stock, i) => (
                  <tr key={`row-${winnerYear}-${stock.code}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)' }}>
                    <td style={{ padding: '0.45rem 0.6rem', color: '#e2e8f0', fontWeight: 700 }}>{stock.name} <span style={{ color: '#64748b', fontWeight: 500 }}>({stock.code})</span></td>
                    <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8' }}>{stock.market}</td>
                    <td style={{ padding: '0.45rem 0.6rem', color: '#93c5fd' }}>{stock.sector}</td>
                    <td style={{ padding: '0.45rem 0.6rem', color: '#c4b5fd' }}>{stock.theme}</td>
                    <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right', color: '#4ade80', fontWeight: 800 }}>{stock.multiple.toFixed(2)}x</td>
                    <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right', color: '#e2e8f0' }}>{getVolPeak(stock).toFixed(1)}x</td>
                    <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right', color: '#e2e8f0' }}>{getAmountPeak(stock).toFixed(1)}x</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* 종합 초과수익 테이블 */}
        <div style={{ ...sectionStyle, marginBottom: '1.2rem' }}>
          <div style={sectionTitle}>시그널별 초과수익 종합 (시장 평균 대비)</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: '#64748b' }}>
                  {['시그널', 'N=1일', 'N=5일', 'N=20일', 'N=60일', '승률', '등급'].map(h => (
                    <th key={h} style={{ padding: '0.4rem 0.6rem', textAlign: h === '시그널' ? 'left' : 'right', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {EXCESS_ROWS.map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)' }}>
                    <td style={{ padding: '0.45rem 0.6rem', color: '#e2e8f0', fontWeight: 500 }}>{r.signal}</td>
                    {[r.n1, r.n5, r.n20, r.n60].map((v, j) => (
                      <td key={j} style={{ padding: '0.45rem 0.6rem', textAlign: 'right',
                        color: v === 'N/A' ? '#475569' : v.startsWith('+') ? '#4ade80' : v === '0.00' ? '#94a3b8' : '#f87171' }}>
                        {v}
                      </td>
                    ))}
                    <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right', color: '#94a3b8' }}>{r.wr}</td>
                    <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right' }}>
                      <span style={{ padding: '0.1rem 0.5rem', borderRadius: '99px', fontSize: '0.72rem', fontWeight: 700,
                        color: GRADE_COLOR[r.grade], background: GRADE_BG[r.grade], border: `1px solid ${GRADE_COLOR[r.grade]}40` }}>
                        {r.grade}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ ...sectionSub }}>
            ※ 등급 기준: A = 60일 초과수익 +3%pt 이상 + 승률 55%+, B = 유의미한 양(+) 패턴, C = 단기 약세 / 장기 수렴, D = 전 기간 시장 대비 부진
          </div>
        </div>

        {/* 1. 프로그램 매매 */}
        <div style={{ ...sectionStyle, marginBottom: '1.2rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.4rem' }}>
            <div>
              <div style={sectionTitle}>① 프로그램 매매 → KOSPI 영향</div>
              <div style={sectionSub}>분석 기간: 2020~2026 · 샘플 N=158건 · P90 이상 대규모 순매수 기준</div>
            </div>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <button style={tabBtn(progTab === 'chart')} onClick={() => setProgTab('chart')}>차트</button>
              <button style={tabBtn(progTab === 'table')} onClick={() => setProgTab('table')}>수치</button>
            </div>
          </div>

          {progTab === 'chart' ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={PROG_DATA} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)" />
                <XAxis dataKey="period" stroke="#64748b" tick={{ fontSize: 11 }} />
                <YAxis stroke="#64748b" tick={{ fontSize: 11 }} tickFormatter={v => `${v}%`} />
                <Tooltip {...TOOLTIP_STYLE} formatter={(v, n) => [`${v.toFixed(2)}%`, n]} />
                <Legend wrapperStyle={{ fontSize: '0.78rem', paddingTop: '0.5rem' }} />
                <Line type="monotone" dataKey="buy" name="대규모 순매수 후" stroke="#2dd4bf" strokeWidth={2.5} dot={{ r: 4 }} />
                <Line type="monotone" dataKey="sell" name="대규모 순매도 후" stroke="#60a5fa" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="4 2" />
                <Line type="monotone" dataKey="base" name="시장 평균" stroke="#475569" strokeWidth={1.5} dot={false} strokeDasharray="6 3" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: '#64748b' }}>
                  {['구분', '1일', '5일', '10일', '20일'].map(h => (
                    <th key={h} style={{ padding: '0.4rem 0.6rem', textAlign: h === '구분' ? 'left' : 'right' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  { name: '대규모 순매수 후', vals: ['0.32%', '1.69%', '2.65%', '5.47%'], color: '#2dd4bf' },
                  { name: '대규모 순매도 후', vals: ['0.28%', '1.58%', '3.29%', '6.16%'], color: '#60a5fa' },
                  { name: '시장 평균', vals: ['0.11%', '0.52%', '0.97%', '1.98%'], color: '#475569' },
                  { name: '순매수 초과수익', vals: ['+0.21%', '+1.17%', '+1.68%', '+3.49%'], color: '#4ade80' },
                ].map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                    <td style={{ padding: '0.4rem 0.6rem', color: r.color, fontWeight: 600 }}>{r.name}</td>
                    {r.vals.map((v, j) => <td key={j} style={{ padding: '0.4rem 0.6rem', textAlign: 'right', color: '#e2e8f0' }}>{v}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div style={{ ...sectionSub, background: 'rgba(45,212,191,0.06)', borderRadius: '8px', padding: '0.6rem 0.85rem', borderLeft: '3px solid rgba(45,212,191,0.4)' }}>
            <strong style={{ color: '#2dd4bf' }}>핵심 발견:</strong> 대규모 프로그램 순매수(4,400억+)일 이후 KOSPI 20일 초과수익 <strong style={{ color: '#4ade80' }}>+3.49%</strong>.
            역설적으로 대규모 순매도 후에도 강한 반등 (+4.18%) — 과매도 회복 패턴. 승률 63~66%.
          </div>
        </div>

        {/* 2. 수급 팩터 */}
        <div style={{ ...sectionStyle, marginBottom: '1.2rem' }}>
          <div>
            <div style={sectionTitle}>② 수급 팩터 → 종목별 영향</div>
            <div style={sectionSub}>분석 기간: 2018~2026 · 162K+ 이벤트 · inst/frn_net_buy_amt 상위 5% 기준</div>
          </div>

          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={SUPPLY_DATA} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)" />
              <XAxis dataKey="period" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} tickFormatter={v => `${v}%`} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v, n) => [`${v.toFixed(2)}%`, n]} />
              <Legend wrapperStyle={{ fontSize: '0.78rem', paddingTop: '0.5rem' }} />
              <Line type="monotone" dataKey="both" name="기관+외국인 동반" stroke="#f97316" strokeWidth={2.5} dot={{ r: 4 }} />
              <Line type="monotone" dataKey="inst" name="기관 순매수 상위5%" stroke="#2dd4bf" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="4 2" />
              <Line type="monotone" dataKey="frn"  name="외국인 순매수 상위5%" stroke="#60a5fa" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="2 3" />
              <Line type="monotone" dataKey="base" name="랜덤 baseline" stroke="#475569" strokeWidth={1.5} dot={false} strokeDasharray="6 3" />
            </LineChart>
          </ResponsiveContainer>

          <div style={{ ...sectionSub, background: 'rgba(251,191,36,0.06)', borderRadius: '8px', padding: '0.6rem 0.85rem', borderLeft: '3px solid rgba(251,191,36,0.35)' }}>
            <strong style={{ color: '#fbbf24' }}>주의:</strong> 기관/외국인 단일 순매수는 5~20일 시장 대비 <strong style={{ color: '#f87171' }}>언더퍼폼</strong>(-1%pt).
            기관+외국인 동반 순매수만 60일에서 +5.83%로 소폭 초과. 단기 역선택 효과(이미 오른 뒤 매수) 가능성.
          </div>
        </div>

        {/* 3. 추세추종 */}
        <div style={{ ...sectionStyle, marginBottom: '1.2rem' }}>
          <div>
            <div style={sectionTitle}>③ 추세추종 시그널 → 종목별 영향</div>
            <div style={sectionSub}>분석 기간: 2015~2026 · 500종목 대상 · 랜덤 baseline 비교</div>
          </div>

          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={TREND_DATA} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)" />
              <XAxis dataKey="period" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} tickFormatter={v => `${v}%`} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v, n) => [`${v.toFixed(2)}%`, n]} />
              <Legend wrapperStyle={{ fontSize: '0.78rem', paddingTop: '0.5rem' }} />
              <Line type="monotone" dataKey="highs"  name="52주 신고가 돌파" stroke="#f97316" strokeWidth={2.5} dot={{ r: 4 }} />
              <Line type="monotone" dataKey="vol3x"  name="거래량 폭발 (3배)" stroke="#4ade80" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="4 2" />
              <Line type="monotone" dataKey="golden" name="골든크로스(MA5{'>'} MA20)" stroke="#94a3b8" strokeWidth={1.5} dot={{ r: 3 }} strokeDasharray="3 3" />
              <Line type="monotone" dataKey="base"   name="랜덤 baseline" stroke="#475569" strokeWidth={1.5} dot={false} strokeDasharray="6 3" />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" strokeDasharray="2 2" />
            </LineChart>
          </ResponsiveContainer>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.6rem' }}>
            {[
              { label: '52주 신고가 돌파', val: '+4.59%pt', sub: '60일 초과수익 · 승률 57%', color: '#f97316', bg: 'rgba(249,115,22,0.10)' },
              { label: '거래량 폭발 (3배)', val: '+1.80%pt', sub: '60일 초과수익 · 단기 +0.38%pt 유효', color: '#4ade80', bg: 'rgba(74,222,128,0.08)' },
              { label: '골든크로스(MA5{'>'} MA20)', val: '-0.96%pt', sub: '60일 시장 대비 부진', color: '#f87171', bg: 'rgba(248,113,113,0.08)' },
            ].map(({ label, val, sub, color, bg }) => (
              <div key={label} style={{ background: bg, border: `1px solid ${color}30`, borderRadius: '8px', padding: '0.6rem 0.8rem', textAlign: 'center' }}>
                <div style={{ fontSize: '0.75rem', color, marginBottom: '0.15rem' }}>{label}</div>
                <div style={{ fontSize: '1.1rem', fontWeight: 800, color }}>{val}</div>
                <div style={{ fontSize: '0.68rem', color: '#64748b', marginTop: '0.15rem' }}>{sub}</div>
              </div>
            ))}
          </div>

          <div style={{ ...sectionSub, background: 'rgba(249,115,22,0.06)', borderRadius: '8px', padding: '0.6rem 0.85rem', borderLeft: '3px solid rgba(249,115,22,0.4)' }}>
            <strong style={{ color: '#f97316' }}>핵심 발견:</strong> 52주 신고가 돌파가 가장 강력한 시그널 (60일 +13.3% vs 시장 8.71%).
            골든크로스(MA5{'>'} MA20)는 <strong style={{ color: '#f87171' }}>지연 신호</strong>로 시장 대비 부진.
            실전 조합 권장: <strong style={{ color: '#4ade80' }}>52주 신고가 + 거래량 확인 + 프로그램 순매수 동반</strong>.
          </div>
        </div>

        {/* 결론 */}
        <div style={{ ...sectionStyle }}>
          <div style={sectionTitle}>실전 적용 시사점</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {[
              { emoji: '🏆', title: '최강 시그널', desc: '52주 신고가 돌파 → 매수 진입 후 60일 보유. 승률 57%, 초과수익 +4.59%.' },
              { emoji: '📡', title: '보조 확인 신호', desc: '프로그램 매매 순매수 동반(KOSPI 상승 배경)이면 52주 신고가 신뢰도 ↑.' },
              { emoji: '⚠️', title: '단독 수급 신호 주의', desc: '기관/외국인 단독 순매수는 5일~20일 역효과. 동반 순매수 + 장기(60일+) 포지션에서만 유효.' },
              { emoji: '❌', title: '피해야 할 신호', desc: '골든크로스(MA5{'>'} MA20) 단독 사용 — 모든 기간 시장 대비 부진. 이미 상승한 뒤 뒤늦게 신호 발생.' },
              { emoji: '🔍', title: '데이터 공백 보완', desc: '신용잔고는 2019~현재 수집 완료(3.5M행). 외국인 지분율은 2026-03~ 이후만. 종목별 프로그램 매매는 2026-05-21~만 존재 — 이 두 항목의 장기 event study는 추가 수집 필요.' },
            ].map(({ emoji, title, desc }) => (
              <div key={title} style={{ display: 'flex', gap: '0.75rem', padding: '0.5rem 0.75rem', background: 'rgba(255,255,255,0.03)', borderRadius: '8px' }}>
                <span style={{ fontSize: '1rem', flexShrink: 0 }}>{emoji}</span>
                <div>
                  <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#f1f5f9' }}>{title}</div>
                  <div style={{ fontSize: '0.77rem', color: '#94a3b8', marginTop: '0.1rem' }}>{desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

      </> : (
        <DeepDrawdownReport
          report={hypothesisReports.find(report => report.id === researchTab)}
          loading={hypothesisLoading}
          error={hypothesisError}
        />
      )}

      </div>
    </div>
  );
}
