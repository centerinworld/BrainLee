"""
collectors/krx_investor_collector.py — KRX Data Marketplace 로그인 후 전종목 투자자 수급 수집

data.krx.co.kr 에 로그인 → OTP 발급 → CSV 다운로드 방식으로
KOSPI + KOSDAQ 전종목의 기관/외국인/개인 일별 순매수 금액(원)을 수집.

저장:
  price_history.inst_net_buy_amt  (기관 합계 순매수, 백만원)
  price_history.frn_net_buy_amt   (외국인 순매수, 백만원)
  price_history.ind_net_buy_amt   (개인 순매수, 백만원)

사용법:
  python3 -m collectors.krx_investor_collector           # 오늘 날짜
  python3 -m collectors.krx_investor_collector 20260416  # 특정 날짜
"""

from __future__ import annotations

import io
import logging
import sqlite3
import sys
import time
from datetime import date, datetime

import requests

logger = logging.getLogger(__name__)

DB_PATH  = "/Applications/stock_dashboard/stock.db"
BASE_URL = "https://data.krx.co.kr"

# 투자자 구분 → 컬럼 키 매핑 (CSV 헤더명)
# KRX CSV의 '순매수' 컬럼 (거래대금 기준, 원 단위)
_INST_COLS = [
    "금융투자", "보험", "투신(사모포함)", "사모",
    "은행", "기타금융", "연기금등", "기타법인",
]
_FRN_COLS  = ["외국인", "기타외국인"]
_IND_COLS  = ["개인"]


class KRXInvestorCollector:
    """KRX Data Marketplace 로그인 세션으로 전종목 수급 CSV를 다운로드."""

    def __init__(self, user_id: str, password: str):
        self.user_id  = user_id
        self.password = password
        self.sess     = self._make_session()
        self._logged_in = False

    # ── 세션 ─────────────────────────────────────────────────────
    @staticmethod
    def _make_session() -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        return s

    # ── 로그인 ────────────────────────────────────────────────────
    def login(self) -> bool:
        if self._logged_in:
            return True
        try:
            # 1) 메인 페이지 방문 (쿠키 초기화)
            self.sess.get(f"{BASE_URL}/main.cmd", timeout=15)

            # 2) 로그인 POST
            login_url = f"{BASE_URL}/contents/COM/Member/loginProcess.cmd"
            self.sess.headers["Referer"] = f"{BASE_URL}/contents/COM/Member/main.cmd"
            r = self.sess.post(
                login_url,
                data={"userId": self.user_id, "password": self.password},
                allow_redirects=True,
                timeout=15,
            )
            # 로그인 성공 여부: 응답 URL 또는 쿠키로 판단
            if "logout" in r.url.lower() or r.status_code != 200:
                # 일부 KRX 환경에서 리다이렉트 후 mypage 등으로 이동
                pass

            # 3) 간단한 인증 확인 — 마이페이지 접근
            chk = self.sess.get(
                f"{BASE_URL}/contents/COM/Member/myInfo.cmd", timeout=10
            )
            if "로그인" in chk.text and "myInfo" not in chk.url:
                logger.error("[KRX] 로그인 실패: 마이페이지 접근 불가")
                return False

            self._logged_in = True
            logger.info("[KRX] 로그인 성공")
            return True

        except Exception as e:
            logger.error(f"[KRX] 로그인 오류: {e}")
            return False

    # ── OTP 발급 + CSV 다운로드 ────────────────────────────────────
    def _download_csv(self, bld: str, params: dict) -> bytes | None:
        """OTP 발급 후 CSV bytes 반환."""
        try:
            self.sess.headers["Referer"] = (
                f"{BASE_URL}/contents/MDC/STAT/standard/MDCSTAT02402.cmd"
            )
            otp_data = {"bld": bld, "csvxls_isNo": "true", **params}
            otp = self.sess.post(
                f"{BASE_URL}/comm/fileDn/GenerateOTP/generate.cmd",
                data=otp_data,
                timeout=15,
            ).text.strip()

            if not otp or "<html" in otp.lower():
                logger.warning(f"[KRX] OTP 발급 실패 (bld={bld})")
                return None

            r = self.sess.post(
                f"{BASE_URL}/comm/fileDn/download_csv.cmd",
                data={"code": otp},
                timeout=30,
            )
            if b"<html" in r.content[:200]:
                logger.warning(f"[KRX] CSV 다운로드 실패 (HTML 반환, bld={bld})")
                return None

            return r.content
        except Exception as e:
            logger.error(f"[KRX] CSV 다운로드 오류: {e}")
            return None

    # ── CSV 파싱 ──────────────────────────────────────────────────
    @staticmethod
    def _parse_investor_csv(raw: bytes) -> list[dict]:
        """KRX 투자자별 거래실적 CSV → 종목별 순매수 금액(백만원) 리스트."""
        results = []
        for enc in ("euc-kr", "cp949", "utf-8-sig", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                continue
        else:
            logger.error("[KRX] CSV 인코딩 판별 실패")
            return results

        reader = io.StringIO(text)
        lines  = reader.readlines()

        # 헤더 행 찾기 (종목코드 포함 행)
        header_idx = None
        for i, line in enumerate(lines):
            if "종목코드" in line and "종목명" in line:
                header_idx = i
                break
        if header_idx is None:
            logger.warning("[KRX] CSV 헤더를 찾지 못했습니다")
            return results

        import csv
        reader2 = csv.DictReader(
            io.StringIO("".join(lines[header_idx:]))
        )

        for row in reader2:
            code = row.get("종목코드", "").strip().strip('"')
            if not code or len(code) != 6 or not code.isdigit():
                continue

            def _amt(col_name: str) -> float:
                """거래대금 순매수 컬럼 → float (원 단위 → 백만원)."""
                # CSV 컬럼명에 '순매수' 접미사 붙는 경우 처리
                for k in [col_name, col_name + "_순매수", col_name + " 순매수"]:
                    v = row.get(k, "")
                    if v:
                        try:
                            return float(v.replace(",", "").replace('"', "")) / 1_000_000
                        except ValueError:
                            pass
                return 0.0

            # 기관 = 금융투자 + 보험 + 투신 + 사모 + 은행 + 기타금융 + 연기금 + 기타법인
            inst_amt = sum(_amt(c) for c in _INST_COLS)
            frn_amt  = sum(_amt(c) for c in _FRN_COLS)
            ind_amt  = sum(_amt(c) for c in _IND_COLS)

            results.append({
                "stock_code": code,
                "inst_net_buy_amt": round(inst_amt),
                "frn_net_buy_amt":  round(frn_amt),
                "ind_net_buy_amt":  round(ind_amt),
            })

        return results

    # ── DB 저장 ───────────────────────────────────────────────────
    @staticmethod
    def _save(conn: sqlite3.Connection, rows: list[dict], trade_date: str) -> int:
        cur     = conn.cursor()
        saved   = 0
        db_date = trade_date.replace("-", "")  # YYYYMMDD
        if len(db_date) == 8:
            db_date = f"{db_date[:4]}-{db_date[4:6]}-{db_date[6:]}"  # YYYY-MM-DD

        for r in rows:
            code = r["stock_code"]
            # 해당 날짜에 price_history 행이 있는지 확인
            exists = cur.execute(
                "SELECT rowid FROM price_history WHERE stock_code=? AND substr(date,1,10)=? LIMIT 1",
                (code, db_date),
            ).fetchone()

            if exists:
                cur.execute(
                    """UPDATE price_history
                       SET inst_net_buy_amt=?, frn_net_buy_amt=?, ind_net_buy_amt=?
                       WHERE stock_code=? AND substr(date,1,10)=?""",
                    (
                        r["inst_net_buy_amt"],
                        r["frn_net_buy_amt"],
                        r["ind_net_buy_amt"],
                        code, db_date,
                    ),
                )
            else:
                # price_history에 없으면 새 행 삽입 (종가 등은 0 — KRX OHLCV 잡이 채움)
                cur.execute(
                    """INSERT OR IGNORE INTO price_history
                       (stock_code, date, open, high, low, close, volume,
                        inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt)
                       VALUES (?,?,0,0,0,0,0,?,?,?)""",
                    (
                        code, db_date,
                        r["inst_net_buy_amt"],
                        r["frn_net_buy_amt"],
                        r["ind_net_buy_amt"],
                    ),
                )
            saved += 1

        conn.commit()
        return saved

    # ── 메인 수집 ─────────────────────────────────────────────────
    def collect(self, trade_date: str | None = None) -> dict:
        """
        trade_date: 'YYYY-MM-DD' 또는 'YYYYMMDD'. None이면 오늘.
        반환: {"date": ..., "kospi": n, "kosdaq": n, "total": n}
        """
        if trade_date is None:
            trade_date = date.today().strftime("%Y-%m-%d")

        td = trade_date.replace("-", "")  # YYYYMMDD for API

        if not self.login():
            return {"error": "로그인 실패"}

        conn   = sqlite3.connect(DB_PATH)
        total  = 0
        counts = {}

        try:
            for mkt_label, bld, mkt_id in [
                ("KOSPI",  "dbms/MDC/STAT/standard/MDCSTAT02402", "STK"),
                ("KOSDAQ", "dbms/MDC/STAT/standard/MDCSTAT02502", "KSQ"),
            ]:
                logger.info(f"[KRX] {mkt_label} 투자자 수급 다운로드 중... ({trade_date})")
                raw = self._download_csv(bld, {
                    "mktId":   mkt_id,
                    "strtDd":  td,
                    "endDd":   td,
                    "share":   "1",
                    "money":   "1",
                })

                if not raw:
                    logger.warning(f"[KRX] {mkt_label} CSV 없음")
                    counts[mkt_label] = 0
                    continue

                rows = self._parse_investor_csv(raw)
                n    = self._save(conn, rows, trade_date)
                counts[mkt_label] = n
                total += n
                logger.info(f"[KRX] {mkt_label} {n}건 저장 완료")
                time.sleep(1.0)  # rate limit

        finally:
            conn.close()

        return {"date": trade_date, **counts, "total": total}


# ── 스케줄러에서 호출하는 함수 ────────────────────────────────────
def collect_krx_investor_daily(trade_date: str | None = None) -> dict:
    """scheduler.py에서 직접 호출."""
    import config as _cfg
    user_id  = getattr(_cfg, "KRX_DATA_ID",  "") or ""
    password = getattr(_cfg, "KRX_DATA_PW",  "") or ""
    if not user_id or not password:
        logger.error("[KRX] KRX_DATA_ID / KRX_DATA_PW 환경변수 없음")
        return {"error": "no credentials"}
    col = KRXInvestorCollector(user_id, password)
    return col.collect(trade_date)


# ── 직접 실행 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    td = sys.argv[1] if len(sys.argv) > 1 else None
    result = collect_krx_investor_daily(td)
    print(result)
