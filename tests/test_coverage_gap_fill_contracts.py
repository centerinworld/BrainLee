"""coverage_gap 보정 파이프라인 계약 회귀 (2026-09-24).

왜 필요한가
-----------
2026-09-24 라이브 감사(`price_jump_audit`)에 `coverage_gap`(결손 후보)이 대량 남아 있고,
그중 일부만 실제로 채울 수 있다. 채우는 스크립트가 라이브 DB에서 `--apply`로 돌기 전에
고정해야 하는 계약은 다음 4가지다 — 하나라도 깨지면 조정주가(Naver) 기준의 값이 원주가
시계열에 섞여 들어가 백테스트·시그널 수익률이 통째로 오염된다.

1. **조정주가 기준**: FDR/Naver 값은 이웃한 DB 종가 대비 KRX 가격제한폭(±30%) 안에 있을
   때만, 그리고 이웃이 최소 하나는 존재할 때만 채택한다. 소수점 가격(조정 basis 지문)은
   원천에서 배제한다.
2. **PIT/구간**: 삽입 대상일은 `previous_date`와 `event_date` **사이**의 거래일뿐이다
   (양 끝점 제외 = 반열린 구간). 장기 공백(>max_gap)은 정지/상폐이지 결손이 아니다.
3. **재실행 idempotency**: 이미 있는 (stock_code,date)는 후보에서 빠지고, 삽입은
   `ON CONFLICT (stock_code,date) DO NOTHING` + `price_history_fix_backup`(old_*=NULL,
   rollback = 해당 run_id 행 삭제)로만 이뤄진다. 재실행이 0건이어야 한다.
4. **검토 기록**: 사람이 "못 채운다"고 판정한 결손은 `price_coverage_gap_reviewed`에 남고,
   감사는 그 행을 매 실행 재판독해 `coverage_gap_reviewed`로 분류한다 — **단 여전히
   return_usable=0**(수익률 계산에 쓰이면 안 된다). 재빌드(DELETE 후 전량 재삽입)에도
   분류가 유지돼야 하며, 비상장/지수 심볼 판정을 덮어쓰면 안 된다.

이 테스트는 라이브 DB에 쓰지 않는다: 순수 함수 + 임시 sqlite + 기록용 가짜 커넥션만 쓴다.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from price_integrity import ensure_schema, rebuild_views  # noqa: E402
from scripts.audit_price_jumps_and_build_canonical import DDL, run  # noqa: E402

_FILL_SCRIPT = ROOT / "scripts" / "fill_coverage_gaps_20260924.py"
_SUSPENSION_SCRIPT = ROOT / "scripts" / "fill_suspension_gaps_from_marcap_20260924.py"


def _load_script(path: Path):
    """scripts/*.py 는 패키지가 아니므로 파일 경로로 로드한다."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


fill = _load_script(_FILL_SCRIPT)
suspension = _load_script(_SUSPENSION_SCRIPT)


def _row(**kwargs) -> pd.DataFrame:
    base = dict(code="005930", date="2026-01-05", open=100, high=110, low=95, close=105, volume=1000)
    base.update(kwargs)
    return pd.DataFrame([base])


# ─────────────────────────────────────────────────────────────────────────────
# 1. 원천 행 필터 — 조정주가 basis / OHLC 형상 / NaN
# ─────────────────────────────────────────────────────────────────────────────
class ValidRowsContract(unittest.TestCase):
    def test_whole_won_ohlc_is_accepted(self):
        self.assertEqual(len(fill.valid_rows(_row())), 1)

    def test_fractional_price_is_rejected_as_adjusted_basis(self):
        """소수점 가격은 Naver/FDR 조정주가 지문 — 원주가 시계열에 들어가면 안 된다."""
        for col in ("open", "high", "low", "close"):
            with self.subTest(col=col):
                self.assertEqual(len(fill.valid_rows(_row(**{col: 105.5}))), 0, f"{col}=105.5 는 거부돼야 한다")

    def test_out_of_shape_ohlc_is_rejected(self):
        self.assertEqual(len(fill.valid_rows(_row(high=99))), 0)
        self.assertEqual(len(fill.valid_rows(_row(low=120))), 0)
        self.assertEqual(len(fill.valid_rows(_row(open=0))), 0)

    def test_nan_does_not_pass_the_filter(self):
        """PG는 NaN을 '모든 실수보다 큰 값'으로 취급해 close>0 으로 걸러지지 않는다."""
        self.assertEqual(len(fill.valid_rows(_row(close=float("nan")))), 0)
        self.assertEqual(len(fill.valid_rows(_row(volume=float("nan")))), 0)

    def test_nonpositive_close_and_negative_volume_are_rejected(self):
        self.assertEqual(len(fill.valid_rows(_row(close=0))), 0)
        self.assertEqual(len(fill.valid_rows(_row(volume=-1))), 0)

    def test_suspension_marker_is_accepted_but_a_zero_row_is_not(self):
        """O=H=L=0 · volume=0 · close>0 은 기존 정지 표시 관례 — 그대로 채택한다."""
        self.assertEqual(len(fill.valid_rows(_row(open=0, high=0, low=0, close=100, volume=0))), 1)
        self.assertEqual(len(fill.valid_rows(_row(open=0, high=0, low=0, close=0, volume=0))), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. FDR(조정 basis) 채택 밴드
# ─────────────────────────────────────────────────────────────────────────────
class FdrBandContract(unittest.TestCase):
    """밴드는 (a) 날짜별 가격제한폭이고 (b) 방향별이다 — 둘 다 여기서 못 박는다."""

    DAY = "2026-06-01"        # ±30% 구간(2015-06-15 이후)
    NEXT = "2026-06-02"

    def test_no_neighbour_refuses_the_value(self):
        self.assertFalse(fill.fdr_band_ok(100, None, None, self.DAY, next_day=self.NEXT))

    def test_in_band_against_both_neighbours_is_accepted(self):
        self.assertTrue(fill.fdr_band_ok(100, 95, 108, self.DAY, next_day=self.NEXT))

    def test_one_sided_neighbour_is_enough_when_it_agrees(self):
        self.assertTrue(fill.fdr_band_ok(100, 95, None, self.DAY))
        self.assertTrue(fill.fdr_band_ok(100, None, 108, self.DAY, next_day=self.NEXT))

    def test_split_adjusted_value_fails_against_a_neighbour(self):
        # 10:1 병합 뒤 Naver 조정계열은 이웃 원주가의 1/10 → 밴드 밖
        self.assertFalse(fill.fdr_band_ok(10, 100, None, self.DAY))
        self.assertFalse(fill.fdr_band_ok(10, None, 100, self.DAY, next_day=self.NEXT))
        # 한쪽만 맞아도 거부 (존재하는 이웃 모두와 맞아야 함)
        self.assertFalse(fill.fdr_band_ok(100, 100, 400, self.DAY, next_day=self.NEXT))

    def test_band_edges_are_inclusive(self):
        self.assertTrue(fill.fdr_band_ok(70, 100, None, self.DAY))
        self.assertTrue(fill.fdr_band_ok(130, 100, None, self.DAY))
        self.assertFalse(fill.fdr_band_ok(69, 100, None, self.DAY))
        self.assertFalse(fill.fdr_band_ok(131, 100, None, self.DAY))

    def test_previous_neighbour_band_is_the_price_limit(self):
        """과거 이웃: 종가가 직전 종가의 -30%..+30% (가격 기준)."""
        self.assertTrue(fill.fdr_band_ok(700, 1000, None, self.DAY))
        self.assertTrue(fill.fdr_band_ok(1300, 1000, None, self.DAY))
        self.assertFalse(fill.fdr_band_ok(699, 1000, None, self.DAY))
        self.assertFalse(fill.fdr_band_ok(1301, 1000, None, self.DAY))

    def test_next_neighbour_band_is_direction_aware(self):
        """미래 이웃은 비율이 뒤집힌다: 하루 -30% 하한이면 close/next = 1/0.70 = 1.4286.

        2026-09-24: 종전 구현은 [0.70, 1.30] 을 양쪽에 그대로 적용해, 2015~2018 소형주의 정상적인
        하한가 하루(close/next = 1.31~1.4286)를 'basis splice'로 잘못 거부하고 반대로 제한폭을 넘는
        다음날 상승(0.70~0.769)은 통과시켰다.
        """
        limit = 1.0 / 0.70
        self.assertAlmostEqual(limit, 1.4285714285714286)
        self.assertTrue(fill.fdr_band_ok(1000, None, 700, self.DAY, next_day=self.NEXT))   # 다음날 -30.0% 하한
        self.assertTrue(fill.fdr_band_ok(1000, None, 770, self.DAY, next_day=self.NEXT))   # -23.0% (종전 거부)
        self.assertTrue(fill.fdr_band_ok(1000, None, 701, self.DAY, next_day=self.NEXT))
        self.assertFalse(fill.fdr_band_ok(1000, None, 699, self.DAY, next_day=self.NEXT))  # -30.1% → 초과
        self.assertTrue(fill.fdr_band_ok(1000, None, 1300, self.DAY, next_day=self.NEXT))  # 1000/1300 = 1/1.3
        self.assertFalse(fill.fdr_band_ok(1000, None, 1301, self.DAY, next_day=self.NEXT))  # +30.1% → 초과
        self.assertFalse(fill.fdr_band_ok(1000, None, 1400, self.DAY, next_day=self.NEXT))  # +40% → 초과

    def test_both_neighbours_must_still_agree(self):
        self.assertTrue(fill.fdr_band_ok(1000, 900, 800, self.DAY, next_day=self.NEXT))
        self.assertFalse(fill.fdr_band_ok(1000, 900, 600, self.DAY, next_day=self.NEXT))    # 다음 이웃만 어긋남
        self.assertFalse(fill.fdr_band_ok(1000, 1500, 800, self.DAY, next_day=self.NEXT))   # 앞 이웃만 어긋남

    # ── 날짜별 가격제한폭 (price_integrity.price_band: 2015-06-15 전 ±15%, 이후 ±30%) ─────────────────
    def test_band_is_date_sensitive_before_and_after_20150615(self):
        """±30% 밴드를 2015-01-02 까지 소급 적용하면 당시 불가능한 움직임을 통과시킨다."""
        self.assertEqual(fill.price_band("2015-06-14"), (0.85, 1.15))
        self.assertEqual(fill.price_band("2015-06-15"), (0.70, 1.30))
        self.assertTrue(fill.fdr_band_ok(115, 100, None, "2015-06-12"))    # +15.0% 상한(당시)
        self.assertFalse(fill.fdr_band_ok(116, 100, None, "2015-06-12"))   # +16.0% → 당시 초과
        self.assertTrue(fill.fdr_band_ok(116, 100, None, "2015-06-15"))    # 같은 값이 이후엔 합법
        self.assertFalse(fill.fdr_band_ok(131, 100, None, "2015-06-15"))

    def test_next_candle_crossing_the_policy_change_uses_the_next_days_band(self):
        """다음 거래일이 2015-06-15 을 넘으면 그 다음날의 밴드(역수)로 판정한다."""
        # close 100, 다음날 75 → -25% 는 이후 기준 ±30% 안, 당시 기준 ±15% 밖
        self.assertTrue(fill.fdr_band_ok(100, None, 75, "2015-06-12", next_day="2015-06-15"))
        self.assertFalse(fill.fdr_band_ok(100, None, 75, "2015-06-12", next_day="2015-06-12"))

    def test_missing_dates_are_programming_errors_not_silent_verdicts(self):
        """날짜가 없으면 제한폭을 알 수 없다 — 조용히 통과/거부시키지 않는다."""
        with self.assertRaises(ValueError):
            fill.fdr_band_ok(100, 95, None, None)
        with self.assertRaises(ValueError):
            fill.fdr_band_ok(100, None, 95, self.DAY)       # next_close 가 있는데 next_day 가 없다


# ─────────────────────────────────────────────────────────────────────────────
# 3. 반열린 구간 / max_gap / 재실행 시 0건
# ─────────────────────────────────────────────────────────────────────────────
class _AnyToInShim:
    """sqlite 로 `missing_pairs` 실코드를 돌리기 위한 최소 shim.

    라이브 스크립트가 PostgreSQL 전용으로 쓰는 곳은 `stock_code = ANY(?)` 한 구문뿐이므로,
    그 구문만 등가인 `IN (?,?,...)` 로 바꿔 실제 SQLite 에서 나머지 SQL(anti-join 포함)을
    그대로 실행한다. 검증 대상인 파이썬 코드는 라이브 그대로다.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params=()):
        if "= ANY(?)" in sql:
            codes = list(params[0])
            sql = sql.replace("stock_code = ANY(?)", "stock_code IN (%s)" % ",".join("?" for _ in codes))
            params = tuple(codes)
        return self._conn.execute(sql, params)


class MissingPairsContract(unittest.TestCase):
    """price_jump_audit 의 coverage_gap → 실제로 삽입 대상인 (code,date) 후보."""

    CAL = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]

    def test_gap_days_is_the_original_half_open_expression(self):
        """리팩터 근거: gap_days 는 인라인 bisect 식과 모든 입력에서 동일해야 한다."""
        import bisect

        for cal in ([], self.CAL, ["2025-12-30"] + self.CAL):
            for p in ["2025-01-01", "2026-01-02", "2026-01-05", "2026-01-07", "2026-01-09"]:
                for d in ["2026-01-02", "2026-01-06", "2026-01-08", "2027-01-01"]:
                    self.assertEqual(fill.gap_days(cal, p, d),
                                     cal[bisect.bisect_right(cal, p):bisect.bisect_left(cal, d)], (cal, p, d))

    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.executescript(
            """CREATE TABLE price_jump_audit(stock_code TEXT,event_date TEXT,previous_date TEXT,classification TEXT);
               CREATE TABLE price_history(stock_code TEXT,date TEXT,close REAL);
               CREATE TABLE price_trading_calendar(date TEXT PRIMARY KEY);"""
        )
        self.c.executemany("INSERT INTO price_trading_calendar VALUES(?)", [(d,) for d in self.CAL])
        self.shim = _AnyToInShim(self.c)

    def _audit(self, code, event, prev):
        self.c.execute("INSERT INTO price_jump_audit VALUES(?,?,?,?)", (code, event, prev, "coverage_gap"))

    def _price(self, date, code="005930", close=100.0):
        self.c.execute("INSERT INTO price_history VALUES(?,?,?)", (code, date, close))

    def test_only_interior_trading_days_are_proposed(self):
        """양 끝점(previous/event)은 이미 존재하는 행 — 다시 제안하면 안 된다."""
        self._price("2026-01-02")
        self._price("2026-01-06", close=105.0)
        self._audit("005930", "2026-01-06", "2026-01-02")
        miss = fill.missing_pairs(self.shim)
        self.assertEqual([tuple(r) for r in miss[["code", "date"]].itertuples(index=False)],
                         [("005930", "2026-01-05")])

    def test_already_filled_pair_drops_out_on_the_next_run(self):
        """재실행 idempotency: 채운 뒤 같은 감사 입력으로 다시 돌리면 0건이어야 한다."""
        self._price("2026-01-02")
        self._price("2026-01-06", close=105.0)
        self._audit("005930", "2026-01-06", "2026-01-02")
        self.assertEqual(len(fill.missing_pairs(self.shim)), 1)
        self._price("2026-01-05", close=102.0)          # 앞선 실행이 채워 넣은 행
        self.assertTrue(fill.missing_pairs(self.shim).empty)

    def test_long_gap_is_not_a_missing_row(self):
        """장기 공백은 거래정지/상폐 — 결손 보정 대상이 아니다(기본 max_gap=30)."""
        self._price("2025-01-02")
        self._price("2026-01-06", close=105.0)
        self._audit("005930", "2026-01-06", "2025-01-02")
        self.assertTrue(fill.missing_pairs(self.shim).empty)
        self.assertFalse(fill.missing_pairs(self.shim, max_gap=100000).empty)

    def test_duplicate_audit_rows_do_not_duplicate_candidates(self):
        self._price("2026-01-02")
        self._price("2026-01-06", close=105.0)
        self._audit("005930", "2026-01-06", "2026-01-02")
        self._audit("005930", "2026-01-06", "2026-01-02")
        self.assertEqual(len(fill.missing_pairs(self.shim)), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 4. apply 경로 — 가드 / 원천값 무변형 / dry-run 무기록
# ─────────────────────────────────────────────────────────────────────────────
class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingConn:
    """connect_primary_db 대역. 실행된 SQL 을 기록만 하고 아무것도 쓰지 않는다."""

    def __init__(self, rows_for=None):
        self.calls: list[tuple[str, object]] = []
        self.committed = False
        self._rows_for = rows_for or {}

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        for needle, rows in self._rows_for.items():
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


def writes(conn) -> list:
    """커넥션에 남은 쓰기성 호출만 추린다(dry-run 검증용)."""
    return [(sql, p) for sql, p in conn.calls if any(mk in sql for mk in WRITE_MARKERS)]


class FillApplyContract(unittest.TestCase):
    SRC = pd.DataFrame([dict(code="005930", date="2026-01-05",
                             open=101, high=103, low=100, close=102, volume=1234)])
    EMPTY = pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume"])
    MARCAP_EMPTY = pd.DataFrame(columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])

    def _run_fill(self, apply: bool, marcap: pd.DataFrame, missing: pd.DataFrame | None = None):
        conn = _RecordingConn()
        miss = self.SRC[["code", "date"]] if missing is None else missing
        with patch.object(fill, "connect_primary_db", lambda **kw: conn), \
             patch.object(fill, "missing_pairs", lambda c, mg: miss), \
             patch.object(fill, "marcap_rows", lambda m: marcap), \
             patch.object(fill, "fdr_rows", lambda c, m: self.EMPTY):
            fill.main(apply=apply, max_gap=30)
        return conn

    def _run_suspension(self, apply: bool, marcap: pd.DataFrame):
        conn = _RecordingConn(rows_for={
            "FROM price_jump_audit": [("005930", "2026-01-06", "2026-01-02")],
            "FROM price_trading_calendar": [("2026-01-02",), ("2026-01-05",), ("2026-01-06",)],
        })
        with patch.object(suspension, "connect_primary_db", lambda **kw: conn), \
             patch.object(suspension, "ensure_year", lambda y: "dummy"), \
             patch("pandas.read_parquet", lambda *a, **kw: marcap):
            suspension.main(apply=apply, queue_path=Path(tempfile.mkdtemp()) / "gate_queue.csv")
        return conn

    # ── fill_coverage_gaps ────────────────────────────────────────────────
    def test_dry_run_writes_nothing_and_does_not_commit(self):
        conn = self._run_fill(apply=False, marcap=self.SRC)
        self.assertEqual(writes(conn), [], "dry-run 은 한 행도 쓰면 안 된다")
        self.assertFalse(conn.committed)

    def test_apply_arms_the_basis_guard_before_inserting(self):
        conn = self._run_fill(apply=True, marcap=self.SRC)
        sqls = [sql for sql, _ in conn.calls]
        guard = next(i for i, s in enumerate(sqls) if "set_config('app.price_basis_checked'" in s)
        insert = next(i for i, s in enumerate(sqls) if "INSERT INTO price_history" in s)
        self.assertLess(guard, insert, "가드 플래그는 삽입보다 먼저 세워져야 한다")
        self.assertIn("ON CONFLICT (stock_code,date) DO NOTHING", sqls[insert])

    def test_inserted_values_are_the_source_values_verbatim(self):
        """채운 값은 원천 그대로 — 이웃 종가로 보간/추정하면 그 자체가 미래정보 주입이다."""
        conn = self._run_fill(apply=True, marcap=self.SRC)
        params = [p for sql, p in conn.calls if "INSERT INTO price_history" in sql][0]
        self.assertEqual(params[0][:7], ("005930", "2026-01-05", 101, 103, 100, 102, 1234))

    def test_backup_row_has_null_old_values_for_rollback(self):
        conn = self._run_fill(apply=True, marcap=self.SRC)
        sql, params = [c for c in conn.calls if "price_history_fix_backup" in c[0]][0]
        # old_* 는 행 부재를 뜻하는 SQL 리터럴 NULL — rollback = 이 run_id 행 삭제
        self.assertIn("VALUES(?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?)", sql)
        run_id, code, date = params[0][:3]
        self.assertTrue(run_id.startswith("coverage_gap_fill_"))
        self.assertEqual((code, date), ("005930", "2026-01-05"))
        self.assertEqual(params[0][3:8], (101, 103, 100, 102, 1234))   # new_* = 원천값
        self.assertIn("[marcap]", params[0][8])
        self.assertTrue(any("data_fix_log" in s for s, _ in conn.calls))
        self.assertTrue(conn.committed)

    def test_apply_with_nothing_to_fill_writes_nothing(self):
        """재실행 경로: 후보 0건이면 --apply 라도 아무것도 하지 않는다."""
        conn = self._run_fill(apply=True, marcap=self.EMPTY, missing=pd.DataFrame(columns=["code", "date"]))
        self.assertEqual(writes(conn), [])
        self.assertFalse(conn.committed)

    # ── fill_suspension_gaps_from_marcap ──────────────────────────────────
    def test_suspension_fill_dry_run_writes_nothing(self):
        conn = self._run_suspension(apply=False, marcap=self.MARCAP_EMPTY)
        self.assertEqual(writes(conn), [], "dry-run 은 한 행도 쓰면 안 된다")
        self.assertFalse(conn.committed)

    def test_suspension_fill_shares_the_guard_and_copies_source_markers(self):
        marker = pd.DataFrame([dict(Code="005930", Date="2026-01-05", Open=0, High=0, Low=0,
                                    Close=100, Volume=0)])
        conn = self._run_suspension(apply=True, marcap=marker)
        sqls = [sql for sql, _ in conn.calls]
        guard = next(i for i, s in enumerate(sqls) if "set_config('app.price_basis_checked'" in s)
        insert = next(i for i, s in enumerate(sqls) if "INSERT INTO price_history" in s)
        self.assertLess(guard, insert)
        self.assertIn("ON CONFLICT (stock_code,date) DO NOTHING", sqls[insert])
        params = [p for sql, p in conn.calls if "INSERT INTO price_history" in sql][0]
        self.assertEqual(params[0][:7], ("005930", "2026-01-05", 0, 0, 0, 100, 0))
        bsql, bparams = [c for c in conn.calls if "price_history_fix_backup" in c[0]][0]
        self.assertIn("VALUES(?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?)", bsql)
        self.assertEqual(bparams[0][3:8], (0, 0, 0, 100, 0))
        self.assertTrue(conn.committed)

    def test_suspension_fill_skips_codes_marcap_does_not_cover(self):
        """marcap 이 결손일을 전부 갖고 있지 않으면 그 이벤트는 건드리지 않는다."""
        other = pd.DataFrame([dict(Code="000660", Date="2026-01-05", Open=0, High=0, Low=0,
                                   Close=100, Volume=0)])
        conn = self._run_suspension(apply=True, marcap=other)
        self.assertEqual(writes(conn), [])


# ─────────────────────────────────────────────────────────────────────────────
# 5. 감사 재분류 — 검토 기록이 매 빌드에서 재판독되고, 수익률 사용은 계속 금지
# ─────────────────────────────────────────────────────────────────────────────
class AuditCoverageGapReviewedContract(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.executescript(
            """CREATE TABLE price_history(id INTEGER PRIMARY KEY,stock_code TEXT,date TEXT,
                open REAL,high REAL,low REAL,close REAL,volume REAL);
               CREATE TABLE corporate_action_events(stock_code TEXT,event_date TEXT,event_type TEXT,
                adjustment_status TEXT,evidence_report_name TEXT,confidence REAL,backward_price_factor REAL);
               CREATE TABLE dart_disclosures(stock_code TEXT,rcept_dt TEXT,report_nm TEXT);
               CREATE TABLE stock_universe(id INTEGER,stock_code TEXT,base_date TEXT,market TEXT,stock_type TEXT);
               CREATE TABLE stock_price_daily(stock_code TEXT,bas_dt TEXT,close_price REAL);
               CREATE TABLE naver_price_history_backfill(stock_code TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL);"""
        )
        self.c.executescript(DDL)
        ensure_schema(self.c)
        self.c.executemany("INSERT INTO price_trading_calendar VALUES(?)",
                           [(d,) for d in ["2026-01-02", "2026-01-05", "2026-01-06"]])
        rebuild_views(self.c)

    def add(self, d, c=100, v=10, code="005930"):
        self.c.execute("INSERT INTO price_history(stock_code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                       (code, d, c, c, c, c, v))

    def _audit(self):
        with patch("pathlib.Path.write_text"):
            return run(self.c)

    def _review(self, code="005930", event="2026-01-06", prev="2026-01-02",
                reason="no_source_data", evidence="1 missing day(s); none present in marcap, FDR"):
        self.c.execute("INSERT INTO price_coverage_gap_reviewed VALUES(?,?,?,?,?,?)",
                       (code, event, prev, reason, evidence, "2026-09-24T13:00:00"))

    def _classification(self, code="005930", event="2026-01-06"):
        return self.c.execute(
            "SELECT classification,return_usable,evidence FROM price_jump_audit WHERE stock_code=? AND event_date=?",
            (code, event)).fetchone()

    def test_unreviewed_gap_stays_queued_as_coverage_gap(self):
        self.add("2026-01-02"); self.add("2026-01-06", 105)
        self._audit()
        row = self._classification()
        self.assertEqual(row["classification"], "coverage_gap")
        self.assertEqual(row["return_usable"], 0)

    def test_reviewed_gap_is_reclassified_with_its_evidence(self):
        self.add("2026-01-02"); self.add("2026-01-06", 105)
        self._review()
        self._audit()
        row = self._classification()
        self.assertEqual(row["classification"], "coverage_gap_reviewed")
        self.assertEqual(row["return_usable"], 0, "검토 완료는 '채웠다'가 아니다 — 수익률 사용 금지")
        self.assertTrue(row["evidence"].startswith("no_source_data:"), row["evidence"])

    def test_review_only_matches_its_own_previous_date(self):
        """previous_date 가 다르면(구간이 바뀌면) 재검토 대상이다 — 무조건 면제가 아니다."""
        self.add("2026-01-02"); self.add("2026-01-06", 105)
        self._review(prev="2025-12-30")
        self._audit()
        self.assertEqual(self._classification()["classification"], "coverage_gap")

    def test_reclassification_survives_a_full_audit_rebuild(self):
        self.add("2026-01-02"); self.add("2026-01-06", 105)
        self._review()
        self._audit()
        self.assertEqual(self._classification()["classification"], "coverage_gap_reviewed")
        self.c.execute("UPDATE price_jump_audit SET classification='coverage_gap'")   # 강제로 되돌린 뒤
        self._audit()                                     # rebuild = DELETE 후 전량 재삽입
        self.assertEqual(self._classification()["classification"], "coverage_gap_reviewed")

    def test_reviewed_gap_is_never_return_usable(self):
        self.add("2026-01-02"); self.add("2026-01-06", 105); self.add("2026-01-07", 106)
        self._review()
        self._audit()
        usable = self.c.execute(
            "SELECT canonical_quality,return_usable FROM canonical_price_history_v WHERE date='2026-01-06'").fetchone()
        self.assertEqual(usable["return_usable"], 0)
        self.assertNotEqual(usable["canonical_quality"], "normal")
        returns = [r[0] for r in self.c.execute("SELECT safe_daily_return FROM canonical_price_returns_v ORDER BY date")]
        self.assertTrue(all(x is None for x in returns), returns)

    def test_review_row_cannot_override_non_equity_symbol(self):
        """지수/매크로 심볼 판정이 먼저다 — 검토 표에 행이 있어도 승격되지 않는다."""
        self.add("2026-01-02", 0, code="^KQ11")        # previous_close=0 → invalid_previous_price
        self.add("2026-01-05", 1000, code="^KQ11")
        self._review(code="^KQ11", event="2026-01-05", prev="2026-01-02")
        self._audit()
        self.assertEqual(self._classification(code="^KQ11", event="2026-01-05")["classification"],
                         "non_equity_symbol")


if __name__ == "__main__":
    unittest.main()
