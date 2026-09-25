"""ETF 구성 수집의 원장 편입 계약 테스트 (2026-09-25, planner 승인 기본안 ②).

실측 배경
---------
`/api/dashboard/stats` 의 `etf` 계약은 `ETF_check/etf_check.db:etf_inclusion_daily` 를 보고
2026-09-18 이후 `stale`(lag 3)였다. 그런데 `data/collection_health.db` 의
`collection_job_runs` 에는 **ETF 관련 잡이 0건**(`job_name LIKE '%etf%'` → 0행)이었다 —
ETF 수집은 launchd(`com.stock-dashboard.etf-daily` 21:15 · `etf-direct-publish` 22:05, 평일)와
cron retry 스크립트로 돌기 때문이다. 즉 계약이 "잡 실행"을 볼 수 없어, 파이프라인이 조용히
멈춰도 원장에는 아무 흔적이 없고 테이블 워터마크만이 유일한 감지기였다.

수정(2026-09-25)
----------------
1. `collection_health.JOB_DATASET_KEYS["ETF수집점검"] = ("etf",)` — 점검 잡을 계약에 묶는다
   (`_run_job_safe` 가 실행 후 계약을 평가해 details·상태에 남긴다).
2. `scheduler._loop_etf_freshness` — 매일 21:45(launchd 21:15 뒤) 계약을 확인한다.
3. `_job_etf_freshness_check` — 비정상이고 **오늘 파이프라인이 아직 안 돌았으면** 한 번 실행,
   그래도 비정상이면 예외로 올려 원장에 `failed` 로 남긴다. 오늘 이미 돌았으면 재실행하지 않고
   로그의 실패 스테이지를 근거로 보고한다(수십 분짜리 파이프라인 중복 실행 방지).

이 파일은 배선·로그 파서·분기 3종을 고정한다.
"""

from __future__ import annotations

import datetime
import subprocess
import threading
import types

import pytest

import collection_health
import scheduler


class TestContractWiring:
    def test_job_is_mapped_to_etf_contract(self):
        assert collection_health.JOB_DATASET_KEYS["ETF수집점검"] == ("etf",)

    def test_etf_contract_exists_and_reads_etf_check_db(self):
        contract = collection_health.CONTRACT_BY_KEY["etf"]
        assert contract.table == "etf_inclusion_daily"
        assert contract.source_date_col == "trade_date"
        assert str(contract.db_path).endswith("ETF_check/etf_check.db")


TODAY = datetime.date.today().isoformat()


def _write_log(tmp_path, lines):
    p = tmp_path / "daily_pipeline.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class TestPipelineLogParser:
    def test_detects_run_and_failed_stages(self, tmp_path):
        log = _write_log(tmp_path, [
            f"[{TODAY} 21:15:01] START base_date=20260924",
            f"[{TODAY} 21:15:02] STAGE_START full_pdf",
            f"[{TODAY} 21:30:00] STAGE_END full_pdf exit=1",
            f"[{TODAY} 21:40:00] STAGE_END scale exit=0",
        ])
        ran, failed = scheduler.CollectionScheduler._etf_pipeline_run_today(log, TODAY)
        assert ran is True
        assert failed == ["full_pdf"]

    def test_previous_day_lines_do_not_count(self, tmp_path):
        log = _write_log(tmp_path, [
            "[2026-09-24 21:15:01] START base_date=20260923",
            "[2026-09-24 21:16:00] STAGE_END full_pdf exit=7",
        ])
        assert scheduler.CollectionScheduler._etf_pipeline_run_today(log, TODAY) == (False, [])

    def test_missing_file_is_not_a_run(self, tmp_path):
        assert scheduler.CollectionScheduler._etf_pipeline_run_today(tmp_path / "nope.log", TODAY) == (False, [])

    def test_malformed_exit_code_does_not_crash(self, tmp_path):
        log = _write_log(tmp_path, [
            f"[{TODAY} 21:15:01] START base_date=20260924",
            f"[{TODAY} 21:16:00] STAGE_END full_pdf exit=unknown",
        ])
        ran, failed = scheduler.CollectionScheduler._etf_pipeline_run_today(log, TODAY)
        assert ran is True and failed == []


def _patch_state(monkeypatch, state):
    monkeypatch.setattr(_JobStub, "_etf_contract_state", staticmethod(lambda: state))


def _patch_pipeline_today(monkeypatch, value):
    monkeypatch.setattr(_JobStub, "_etf_pipeline_run_today", staticmethod(lambda log_path, day: value))


STALE = {"key": "etf", "status": "stale", "lag": 3, "source_as_of": "2026-09-18", "expected_as_of": "2026-09-23"}
HEALTHY = {"key": "etf", "status": "healthy", "lag": 0, "source_as_of": "2026-09-23", "latest_coverage": 1357}


class _JobStub:
    """job 본문만 떼어내 돌리기 위한 스텁(계약/로그 판정은 각 테스트가 주입한다)."""

    _job = scheduler.CollectionScheduler._job_etf_freshness_check
    _etf_contract_state = staticmethod(dict)
    _etf_pipeline_run_today = staticmethod(lambda log_path, day: (False, []))


class TestFreshnessJob:
    def test_healthy_contract_does_not_run_pipeline(self, monkeypatch):
        _patch_state(monkeypatch, HEALTHY)
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or pytest.fail("실행되면 안 됨"))
        _JobStub()._job()
        assert calls == []

    def test_already_ran_today_raises_with_stage_evidence(self, monkeypatch):
        _patch_state(monkeypatch, STALE)
        _patch_pipeline_today(monkeypatch, (True, ["full_pdf"]))
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("중복 실행 금지"))
        with pytest.raises(RuntimeError) as exc:
            _JobStub()._job()
        message = str(exc.value)
        assert "이미 실행됨" in message and "full_pdf" in message and "lag=3" in message

    def test_not_run_today_triggers_one_retry_then_succeeds(self, monkeypatch):
        _patch_state(monkeypatch, STALE)
        _patch_pipeline_today(monkeypatch, (False, []))
        calls = []

        def fake_run(*a, **k):
            calls.append(a)
            _patch_state(monkeypatch, HEALTHY)
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _JobStub()._job()
        assert len(calls) == 1 and calls[0][0][0] == "/bin/zsh"
        assert str(calls[0][0][1]).endswith("scripts/run_etf_daily_pipeline.sh")

    def test_not_run_today_and_still_stale_raises(self, monkeypatch):
        _patch_state(monkeypatch, STALE)
        _patch_pipeline_today(monkeypatch, (False, []))
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
        )
        with pytest.raises(RuntimeError) as exc:
            _JobStub()._job()
        assert "재시도 후에도 비정상" in str(exc.value)


class TestScheduleOrder:
    def test_runs_once_daily_at_2145(self, monkeypatch):
        runs: list[str] = []
        monkeypatch.setattr(scheduler, "_run_job_safe", lambda name, fn: runs.append(name) or True)

        class Stub:
            _loop = scheduler.CollectionScheduler._loop_etf_freshness
            _job_etf_freshness_check = staticmethod(lambda: None)

            def __init__(self):
                self.calls: list[tuple] = []
                self._stop_event = threading.Event()

            def _wait_secs(self, secs):
                self.calls.append(("secs", secs))

            def _wait_until(self, hour, minute, skip_weekend=True):
                self.calls.append(("until", hour, minute))
                self._stop_event.set()

        stub = Stub()
        stub._loop()
        assert stub.calls == [("secs", 76), ("until", 21, 45)]
        assert runs == ["ETF수집점검"]

    def test_job_is_registered_in_the_scheduler_jobs_list(self):
        source = (scheduler.__file__ and open(scheduler.__file__, encoding="utf-8").read()) or ""
        assert '("ETF수집점검",' in source and "self._loop_etf_freshness" in source

    def test_job_is_not_a_db_write_job(self):
        # 파이프라인을 3회 재시도하는 사고를 막기 위해 의도적으로 제외한다.
        assert "ETF수집점검" not in scheduler._DB_WRITE_JOBS
