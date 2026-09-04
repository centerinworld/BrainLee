"""
collectors/kiwoom_collector.py — 키움 REST/WebSocket 연동 수집기

단계:
  1) OAuth 토큰 발급/헬스체크
  2) 실시간 시세(웹소켓) 수집 + 기관/외국인 REST 수급 보조 수집
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime
from typing import Any

import requests

import config
from collectors.base import BaseCollector
from db_utils import connect_stock_db

logger = logging.getLogger(__name__)


class KiwoomCollector(BaseCollector):
    def __init__(self):
        super().__init__(rate_limit_secs=1.0, name="KIWOOM")
        self.base_url = (config.KIWOOM_BASE_URL or "https://api.kiwoom.com").rstrip("/")
        self.app_key = config.KIWOOM_APP_KEY
        self.secret_key = config.KIWOOM_SECRET_KEY
        self.enabled = bool(config.KIWOOM_ENABLED)
        self.ws_url = (getattr(config, "KIWOOM_WS_URL", "") or "wss://api.kiwoom.com:10000/api/dostk/websocket").strip()
        self._token: str = ""
        self._token_expiry_epoch: float = 0.0

    def is_configured(self) -> bool:
        return self.enabled and bool(self.app_key and self.secret_key)

    def _token_alive(self) -> bool:
        return bool(self._token) and (time.time() + 60.0) < self._token_expiry_epoch

    def _ensure_tables(self) -> None:
        last_err = None
        for _ in range(6):
            conn = connect_stock_db(timeout=30)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kiwoom_realtime_quote (
                    stock_code TEXT PRIMARY KEY,
                    last_price REAL,
                    change_price REAL,
                    change_rate REAL,
                    trade_volume REAL,
                    trade_strength REAL,
                    bid1 REAL,
                    ask1 REAL,
                    bid_qty1 REAL,
                    ask_qty1 REAL,
                    source_type TEXT,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
                conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kiwoom_foreign_flow (
                    stock_code TEXT,
                    dt TEXT,
                    close_price REAL,
                    change_qty REAL,
                    poss_stock_cnt REAL,
                    weight REAL,
                    limit_exhaust_rate REAL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (stock_code, dt)
                )
                """
            )
                conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kiwoom_tick_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    source_type TEXT,
                    event_ts TEXT NOT NULL,
                    last_price REAL,
                    change_price REAL,
                    change_rate REAL,
                    trade_volume REAL,
                    trade_strength REAL,
                    bid1 REAL,
                    ask1 REAL,
                    bid_qty1 REAL,
                    ask_qty1 REAL,
                    spread REAL,
                    raw_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kiwoom_tick_code_ts ON kiwoom_tick_history(stock_code, event_ts)")
                conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kiwoom_minute_snapshot (
                    stock_code TEXT NOT NULL,
                    minute_ts TEXT NOT NULL,   -- YYYY-MM-DD HH:MM:00
                    open_price REAL,
                    high_price REAL,
                    low_price REAL,
                    close_price REAL,
                    sum_volume REAL DEFAULT 0,
                    max_strength REAL,
                    min_strength REAL,
                    avg_strength REAL,
                    sample_count INTEGER DEFAULT 0,
                    best_bid1 REAL,
                    best_ask1 REAL,
                    spread_close REAL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (stock_code, minute_ts)
                )
                """
            )
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                last_err = e
                time.sleep(0.8)
            finally:
                conn.close()
        raise sqlite3.OperationalError(f"kiwoom table ensure failed: {last_err}")

    def issue_token(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"ok": False, "reason": "KIWOOM_ENABLED 또는 APP_KEY/SECRET_KEY 미설정"}

        url = f"{self.base_url}/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.secret_key,
        }
        try:
            r = requests.post(url, json=payload, timeout=10)
            try:
                data = r.json() if r.content else {}
            except Exception:
                txt = (r.text or "")[:300]
                return {"ok": False, "reason": f"token_non_json_http_{r.status_code}", "raw": txt}
            if r.status_code >= 400:
                return {"ok": False, "reason": f"HTTP {r.status_code}", "raw": str(data)[:300]}
            token = data.get("token") or data.get("access_token") or ""
            if not token:
                return {"ok": False, "reason": "토큰 필드 없음", "raw": str(data)[:300]}
            self._token = token
            self._token_expiry_epoch = time.time() + 23.5 * 3600
            return {"ok": True, "expires_dt": data.get("expires_dt", ""), "token_type": data.get("token_type", "")}
        except Exception as e:
            return {"ok": False, "reason": f"예외: {e}"}

    def ensure_token(self) -> bool:
        if self._token_alive():
            return True
        res = self.issue_token()
        if not res.get("ok"):
            logger.warning(f"[KIWOOM] 토큰 발급 실패: {res}")
            return False
        return True

    def health_check(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "reason": "KIWOOM_ENABLED=false"}
        if not (self.app_key and self.secret_key):
            return {"ok": False, "enabled": True, "reason": "APP_KEY/SECRET_KEY 미설정"}
        ok = self.ensure_token()
        return {
            "ok": ok,
            "enabled": True,
            "token_alive": self._token_alive(),
            "base_url": self.base_url,
            "ws_url": self.ws_url,
        }

    def _auth_headers(self, api_id: str, cont_yn: str = "N", next_key: str = "") -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
            "Content-Type": "application/json;charset=UTF-8",
        }

    @staticmethod
    def _to_num(v: Any) -> float:
        try:
            if v is None:
                return 0.0
            s = str(v).replace(",", "").strip()
            if not s:
                return 0.0
            return float(s)
        except Exception:
            return 0.0

    def fetch_foreign_flow(self, stock_code: str) -> dict[str, Any]:
        """주식외국인종목별매매동향(ka10008) 수집."""
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}
        self._ensure_tables()

        url = f"{self.base_url}/api/dostk/frgnistt"
        headers = self._auth_headers(api_id="ka10008")
        body = {"stk_cd": stock_code}

        try:
            r = requests.post(url, headers=headers, json=body, timeout=10)
            data = r.json() if r.content else {}
            if r.status_code >= 400:
                return {"ok": False, "reason": f"HTTP {r.status_code}", "raw": str(data)[:400]}

            rows = data.get("stk_frgnr") or []
            if not isinstance(rows, list):
                rows = []

            conn = connect_stock_db(timeout=30)
            try:
                saved = 0
                for row in rows:
                    dt = str(row.get("dt") or "")
                    if not dt:
                        continue
                    conn.execute(
                        """
                        INSERT INTO kiwoom_foreign_flow
                        (stock_code, dt, close_price, change_qty, poss_stock_cnt, weight, limit_exhaust_rate, raw_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(stock_code, dt) DO UPDATE SET
                            close_price=excluded.close_price,
                            change_qty=excluded.change_qty,
                            poss_stock_cnt=excluded.poss_stock_cnt,
                            weight=excluded.weight,
                            limit_exhaust_rate=excluded.limit_exhaust_rate,
                            raw_json=excluded.raw_json,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            stock_code,
                            dt,
                            self._to_num(row.get("close_pric")),
                            self._to_num(row.get("chg_qty")),
                            self._to_num(row.get("poss_stkcnt")),
                            self._to_num(row.get("wght")),
                            self._to_num(row.get("limit_exh_rt")),
                            json.dumps(row, ensure_ascii=False),
                        ),
                    )
                    saved += 1
                conn.commit()
            finally:
                conn.close()

            return {"ok": True, "saved": saved, "count": len(rows), "stock_code": stock_code}
        except Exception as e:
            return {"ok": False, "reason": f"예외: {e}"}

    # ── ka00190: 대량체결상위 (키움 순위정보) ─────────────────────────────
    def _ensure_large_trade_rank_table(self) -> None:
        """Raw-first storage for the Kiwoom large-trade ranking response."""
        conn = connect_stock_db(timeout=30)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kiwoom_large_trade_rank (
                    snapshot_at TEXT NOT NULL,
                    rank_type TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    rank_no INTEGER NOT NULL,
                    stock_code TEXT,
                    stock_name TEXT,
                    raw_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (snapshot_at, rank_type, market_type, rank_no)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kiwoom_large_trade_latest "
                "ON kiwoom_large_trade_rank(snapshot_at DESC, rank_type, market_type)"
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _first_list_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the first list-shaped response field without assuming its name."""
        for value in data.values():
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []

    def fetch_large_trade_rank(
        self,
        market_type: str = "000",
        rank_type: str = "buy",
        min_case_amount: str = "10",
        min_turnover: str = "0",
        stock_filter: str = "20",
    ) -> dict[str, Any]:
        """Collect ``ka00190`` as a Kiwoom-only attention input.

        The raw row is retained so a Kiwoom field-name change cannot silently
        create a misleading numeric strategy feature.
        """
        if market_type not in {"000", "001", "101"}:
            return {"ok": False, "reason": "market_type must be 000, 001, or 101"}
        if rank_type not in {"buy", "sell"}:
            return {"ok": False, "reason": "rank_type must be buy or sell"}
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}

        self._ensure_large_trade_rank_table()
        body = {
            "mrkt_tp": market_type,
            "sort_tp": "1" if rank_type == "buy" else "2",
            "case_pric_tp": str(min_case_amount),
            # The live ka00190 contract requires both filters. Omitting either
            # field returns 1511 and leaves the ranking table empty.
            "trde_qty_tp": str(min_turnover),
            "trde_prica_tp": str(min_turnover),
            "stk_tp": str(stock_filter),
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/dostk/rkinfo",
                headers=self._auth_headers(api_id="ka00190"),
                json=body,
                timeout=12,
            )
            data = response.json() if response.content else {}
        except Exception as exc:
            return {"ok": False, "reason": f"HTTP 오류: {exc}"}
        if response.status_code >= 400:
            return {"ok": False, "reason": f"HTTP {response.status_code}", "raw": str(data)[:500]}
        if not isinstance(data, dict):
            return {"ok": False, "reason": "unexpected_response", "raw": str(data)[:500]}
        if str(data.get("return_code", "0")) not in {"0", "", "None"}:
            return {
                "ok": False,
                "reason": str(data.get("return_msg") or "Kiwoom ranking request rejected"),
                "return_code": data.get("return_code"),
            }

        rows = self._first_list_payload(data)
        snapshot_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = connect_stock_db(timeout=30)
        try:
            for rank_no, row in enumerate(rows, 1):
                code = str(row.get("stk_cd") or row.get("stock_code") or row.get("code") or "").strip() or None
                name = str(row.get("stk_nm") or row.get("stock_name") or row.get("name") or "").strip() or None
                conn.execute(
                    """
                    INSERT INTO kiwoom_large_trade_rank
                    (snapshot_at, rank_type, market_type, rank_no, stock_code, stock_name, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (snapshot_at, rank_type, market_type, rank_no, code, name, json.dumps(row, ensure_ascii=False)),
                )
            conn.commit()
        finally:
            conn.close()
        return {
            "ok": True,
            "api_id": "ka00190",
            "snapshot_at": snapshot_at,
            "rank_type": rank_type,
            "market_type": market_type,
            "saved": len(rows),
            "request": body,
            "response_keys": list(data.keys()),
            "return_code": data.get("return_code"),
            "return_msg": data.get("return_msg"),
            "notice": "키움 대량체결 원본 순위입니다. 단독 매수 신호가 아니며 KIS/공식 수급 대조 후 연구·가상매매에만 사용합니다.",
        }

    def _extract_realtime_fields(self, payload: dict[str, Any]) -> dict[str, float]:
        # 키움 실시간 타입별 필드명이 다를 수 있어 다중 alias를 허용
        values = payload.get("values") if isinstance(payload.get("values"), dict) else {}

        def _pick(*keys: str) -> float:
            for k in keys:
                if k in payload and payload.get(k) not in (None, ""):
                    return self._to_num(payload.get(k))
                if k in values and values.get(k) not in (None, ""):
                    return self._to_num(values.get(k))
            return 0.0

        def _abs_pick(*keys: str) -> float:
            return abs(_pick(*keys))

        return {
            "last_price": _abs_pick("cur_prc", "current_price", "price", "close_pric", "10"),
            "change_price": _pick("pred_pre", "change_price", "11"),
            "change_rate": _pick("flu_rt", "change_rate", "12"),
            "trade_volume": _pick("trde_qty", "acml_trde_qty", "trade_volume", "13"),
            "trade_strength": _pick("cntr_str", "trade_strength", "chegyeol_strength", "228"),
            "bid1": _abs_pick("bid_pric1", "buy_hoga1", "bid1", "27"),
            "ask1": _abs_pick("ask_pric1", "sell_hoga1", "ask1", "28"),
            "bid_qty1": _pick("bid_qty1", "buy_qty1", "41", "61"),
            "ask_qty1": _pick("ask_qty1", "sell_qty1", "51", "71"),
        }

    def _save_realtime_snapshot(self, stock_code: str, source_type: str, payload: dict[str, Any]) -> None:
        self._ensure_tables()
        fields = self._extract_realtime_fields(payload)
        now = datetime.now()
        event_ts = now.strftime("%Y-%m-%d %H:%M:%S")
        minute_ts = now.strftime("%Y-%m-%d %H:%M:00")
        spread = 0.0
        if fields["ask1"] and fields["bid1"]:
            spread = fields["ask1"] - fields["bid1"]
        conn = connect_stock_db(timeout=30)
        try:
            # 최신 스냅샷(종목당 1행) 유지
            conn.execute(
                """
                INSERT INTO kiwoom_realtime_quote
                (stock_code,last_price,change_price,change_rate,trade_volume,trade_strength,bid1,ask1,bid_qty1,ask_qty1,source_type,raw_json,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(stock_code) DO UPDATE SET
                    last_price=CASE WHEN excluded.last_price!=0 THEN excluded.last_price ELSE kiwoom_realtime_quote.last_price END,
                    change_price=CASE WHEN excluded.change_price!=0 THEN excluded.change_price ELSE kiwoom_realtime_quote.change_price END,
                    change_rate=CASE WHEN excluded.change_rate!=0 THEN excluded.change_rate ELSE kiwoom_realtime_quote.change_rate END,
                    trade_volume=CASE WHEN excluded.trade_volume!=0 THEN excluded.trade_volume ELSE kiwoom_realtime_quote.trade_volume END,
                    trade_strength=CASE WHEN excluded.trade_strength!=0 THEN excluded.trade_strength ELSE kiwoom_realtime_quote.trade_strength END,
                    bid1=CASE WHEN excluded.bid1!=0 THEN excluded.bid1 ELSE kiwoom_realtime_quote.bid1 END,
                    ask1=CASE WHEN excluded.ask1!=0 THEN excluded.ask1 ELSE kiwoom_realtime_quote.ask1 END,
                    bid_qty1=CASE WHEN excluded.bid_qty1!=0 THEN excluded.bid_qty1 ELSE kiwoom_realtime_quote.bid_qty1 END,
                    ask_qty1=CASE WHEN excluded.ask_qty1!=0 THEN excluded.ask_qty1 ELSE kiwoom_realtime_quote.ask_qty1 END,
                    source_type=excluded.source_type,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    stock_code,
                    fields["last_price"],
                    fields["change_price"],
                    fields["change_rate"],
                    fields["trade_volume"],
                    fields["trade_strength"],
                    fields["bid1"],
                    fields["ask1"],
                    fields["bid_qty1"],
                    fields["ask_qty1"],
                    source_type,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            # 틱/스냅샷 히스토리 적재
            conn.execute(
                """
                INSERT INTO kiwoom_tick_history
                (stock_code, source_type, event_ts, last_price, change_price, change_rate,
                 trade_volume, trade_strength, bid1, ask1, bid_qty1, ask_qty1, spread, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    stock_code,
                    source_type,
                    event_ts,
                    fields["last_price"],
                    fields["change_price"],
                    fields["change_rate"],
                    fields["trade_volume"],
                    fields["trade_strength"],
                    fields["bid1"],
                    fields["ask1"],
                    fields["bid_qty1"],
                    fields["ask_qty1"],
                    spread,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            # 1분 집계(OHLC + 체결강도 통계)
            conn.execute(
                """
                INSERT INTO kiwoom_minute_snapshot
                (stock_code, minute_ts, open_price, high_price, low_price, close_price, sum_volume,
                 max_strength, min_strength, avg_strength, sample_count, best_bid1, best_ask1, spread_close, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(stock_code, minute_ts) DO UPDATE SET
                    high_price = CASE
                        WHEN excluded.high_price > COALESCE(kiwoom_minute_snapshot.high_price, excluded.high_price)
                        THEN excluded.high_price ELSE kiwoom_minute_snapshot.high_price END,
                    low_price = CASE
                        WHEN kiwoom_minute_snapshot.low_price IS NULL THEN excluded.low_price
                        WHEN excluded.low_price < kiwoom_minute_snapshot.low_price THEN excluded.low_price
                        ELSE kiwoom_minute_snapshot.low_price END,
                    close_price = excluded.close_price,
                    sum_volume = COALESCE(kiwoom_minute_snapshot.sum_volume, 0) + COALESCE(excluded.sum_volume, 0),
                    max_strength = CASE
                        WHEN kiwoom_minute_snapshot.max_strength IS NULL THEN excluded.max_strength
                        WHEN excluded.max_strength > kiwoom_minute_snapshot.max_strength THEN excluded.max_strength
                        ELSE kiwoom_minute_snapshot.max_strength END,
                    min_strength = CASE
                        WHEN kiwoom_minute_snapshot.min_strength IS NULL THEN excluded.min_strength
                        WHEN excluded.min_strength < kiwoom_minute_snapshot.min_strength THEN excluded.min_strength
                        ELSE kiwoom_minute_snapshot.min_strength END,
                    avg_strength = (
                        (COALESCE(kiwoom_minute_snapshot.avg_strength, 0) * COALESCE(kiwoom_minute_snapshot.sample_count, 0)
                         + COALESCE(excluded.avg_strength, 0))
                        / (COALESCE(kiwoom_minute_snapshot.sample_count, 0) + 1)
                    ),
                    sample_count = COALESCE(kiwoom_minute_snapshot.sample_count, 0) + 1,
                    best_bid1 = excluded.best_bid1,
                    best_ask1 = excluded.best_ask1,
                    spread_close = excluded.spread_close,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    stock_code,
                    minute_ts,
                    fields["last_price"],
                    fields["last_price"],
                    fields["last_price"],
                    fields["last_price"],
                    fields["trade_volume"],
                    fields["trade_strength"],
                    fields["trade_strength"],
                    fields["trade_strength"],
                    fields["bid1"],
                    fields["ask1"],
                    spread,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def _ws_collect_once(self, stock_codes: list[str], types: list[str], duration_sec: int = 15) -> dict[str, Any]:
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}

        try:
            import websockets
        except Exception as e:
            return {"ok": False, "reason": f"websockets 모듈 없음: {e}"}

        saved_count = 0
        started = time.time()

        close_reason = ""
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"trnm": "LOGIN", "token": self._token}, ensure_ascii=False))

            # LOGIN 응답 대기
            login_deadline = time.time() + 5
            while time.time() < login_deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if msg.get("trnm") == "LOGIN":
                    if int(msg.get("return_code", -1)) != 0:
                        return {"ok": False, "reason": f"ws_login_fail: {msg.get('return_msg')}"}
                    break

            reg = {
                "trnm": "REG",
                "grp_no": "1",
                "refresh": "1",
                "data": [{"item": stock_codes, "type": types}],
            }
            await ws.send(json.dumps(reg, ensure_ascii=False))

            while time.time() - started < duration_sec:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosedOK as e:
                    close_reason = f"closed_ok:{e.code}:{e.reason}"
                    break
                except websockets.ConnectionClosed as e:
                    close_reason = f"closed:{e.code}:{e.reason}"
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                trnm = str(msg.get("trnm") or "")
                if trnm == "PING":
                    await ws.send(json.dumps(msg, ensure_ascii=False))
                    continue
                if trnm == "REG":
                    if int(msg.get("return_code", 0) or 0) != 0:
                        close_reason = f"reg_fail:{msg.get('return_msg') or msg.get('return_code')}"
                        break
                    continue
                if trnm == "LOGIN":
                    continue

                data_rows = msg.get("data") or []
                if isinstance(data_rows, dict):
                    data_rows = [data_rows]

                for row in data_rows:
                    code = str(row.get("item") or row.get("stk_cd") or row.get("stock_code") or "")
                    if not code:
                        continue
                    self._save_realtime_snapshot(code, source_type=trnm or "WS", payload=row)
                    saved_count += 1

        result = {"ok": True, "saved": saved_count, "duration_sec": duration_sec, "codes": stock_codes, "types": types}
        if close_reason:
            result["close_reason"] = close_reason
        return result

    def collect_realtime_snapshot(self, stock_codes: list[str], types: list[str] | None = None, duration_sec: int = 15) -> dict[str, Any]:
        if not stock_codes:
            return {"ok": False, "reason": "stock_codes empty"}
        types = types or ["0A", "0B", "0C"]  # 주식체결/우선호가/호가잔량
        try:
            return asyncio.run(self._ws_collect_once(stock_codes=stock_codes, types=types, duration_sec=duration_sec))
        except RuntimeError:
            # 이미 이벤트 루프가 돌고 있는 환경 fallback
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._ws_collect_once(stock_codes=stock_codes, types=types, duration_sec=duration_sec))
            finally:
                loop.close()
        except Exception as e:
            return {"ok": False, "reason": f"예외: {e}"}

    # ── ka10059: 종목별 투자자 매매 동향 ─────────────────────────────────
    def _ensure_investor_tables(self) -> None:
        """kiwoom_investor_daily 테이블 생성."""
        conn = connect_stock_db(timeout=60)
        try:
            conn.execute("PRAGMA busy_timeout=60000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kiwoom_investor_daily (
                    stock_code TEXT NOT NULL,
                    dt TEXT NOT NULL,
                    close_pric REAL,
                    acc_trde_qty REAL,
                    acc_trde_prica REAL,
                    ind_invsr REAL,
                    frgnr_invsr REAL,
                    orgn REAL,
                    fnnc_invt REAL,
                    insrnc REAL,
                    invtrt REAL,
                    etc_fnnc REAL,
                    bank REAL,
                    penfnd_etc REAL,
                    samo_fund REAL,
                    natn REAL,
                    etc_corp REAL,
                    natfor REAL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (stock_code, dt)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kiwoom_inv_daily_dt ON kiwoom_investor_daily(dt)")
            conn.commit()
        finally:
            conn.close()

    def _ensure_condition_membership_table(self) -> None:
        """Persist account-owned Hero4 condition state in the primary database."""
        conn = connect_stock_db(timeout=30)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kiwoom_condition_membership (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    condition_name TEXT,
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'kiwoom_hero4',
                    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    raw_json TEXT,
                    UNIQUE(stock_code, condition_id, event_type, event_at)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kiwoom_condition_code_ts ON kiwoom_condition_membership(stock_code, event_at DESC)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kiwoom_condition_definition (
                    condition_id TEXT PRIMARY KEY,
                    condition_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kiwoom_condition_current (
                    stock_code TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    condition_name TEXT,
                    detected_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'kiwoom_condition_snapshot',
                    PRIMARY KEY (stock_code, condition_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kiwoom_condition_current_code ON kiwoom_condition_current(stock_code)")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _condition_stock_code(value: Any) -> str:
        """Normalize Kiwoom condition result codes such as A005930 to 005930."""
        code = str(value or "").strip().upper()
        if code.startswith("A"):
            code = code[1:]
        return code if len(code) == 6 and code.isdigit() else ""

    def _upsert_condition_definition(self, condition_id: str, condition_name: str | None) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = connect_stock_db(timeout=30)
        try:
            conn.execute(
                """
                INSERT INTO kiwoom_condition_definition
                (condition_id, condition_name, first_seen_at, last_seen_at, active)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(condition_id) DO UPDATE SET
                    condition_name=COALESCE(excluded.condition_name, kiwoom_condition_definition.condition_name),
                    last_seen_at=excluded.last_seen_at,
                    active=1
                """,
                (condition_id, condition_name, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def _reconcile_condition_snapshot(
        self,
        condition_id: str,
        condition_name: str | None,
        stock_codes: set[str],
    ) -> dict[str, int]:
        """Turn a full condition result into durable IN/OUT events."""
        self._ensure_condition_membership_table()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = connect_stock_db(timeout=30)
        try:
            existing = {
                str(row[0])
                for row in conn.execute(
                    "SELECT stock_code FROM kiwoom_condition_current WHERE condition_id=?",
                    (condition_id,),
                ).fetchall()
            }
            entered = stock_codes - existing
            exited = existing - stock_codes
            for code, event_type in [(code, "IN") for code in entered] + [(code, "OUT") for code in exited]:
                conn.execute(
                    """
                    INSERT INTO kiwoom_condition_membership
                    (stock_code, condition_id, condition_name, event_type, event_at, source, raw_json)
                    VALUES (?, ?, ?, ?, ?, 'kiwoom_condition_snapshot', ?)
                    ON CONFLICT(stock_code, condition_id, event_type, event_at) DO NOTHING
                    """,
                    (
                        code, condition_id, condition_name, event_type, now,
                        json.dumps({"snapshot_size": len(stock_codes)}, ensure_ascii=False),
                    ),
                )
            for code in stock_codes:
                conn.execute(
                    """
                    INSERT INTO kiwoom_condition_current
                    (stock_code, condition_id, condition_name, detected_at, source)
                    VALUES (?, ?, ?, ?, 'kiwoom_condition_snapshot')
                    ON CONFLICT(stock_code, condition_id) DO UPDATE SET
                        condition_name=excluded.condition_name,
                        detected_at=excluded.detected_at,
                        source=excluded.source
                    """,
                    (code, condition_id, condition_name, now),
                )
            if exited:
                conn.execute(
                    "DELETE FROM kiwoom_condition_current WHERE condition_id=? AND stock_code IN ({})".format(
                        ",".join("?" for _ in exited)
                    ),
                    (condition_id, *sorted(exited)),
                )
            conn.commit()
            return {"entered": len(entered), "exited": len(exited), "current": len(stock_codes)}
        finally:
            conn.close()

    async def _ws_condition_snapshot(self, max_conditions: int) -> dict[str, Any]:
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}
        try:
            import websockets
        except Exception as exc:
            return {"ok": False, "reason": f"websockets module unavailable: {exc}"}

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"trnm": "LOGIN", "token": self._token}, ensure_ascii=False))
            while True:
                login = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if login.get("trnm") == "PING":
                    await ws.send(json.dumps(login, ensure_ascii=False))
                    continue
                if login.get("trnm") == "LOGIN":
                    if int(login.get("return_code", -1)) != 0:
                        return {"ok": False, "reason": f"ws_login_fail: {login.get('return_msg')}"}
                    break

            await ws.send(json.dumps({"trnm": "CNSRLST"}, ensure_ascii=False))
            while True:
                response = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if response.get("trnm") == "PING":
                    await ws.send(json.dumps(response, ensure_ascii=False))
                    continue
                if response.get("trnm") == "CNSRLST":
                    if int(response.get("return_code", -1)) != 0:
                        return {"ok": False, "reason": f"condition_list_fail: {response.get('return_msg')}"}
                    raw_conditions = response.get("data") or []
                    break

            conditions: list[tuple[str, str | None]] = []
            for item in raw_conditions:
                if isinstance(item, (list, tuple)) and item:
                    condition_id = str(item[0]).strip()
                    name = str(item[1]).strip() if len(item) > 1 and item[1] else None
                elif isinstance(item, dict):
                    condition_id = str(item.get("seq") or item.get("condition_id") or "").strip()
                    name = item.get("name") or item.get("condition_name")
                else:
                    continue
                if condition_id:
                    conditions.append((condition_id, name))
            conditions = conditions[:max(1, max_conditions)]
            totals = {"entered": 0, "exited": 0, "current": 0}
            for condition_id, condition_name in conditions:
                self._upsert_condition_definition(condition_id, condition_name)
                codes: set[str] = set()
                cont_yn, next_key = "N", ""
                while True:
                    await ws.send(json.dumps({
                        "trnm": "CNSRREQ", "seq": condition_id, "search_type": "0",
                        "stex_tp": "K", "cont_yn": cont_yn, "next_key": next_key,
                    }, ensure_ascii=False))
                    while True:
                        result = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                        if result.get("trnm") == "PING":
                            await ws.send(json.dumps(result, ensure_ascii=False))
                            continue
                        if result.get("trnm") == "CNSRREQ" and str(result.get("seq") or "").strip() == condition_id:
                            break
                    if int(result.get("return_code", 0) or 0) != 0:
                        logger.warning("[키움조건검색] %s 조회 실패: %s", condition_id, result.get("return_msg"))
                        break
                    for row in result.get("data") or []:
                        raw_code = row.get("9001") if isinstance(row, dict) else row
                        code = self._condition_stock_code(raw_code)
                        if code:
                            codes.add(code)
                    cont_yn = str(result.get("cont_yn") or "N").upper()
                    next_key = str(result.get("next_key") or "")
                    if cont_yn != "Y" or not next_key:
                        break
                changes = self._reconcile_condition_snapshot(condition_id, condition_name, codes)
                for key in totals:
                    totals[key] += changes[key]
                # Kiwoom documents a 5 requests/sec limit for domestic lookup TRs.
                await asyncio.sleep(0.22)
            return {"ok": True, "condition_count": len(conditions), **totals}

    def collect_condition_snapshot(self, max_conditions: int | None = None) -> dict[str, Any]:
        """Collect all configured Hero4 conditions and record current hits plus changes."""
        self._ensure_condition_membership_table()
        limit = max_conditions if max_conditions is not None else int(
            getattr(config, "KIWOOM_CONDITION_SCAN_LIMIT", 100)
        )
        try:
            return asyncio.run(self._ws_condition_snapshot(max(1, limit)))
        except Exception as exc:
            logger.warning("[키움조건검색] 스냅샷 수집 오류: %s", exc)
            return {"ok": False, "reason": str(exc)}

    def _ensure_us_realtime_table(self) -> None:
        """Raw-first storage for Kiwoom US F5/FE/FT realtime messages."""
        conn = connect_stock_db(timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kiwoom_us_realtime_quote (
                    ticker TEXT PRIMARY KEY,
                    exchange_code TEXT,
                    source_types TEXT,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _save_us_realtime_message(self, ticker: str, exchange_code: str, source_type: str, payload: dict[str, Any]) -> None:
        if not ticker:
            return
        self._ensure_us_realtime_table()
        conn = connect_stock_db(timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("""
                INSERT INTO kiwoom_us_realtime_quote (ticker, exchange_code, source_types, raw_json, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ticker) DO UPDATE SET
                    exchange_code=excluded.exchange_code,
                    source_types=excluded.source_types,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
            """, (ticker.upper(), exchange_code, source_type, json.dumps(payload, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()

    async def _ws_collect_us_once(self, stock_items: list[dict[str, str]], types: list[str], duration_sec: int) -> dict[str, Any]:
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}
        try:
            import websockets
        except Exception as e:
            return {"ok": False, "reason": f"websockets 모듈 없음: {e}"}

        exchange_by_ticker = {str(row.get("jmcode") or "").upper(): str(row.get("stex_tp") or "") for row in stock_items}
        saved = 0
        started = time.time()
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"trnm": "LOGIN", "token": self._token}, ensure_ascii=False))
            while time.time() < started + 5:
                response = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if response.get("trnm") == "LOGIN":
                    if int(response.get("return_code", -1)) != 0:
                        return {"ok": False, "reason": f"ws_login_fail: {response.get('return_msg')}"}
                    break
            await ws.send(json.dumps({
                "trnm": "REG", "grp_no": "us_quotes", "refresh": "1",
                "data": [{"item": stock_items, "type": types}],
            }, ensure_ascii=False))
            while time.time() - started < duration_sec:
                try:
                    message = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                except asyncio.TimeoutError:
                    continue
                if message.get("trnm") == "PING":
                    await ws.send(json.dumps(message, ensure_ascii=False))
                    continue
                if message.get("trnm") in {"LOGIN", "REG"}:
                    continue
                source_type = str(message.get("trnm") or "REAL")
                rows = message.get("data") or []
                if isinstance(rows, dict):
                    rows = [rows]
                for row in rows:
                    item = row.get("item") or row.get("jmcode") or row.get("ticker") or ""
                    ticker = str(item.get("jmcode") if isinstance(item, dict) else item).upper()
                    if ticker:
                        self._save_us_realtime_message(ticker, exchange_by_ticker.get(ticker, ""), source_type, row)
                        saved += 1
        return {"ok": True, "saved": saved, "types": types, "items": stock_items}

    def collect_us_realtime_snapshot(self, stock_items: list[dict[str, str]], types: list[str] | None = None, duration_sec: int = 12) -> dict[str, Any]:
        """Collect US candidates/positions only; never subscribe the full universe."""
        valid = [
            {"jmcode": str(row.get("jmcode") or "").upper(), "stex_tp": str(row.get("stex_tp") or "").upper()}
            for row in stock_items
            if row.get("jmcode") and row.get("stex_tp")
        ]
        if not valid:
            return {"ok": False, "reason": "US ticker and exchange code are required"}
        try:
            return asyncio.run(self._ws_collect_us_once(valid, types or ["F5", "FE", "FT"], duration_sec))
        except Exception as e:
            return {"ok": False, "reason": f"예외: {e}"}

    def record_condition_membership_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Store normalized inclusion/removal events emitted by a Kiwoom condition feed."""
        self._ensure_condition_membership_table()
        saved = skipped = 0
        conn = connect_stock_db(timeout=30)
        try:
            for event in events:
                code = str(event.get("stock_code") or event.get("stk_cd") or "").strip()
                condition_id = str(event.get("condition_id") or event.get("seq") or "").strip()
                raw_type = str(event.get("event_type") or event.get("type") or "").upper().strip()
                event_type = "IN" if raw_type in {"IN", "I", "1", "ENTER", "편입"} else "OUT" if raw_type in {"OUT", "O", "0", "EXIT", "편출"} else ""
                event_at = str(event.get("event_at") or event.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                if not (len(code) == 6 and code.isdigit() and condition_id and event_type):
                    skipped += 1
                    continue
                conn.execute("""
                    INSERT INTO kiwoom_condition_membership
                    (stock_code, condition_id, condition_name, event_type, event_at, source, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stock_code, condition_id, event_type, event_at) DO NOTHING
                """, (
                    code, condition_id, event.get("condition_name") or event.get("name"),
                    event_type, event_at, event.get("source") or "kiwoom_hero4",
                    json.dumps(event, ensure_ascii=False),
                ))
                saved += 1
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "saved": saved, "skipped": skipped}

    def fetch_investor_by_stock(
        self,
        stock_code: str,
        base_dt: str | None = None,
        max_pages: int = 10,
        also_fill_price_history: bool = False,
    ) -> dict[str, Any]:
        """ka10059: 종목별 투자자 일별 매매 수집 (기관/외국인/개인 + 세부 기관 분류).

        Args:
            stock_code: 종목코드 (6자리)
            base_dt: 기준일 YYYYMMDD (None=오늘)
            max_pages: 최대 페이지 수 (1페이지=100행, max_pages=10 → ~1,000행)
            also_fill_price_history: price_history 투자자 컬럼도 COALESCE 업데이트 여부
        """
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}
        self._ensure_investor_tables()

        url = f"{self.base_url}/api/dostk/stkinfo"
        if not base_dt:
            base_dt = datetime.now().strftime("%Y%m%d")
        # ⚠️ 2026-06-21 버그 발견 → 2026-07-21 근본원인 확정+수정:
        # trde_tp="1"이 매수(buy-only)였던 것이 원인 확정. 005930 2026-07-20 KIS 검증값(price_history:
        # inst_net_buy_amt=-359076, frn_net_buy_amt=283920, ind_net_buy_amt=54386, 단위 백만원)과
        # trde_tp별 실측 대조 결과 trde_tp="0"(순매수)이 KIS와 거의 완전 일치(ind=54386 정확 일치,
        # orgn=-359076 정확 일치, frgnr=285162≈283920)로 확정. trde_tp="1"은 ind=1462450처럼 배수로
        # 커진 매수전용(buy-only) 값이었음. amt_qty_tp="1"=금액(백만원)/"2"=수량(주) 구분도 실측 확인.
        # 기존 4.5M행(trde_tp=1)은 재수집으로 덮어써야 함(scratch/backfill_kiwoom_investor_netbuy_20260721.py).
        body: dict[str, Any] = {
            "stk_cd": stock_code,
            "amt_qty_tp": "1",   # 금액(백만원) — price_history *_amt 컬럼과 동일 단위로 유지
            "trde_tp": "0",      # 순매수(net buy) — 2026-07-21 수정 (구 "1"=매수전용 버그)
            "unit_tp": "1",      # 1=주(shares)
            "dt": base_dt,
        }

        all_rows: list[dict] = []
        cont_yn = "N"
        next_key = ""

        for page in range(max_pages):
            headers = self._auth_headers(api_id="ka10059", cont_yn=cont_yn, next_key=next_key)
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=10)
                data = resp.json() if resp.content else {}
            except Exception as e:
                return {"ok": False, "reason": f"HTTP 오류: {e}", "saved": len(all_rows)}

            rows = data.get("stk_invsr_orgn") or []
            if not isinstance(rows, list):
                break
            all_rows.extend(rows)

            # 다음 페이지
            cont_yn = resp.headers.get("cont-yn", "N")
            next_key = resp.headers.get("next-key", "")
            if cont_yn != "Y" or not next_key:
                break
            if page < max_pages - 1:
                time.sleep(0.3)  # rate limit

        # DB 저장 (locked 시 최대 5회 재시도, 10초 간격)
        saved = 0
        for _attempt in range(5):
            try:
                conn = connect_stock_db(timeout=90)
                conn.execute("PRAGMA busy_timeout=90000")
                for row in all_rows:
                    dt_raw = str(row.get("dt") or "")
                    if not dt_raw or len(dt_raw) != 8:
                        continue
                    dt_iso = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:]}"

                    n = self._to_num
                    conn.execute("""
                        INSERT INTO kiwoom_investor_daily
                        (stock_code, dt, close_pric, acc_trde_qty, acc_trde_prica,
                         ind_invsr, frgnr_invsr, orgn, fnnc_invt, insrnc, invtrt,
                         etc_fnnc, bank, penfnd_etc, samo_fund, natn, etc_corp, natfor,
                         updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                        ON CONFLICT(stock_code, dt) DO UPDATE SET
                            close_pric=excluded.close_pric,
                            acc_trde_qty=excluded.acc_trde_qty,
                            acc_trde_prica=excluded.acc_trde_prica,
                            ind_invsr=excluded.ind_invsr,
                            frgnr_invsr=excluded.frgnr_invsr,
                            orgn=excluded.orgn,
                            fnnc_invt=excluded.fnnc_invt,
                            insrnc=excluded.insrnc,
                            invtrt=excluded.invtrt,
                            etc_fnnc=excluded.etc_fnnc,
                            bank=excluded.bank,
                            penfnd_etc=excluded.penfnd_etc,
                            samo_fund=excluded.samo_fund,
                            natn=excluded.natn,
                            etc_corp=excluded.etc_corp,
                            natfor=excluded.natfor,
                            updated_at=CURRENT_TIMESTAMP
                    """, (
                        stock_code, dt_iso,
                        n(row.get("cur_prc")), n(row.get("acc_trde_qty")), n(row.get("acc_trde_prica")),
                        n(row.get("ind_invsr")), n(row.get("frgnr_invsr")), n(row.get("orgn")),
                        n(row.get("fnnc_invt")), n(row.get("insrnc")), n(row.get("invtrt")),
                        n(row.get("etc_fnnc")), n(row.get("bank")), n(row.get("penfnd_etc")),
                        n(row.get("samo_fund")), n(row.get("natn")), n(row.get("etc_corp")),
                        n(row.get("natfor")),
                    ))

                    saved += 1

                conn.commit()
                break  # 성공 시 재시도 루프 탈출
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and _attempt < 4:
                    logger.info(f"[KIWOOM] DB locked {stock_code} — {_attempt+1}회 재시도 대기 10s")
                    saved = 0
                    time.sleep(10)
                    continue
                logger.warning(f"[KIWOOM] investor_daily save error {stock_code}: {e}")
                break
            except Exception as e:
                logger.warning(f"[KIWOOM] investor_daily save error {stock_code}: {e}")
                break
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        return {"ok": True, "stock_code": stock_code, "rows": len(all_rows), "saved": saved}

    # ── ka10001: 종목 기본정보 (PER/PBR/ROE/EPS/BPS/유동주식수) ──────────
    def fetch_stock_info(self, stock_code: str) -> dict[str, Any]:
        """ka10001: 종목 기본정보 수집 → stock_universe + stock_meta 업데이트."""
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}

        url = f"{self.base_url}/api/dostk/stkinfo"
        headers = self._auth_headers(api_id="ka10001")
        try:
            resp = requests.post(url, headers=headers, json={"stk_cd": stock_code}, timeout=10)
            data = resp.json() if resp.content else {}
        except Exception as e:
            return {"ok": False, "reason": f"HTTP 오류: {e}"}

        if data.get("return_code") != 0:
            return {"ok": False, "reason": data.get("return_msg", "API 오류")}

        n = self._to_num
        per  = n(data.get("per"))
        pbr  = n(data.get("pbr"))
        eps  = n(data.get("eps"))
        bps  = n(data.get("bps"))
        roe  = n(data.get("roe"))
        mac  = n(data.get("mac"))        # 시가총액 (억원)
        flo  = n(data.get("flo_stk"))   # 유동주식수 (천주)
        fex  = n(data.get("for_exh_rt"))# 외국인지분율 (%)
        sale = n(data.get("sale_amt"))   # 매출액 (억원)
        oper = n(data.get("bus_pro"))    # 영업이익 (억원)
        neti = n(data.get("cup_nga"))    # 당기순이익 (억원)

        conn = connect_stock_db(timeout=60)
        try:
            conn.execute("PRAGMA busy_timeout=60000")
            # stock_universe PER/PBR/ROE 업데이트 (0값은 NULL로)
            conn.execute("""
                UPDATE stock_universe SET
                    per=CASE WHEN ?!=0 THEN ? ELSE per END,
                    pbr=CASE WHEN ?!=0 THEN ? ELSE pbr END,
                    roe=CASE WHEN ?!=0 THEN ? ELSE roe END,
                    market_cap=CASE WHEN ?!=0 THEN ? ELSE market_cap END,
                    base_info_updated_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE stock_code=?
            """, (per,per, pbr,pbr, roe,roe, mac,mac, stock_code))

            # stock_meta 유동주식수 업데이트 (flo_stk is in 천주 → *1000 for actual shares)
            # 2026-08-25: 2026-04-08 배치에서 flo_stk 단위 가정(천주)이 일부 응답에서
            # 틀려 최대 1000배 오염된 사고가 있었음(89건 NULL 처리로 정리, CLAUDE.md 참조).
            # stock_universe.shares_issued(신뢰 가능한 발행주식총수) 대비 1.2배 넘게
            # 크면 유동주식수가 발행주식수를 초과하는 논리모순이므로 저장을 스킵한다.
            if flo > 0:
                float_shares_candidate = int(flo * 1000)
                shares_issued_row = conn.execute(
                    "SELECT shares_issued FROM stock_universe WHERE stock_code=?", (stock_code,)
                ).fetchone()
                shares_issued = shares_issued_row[0] if shares_issued_row else None
                if shares_issued and shares_issued > 0 and float_shares_candidate > shares_issued * 1.2:
                    logger.warning(
                        "[ka10001] %s 유동주식수 이상치 스킵: flo_stk*1000=%s > shares_issued*1.2=%s",
                        stock_code, float_shares_candidate, shares_issued * 1.2,
                    )
                else:
                    conn.execute("""
                        INSERT INTO stock_meta (stock_code, float_shares, shares_outstanding)
                        VALUES (?, ?, NULL)
                        ON CONFLICT(stock_code) DO UPDATE SET
                            float_shares=excluded.float_shares
                    """, (stock_code, float_shares_candidate))

            conn.commit()
        finally:
            conn.close()

        return {
            "ok": True, "stock_code": stock_code,
            "per": per, "pbr": pbr, "eps": eps, "bps": bps,
            "roe": roe, "mac": mac, "flo_stk": flo, "for_exh_rt": fex,
            "sale_amt": sale, "bus_pro": oper, "cup_nga": neti,
        }

    def bulk_investor_collect(
        self,
        stock_codes: list[str] | None = None,
        limit: int = 1000,
        max_pages: int = 3,
        sleep_secs: float = 0.4,
        skip_existing_latest: bool = False,
    ) -> dict[str, Any]:
        """ka10059 배치: 전종목 투자자 일별 수급 수집.

        Args:
            stock_codes: None이면 stock_universe 시가총액 상위 limit개
            limit: 최대 종목수
            max_pages: 페이지수 per 종목 (1page=100거래일)
            sleep_secs: 요청 간 대기
            skip_existing_latest: 최신 정상 가격일에 이미 수집된 종목 제외
        """
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}

        target_dt: str | None = None
        skipped_existing = 0
        if stock_codes is None:
            conn = connect_stock_db(timeout=30)
            try:
                rows = conn.execute(
                    """SELECT stock_code
                       FROM stock_universe
                       WHERE stock_code IS NOT NULL
                         AND LENGTH(stock_code) = 6
                         AND stock_code NOT LIKE '%^%'
                         AND stock_code NOT LIKE '%-F'
                         AND (
                           market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
                           OR market IS NULL
                         )
                         AND COALESCE(stock_type, '') NOT IN ('ETF', 'ETN')
                         AND COALESCE(secugrp_nm, '') NOT IN ('ETF', 'ETN')
                         AND COALESCE(stock_name, '') NOT LIKE '%ETF%'
                         AND COALESCE(stock_name, '') NOT LIKE '%ETN%'
                       GROUP BY stock_code
                       ORDER BY MAX(market_cap) DESC NULLS LAST
                       LIMIT ?""",
                    (limit,)
                ).fetchall()
                stock_codes = [r[0] for r in rows]

                if skip_existing_latest:
                    target = conn.execute(
                        """
                        SELECT date
                        FROM price_history
                        GROUP BY date
                        HAVING COUNT(DISTINCT stock_code) >= 2000
                        ORDER BY date DESC
                        LIMIT 1
                        """
                    ).fetchone()
                    if target and target[0]:
                        target_dt = str(target[0])[:10]
                        existing_rows = conn.execute(
                            """
                            SELECT stock_code
                            FROM kiwoom_investor_daily
                            WHERE dt = ?
                            """,
                            (target_dt,),
                        ).fetchall()
                        existing = {str(r[0]).zfill(6) for r in existing_rows}
                        before = len(stock_codes)
                        stock_codes = [code for code in stock_codes if str(code).zfill(6) not in existing]
                        skipped_existing = before - len(stock_codes)
            finally:
                conn.close()

        updated = 0
        failed = 0
        total_saved = 0
        for sc in stock_codes[:limit]:
            result = self.fetch_investor_by_stock(sc, max_pages=max_pages)
            if result.get("ok"):
                updated += 1
                total_saved += result.get("saved", 0)
            else:
                failed += 1
            time.sleep(sleep_secs)

        return {
            "ok": True,
            "updated": updated,
            "failed": failed,
            "total_saved": total_saved,
            "target_dt": target_dt,
            "skipped_existing": skipped_existing,
        }

    def bulk_update_stock_universe(
        self,
        stock_codes: list[str] | None = None,
        limit: int = 100,
        sleep_secs: float = 0.5,
    ) -> dict[str, Any]:
        """stock_universe 전체 또는 일부 종목에 대해 ka10001로 PER/PBR/ROE/유동주식수 갱신.

        Args:
            stock_codes: None이면 stock_universe 전종목
            limit: 최대 처리 종목수
            sleep_secs: 요청 간 대기 (rate limit)
        """
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}

        if stock_codes is None:
            conn = connect_stock_db(timeout=30)
            try:
                rows = conn.execute(
                    "SELECT stock_code FROM stock_universe ORDER BY market_cap DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                stock_codes = [r[0] for r in rows]
            finally:
                conn.close()

        updated = 0
        failed = 0
        for sc in stock_codes[:limit]:
            result = self.fetch_stock_info(sc)
            if result.get("ok"):
                updated += 1
            else:
                failed += 1
            time.sleep(sleep_secs)

        return {"ok": True, "updated": updated, "failed": failed, "total": len(stock_codes[:limit])}

    def fetch_credit_balance(self, stock_code: str, dt: str = None, qry_tp: str = "1",
                              max_pages: int = 1) -> dict:
        """신용거래동향 (ka10013) — 종목별 신용잔고 추이 수집.

        Args:
            stock_code: 종목코드
            dt: 기준일 YYYYMMDD (빈 값이면 최근)
            qry_tp: "1"=일별 100행
            max_pages: 페이지네이션 최대 횟수 (13 ≈ 5년치)
        """
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}
        self._ensure_tables()

        url = f"{self.base_url}/api/dostk/stkinfo"
        from datetime import datetime as _dt, timedelta as _td
        if not dt:
            dt = (_dt.now() - _td(days=1)).strftime("%Y%m%d")

        all_rows: list = []
        cont_yn = "N"
        next_key = ""
        for _page in range(max_pages):
            headers = self._auth_headers(api_id="ka10013", cont_yn=cont_yn, next_key=next_key)
            body = {"stk_cd": stock_code, "dt": dt, "qry_tp": qry_tp}
            try:
                r = requests.post(url, headers=headers, json=body, timeout=10)
                if r.status_code >= 400:
                    break
                data = r.json() if r.content else {}
                rows = data.get("crd_trde_trend") or data.get("stk_crdt_trde_tend") or []
                if not isinstance(rows, list) or not rows:
                    break
                all_rows.extend(rows)
                # 페이지네이션
                cont = r.headers.get("cont-yn", "N")
                next_key = r.headers.get("next-key", "")
                if cont != "Y" or not next_key:
                    break
                cont_yn = "Y"
                time.sleep(0.2)
            except Exception as e:
                logger.warning("[ka10013] %s page %d: %s", stock_code, _page, e)
                break

        if not all_rows:
            return {"ok": True, "saved": 0, "count": 0}

        try:
            conn = connect_stock_db(timeout=60)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kiwoom_credit_balance (
                    stock_code TEXT NOT NULL,
                    dt TEXT NOT NULL,
                    credit_balance_qty REAL,
                    credit_balance_amt REAL,
                    credit_ratio REAL,
                    new_credit_qty REAL,
                    repay_credit_qty REAL,
                    raw_json TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (stock_code, dt)
                )
            """)
            saved = 0
            for row in all_rows:
                dt_val = str(row.get("dt") or row.get("base_dt") or "")
                if not dt_val:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO kiwoom_credit_balance
                    (stock_code, dt, credit_balance_qty, credit_balance_amt, credit_ratio,
                     new_credit_qty, repay_credit_qty, raw_json, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                """, (
                    stock_code, dt_val,
                    self._to_num(row.get("remn") or row.get("crdt_bal_qty")),
                    self._to_num(row.get("amt")  or row.get("crdt_bal_amt")),
                    self._to_num(row.get("shr_rt") or row.get("crdt_rt")),
                    self._to_num(row.get("new")  or row.get("new_crdt_qty")),
                    self._to_num(row.get("rpya") or row.get("rpay_crdt_qty")),
                    json.dumps(row, ensure_ascii=False),
                ))
                saved += 1
            conn.commit()
            conn.close()
            return {"ok": True, "saved": saved, "count": len(all_rows)}
        except Exception as e:
            logger.warning("[ka10013] %s: %s", stock_code, e)
            return {"ok": False, "reason": str(e)}

    def bulk_collect_credit_balance(
        self,
        stock_codes: list[str] | None = None,
        limit: int = 500,
        sleep_secs: float = 0.4,
        qry_tp: str = "3",
    ) -> dict:
        """시총 상위 종목 신용잔고 일괄 수집.

        qry_tp: "1"=일별(100일), "2"=주별(100주), "3"=월별(100개월≈8년)
        기본값 "3"(월별)으로 한 번에 5년 이상 취득.
        """
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}
        if stock_codes is None:
            conn = connect_stock_db(timeout=30)
            stock_codes = [r[0] for r in conn.execute("""
                SELECT stock_code FROM stock_universe
                WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
                  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND market_cap IS NOT NULL
                ORDER BY market_cap DESC LIMIT ?
            """, (limit,)).fetchall()]
            conn.close()

        saved_total = 0
        for i, sc in enumerate(stock_codes):
            r = self.fetch_credit_balance(sc, qry_tp=qry_tp)
            if r.get("ok"):
                saved_total += r.get("saved", 0)
            if i % 100 == 0 and i > 0:
                logger.info("신용잔고 진행: %d/%d, 누적 %d행", i, len(stock_codes), saved_total)
            time.sleep(sleep_secs)
        return {"ok": True, "total_saved": saved_total, "stocks": len(stock_codes)}

    def bulk_collect_foreign_holding(
        self,
        stock_codes: list[str] | None = None,
        limit: int = 2200,
        sleep_secs: float = 0.35,
    ) -> dict:
        """시총 상위 종목 외국인 지분율 일괄 수집 (ka10008 → kiwoom_foreign_flow)."""
        if not self.ensure_token():
            return {"ok": False, "reason": "token_fail"}
        if stock_codes is None:
            conn = connect_stock_db(timeout=30)
            stock_codes = [r[0] for r in conn.execute("""
                SELECT stock_code FROM stock_universe
                WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
                  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND market_cap IS NOT NULL
                ORDER BY market_cap DESC LIMIT ?
            """, (limit,)).fetchall()]
            conn.close()

        saved_total = 0; errors = 0
        for i, sc in enumerate(stock_codes):
            r = self.fetch_foreign_flow(sc)
            if r.get("ok"):
                saved_total += r.get("saved", 0)
            else:
                errors += 1
            if i % 200 == 0 and i > 0:
                logger.info("외국인지분율 진행: %d/%d, 누적 %d행", i, len(stock_codes), saved_total)
            time.sleep(sleep_secs)
        return {"ok": True, "total_saved": saved_total, "stocks": len(stock_codes), "errors": errors}
