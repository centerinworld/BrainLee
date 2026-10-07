"""§26-2 ①②③ (REVIEW_PLAN §26): 선택 순서·폐지 청산 확정 조건·전략별 최소 이력 행 수 강제 테스트.
운영 DB에는 아무것도 쓰지 않는다(_save_result·_record_run_spec 패치). PostgreSQL 접속 불가 시 건너뛴다."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402

try:
    from db_compat import connect_primary_db
    _c = connect_primary_db(timeout=10)
    _HAS_DB = bool(_c.execute("SELECT 1 FROM price_history LIMIT 1").fetchone())
    _c.close()
except Exception:  # pragma: no cover
    _HAS_DB = False


def _engine(sig, start, end, max_positions=10, selection_order=None, loader_patch=None, asof_mktcap=False):
    captured = {}
    ctx = [mock.patch.object(bc, "_save_result", lambda run_id, result: captured.update(result)),
           mock.patch.object(bc, "_record_run_spec", lambda *a, **k: None)]
    if loader_patch:
        orig = bc.load_adjusted_prices

        def wrapped(*a, **k):
            out = orig(*a, **k)
            loader_patch(out)
            return out
        ctx.append(mock.patch.object(bc, "load_adjusted_prices", wrapped))
    for c in ctx:
        c.start()
    try:
        bc._run_generic_backtest(
            "T", sig, start, end, per_stock=10_000_000, max_positions=max_positions, run_name="sel-test", run_id="sel_test_x",
            stop_loss=-0.99, take_profit=50.0, trail_stop=-0.99, mktcap_min=0, use_market_filter=False,
            asof_mktcap=asof_mktcap, adjusted_prices=True, selection_order=selection_order)
    finally:
        for c in ctx:
            c.stop()
    return captured["trades"], captured["summary"]


def _closes(codes, start, end):
    conn = connect_primary_db(timeout=60)
    warm = bc.datetime.strptime(start, "%Y-%m-%d") - bc.timedelta(days=450)
    ap = bc.load_adjusted_prices(conn, codes, warm.strftime("%Y-%m-%d"), end)
    conn.close()
    return ap


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestSelectionOrder(unittest.TestCase):
    def _two(self):
        # 000660(코드 앞), 005930: 같은 날 둘 다 신호, 자리는 1개 — 3개월 수익률이 높은 쪽을 고르는 날을 찾는다
        ap = _closes(["000660", "005930"], "2023-01-02", "2023-12-28")
        a, b = ap["000660"], ap["005930"]
        ia = {d: i for i, d in enumerate(a["dates"])}
        ib = {d: i for i, d in enumerate(b["dates"])}
        for d in a["dates"]:
            if "2023-03-01" <= d <= "2023-10-31" and d in ib and ia[d] >= 63 and ib[d] >= 63:
                ra = a["close"][ia[d]] / a["close"][ia[d] - 63]
                rb = b["close"][ib[d]] / b["close"][ib[d] - 63]
                if rb > ra * 1.05:
                    return d, a, b
        self.skipTest("수익률 순서가 코드 순서와 다른 날을 찾지 못함")

    def test_score_picks_higher_return_not_lower_code(self):
        d, a, b = self._two()
        targets = {"000660": a["close"][a["dates"].index(d)], "005930": b["close"][b["dates"].index(d)]}

        def sig(i, sim_start_i, dates, prices, volumes, frn, inst, fins):
            return i >= sim_start_i and dates[i] == d and any(abs(prices[i] - v) < 1e-9 for v in targets.values())
        t_code, _ = _engine(sig, "2023-01-02", "2023-12-28", max_positions=1, selection_order="code")
        t_score, _ = _engine(sig, "2023-01-02", "2023-12-28", max_positions=1, selection_order="score")
        first = lambda ts: sorted((t for t in ts if t["stock_code"] in targets), key=lambda t: t["entry_date"])[0]["stock_code"]
        self.assertEqual(first(t_code), "000660")      # 옛 동작: 종목코드 순 선착순
        self.assertEqual(first(t_score), "005930")     # 새 동작: 3개월 수익률 높은 종목

    def test_random_order_is_reproducible(self):
        d, a, b = self._two()
        targets = {a["close"][a["dates"].index(d)], b["close"][b["dates"].index(d)]}

        def sig(i, sim_start_i, dates, prices, volumes, frn, inst, fins):
            return i >= sim_start_i and dates[i] == d and any(abs(prices[i] - v) < 1e-9 for v in targets)
        r1, _ = _engine(sig, "2023-01-02", "2023-12-28", max_positions=1, selection_order="random:3")
        r2, _ = _engine(sig, "2023-01-02", "2023-12-28", max_positions=1, selection_order="random:3")
        pick = lambda ts: sorted((t["stock_code"], t["entry_date"]) for t in ts)
        self.assertEqual(pick(r1), pick(r2))


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestDelistingConfirmation(unittest.TestCase):
    @staticmethod
    def _sig_for(code):
        ap = _closes([code], "2015-03-16", "2015-06-30")[code]
        target = {d: c for d, c in zip(ap["dates"], ap["close"]) if d == "2015-05-12"}

        def sig(i, sim_start_i, dates, prices, volumes, frn, inst, fins):
            return i >= sim_start_i and dates[i] in target and abs(prices[i] - target[dates[i]]) < 1e-9
        return sig

    def test_confirmed_delisting_near_master_end_exits_as_delisted(self):
        trades, summary = _engine(self._sig_for("066350"), "2015-03-16", "2015-06-30", asof_mktcap=True)
        t = [x for x in trades if x["stock_code"] == "066350"]
        self.assertEqual(len(t), 1)
        self.assertTrue(t[0]["exit_reason"].startswith("상장폐지 청산"))
        self.assertNotEqual(t[0].get("evaluation"), "unevaluable_delisting")
        self.assertRegex(summary, r'"delisted_exits": 1')

    def test_data_gap_without_master_closure_is_unevaluable(self):
        # 마스터상 폐지가 아닌데 자료만 끊긴 경우를 흉내: 로더 결과의 master_closed_to를 None으로 — 청산하되 '평가 불가'로 따로 집계
        def patch(out):
            if "066350" in out:
                out["066350"]["master_closed_to"] = None
        trades, summary = _engine(self._sig_for("066350"), "2015-03-16", "2015-06-30", loader_patch=patch, asof_mktcap=True)
        t = [x for x in trades if x["stock_code"] == "066350"]
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].get("evaluation"), "unevaluable_delisting")
        self.assertRegex(summary, r'"unevaluable_delistings": 1')
        self.assertRegex(summary, r'"delisted_exits": 0')

    def test_master_end_far_from_last_price_is_unevaluable(self):
        def patch(out):
            if "066350" in out:
                out["066350"]["master_closed_to"] = "2015-09-30"      # 마지막 가격일(05-19)과 10일 넘게 어긋남
        trades, summary = _engine(self._sig_for("066350"), "2015-03-16", "2015-06-30", loader_patch=patch, asof_mktcap=True)
        t = [x for x in trades if x["stock_code"] == "066350"]
        self.assertEqual(t[0].get("evaluation"), "unevaluable_delisting")


class TestMinHistoryRows(unittest.TestCase):
    def test_table_covers_the_eight_strategies(self):
        for name in ("_is_buy_v1", "_is_buy_value", "_is_buy_v2", "_is_buy_v5", "_is_buy_v10", "_is_buy_v11",
                     "_is_buy_hidden_rev", "_is_buy_minervini"):
            self.assertIn(name, bc.MIN_HISTORY_ROWS)
        self.assertGreaterEqual(bc.MIN_HISTORY_ROWS["_is_buy_v5"], 120)
        self.assertGreaterEqual(bc.MIN_HISTORY_ROWS["_is_buy_v11"], 252)

    @unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
    def test_short_history_is_not_a_candidate(self):
        # 신규 상장 종목이 상장 후 100번째 행(i==100)에서 신호를 내는 경우: 기본 60행 기준이면 후보, MA120를 쓰는 전략(_is_buy_v5, 120행)이면 후보가 아니다.
        def make(name):
            def sig(i, sim_start_i, dates, prices, volumes, frn, inst, fins):
                return i == 100 and i >= sim_start_i
            sig.__name__ = name
            return sig
        base, _ = _engine(make("_is_buy_generic_test"), "2023-01-02", "2023-12-28", max_positions=50)
        long_, _ = _engine(make("_is_buy_v5"), "2023-01-02", "2023-12-28", max_positions=50)
        self.assertGreater(len(base), 0, "2023년 신규 상장 종목이 i==100에서 후보가 되는 실데이터가 있어야 한다")
        self.assertEqual(len(long_), 0)


if __name__ == "__main__":
    unittest.main()
