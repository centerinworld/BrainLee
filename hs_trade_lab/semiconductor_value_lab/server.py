from __future__ import annotations

import json
import sqlite3
import subprocess
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_DIR = Path("/Applications/stock_dashboard/hs_trade_lab/semiconductor_value_lab")
STATIC_DIR = PROJECT_DIR / "static"
DB_PATH = PROJECT_DIR / "data" / "semiconductor_value_lab.db"
REBUILD_SCRIPT = PROJECT_DIR / "scripts" / "rebuild_cache.py"


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def _json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json({"message": "ok"})
        if parsed.path == "/api/meta":
            return self._meta()
        if parsed.path == "/api/summary":
            return self._summary()
        if parsed.path == "/api/stocks":
            return self._stocks(parsed.query)
        if parsed.path == "/api/watchlist":
            return self._watchlist()
        if parsed.path.startswith("/api/stock/"):
            stock_code = parsed.path.split("/")[-1]
            return self._stock_detail(stock_code)
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/watchlist":
            return self._save_watchlist()
        if parsed.path == "/api/reference-dates":
            return self._update_reference_dates()
        if parsed.path == "/api/rebuild":
            return self._rebuild_cache()
        self._json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/watchlist/"):
            stock_code = parsed.path.split("/")[-1]
            conn = db_conn()
            conn.execute("DELETE FROM watchlist WHERE stock_code = ?", (stock_code,))
            conn.commit()
            conn.close()
            return self._json({"message": "deleted"})
        self._json({"error": "not found"}, 404)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        payload = self.rfile.read(length) if length else b"{}"
        return json.loads(payload.decode("utf-8"))

    def _meta(self) -> None:
        conn = db_conn()
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        conn.close()
        self._json(meta)

    def _summary(self) -> None:
        conn = db_conn()
        rows = [dict(row) for row in conn.execute("SELECT * FROM sector_summary ORDER BY sector_order")]
        conn.close()
        self._json({"items": rows})

    def _stocks(self, query: str) -> None:
        params = parse_qs(query)
        lv1 = (params.get("lv1") or [""])[0]
        watch_only = (params.get("watch_only") or ["false"])[0] == "true"
        conn = db_conn()
        sql = "SELECT * FROM stock_cache WHERE 1=1"
        args: list[object] = []
        if lv1:
            sql += " AND lv1 = ?"
            args.append(lv1)
        if watch_only:
            sql += " AND stock_code IN (SELECT stock_code FROM watchlist)"
        sql += " ORDER BY lv1, stock_name"
        rows = [dict(row) for row in conn.execute(sql, args)]
        conn.close()
        self._json({"items": rows})

    def _watchlist(self) -> None:
        conn = db_conn()
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT w.stock_code, w.stock_name, w.lv2_override, w.customer_override, w.memo,
                       s.lv1, s.lv2, s.customer, s.main_business, s.current_price,
                       s.ref_a_change_pct, s.ref_b_change_pct, s.ref_c_change_pct,
                       s.annual_yoy, s.quarter_qoq
                  FROM watchlist w
                  LEFT JOIN stock_cache s ON s.stock_code = w.stock_code
                 ORDER BY w.stock_name
                """
            )
        ]
        conn.close()
        self._json({"items": rows})

    def _stock_detail(self, stock_code: str) -> None:
        conn = db_conn()
        stock = conn.execute("SELECT * FROM stock_cache WHERE stock_code = ?", (stock_code,)).fetchone()
        if not stock:
            conn.close()
            return self._json({"error": "not found"}, 404)
        yearly = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM yearly_cache WHERE stock_code = ? ORDER BY fiscal_year, item_type",
                (stock_code,),
            )
        ]
        quarterly = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM quarterly_cache WHERE stock_code = ? ORDER BY sort_year, sort_quarter, item_type",
                (stock_code,),
            )
        ]
        conn.close()
        self._json({"stock": dict(stock), "yearly": yearly, "quarterly": quarterly})

    def _save_watchlist(self) -> None:
        payload = self._read_json()
        conn = db_conn()
        conn.execute(
            """
            INSERT INTO watchlist(stock_code, stock_name, lv2_override, customer_override, memo)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                lv2_override = excluded.lv2_override,
                customer_override = excluded.customer_override,
                memo = excluded.memo
            """,
            (
                payload.get("stock_code"),
                payload.get("stock_name"),
                payload.get("lv2_override", ""),
                payload.get("customer_override", ""),
                payload.get("memo", ""),
            ),
        )
        conn.commit()
        conn.close()
        self._json({"message": "saved"})

    def _update_reference_dates(self) -> None:
        payload = self._read_json()
        conn = db_conn()
        for key in ("ref_a", "ref_b", "ref_c"):
            value = payload.get(key)
            if value:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
        conn.commit()
        conn.close()
        self._json({"message": "saved", "note": "기준일 저장 완료. 재계산은 재빌드 버튼으로 실행하세요."})

    def _rebuild_cache(self) -> None:
        result = subprocess.run(
            ["python3", str(REBUILD_SCRIPT)],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return self._json(
                {"message": "rebuild failed", "stderr": result.stderr[-4000:], "stdout": result.stdout[-2000:]},
                500,
            )
        self._json({"message": "rebuilt", "stdout": result.stdout[-2000:]})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8021), Handler)
    print("Semiconductor Value Lab: http://127.0.0.1:8021")
    server.serve_forever()


if __name__ == "__main__":
    main()
