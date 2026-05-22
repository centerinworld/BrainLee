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
from db_utils import STOCK_DB_PATH

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
        conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
        try:
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
            conn.commit()
        finally:
            conn.close()

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
            data = r.json() if r.content else {}
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

            conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
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

    def _extract_realtime_fields(self, payload: dict[str, Any]) -> dict[str, float]:
        # 키움 실시간 타입별 필드명이 다를 수 있어 다중 alias를 허용
        def _pick(*keys: str) -> float:
            for k in keys:
                if k in payload and payload.get(k) not in (None, ""):
                    return self._to_num(payload.get(k))
            return 0.0

        return {
            "last_price": _pick("cur_prc", "current_price", "price", "close_pric"),
            "change_price": _pick("pred_pre", "change_price"),
            "change_rate": _pick("flu_rt", "change_rate"),
            "trade_volume": _pick("trde_qty", "acml_trde_qty", "trade_volume"),
            "trade_strength": _pick("cntr_str", "trade_strength", "chegyeol_strength"),
            "bid1": _pick("bid_pric1", "buy_hoga1", "bid1"),
            "ask1": _pick("ask_pric1", "sell_hoga1", "ask1"),
            "bid_qty1": _pick("bid_qty1", "buy_qty1"),
            "ask_qty1": _pick("ask_qty1", "sell_qty1"),
        }

    def _save_realtime_snapshot(self, stock_code: str, source_type: str, payload: dict[str, Any]) -> None:
        self._ensure_tables()
        fields = self._extract_realtime_fields(payload)
        conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
        try:
            conn.execute(
                """
                INSERT INTO kiwoom_realtime_quote
                (stock_code,last_price,change_price,change_rate,trade_volume,trade_strength,bid1,ask1,bid_qty1,ask_qty1,source_type,raw_json,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(stock_code) DO UPDATE SET
                    last_price=CASE WHEN excluded.last_price!=0 THEN excluded.last_price ELSE kiwoom_realtime_quote.last_price END,
                    change_price=excluded.change_price,
                    change_rate=excluded.change_rate,
                    trade_volume=excluded.trade_volume,
                    trade_strength=CASE WHEN excluded.trade_strength!=0 THEN excluded.trade_strength ELSE kiwoom_realtime_quote.trade_strength END,
                    bid1=CASE WHEN excluded.bid1!=0 THEN excluded.bid1 ELSE kiwoom_realtime_quote.bid1 END,
                    ask1=CASE WHEN excluded.ask1!=0 THEN excluded.ask1 ELSE kiwoom_realtime_quote.ask1 END,
                    bid_qty1=excluded.bid_qty1,
                    ask_qty1=excluded.ask_qty1,
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
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                trnm = str(msg.get("trnm") or "")
                if trnm == "PING":
                    await ws.send(json.dumps(msg, ensure_ascii=False))
                    continue
                if trnm in ("REG", "LOGIN"):
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

        return {"ok": True, "saved": saved_count, "duration_sec": duration_sec, "codes": stock_codes, "types": types}

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
