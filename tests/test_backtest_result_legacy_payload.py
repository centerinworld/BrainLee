"""Regression coverage for legacy backtest result payloads."""

from routes import backtest


class _FakeConnection:
    def __init__(self, row):
        self._row = row
        self.closed = False

    def execute(self, _sql, _params):
        return self

    def fetchone(self):
        return self._row

    def close(self):
        self.closed = True


def _get_result(monkeypatch, row):
    conn = _FakeConnection(row)
    monkeypatch.setattr(backtest, "_db", lambda: conn)
    result = backtest.get_backtest_result("legacy-run")
    assert conn.closed is True
    return result


def test_get_backtest_result_normalizes_legacy_array_payload(monkeypatch):
    trades = [{"date": "2026-09-23", "action": "buy"}]

    result = _get_result(monkeypatch, ("done", __import__("json").dumps(trades), "legacy"))

    assert result == {"trades": trades, "status": "done", "summary_text": "legacy"}


def test_get_backtest_result_preserves_object_payload(monkeypatch):
    payload = {"trades": [{"date": "2026-09-23", "action": "sell"}], "metric": 1.25}

    result = _get_result(monkeypatch, ("done", __import__("json").dumps(payload), "current"))

    assert result == {**payload, "status": "done", "summary_text": "current"}
