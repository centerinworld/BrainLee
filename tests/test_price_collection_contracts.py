import asyncio
import sqlite3
import unittest
from unittest.mock import patch, Mock
from collectors.kis_collector import KISCollector
from collect_kis_ohlcv import fetch_ohlcv
from scripts.backfill_naver_ohlcv_2015_2018 import fetch
from scripts.verify_price_history_with_naver import run as verify
from scripts.audit_price_jumps_and_build_canonical import DDL
from price_integrity import ensure_schema

class CollectionContracts(unittest.TestCase):
    def test_short_calendar_page_does_not_end_kis_history(self):
        response=Mock();response.json.return_value={'rt_cd':'0','output2':[{'stck_bsop_date':'20250102','stck_clpr':'100','stck_oprc':'100','stck_hgpr':'100','stck_lwpr':'100','acml_vol':'0'}]}
        with patch('collect_kis_ohlcv.requests.get',return_value=response) as get,patch('collect_kis_ohlcv._rate_wait'):
            rows=fetch_ohlcv('005930','20250101','20250701','test')
        self.assertEqual(get.call_count,2)
        self.assertEqual(len(rows),1)
        self.assertEqual(get.call_args.kwargs['params']['FID_ORG_ADJ_PRC'],'0')
    def test_naver_date_bounded_api_does_not_fabricate_suspended_candle(self):
        response=Mock();response.text="[['date','o','h','l','c','v'],['20100104',0,0,0,100,0]]"
        with patch('scripts.backfill_naver_ohlcv_2015_2018.requests.get',return_value=response) as get:
            _,rows,error=fetch('005930','20100101','20260911')
        self.assertIsNone(error);self.assertIn('startTime=20100101',get.call_args.args[0])
        self.assertEqual(rows[0][2:6],(0,0,0,100))
    def test_period_collector_uses_matching_endpoint_and_tr(self):
        from collectors.kis_collector import _TR
        self.assertEqual(_TR['PERIOD'],'FHKST03010100')
        import inspect
        src=inspect.getsource(KISCollector.fetch_period_ohlcv)
        self.assertIn('inquire-daily-itemchartprice',src)
        self.assertEqual(inspect.signature(KISCollector.fetch_period_ohlcv).parameters['adj_price'].default,'0')
    def test_external_verifier_never_substitutes_previous_date_or_approves(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;c.executescript(DDL);ensure_schema(c)
        c.execute('INSERT INTO price_jump_audit VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          ('005930','2026-01-06','2026-01-02',100,150,1.5,None,None,None,'coverage_gap',0,None,None,'','now'))
        with patch('scripts.verify_price_history_with_naver.fetch_history',return_value=('005930',{'20260105':100,'20260106':150},None)),patch('pathlib.Path.write_text'):
            verify(c,workers=1,only_new=True)
        row=c.execute('SELECT * FROM external_price_verification').fetchone()
        self.assertIsNone(row['external_price_ratio'])
        self.assertEqual(c.execute('SELECT return_usable FROM price_jump_audit').fetchone()[0],0)
    def test_failed_verification_is_retried(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;c.executescript(DDL);ensure_schema(c)
        c.execute('INSERT INTO price_jump_audit VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          ('005930','2026-01-06','2026-01-05',100,150,1.5,None,None,None,'unresolved_active_common',0,None,None,'','now'))
        with patch('scripts.verify_price_history_with_naver.fetch_history',return_value=('005930',{},'timeout')) as f,patch('pathlib.Path.write_text'):
            verify(c,workers=1,only_new=True);verify(c,workers=1,only_new=True)
        self.assertEqual(f.call_count,2)

if __name__=='__main__':unittest.main()
