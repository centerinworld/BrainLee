"""P0 — 전략 연구 정적 요약 fail-closed 검증 계약 (2026-10-11 신설).

배경
----
`GET /api/backtest/strategy-research/summary` 는 `research_outputs/strategy_research_summary.json`
(정적 파일, 2026-10-04 생성) 을 그대로 내보내면서 `dataset.snapshot_table` 을
존재하지 않는 `strategy_feature_snapshot_stage` 로 명시한다. 라이브 PG 에는 canonical
`strategy_feature_snapshot` 만 있고 `backtest_run_specs` 도 6,947행 보유한다.

즉 화면의 고수익 테스트 수치(evaluation.ml_top20.avg_forward_ret_12m 등)는
정적 JSON 의 자기 주장이며, 라이브 canonical·run spec·PIT 증거와의 대조가 API 응답에
한 줄도 없었다. 이 테스트는 아래 수용기준을 고정한다.

수용기준 (Planner 확정, 2026-10-11)
-----------------------------------
1. canonical 은 `strategy_feature_snapshot` 단일 고정. `strategy_feature_snapshot_stage`
   를 비롯한 형제 테이블(`_pit_v2`, `_legacy_*`, `_rebuild_*`)은 참조 금지.
2. API 는 정적 요약의 생성시각·snapshot max date·row count·distinct stock count 를
   라이브 canonical 및 `backtest_run_specs` 와 대조한다.
3. 하나라도 불일치/부재/PIT 증명 불가면 `strategy_research_status="not_usable"` 와
   정확한 사유를 반환하고 순위·수익률을 매수/승격 근거로 노출하지 않는다.

이 테스트는 라이브 DB 를 열지 않는다 — canonical 통계와 run-spec 조회 결과를 인자로
주입하는 순수 함수만 검증한다(DB 쓰기 0, 서버 호출 0).
"""

from __future__ import annotations

import unittest
from unittest import mock

import backtest as _backtest

# The route initializes its database on import in production.  This route-payload
# seam is pure, so suppress that startup side effect for this no-DB regression.
with mock.patch.object(_backtest, "init_backtest_db", return_value=None):
    from routes.backtest import _assemble_strategy_research_summary_payload

from strategy_research_guard import (
    CANONICAL_SNAPSHOT_TABLE,
    STATUS_NOT_USABLE,
    STATUS_USABLE,
    apply_strategy_research_verdict,
    evaluate_strategy_research,
)

CANONICAL = CANONICAL_SNAPSHOT_TABLE


def _static_payload(**overrides) -> dict:
    """라이브 정적 파일(`research_outputs/strategy_research_summary.json`)의 실측 골격."""
    dataset = {
        "snapshot_table": "strategy_feature_snapshot_stage",
        "point_in_time_universe": True,
        "snapshot_rows": 193093,
        "snapshot_months": 82,
        "stocks_covered": 2708,
        "label_3x_6m_rows": 175548,
        "label_3x_12m_rows": 160437,
        "train_rows_12m": 123159,
        "test_rows_12m": 37278,
    }
    dataset.update(overrides.pop("dataset", {}))
    payload = {
        "generated_at": "2026-10-04T13:11:32Z",
        "latest_snapshot_date": "2026-10-02",
        "dataset": dataset,
        "evaluation": {
            "target": "12개월 내 3배(최대수익률 +200% 이상)",
            "train_end_date": "2024-06-30",
            "ml_top10": {"months": 15, "precision": 0.14, "avg_forward_ret_12m": 0.9393},
            "ml_top20": {"months": 15, "precision": 0.1333, "avg_forward_ret_12m": 0.8855},
        },
        "current_rankings": {
            "ml_top20": [{"stock_code": "065170", "stock_name": "비엘팜텍"}],
            "heuristic_top20": [{"stock_code": "009780", "stock_name": "엠에스씨"}],
        },
    }
    payload.update(overrides)
    return payload


def _canonical_stats(**overrides) -> dict:
    """라이브 PG 실측값(2026-10-11): 193,093행 · 2020-01-31~2026-10-02 · 2,708종목."""
    stats = {
        "canonical_table": CANONICAL,
        "canonical_table_exists": True,
        "declared_table": "strategy_feature_snapshot_stage",
        "declared_table_exists": False,
        "rows": 193093,
        "min_date": "2020-01-31",
        "max_date": "2026-10-02",
        "stock_count": 2708,
        "month_count": 82,
        "label_3x_6m_rows": 175548,
        "label_3x_12m_rows": 160437,
        "train_rows_12m": 123159,
        "test_rows_12m": 37278,
        "max_label_12m_date": "2025-09-30",
        "immature_label_12m_rows": 0,
        "error": None,
    }
    stats.update(overrides)
    return stats


def _static_meta(**overrides) -> dict:
    meta = {
        "path": "/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/"
                "strategy_research_summary.json",
        "exists": True,
        "parseable": True,
        "sha256": "5e0896ed" + "0" * 56,
        "mtime": "2026-10-04T22:11:00+09:00",
    }
    meta.update(overrides)
    return meta


def _run_spec(required: bool = False, run_hash=None, found: bool = False, **overrides) -> dict:
    spec = {
        "required": required,
        "run_hash": run_hash,
        "found": found,
        "table_rows": 6947,
        "error": None,
    }
    spec.update(overrides)
    return spec


_UNSET = object()


def _verdict(payload=_UNSET, stats=None, meta=None, spec=None) -> dict:
    return evaluate_strategy_research(
        _static_payload() if payload is _UNSET else payload,
        stats if stats is not None else _canonical_stats(),
        meta if meta is not None else _static_meta(),
        spec if spec is not None else _run_spec(),
    )


class LiveShapeRegressionTests(unittest.TestCase):
    """라이브 실측 형상: 수치는 canonical 과 1:1 일치하지만 이름표(_stage)와 run-spec 연결이 없다."""

    def test_live_shape_is_not_usable(self):
        v = _verdict()
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_live_shape_reports_dataset_table_missing(self):
        v = _verdict()
        ev = v["evidence"]
        self.assertTrue(ev["dataset_table_missing"])
        self.assertTrue(ev["dataset_table_not_canonical"])
        self.assertIn("dataset_table_missing", v["reasons"])
        self.assertIn("dataset_table_not_canonical", v["reasons"])

    def test_live_shape_counts_match_is_true(self):
        """수치는 canonical 과 일치한다 — 사유를 '수치 불일치'와 뭉개면 안 된다."""
        v = _verdict()
        self.assertTrue(v["evidence"]["counts_match"])
        self.assertEqual(v["evidence"]["count_mismatches"], {})
        self.assertNotIn("counts_mismatch", v["reasons"])

    def test_live_shape_run_spec_proof_missing(self):
        v = _verdict()
        self.assertIn("run_spec_proof_missing", v["reasons"])

    def test_live_shape_pit_is_verified_not_failed(self):
        """12개월 라벨은 2025-09-30 에서 멈춘다 — look-ahead 는 이 경로로는 없다."""
        v = _verdict()
        self.assertTrue(v["evidence"]["pit"]["verified"])
        self.assertNotIn("pit_proof_failed", v["reasons"])


class TableIdentityTests(unittest.TestCase):
    def test_declared_missing_key_is_flagged(self):
        payload = _static_payload()
        payload["dataset"].pop("snapshot_table")
        v = _verdict(payload=payload)
        self.assertIn("dataset_table_declared_missing", v["reasons"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_sibling_table_is_rejected_as_not_canonical(self):
        """`_pit_v2` 가 실재하더라도 canonical 이 아니면 통과하지 않는다."""
        payload = _static_payload()
        payload["dataset"]["snapshot_table"] = "strategy_feature_snapshot_pit_v2"
        v = _verdict(payload=payload, stats=_canonical_stats(declared_table_exists=True))
        self.assertIn("dataset_table_not_canonical", v["reasons"])
        self.assertFalse(v["evidence"]["dataset_table_missing"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_canonical_table_absent_in_db_is_flagged(self):
        v = _verdict(stats=_canonical_stats(canonical_table_exists=False))
        self.assertIn("canonical_table_missing", v["reasons"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)


class CountComparisonTests(unittest.TestCase):
    def test_each_counted_metric_is_compared(self):
        for key, db_key in (
            ("snapshot_rows", "rows"),
            ("snapshot_months", "month_count"),
            ("stocks_covered", "stock_count"),
            ("label_3x_6m_rows", "label_3x_6m_rows"),
            ("label_3x_12m_rows", "label_3x_12m_rows"),
            ("train_rows_12m", "train_rows_12m"),
            ("test_rows_12m", "test_rows_12m"),
        ):
            with self.subTest(metric=key):
                v = _verdict(stats=_canonical_stats(**{db_key: 1}))
                self.assertFalse(v["evidence"]["counts_match"])
                self.assertIn("counts_mismatch", v["reasons"])
                self.assertIn(key, v["evidence"]["count_mismatches"])
                self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_count_mismatch_reports_both_sides(self):
        v = _verdict(stats=_canonical_stats(rows=193092))
        mm = v["evidence"]["count_mismatches"]["snapshot_rows"]
        self.assertEqual(mm["static"], 193093)
        self.assertEqual(mm["canonical"], 193092)

    def test_snapshot_date_mismatch_is_its_own_reason(self):
        v = _verdict(stats=_canonical_stats(max_date="2026-09-30"))
        self.assertIn("snapshot_date_mismatch", v["reasons"])
        self.assertFalse(v["evidence"]["snapshot_date_match"])

    def test_snapshot_date_unparsable_when_neither_side_parses(self):
        """canonical 기준일도 정적 주장도 파싱 불가면 조용히 통과시키지 않는다(fail-closed)."""
        payload = _static_payload(latest_snapshot_date="어제")
        v = _verdict(payload=payload, stats=_canonical_stats(max_date=None))
        self.assertIn("snapshot_date_unparsable", v["reasons"])
        self.assertIsNone(v["evidence"]["generated_at_after_snapshot"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_train_end_date_drives_the_train_test_split_comparison(self):
        """정적 파일이 주장한 경계로 canonical 을 다시 계산하면 split 값이 달라진다 → 불일치."""
        payload = _static_payload()
        payload["evaluation"]["train_end_date"] = "2024-03-31"
        v = _verdict(payload=payload, stats=_canonical_stats(train_rows_12m=111111,
                                                            test_rows_12m=49326))
        self.assertIn("counts_mismatch", v["reasons"])
        self.assertIn("train_rows_12m", v["evidence"]["count_mismatches"])

    def test_missing_train_end_date_is_its_own_blocking_reason(self):
        payload = _static_payload()
        payload["evaluation"].pop("train_end_date")
        v = _verdict(payload=payload)
        self.assertIn("train_end_date_missing", v["reasons"])
        self.assertIsNone(v["evidence"]["counts_match"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)


class PitProofTests(unittest.TestCase):
    def test_immature_labels_block_use(self):
        v = _verdict(stats=_canonical_stats(immature_label_12m_rows=17))
        self.assertIn("pit_proof_failed", v["reasons"])
        self.assertFalse(v["evidence"]["pit"]["verified"])

    def test_label_beyond_maturity_boundary_blocks_use(self):
        """max(snapshot)=2026-10-02 이면 12개월 라벨은 2025-10-02 이전이어야 한다."""
        v = _verdict(stats=_canonical_stats(max_label_12m_date="2026-01-31"))
        self.assertIn("pit_proof_failed", v["reasons"])

    def test_boundary_equality_is_accepted(self):
        v = _verdict(stats=_canonical_stats(max_label_12m_date="2025-10-02"))
        self.assertTrue(v["evidence"]["pit"]["verified"])

    def test_static_pit_claim_without_rows_is_rejected(self):
        v = _verdict(stats=_canonical_stats(max_label_12m_date=None, label_3x_12m_rows=0))
        self.assertIn("pit_proof_failed", v["reasons"])

    def test_missing_point_in_time_availability_blocks_usable_fixture(self):
        """A mature label alone cannot prove that the source universe was PIT-available."""
        payload = _static_payload()
        payload["dataset"].update({
            "snapshot_table": CANONICAL,
            "point_in_time_universe": False,
        })
        payload["run_spec"] = {"run_hash": "abc123"}
        v = _verdict(
            payload=payload,
            stats=_canonical_stats(declared_table=CANONICAL, declared_table_exists=True),
            spec=_run_spec(required=True, run_hash="abc123", found=True),
        )
        self.assertEqual(v["status"], STATUS_NOT_USABLE)
        self.assertIn("pit_availability_unproven", v["reasons"])


class RunSpecProofTests(unittest.TestCase):
    def test_missing_run_spec_link_blocks_even_when_counts_match(self):
        v = _verdict(spec=_run_spec(required=False, run_hash=None, found=False))
        self.assertTrue(v["evidence"]["counts_match"])
        self.assertIn("run_spec_proof_missing", v["reasons"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_run_hash_not_present_in_registry_blocks(self):
        v = _verdict(spec=_run_spec(required=True, run_hash="deadbeef", found=False))
        self.assertIn("run_spec_not_found", v["reasons"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_registry_lookup_error_blocks(self):
        v = _verdict(spec=_run_spec(required=True, run_hash="abc", found=False, error="boom"))
        self.assertIn("run_spec_unverifiable", v["reasons"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)


class GeneratedAtTests(unittest.TestCase):
    """수용기준 2 '생성시각 대조' — 존재 여부가 아니라 타임존 포함 파싱 + 기준일 순서를 본다."""

    def test_live_generated_at_passes_the_ordering_check(self):
        v = _verdict()
        self.assertTrue(v["evidence"]["generated_at_after_snapshot"])
        self.assertNotIn("generated_at_before_snapshot_date", v["reasons"])
        self.assertNotIn("generated_at_unparsable", v["reasons"])
        self.assertNotIn("generated_at_naive_timezone", v["reasons"])

    def test_utc_z_suffix_is_parsed_with_timezone(self):
        """Z 접미사는 UTC 로 해석돼 KST 로 환산된다(2026-10-04T13:11:32Z = KST 22:11:32)."""
        v = _verdict()
        self.assertEqual(v["evidence"]["generated_at_kst"], "2026-10-04T22:11:32+09:00")

    def test_explicit_offset_form_is_parsed(self):
        payload = _static_payload(generated_at="2026-10-04T22:11:32+09:00")
        v = _verdict(payload=payload)
        self.assertTrue(v["evidence"]["generated_at_after_snapshot"])

    def test_backend_fix_1_regression_naive_timestamp_is_rejected(self):
        """수정 전 결함: 존재만 보면 naive 문자열도 통과했다."""
        payload = _static_payload(generated_at="2026-10-04T22:11:32")
        v = _verdict(payload=payload)
        self.assertIn("generated_at_naive_timezone", v["reasons"])
        self.assertIsNone(v["evidence"]["generated_at_after_snapshot"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_unparsable_timestamp_is_rejected(self):
        payload = _static_payload(generated_at="어제쯤")
        v = _verdict(payload=payload)
        self.assertIn("generated_at_unparsable", v["reasons"])
        self.assertNotIn("generated_at_naive_timezone", v["reasons"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_generated_before_snapshot_date_is_rejected(self):
        payload = _static_payload(generated_at="2026-10-01T00:00:00Z")
        v = _verdict(payload=payload)
        self.assertIn("generated_at_before_snapshot_date", v["reasons"])
        self.assertFalse(v["evidence"]["generated_at_after_snapshot"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_comparison_is_kst_aware_at_the_midnight_boundary(self):
        """UTC 로는 10-01 이지만 KST 로는 10-02 00:00 — 경계는 포함(>=)이다."""
        payload = _static_payload(generated_at="2026-10-01T15:00:00Z")
        v = _verdict(payload=payload)
        self.assertTrue(v["evidence"]["generated_at_after_snapshot"])
        self.assertNotIn("generated_at_before_snapshot_date", v["reasons"])

    def test_one_second_before_the_kst_boundary_is_rejected(self):
        payload = _static_payload(generated_at="2026-10-01T14:59:59Z")
        v = _verdict(payload=payload)
        self.assertIn("generated_at_before_snapshot_date", v["reasons"])

    def test_ordering_uses_canonical_max_date_when_it_is_newer(self):
        """정적 파일이 낡았지만 자기 기준일만 주장하는 경우를 canonical 이 잡아낸다."""
        v = _verdict(stats=_canonical_stats(max_date="2026-10-10"))
        self.assertIn("generated_at_before_snapshot_date", v["reasons"])
        self.assertEqual(
            v["evidence"]["checks"][[c["name"] for c in v["evidence"]["checks"]]
                                    .index("generated_at_after_snapshot")]["detail"]["compared_against"],
            "2026-10-10",
        )

    def test_usable_path_requires_the_ordering_check_too(self):
        payload = _static_payload()
        payload["dataset"]["snapshot_table"] = CANONICAL
        payload["run_spec"] = {"run_hash": "abc123"}
        payload["generated_at"] = "2026-09-01T00:00:00Z"
        v = _verdict(
            payload=payload,
            stats=_canonical_stats(declared_table=CANONICAL, declared_table_exists=True),
            spec=_run_spec(required=True, run_hash="abc123", found=True),
        )
        self.assertEqual(v["status"], STATUS_NOT_USABLE)
        self.assertIn("generated_at_before_snapshot_date", v["reasons"])


class FailClosedTests(unittest.TestCase):
    def test_missing_static_summary(self):
        v = _verdict(payload=None, meta=_static_meta(exists=False, parseable=False))
        self.assertEqual(v["status"], STATUS_NOT_USABLE)
        self.assertIn("static_summary_missing", v["reasons"])
        self.assertIsNone(v["evidence"]["counts_match"])

    def test_unparsable_static_summary(self):
        v = _verdict(payload=None, meta=_static_meta(parseable=False))
        self.assertIn("static_summary_unparsable", v["reasons"])

    def test_generated_at_missing_blocks(self):
        payload = _static_payload(generated_at=None)
        v = _verdict(payload=payload)
        self.assertIn("generated_at_missing", v["reasons"])

    def test_canonical_read_error_blocks(self):
        v = _verdict(stats=_canonical_stats(error="undefined table"))
        self.assertIn("canonical_unreadable", v["reasons"])
        self.assertEqual(v["status"], STATUS_NOT_USABLE)

    def test_every_reason_is_a_machine_code(self):
        v = _verdict()
        for reason in v["reasons"]:
            self.assertRegex(reason, r"^[a-z0-9_]+$")

    def test_reasons_never_leak_check_names(self):
        """검사 이름과 사유 코드가 둘 다 들어가면 소비자가 사유를 셀 수 없다."""
        v = _verdict()
        for name in ("dataset_table_exists", "dataset_table_canonical", "dataset_table_declared",
                     "counts_consistent", "snapshot_date_consistent", "generated_at_present",
                     "generated_at_parsable", "generated_at_after_snapshot",
                     "run_spec_proof", "pit_proof"):
            self.assertNotIn(name, v["reasons"])
        self.assertEqual(
            sorted(v["reasons"]),
            sorted({"dataset_table_missing", "dataset_table_not_canonical",
                    "run_spec_proof_missing"}),
        )


class UsablePathTests(unittest.TestCase):
    def _usable_verdict(self) -> dict:
        payload = _static_payload()
        payload["dataset"]["snapshot_table"] = CANONICAL
        payload["run_spec"] = {"run_hash": "abc123"}
        return _verdict(
            payload=payload,
            stats=_canonical_stats(declared_table=CANONICAL, declared_table_exists=True),
            spec=_run_spec(required=True, run_hash="abc123", found=True),
        )

    def test_all_checks_pass_yields_usable(self):
        v = self._usable_verdict()
        self.assertEqual(v["status"], STATUS_USABLE)
        self.assertEqual(v["reasons"], [])

    def test_usable_path_still_reports_evidence(self):
        v = self._usable_verdict()
        self.assertTrue(v["evidence"]["counts_match"])
        self.assertTrue(v["evidence"]["pit"]["verified"])
        self.assertFalse(v["evidence"]["dataset_table_missing"])
        self.assertEqual(v["evidence"]["run_spec"]["run_hash"], "abc123")

    def test_usable_verdict_keeps_rankings(self):
        v = self._usable_verdict()
        out = apply_strategy_research_verdict(_static_payload(), v)
        self.assertEqual(out["strategy_research_status"], STATUS_USABLE)
        self.assertIsNotNone(out["current_rankings"])
        self.assertIsNotNone(out["evaluation"])


class WithholdingTests(unittest.TestCase):
    def test_not_usable_omits_rankings_and_returns(self):
        """P0 contract: withheld ranking/return keys are absent, never null."""
        v = _verdict()
        out = apply_strategy_research_verdict(_static_payload(), v)
        self.assertEqual(out["strategy_research_status"], STATUS_NOT_USABLE)
        self.assertNotIn("current_rankings", out)
        self.assertNotIn("evaluation", out)

    def test_not_usable_keeps_provenance_for_audit(self):
        v = _verdict()
        out = apply_strategy_research_verdict(_static_payload(), v)
        self.assertEqual(out["latest_snapshot_date"], "2026-10-02")
        self.assertEqual(out["dataset"]["snapshot_rows"], 193093)
        self.assertIn("strategy_research_notice", out)

    def test_not_usable_exposes_machine_readable_reasons(self):
        v = _verdict()
        out = apply_strategy_research_verdict(_static_payload(), v)
        self.assertIn("dataset_table_missing", out["strategy_research_reasons"])
        self.assertTrue(out["strategy_research_evidence"]["counts_match"])

    def test_withholding_does_not_mutate_input(self):
        payload = _static_payload()
        before = payload["current_rankings"]
        apply_strategy_research_verdict(payload, _verdict())
        self.assertIs(payload["current_rankings"], before)

    def test_no_ranking_survives_anywhere_in_withheld_payload(self):
        import json

        out = apply_strategy_research_verdict(_static_payload(), _verdict())
        blob = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("비엘팜텍", blob)
        self.assertNotIn("엠에스씨", blob)
        self.assertNotIn("avg_forward_ret_12m", blob)


class RoutePayloadAssemblyTests(unittest.TestCase):
    def test_not_usable_omits_route_added_rankings_and_quality_outputs(self):
        """The route must not reintroduce returns after the guard has withheld them."""
        verdict = _verdict()
        out = _assemble_strategy_research_summary_payload(
            _static_payload(),
            verdict,
            quality_factor_validation={"avg_forward_ret_12m": 0.91},
            quality_overlay_sweep={"best_return_pct": 42.0},
            quality_overlay_monthly_backtest={"monthly_return_pct": 3.4},
            strategy_rankings={"top": [{"strategy": "v2", "avg_ret": 17.2}]},
        )

        self.assertEqual(out["strategy_research_status"], STATUS_NOT_USABLE)
        self.assertIn("dataset_table_missing", out["strategy_research_reasons"])
        self.assertIn("strategy_research_evidence", out)
        for key in (
            "current_rankings",
            "evaluation",
            "strategy_rankings",
            "quality_factor_validation",
            "quality_overlay_sweep",
            "quality_overlay_monthly_backtest",
        ):
            self.assertNotIn(key, out)

    def test_usable_keeps_route_added_rankings_and_quality_outputs(self):
        payload = _static_payload()
        payload["dataset"]["snapshot_table"] = CANONICAL
        payload["run_spec"] = {"run_hash": "abc123"}
        verdict = _verdict(
            payload=payload,
            stats=_canonical_stats(declared_table=CANONICAL, declared_table_exists=True),
            spec=_run_spec(required=True, run_hash="abc123", found=True),
        )
        out = _assemble_strategy_research_summary_payload(
            payload,
            verdict,
            quality_factor_validation={"validated": True},
            quality_overlay_sweep={"periods": 6},
            quality_overlay_monthly_backtest={"months": 12},
            strategy_rankings={"top": [{"strategy": "v2", "avg_ret": 17.2}]},
        )

        self.assertEqual(out["strategy_research_status"], STATUS_USABLE)
        self.assertEqual(out["strategy_rankings"]["top"][0]["avg_ret"], 17.2)
        self.assertEqual(out["quality_factor_validation"], {"validated": True})
        self.assertEqual(out["quality_overlay_sweep"], {"periods": 6})
        self.assertEqual(out["quality_overlay_monthly_backtest"], {"months": 12})


if __name__ == "__main__":
    unittest.main()
