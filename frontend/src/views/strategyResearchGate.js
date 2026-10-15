/**
 * strategyResearchGate.js — 전략 연구 정적 요약의 표시 게이트 (2026-10-11).
 *
 * 왜 필요한가
 * ----------
 * `/api/backtest/strategy-research/summary` 는 fail-closed 판정(`strategy_research_status`)과
 * 함께 **검증 불통과 시 `evaluation`·`current_rankings` 를 null 로 비워서** 보낸다.
 * 그런데 화면은 `(Q?.ml_top10?.precision || 0) * 100` 처럼 null 을 0 으로 강제 변환해
 * "ML Top10 정밀도 0.0%" 같은 **거짓 수치**를 그렸다. 값이 없다는 사실을 0 이라는
 * 값으로 위장하는 것이 결함이었다.
 *
 * 규칙
 * ----
 * 1. `strategy_research_status !== 'usable'` 이면 성과·추천 패널을 렌더하지 않는다.
 * 2. 상태 필드가 **아예 없으면**(구버전 응답) 차단으로 본다 — fail-closed.
 * 3. 계산된 0 과 '값 없음' 은 절대 같은 표시가 되면 안 된다(`formatMetric`).
 * 4. 이 모듈은 JSX·React 를 import 하지 않는다 → `node --test` 로 그대로 검증 가능.
 */

export const STATUS_USABLE = 'usable';
export const STATUS_NOT_USABLE = 'not_usable';

const REASON_LABELS = {
  dataset_table_missing: '표기한 스냅샷 테이블이 라이브 DB에 없음',
  dataset_table_not_canonical: '표기한 스냅샷 테이블이 canonical이 아님',
  dataset_table_declared_missing: '스냅샷 테이블 표기 자체가 없음',
  canonical_table_missing: 'canonical 스냅샷 테이블이 없음',
  canonical_unreadable: 'canonical 대조에 실패함',
  static_summary_missing: '정적 요약 파일이 없음',
  static_summary_unparsable: '정적 요약 파일을 읽지 못함',
  generated_at_missing: '생성시각이 없음',
  generated_at_unparsable: '생성시각을 파싱할 수 없음',
  generated_at_naive_timezone: '생성시각에 타임존이 없음',
  generated_at_before_snapshot_date: '생성시각이 스냅샷 기준일보다 이전임',
  counts_mismatch: '수치가 canonical과 불일치',
  snapshot_date_mismatch: '스냅샷 기준일이 canonical과 불일치',
  train_end_date_missing: 'train/test 경계일이 없음',
  pit_proof_failed: 'point-in-time 증명 실패',
  run_spec_proof_missing: 'run spec/run hash 연결이 없음',
  run_spec_not_found: 'run hash가 registry에 없음',
  run_spec_unverifiable: 'run spec 조회 실패',
  snapshot_date_unparsable: '스냅샷 기준일을 파싱할 수 없음',
};

/** 사유 코드 → 사람이 읽는 라벨(미등록 코드는 코드 그대로 노출). */
export function reasonLabel(code) {
  return REASON_LABELS[code] || String(code || '');
}

/**
 * 응답을 게이트 판정으로 바꾼다.
 * @returns {{blocked:boolean, status:string, reasons:string[], notice:string, summary:object}}
 */
export function strategyResearchGate(summary) {
  const reasons = Array.isArray(summary?.strategy_research_reasons)
    ? summary.strategy_research_reasons.filter((r) => typeof r === 'string' && r)
    : [];
  const status = typeof summary?.strategy_research_status === 'string'
    ? summary.strategy_research_status
    : null;

  if (status === STATUS_USABLE) {
    return { blocked: false, status: STATUS_USABLE, reasons, notice: '', summary: summary || {} };
  }

  const labelText = reasons.length
    ? reasons.map(reasonLabel).join(' · ')
    : '검증 사유를 받지 못했습니다';
  const heading = status
    ? '전략 연구 요약이 검증을 통과하지 못했습니다'
    : '전략 연구 요약의 검증 상태를 확인할 수 없습니다';
  const tail = typeof summary?.strategy_research_notice === 'string' && summary.strategy_research_notice
    ? summary.strategy_research_notice
    : '정적 요약을 라이브 canonical·run spec·PIT 증거와 대조할 수 없어 순위·수익률을 표시하지 않습니다.';

  return {
    blocked: true,
    status: status || 'unknown',
    reasons,
    notice: `${heading} — ${labelText}. ${tail}`,
    summary: summary || {},
  };
}

/**
 * 차단 시 어떤 패널을 그릴지. 전부 `strategyResearch` 파생 패널이다.
 *
 * `usMinervini` 패널은 여기 없다 — 데이터 출처가 `/api/research/us-minervini` 로 **독립**이고
 * 자체 게이트(`research_grade`/`forward_validated`)를 갖는다. 이 함수가 게이팅하는 대상은
 * 문제가 된 정적 요약 파생 패널뿐이다.
 */
export function visibleResearchSections(gate) {
  const blocked = !gate || gate.blocked !== false;
  return {
    datasetStats: !blocked,
    evaluation: !blocked,
    qualityValidation: !blocked,
    rankings: !blocked,
  };
}

/** 하나라도 그릴 패널이 있는가(= 연구 그리드 렌더 여부). */
export function anyResearchSectionVisible(sections) {
  return Object.values(sections || {}).some(Boolean);
}

/**
 * 값이 없을 때 0 을 그리지 않는다 — '0.0%' 는 **계산된 0** 과 구분 불가능하기 때문.
 * @param {number|string|null|undefined} value 비율(0.14 = 14%)
 * @param {number} digits 소수 자릿수
 * @param {{raw?: boolean}} [options] `raw: true` 면 값이 이미 퍼센트 단위(예: 12.3 = 12.3%)
 */
export function formatPercent(value, digits = 1, options = {}) {
  if (value === null || value === undefined || value === '') return '—';
  let num = Number(value);
  if (!Number.isFinite(num)) return '—';
  if (!options.raw) num *= 100;
  return `${num.toFixed(digits)}%`;
}

/** 부호를 붙여 표시하되 결측은 '+' 없이 '—' 로 남긴다(결측을 0%로 위장하지 않는다). */
export function formatSignedPercent(value, digits = 1, options = {}) {
  const text = formatPercent(value, digits, options);
  if (text === '—') return text;
  const num = Number(value);
  return `${num > 0 ? '+' : ''}${text}`;
}

/** 개수·점수처럼 비율이 아닌 값도 결측을 '—' 로 구분한다. */
export function formatCount(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return digits === 0 ? num.toLocaleString() : num.toFixed(digits);
}
