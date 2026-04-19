"""
routes/market_radar.py — 시장 Radar
LEVEL 1: 섹터별 해외선행지표 + 국내 시총상위 종목 + 시그널
LEVEL 2: 서브섹터별 세부 종목 테이블 + 개별 시그널
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "stock.db"


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── 섹터 + 서브섹터 정의 ────────────────────────────────────────
SECTORS: dict[str, dict] = {
    "semiconductor": {
        "name": "반도체",
        "emoji": "💾",
        "overseas": [
            {"symbol": "NVDA",   "name": "엔비디아",      "country": "US"},
            {"symbol": "AMD",    "name": "AMD",           "country": "US"},
            {"symbol": "AVGO",   "name": "브로드컴",      "country": "US"},
            {"symbol": "TSM",    "name": "TSMC",          "country": "TW"},
            {"symbol": "AMAT",   "name": "AMAT",          "country": "US"},
            {"symbol": "8035.T", "name": "도쿄일렉트론",   "country": "JP"},
        ],
        "korean_sectors": ["반도체", "반도체장비"],
        "subsectors": [
            {
                "key": "design_memory",
                "name": "설계 / 메모리",
                "stocks": [
                    {"symbol": "NVDA",   "name": "엔비디아",    "country": "US", "desc": "GPU·AI가속기 세계 1위 — 데이터센터·자율주행 핵심"},
                    {"symbol": "AMD",    "name": "AMD",         "country": "US", "desc": "CPU·GPU 설계 2위 — 서버 점유율 확대"},
                    {"symbol": "AVGO",   "name": "브로드컴",    "country": "US", "desc": "네트워킹 ASIC·AI XPU 선두"},
                    {"symbol": "MU",     "name": "마이크론",    "country": "US", "desc": "HBM3E 양산 확대, DRAM 2위"},
                    {"symbol": "005930", "name": "삼성전자",    "country": "KR", "desc": "메모리+파운드리 통합, 갤럭시 AI"},
                    {"symbol": "000660", "name": "SK하이닉스",  "country": "KR", "desc": "HBM3E 엔비디아 단독 공급, 세계 1위"},
                ],
            },
            {
                "key": "foundry",
                "name": "파운드리 / 제조",
                "stocks": [
                    {"symbol": "TSM",    "name": "TSMC",        "country": "TW", "desc": "파운드리 점유 54%, 2nm 공정 선도"},
                    {"symbol": "005930", "name": "삼성전자",    "country": "KR", "desc": "3nm GAA 가동, 삼성 파운드리"},
                ],
            },
            {
                "key": "equipment",
                "name": "장비",
                "stocks": [
                    {"symbol": "AMAT",   "name": "AMAT",        "country": "US", "desc": "CVD·PVD 장비 세계 1위"},
                    {"symbol": "LRCX",   "name": "램리서치",    "country": "US", "desc": "식각(Etch) 장비 세계 1위"},
                    {"symbol": "KLAC",   "name": "KLA",         "country": "US", "desc": "반도체 검사 장비 1위"},
                    {"symbol": "8035.T", "name": "도쿄일렉트론","country": "JP", "desc": "코터·현상기·CVD 장비 일본 1위"},
                    {"symbol": "042700", "name": "한미반도체",   "country": "KR", "desc": "TC본더 HBM 패키징 독점 공급"},
                    {"symbol": "058470", "name": "리노공업",     "country": "KR", "desc": "반도체 테스트 소켓 선두"},
                ],
            },
            {
                "key": "materials",
                "name": "소재 / 부품",
                "stocks": [
                    {"symbol": "036830", "name": "솔브레인",    "country": "KR", "desc": "반도체 식각액·전해질 소재"},
                    {"symbol": "005290", "name": "동진쎄미켐",  "country": "KR", "desc": "포토레지스트·현상액"},
                    {"symbol": "232830", "name": "에이치비테크놀러지", "country": "KR", "desc": "반도체 테스트용 히터"},
                ],
            },
        ],
    },
    "battery": {
        "name": "2차전지",
        "emoji": "🔋",
        "overseas": [
            {"symbol": "TSLA",   "name": "테슬라",        "country": "US"},
            {"symbol": "ALB",    "name": "알베말",        "country": "US"},
            {"symbol": "LTHM",   "name": "리튬아메리카스", "country": "US"},
            {"symbol": "6981.T", "name": "무라타제작소",   "country": "JP"},
        ],
        "korean_sectors": ["2차전지", "배터리"],
        "subsectors": [
            {
                "key": "cell",
                "name": "배터리 셀",
                "stocks": [
                    {"symbol": "TSLA",   "name": "테슬라",          "country": "US", "desc": "EV 1위 — 배터리 수요 최대 고객사"},
                    {"symbol": "006400", "name": "삼성SDI",          "country": "KR", "desc": "각형·원통형 배터리, 전고체 개발"},
                    {"symbol": "373220", "name": "LG에너지솔루션",   "country": "KR", "desc": "원통형·파우치형, GM·현대 공급"},
                    {"symbol": "096770", "name": "SK이노베이션",     "country": "KR", "desc": "SK온 배터리 모회사"},
                ],
            },
            {
                "key": "cathode_anode",
                "name": "양극재 / 음극재",
                "stocks": [
                    {"symbol": "ALB",    "name": "알베말",          "country": "US", "desc": "리튬 생산 세계 2위"},
                    {"symbol": "LTHM",   "name": "리튬아메리카스",  "country": "US", "desc": "리튬 광산 개발사"},
                    {"symbol": "247540", "name": "에코프로비엠",     "country": "KR", "desc": "NCA 양극재 1위, HLB 협력"},
                    {"symbol": "003670", "name": "포스코퓨처엠",     "country": "KR", "desc": "양극재+음극재 통합, 리튬 제련"},
                    {"symbol": "066970", "name": "엘앤에프",          "country": "KR", "desc": "NCM·NCMA 양극재"},
                ],
            },
            {
                "key": "electrolyte_separator",
                "name": "전해질 / 분리막",
                "stocks": [
                    {"symbol": "036830", "name": "솔브레인",         "country": "KR", "desc": "배터리 전해질·반도체 소재 겸용"},
                    {"symbol": "361610", "name": "SK아이이테크놀로지", "country": "KR", "desc": "LiBS 분리막, SK온 주요 공급"},
                    {"symbol": "6981.T", "name": "무라타제작소",      "country": "JP", "desc": "소형 리튬이온 배터리 1위"},
                ],
            },
        ],
    },
    "power_infra": {
        "name": "전력인프라",
        "emoji": "⚡",
        "overseas": [
            {"symbol": "ETN",    "name": "이튼",          "country": "US"},
            {"symbol": "VRT",    "name": "버티브",        "country": "US"},
            {"symbol": "PWR",    "name": "퀀타서비스",    "country": "US"},
            {"symbol": "HUBB",   "name": "허블",          "country": "US"},
        ],
        "korean_sectors": ["전력장비", "전기전자"],
        "subsectors": [
            {
                "key": "transformer_switchgear",
                "name": "변압기 / 전력기기",
                "stocks": [
                    {"symbol": "ETN",    "name": "이튼",            "country": "US", "desc": "전력관리·배전장비 글로벌 1위"},
                    {"symbol": "HUBB",   "name": "허블",            "country": "US", "desc": "산업용 전기배전장비"},
                    {"symbol": "298040", "name": "효성중공업",       "country": "KR", "desc": "초고압 변압기·차단기 국내 1위"},
                    {"symbol": "010120", "name": "LS일렉트릭",       "country": "KR", "desc": "배전반·스마트그리드 솔루션"},
                    {"symbol": "267260", "name": "현대일렉트릭",     "country": "KR", "desc": "발전기·변압기 수출 확대"},
                ],
            },
            {
                "key": "datacenter_power",
                "name": "데이터센터 / UPS",
                "stocks": [
                    {"symbol": "VRT",    "name": "버티브",          "country": "US", "desc": "데이터센터 냉각·전력 1위"},
                    {"symbol": "PWR",    "name": "퀀타서비스",      "country": "US", "desc": "전력망 공사·유지보수 1위"},
                ],
            },
            {
                "key": "cable_wire",
                "name": "전선 / 케이블",
                "stocks": [
                    {"symbol": "006260", "name": "LS",              "country": "KR", "desc": "전선·동·에너지 종합, LS전선 모회사"},
                    {"symbol": "001440", "name": "대한전선",         "country": "KR", "desc": "초고압 해저케이블 수주 증가"},
                ],
            },
            {
                "key": "nuclear_plant",
                "name": "발전 / 원자력",
                "stocks": [
                    {"symbol": "034020", "name": "두산에너빌리티",   "country": "KR", "desc": "원전 핵심 기자재, 신한울 3·4호기"},
                    {"symbol": "015760", "name": "한국전력",         "country": "KR", "desc": "전력 독점 판매, 원가 연동제"},
                ],
            },
        ],
    },
    "pharma": {
        "name": "제약/바이오",
        "emoji": "💊",
        "overseas": [
            {"symbol": "LLY",    "name": "일라이릴리",    "country": "US"},
            {"symbol": "NVO",    "name": "노보노디스크",  "country": "US"},
            {"symbol": "ABBV",   "name": "애브비",        "country": "US"},
            {"symbol": "MRNA",   "name": "모더나",        "country": "US"},
        ],
        "korean_sectors": ["제약", "바이오"],
        "subsectors": [
            {
                "key": "global_innovative",
                "name": "글로벌 혁신 신약",
                "stocks": [
                    {"symbol": "LLY",    "name": "일라이릴리",      "country": "US", "desc": "비만치료제 Mounjaro 1위, AI 신약"},
                    {"symbol": "NVO",    "name": "노보노디스크",    "country": "US", "desc": "GLP-1 Ozempic·Wegovy 선두"},
                    {"symbol": "ABBV",   "name": "애브비",          "country": "US", "desc": "휴미라 후속 Skyrizi·Rinvoq"},
                    {"symbol": "MRNA",   "name": "모더나",          "country": "US", "desc": "mRNA 플랫폼, 암백신 개발 중"},
                ],
            },
            {
                "key": "biosimilar_cmo",
                "name": "바이오시밀러 / CMO",
                "stocks": [
                    {"symbol": "207940", "name": "삼성바이오로직스", "country": "KR", "desc": "CMO 세계 1위(48만L 캐파), 4·5공장"},
                    {"symbol": "068270", "name": "셀트리온",         "country": "KR", "desc": "바이오시밀러+신약 병행, 짐펜트라"},
                ],
            },
            {
                "key": "domestic_innovative",
                "name": "국내 신약",
                "stocks": [
                    {"symbol": "128940", "name": "한미약품",         "country": "KR", "desc": "비만·당뇨 GLP-1 에파노펩타이드"},
                    {"symbol": "000100", "name": "유한양행",         "country": "KR", "desc": "레이저티닙 글로벌 임상, J&J협업"},
                    {"symbol": "326030", "name": "SK바이오팜",       "country": "KR", "desc": "세노바메이트 미국 판매 확대"},
                ],
            },
        ],
    },
    "defense": {
        "name": "K방산/우주",
        "emoji": "🚀",
        "overseas": [
            {"symbol": "LMT",    "name": "록히드마틴",    "country": "US"},
            {"symbol": "NOC",    "name": "노스롭그루만",  "country": "US"},
            {"symbol": "RTX",    "name": "RTX",          "country": "US"},
            {"symbol": "HII",    "name": "헌팅턴잉걸스",  "country": "US"},
        ],
        "korean_sectors": ["방산", "항공우주"],
        "subsectors": [
            {
                "key": "missiles_aircraft",
                "name": "항공 / 미사일",
                "stocks": [
                    {"symbol": "LMT",    "name": "록히드마틴",      "country": "US", "desc": "F-35·미사일 방어체계 세계 1위"},
                    {"symbol": "NOC",    "name": "노스롭그루만",    "country": "US", "desc": "B-21 스텔스·위성 시스템"},
                    {"symbol": "012450", "name": "한화에어로스페이스","country": "KR", "desc": "K9자주포·천무 수출, 한화방산"},
                    {"symbol": "047810", "name": "한국항공우주",     "country": "KR", "desc": "T-50·FA-50 항공기 수출"},
                    {"symbol": "079550", "name": "LIG넥스원",        "country": "KR", "desc": "천궁II·유도무기 체계"},
                ],
            },
            {
                "key": "naval_ground",
                "name": "함정 / 기갑 / 우주",
                "stocks": [
                    {"symbol": "RTX",    "name": "RTX",             "country": "US", "desc": "패트리엇·레이시온 미사일"},
                    {"symbol": "HII",    "name": "헌팅턴잉걸스",    "country": "US", "desc": "핵잠수함·항공모함 독점 건조"},
                    {"symbol": "064350", "name": "현대로템",         "country": "KR", "desc": "K2전차·장갑차, 폴란드 수출"},
                    {"symbol": "272210", "name": "한화시스템",       "country": "KR", "desc": "레이더·SAR위성·UAM 통합"},
                ],
            },
        ],
    },
    "shipbuilding": {
        "name": "조선/해운",
        "emoji": "🚢",
        "overseas": [
            {"symbol": "ZIM",    "name": "ZIM해운",       "country": "US"},
            {"symbol": "STNG",   "name": "스코피오탱커",  "country": "US"},
            {"symbol": "SBLK",   "name": "스타벌크",      "country": "US"},
        ],
        "korean_sectors": ["조선", "해운"],
        "subsectors": [
            {
                "key": "shipyard",
                "name": "조선소",
                "stocks": [
                    {"symbol": "009540", "name": "한국조선해양",     "country": "KR", "desc": "현대重 모회사, LNG선·초대형컨선 1위"},
                    {"symbol": "010140", "name": "삼성중공업",       "country": "KR", "desc": "LNG선·FLNG 특화 수주 증가"},
                    {"symbol": "010620", "name": "HD현대미포",       "country": "KR", "desc": "PC탱커·화학선 세계 1위"},
                    {"symbol": "329180", "name": "HD현대중공업",     "country": "KR", "desc": "울산 조선소, 군함·LNG"},
                ],
            },
            {
                "key": "shipping",
                "name": "해운",
                "stocks": [
                    {"symbol": "ZIM",    "name": "ZIM해운",         "country": "US", "desc": "이스라엘 컨테이너 선사, 배당 우수"},
                    {"symbol": "SBLK",   "name": "스타벌크",        "country": "US", "desc": "벌크선 글로벌 대형 선사"},
                    {"symbol": "011200", "name": "HMM",             "country": "KR", "desc": "국내 최대 컨테이너 선사"},
                    {"symbol": "028670", "name": "팬오션",           "country": "KR", "desc": "벌크·LNG 운반선 다각화"},
                ],
            },
            {
                "key": "offshore_plant",
                "name": "해양플랜트 / 기자재",
                "stocks": [
                    {"symbol": "STNG",   "name": "스코피오탱커",    "country": "US", "desc": "MR탱커 세계 최대 선사"},
                    {"symbol": "071970", "name": "HD현대마린솔루션", "country": "KR", "desc": "선박 엔진·MRO 서비스"},
                ],
            },
        ],
    },
    "energy": {
        "name": "에너지/소재",
        "emoji": "⛽",
        "overseas": [
            {"symbol": "XOM",    "name": "엑슨모빌",      "country": "US"},
            {"symbol": "CVX",    "name": "셰브론",        "country": "US"},
            {"symbol": "FCX",    "name": "프리포트맥모란", "country": "US"},
            {"symbol": "RIO",    "name": "리오틴토",      "country": "US"},
        ],
        "korean_sectors": ["에너지", "화학", "철강"],
        "subsectors": [
            {
                "key": "oil_gas",
                "name": "원유 / 가스",
                "stocks": [
                    {"symbol": "XOM",    "name": "엑슨모빌",        "country": "US", "desc": "석유 메이저 1위, LNG·화학 수직통합"},
                    {"symbol": "CVX",    "name": "셰브론",          "country": "US", "desc": "셰일가스·LNG 글로벌 2위"},
                    {"symbol": "096770", "name": "SK이노베이션",     "country": "KR", "desc": "정유+배터리 SK온 모회사"},
                    {"symbol": "010950", "name": "S-Oil",           "country": "KR", "desc": "아람코 계열, 샤힌 프로젝트"},
                ],
            },
            {
                "key": "metals_materials",
                "name": "비철금속 / 소재",
                "stocks": [
                    {"symbol": "FCX",    "name": "프리포트맥모란",  "country": "US", "desc": "구리 생산 세계 1위, AI 전력 수혜"},
                    {"symbol": "RIO",    "name": "리오틴토",        "country": "US", "desc": "철광석·구리·리튬 광산 기업"},
                    {"symbol": "005490", "name": "POSCO홀딩스",     "country": "KR", "desc": "철강+리튬·니켈 소재 수직통합"},
                    {"symbol": "010130", "name": "고려아연",         "country": "KR", "desc": "아연 세계 1위, 2차전지 소재 확장"},
                ],
            },
            {
                "key": "petrochemical",
                "name": "석유화학",
                "stocks": [
                    {"symbol": "051910", "name": "LG화학",           "country": "KR", "desc": "배터리소재+석화 통합, 양극재"},
                    {"symbol": "011170", "name": "롯데케미칼",       "country": "KR", "desc": "NCC·스페셜티 전환 구조조정"},
                    {"symbol": "009830", "name": "한화솔루션",       "country": "KR", "desc": "태양광(큐셀)+석화 복합"},
                ],
            },
        ],
    },
}

_radar_cache: dict = {}
_CACHE_TTL = 300  # 5분


def _calc_signal(changes: list[float]) -> str:
    if not changes:
        return "neutral"
    avg = sum(changes) / len(changes)
    if avg >= 1.5:
        return "green"
    if avg <= -1.5:
        return "red"
    if avg >= 0.3:
        return "yellow_up"
    if avg <= -0.3:
        return "yellow_down"
    return "neutral"


def _fetch_overseas_prices(symbols: list[str]) -> dict[str, dict]:
    """yfinance로 해외 종목 1D/1W 변화율 조회."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[Radar] yfinance 미설치 — pip install yfinance")
        return {}

    result: dict[str, dict] = {}
    try:
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                ticker = tickers.tickers.get(sym)
                if ticker is None:
                    continue
                hist = ticker.history(period="10d")
                if hist is None or len(hist) < 2:
                    continue
                curr   = float(hist["Close"].iloc[-1])
                prev1  = float(hist["Close"].iloc[-2])
                prev5  = float(hist["Close"].iloc[max(0, len(hist) - 6)])
                chg_1d = round((curr - prev1) / prev1 * 100, 2) if prev1 > 0 else 0.0
                chg_1w = round((curr - prev5) / prev5 * 100, 2) if prev5 > 0 else 0.0
                result[sym] = {"price": round(curr, 2), "chg_1d": chg_1d, "chg_1w": chg_1w}
            except Exception as e:
                logger.debug(f"[Radar] {sym}: {e}")
    except Exception as e:
        logger.warning(f"[Radar] yfinance 일괄 조회 실패: {e}")
    return result


def _fetch_korean_prices_bulk(stock_codes: list[str]) -> dict[str, dict]:
    """price_history에서 국내 종목 최근 7거래일 종가 조회 → 1D/1W 변화율 계산."""
    if not stock_codes:
        return {}
    conn = _db()
    try:
        ph = ",".join("?" * len(stock_codes))
        rows = conn.execute(
            f"""SELECT stock_code,
                       MAX(CASE WHEN rn=1 THEN close END) AS c1,
                       MAX(CASE WHEN rn=2 THEN close END) AS c2,
                       MAX(CASE WHEN rn=6 THEN close END) AS c6
                FROM (
                    SELECT stock_code, close,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                    FROM price_history
                    WHERE stock_code IN ({ph}) AND close > 0
                ) sub
                WHERE rn <= 7
                GROUP BY stock_code""",
            stock_codes,
        ).fetchall()
        result = {}
        for r in rows:
            c1 = r["c1"] or 0.0
            c2 = r["c2"] or 0.0
            c6 = r["c6"] or 0.0
            chg_1d = round((c1 - c2) / c2 * 100, 2) if c2 > 0 else 0.0
            chg_1w = round((c1 - c6) / c6 * 100, 2) if c6 > 0 else 0.0
            result[r["stock_code"]] = {
                "price":  c1,
                "chg_1d": chg_1d,
                "chg_1w": chg_1w if c6 > 0 else None,
            }
        return result
    finally:
        conn.close()


def _get_korean_top(sector_keys: list[str], limit: int = 8) -> list[dict]:
    """stock_universe + price_history에서 섹터 상위 종목 당일 등락률 조회."""
    if not sector_keys:
        return []
    conn = _db()
    try:
        ph = ",".join("?" * len(sector_keys))
        rows = conn.execute(
            f"""SELECT u.stock_code, u.stock_name, u.sector_large, u.market_cap,
                       MAX(CASE WHEN rn=1 THEN ph.close END) AS close,
                       MAX(CASE WHEN rn=2 THEN ph.close END) AS prev_close
                FROM stock_universe u
                JOIN (
                    SELECT stock_code, close,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                    FROM price_history
                    WHERE close > 0
                      AND stock_code NOT LIKE '%^%'
                ) ph ON u.stock_code = ph.stock_code
                WHERE u.sector_large IN ({ph})
                  AND ph.rn <= 2
                  AND u.market_cap > 0
                GROUP BY u.stock_code
                ORDER BY u.market_cap DESC
                LIMIT ?""",
            sector_keys + [limit],
        ).fetchall()
        result = []
        for r in rows:
            chg = 0.0
            if r["prev_close"] and r["prev_close"] > 0 and r["close"]:
                chg = round((r["close"] - r["prev_close"]) / r["prev_close"] * 100, 2)
            result.append({
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "sector":     r["sector_large"],
                "mktcap":     r["market_cap"],
                "close":      r["close"],
                "chg_1d":     chg,
            })
        return result
    finally:
        conn.close()


# ── GET /api/market-radar/sectors ─────────────────────────────────
@router.get("/sectors")
def list_sectors():
    return [
        {"key": k, "name": v["name"], "emoji": v["emoji"]}
        for k, v in SECTORS.items()
    ]


# ── GET /api/market-radar/sector/{sector_key} (LEVEL 1) ───────────
@router.get("/sector/{sector_key}")
def get_sector_radar(sector_key: str):
    """LEVEL 1: 섹터 해외선행지표 + 국내 시총상위 + 시그널 (캐시 5분)."""
    cache_key = f"radar_{sector_key}"
    cached = _radar_cache.get(cache_key)
    if cached and time.time() - cached["at"] < _CACHE_TTL:
        return cached["data"]

    sector = SECTORS.get(sector_key)
    if not sector:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector_key}")

    symbols = [s["symbol"] for s in sector["overseas"]]
    prices  = _fetch_overseas_prices(symbols)

    overseas_result = []
    changes_1d: list[float] = []
    for s in sector["overseas"]:
        p = prices.get(s["symbol"], {})
        chg_1d = p.get("chg_1d", 0.0)
        if p:
            changes_1d.append(chg_1d)
        overseas_result.append({
            **s,
            "price":    p.get("price"),
            "chg_1d":   chg_1d,
            "chg_1w":   p.get("chg_1w", 0.0),
            "has_data": bool(p),
        })

    korean_stocks = _get_korean_top(sector["korean_sectors"])
    signal = _calc_signal(changes_1d)

    data = {
        "sector_key":      sector_key,
        "sector_name":     sector["name"],
        "emoji":           sector["emoji"],
        "signal":          signal,
        "overseas_avg_1d": round(sum(changes_1d) / len(changes_1d), 2) if changes_1d else 0.0,
        "overseas":        overseas_result,
        "korean":          korean_stocks,
    }
    _radar_cache[cache_key] = {"data": data, "at": time.time()}
    return data


# ── GET /api/market-radar/sector/{sector_key}/detail (LEVEL 2) ────
@router.get("/sector/{sector_key}/detail")
def get_sector_detail(sector_key: str):
    """LEVEL 2: 서브섹터별 세부 종목 테이블 + 개별 시그널 (캐시 5분)."""
    cache_key = f"radar_detail_{sector_key}"
    cached = _radar_cache.get(cache_key)
    if cached and time.time() - cached["at"] < _CACHE_TTL:
        return cached["data"]

    sector = SECTORS.get(sector_key)
    if not sector:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector_key}")

    subsectors = sector.get("subsectors", [])

    # 모든 종목 수집
    all_overseas = list({
        s["symbol"]
        for ss in subsectors
        for s in ss["stocks"]
        if s.get("country", "KR") != "KR"
    })
    all_korean = list({
        s["symbol"]
        for ss in subsectors
        for s in ss["stocks"]
        if s.get("country", "KR") == "KR"
    })

    # 가격 일괄 조회
    overseas_prices = _fetch_overseas_prices(all_overseas)
    korean_prices   = _fetch_korean_prices_bulk(all_korean)

    def _get_price(sym: str, country: str) -> dict:
        if country == "KR":
            p = korean_prices.get(sym, {})
            return {
                "price":    p.get("price"),
                "chg_1d":   p.get("chg_1d", 0.0),
                "chg_1w":   p.get("chg_1w"),
                "has_data": bool(p),
            }
        else:
            p = overseas_prices.get(sym, {})
            return {
                "price":    p.get("price"),
                "chg_1d":   p.get("chg_1d", 0.0),
                "chg_1w":   p.get("chg_1w"),
                "has_data": bool(p),
            }

    # 서브섹터별 조립
    result_subsectors = []
    all_changes: list[float] = []

    for ss in subsectors:
        stocks_out = []
        sub_changes: list[float] = []
        for s in ss["stocks"]:
            p = _get_price(s["symbol"], s.get("country", "KR"))
            chg = p["chg_1d"] or 0.0
            if p["has_data"]:
                sub_changes.append(chg)
                all_changes.append(chg)
            stocks_out.append({
                "symbol":   s["symbol"],
                "name":     s["name"],
                "country":  s.get("country", "KR"),
                "desc":     s.get("desc", ""),
                **p,
            })

        result_subsectors.append({
            "key":     ss["key"],
            "name":    ss["name"],
            "signal":  _calc_signal(sub_changes),
            "avg_1d":  round(sum(sub_changes) / len(sub_changes), 2) if sub_changes else 0.0,
            "stocks":  stocks_out,
        })

    data = {
        "sector_key":  sector_key,
        "sector_name": sector["name"],
        "emoji":       sector["emoji"],
        "signal":      _calc_signal(all_changes),
        "avg_1d":      round(sum(all_changes) / len(all_changes), 2) if all_changes else 0.0,
        "subsectors":  result_subsectors,
    }
    _radar_cache[cache_key] = {"data": data, "at": time.time()}
    return data


# ── GET /api/market-radar/all ──────────────────────────────────────
@router.get("/all")
def get_all_radar():
    """전체 섹터 시그널 요약."""
    cache_key = "radar_all"
    cached = _radar_cache.get(cache_key)
    if cached and time.time() - cached["at"] < _CACHE_TTL:
        return cached["data"]

    all_symbols = list({
        s["symbol"]
        for v in SECTORS.values()
        for s in v["overseas"]
    })
    prices = _fetch_overseas_prices(all_symbols)

    result = []
    for key, sector in SECTORS.items():
        changes = [
            prices[s["symbol"]]["chg_1d"]
            for s in sector["overseas"]
            if s["symbol"] in prices
        ]
        result.append({
            "key":    key,
            "name":   sector["name"],
            "emoji":  sector["emoji"],
            "signal": _calc_signal(changes),
            "avg_1d": round(sum(changes) / len(changes), 2) if changes else 0.0,
        })

    data = {"sectors": result, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _radar_cache[cache_key] = {"data": data, "at": time.time()}
    return data
