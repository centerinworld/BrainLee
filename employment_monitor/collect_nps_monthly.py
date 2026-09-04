"""
collect_nps_monthly.py — NPS 월별 입사/퇴사 데이터 수집기 (v2)

API: https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2
 - getBassInfoSearchV2      : 사업장 검색 → seq 획득
 - getDetailInfoSearchV2    : 가입자수(jnngpCnt) 확인
 - getPdAcctoSttusInfoSearchV2: 신규취득(nwAcqzrCnt) + 상실(lssJnngpCnt)

핵심 설계:
 - 회사명 패턴 기반 검색 + 정확한 이름 매칭(exact match)
 - 가장 높은 seq = 가장 최근 월 스냅샷 → 매월 seq 업데이트 필요
 - getPdAcctoSttusInfoSearchV2(seq) = 해당 seq의 월 신규취득/상실 반환

수집 주기: 매월 15일 이후 (국민연금공단 데이터 제공 시점)
저장 테이블: nps_monthly (employment.db)
             nps_seq_map (employment.db) — stock_code ↔ 최신 seq 매핑

실행:
  python3 collect_nps_monthly.py --build-map   # seq 매핑 구축 (매월 갱신)
  python3 collect_nps_monthly.py --collect     # 현재월 신규취득/상실 수집
  python3 collect_nps_monthly.py --test 005930 # 단일 종목 테스트
  python3 collect_nps_monthly.py               # 매핑 구축 후 수집 (기본)
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dateutil.relativedelta import relativedelta as _relativedelta
    _HAS_DATEUTIL = True
except ImportError:
    _HAS_DATEUTIL = False

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from env_utils import require_env

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ── 경로 설정 ────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
EMP_DB   = os.path.join(_DIR, "employment.db")
STOCK_DB = os.environ.get("STOCK_DB", str(PROJECT_ROOT / "stock.db"))

# ── NPS API 설정 ─────────────────────────────────────────────────────────────
API_KEY  = require_env("PUBLIC_DATA_API_KEY")
BASE_URL = "https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2"
HEADERS  = {"User-Agent": "Mozilla/5.0"}


# ── DB 초기화 ─────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(EMP_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nps_seq_map (
            stock_code  TEXT NOT NULL PRIMARY KEY,
            seq         INTEGER NOT NULL,
            wkpl_nm     TEXT,
            jnngp_cnt   INTEGER DEFAULT 0,
            updated_ym  TEXT
        );

        CREATE TABLE IF NOT EXISTS nps_monthly (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code   TEXT NOT NULL,
            data_ym      TEXT NOT NULL,            -- '202604' 형식
            new_hires    INTEGER DEFAULT 0,         -- 신규취득(nwAcqzrCnt)
            terminations INTEGER DEFAULT 0,         -- 상실(lssJnngpCnt)
            net_change   INTEGER DEFAULT 0,         -- new_hires - terminations
            fetched_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, data_ym)
        );

        CREATE INDEX IF NOT EXISTS ix_nps_monthly_sc ON nps_monthly(stock_code, data_ym);
        CREATE INDEX IF NOT EXISTS ix_nps_monthly_ym ON nps_monthly(data_ym);
    """)
    conn.commit()
    conn.close()
    logger.info("nps_seq_map / nps_monthly 테이블 초기화 완료")


# ── API 헬퍼 ──────────────────────────────────────────────────────────────────
def _api_get(endpoint: str, params: dict, retries: int = 3) -> dict:
    params["serviceKey"] = API_KEY
    params["dataType"]   = "json"
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", {}).get("body", {})
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.warning(f"API 오류 [{endpoint}]: {e}")
    return {}


def _get_items(body: dict) -> list:
    items = body.get("items", {})
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return items if isinstance(items, list) else []


_ENG_TO_KOR = [
    # 영문 약칭 → 한글 발음 (NPS 공식 사업장명 기준)
    ("POSCO홀딩스", "포스코홀딩스"),
    ("POSCO", "포스코"),
    ("NAVER", "네이버"),
    ("SK하이닉스", "에스케이하이닉스"),
    ("SK이노베이션", "에스케이이노베이션"),
    ("SK텔레콤", "에스케이텔레콤"),
    ("SKC", "에스케이씨"),
    ("SK", "에스케이"),
    ("LG화학", "엘지화학"),
    ("LG전자", "엘지전자"),
    ("LG에너지솔루션", "엘지에너지솔루션"),
    ("LG이노텍", "엘지이노텍"),
    ("LGU+", "엘지유플러스"),
    ("LG", "엘지"),
    ("KT&G", "케이티앤지"),
    ("KT", "케이티"),
    ("GS칼텍스", "지에스칼텍스"),
    ("GS건설", "지에스건설"),
    ("GS리테일", "지에스리테일"),
    ("GS", "지에스"),
    ("CJ대한통운", "씨제이대한통운"),
    ("CJ제일제당", "씨제이제일제당"),
    ("CJ", "씨제이"),
    ("LS ELECTRIC", "엘에스일렉트릭"),
    ("LS", "엘에스"),
    ("OCI", "오씨아이"),
    ("DL이앤씨", "디엘이앤씨"),
    ("DL케미칼", "디엘케미칼"),
    ("DL", "디엘"),
    ("DB하이텍", "디비하이텍"),
    ("DB손해보험", "디비손해보험"),
    ("DB", "디비"),
    ("KB금융", "케이비금융"),
    ("KB", "케이비"),
    ("IBK", "아이비케이"),
    ("NH투자증권", "엔에이치투자증권"),
    ("NH", "엔에이치"),
    ("HD현대", "에이치디현대"),
    ("HD", "에이치디"),
    ("KG케미칼", "케이지케미칼"),
    ("KG", "케이지"),
]


def _korean_variants(stock_name: str) -> list:
    """영문 포함 회사명 → 한글 발음 변환 후보 목록."""
    variants = []
    for eng, kor in _ENG_TO_KOR:
        if eng in stock_name:
            variants.append(stock_name.replace(eng, kor))
    return variants


def _name_patterns(stock_name: str) -> list:
    """회사명의 NPS 사업장명 후보 패턴 목록 반환 (우선순위 순)."""
    candidates = [stock_name] + _korean_variants(stock_name)
    patterns = []
    for n in candidates:
        patterns += [
            f"{n}(주)",           # 삼성전자(주) / 에스케이하이닉스(주)
            f"{n}㈜",
            f"(주){n}",
            f"㈜{n}",
            f"{n} 주식회사",       # 에스케이하이닉스 주식회사
            f"주식회사 {n}",
            f"주식회사{n}",
            n,                    # exact
        ]
    # 중복 제거, 순서 유지
    seen = set()
    result = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


_NAME_CLEAN_RE = __import__("re").compile(
    r"주식회사|\(주\)|㈜|（주）|\( 주 \)|\s+",
    __import__("re").IGNORECASE,
)


def _clean_company_name(name: str) -> str:
    return _NAME_CLEAN_RE.sub("", name or "").upper()


def _is_project_workplace(name: str) -> bool:
    """건설/공사성 프로젝트 사업장처럼 본사 사업장으로 보기 어려운 NPS 행 제외."""
    s = name or ""
    return any(token in s for token in ("/일용", "-(일용", "（일용", "/상용", "-(상용", "（상용"))


def _find_latest_seq_fast(stock_name: str, biz_no_6: str = "") -> tuple | None:
    """
    적은 호출로 NPS seq를 찾는 1차 매칭.
    - stock_name 원문/한글 변환명으로 검색
    - wkplNm을 정규화해 상장사명과 같으면 채택
    - biz_no_6가 있으면 같이 전달해 동명이인/공사성 사업장을 줄인다.
    """
    search_names = [stock_name] + _korean_variants(stock_name)
    target_names = {_clean_company_name(n) for n in search_names}
    candidates = []

    for name in search_names:
        time.sleep(0.12)
        params = {"wkplNm": name, "numOfRows": "100", "pageNo": "1"}
        if biz_no_6:
            params["bzowrRgstNo"] = biz_no_6
        body = _api_get("getBassInfoSearchV2", params)
        for it in _get_items(body):
            wkpl_nm = it.get("wkplNm", "")
            seq = it.get("seq")
            if not seq:
                continue
            if _is_project_workplace(wkpl_nm):
                continue
            cleaned = _clean_company_name(wkpl_nm)
            if cleaned in target_names:
                candidates.append((int(seq), wkpl_nm))
        if candidates:
            break

    if not candidates:
        return None

    best_seq, best_nm = max(candidates, key=lambda x: x[0])
    time.sleep(0.12)
    d = _api_get("getDetailInfoSearchV2", {"seq": str(best_seq)})
    di = _get_items(d)
    jnngp = int(di[0].get("jnngpCnt", 0) or 0) if di else 0
    return (best_seq, best_nm, jnngp)


def _find_latest_seq(stock_name: str, biz_no_6: str = "") -> tuple | None:
    """
    회사명 패턴으로 NPS 검색 → 정확히 일치하는 사업장명 중 최신(최대) seq 반환.
    반환: (seq, wkpl_nm, jnngp_cnt) 또는 None
    """
    patterns = _name_patterns(stock_name)
    candidates = []  # (seq, wkpl_nm)

    for pattern in patterns:
        time.sleep(0.25)
        params = {
            "wkplNm": pattern,
            "numOfRows": "100", "pageNo": "1"
        }
        if biz_no_6:
            params["bzowrRgstNo"] = biz_no_6
        body = _api_get("getBassInfoSearchV2", params)
        items = _get_items(body)
        if not items:
            continue

        for it in items:
            nm  = it.get("wkplNm", "")
            seq = it.get("seq")
            if not seq:
                continue
            # 정확한 이름 일치만 허용
            if nm == pattern:
                candidates.append((int(seq), nm))

        if candidates:
            break  # 첫 번째 성공 패턴에서 중단

    if not candidates:
        return None

    # 최신(최대) seq 선택
    best_seq, best_nm = max(candidates, key=lambda x: x[0])

    # 가입자수 확인
    time.sleep(0.2)
    d = _api_get("getDetailInfoSearchV2", {"seq": str(best_seq)})
    di = _get_items(d)
    jnngp = int(di[0].get("jnngpCnt", 0) or 0) if di else 0

    return (best_seq, best_nm, jnngp)


# ── 1단계: seq 매핑 구축 ─────────────────────────────────────────────────────
def build_seq_map(
    limit: int = 300,
    refresh: bool = False,
    fast_only: bool = False,
    offset: int = 0,
):
    """
    stock_universe 상위 N개 종목에 대해 최신 NPS seq를 찾아 nps_seq_map에 저장.
    매월 실행해야 seq가 갱신됨 (seq = 월별 스냅샷 번호).
    """
    stk_conn = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True)
    sql = """
        SELECT stock_code, stock_name
        FROM stock_universe
        WHERE market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
          AND stock_type = '보통주'
          AND LENGTH(stock_code) = 6
        ORDER BY market_cap DESC NULLS LAST, stock_code
    """
    params = ()
    if limit and limit > 0:
        sql += " LIMIT ?"
        params = (limit,)
    if offset and offset > 0:
        if not (limit and limit > 0):
            sql += " LIMIT -1"
        sql += " OFFSET ?"
        params = params + (offset,)
    universe = {r[0]: r[1] for r in stk_conn.execute(sql, params).fetchall()}
    stk_conn.close()
    conn = sqlite3.connect(EMP_DB)
    biz_map = {
        r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, biz_no_6 FROM stock_bizno_map WHERE biz_no_6 IS NOT NULL"
        ).fetchall()
    }
    conn.close()

    if not universe:
        logger.error("stock_universe 비어 있음")
        return

    current_ym = datetime.now().strftime("%Y%m")
    total = saved = failed = 0
    conn = sqlite3.connect(EMP_DB)
    existing = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM nps_seq_map WHERE updated_ym=?",
            (current_ym,),
        ).fetchall()
    }
    conn.close()

    for stock_code, stock_name in universe.items():
        total += 1
        if not refresh and stock_code in existing:
            saved += 1
            if total % 50 == 0:
                logger.info(f"seq 매핑 진행: {total}/{len(universe)} 처리, 보유/저장={saved}, 실패={failed}")
            continue

        biz_no_6 = biz_map.get(stock_code, "")
        result = _find_latest_seq_fast(stock_name, biz_no_6)
        if result is None and not fast_only:
            result = _find_latest_seq(stock_name, biz_no_6)

        if result is None:
            logger.debug(f"[{stock_code}] {stock_name}: 매핑 실패")
            failed += 1
            if total % 50 == 0:
                logger.info(f"seq 매핑 진행: {total}/{len(universe)} 처리, 보유/저장={saved}, 실패={failed}")
            continue

        seq, wkpl_nm, jnngp = result
        conn = sqlite3.connect(EMP_DB)
        conn.execute("""
            INSERT OR REPLACE INTO nps_seq_map
                (stock_code, seq, wkpl_nm, jnngp_cnt, updated_ym)
            VALUES (?,?,?,?,?)
        """, (stock_code, seq, wkpl_nm, jnngp, current_ym))
        conn.commit()
        conn.close()
        saved += 1

        logger.info(f"[{stock_code}] {stock_name} → {wkpl_nm} (seq={seq}, 가입자={jnngp:,}명)")
        if total % 50 == 0:
            logger.info(f"seq 매핑 진행: {total}/{len(universe)} 처리, 보유/저장={saved}, 실패={failed}")

    logger.info(f"seq 매핑 완료: {total}종목 처리, {saved}개 보유/저장, {failed}개 실패")


# ── 2단계: 월별 입사/퇴사 수집 ──────────────────────────────────────────────
def collect_monthly(target_ym: str = None):
    """
    nps_seq_map의 최신 seq로 신규취득/상실 수집 → nps_monthly에 저장.
    월별로 build_seq_map → collect_monthly 순서로 실행.
    """
    if target_ym is None:
        target_ym = _detect_latest_data_ym()
    if not target_ym:
        target_ym = datetime.now().strftime("%Y%m")

    conn = sqlite3.connect(EMP_DB)
    seq_rows = conn.execute(
        "SELECT stock_code, seq, wkpl_nm FROM nps_seq_map ORDER BY stock_code"
    ).fetchall()
    conn.close()

    if not seq_rows:
        logger.warning("nps_seq_map 비어 있음 — build_seq_map 먼저 실행하세요")
        return

    saved = skip = 0
    for sc, seq, wkpl_nm in seq_rows:
        # 이미 수집한 데이터 스킵
        conn = sqlite3.connect(EMP_DB)
        already = conn.execute(
            "SELECT 1 FROM nps_monthly WHERE stock_code=? AND data_ym=?", (sc, target_ym)
        ).fetchone()
        conn.close()
        if already:
            skip += 1
            continue

        time.sleep(0.2)
        body = _api_get("getPdAcctoSttusInfoSearchV2", {
            "seq": str(seq),
            "dataCrtYm": target_ym,
        })
        items = _get_items(body)

        if not items:
            continue

        it = items[0]
        new_hires    = int(it.get("nwAcqzrCnt", 0) or 0)
        terminations = int(it.get("lssJnngpCnt", 0) or 0)
        net_change   = new_hires - terminations

        conn = sqlite3.connect(EMP_DB)
        conn.execute("""
            INSERT OR REPLACE INTO nps_monthly
                (stock_code, data_ym, new_hires, terminations, net_change, wkpl_count)
            VALUES (?,?,?,?,?,?)
        """, (sc, target_ym, new_hires, terminations, net_change, 1))
        conn.commit()
        conn.close()
        saved += 1

        if new_hires > 0 or terminations > 0:
            logger.info(
                f"[{sc}] {(wkpl_nm or '')[:25]} {target_ym}: "
                f"신규={new_hires:+d} 상실={terminations} 순증={net_change:+d}"
            )

    logger.info(f"월별 수집 완료: {saved}건 저장, {skip}건 스킵 ({target_ym})")


# ── 3단계: 보너스 맵 (signal_engine 연동용) ──────────────────────────────────
def get_nps_bonus_map(months: int = 3) -> dict:
    """
    최근 N개월 누적 순증가(net_change 합산) → 보너스 점수 맵.
    고용 순증가가 클수록 높은 점수(+1~+3).

    Returns:
        {stock_code: bonus_score}  # 1~3 (없으면 미포함)
    """
    conn = sqlite3.connect(EMP_DB)
    try:
        rows = conn.execute("""
            SELECT stock_code, SUM(net_change) as net_sum, COUNT(*) as months_cnt
            FROM nps_monthly
            WHERE data_ym >= (
                SELECT data_ym
                FROM (SELECT DISTINCT data_ym FROM nps_monthly ORDER BY data_ym DESC)
                LIMIT 1 OFFSET ?
            )
            GROUP BY stock_code
            HAVING months_cnt >= 1
        """, (max(0, months - 1),)).fetchall()
    except Exception as e:
        logger.warning(f"get_nps_bonus_map 오류: {e}")
        return {}
    finally:
        conn.close()

    result = {}
    for sc, net_sum, mcnt in rows:
        if net_sum is None or net_sum <= 0:
            continue
        if net_sum >= 300:
            result[sc] = 3
        elif net_sum >= 100:
            result[sc] = 2
        elif net_sum > 0:
            result[sc] = 1
    return result


# ── 4단계: 과거 데이터 소급 수집 ────────────────────────────────────────────
def _detect_latest_data_ym() -> str | None:
    """NPS 사업장 검색 응답에서 현재 제공 중인 최신 자료생성년월을 확인."""
    body = _api_get("getBassInfoSearchV2", {
        "wkplNm": "삼성전자로지텍주식회사",
        "numOfRows": "1",
        "pageNo": "1",
    })
    items = _get_items(body)
    if not items:
        return None
    ym = str(items[0].get("dataCrtYm", "") or "").strip()
    return ym if len(ym) == 6 and ym.isdigit() else None


def _has_collectable_month(target_ym: str, sample_size: int = 8) -> bool:
    """검색에 최신월이 보여도 취득/상실 API가 비어 있으면 아직 수집 대상이 아니다."""
    conn = sqlite3.connect(EMP_DB)
    rows = conn.execute(
        """
        SELECT seq
        FROM nps_seq_map
        WHERE seq IS NOT NULL
        ORDER BY jnngp_cnt DESC
        LIMIT ?
        """,
        (sample_size,),
    ).fetchall()
    conn.close()

    for (seq,) in rows:
        time.sleep(0.2)
        body = _api_get("getPdAcctoSttusInfoSearchV2", {
            "seq": str(seq),
            "dataCrtYm": target_ym,
        })
        if _get_items(body):
            return True
    return False


def _get_recent_company_snapshots(
    wkpl_nm: str,
    months_back: int | None = None,
    from_ym: str | None = None,
) -> list[tuple[int, str]]:
    """
    사업장명으로 검색 가능한 월별 스냅샷 목록을 최신순 반환.
    반환: [(seq, data_ym), ...]
    """
    snapshots = []
    seen = set()
    max_pages = 2 if months_back and months_back <= 3 and not from_ym else 20
    for page in range(1, max_pages + 1):
        time.sleep(0.2)
        body = _api_get("getBassInfoSearchV2", {
            "wkplNm": wkpl_nm,
            "numOfRows": "100",
            "pageNo": str(page),
        })
        items = _get_items(body)
        if not items:
            break
        for it in items:
            if it.get("wkplNm") != wkpl_nm:
                continue
            seq = it.get("seq")
            data_ym = str(it.get("dataCrtYm", "") or "").strip()
            if not seq or len(data_ym) != 6 or not data_ym.isdigit():
                continue
            key = (int(seq), data_ym)
            if key in seen:
                continue
            seen.add(key)
            snapshots.append(key)

        if months_back and months_back > 0 and len(snapshots) >= months_back:
            break

        total_cnt = int(body.get("totalCount") or 0)
        if page * 100 >= total_cnt:
            break

    snapshots.sort(key=lambda x: (x[1], x[0]), reverse=True)
    if from_ym:
        snapshots = [snap for snap in snapshots if snap[1] >= from_ym]
    if months_back and months_back > 0:
        snapshots = snapshots[:months_back]
    return snapshots


def build_historical_data(months_back: int | None = 36, from_ym: str | None = None):
    """
    이미 수집된 nps_seq_map의 회사들에 대해 과거 데이터를 소급 수집.
    getBassInfoSearchV2의 dataCrtYm/seq를 기준으로 최근 N개월 스냅샷을 찾고,
    getPdAcctoSttusInfoSearchV2(seq, dataCrtYm)로 월별 취득/상실을 저장한다.
    """
    conn = sqlite3.connect(EMP_DB)
    try:
        conn.execute("ATTACH DATABASE ? AS stock_src", (STOCK_DB,))
        seq_rows = conn.execute(
            """
            SELECT m.stock_code, m.wkpl_nm
            FROM nps_seq_map m
            JOIN stock_src.stock_universe u ON u.stock_code = m.stock_code
            WHERE m.wkpl_nm IS NOT NULL
              AND u.market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
              AND u.stock_type = '보통주'
              AND LENGTH(u.stock_code) = 6
            GROUP BY m.stock_code, m.wkpl_nm
            ORDER BY m.stock_code
            """
        ).fetchall()
    except sqlite3.Error as e:
        logger.warning(f"stock_universe 필터 적용 실패, nps_seq_map 전체 사용: {e}")
        seq_rows = conn.execute(
            """
            SELECT stock_code, wkpl_nm
            FROM nps_seq_map
            WHERE wkpl_nm IS NOT NULL
            GROUP BY stock_code, wkpl_nm
            ORDER BY stock_code
            """
        ).fetchall()
    conn.close()

    if months_back and months_back > 0 and not from_ym:
        conn = sqlite3.connect(EMP_DB)
        target_yms = [
            r[0] for r in conn.execute(
                """
                SELECT data_ym
                FROM nps_monthly
                GROUP BY data_ym
                HAVING COUNT(*) >= 100
                ORDER BY data_ym DESC
                LIMIT ?
                """,
                (months_back,),
            ).fetchall()
        ]
        conn.close()

        latest_ym = _detect_latest_data_ym()
        if latest_ym and latest_ym not in target_yms and _has_collectable_month(latest_ym):
            target_yms = [latest_ym] + target_yms
            target_yms = target_yms[:months_back]

        if target_yms:
            conn = sqlite3.connect(EMP_DB)
            placeholders = ",".join("?" for _ in target_yms)
            coverage = {
                r[0]: r[1] for r in conn.execute(
                    f"""
                    SELECT stock_code, COUNT(DISTINCT data_ym)
                    FROM nps_monthly
                    WHERE data_ym IN ({placeholders})
                    GROUP BY stock_code
                    """,
                    target_yms,
                ).fetchall()
            }
            conn.close()

            before = len(seq_rows)
            seq_rows = [
                row for row in seq_rows
                if coverage.get(row[0], 0) < len(target_yms)
            ]
            logger.info(
                f"최근 NPS 보완 대상월={','.join(target_yms)} "
                f"누락 대상={len(seq_rows)}/{before}종목"
            )

    if not seq_rows:
        logger.warning("nps_seq_map 비어 있음 — build_seq_map 먼저 실행하세요")
        return

    total_saved = 0
    total_companies = len(seq_rows)

    for idx, (sc, wkpl_nm) in enumerate(seq_rows, start=1):
        if not wkpl_nm:
            logger.debug(f"[{sc}] wkpl_nm 없음, 스킵")
            continue

        snapshots = _get_recent_company_snapshots(wkpl_nm, months_back, from_ym)
        if not snapshots:
            logger.debug(f"[{sc}] {wkpl_nm}: 월별 스냅샷 없음")
            continue

        company_saved = 0

        for seq, data_ym in snapshots:
            # 이미 수집한 데이터 스킵
            conn = sqlite3.connect(EMP_DB)
            already = conn.execute(
                "SELECT 1 FROM nps_monthly WHERE stock_code=? AND data_ym=?", (sc, data_ym)
            ).fetchone()
            conn.close()
            if already:
                continue

            time.sleep(0.2)
            body = _api_get("getPdAcctoSttusInfoSearchV2", {
                "seq": str(seq),
                "dataCrtYm": data_ym,
            })
            items = _get_items(body)

            if not items:
                continue

            it = items[0]
            new_hires    = int(it.get("nwAcqzrCnt", 0) or 0)
            terminations = int(it.get("lssJnngpCnt", 0) or 0)
            net_change   = new_hires - terminations

            conn = sqlite3.connect(EMP_DB)
            conn.execute("""
                INSERT OR IGNORE INTO nps_monthly
                    (stock_code, data_ym, new_hires, terminations, net_change, wkpl_count)
                VALUES (?,?,?,?,?,?)
            """, (sc, data_ym, new_hires, terminations, net_change, 1))
            conn.commit()
            conn.close()
            company_saved += 1
            total_saved += 1

        if company_saved > 0:
            logger.info(f"[{sc}] {wkpl_nm[:25]}: {company_saved}개월 저장 (스냅샷={len(snapshots)})")
        if idx % 100 == 0:
            logger.info(
                f"과거 데이터 소급 진행: {idx}/{total_companies}종목 처리, "
                f"누적 저장={total_saved}건"
            )

    logger.info(f"과거 데이터 소급 수집 완료: 총 {total_saved}건 저장")


# ── 테스트 ────────────────────────────────────────────────────────────────────
def test_single(stock_code: str):
    """단일 종목 NPS 데이터 조회 테스트"""
    stk_conn = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True)
    row = stk_conn.execute(
        "SELECT stock_name FROM stock_universe WHERE stock_code=?", (stock_code,)
    ).fetchone()
    stk_conn.close()

    if not row:
        logger.error(f"{stock_code}: stock_universe에 없음")
        return

    stock_name = row[0]
    logger.info(f"[{stock_code}] {stock_name}")

    patterns = _name_patterns(stock_name)
    logger.info(f"  시도할 패턴: {patterns}")

    for pattern in patterns:
        time.sleep(0.3)
        body = _api_get("getBassInfoSearchV2", {
            "wkplNm": pattern, "numOfRows": "50", "pageNo": "1"
        })
        items = _get_items(body)
        exact = [(it.get("seq"), it.get("wkplNm"), it.get("wkplJnngStcd"))
                 for it in items if it.get("wkplNm") == pattern]

        if not exact:
            logger.info(f"  [{pattern}]: 정확 일치 없음 (총 {body.get('totalCount',0)}건)")
            continue

        logger.info(f"  [{pattern}]: {len(exact)}건 일치")
        # 최신(최대) seq
        best_seq = max(s for s, _, _ in exact)
        d = _api_get("getDetailInfoSearchV2", {"seq": str(best_seq)})
        di = _get_items(d)
        jnngp = int(di[0].get("jnngpCnt", 0) or 0) if di else 0

        latest_ym = _detect_latest_data_ym()
        p = _api_get("getPdAcctoSttusInfoSearchV2", {
            "seq": str(best_seq),
            "dataCrtYm": latest_ym,
        })
        pi = _get_items(p)
        nw = int(pi[0].get("nwAcqzrCnt", 0) or 0) if pi else 0
        ls = int(pi[0].get("lssJnngpCnt", 0) or 0) if pi else 0

        logger.info(
            f"    최신 seq={best_seq} | 가입자={jnngp:,} "
            f"| 신규취득={nw:+d} | 상실={ls} | 순증={nw-ls:+d}"
        )
        break  # 첫 성공 패턴에서 중단


# ── 메인 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-map",  action="store_true", help="seq 매핑 구축")
    parser.add_argument("--collect",    action="store_true", help="현재월 수집")
    parser.add_argument("--test",       type=str,            help="단일 종목 테스트")
    parser.add_argument("--historical", action="store_true", help="3년치 과거 데이터 소급 수집")
    parser.add_argument("--ym",         type=str,            help="수집 년월 (기본: 현재월)")
    parser.add_argument("--limit",      type=int, default=300, help="처리 종목 수 (0=전체 코스피/코스닥)")
    parser.add_argument("--offset",     type=int, default=0, help="매핑 시작 offset (--build-map 전용)")
    parser.add_argument("--refresh-map", action="store_true", help="이미 매핑된 종목도 다시 조회")
    parser.add_argument("--fast-only", action="store_true", help="정규화 빠른 매칭만 수행하고 느린 패턴 fallback 생략")
    parser.add_argument("--months-back", type=int, default=36, help="소급 수집 개월수 (--historical 전용, 0=제한 없음)")
    parser.add_argument("--from-ym", type=str, help="소급 수집 시작년월 YYYYMM (--historical 전용)")
    args = parser.parse_args()

    init_db()

    if args.test:
        test_single(args.test)
    elif args.build_map:
        build_seq_map(limit=args.limit, refresh=args.refresh_map, fast_only=args.fast_only, offset=args.offset)
    elif args.collect:
        collect_monthly(target_ym=args.ym)
    elif args.historical:
        build_historical_data(
            months_back=None if args.months_back == 0 else args.months_back,
            from_ym=args.from_ym,
        )
    else:
        build_seq_map(limit=args.limit, refresh=args.refresh_map, fast_only=args.fast_only, offset=args.offset)
        collect_monthly(target_ym=args.ym)
