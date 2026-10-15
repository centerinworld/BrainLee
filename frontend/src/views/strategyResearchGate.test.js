/**
 * strategyResearchGate.test.js — `node --test` 로 실행(추가 의존성 0).
 *   cd runtime/frontend && node --test src/views/strategyResearchGate.test.js
 *
 * 회귀 대상(Planner 지목 결함 2): `evaluation:null` 이 화면에서 "0.0%" 로 그려지던 문제.
 * 프런트엔드 표시 게이트가 (a) not_usable/상태없음 에서 성과·추천 패널을 숨기고
 * (b) 결측을 0 으로 위장하지 않는지 고정한다.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  STATUS_NOT_USABLE,
  STATUS_USABLE,
  anyResearchSectionVisible,
  formatCount,
  formatPercent,
  formatSignedPercent,
  reasonLabel,
  strategyResearchGate,
  visibleResearchSections,
} from './strategyResearchGate.js';

const LIVE_BLOCKED = {
  strategy_research_status: STATUS_NOT_USABLE,
  strategy_research_reasons: [
    'dataset_table_missing',
    'dataset_table_not_canonical',
    'run_spec_proof_missing',
  ],
  strategy_research_notice:
    '정적 전략 요약(research_outputs/strategy_research_summary.json)이 … 사유: dataset_table_missing',
  current_rankings: null,
  evaluation: null,
  latest_snapshot_date: '2026-10-02',
};

const USABLE = {
  strategy_research_status: STATUS_USABLE,
  strategy_research_reasons: [],
  current_rankings: { ml_top20: [{ stock_code: '065170', stock_name: '비엘팜텍' }] },
  evaluation: { ml_top10: { precision: 0.14 } },
};

test('live not_usable response blocks the panels', () => {
  const gate = strategyResearchGate(LIVE_BLOCKED);
  assert.equal(gate.blocked, true);
  assert.deepEqual(gate.reasons, [
    'dataset_table_missing',
    'dataset_table_not_canonical',
    'run_spec_proof_missing',
  ]);
});

test('blocked gate hides every summary-derived section', () => {
  const sections = visibleResearchSections(strategyResearchGate(LIVE_BLOCKED));
  assert.deepEqual(sections, {
    datasetStats: false,
    evaluation: false,
    qualityValidation: false,
    rankings: false,
  });
  assert.equal(anyResearchSectionVisible(sections), false);
});

test('usable gate shows every section', () => {
  const gate = strategyResearchGate(USABLE);
  assert.equal(gate.blocked, false);
  const sections = visibleResearchSections(gate);
  assert.equal(anyResearchSectionVisible(sections), true);
  assert.deepEqual(sections, {
    datasetStats: true,
    evaluation: true,
    qualityValidation: true,
    rankings: true,
  });
});

test('missing status field is fail-closed (legacy backend)', () => {
  const gate = strategyResearchGate({ dataset: { snapshot_rows: 193093 } });
  assert.equal(gate.blocked, true);
  assert.equal(gate.status, 'unknown');
  assert.equal(anyResearchSectionVisible(visibleResearchSections(gate)), false);
});

test('malformed status is fail-closed even if rankings are present', () => {
  const gate = strategyResearchGate({
    strategy_research_status: 'partially_usable',
    current_rankings: { ml_top20: [{ stock_code: '065170' }] },
    evaluation: { ml_top10: { precision: 0.14 } },
  });
  assert.equal(gate.blocked, true);
  assert.equal(gate.status, 'partially_usable');
  assert.equal(anyResearchSectionVisible(visibleResearchSections(gate)), false);
});

test('null payload is blocked, not rendered', () => {
  const gate = strategyResearchGate(null);
  assert.equal(gate.blocked, true);
  assert.equal(visibleResearchSections(gate).rankings, false);
});

test('gate with no gate argument at all stays blocked', () => {
  assert.equal(anyResearchSectionVisible(visibleResearchSections(undefined)), false);
});

test('notice names the reasons and never claims a return', () => {
  const gate = strategyResearchGate(LIVE_BLOCKED);
  assert.match(gate.notice, /검증을 통과하지 못했습니다/);
  assert.match(gate.notice, /표기한 스냅샷 테이블이 라이브 DB에 없음/);
  assert.ok(!gate.notice.includes('0.0%'), gate.notice);
});

test('notice falls back when the backend sends no notice', () => {
  const gate = strategyResearchGate({ strategy_research_status: STATUS_NOT_USABLE });
  assert.match(gate.notice, /순위·수익률을 표시하지 않습니다/);
});

test('regression: null evaluation must not render as 0.0%', () => {
  assert.equal(formatPercent(LIVE_BLOCKED.evaluation?.ml_top10?.precision), '—');
  assert.equal(formatPercent(null), '—');
  assert.equal(formatPercent(undefined), '—');
  assert.equal(formatPercent(''), '—');
  assert.equal(formatPercent(Number.NaN), '—');
});

test('a real zero is still rendered as 0.0%, distinct from missing', () => {
  assert.equal(formatPercent(0), '0.0%');
  assert.notEqual(formatPercent(0), formatPercent(null));
});

test('percentages are scaled from ratios', () => {
  assert.equal(formatPercent(0.14), '14.0%');
  assert.equal(formatPercent(0.9393, 1), '93.9%');
});

test('raw percentages are not scaled twice', () => {
  assert.equal(formatPercent(12.3, 1, { raw: true }), '12.3%');
  assert.equal(formatPercent(null, 1, { raw: true }), '—');
});

test('signed percentages keep the sign but never fabricate a zero', () => {
  assert.equal(formatSignedPercent(0.05), '+5.0%');
  assert.equal(formatSignedPercent(null), '—');
  assert.equal(formatSignedPercent(undefined), '—');
  assert.equal(formatSignedPercent(-0.02), '-2.0%');
  assert.notEqual(formatSignedPercent(null), '+0.0%');
});

test('counts show the missing marker instead of zero', () => {
  assert.equal(formatCount(null), '—');
  assert.equal(formatCount(193093, 0), (193093).toLocaleString());
  assert.equal(formatCount(42), '42.0');
});

test('unknown reason codes degrade to the raw code, never to silence', () => {
  assert.equal(reasonLabel('brand_new_reason'), 'brand_new_reason');
  const gate = strategyResearchGate({
    strategy_research_status: STATUS_NOT_USABLE,
    strategy_research_reasons: ['brand_new_reason'],
  });
  assert.match(gate.notice, /brand_new_reason/);
});

test('non-string reason entries are ignored without throwing', () => {
  const gate = strategyResearchGate({
    strategy_research_status: STATUS_NOT_USABLE,
    strategy_research_reasons: [null, 7, 'pit_proof_failed'],
  });
  assert.deepEqual(gate.reasons, ['pit_proof_failed']);
});

// ── 소비자 배선(StrategyHub.jsx) 정적 회귀 ──────────────────────────────
// 렌더 수준 DOM 하네스가 없다(브라우저/테스트 렌더러 미설치). 그래서 결함이
// 되살아나면 실패하는 문자열 수준 회귀만 고정한다:
//   (a) 네 개 연구 패널이 모두 게이트를 통과해야 그려져야 하고,
//   (b) 종전 "null → 0" 서식(`(x || 0) * 100`)이 되돌아오면 안 된다.
import { readFileSync } from 'node:fs';

const HUB_SOURCE = readFileSync(new URL('./StrategyHub.jsx', import.meta.url), 'utf8');

test('StrategyHub gates every research panel on the verdict', () => {
  assert.match(HUB_SOURCE, /strategyResearchGate\(strategyResearch\)/);
  for (const key of ['datasetStats', 'evaluation', 'qualityValidation', 'rankings']) {
    assert.ok(HUB_SOURCE.includes(`researchSections.${key} &&`), `${key} 게이트 누락`);
  }
});

test('StrategyHub renders an explicit blocked state', () => {
  assert.match(HUB_SOURCE, /strategy-research-blocked-banner/);
  assert.match(HUB_SOURCE, /researchGate\.blocked/);
  // 차단 배너를 다른 상태(로딩 등)에서 쓰지 않는다 — 성과 패널 옆에서만 뜬다.
  const bannerIdx = HUB_SOURCE.indexOf('strategy-research-blocked-banner');
  assert.ok(HUB_SOURCE.lastIndexOf('strategy-research-blocked-banner') === bannerIdx);
});

test('legacy null-to-zero formatting does not come back', () => {
  for (const legacy of [
    'researchEval?.ml_top10?.precision || 0',
    'researchEval?.ml_top20?.avg_forward_ret_12m || 0',
    'strategyResearch?.dataset?.snapshot_rows || 0',
    'strategyResearch?.dataset?.stocks_covered || 0',
    'strategyResearch?.dataset?.label_3x_12m_rows || 0',
    'strategyResearch?.dataset?.snapshot_months || 0',
  ]) {
    assert.ok(!HUB_SOURCE.includes(legacy), `legacy fallback restored: ${legacy}`);
  }
});

test('research panels use the missing-aware formatters', () => {
  assert.match(HUB_SOURCE, /formatPercent\(researchEval\?\.ml_top10\?\.precision\)/);
  assert.match(HUB_SOURCE, /formatPercent\(researchEval\?\.ml_top20\?\.avg_forward_ret_12m\)/);
  assert.match(HUB_SOURCE, /formatCount\(strategyResearch\?\.dataset\?\.snapshot_rows, 0\)/);
  assert.match(HUB_SOURCE, /formatSignedPercent\(item\.avg_ret, 1, \{ raw: true \}\)/);
});
