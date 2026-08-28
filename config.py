import os
from pathlib import Path

# Base Directory
BASE_DIR = Path(__file__).resolve().parent

# ── .env 파일 자동 로드 ────────────────────────────────────────
# python-dotenv가 설치되어 있으면 사용, 없으면 직접 파싱.
# 우선순위: 시스템 환경변수 > .env 파일
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)
    except ImportError:
        # python-dotenv 미설치 시 직접 파싱
        with open(_env_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _key, _, _val = _line.partition("=")
                _key = _key.strip()
                _val = _val.strip().strip('"').strip("'")
                if _key and _key not in os.environ:
                    os.environ[_key] = _val
# ──────────────────────────────────────────────────────────────

# Database
# POSTGRES_DATABASE_URL lets us stage the migration without clobbering an
# existing DATABASE_URL used by legacy SQLite scripts.
DATABASE_URL = os.getenv(
    "POSTGRES_DATABASE_URL",
    os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/stock.db"),
)
IS_POSTGRES = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))
IS_SQLITE = DATABASE_URL.startswith("sqlite:")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# API prefix
API_V1_STR = "/api"


# ── API 키 — .env 또는 시스템 환경변수에서 읽음 ─────────────────
def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"\n[설정 오류] 필수 환경변수 '{key}'가 설정되지 않았습니다.\n"
            f"  해결 방법:\n"
            f"    1) {BASE_DIR}/.env 파일을 만들고 아래 내용 추가:\n"
            f"         {key}=여기에_값_입력\n"
            f"    2) 또는 터미널에서: export {key}=값  후 재시작\n"
        )
    return value


DART_API_KEY   = _require_env("DART_API_KEY")           # 재무제표 수집 전용 (KEY1)
DART_API_KEY2  = os.getenv("DART_API_KEY2", DART_API_KEY)  # 공시정보 수집 전용 (KEY2, 미설정 시 KEY1 폴백)
DART_API_KEY3  = os.getenv("DART_API_KEY3", None)          # 추가 재무제표 수집 (KEY3, 2026-05-26 추가)
DART_API_KEYS  = list(dict.fromkeys(k for k in (DART_API_KEY, DART_API_KEY2, DART_API_KEY3) if k))
KIS_APP_KEY    = _require_env("KIS_APP_KEY")
KIS_APP_SECRET = _require_env("KIS_APP_SECRET")

# ── KIS ────────────────────────────────────────────────────────
KIS_URL          = "https://openapi.koreainvestment.com:9443"
KIS_ACCOUNT_NO   = _require_env("KIS_ACCOUNT_NO")
KIS_ACCOUNT_PROD = os.getenv("KIS_ACCOUNT_PROD", "01")
KIS_RATE_LIMIT_SECS = float(os.getenv("KIS_RATE_LIMIT_SECS", "1.05"))

# 지수 코드 (config에서 override 가능)
KIS_INDEX_KOSPI  = os.getenv("KIS_INDEX_KOSPI",  "0001")
KIS_INDEX_KOSDAQ = os.getenv("KIS_INDEX_KOSDAQ", "1001")

# ── KRX ────────────────────────────────────────────────────────
KRX_API_KEY     = os.getenv("KRX_API_KEY", "")
KRX_OPENAPI_KEY = os.getenv("KRX_OPENAPI_KEY", "") or os.getenv("KRX_API_KEY", "")
KRX_DATA_URL    = os.getenv("KRX_DATA_URL", "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd")
KRX_DATA_ID     = os.getenv("KRX_DATA_ID", "")
KRX_DATA_PW     = os.getenv("KRX_DATA_PASS", "") or os.getenv("KRX_DATA_PW", "")
KMYDATA_BASE    = os.getenv("KMYDATA_BASE",  "https://oap.k-mydata.org")

# ── SEIBro ────────────────────────────────────────────────────
SEIBRO_ID   = os.getenv("SEIBRO_ID", "")
SEIBRO_PASS = os.getenv("SEIBRO_PASS", "")

# ── 공공데이터포털 ─────────────────────────────────────────────
PUBLIC_DATA_API_KEY = os.getenv("PUBLIC_DATA_API_KEY", "")
PUBLIC_DATA_BASE    = os.getenv("PUBLIC_DATA_BASE", "https://apis.data.go.kr/1160100/service")

# ── Telegram ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")
TELEGRAM_API_ID    = _require_env("TELEGRAM_API_ID")
TELEGRAM_API_HASH  = _require_env("TELEGRAM_API_HASH")
TELEGRAM_PHONE     = _require_env("TELEGRAM_PHONE")

# ── OpenAI ─────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── 기타 ───────────────────────────────────────────────────────
STOCKEASY_EMAIL    = os.getenv("STOCKEASY_EMAIL", "")
STOCKEASY_PASSWORD = os.getenv("STOCKEASY_PASSWORD", "")
COLLECT_CYCLE_DELAY = int(os.getenv("COLLECT_CYCLE_DELAY", "300"))

# ── Kiwoom REST/WebSocket (옵션) ───────────────────────────────
KIWOOM_ENABLED = os.getenv("KIWOOM_ENABLED", "false").lower() in ("1", "true", "yes", "on")
KIWOOM_APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
KIWOOM_SECRET_KEY = os.getenv("KIWOOM_SECRET_KEY", "")
KIWOOM_BASE_URL = os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com")
# 실시간 웹소켓 URL은 계정/가이드에 따라 상이할 수 있어 .env에서 override 권장
KIWOOM_WS_URL = os.getenv("KIWOOM_WS_URL", "")
KIWOOM_RT_ENABLED = os.getenv("KIWOOM_RT_ENABLED", "false").lower() in ("1", "true", "yes", "on")
KIWOOM_CONDITION_SCAN_LIMIT = int(os.getenv("KIWOOM_CONDITION_SCAN_LIMIT", "100"))
KIWOOM_CONDITION_SNAPSHOT_SECONDS = int(os.getenv("KIWOOM_CONDITION_SNAPSHOT_SECONDS", "300"))
KIWOOM_RT_TYPES = os.getenv("KIWOOM_RT_TYPES", "0A,0B,0C")
KIWOOM_RT_SNAPSHOT_SEC = int(os.getenv("KIWOOM_RT_SNAPSHOT_SEC", "12"))
KIWOOM_RT_BATCH_SIZE = int(os.getenv("KIWOOM_RT_BATCH_SIZE", "120"))
KIWOOM_RT_BATCHES_PER_CYCLE = int(os.getenv("KIWOOM_RT_BATCHES_PER_CYCLE", "6"))
KIWOOM_RT_UNIVERSE = os.getenv("KIWOOM_RT_UNIVERSE", "ALL").upper()  # ALL | ACTIVE
KIWOOM_FLOW_ENABLED = os.getenv("KIWOOM_FLOW_ENABLED", "false").lower() in ("1", "true", "yes", "on")
KIWOOM_FLOW_TOP_N = int(os.getenv("KIWOOM_FLOW_TOP_N", "10"))

# ── 서킷브레이커 / 재시도 ─────────────────────────────────────
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
CB_RECOVERY_SECS     = int(os.getenv("CB_RECOVERY_SECS",    "120"))
RETRY_MAX_ATTEMPTS   = int(os.getenv("RETRY_MAX_ATTEMPTS",  "3"))
RETRY_BACKOFF_BASE   = float(os.getenv("RETRY_BACKOFF_BASE","2.0"))

# ── Yahoo Finance 매크로 심볼 (override 가능) ──────────────────
# format: "SYMBOL:표시명,SYMBOL:표시명,..."
_yahoo_macro_env = os.getenv("YAHOO_MACRO_SYMBOLS", "")
YAHOO_MACRO_SYMBOLS: dict | None = None
if _yahoo_macro_env:
    try:
        YAHOO_MACRO_SYMBOLS = dict(
            part.split(":", 1)
            for part in _yahoo_macro_env.split(",")
            if ":" in part
        )
    except Exception:
        YAHOO_MACRO_SYMBOLS = None
