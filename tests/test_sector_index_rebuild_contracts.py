"""섹터지수보완(파생 섹터지수) '조용한 하루 누락' 계약 테스트 (2026-09-25).

실측 배경
---------
`sector_index_daily` 의 MAX(date)가 price_history 보다 하루 뒤처졌다(09-23 종가 반영 후에도
MAX=2026-09-22). 원장은 정반대로 말했다:

    run_id 37215  2026-09-23T18:40:00  status=success  details.sector_index=healthy(lag 0)
    실제            sector_index_daily MAX = 2026-09-22   ← 하루 누락

`scripts/rebuild_sector_index_from_price_history.py` 는 `start = max(sector)+1`,
`end = _latest_full_price_date(min_price_coverage=2000)` 로 구간을 잡는다. 18:40 시점에는
당일 종가 커버리지가 2000종목에 못 미쳐 `end` 가 전일로 잡히고, `start > end` 가 되어
루프가 0회 돌고 `{'ok': True, inserted_or_updated: 0}` 를 돌려준다 — 예외도, 실패도 아니다.
실제 로그에 그 서명이 두 번 남아 있다:

    {'ok': True, 'start': '2026-09-12', 'end': '2026-09-11', 'inserted_or_updated': 0, 'skipped_dates': 0}

게다가 그 시각의 계약 평가는 `expected_as_of` 를 09-22 로 계산하므로 `lag: 0, healthy` 로
기록된다 → 누락은 **다음 날에야** lag 로 드러나고, 그때는 아무도 재실행하지 않는다.

수정(2026-09-25)
----------------
1. `_loop_sector_index_rebuild` 가 18:40 이후 **19:30 에 한 번 더** 실행한다(가격 파이프라인
   KIS일별수집 18:00 · 가격커버리지백필 19:15 뒤에 커버리지가 찬다).
2. `_job_sector_index_rebuild` 가 스크립트 stdout(dict repr)을 파싱해 `0행 + start>end` 를
   경고로 남긴다. 휴장일/이미 최신 상태와 구분되지 않으므로 실패로 승격하지는 않는다.

이 파일은 위 세 가지를 고정한다: 파싱 · 판정 · 스케줄 순서.
"""

from __future__ import annotations

import subprocess
import threading
import types

import pytest

import scheduler


# ---------------------------------------------------------------- 실제 로그에서 가져온 값
NOOP_INVERTED = {  # 09-12 · 09-19 18:40 실제 로그 라인
    "ok": True, "start": "2026-09-12", "end": "2026-09-11",
    "inserted_or_updated": 0, "skipped_dates": 0,
}
NOOP_INVERTED_TAIL = {  # 09-23 18:40 이 이런 형태였을 것으로 보는 값(계약은 healthy 로 기록)
    "ok": True, "start": "2026-09-23", "end": "2026-09-22",
    "inserted_or_updated": 0, "skipped_dates": 0,
}
FILLED = {  # 09-22 18:40 실제 로그 라인
    "ok": True, "start": "2026-09-22", "end": "2026-09-22",
    "inserted_or_updated": 17, "skipped_dates": 0,
}
HOLIDAY_STEADY = {  # 휴장일/이미 최신: 채울 구간 없음(정상)
    "ok": True, "start": "2026-09-25", "end": "2026-09-24",
    "inserted_or_updated": 0, "skipped_dates": 0,
}
NOTHING_TO_DO = {  # start == end 인데 0행 — 역전이 아니므로 판정 대상 아님
    "ok": True, "start": "2026-09-24", "end": "2026-09-24",
    "inserted_or_updated": 0, "skipped_dates": 0,
}


class TestParsePayload:
    def test_parses_logger_prefixed_stdout(self):
        stdout = "INFO:something: noise\n{'ok': True, 'start': '2026-09-22', 'end': '2026-09-22', 'inserted_or_updated': 17, 'skipped_dates': 0}\n"
        assert scheduler.CollectionScheduler._parse_sector_index_payload(stdout) == FILLED

    def test_parses_real_noop_line(self):
        raw = "{'ok': True, 'start': '2026-09-12', 'end': '2026-09-11', 'inserted_or_updated': 0, 'skipped_dates': 0}"
        assert scheduler.CollectionScheduler._parse_sector_index_payload(raw) == NOOP_INVERTED

    def test_takes_last_dict_when_several(self):
        stdout = "{'ok': False, 'start': 'x'}\n" + str(FILLED)
        assert scheduler.CollectionScheduler._parse_sector_index_payload(stdout) == FILLED

    @pytest.mark.parametrize("stdout", ["", "   ", None, "Traceback (most recent call last):\nValueError: boom", "{not a dict literal"])
    def test_unparsable_returns_none(self, stdout):
        assert scheduler.CollectionScheduler._parse_sector_index_payload(stdout) is None


class TestNoopReason:
    def test_inverted_range_with_zero_rows_is_reported(self):
        reason = scheduler.CollectionScheduler._sector_index_noop_reason(NOOP_INVERTED)
        assert reason is not None
        assert "2026-09-12" in reason and "2026-09-11" in reason and "0행" in reason

    def test_tail_day_miss_is_reported(self):
        assert scheduler.CollectionScheduler._sector_index_noop_reason(NOOP_INVERTED_TAIL) is not None

    def test_holiday_steady_state_is_reported_but_not_a_failure(self):
        # 휴장일에도 같은 서명이 나온다 → 실패로 승격하지 않는 이유(경고만).
        assert scheduler.CollectionScheduler._sector_index_noop_reason(HOLIDAY_STEADY) is not None

    def test_filled_run_is_silent(self):
        assert scheduler.CollectionScheduler._sector_index_noop_reason(FILLED) is None

    def test_equal_start_end_is_silent(self):
        assert scheduler.CollectionScheduler._sector_index_noop_reason(NOTHING_TO_DO) is None

    @pytest.mark.parametrize("payload", [{}, {"start": None, "end": None}, {"start": "a", "end": "b", "inserted_or_updated": "x"}])
    def test_malformed_payload_is_silent(self, payload):
        assert scheduler.CollectionScheduler._sector_index_noop_reason(payload) is None


class TestScheduleOrder:
    """18:40 실행 뒤 19:30 재시도가 같은 잡으로 이어지는지(순서 그대로) 고정한다."""

    def test_runs_twice_per_day(self, monkeypatch):
        runs: list[str] = []
        monkeypatch.setattr(scheduler, "_run_job_safe", lambda name, fn: runs.append(name) or True)

        class Stub:
            _loop = scheduler.CollectionScheduler._loop_sector_index_rebuild
            _job_sector_index_rebuild = staticmethod(lambda: None)

            def __init__(self):
                self.calls: list[tuple] = []
                self._stop_event = threading.Event()

            def _wait_secs(self, secs):
                self.calls.append(("secs", secs))

            def _wait_until(self, hour, minute, skip_weekend=True):
                self.calls.append(("until", hour, minute))
                if sum(1 for c in self.calls if c[0] == "until") >= 2:
                    self._stop_event.set()

        stub = Stub()
        stub._loop()
        assert stub.calls == [("secs", 72), ("until", 18, 40), ("until", 19, 30)]
        assert runs == ["섹터지수보완", "섹터지수보완"]


class _JobStub:
    _job = scheduler.CollectionScheduler._job_sector_index_rebuild
    _parse_sector_index_payload = staticmethod(scheduler.CollectionScheduler._parse_sector_index_payload)
    _sector_index_noop_reason = staticmethod(scheduler.CollectionScheduler._sector_index_noop_reason)


def _fake_run(returncode=0, stdout="", stderr=""):
    return lambda *a, **k: types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestJobLogging:
    def test_noop_day_logs_warning(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=str(NOOP_INVERTED_TAIL)))
        monkeypatch.setattr(scheduler.logger, "warning", lambda msg, *a, **k: warnings.append(str(msg)))
        _JobStub()._job()
        assert any("0행" in w and "19:30 재시도" in w for w in warnings), warnings

    def test_filled_day_logs_no_warning(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=str(FILLED)))
        monkeypatch.setattr(scheduler.logger, "warning", lambda msg, *a, **k: warnings.append(str(msg)))
        _JobStub()._job()
        assert warnings == []

    def test_unparsable_stdout_is_flagged_not_silent(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=""))
        monkeypatch.setattr(scheduler.logger, "warning", lambda msg, *a, **k: warnings.append(str(msg)))
        _JobStub()._job()
        assert any("파싱 실패" in w for w in warnings), warnings

    def test_nonzero_returncode_is_logged_as_error(self, monkeypatch):
        errors: list[str] = []
        monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1, stderr="boom"))
        monkeypatch.setattr(scheduler.logger, "error", lambda msg, *a, **k: errors.append(str(msg)))
        _JobStub()._job()  # 예외를 밖으로 던지지 않는다(원장 기록은 _run_job_safe 몫)
        assert any("섹터지수보완" in e for e in errors), errors
