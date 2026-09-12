import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_seoul_timezone():
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9))


CORP_CODES = {
    "00309503": "한국항공우주산업",
    "00126566": "한화에어로스페이스",
    "00503668": "LIG넥스원"
}

# 한화/LIG 단일판매공급계약 알림 기준 (원)
CONTRACT_MIN_AMOUNT_WON = 1_000_000_000_000  # 1조원


def _fetch_contract_amount(dart_key: str, rcept_no: str) -> int | None:
    """
    DART 단일판매·공급계약체결 API로 계약금액(원)을 조회.
    조회 실패 시 None 반환.
    """
    url = "https://opendart.fss.or.kr/api/snglAcqsDspsRlstn.json"
    params = {"crtfc_key": dart_key, "rcept_no": rcept_no}
    try:
        req = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("status") != "000":
            return None

        rows = data.get("list", [])
        if not rows:
            return None

        # 계약금액 필드 탐색: DART API는 'deal_amnt' 또는 'slng_atr_amt' 사용
        for row in rows:
            for field in ("deal_amnt", "slng_atr_amt", "contract_amnt", "tot_deal_amnt"):
                raw = row.get(field, "")
                if raw:
                    # 쉼표·공백·'원' 제거 후 정수 변환
                    cleaned = str(raw).replace(",", "").replace(" ", "").replace("원", "").strip()
                    if cleaned.lstrip("-").isdigit():
                        return int(cleaned)
        return None
    except Exception:
        return None


def is_contract_disclosure(report_nm_clean: str) -> bool:
    """단일판매/공급계약 공시 여부"""
    return "단일판매공급계약" in report_nm_clean or "공급계약체결" in report_nm_clean


def should_send_disclosure(corp_code: str, report_nm: str) -> bool:
    """
    공시 종목코드 + 공시명만으로 1차 필터링.
    계약금액이 필요한 경우(한화/LIG 단일판매공급계약)는 별도 check_and_send_disclosures에서 처리.
    """
    report_nm_clean = report_nm.replace(" ", "").replace("·", "").replace("•", "").replace("ㆍ", "")

    # 임원 및 대표이사 개인의 주식 소유 현황 공시는 무조건 배제 (스팸 방지)
    if "임원주요주주특정증권" in report_nm_clean or "소유상황보고서" in report_nm_clean:
        return False

    # 1. 한국항공우주산업 (KAI): 나머지 모든 공시 전송
    if corp_code == "00309503":
        return True

    # 2. 한화에어로스페이스 및 LIG넥스원
    # 대량보유 공시(5% 룰) 또는 지분 구조 변동
    if "대량보유" in report_nm_clean or "지분변동" in report_nm_clean:
        return True

    # 대표이사 변경
    if "대표이사변경" in report_nm_clean:
        return True

    # 단일판매/공급계약 — 금액 기준은 check_and_send_disclosures에서 별도 검증
    if is_contract_disclosure(report_nm_clean):
        return True

    # 유상증자 / 전환사채(CB) / 신주인수권부사채(BW) 발행
    if any(kw in report_nm_clean for kw in [
        "유상증자", "전환사채", "신주인수권부사채", "교환사채", "신주발행"
    ]):
        return True

    # 주요 투자 결정 / M&A / 영업양수도 / 합병
    if any(kw in report_nm_clean for kw in [
        "주요사항보고", "합병", "분할", "영업양수", "영업양도",
        "주식취득", "주식처분", "종속회사편입", "자회사편입"
    ]):
        return True

    return False


def send_telegram_msg(token: str, chat_id: str, text: str) -> None:
    _url = f"https://api.telegram.org/bot{token}/sendMessage"
    _payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }).encode("utf-8")
    _req = urllib.request.Request(_url, data=_payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(_req, timeout=15):
        pass


def _fmt_amount(won: int | None) -> str:
    """원 단위를 조/억 단위 문자열로 변환. 예: 1_500_000_000_000 → '1조 5,000억원'"""
    if won is None:
        return ""
    jo = won // 1_000_000_000_000
    eok = (won % 1_000_000_000_000) // 100_000_000
    if jo > 0 and eok > 0:
        return f"{jo:,}조 {eok:,}억원"
    elif jo > 0:
        return f"{jo:,}조원"
    elif eok > 0:
        return f"{eok:,}억원"
    else:
        return f"{won:,}원"


def check_and_send_disclosures(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    dart_key = None
    bot_token = None
    chat_id = None

    for row in conn.execute(
        "SELECT key, value FROM app_settings WHERE key IN ('dart_api_key', 'telegram_bot_token', 'telegram_chat_id')"
    ).fetchall():
        if row[0] == "dart_api_key":
            dart_key = row[1]
        elif row[0] == "telegram_bot_token":
            bot_token = row[1]
        elif row[0] == "telegram_chat_id":
            chat_id = row[1]

    if not dart_key or not bot_token or not chat_id:
        print("DART disclosure checker skipped: missing settings.")
        return

    seoul_tz = get_seoul_timezone()
    now_seoul = datetime.now(seoul_tz)
    today_str = now_seoul.strftime("%Y%m%d")
    yesterday_str = (now_seoul - timedelta(days=1)).strftime("%Y%m%d")

    for corp_code, corp_name in CORP_CODES.items():
        try:
            url = "https://opendart.fss.or.kr/api/list.json"
            params = {
                "crtfc_key": dart_key,
                "corp_code": corp_code,
                "bgn_de": yesterday_str,
                "end_de": today_str,
                "page_count": "100"
            }
            query_str = urllib.parse.urlencode(params)
            req_url = f"{url}?{query_str}"

            req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("status") != "000":
                if data.get("status") == "013":  # no results
                    continue
                print(f"DART API error for {corp_name}: {data.get('message')}")
                continue

            disclosures = data.get("list", [])
            for disc in disclosures:
                rcept_no = disc.get("rcept_no")
                report_nm = disc.get("report_nm", "")
                rcept_dt = disc.get("rcept_dt", "")

                if not rcept_no or not report_nm:
                    continue

                # 중복 체크
                dup = conn.execute("SELECT 1 FROM disclosure_logs WHERE rcept_no = ?", (rcept_no,)).fetchone()
                if dup:
                    continue

                # 1차 필터 (공시명 기반)
                if not should_send_disclosure(corp_code, report_nm):
                    continue

                report_nm_clean = report_nm.replace(" ", "").replace("·", "").replace("•", "").replace("ㆍ", "")

                # 한화/LIG 단일판매공급계약: 1조원 이상만 발송
                amount_won = None
                if corp_code != "00309503" and is_contract_disclosure(report_nm_clean):
                    amount_won = _fetch_contract_amount(dart_key, rcept_no)
                    if amount_won is not None and amount_won < CONTRACT_MIN_AMOUNT_WON:
                        # 1조 미만 — DB에 skip 기록 후 다음
                        conn.execute(
                            """
                            INSERT INTO disclosure_logs (rcept_no, corp_code, corp_name, report_nm, rcept_dt)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (rcept_no, corp_code, corp_name, f"[SKIP 금액미달] {report_nm}", rcept_dt)
                        )
                        conn.commit()
                        print(f"[SKIP] {corp_name} 단일판매공급계약 {_fmt_amount(amount_won)} < 1조원: {report_nm}")
                        continue
                    # amount_won이 None이면 금액 조회 실패 → 안전하게 발송

                # 메시지 구성
                amount_line = ""
                if amount_won is not None:
                    amount_line = f"\n💰 <b>계약금액:</b> {_fmt_amount(amount_won)}"

                formatted_msg = (
                    f"🔔 <b>[DART 공시 알림] {corp_name}</b>\n\n"
                    f"📝 <b>공시명:</b> {report_nm}"
                    f"{amount_line}\n"
                    f"📅 <b>공시일자:</b> {rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}\n\n"
                    f"🔗 <a href='https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}'>DART 공시 상세 보기</a>"
                )

                if dry_run:
                    print(f"--- [DRY RUN] DART Disclosure ---")
                    print(formatted_msg)
                    print("---------------------------------")
                else:
                    send_telegram_msg(bot_token, chat_id, formatted_msg)

                # 발송 기록
                conn.execute(
                    """
                    INSERT INTO disclosure_logs (rcept_no, corp_code, corp_name, report_nm, rcept_dt)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (rcept_no, corp_code, corp_name, report_nm, rcept_dt)
                )
                conn.commit()

        except Exception as e:
            print(f"Failed to check disclosures for {corp_name}: {e}")
