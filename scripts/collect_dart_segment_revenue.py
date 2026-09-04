"""
DART 사업보고서 HTML에서 사업부문별 매출액을 추출해 segment_revenue 테이블에 저장.

실행:
    python3 scripts/collect_dart_segment_revenue.py                   # 전종목
    python3 scripts/collect_dart_segment_revenue.py --limit 100       # 상위 100종목
    python3 scripts/collect_dart_segment_revenue.py --codes 005930,000270

표 구조 패턴:
  A. [부문명|제품|매출액|비중]  → 삼성전자, 삼성전기, SK하이닉스
  B. [부문명|매출액|비중|...]   → rowspan 없는 단순형
  C. [부문명|매출액/영업이익/총자산|금액|...]  → 현대차 rowspan형 (매출액 행만 추출)
"""
import argparse, io, logging, os, re, sqlite3, time, warnings, zipfile
from typing import Optional

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"

def _load_keys():
    keys = []
    try:
        for line in open(os.path.join(os.path.dirname(__file__), '..', '.env')):
            line = line.strip()
            for k in ['DART_API_KEY', 'DART_API_KEY2', 'DART_API_KEY3']:
                if line.startswith(k + '='):
                    v = line.split('=', 1)[1].strip()
                    if v:
                        keys.append(v)
    except Exception:
        pass
    if not keys:
        keys = [os.getenv("DART_API_KEY", "")]
    return [k for k in keys if k]

_KEYS = _load_keys()
_key_idx = [0]

def _dart_get(url, params, timeout=20):
    """DART API GET - 한도 초과 시 다음 키 자동 교체"""
    for attempt in range(len(_KEYS) + 1):
        key = _KEYS[_key_idx[0] % len(_KEYS)]
        params['crtfc_key'] = key
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                data = r.json() if 'document' not in url else r
                if hasattr(data, 'get') and data.get('status') == '020':
                    log.warning(f"DART 한도초과 키{_key_idx[0]}, 다음 키로 전환")
                    _key_idx[0] += 1
                    if _key_idx[0] >= len(_KEYS):
                        return None
                    continue
                return data
        except Exception as e:
            log.debug(f"DART 요청 실패: {e}")
        time.sleep(0.5)
    return None


def _get_latest_rcept(corp_code: str, year: int) -> Optional[str]:
    """사업보고서 rcept_no 조회"""
    data = _dart_get('https://opendart.fss.or.kr/api/list.json', {
        'corp_code': corp_code,
        'bgn_de': f'{year}0101',
        'end_de': f'{year}1231',
        'pblntf_ty': 'A',
        'page_no': '1',
        'page_count': '5',
    })
    if not data:
        return None
    for item in data.get('list', []):
        nm = item.get('report_nm', '')
        if '사업보고서' in nm and '반기' not in nm and '분기' not in nm:
            return item.get('rcept_no')
    return None


def _fetch_document(rcept_no: str) -> Optional[str]:
    """ZIP 다운로드 → 메인 XML 텍스트 반환"""
    r = _dart_get(
        'https://opendart.fss.or.kr/api/document.xml',
        {'rcept_no': rcept_no},
        timeout=45,
    )
    if r is None:
        return None
    raw = r.content
    if raw[:2] != b'PK':
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        main = [n for n in zf.namelist() if n.endswith('.xml') and '_' not in n]
        if not main:
            main = [n for n in zf.namelist() if n.endswith('.xml')]
        if not main:
            return None
        return zf.read(main[0]).decode('utf-8', errors='ignore')
    except Exception:
        return None


def _parse_num(s: str) -> Optional[float]:
    s = str(s).strip().replace(',', '').replace('\xa0', '').replace(' ', '')
    neg = '△' in s or '▲' in s or s.startswith('-')
    s = re.sub(r'[△▲\-+]', '', s)
    # 퍼센트 제거
    s = s.rstrip('%')
    if not s:
        return None
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def _is_valid_revenue(v: Optional[float]) -> bool:
    """유효한 매출액인지 (백만원 단위 기준, 1억 이상)"""
    if v is None:
        return False
    return abs(v) > 100  # 100백만원(1억) 이상


def _detect_unit(text: str) -> float:
    """단위 감지 → 억원 변환 배수 반환"""
    if '백만원' in text or '(단위 : 백만원)' in text or '단위:백만원' in text:
        return 0.01    # 백만원 → 억원
    if '천원' in text:
        return 0.00001  # 천원 → 억원
    if re.search(r'단위\s*[:：]\s*원', text) and '억원' not in text:
        return 1e-8    # 원 → 억원
    return 1.0          # 기본: 억원


def _get_prev_text(tbl, chars=400) -> str:
    """표 앞 텍스트 추출 (단위 감지용)"""
    txt = ''
    for sib in tbl.previous_siblings:
        if hasattr(sib, 'get_text'):
            txt = sib.get_text() + txt
        elif isinstance(sib, str):
            txt = sib + txt
        if len(txt) > chars:
            break
    return txt


# 합계/소계 키워드
_TOTAL_KW = {'합계', '총계', '소계', '계', 'total', '소 계', '합  계', '총  계', '합 계', '소합계'}
# 제외할 부문명 키워드
_SKIP_KW = {'구분', '부문', '사업부문', '사업', '제품', '매출유형', '매출액', '금액', '비중', '비율',
            '주요제품', '주요', '품목', '비고', '항목', '대분류', '유형', '용도', '상표',
            '보상', '임직원', '임원', '이름', '이사', '감사', '성명', ''}


# 재고자산/원재료 관련 키워드 - 이 표는 제외
_INVENTORY_KW = {'재고자산', '재공품', '원재료', '저장품', '미착품', '원재료및저장품',
                 '재고', '제품및반제품', '반제품및재공품'}

# 표 앞 텍스트에 있으면 사업부문 표로 간주
_SECTION_KW = {'주요 제품', '주요제품', '영업부문', '사업부문', '제품 및 서비스',
               '제품및서비스', '매출 현황', '매출현황', '사업의 내용', '부문별',
               '영업 부문', '사업 개요', '세그먼트'}


def _table_position_score(tbl_idx: int, total_tables: int) -> float:
    """문서 앞쪽 표일수록 높은 점수 (사업의 내용 섹션이 앞에 위치)"""
    ratio = tbl_idx / max(total_tables, 1)
    if ratio < 0.1:
        return 5.0
    if ratio < 0.2:
        return 3.0
    if ratio < 0.35:
        return 1.0
    return 0.0


def extract_segment_revenue(soup: BeautifulSoup) -> list[dict]:
    """
    사업보고서에서 사업부문별 매출액 추출.
    반환: [{'segment': '부문명', 'revenue_억': float, 'ratio_pct': float|None}, ...]
    """
    tables = soup.find_all('table')
    total_tables = len(tables)
    best = None
    best_score = 0

    for i, tbl in enumerate(tables):
        tbl_text = tbl.get_text()

        # 필수 키워드
        if '매출액' not in tbl_text and '매출' not in tbl_text:
            continue
        if not any(kw in tbl_text for kw in ['부문', '사업부', '세그먼트', 'Segment']):
            continue

        # 재고자산 표 제외
        inv_count = sum(1 for kw in _INVENTORY_KW if kw in tbl_text)
        if inv_count >= 3:
            continue

        rows = tbl.find_all('tr')
        if len(rows) < 3:
            continue

        # 컬럼 수가 너무 많으면 복잡한 IFRS 주석 표일 가능성 높음 (제외)
        header_col_counts = [len(r.find_all(['td','th'])) for r in rows[:3] if r.find_all(['td','th'])]
        if not header_col_counts:
            continue
        max_cols = max(header_col_counts)
        if max_cols > 8:
            continue

        # 표 앞 텍스트 가져오기
        prev_txt = _get_prev_text(tbl)

        # 표 앞에 재고자산/임원/주석 섹션 헤딩이 있으면 제외
        if any(kw in prev_txt for kw in ['재고자산', '재고 자산', '재고(', '재고\n',
                                          '임직원', '임원', '종속기업', '특수관계자',
                                          '영업권', '무형자산', '유형자산']):
            continue

        # 단위 감지
        unit_mult = _detect_unit(prev_txt + tbl_text[:300])

        cells_all = []
        for row in rows:
            cells = [c.get_text(strip=True).replace('\xa0', '').replace('​', '') for c in row.find_all(['td', 'th'])]
            cells_all.append(cells)

        # --- 패턴 A: 헤더에 '매출액' 컬럼이 명시적 ---
        # 예: [부문명|제품|매출액|비중]
        header = cells_all[0] if cells_all else []
        rev_col = None
        ratio_col = None
        for j, h in enumerate(header):
            if h in ('매출액', '매출', '수익', '순매출액', '영업수익', '매출액(백만원)'):
                rev_col = j
            if any(kw in h for kw in ['비중', '비율', '%', 'Ratio']):
                ratio_col = j

        # --- 패턴 B: [부문명|매출액/영업이익/총자산 행] rowspan 형 (현대차) ---
        # 탐지: cells[1] == '매출액' 형태의 행이 있으면
        rowspan_pattern = any(
            len(c) >= 3 and c[1] in ('매출액', '매출', '수익')
            for c in cells_all[1:8]
        )

        segments = []

        if rowspan_pattern:
            # 현대차 패턴
            cur_seg = None
            for cells in cells_all[1:]:
                if not cells:
                    continue
                if len(cells) >= 3 and cells[1] in ('매출액', '매출', '수익', '순매출액'):
                    # 매출액 행 - cur_seg에 할당
                    seg_name = cur_seg
                    if seg_name and seg_name.lower() not in _SKIP_KW and '합계' not in seg_name:
                        val = _parse_num(cells[2]) if len(cells) > 2 else None
                        if _is_valid_revenue(val):
                            ratio = None
                            for j in range(3, min(5, len(cells))):
                                r = _parse_num(cells[j])
                                if r is not None and 0 < abs(r) <= 100:
                                    ratio = r
                                    break
                            segments.append({'segment': seg_name, 'revenue_억': val * unit_mult, 'ratio_pct': ratio})
                elif cells[0] and cells[0].lower() not in _SKIP_KW:
                    # 부문명 행
                    if not any(kw in cells[0] for kw in ['합계', '총계', '소계']):
                        cur_seg = cells[0].strip()

        elif rev_col is not None:
            # 패턴 A: 명시적 매출액 컬럼
            data_start = 1
            # 2중 헤더 대응 (LG에너지솔루션 패턴)
            if cells_all[1] and all(not _is_valid_revenue(_parse_num(c)) for c in cells_all[1] if c):
                data_start = 2

            for cells in cells_all[data_start:]:
                if not cells or rev_col >= len(cells):
                    continue
                seg_name = cells[0].strip()
                if not seg_name or seg_name.lower() in _SKIP_KW:
                    continue
                if seg_name.rstrip(' ') in _TOTAL_KW or any(seg_name.startswith(k) for k in ['합계', '총계', '소계', '합  계']):
                    continue

                val = _parse_num(cells[rev_col])
                if not _is_valid_revenue(val):
                    continue

                ratio = None
                if ratio_col and ratio_col < len(cells):
                    ratio = _parse_num(cells[ratio_col])
                    if ratio is not None and abs(ratio) > 100:
                        ratio = None

                segments.append({'segment': seg_name, 'revenue_억': val * unit_mult, 'ratio_pct': ratio})

        else:
            # 패턴 C: 헤더에 매출액 없음, 2~3번째 열이 숫자인 경우
            for cells in cells_all[1:]:
                if len(cells) < 2:
                    continue
                seg_name = cells[0].strip()
                if not seg_name or seg_name.lower() in _SKIP_KW:
                    continue
                if any(kw in seg_name for kw in ['합계', '총계', '소계']):
                    continue

                # 두 번째 셀이 숫자
                val = _parse_num(cells[1])
                if not _is_valid_revenue(val):
                    continue

                ratio = None
                if len(cells) > 2:
                    r = _parse_num(cells[2])
                    if r is not None and 0 < abs(r) <= 100:
                        ratio = r

                segments.append({'segment': seg_name, 'revenue_억': val * unit_mult, 'ratio_pct': ratio})

        # 점수 계산 (부문 수, 숫자 합리성)
        if not segments:
            continue

        # 유효 부문: 2~20개 사이
        n = len(segments)
        if n < 2 or n > 20:
            continue

        # 합리성 검사: 절댓값 합계가 너무 크거나 작으면 제외 (단위 오류 방지)
        total_rev = sum(abs(s['revenue_억']) for s in segments if s['revenue_억'] and s['segment'] not in ('기타', '연결조정', '내부거래'))
        if total_rev < 10 or total_rev > 1e8:  # 10억~10조조 범위
            continue

        # 비중 정보 있으면 보너스
        has_ratio = sum(1 for s in segments if s['ratio_pct'] is not None)
        # 문서 앞쪽 표 선호 (사업의 내용 섹션이 재무제표보다 앞)
        pos_score = _table_position_score(i, total_tables)
        # 명시적 매출액 컬럼이면 보너스
        explicit_hdr = 2 if rev_col is not None else 0
        score = n * 2 + has_ratio + pos_score * 5 + explicit_hdr

        if score > best_score:
            best_score = score
            best = segments

    return best or []


def collect_segments(
    stock_code: str,
    corp_code: str,
    year: int,
    conn: sqlite3.Connection,
) -> int:
    """한 종목 한 연도의 사업부문별 매출 수집 → DB 저장. 반환: 저장 행 수"""
    rcept_no = _get_latest_rcept(corp_code, year)
    if not rcept_no:
        return 0

    xml_text = _fetch_document(rcept_no)
    if not xml_text:
        return 0

    soup = BeautifulSoup(xml_text, 'lxml')
    segments = extract_segment_revenue(soup)
    if not segments:
        return 0

    # segment_revenue에 저장 (segment_name != '연결전체' 인 행)
    saved = 0
    for seg in segments:
        seg_name = seg['segment']
        rev = seg['revenue_억']
        ratio = seg.get('ratio_pct')
        if not seg_name or rev is None:
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO segment_revenue
                (stock_code, corp_code, year, quarter, segment_name, revenue, revenue_pct, report_type)
                VALUES (?, ?, ?, 0, ?, ?, ?, 'CFS')
            """, (stock_code, corp_code, year, seg_name, rev, ratio))
            saved += 1
        except sqlite3.Error as e:
            log.debug(f"Insert 실패 {stock_code} {year} {seg_name}: {e}")

    if saved:
        conn.commit()

    return saved


def main():
    parser = argparse.ArgumentParser(description="DART 사업보고서 부문별 매출 수집")
    parser.add_argument('--codes', help="쉼표 구분 종목코드")
    parser.add_argument('--limit', type=int, default=500, help="상위 N종목 (시총 순)")
    parser.add_argument('--years', default='2022,2023,2024', help="수집 연도 (쉼표 구분)")
    parser.add_argument('--overwrite', action='store_true', help="기존 데이터 덮어쓰기")
    args = parser.parse_args()

    years = [int(y) for y in args.years.split(',')]

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row

    # 대상 종목 조회
    if args.codes:
        codes = args.codes.split(',')
        targets = conn.execute(f"""
            SELECT su.stock_code, sr.corp_code, su.stock_name
            FROM stock_universe su
            JOIN (
                SELECT stock_code, corp_code,
                       ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY year DESC) rn
                FROM segment_revenue WHERE corp_code IS NOT NULL AND corp_code != ''
            ) sr ON sr.stock_code = su.stock_code AND sr.rn = 1
            WHERE su.stock_code IN ({','.join('?'*len(codes))})
        """, codes).fetchall()
    else:
        targets = conn.execute(f"""
            SELECT su.stock_code, sr.corp_code, su.stock_name
            FROM stock_universe su
            JOIN (
                SELECT stock_code, corp_code,
                       ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY year DESC) rn
                FROM segment_revenue WHERE corp_code IS NOT NULL AND corp_code != ''
            ) sr ON sr.stock_code = su.stock_code AND sr.rn = 1
            WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
            ORDER BY su.market_cap DESC NULLS LAST
            LIMIT {args.limit}
        """).fetchall()

    log.info(f"대상: {len(targets)}종목, 연도: {years}")

    total_saved = 0
    for i, row in enumerate(targets):
        code, corp_code, name = row['stock_code'], row['corp_code'], row['stock_name']

        for year in years:
            # 이미 수집된 경우 스킵 (overwrite 옵션 없으면)
            if not args.overwrite:
                existing = conn.execute("""
                    SELECT COUNT(*) FROM segment_revenue
                    WHERE stock_code=? AND year=? AND segment_name != '연결전체'
                """, (code, year)).fetchone()[0]
                if existing > 0:
                    continue

            try:
                n = collect_segments(code, corp_code, year, conn)
                if n:
                    total_saved += n
                    log.info(f"[{i+1}/{len(targets)}] {name}({code}) {year}: {n}부문 저장")
            except Exception as e:
                log.warning(f"[{i+1}] {name}({code}) {year} 오류: {e}")

            time.sleep(0.15)  # DART 요청 간격

        if (i + 1) % 50 == 0:
            log.info(f"진행: {i+1}/{len(targets)} 완료, 누적 {total_saved}행")

    conn.close()
    log.info(f"완료: 총 {total_saved}행 저장")


if __name__ == '__main__':
    main()
