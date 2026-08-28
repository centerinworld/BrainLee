"""
telegram_collector.py — 텔레그램 채널 보고서 자동 수집

기능:
  - 설정된 텔레그램 채널에서 PDF/문서 파일 다운로드
  - 종목코드 자동 추출 (파일명 기반)
  - DB 저장 (report_files 테이블)
  - 중복 방지 (message_id 기준)
  - 매일 08:30 자동 실행 (cron)

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  pip install telethon

  # 최초 1회 인증 (전화번호 인증)
  python3 telegram_collector.py --auth

  # 수동 수집
  python3 telegram_collector.py

  # cron 등록
  python3 telegram_collector.py --setup-cron

  # 특정 채널만
  python3 telegram_collector.py --channel @채널명
"""

import sys, os, asyncio, sqlite3, logging, logging.handlers, argparse, re, time
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, "/Applications/stock_dashboard")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "/Applications/stock_dashboard/telegram_collector.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=2,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

DB_PATH      = Path("/Applications/stock_dashboard/stock.db")
REPORT_DIR   = Path("/Applications/stock_dashboard/reports")
REPORT_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════════
# 설정 로드
# ══════════════════════════════════════════════════════════════════
def get_config():
    try:
        import config as _cfg
        return {
            "api_id":   getattr(_cfg, "TELEGRAM_API_ID",   os.getenv("TELEGRAM_API_ID", "")),
            "api_hash": getattr(_cfg, "TELEGRAM_API_HASH", os.getenv("TELEGRAM_API_HASH", "")),
            "phone":    os.getenv("TELEGRAM_PHONE") or getattr(_cfg, "TELEGRAM_PHONE", ""),
            "session":  os.getenv("TELEGRAM_SESSION_PATH", "/Applications/stock_dashboard/telegram_session"),
        }
    except Exception:
        return {
            "api_id":   os.getenv("TELEGRAM_API_ID", ""),
            "api_hash": os.getenv("TELEGRAM_API_HASH", ""),
            "phone":    os.getenv("TELEGRAM_PHONE", ""),
            "session":  os.getenv("TELEGRAM_SESSION_PATH", "/Applications/stock_dashboard/telegram_session"),
        }


# ══════════════════════════════════════════════════════════════════
# DB 초기화
# ══════════════════════════════════════════════════════════════════
def init_db(conn):
    conn.executescript("""
    -- 텔레그램 채널 설정
    CREATE TABLE IF NOT EXISTS telegram_channels (
        id          INTEGER PRIMARY KEY,
        channel_id  TEXT NOT NULL UNIQUE,   -- @채널명 또는 채널 ID
        channel_name TEXT,                  -- 표시 이름
        is_active   INTEGER DEFAULT 1,
        last_sync   TEXT,                   -- 마지막 수집 일시
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    -- 수집된 보고서 파일
    CREATE TABLE IF NOT EXISTS report_files (
        id           INTEGER PRIMARY KEY,
        channel_id   TEXT,
        message_id   INTEGER,
        stock_code   TEXT,
        stock_name   TEXT,
        sector       TEXT,                  -- 섹터 분류
        report_date  TEXT,
        file_name    TEXT,
        saved_name   TEXT,
        file_path    TEXT,
        file_size    INTEGER,
        mime_type    TEXT,
        caption      TEXT,
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(channel_id, message_id)
    );
    CREATE INDEX IF NOT EXISTS idx_rf_stock  ON report_files(stock_code);
    CREATE INDEX IF NOT EXISTS idx_rf_date   ON report_files(report_date);
    CREATE INDEX IF NOT EXISTS idx_rf_ch     ON report_files(channel_id);
    CREATE INDEX IF NOT EXISTS idx_rf_sector ON report_files(sector);
    """)
    conn.commit()
    logger.info("DB 초기화 완료")




# ══════════════════════════════════════════════════════════════════
# 섹터 분류 (KRX 기반)
# ══════════════════════════════════════════════════════════════════
SECTOR_KEYWORDS = {
    "반도체":      ["반도체", "메모리", "파운드리", "DRAM", "NAND", "HBM", "반도체장비", "웨이퍼", "SSD"],
    "IT/전자":     ["전자", "디스플레이", "OLED", "LCD", "전자부품", "PCB", "카메라모듈"],
    "2차전지/EV":  ["2차전지", "배터리", "전기차", "EV", "양극재", "음극재", "전해액", "분리막", "리튬"],
    "자동차":      ["자동차", "완성차", "자동차부품", "타이어", "모빌리티", "현대차", "기아"],
    "정유/화학":   ["정유", "석유화학", "화학", "에너지", "가스", "LNG", "LPG", "원유", "납사"],
    "바이오/제약": ["바이오", "제약", "의약품", "신약", "임상", "헬스케어", "의료기기", "진단", "의료"],
    "금융":        ["은행", "보험", "증권", "금융", "카드", "핀테크", "자산운용", "금리"],
    "통신":        ["통신", "5G", "6G", "인터넷", "플랫폼", "OTT", "미디어", "SKT", "KT", "LGU"],
    "건설/부동산": ["건설", "부동산", "리츠", "REIT", "건자재", "시멘트", "분양"],
    "철강/소재":   ["철강", "금속", "알루미늄", "구리", "소재", "포스코", "POSCO"],
    "조선/기계":   ["조선", "기계", "중공업", "항공", "방산", "로봇", "HD현대"],
    "유통/소비재": ["유통", "소비재", "이커머스", "패션", "식품", "음료", "화장품", "뷰티", "명품"],
    "게임/엔터":   ["게임", "엔터", "콘텐츠", "웹툰", "K-POP", "엔터테인먼트", "드라마"],
    "해운/물류":   ["해운", "물류", "운송", "택배", "HMM", "팬오션"],
    "전력/신재생": ["전력", "신재생", "태양광", "풍력", "수소", "원전", "ESS", "전기"],
    "코스피시장":  ["코스피", "KOSPI", "시장전망", "매크로", "증시전망", "주간전망", "월간전망"],
    "코스닥시장":  ["코스닥", "KOSDAQ", "중소형", "벤처", "코스닥전망"],
    "해외/글로벌": ["미국", "중국", "글로벌", "S&P", "나스닥", "달러", "환율", "Fed", "연준", "미장"],
}


def classify_sector(text: str) -> str:
    """텍스트에서 섹터 분류."""
    if not text:
        return ""
    text_upper = text.upper()
    # 점수 기반 분류 (키워드 매칭 수)
    scores = {}
    for sector, keywords in SECTOR_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.upper() in text_upper or kw in text)
        if score > 0:
            scores[sector] = score
    if scores:
        return max(scores, key=scores.get)
    return ""


def _normalize_kor_alnum(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", (text or "")).upper()


def _load_stock_name_candidates(conn) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        """
        WITH names AS (
            SELECT stock_code, stock_name, market
            FROM stock_universe
            WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
              AND market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
              AND COALESCE(stock_type, '보통주')='보통주'
            UNION
            SELECT stock_code, stock_name, market
            FROM stock_meta
            WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
              AND UPPER(COALESCE(market, '')) IN ('KOSPI', 'KOSDAQ')
        )
        SELECT stock_code, stock_name, MAX(market)
        FROM names
        GROUP BY stock_code, stock_name
        """
    ).fetchall()
    out = []
    for code, name, _market in rows:
        code = str(code).zfill(6)
        name = str(name or "").strip()
        norm = _normalize_kor_alnum(name)
        if len(norm) < 2:
            continue
        out.append((code, name, norm))
    # 긴 이름을 우선 매칭 (예: 신세계인터내셔날 > 신세계)
    out.sort(key=lambda x: len(x[2]), reverse=True)
    return out


# 매칭에 사용할 최소 정규화 종목명 길이 (짧은 이름은 오탐 위험)
_MIN_NORM_NAME_LEN = 3
# 매칭 신뢰도 최소 임계치: 미달 시 미매핑 처리
_MIN_MATCH_SCORE = 30.0

_KOR_ALNUM_PAT = re.compile(r"[0-9A-Za-z가-힣]")


def _resolve_stock_from_text(text: str, candidates: list[tuple[str, str, str]]) -> tuple[str, str]:
    norm = _normalize_kor_alnum(text)
    if not norm:
        return "", ""
    best_score = -1e9
    best = ("", "")
    for code, name, norm_name in candidates:
        if not norm_name or len(norm_name) < _MIN_NORM_NAME_LEN:
            continue
        pos = norm.find(norm_name)
        if pos < 0:
            continue
        # 접두/접미 경계 검사: 매칭 직후 문자가 한글/영숫자면 더 긴 이름의 일부일 수 있음
        end = pos + len(norm_name)
        if end < len(norm) and _KOR_ALNUM_PAT.match(norm[end]):
            # 더 긴 이름의 접두사로 매칭된 경우 크게 감점
            score = (200 - min(pos, 200)) + (len(norm_name) * 1.5) - 80
        else:
            score = (200 - min(pos, 200)) + (len(norm_name) * 1.5)
        # 리포트 파일명 후반 작성자(OO증권) 오탐 방지
        if name.endswith("증권") and pos > 24:
            score -= 120
        if score > best_score:
            best_score = score
            best = (code, name)
    # 신뢰도 임계치 미달 시 매핑 거부
    if best_score < _MIN_MATCH_SCORE:
        return "", ""
    return best

# ══════════════════════════════════════════════════════════════════
# 종목코드 추출
# ══════════════════════════════════════════════════════════════════
def extract_stock_code(text: str) -> tuple[str, str]:
    """파일명/캡션에서 종목코드와 종목명 추출."""
    if not text:
        return "", ""

    # 불필요한 패턴 제거 (날짜, 채널명, 조번호 등)
    clean = re.sub(r'\d{8}_', '', text)           # 날짜 제거
    clean = re.sub(r'YIG|SOKHA|KEYSS|DOC_POOL|sunstudy', '', clean, flags=re.I)
    clean = re.sub(r'\d+조[_\s]', '', clean)      # 1조, 2조 제거
    clean = re.sub(r'\.pdf|\.PDF|\.hwp|\.HWP|\.docx', '', clean)
    clean = re.sub(r'[_\-]', ' ', clean).strip()

    # 패턴 1: 6자리 숫자 종목코드
    m = re.search(r'\b(\d{6})\b', text)
    if m:
        code = m.group(1)
        name = re.sub(r'\d{6}', '', clean).strip()
        name = ' '.join(name.split())[:20]
        return code, name

    # 패턴 2: 한글 종목명 추출 (2글자 이상 연속 한글)
    # 긴 것 우선 (회사명은 보통 2~8글자)
    kr_matches = re.findall(r'[가-힣]{2,10}', clean)
    if kr_matches:
        # 가장 긴 한글 단어 = 종목명일 가능성 높음
        name = max(kr_matches, key=len)
        return "", name

    # 패턴 3: 영문 종목명
    en_matches = re.findall(r'[A-Z][a-zA-Z]{2,15}', clean)
    if en_matches:
        return "", en_matches[0]

    return "", ""


def make_safe_filename(date_str: str, original: str, caption: str = "") -> str:
    """
    파일명 생성: YYYYMMDD_종목명_원본명
    파일명이 모호하면 캡션에서 종목명/정보 추출해서 보완
    """
    safe_orig = re.sub(r'[^\w\-_\.]', '_', original)
    safe_orig = re.sub(r'_+', '_', safe_orig).strip('_')

    # 파일명이 너무 짧거나 모호한 경우 (숫자만, 영문만 짧은 경우)
    is_vague = (
        len(safe_orig.replace('.pdf','').replace('.PDF','')) < 5 or
        re.match(r'^[\d_]+\.', safe_orig) or  # 숫자로만 구성
        safe_orig.lower() in ['report.pdf', 'file.pdf', '보고서.pdf', 'document.pdf']
    )

    if is_vague and caption:
        # 캡션에서 종목명 추출
        # 패턴: "삼성전자", "LG에너지솔루션", 종목 관련 키워드
        kr_name = re.search(r'([가-힣A-Za-z][가-힣A-Za-z0-9]{1,15})', caption)
        code_in_cap = re.search(r'\b(\d{6})\b', caption)

        extra = ""
        if code_in_cap:
            extra = code_in_cap.group(1)
        elif kr_name:
            extra = kr_name.group(1)[:10]

        if extra:
            ext = Path(original).suffix or '.pdf'
            safe_orig = f"{extra}{ext}"

    return f"{date_str}_{safe_orig}"


# ══════════════════════════════════════════════════════════════════
# 채널 관리
# ══════════════════════════════════════════════════════════════════
def get_channels(conn) -> list:
    rows = conn.execute(
        "SELECT channel_id, channel_name, last_sync FROM telegram_channels WHERE is_active=1"
    ).fetchall()
    return [{"channel_id": r[0], "channel_name": r[1], "last_sync": r[2]} for r in rows]


def add_channel(conn, channel_id: str, name: str = ""):
    try:
        conn.execute(
            "INSERT OR IGNORE INTO telegram_channels (channel_id, channel_name) VALUES (?,?)",
            (channel_id, name or channel_id)
        )
        conn.commit()
        print(f"✅ 채널 추가: {channel_id}")
    except Exception as e:
        print(f"❌ 채널 추가 실패: {e}")


def remove_channel(conn, channel_id: str):
    conn.execute(
        "UPDATE telegram_channels SET is_active=0 WHERE channel_id=?", (channel_id,)
    )
    conn.commit()
    print(f"✅ 채널 비활성화: {channel_id}")


def list_channels(conn):
    rows = conn.execute(
        "SELECT channel_id, channel_name, is_active, last_sync FROM telegram_channels ORDER BY id"
    ).fetchall()
    print("\n── 등록된 텔레그램 채널 ──")
    for r in rows:
        status = "✅" if r[2] else "❌"
        print(f"  {status} {r[0]:30s} {r[1] or '':20s} 마지막: {r[3] or '-'}")
    print()


# ══════════════════════════════════════════════════════════════════
# 텔레그램 수집 (Telethon)
# ══════════════════════════════════════════════════════════════════
async def collect_channel(client, conn, channel_id: str, limit: int = 500,
                          since_days: int = None) -> int:
    """채널에서 파일 메시지 수집."""
    from telethon.tl.types import DocumentAttributeFilename
    saved = 0
    skipped = 0
    stock_candidates = _load_stock_name_candidates(conn)

    # 날짜 기준 수집 - Telethon offset_date는 해당날짜 이전 메시지 반환
    # since_days는 min_date로 사용해서 직접 필터링
    offset_date = None
    min_date = None
    if since_days:
        from datetime import timezone
        min_date = datetime.now(timezone.utc) - timedelta(days=since_days)

    try:
        entity = await client.get_entity(channel_id)
        logger.info(f"[{channel_id}] 채널 접속: {getattr(entity, 'title', channel_id)}")
        if offset_date:
            logger.info(f"[{channel_id}] {since_days}일치 수집 (since {offset_date.strftime('%Y-%m-%d')})")

        async for message in client.iter_messages(entity, limit=limit, reverse=False):
            # min_date 이전 메시지는 스킵
            if min_date and message.date and message.date < min_date:
                continue
            if not message.document:
                continue

            # 중복 확인
            exists = conn.execute(
                "SELECT 1 FROM report_files WHERE channel_id=? AND message_id=?",
                (channel_id, message.id)
            ).fetchone()
            if exists:
                skipped += 1
                continue

            # 파일 정보 추출
            mime = message.document.mime_type or ""
            caption = message.message or ""
            if not any(t in mime for t in ["pdf", "msword", "officedocument", "hwp", "excel"]):
                if not mime.startswith("application/"):
                    continue

            # 파일명 추출
            orig_name = ""
            for attr in message.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    orig_name = attr.file_name
                    break
            if not orig_name:
                orig_name = f"report_{message.id}.pdf"

            # 날짜
            msg_date = message.date.strftime("%Y%m%d")
            msg_date_iso = message.date.strftime("%Y-%m-%d")

            # 저장 파일명
            saved_name = make_safe_filename(msg_date, orig_name, caption)
            file_path = REPORT_DIR / saved_name

            # 종목코드 + 섹터 추출
            full_text = f"{orig_name} {caption}"
            code, name = extract_stock_code(full_text)
            # 6자리 코드가 없더라도 파일명/캡션에서 종목명을 찾아 종목 리포트로 우선 매핑
            if not code:
                mapped_code, mapped_name = _resolve_stock_from_text(full_text, stock_candidates)
                if mapped_code:
                    code, name = mapped_code, mapped_name
            # 종목코드가 없으면 섹터 보고서로 분류
            sector = ""
            if not code:
                sector = classify_sector(full_text)

            # 파일 다운로드
            if not file_path.exists():
                logger.info(f"  다운로드: {saved_name} ({message.document.size:,} bytes)")
                await client.download_media(message, str(file_path))
                await asyncio.sleep(1.5)  # FloodWait 방지
            else:
                logger.debug(f"  이미 존재: {saved_name}")

            # DB 저장
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO report_files
                    (channel_id, message_id, stock_code, stock_name, sector,
                     report_date, file_name, saved_name, file_path,
                     file_size, mime_type, caption)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    channel_id, message.id, code, name, sector,
                    msg_date_iso, orig_name, saved_name, str(file_path),
                    message.document.size, mime, caption[:500] if caption else ""
                ))
                conn.commit()
                saved += 1
                logger.info(f"  저장: {saved_name} [종목: {code or '미추출'}]")
            except Exception as e:
                logger.warning(f"  DB 저장 오류: {e}")

        # 마지막 동기화 시간 업데이트
        conn.execute(
            "UPDATE telegram_channels SET last_sync=? WHERE channel_id=?",
            (datetime.now().isoformat(), channel_id)
        )
        conn.commit()

    except Exception as e:
        logger.error(f"[{channel_id}] 오류: {e}")

    logger.info(f"[{channel_id}] 완료: 신규 {saved}건 / 스킵 {skipped}건")
    return saved


async def run_collect(channels: list, limit: int = 500, since_days: int = None):
    """전체 채널 수집 실행."""
    from telethon import TelegramClient
    cfg = get_config()

    if not cfg["api_id"] or not cfg["api_hash"]:
        logger.error("TELEGRAM_API_ID / TELEGRAM_API_HASH 없음 — .env 확인")
        return

    session_path = cfg.get("session") or "/Applications/stock_dashboard/telegram_session"
    client = TelegramClient(session_path, int(cfg["api_id"]), cfg["api_hash"])

    await client.start(phone=cfg["phone"] or None)
    logger.info("텔레그램 로그인 완료")

    conn = sqlite3.connect(str(DB_PATH))
    total = 0
    for ch in channels:
        n = await collect_channel(client, conn, ch["channel_id"],
                                  limit=limit, since_days=since_days)
        total += n
        if len(channels) > 1:
            await asyncio.sleep(2)  # 채널 간 딜레이

    conn.close()
    await client.disconnect()
    logger.info(f"전체 완료: {total}건 신규 수집")


# ══════════════════════════════════════════════════════════════════
# cron 등록
# ══════════════════════════════════════════════════════════════════
def setup_cron():
    import subprocess
    py  = "/Applications/stock_dashboard/venv/bin/python3"
    scr = "/Applications/stock_dashboard/telegram_collector.py"
    log = "/Applications/stock_dashboard/telegram_collector.log"
    line = f"30 8 * * 1-5 cd /Applications/stock_dashboard && {py} {scr} >> {log} 2>&1"

    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = res.stdout if res.returncode == 0 else ""
    if "telegram_collector" in existing:
        print("✅ cron 이미 등록됨")
        return
    new_cron = existing.rstrip() + "\n" + line + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_cron, text=True)
    if proc.returncode == 0:
        print(f"✅ cron 등록 완료!\n   {line}")
    else:
        print(f"❌ 수동 등록:\n   crontab -e 후 추가:\n   {line}")


# ══════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="텔레그램 채널 보고서 자동 수집")
    parser.add_argument("--auth",         action="store_true",  help="텔레그램 최초 인증")
    parser.add_argument("--channel",      default="",           help="특정 채널만 수집")
    parser.add_argument("--add-channel",  default="",           help="채널 추가 (@채널명)")
    parser.add_argument("--del-channel",  default="",           help="채널 비활성화")
    parser.add_argument("--list",         action="store_true",  help="채널 목록 출력")
    parser.add_argument("--setup-cron",   action="store_true",  help="crontab 등록")
    parser.add_argument("--limit",        type=int, default=500,  help="채널당 최대 메시지 수")
    parser.add_argument("--days",         type=int, default=None, help="최근 N일치 수집 (예: --days 30)")
    parser.add_argument("--stats",        action="store_true",  help="수집 현황 출력")
    args = parser.parse_args()

    if args.setup_cron:
        setup_cron(); return

    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    if args.list:
        list_channels(conn); conn.close(); return

    if args.add_channel:
        add_channel(conn, args.add_channel); conn.close(); return

    if args.del_channel:
        remove_channel(conn, args.del_channel); conn.close(); return

    if args.stats:
        total = conn.execute("SELECT COUNT(*) FROM report_files").fetchone()[0]
        today = conn.execute("SELECT COUNT(*) FROM report_files WHERE report_date=date('now')").fetchone()[0]
        by_ch = conn.execute("""
            SELECT c.channel_name, COUNT(r.id), MAX(r.report_date)
            FROM telegram_channels c
            LEFT JOIN report_files r ON c.channel_id=r.channel_id
            GROUP BY c.channel_id ORDER BY COUNT(r.id) DESC
        """).fetchall()
        print(f"\n총 보고서: {total}건 / 오늘: {today}건")
        for row in by_ch:
            print(f"  {row[0]:30s}: {row[1]:5d}건  최신={row[2] or '-'}")
        conn.close(); return

    # 수집 실행
    if args.auth:
        # 최초 인증 모드
        channels = [{"channel_id": "me", "channel_name": "인증테스트"}]
    elif args.channel:
        channels = [{"channel_id": args.channel, "channel_name": args.channel}]
    else:
        channels = get_channels(conn)
        if not channels:
            print("등록된 채널이 없습니다. --add-channel @채널명 으로 추가하세요.")
            conn.close(); return

    conn.close()

    try:
        asyncio.run(run_collect(channels, limit=args.limit, since_days=args.days))
    except KeyboardInterrupt:
        print("\n중단됨")
    except ImportError:
        print("❌ telethon 미설치")
        print("   pip install telethon --break-system-packages")


if __name__ == "__main__":
    main()
