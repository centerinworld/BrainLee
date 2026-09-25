"""suspension-gap 채움 게이트 계약 회귀 (2026-09-24).

왜 필요한가
-----------
`fill_suspension_gaps_from_marcap_20260924.py` 는 장기 결손(>30일)을 marcap 전량 커버로 채운다. 그 결손일에는
두 종류가 섞여 있고, 한 종류만 믿고 넣어도 된다:

* **정지 마커** (`volume=0` + `open=high=low=0`, `close>0`) — 거래정지 기간에 직전 종가를 이월한 표시.
  `price_integrity.invalid_ohlcv()` 가 "supplied suspension marker" 로 인정하는 형상이고, canonical 읽기 뷰
  `price_history_quality_v` 가 OHLCV 패턴으로 `quality_status='suspended'` 를 파생한다(`price_history` 테이블
  자체에는 `quality_status` 컬럼이 없다). `return_usable=0` 이므로 수익률 계산에서 배제된다 — 기본 허용.
* **실거래행** — 실제 체결 가격. 장기 결손은 *marcap 세그먼트 basis 혼입*일 수도 있다(008800 2016-03-04 x0.1297 과
  2018-04-20 x14.3522 는 서로 역수 = 하루 변동으로 불가능, 기업행위 공시도 없음). 그래서 아래 중 하나가 있어야
  삽입한다:
    (A) 재구성 시계열의 **양 이웃 종가** 대비 **그날의** KRX 가격제한폭 안 — `price_integrity.price_band`
        정책을 날짜별로 적용한다(2015-06-15 전 ±15%, 이후 ±30%; 다음 이웃은 그 다음 거래일 밴드의 역수).
        ±30% 하나로 소급 적용하면 2015-01-02~2015-06-12 구간에서 당시 불가능한 움직임이 통과한다.
    (B) `corporate_action_events` 의 `factor_confirmed` + `backward_price_factor`(또는 역수)가 관측 비율을
        하루 제한폭 안에서 재현(±25일)
    (C) DART `권리락` 공시(-5..+3일) + 같은 종목의 가격조정형 기업행위 등록(±25일)
  그 외는 **거부**하고, 한 행이라도 거부되면 그 이벤트 전체를 보류한다(주변 마커가 "가격으로 넣지 않은 종가"를
  이월하게 되기 때문). 거부·보류·주의 마커·출처 무효행은 감사 큐 CSV 로 나간다 — 여기서 조용히 버려지는 행은 없다.

이 테스트는 라이브 DB 에 쓰지 않는다: 순수 게이트 함수 + 기록용 가짜 커넥션 + 임시 CSV 만 쓴다.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SUSPENSION_SCRIPT = ROOT / "scripts" / "fill_suspension_gaps_from_marcap_20260924.py"
_FILL_SCRIPT = ROOT / "scripts" / "fill_coverage_gaps_20260924.py"


def _load_script(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_script(_SUSPENSION_SCRIPT)
# 두 스크립트의 게이트는 같은 밴드 정의를 써야 한다 (한쪽만 바뀌면 FDR 경로와 marcap 경로가 갈라진다)
fill = _load_script(_FILL_SCRIPT)

MARKER = dict(open=0, high=0, low=0, volume=0)


def series(*rows) -> pd.DataFrame:
    """이벤트 내부일 행들 (code,date,open,high,low,close,volume)."""
    base = dict(code="005930", date="2026-01-05", open=100, high=105, low=99, close=102, volume=1000)
    out = []
    for r in rows:
        row = dict(base); row.update(r); out.append(row)
    return pd.DataFrame(out)


def gate(rows, prev_anchor, next_anchor, ca_events=(), ex_rights=(), next_anchor_date="2026-01-07"):
    """`gate_event` + 다음 앵커 날짜(= 감사의 event_date). 날짜는 필수다 — 제한폭이 날짜별이기 때문.

    이 파일의 픽스처는 전부 2015-06-15 이후(±30% 시대)라 `next_anchor_date` 의 값 자체는 판정을 바꾸지 않는다.
    날짜 경계 동작은 별도 테스트가 못 박는다.
    """
    return guard.gate_event(rows, prev_anchor, next_anchor, ca_events, ex_rights,
                            next_anchor_date=next_anchor_date)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 행 단위 판정
# ─────────────────────────────────────────────────────────────────────────────
class RowGateContract(unittest.TestCase):
    def test_suspension_marker_is_allowed_without_any_anchor(self):
        g = gate(series(dict(date="2026-01-05", close=100, **MARKER)), None, None)
        self.assertEqual(list(g.decision), ["suspension_marker"])
        self.assertEqual(list(g.row_kind), ["suspension_marker"])

    def test_marker_carrying_the_previous_close_forward_raises_no_advisory(self):
        g = gate(series(dict(date="2026-01-05", close=100, **MARKER)), 100.0, 105.0)
        self.assertEqual(list(g.decision), ["suspension_marker"])
        self.assertEqual(list(g.advisory), [""])
        self.assertEqual(g.carry_forward_ratio.iloc[0], 1.0)

    def test_marker_that_does_not_carry_forward_is_allowed_but_queued_as_advisory(self):
        """마커는 기본 허용이지만, 크게 어긋나면 basis splice 지문이므로 사람이 봐야 한다."""
        g = gate(series(dict(date="2026-01-05", close=50, **MARKER)), 100.0, 105.0)
        self.assertEqual(list(g.decision), ["suspension_marker"])
        self.assertIn("previous available close", g.advisory.iloc[0])
        self.assertAlmostEqual(g.carry_forward_ratio.iloc[0], 0.5)

    def test_marker_without_previous_anchor_is_advisory_but_still_allowed(self):
        g = gate(series(dict(date="2026-01-05", close=100, **MARKER)), None, 105.0)
        self.assertEqual(list(g.decision), ["suspension_marker"])
        self.assertIn("unverifiable", g.advisory.iloc[0])

    def test_trading_row_inside_both_neighbour_bands_is_accepted_on_band(self):
        rows = series(dict(date="2026-01-05", close=100, **MARKER),
                      dict(date="2026-01-06", close=103, open=101, high=104, low=100, volume=5000))
        g = gate(rows, 100.0, 105.0)
        self.assertEqual(list(g.decision), ["suspension_marker", "band_ok"])

    def test_band_is_the_same_one_the_fdr_path_uses(self):
        """게이트와 FDR 경로가 다른 밴드를 쓰면 한쪽만 조정주가를 통과시킨다 — 정의는 단일 출처여야 한다."""
        self.assertIs(guard.price_band, fill.price_band)        # 같은 price_integrity 정책 함수를 쓴다
        self.assertEqual(guard.price_band("2015-06-14"), (0.85, 1.15))
        self.assertEqual(guard.price_band("2015-06-15"), (0.70, 1.30))
        self.assertTrue(fill.fdr_band_ok(103, 100, 105, "2026-01-06", next_day="2026-01-07"))

    def test_legitimate_limit_down_is_not_a_splice(self):
        """하루 -30% 하한(다음 이웃 대비 1.40)은 정상 시세다 — 종전 대칭 밴드가 이걸 splice 로 거부했다."""
        rows = series(dict(date="2026-01-05", open=1400, high=1400, low=1400, close=1400, volume=10),
                      dict(date="2026-01-06", open=1400, high=1400, low=1400, close=1400, volume=10),
                      dict(date="2026-01-07", open=1000, high=1000, low=1000, close=1000, volume=10))
        g = gate(rows, 1400.0, 1000.0)
        self.assertEqual(list(g.decision), ["band_ok", "band_ok", "band_ok"])
        # 실제 splice 는 그대로 거부된다
        g2 = gate(series(dict(date="2026-01-06", close=3545, open=3545, high=3545, low=3545, volume=10)),
                              247.0, 3700.0)
        self.assertEqual(list(g2.decision), ["rejected_unverified_basis"])

    def test_band_is_date_sensitive_the_gate_applies_the_policy_of_each_day(self):
        """같은 숫자라도 2015-06-15 전(±15%)과 후(±30%)의 판정이 달라야 한다 — 밴드 소급 적용 금지."""
        pre = gate(series(dict(date="2015-06-10", close=120, open=120, high=120, low=120, volume=10)),
                   100.0, 120.0, next_anchor_date="2015-06-11")
        self.assertEqual(list(pre.decision), ["rejected_unverified_basis"])   # +20% 는 당시 불가능
        post = gate(series(dict(date="2016-06-10", close=120, open=120, high=120, low=120, volume=10)),
                    100.0, 120.0, next_anchor_date="2016-06-11")
        self.assertEqual(list(post.decision), ["band_ok"])                    # ±30% 시대엔 합법

    def test_next_anchor_crossing_the_policy_change_uses_the_next_days_band(self):
        """마지막 내부 행의 다음 이웃은 event_date 행 — 그 날짜의 밴드(역수)로 판정한다."""
        rows = series(dict(date="2015-06-12", close=100, open=100, high=100, low=100, volume=10))
        g = gate(rows, 100.0, 75.0, next_anchor_date="2015-06-15")     # 다음날 -25%: 이후 기준 합법
        self.assertEqual(list(g.decision), ["band_ok"])
        g2 = gate(rows, 100.0, 75.0, next_anchor_date="2015-06-12")    # 그날 기준(±15%)이면 불법
        self.assertEqual(list(g2.decision), ["rejected_unverified_basis"])

    def test_trading_row_out_of_band_without_evidence_is_refused(self):
        g = gate(series(dict(date="2026-01-06", close=10, volume=5000)), 100.0, 105.0)
        self.assertEqual(list(g.decision), ["rejected_unverified_basis"])
        self.assertIn("no confirmed corporate-action evidence", g.evidence.iloc[0])

    def test_band_edges_are_inclusive_for_trading_rows(self):
        g = gate(series(dict(date="2026-01-06", close=70, volume=5000)), 100.0, 70.0)
        self.assertEqual(list(g.decision), ["band_ok"])
        g = gate(series(dict(date="2026-01-06", close=130, volume=5000)), 100.0, 130.0)
        self.assertEqual(list(g.decision), ["band_ok"])
        g = gate(series(dict(date="2026-01-06", close=69, volume=5000)), 100.0, 100.0)
        self.assertEqual(list(g.decision), ["rejected_unverified_basis"])

    def test_missing_anchor_on_a_trading_row_is_refused_not_guessed(self):
        """한쪽 이웃이 없으면 검증 자체가 불가 — fail-closed 로 거부한다."""
        for prev, nxt in ((None, 105.0), (100.0, None), (None, None)):
            with self.subTest(prev=prev, next=nxt):
                g = gate(series(dict(date="2026-01-06", close=103, volume=5000)), prev, nxt)
                self.assertEqual(list(g.decision), ["rejected_unverified_basis"])

    def test_non_positive_or_nan_anchors_are_not_anchors(self):
        for bad in (0.0, -5.0, float("nan")):
            with self.subTest(anchor=bad):
                g = gate(series(dict(date="2026-01-06", close=103, volume=5000)), bad, 105.0)
                self.assertEqual(list(g.decision), ["rejected_unverified_basis"])

    def test_zero_volume_quote_is_gated_like_a_trading_row_and_a_limit_move_passes(self):
        """volume=0 이지만 OHLC 가 살아 있는 행(상한가 잠김 등)은 정지 마커 관례가 아니다 → 밴드 검증 대상."""
        self.assertEqual(guard.row_kind(dict(open=100, high=100, low=100, close=100, volume=0)), "trading_row")
        g = gate(series(dict(date="2026-01-06", open=130, high=130, low=130, close=130, volume=0)),
                             100.0, 135.0)
        self.assertEqual(list(g.decision), ["band_ok"])

    def test_neighbours_are_the_reconstructed_series_not_the_event_endpoints(self):
        """내부 3행: 가운데 행의 이웃은 양 끝점(100/105)이 아니라 앞뒤 내부 행이다."""
        rows = series(dict(date="2026-01-05", close=200, open=200, high=200, low=200, volume=10),
                      dict(date="2026-01-06", close=210, open=210, high=210, low=210, volume=10),
                      dict(date="2026-01-07", close=205, open=205, high=205, low=205, volume=10))
        g = gate(rows, 100.0, 105.0)
        self.assertEqual(g.prev_neighbor.iloc[1], 200.0)
        self.assertEqual(g.next_neighbor.iloc[1], 205.0)
        # 양 끝 행은 앵커(100/105)와 2배 차이라 거부, 가운데 행만 앞뒤 내부 행과 일관돼 통과
        self.assertEqual(list(g.decision),
                         ["rejected_unverified_basis", "band_ok", "rejected_unverified_basis"])

    def test_gate_never_rewrites_source_values(self):
        rows = series(dict(date="2026-01-05", close=100, **MARKER),
                      dict(date="2026-01-06", close=103, open=101, high=104, low=100, volume=5000))
        g = gate(rows, 100.0, 105.0)
        self.assertEqual([tuple(r) for r in g[["close", "volume"]].itertuples(index=False)],
                         [(100.0, 0.0), (103.0, 5000.0)])

    def test_empty_event_produces_no_rows(self):
        g = gate(pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume"]),
                             None, None)
        self.assertTrue(g.empty)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 기업행위 증빙 — 라이브에서 실측한 3건(통과) / 6건(거부) 재현
# ─────────────────────────────────────────────────────────────────────────────
class CorporateActionEvidenceContract(unittest.TestCase):
    # 221800 2023-02-23: 무상증자 권리락 추정, corporate_action_events 에 factor_confirmed bpf=0.25 (2023-02-24)
    CA_221800 = ("2023-02-24", "bonus_issue", "factor_confirmed", 0.25, "주요사항보고서(무상증자결정)")
    # 232830 2020-09-04: CA 는 review_required(계수 미확정) → DART 권리락 공시가 증빙
    CA_232830 = (("2020-08-21", "bonus_issue", "review_required", None, "주요사항보고서(무상증자결정)"),
                 ("2020-09-04", "bonus_issue", "review_required", None, "[기재정정]주요사항보고서(무상증자결정)"))
    DISC_232830 = (("20200903", "권리락(무상증자)"),)

    def test_confirmed_factor_reproduces_the_observed_ratio(self):
        # 2320 -> 634 (x0.2733) : bpf 0.25 대비 하루 시장변동 +9.3% → 제한폭 안
        ev = guard.match_corporate_action("221800", "2023-02-23", 634, 2320, (self.CA_221800,), ())
        self.assertIsNotNone(ev)
        self.assertIn("factor_confirmed", ev)

    def test_confirmed_factor_that_cannot_reproduce_the_ratio_is_not_evidence(self):
        # 008800 2018-04-20 실측 비율 x14.35 — 어떤 확정계수로도 하루 제한폭 안에서 재현되지 않는다
        self.assertIsNone(guard.match_corporate_action("008800", "2018-04-20", 3545, 247,
                                                      (("2018-04-19", "bonus_issue", "factor_confirmed", 0.5, "x"),), ()))
        self.assertIsNone(guard.match_corporate_action("008800", "2016-03-04", 1505, 11600,
                                                      (("2016-03-03", "bonus_issue", "factor_confirmed", 0.5, "x"),), ()))

    def test_inverse_factor_direction_is_accepted(self):
        ev = guard.match_corporate_action("005930", "2026-01-06", 380, 100,
                                          (("2026-01-05", "reverse_split", "factor_confirmed", 0.25, "x"),), ())
        self.assertIsNotNone(ev)

    def test_factor_outside_the_settlement_window_is_not_evidence(self):
        self.assertIsNone(guard.match_corporate_action("221800", "2023-02-23", 634, 2320,
                                                      (("2023-01-20", "bonus_issue", "factor_confirmed", 0.25, "x"),), ()))
        self.assertIsNone(guard.match_corporate_action("221800", "2023-02-23", 634, 2320,
                                                      (("2023-03-25", "bonus_issue", "factor_confirmed", 0.25, "x"),), ()))

    def test_unconfirmed_or_factorless_rows_are_not_evidence(self):
        self.assertIsNone(guard.match_corporate_action("005930", "2026-01-06", 27, 100,
                                                      (("2026-01-05", "bonus_issue", "review_required", 0.25, "x"),), ()))
        self.assertIsNone(guard.match_corporate_action("005930", "2026-01-06", 27, 100,
                                                      (("2026-01-05", "bonus_issue", "factor_confirmed", None, "x"),), ()))

    def test_dart_ex_rights_notice_plus_registered_bonus_issue_is_evidence(self):
        ev = guard.match_corporate_action("232830", "2020-09-04", 1590, 6920, self.CA_232830, self.DISC_232830)
        self.assertIsNotNone(ev)
        self.assertIn("권리락", ev)
        self.assertIn("bonus_issue", ev)

    def test_ex_rights_notice_alone_is_not_evidence(self):
        """공시 텍스트는 맥락이지 권위가 아니다 — 기업행위 등록이 함께 있어야 한다."""
        self.assertIsNone(guard.match_corporate_action("232830", "2020-09-04", 1590, 6920, (), self.DISC_232830))

    def test_ex_rights_notice_without_a_price_adjusting_action_is_not_evidence(self):
        self.assertIsNone(guard.match_corporate_action(
            "232830", "2020-09-04", 1590, 6920,
            (("2020-09-04", "not_price_adjusting", "not_price_adjusting", None, "x"),), self.DISC_232830))

    def test_ex_rights_notice_window_is_tight(self):
        for rcept, ok in (("20200903", True), ("20200902", True), ("20200830", True),
                          ("20200828", False), ("20200907", True), ("20200908", False)):
            with self.subTest(rcept=rcept):
                ev = guard.match_corporate_action("232830", "2020-09-04", 1590, 6920,
                                                  self.CA_232830, ((rcept, "권리락(무상증자)"),))
                self.assertEqual(ev is not None, ok, rcept)

    def test_registered_action_outside_the_window_is_not_evidence(self):
        self.assertIsNone(guard.match_corporate_action(
            "232830", "2020-09-04", 1590, 6920,
            (("2020-07-01", "bonus_issue", "review_required", None, "x"),), self.DISC_232830))

    def test_no_evidence_at_all_is_no_evidence(self):
        self.assertIsNone(guard.match_corporate_action("008800", "2016-03-04", 1505, 11600))
        self.assertIsNone(guard.match_corporate_action("008800", "2016-03-04", 1505, None))

    def test_adjusting_event_types_are_an_explicit_allowlist(self):
        """모르는 event_type 은 통과가 아니라 거부로 가야 한다(fail-closed)."""
        self.assertIn("bonus_issue", guard.PRICE_ADJUSTING_EVENT_TYPES)
        self.assertNotIn("not_price_adjusting", guard.PRICE_ADJUSTING_EVENT_TYPES)
        self.assertNotIn("share_increase_review", guard.PRICE_ADJUSTING_EVENT_TYPES)

    def test_evidence_reaches_the_row_decision(self):
        rows = series(dict(date="2026-01-05", close=100, **MARKER),
                      dict(date="2026-01-06", close=27, open=27, high=27, low=27, volume=900))
        self.assertEqual(list(gate(rows, 100.0, 105.0,
                                   (("2026-01-06", "bonus_issue", "factor_confirmed", 0.25, "x"),),
                                   ()).decision),
                         ["suspension_marker", "corporate_action_confirmed"])


# ─────────────────────────────────────────────────────────────────────────────
# 3. 이벤트 단위 — 거부 1행이 이벤트 전체를 보류시킨다
# ─────────────────────────────────────────────────────────────────────────────
class EventLevelContract(unittest.TestCase):
    def test_one_refused_row_blocks_the_whole_event(self):
        rows = series(dict(date="2026-01-05", close=100, **MARKER),
                      dict(date="2026-01-06", close=10, open=10, high=10, low=10, volume=700),
                      dict(date="2026-01-07", close=100, **MARKER))
        g = gate(rows, 100.0, 105.0)
        self.assertEqual(list(g.decision), ["suspension_marker", "rejected_unverified_basis", "suspension_marker"])
        self.assertTrue(guard.event_blocked(g))

    def test_event_without_a_refused_row_is_not_blocked(self):
        rows = series(dict(date="2026-01-05", close=100, **MARKER),
                      dict(date="2026-01-06", close=103, open=101, high=104, low=100, volume=5000))
        self.assertFalse(guard.event_blocked(gate(rows, 100.0, 105.0)))

    def test_non_equity_or_marker_only_events_are_not_blocked(self):
        self.assertFalse(guard.event_blocked(gate(series(dict(close=100, **MARKER)), None, None)))


# ─────────────────────────────────────────────────────────────────────────────
# 4. 출처 무효행 — valid_rows 와 판정이 어긋나면 안 된다
# ─────────────────────────────────────────────────────────────────────────────
class InvalidReasonContract(unittest.TestCase):
    def _survives(self, row) -> bool:
        return len(fill.valid_rows(pd.DataFrame([row]))) == 1

    def test_invalid_reason_agrees_with_valid_rows_on_every_shape(self):
        base = dict(code="005930", date="2026-01-05", open=100, high=110, low=95, close=105, volume=1000)
        cases = [
            dict(base),
            dict(base, close=105.5),
            dict(base, open=0, high=0, low=0, close=100, volume=0),
            dict(base, volume=0),
            dict(base, open=0, high=0, low=0, close=0, volume=0),
            dict(base, close=float("nan")),
            dict(base, volume=float("nan")),
            dict(base, high=99),
            dict(base, low=120),
            dict(base, volume=-1),
            dict(base, close=0),
        ]
        for case in cases:
            with self.subTest(case=case):
                reason = guard.invalid_reason(case)
                self.assertEqual(reason is not None, not self._survives(case), case)

    def test_fractional_price_is_labelled_as_adjusted_basis(self):
        self.assertIn("adjusted_basis", guard.invalid_reason(
            dict(open=100, high=110, low=95, close=105.5, volume=1000)))

    def test_valid_marker_row_has_no_reason(self):
        self.assertIsNone(guard.invalid_reason(dict(open=0, high=0, low=0, close=100, volume=0)))


# ─────────────────────────────────────────────────────────────────────────────
# 5. main 경로 — 게이트 통과분만 삽입 / 거부는 큐로 / dry-run 무기록
# ─────────────────────────────────────────────────────────────────────────────
class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """connect_primary_db 대역. SELECT 는 needle 로 응답하고 쓰기는 기록만 한다."""

    def __init__(self, audit=(), calendar=(), ca=(), disclosures=(), present=(), anchors=None):
        self.rows_for = [
            ("FROM price_jump_audit", list(audit)),
            ("FROM price_trading_calendar", [(d,) for d in calendar]),
            ("FROM corporate_action_events", list(ca)),
            ("FROM dart_disclosures", list(disclosures)),
            ("AND date>?", [(d,) for d in present]),
        ]
        self.anchors = anchors or {}
        self.calls = []
        self.committed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "AND date=?" in sql:
            value = self.anchors.get((params[0], params[1]))
            return _FakeResult([(value,)] if value is not None else [])
        for needle, rows in self.rows_for:
            if needle in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def executemany(self, sql, seq):
        self.calls.append((sql, list(seq)))
        return _FakeResult([])

    def commit(self):
        self.committed = True

    def close(self):
        pass


WRITE_MARKERS = ("INSERT INTO", "UPDATE ", "DELETE FROM", "set_config(")


def _writes(conn):
    return [(sql, p) for sql, p in conn.calls if any(mk in sql for mk in WRITE_MARKERS)]


def _inserted(conn):
    return [p for sql, p in conn.calls if "INSERT INTO price_history(" in sql][0] if _writes(conn) else []


CAL = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
AUDIT = (("005930", "2026-01-08", "2026-01-02"),)


def marcap_frame(rows):
    base = dict(Code="005930", Date="2026-01-05", Open=100, High=105, Low=99, Close=102, Volume=1000)
    out = []
    for r in rows:
        row = dict(base); row.update(r); out.append(row)
    return pd.DataFrame(out)


def marcap_days(overrides=None):
    """이벤트 내부일(01-05..01-07) 전량을 marcap 이 공급하는 프레임. 미지정일은 정지 마커(close=100)."""
    overrides = overrides or {}
    out = []
    for d in ("2026-01-05", "2026-01-06", "2026-01-07"):
        row = dict(Code="005930", Date=d, Open=0, High=0, Low=0, Close=100, Volume=0)
        row.update(overrides.get(d, {}))
        out.append(row)
    return pd.DataFrame(out)


class MainGateContract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate_queue_"))
        self.queue = self.tmp / "queue.csv"

    def _run(self, apply, marcap, conn):
        reads = {"n": 0}

        def fake_read(*a, **kw):
            # main() 는 연도별 parquet 을 concat 한다 — 테스트 프레임은 한 번만 돌려준다(중복 방지)
            reads["n"] += 1
            return marcap if reads["n"] == 1 else marcap.iloc[0:0]

        with patch.object(guard, "connect_primary_db", lambda **kw: conn), \
             patch.object(guard, "ensure_year", lambda y: "dummy"), \
             patch("pandas.read_parquet", fake_read):
            return guard.main(apply=apply, queue_path=self.queue)

    def _conn(self, **kw):
        anchors = {("005930", "2026-01-02"): 100.0, ("005930", "2026-01-08"): 105.0}
        anchors.update(kw.pop("anchors", {}))
        return _FakeConn(audit=AUDIT, calendar=CAL, anchors=anchors, **kw)

    def _read_queue(self):
        self.assertTrue(self.queue.exists(), "게이트는 항상 큐 CSV 를 남겨야 한다")
        return pd.read_csv(self.queue, encoding="utf-8-sig", dtype={"code": str})

    def test_clean_event_inserts_only_gate_verified_rows(self):
        marcap = marcap_days({"2026-01-07": dict(Open=101, High=104, Low=100, Close=103, Volume=5000)})
        conn = self._conn()
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["events_fully_covered"], 1)
        self.assertEqual(summary["rows_insertable"], 3)
        self.assertEqual(summary["insertable_suspension_markers"], 2)
        self.assertEqual(summary["insertable_trading_rows"], 1)
        self.assertEqual(summary["queue_rows"], 0)
        self.assertEqual(summary["band_ok"], 1)
        self.assertTrue(conn.committed)
        self.assertEqual([(p[0], p[1]) for p in _inserted(conn)],
                         [("005930", d) for d in ("2026-01-05", "2026-01-06", "2026-01-07")])

    def test_inserted_values_are_the_source_values_verbatim(self):
        marcap = marcap_days({"2026-01-07": dict(Open=101, High=104, Low=100, Close=103, Volume=5000)})
        conn = self._conn()
        self._run(apply=True, marcap=marcap, conn=conn)
        by_date = {p[1]: p for p in _inserted(conn)}       # (code, date, open, high, low, close, volume, created_at)
        self.assertEqual(by_date["2026-01-07"][:7], ("005930", "2026-01-07", 101, 104, 100, 103, 5000))
        self.assertEqual(by_date["2026-01-05"][:7], ("005930", "2026-01-05", 0, 0, 0, 100, 0))

    def test_guard_flag_is_set_before_the_insert(self):
        conn = self._conn()
        self._run(apply=True, marcap=marcap_days(), conn=conn)
        sqls = [s for s, _ in conn.calls]
        self.assertLess(next(i for i, s in enumerate(sqls) if "set_config('app.price_basis_checked'" in s),
                        next(i for i, s in enumerate(sqls) if "INSERT INTO price_history(" in s))

    def test_blocked_event_is_never_inserted_and_lands_in_the_queue(self):
        marcap = marcap_days({"2026-01-06": dict(Open=10, High=10, Low=10, Close=10, Volume=700)})
        conn = self._conn()
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["rows_insertable"], 0)
        self.assertEqual(summary["events_blocked_for_review"], 1)
        self.assertEqual(_writes(conn), [], "보류된 이벤트는 한 행도 쓰면 안 된다")
        self.assertFalse(conn.committed)
        queue = self._read_queue()
        self.assertEqual(len(queue), 3)
        self.assertEqual(set(queue.event_blocked), {1})
        self.assertIn("rejected_unverified_basis", set(queue.decision))
        self.assertEqual(set(queue.code), {"005930"})

    def test_corporate_action_evidence_keeps_the_event_clean(self):
        marcap = marcap_days({"2026-01-06": dict(Open=27, High=27, Low=27, Close=27, Volume=900),
                              "2026-01-07": dict(Close=27)})
        conn = self._conn(ca=(("005930", "2026-01-06", "bonus_issue", "factor_confirmed", 0.25,
                               "주요사항보고서(무상증자결정)"),))
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["corporate_action_confirmed"], 1)
        self.assertEqual(summary["rows_insertable"], 3)
        self.assertEqual(summary["events_blocked_for_review"], 0)
        self.assertEqual(summary["queue_rows"], 0)

    def test_already_present_row_is_not_re_inserted_but_refusals_are_reported(self):
        """사전(게이트 이전) 실행이 이미 넣은 행: 재삽입은 없지만, 게이트가 거부하면 롤백 후보로 큐에 남는다."""
        marcap = marcap_days({"2026-01-06": dict(Open=10, High=10, Low=10, Close=10, Volume=700)})
        conn = self._conn(present=["2026-01-05", "2026-01-06"])
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["rows_insertable"], 0)
        self.assertEqual(summary["rejected_already_present"], 1)
        self.assertEqual(_writes(conn), [])
        queue = self._read_queue()
        self.assertEqual(sorted(set(queue.already_present)), [0, 1])
        self.assertIn("rejected_unverified_basis", set(queue.decision))

    def test_invalid_provenance_rows_are_queued_and_never_inserted(self):
        marcap = marcap_days({"2026-01-06": dict(Open=100.5, High=105.5, Low=99.5, Close=102.5, Volume=10)})
        conn = self._conn()
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["invalid_provenance_rows"], 1)
        self.assertEqual(summary["rows_insertable"], 2)
        queue = self._read_queue()
        self.assertEqual(list(queue.decision), ["rejected_invalid_row"])
        self.assertIn("adjusted_basis", queue.evidence.iloc[0])

    def test_dry_run_writes_nothing_to_the_db_but_still_emits_the_queue(self):
        marcap = marcap_days({"2026-01-06": dict(Open=10, High=10, Low=10, Close=10, Volume=700)})
        conn = self._conn()
        summary = self._run(apply=False, marcap=marcap, conn=conn)
        self.assertEqual(_writes(conn), [], "dry-run 은 DB 에 한 행도 쓰면 안 된다")
        self.assertFalse(conn.committed)
        self.assertEqual(summary["queue_rows"], 3)
        self.assertTrue(self.queue.exists())

    def test_events_marcap_does_not_cover_are_left_alone(self):
        marcap = marcap_frame([dict(Date="2026-01-05", Open=0, High=0, Low=0, Close=100, Volume=0)])
        conn = self._conn()
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["events_fully_covered"], 0)
        self.assertEqual(summary["rows_insertable"], 0)
        self.assertEqual(_writes(conn), [])

    def test_missing_anchors_block_instead_of_guessing(self):
        """앵커 조회가 실패하면 실거래행은 거부되고 이벤트가 보류된다 — 조용히 통과시키지 않는다."""
        marcap = marcap_days({"2026-01-05": dict(Open=101, High=104, Low=100, Close=103, Volume=5000)})
        conn = _FakeConn(audit=AUDIT, calendar=CAL, anchors={})
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["rejected_unverified_basis"], 1)
        self.assertEqual(summary["events_blocked_for_review"], 1)
        self.assertEqual(_writes(conn), [])

    def test_evidence_lookup_failure_is_fail_closed(self):
        """기업행위 테이블을 못 읽으면 증빙이 없다 → 밴드 밖 행은 거부된다."""
        class _Broken(_FakeConn):
            def execute(self, sql, params=None):
                if "FROM corporate_action_events" in sql or "FROM dart_disclosures" in sql:
                    self.calls.append((sql, params))
                    raise RuntimeError("relation does not exist")
                return super().execute(sql, params)

        anchors = {("005930", "2026-01-02"): 100.0, ("005930", "2026-01-08"): 105.0}
        marcap = marcap_days({"2026-01-05": dict(Open=27, High=27, Low=27, Close=27, Volume=900)})
        conn = _Broken(audit=AUDIT, calendar=CAL, anchors=anchors)
        summary = self._run(apply=True, marcap=marcap, conn=conn)
        self.assertEqual(summary["rejected_unverified_basis"], 1)
        self.assertEqual(summary["rows_insertable"], 0)

    def test_backup_row_carries_the_new_values_and_the_gate_reason(self):
        marcap = marcap_days({"2026-01-07": dict(Open=101, High=104, Low=100, Close=103, Volume=5000)})
        conn = self._conn()
        self._run(apply=True, marcap=marcap, conn=conn)
        sql, params = [c for c in conn.calls if "price_history_fix_backup" in c[0]][0]
        self.assertIn("VALUES(?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?)", sql)
        self.assertEqual(len(params), 3)
        self.assertTrue(params[0][0].startswith("suspension_gap_fill_"))
        third = {p[2]: p for p in params}["2026-01-07"]
        self.assertEqual(third[3:8], (101, 104, 100, 103, 5000))
        self.assertIn("gate-verified", third[8])
        log_sql, log_params = [c for c in conn.calls if "data_fix_log" in c[0]][0]
        self.assertEqual(log_params[1], "price_history")
        self.assertEqual(log_params[3], 3)

    def test_every_audit_coverage_gap_event_is_evaluated_even_when_already_filled(self):
        """이미 채워진 이벤트도 재평가한다 — 아니면 게이트가 무엇을 거부하는지 사후에 알 수 없다."""
        conn = self._conn(present=["2026-01-05", "2026-01-06", "2026-01-07"])
        summary = self._run(apply=True, marcap=marcap_days(), conn=conn)
        self.assertEqual(summary["suspension_markers"], 3)
        self.assertEqual(summary["rows_insertable"], 0)
        self.assertEqual(summary["queue_rows"], 0)


if __name__ == "__main__":
    unittest.main()
