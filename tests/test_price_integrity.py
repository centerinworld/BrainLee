import sqlite3
import unittest
from unittest.mock import patch
from price_integrity import (ensure_schema,rebuild_views,invalid_ohlcv,outside_band,
    gate_price_batch,gate_gap_fill_row,assert_research_prices,PriceIntegrityError,verification_fingerprint,
    manifest_repair_status,manifest_gap_fill_status,refresh_calendar)
from scripts.audit_price_jumps_and_build_canonical import DDL, run

class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.c.executescript('''CREATE TABLE price_history(id INTEGER PRIMARY KEY,stock_code TEXT,date TEXT,
            open REAL,high REAL,low REAL,close REAL,volume REAL);
            CREATE TABLE corporate_action_events(stock_code TEXT,event_date TEXT,event_type TEXT,
            adjustment_status TEXT,evidence_report_name TEXT,confidence REAL,backward_price_factor REAL);
            CREATE TABLE dart_disclosures(stock_code TEXT,rcept_dt TEXT,report_nm TEXT);
            CREATE TABLE stock_universe(id INTEGER,stock_code TEXT,base_date TEXT,market TEXT,stock_type TEXT);
            CREATE TABLE stock_price_daily(stock_code TEXT,bas_dt TEXT,close_price REAL);
            CREATE TABLE naver_price_history_backfill(stock_code TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL);
        ''');self.c.executescript(DDL);ensure_schema(self.c)
        self.c.executemany('INSERT INTO price_trading_calendar VALUES(?)',[(d,) for d in ['2014-01-02','2014-01-03','2026-01-02','2026-01-05','2026-01-06','2026-01-07']])
        rebuild_views(self.c)
    def add(self,d,c=100,o=None,h=None,l=None,v=10,code='005930'):
        self.c.execute('INSERT INTO price_history(stock_code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)',
          (code,d,c if o is None else o,c if h is None else h,c if l is None else l,c,v))
    def qualities(self):
        return [tuple(r) for r in self.c.execute('SELECT date,canonical_quality,return_usable FROM canonical_price_history_v ORDER BY date')]
    def test_historical_limits(self):
        self.assertTrue(outside_band(100,120,'2014-01-03'))
        self.assertFalse(outside_band(100,130,'2026-01-05'))
        self.assertTrue(outside_band(100,131,'2026-01-05'))
    def test_weekend_not_gap(self):
        self.add('2026-01-02');self.add('2026-01-05',110)
        self.assertEqual(self.qualities()[-1][1],'normal')
    def test_missing_session_is_gap_even_small_return(self):
        self.add('2026-01-02');self.add('2026-01-06',105)
        self.assertEqual(self.qualities()[-1][1],'coverage_gap')
    def test_zero_price_not_skipped(self):
        self.add('2026-01-02',0);self.add('2026-01-05',100)
        self.assertEqual(self.qualities()[-1][1],'invalid_previous_price')
    def test_refresh_calendar_ignores_ks11_alone_without_broad_equity_coverage(self):
        """2026-09-20 실사용 중 재현: 2026-07-17(실제 KR 휴장일 - pykrx/KRX 공식으로
        확인: 개별 종목은 하나도 거래 안 했는데 ^KS11 지수만 종가·거래량이 있는
        행이 남아 있었음)이 refresh_calendar()의 예전 "^KS11 존재 OR 종목 100개+"
        조건 때문에 거래일로 잘못 등록됐고, 그 결과 다음 실제 거래일마다 모든
        종목이 coverage_gap으로 오탐됐다(실측 2,689건, 당시 전체 coverage_gap
        풀의 약 90%). ^KS11 단독으로는 더 이상 거래일로 인정하지 않아야 한다."""
        self.add('2026-01-08',code='^KS11',v=424280)  # holiday-artifact 행: 지수만 있고 개별종목 0개
        refresh_calendar(self.c)
        row = self.c.execute("SELECT 1 FROM price_trading_calendar WHERE date='2026-01-08'").fetchone()
        self.assertIsNone(row, "^KS11 혼자로는 거래일 캘린더에 등록되면 안 된다")
    def test_refresh_calendar_still_accepts_broad_real_trading_day(self):
        """회귀 방지: 종목 100개 이상이 실제로 거래된 날은 그대로 인정돼야 한다."""
        for i in range(100):
            self.add('2026-01-09',code=f'{i:06d}')
        refresh_calendar(self.c)
        row = self.c.execute("SELECT 1 FROM price_trading_calendar WHERE date='2026-01-09'").fetchone()
        self.assertIsNotNone(row)
    def test_ohlc_failure_inside_band(self):
        self.add('2026-01-02');self.add('2026-01-05',101,h=99)
        self.assertEqual(self.qualities()[-1][1],'invalid_ohlcv')
    def test_bad_row_blocks_both_returns(self):
        self.add('2026-01-02');self.add('2026-01-05',150);self.add('2026-01-06',155)
        self.assertEqual([r[0] for r in self.c.execute('SELECT safe_daily_return FROM canonical_price_returns_v')],[None,None,None])
    def test_quarantine_blocks_small_jump(self):
        self.add('2026-01-02');self.add('2026-01-05',110)
        self.c.execute("INSERT INTO price_integrity_quarantine VALUES('005930','2026-01-05','basis','sample','now')")
        self.assertEqual(self.qualities()[-1][1],'quarantined_basis')
    def test_legacy_write_observation_is_evidence_not_active_quarantine(self):
        self.add('2026-01-02');self.add('2026-01-05',110)
        self.c.execute("INSERT INTO price_integrity_quarantine VALUES('005930','2026-01-05','unverified_historical_write','legacy trigger observation','now')")
        self.assertEqual(self.qualities()[-1][1],'normal')
    def test_ingestion_rejects_overlap_and_retains_payload(self):
        self.add('2026-01-02',100)
        self.assertFalse(gate_price_batch(self.c,'005930',[('2026-01-02',200,200,200,200,10)],'test',today='2026-01-06'))
        self.assertEqual(self.c.execute('SELECT close FROM price_history').fetchone()[0],100)
        self.assertEqual(self.c.execute('SELECT count(*) FROM price_ingestion_quarantine').fetchone()[0],1)
    def test_ingestion_checks_right_seam(self):
        self.add('2026-01-05',100)
        self.assertFalse(gate_price_batch(self.c,'005930',[('2026-01-02',20,20,20,20,10)],'test',today='2026-01-06'))
    def test_ingestion_matching_overlap_allowed(self):
        self.add('2026-01-02',100)
        self.assertTrue(gate_price_batch(self.c,'005930',[('2026-01-02',100,100,100,100,10),('2026-01-05',102,102,102,102,10)],'test',today='2026-01-06'))
    def test_gap_fill_row_accepts_isolated_missing_day_in_band(self):
        # gate_price_batch would reject this exact case (no existing overlap on
        # any of the batch's own dates) - gate_gap_fill_row is the counterpart
        # for a single day with no batch to overlap against.
        self.add('2026-01-02',100);self.add('2026-01-06',105)
        self.assertTrue(gate_gap_fill_row(self.c,'005930','2026-01-05',(101,103,100,102,10),'test'))
        self.c.execute('INSERT INTO price_history(stock_code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)',
          ('005930','2026-01-05',101,103,100,102,10))
        self.assertEqual(self.qualities()[1][1],'normal')
    def test_gap_fill_row_rejects_isolated_missing_day_outside_band(self):
        self.add('2026-01-02',100);self.add('2026-01-06',105)
        self.assertFalse(gate_gap_fill_row(self.c,'005930','2026-01-05',(900,900,900,900,10),'test'))
        self.assertEqual(self.c.execute('SELECT count(*) FROM price_ingestion_quarantine').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM price_history').fetchone()[0],2)
    def test_gap_fill_row_rejects_when_not_actually_a_gap(self):
        self.add('2026-01-02',100);self.add('2026-01-05',102);self.add('2026-01-06',105)
        self.assertFalse(gate_gap_fill_row(self.c,'005930','2026-01-05',(101,103,100,102,10),'test'))
    def test_gap_fill_row_allows_overwriting_zero_volume_placeholder(self):
        # The volume=0 placeholder is exactly what the "보완" (backfill) branch of
        # collect_krx_history._save_stocks targets - it must not be treated as a
        # real observation blocking the gap-fill path.
        self.add('2026-01-02',100);self.add('2026-01-05',102,v=0);self.add('2026-01-06',105)
        self.assertTrue(gate_gap_fill_row(self.c,'005930','2026-01-05',(101,103,100,102,10),'test'))
    def test_gap_fill_row_rejects_future_date(self):
        self.assertFalse(gate_gap_fill_row(self.c,'005930','2099-01-01',(100,100,100,100,10),'test'))
        self.assertEqual(self.c.execute('SELECT count(*) FROM price_ingestion_quarantine').fetchone()[0],1)
    def test_suspension_zero_ohlc_is_not_fabricated(self):
        self.assertFalse(invalid_ohlcv(0,0,0,100,0));self.assertTrue(invalid_ohlcv(0,0,0,100,10))
    def test_nonfinite_rejected(self):
        self.assertTrue(invalid_ohlcv(100,100,100,float('nan'),10))
    def test_backtest_fails_instead_of_dropping_symbol(self):
        """exclude=False(기본값) - 예전과 동일한 엄격 동작, 여전히 유효."""
        self.add('2026-01-02');self.add('2026-01-05',150)
        with self.assertRaises(PriceIntegrityError):assert_research_prices(self.c,['005930'],'2026-01-01','2026-01-06')
    def test_exclude_mode_drops_only_the_problem_symbol_not_the_whole_run(self):
        """2026-09-20 소유자 지시로 정책 추가: exclude=True면 문제 있는 종목만 빼고
        나머지 유니버스는 그대로 진행할 수 있어야 한다(넓은 유니버스 전략이 시장
        전역 어딘가의 사소한 미확정 건 하나 때문에 통째로 막히는 문제 해소 -
        2026-09-12 핸드오프의 v4 사례). 문제 없는 종목은 절대 제외 목록에 들어가면
        안 된다."""
        self.add('2026-01-02');self.add('2026-01-05',150)  # 005930: 미확정 점프
        self.add('2026-01-02',code='000660');self.add('2026-01-05',105,code='000660')  # 정상
        excluded = assert_research_prices(self.c,['005930','000660'],'2026-01-01','2026-01-06',exclude=True)
        self.assertEqual(excluded,{'005930'})
    def test_exclude_mode_returns_empty_set_when_nothing_is_wrong(self):
        self.add('2026-01-02',code='000660');self.add('2026-01-05',105,code='000660')
        excluded = assert_research_prices(self.c,['000660'],'2026-01-01','2026-01-06',exclude=True)
        self.assertEqual(excluded,set())
    def test_expanded_audit_and_raw_agreement_never_approve(self):
        self.add('2026-01-02');self.add('2026-01-05',150)
        self.c.executemany('INSERT INTO stock_price_daily VALUES(?,?,?)',[('005930','20260102',100),('005930','20260105',150)])
        with patch('pathlib.Path.write_text'):
            result=run(self.c)
        self.assertEqual(result['audited_jumps'],1)
        self.assertEqual(result['return_usable_jumps'],0)
    def test_fingerprint_changes_with_price_not_derived_verdict(self):
        fields=('stock_code','event_date','previous_date','previous_close','event_close','price_ratio','public_previous_close','public_event_close','public_price_ratio','matched_event_type','matched_report_name')
        r=dict.fromkeys(fields,None);r['event_close']=100
        fp=verification_fingerprint(r);r['classification']='external'
        self.assertEqual(fp,verification_fingerprint(r));r['event_close']=101
        self.assertNotEqual(fp,verification_fingerprint(r))
    def test_manifest_repair_revalidates_live_and_source_rows(self):
        self.add('2026-01-02',100)
        self.c.execute("INSERT INTO naver_price_history_backfill VALUES('005930','2026-01-02',110,110,110,110,10)")
        self.assertEqual(manifest_repair_status(self.c,'005930','2026-01-02',(100,100,100,100,10),(110,110,110,110,10)),'ready')
        self.c.execute("UPDATE price_history SET close=101 WHERE stock_code='005930'")
        self.assertEqual(manifest_repair_status(self.c,'005930','2026-01-02',(100,100,100,100,10),(110,110,110,110,10)),'live_row_changed_since_review')
    def test_manifest_gap_fill_must_still_be_an_interior_market_gap(self):
        self.add('2026-01-02',100);self.add('2026-01-06',102)
        self.c.execute("INSERT INTO naver_price_history_backfill VALUES('005930','2026-01-05',101,101,101,101,10)")
        self.assertEqual(manifest_gap_fill_status(self.c,'005930','2026-01-05',(101,101,101,101,10)),'ready')

if __name__=='__main__':unittest.main()
