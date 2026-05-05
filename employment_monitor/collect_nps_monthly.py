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
import time
from datetime import datetime

try:
    from dateutil.relativedelta import relativedelta as _relativedelta
    _HAS_DATEUTIL = True
except ImportError:
    _HAS_DATEUTIL = False

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ── 경로 설정 ────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
EMP_DB   = os.path.join(_DIR, "employment.db")
STOCK_DB = "/Applications/stock_dashboard/stock.db"

# ── NPS API 설정 ─────────────────────────────────────────────────────────────
API_KEY  = "93b5be4d33f6d76af92ead610f161975e4dca7cd021b60e97d40348ab0d824da"
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


def _find_latest_seq(stock_name: str) -> tuple | None:
    """
    회사명 패턴으로 NPS 검색 → 정확히 일치하는 사업장명 중 최신(최대) seq 반환.
    반환: (seq, wkpl_nm, jnngp_cnt) 또는 None
    """
    patterns = _name_patterns(stock_name)
    candidates = []  # (seq, wkpl_nm)

    for pattern in patterns:
        time.sleep(0.25)
        body = _api_get("getBassInfoSearchV2", {
            "wkplNm": pattern,
            "numOfRows": "100", "pageNo": "1"
        })
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
def build_seq_map(limit: int = 300):
    """
    stock_universe 상위 N개 종목에 대해 최신 NPS seq를 찾아 nps_seq_map에 저장.
    매월 실행해야 seq가 갱신됨 (seq = 월별 스냅샷 번호).
    """
    stk_conn = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True)
    universe = {r[0]: r[1] for r in stk_conn.execute(
        "SELECT stock_code, stock_name FROM stock_universe "
        "ORDER BY market_cap DESC NULLS LAST LIMIT ?", (limit,)
    ).fetchall()}
    stk_conn.close()

    if not universe:
        logger.error("stock_universe 비어 있음")
        return

    current_ym = datetime.now().strftime("%Y%m")
    total = saved = failed = 0

    for stock_code, stock_name in universe.items():
        total += 1
        result = _find_latest_seq(stock_name)

        if result is None:
            logger.debug(f"[{stock_code}] {stock_name}: 매핑 실패")
            failed += 1
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

    logger.info(f"seq 매핑 완료: {total}종목 처리, {saved}개 저장, {failed}개 실패")


# ── 2단계: 월별 입사/퇴사 수집 ──────────────────────────────────────────────
def collect_monthly(target_ym: str = None):
    """
    nps_seq_map의 최신 seq로 신규취득/상실 수집 → nps_monthly에 저장.
    월별로 build_seq_map → collect_monthly 순서로 실행.
    """
    if target_ym is None:
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
        body = _api_get("getPdAcctoSttusInfoSearchV2", {"seq": str(seq)})
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
                (stock_code, data_ym, new_hires, terminations, net_change)
            VALUES (?,?,?,?,?)
        """, (sc, target_ym, new_hires, terminations, net_change))
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
                SELECT data_ym FROM nps_monthly
                ORDER BY data_ym DESC LIMIT 1 OFFSET ?
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
def build_historical_data(months_back: int = 36):
    """
    이미 수집된 nps_seq_map의 회사들에 대해 과거 데이터를 소급 수집.
    NPS API는 날짜 파라미터 없음 → seq 순서로 상대 월 추론.

    각 회사의 wkpl_nm으로 API에서 ALL seqs 수집(최대 5페이지 × 100건).
    최신 seq → 현재월, 다음 seq → 3개월 전, ... 형식으로 날짜 추론.
    """
    if not _HAS_DATEUTIL:
        logger.error("dateutil 패키지 필요: pip3 install python-dateutil")
        return

    from dateutil.relativedelta import relativedelta

    conn = sqlite3.connect(EMP_DB)
    seq_rows = conn.execute(
        "SELECT stock_code, seq, wkpl_nm FROM nps_seq_map ORDER BY stock_code"
    ).fetchall()
    conn.close()

    if not seq_rows:
        logger.warning("nps_seq_map 비어 있음 — build_seq_map 먼저 실행하세요")
        return

    total_saved = 0
    max_quarters = months_back // 3 + 1

    for sc, latest_seq, wkpl_nm in seq_rows:
        if not wkpl_nm:
            logger.debug(f"[{sc}] wkpl_nm 없음, 스킵")
            continue

        # 해당 회사의 모든 historical seqs 수집 (최대 5페이지)
        all_seqs = []
        for page in range(1, 6):
            time.sleep(0.3)
            body = _api_get("getBassInfoSearchV2", {
                "wkplNm": wkpl_nm,
                "numOfRows": "100", "pageNo": str(page)
            })
            items = _get_items(body)
            if not items:
                break
            for it in items:
                nm  = it.get("wkplNm", "")
                seq = it.get("seq")
                if nm == wkpl_nm and seq:
                    all_seqs.append(int(seq))

            total_cnt = body.get("totalCount", 0)
            if page * 100 >= int(total_cnt or 0):
                break

        if not all_seqs:
            logger.debug(f"[{sc}] {wkpl_nm}: seqs 없음")
            continue

        all_seqs = sorted(set(all_seqs), reverse=True)  # 최신 seq 먼저, 중복 제거

        # 최대 max_quarters개 수집
        collect_seqs = all_seqs[:max_quarters]
        company_saved = 0

        for i, seq in enumerate(collect_seqs):
            # 날짜 추론: i=0 → 현재월, i=1 → 3개월 전, ...
            dt = datetime.now() - relativedelta(months=i * 3)
            data_ym = dt.strftime("%Y%m")

            # 이미 수집한 데이터 스킵
            conn = sqlite3.connect(EMP_DB)
            already = conn.execute(
                "SELECT 1 FROM nps_monthly WHERE stock_code=? AND data_ym=?", (sc, data_ym)
            ).fetchone()
            conn.close()
            if already:
                continue

            time.sleep(0.2)
            body = _api_get("getPdAcctoSttusInfoSearchV2", {"seq": str(seq)})
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
                    (stock_code, data_ym, new_hires, terminations, net_change)
                VALUES (?,?,?,?,?)
            """, (sc, data_ym, new_hires, terminations, net_change))
            conn.commit()
            conn.close()
            company_saved += 1
            total_saved += 1

        if company_saved > 0:
            logger.info(f"[{sc}] {wkpl_nm[:25]}: {company_saved}개 기간 저장 (총 seqs={len(all_seqs)})")

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

        p = _api_get("getPdAcctoSttusInfoSearchV2", {"seq": str(best_seq)})
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
    parser.add_argument("--limit",      type=int, default=300, help="처리 종목 수")
    parser.add_argument("--months-back", type=int, default=36, help="소급 수집 개월수 (--historical 전용)")
    args = parser.parse_args()

    init_db()

    if args.test:
        test_single(args.test)
    elif args.build_map:
        build_seq_map(limit=args.limit)
    elif args.collect:
        collect_monthly(target_ym=args.ym)
    elif args.historical:
        build_historical_data(months_back=args.months_back)
    else:
        build_seq_map(limit=args.limit)
        collect_monthly(target_ym=args.ym)
