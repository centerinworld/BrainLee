"""
legacy_dart_recollect.py — legacy_collected 분기 전량 DART 재수집
=================================================================
대상: 활성 종목의 financial_data + cash_flow_data legacy_collected 분기행
방법: DART 1분기(11013)/반기(11012)/3분기(11014) 재호출
      누적/standalone 자동 판별 → Q1/Q2/Q3/Q4 standalone 계산
      CF(OCF/ICF/FCF/CapEx/DEP) 동시 수집

실행:
  python3 scripts/ops/legacy_dart_recollect.py                  # 전체 (2016~2025)
  python3 scripts/ops/legacy_dart_recollect.py --year 2023      # 특정 연도
  python3 scripts/ops/legacy_dart_recollect.py --limit 200      # 200개사만
  python3 scripts/ops/legacy_dart_recollect.py --resume         # 체크포인트에서 재개

체크포인트: scratch/.recollect_checkpoint.json  (DART 한도 초과 시 자동 저장)
결과 로그:  scratch/recollect_result_{ts}.csv
"""
from __future__ import annotations
from db_compat import connect_primary_db
import argparse, csv, io, json, time, sqlite3, zipfile, sys
import xml.etree.ElementTree as ET
import requests
from pathlib import Path
from datetime import datetime

RUNTIME_ROOT = Path(__file__).resolve().parents[2]  # scripts/ops/ 기준(2026-10-03 scratch에서 이동)
CKPT_PATH = RUNTIME_ROOT / "scratch" / ".recollect_checkpoint.json"
OUT_DIR   = RUNTIME_ROOT / "scratch"

# ── DART API ─────────────────────────────────────────────────────────────────

def _load_dart_keys() -> list[str]:
    keys = {}
    for line in (RUNTIME_ROOT / ".env").read_text().splitlines():
        if line.startswith("DART_API_KEY="):
            keys[1] = line.split("=", 1)[1].strip()
        elif line.startswith("DART_API_KEY2="):
            keys[2] = line.split("=", 1)[1].strip()
        elif line.startswith("DART_API_KEY3="):
            keys[3] = line.split("=", 1)[1].strip()
    if not keys:
        raise RuntimeError("DART_API_KEY 없음")
    return [keys[k] for k in sorted(keys)]

# 키 로테이션 관리
_DART_KEYS  = _load_dart_keys()
_key_idx    = 0          # 현재 사용 중인 키 인덱스
_key_calls  = [0] * len(_DART_KEYS)  # 키별 호출 횟수

def _current_key() -> str:
    return _DART_KEYS[_key_idx]

def _rotate_key(reason: str = "") -> bool:
    """다음 키로 전환. 모든 키 소진 시 False 반환."""
    global _key_idx
    next_idx = _key_idx + 1
    if next_idx >= len(_DART_KEYS):
        return False
    _key_idx = next_idx
    print(f"\n🔄 DART 키 전환 → KEY{_key_idx + 1} ({reason})", flush=True)
    return True

# 하위 호환
DART_KEY = _current_key()
_CORP_MAP: dict[str, str] = {}

_CORPCODE_CACHE = "/tmp/CORPCODE.xml"

def build_corp_map():
    global _CORP_MAP
    print("DART corp_code 매핑 로드 중...", end="", flush=True)

    # 캐시 파일 우선 사용 (API 한도 절약)
    xml_data = None
    cache_path = Path(_CORPCODE_CACHE)
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < 86400 * 7:
        xml_data = cache_path.read_bytes()
        print(" (캐시)", end="", flush=True)
    else:
        # API 호출 (최대 3회 재시도)
        for attempt in range(3):
            try:
                r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml",
                                 params={"crtfc_key": DART_KEY}, timeout=30)
                z = zipfile.ZipFile(io.BytesIO(r.content))
                xml_data = z.read("CORPCODE.xml")
                cache_path.write_bytes(xml_data)  # 캐시 저장
                break
            except Exception as e:
                if attempt == 2:
                    raise
                print(f" (retry {attempt+1})", end="", flush=True)
                time.sleep(5)

    root = ET.fromstring(xml_data)
    for item in root.findall("list"):
        sc = item.findtext("stock_code", "").strip()
        cc = item.findtext("corp_code", "")
        if sc and cc:
            _CORP_MAP[sc] = cc
    print(f" {len(_CORP_MAP)}건")

def fetch_dart(corp_code: str, year: int, reprt_code: str, fs_div: str = "CFS") -> list[dict]:
    """DART fnlttSinglAcntAll 호출 → row list (빈 리스트 = 없음, RuntimeError = 한도초과)"""
    global _key_calls
    while True:
        key = _current_key()
        _key_calls[_key_idx] += 1
        r = requests.get(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            params={"crtfc_key": key, "corp_code": corp_code,
                    "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div},
            timeout=15,
        )
        data = r.json()
        if data.get("status") != "020":
            return data.get("list", [])

        print(f"\n⚠️  KEY{_key_idx+1} 한도초과 (호출 {_key_calls[_key_idx]}회)", flush=True)
        if not _rotate_key("한도초과"):
            raise RuntimeError("DART_LIMIT")

# ── P&L 추출 ─────────────────────────────────────────────────────────────────

_REV_KW  = ("매출", "영업수익", "수익")  # fix_q4_neg_dart_9m.py와 동일 (검증된 로직)
# "기타" 추가(2026-08-10): collectors/dart_collector.py·fnguide_financial_collector.py에서
# 오늘 발견한 "기타영업수익"(부수 소액항목) 오매칭과 동일 클래스 위험 방어(이 파일은 max값
# 선택이라 원래도 상대적으로 덜 취약하지만, 방어비용이 낮아 동일하게 적용).
_REV_EX  = ("원가", "총이익", "차감", "비용", "손실", "이익", "기타")
_OP_KW   = ("영업이익", "영업손익", "영업손이익")
# "계속"/"중단"/"신용손실충당금"/"반영전" 추가(2026-08-10): 이 스크립트는 매일 00:30
# DART재무재수집으로 실행되는 별도의 독립 파서(collectors/dart_collector.py와 무관)라
# 오늘 그쪽에서 고친 방어가 전혀 적용돼 있지 않았음 — "계속영업이익"/"중단영업이익"/
# "신용손실충당금 반영전 영업이익"이 "영업이익" 키워드에 오매칭되는 동일 클래스 위험을
# 여기서도 방어. financial_data는 COALESCE(NULL만 채움)로 쓰기 때문에 기존에 이미 올바른
# 값이 있는 행은 영향받지 않으나, NULL 갭을 이 스크립트가 메울 때 오염된 값을 채워넣을
# 위험은 그대로 있었음.
_OP_EX   = ("영업외", "계속", "중단", "신용손실충당금", "반영전")
_NET_KW  = ("당기순이익", "당기순손익", "당기순손이익", "분기순이익", "반기순이익",
            "당기순이익(손실)", "당기순손익(이익)", "연간순이익")
# 2026-09-19: dart_collector.py의 더 넓은 키워드 목록(분기순이익/반기순이익/연간순이익 등)이
# 이 독립 파서에는 반영돼 있지 않아, 소형사/특정 보고서식이 분기순이익 등의 라벨을 쓰는 경우
# net_income이 계속 NULL로 남는 원인이 됐음(2016~2024년 7,613건 미해결 확인) - 동기화.
# "계속"/"중단" 추가(2026-08-10): 위와 동일 이유 — "계속영업연결당기순이익"류가
# "당기순이익" 키워드에 오매칭되는 것을 방지.
_NET_EX  = ("비지배", "지배주주", "계속", "중단")

def _pl_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if "손익" in r.get("sj_nm","") or "포괄손익" in r.get("sj_nm","")]

def _parse_amount(row: dict) -> float | None:
    s = row.get("thstrm_amount","").replace(",","").strip()
    if not s or s == "-":
        return None
    try:
        v = float(s)
        return v if v != 0 else None
    except:
        return None

def _extract_field_first(pl: list[dict], kws: tuple, excl: tuple) -> float | None:
    """첫 번째 매칭 반환 (영업이익, 순이익 등 단일 항목용)"""
    for row in pl:
        label = row.get("account_nm","")
        if any(k in label for k in kws) and not any(e in label for e in excl):
            v = _parse_amount(row)
            if v is not None:
                return v
    return None

def _extract_revenue(pl: list[dict]) -> float | None:
    """
    매출 추출 — 후보 중 최대값 선택
    이유: DART P&L 행 순서가 역순인 회사가 많아 (기아 등)
         기타수익/금융수익이 매출액보다 먼저 나옴 → max로 정확히 식별
    매출액(수십조) >> 기타수익/금융수익(수천억) → max가 항상 올바른 값
    """
    candidates: list[float] = []
    for row in pl:
        label = row.get("account_nm","")
        if any(k in label for k in _REV_KW) and not any(e in label for e in _REV_EX):
            v = _parse_amount(row)
            if v is not None and v > 0:
                candidates.append(v)
    return max(candidates) if candidates else None

def _by_account_id(pl: list[dict], ids: tuple) -> float | None:
    """account_id 기반 추출 - 적자 회사의 '영업손실'/'당기순손실' 계정명은 키워드로 못 잡음(2026-09-24)."""
    for want in ids:
        for row in pl:
            if row.get("account_id") == want:
                v = _parse_amount(row)
                if v is not None:
                    return v
    return None


def extract_pl(rows: list[dict]) -> dict:
    pl = _pl_rows(rows)
    op_id = _by_account_id(pl, ("dart_OperatingIncomeLoss", "ifrs-full_OperatingIncomeLoss"))
    ni_id = _by_account_id(pl, ("ifrs-full_ProfitLoss",))
    return {
        "revenue":          _extract_revenue(pl),
        "operating_profit": op_id if op_id is not None else _extract_field_first(pl, _OP_KW,  _OP_EX),
        "net_income":       ni_id if ni_id is not None else _extract_field_first(pl, _NET_KW, _NET_EX),
    }

# ── CF 추출 ──────────────────────────────────────────────────────────────────

def _cf_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if "현금흐름" in r.get("sj_nm","")]

def _cf_extract(cf: list[dict], kws: tuple) -> float | None:
    for row in cf:
        label = row.get("account_nm","").replace(" ","")
        if any(k.replace(" ","") in label for k in kws):
            v = _parse_amount(row)
            if v is not None:
                return v
    return None

def extract_cf(rows: list[dict]) -> dict:
    cf = _cf_rows(rows)
    ocf  = _cf_extract(cf, ("영업활동현금흐름","영업활동으로인한현금흐름"))
    icf  = _cf_extract(cf, ("투자활동현금흐름","투자활동으로인한현금흐름"))
    fcf  = _cf_extract(cf, ("재무활동현금흐름","재무활동으로인한현금흐름"))
    cash = _cf_extract(cf, ("기말현금및현금성자산","현금및현금성자산기말잔액","기말의현금및현금성자산"))
    capex = _cf_extract(cf, ("유형자산취득","유형자산의취득","유형자산및무형자산취득",
                             "유형자산및무형자산의취득","설비투자"))
    # D&A: 이미 합쳐진 "감가상각비및무형자산상각비" 류가 있으면 그대로 사용,
    # 없으면 자산군별(유형자산/무형자산/사용권자산/투자부동산) 개별 항목을 전부 합산.
    # 2026-08-10 수정: 기존엔 우선순위 리스트에서 첫 매칭 하나만 쓰고 break — 000080/000100/
    # 000140(collectors/dart_collector.py 오늘 실측) 같이 자산군별로 별도 행 공시하는
    # 회사에서 나머지 자산군이 누락돼 총액이 최대 1/3 수준으로 과소집계됐음. "대손상각비"는
    # 이 스크립트의 모든 키워드가 "감가"/"무형자산"/"감모" 접두어를 요구해 원천적으로
    # 오매칭 안 됨(collectors 쪽과 달리 bare "상각비" 키워드가 없음 — 확인됨).
    _dep_combined_kws = (
        "감가상각비및무형자산상각비",
        "감가상각및상각비",
        "유무형자산감가상각비",
        "유형자산및무형자산상각",
    )
    _dep_component_kws = (
        "감가상각비",          # 유형자산 감가상각(단독 라벨 또는 "감가상각비(유형자산)" 등 포함)
        "유형자산감가상각비",
        "유형자산감가상각",
        "유형자산의감가상각비",
        "유형자산의감가상각",
        "사용권자산감가상각비",  # IFRS 16, "감가상각비(사용권자산)" 등 다른 어순은 별도 처리
        "무형자산상각비",
        "무형자산의상각",
        "투자부동산감가상각비",
        "감모상각비",
    )
    dep = None
    for kw in _dep_combined_kws:
        v = _cf_extract(cf, (kw,))
        if v is not None and v > 0:
            dep = v
            break
    if dep is None:
        # 자산군별 개별 항목 전부 순회하며 "같은 행을 중복으로 세지 않고" 합산.
        seen_idx: set = set()
        total = 0.0
        for row in cf:
            label = row.get("account_nm", "").replace(" ", "")
            row_id = id(row)
            if row_id in seen_idx:
                continue
            if "대손" in label or "손상" in label:
                continue
            if any(kw in label for kw in _dep_component_kws):
                v = _parse_amount(row)
                if v is not None and v > 0:
                    total += v
                    seen_idx.add(row_id)
        if total > 0:
            dep = total
    # capex 부호: 지출이므로 절댓값
    if capex is not None:
        capex = abs(capex)
    return {"ocf": ocf, "icf": icf, "fcf": fcf,
            "cash_end": cash, "capex": capex, "depreciation": dep}

# ── 누적/standalone 판별 및 분기 계산 ────────────────────────────────────────

def _validate_quarters(q1, q2, q3, q4, annual) -> bool:
    """
    분기 값 합리성 검증
    1) 음수 없음
    2) Q123/Annual: 20%~125%
    3) Q4 ≤ Annual * 0.75
    4) 분기 균등성: min(Q1,Q2,Q3) ≥ max(Q1,Q2,Q3) * 0.10
       (어느 분기도 최대 분기의 10% 미만이면 파싱오류 의심)
    """
    if any(v is not None and v < 0 for v in (q2, q3, q4)):
        return False
    # 음이 아닌 Q2,Q3 선택
    present = [v for v in (q1, q2, q3) if v is not None and v > 0]
    if not present:
        return False

    q123 = sum(present)
    if annual and annual > 0:
        ratio = q123 / annual
        if ratio < 0.20 or ratio > 1.25:
            return False
        if q4 is not None and q4 > annual * 0.75:
            return False

    # 분기 균등성: 최대값의 10% 이상이어야
    if len(present) >= 2:
        min_v, max_v = min(present), max(present)
        if max_v > 0 and min_v / max_v < 0.10:
            return False

    return True

def compute_quarters(q1_val: float | None,
                     h1_val: float | None,
                     nm_val: float | None,
                     annual: float | None) -> dict:
    """
    누적/standalone 양방향 시도:

    Pass 1 (누적 가정):  Q2=H1-Q1, Q3=9M-H1, Q4=Annual-9M
    Pass 2 (standalone): Q2=H1,    Q3=9M,    Q4=Annual-Q1-H1-9M

    합리성 검증 (20%≤Q123/Annual≤125%, Q4≤75%) 통과 시 채택
    둘 다 실패하면 ok=False (legacy_collected 유지)
    """
    result = {k: None for k in ("Q1","Q2","Q3","Q4","h1_cum","nm_cum","ok")}
    result["ok"] = False

    if not q1_val or q1_val <= 0:
        return result
    result["Q1"] = q1_val

    h1 = h1_val if (h1_val and h1_val > 0) else None
    nm = nm_val if (nm_val and nm_val > 0) else None

    # ── Pass 1: 누적 가정 ────────────────────────────────────────────────
    if h1 is not None:
        q2_c = h1 - q1_val
        h1_total_c = h1
        if nm is not None:
            q3_c = nm - h1_total_c
            q4_c = (annual - nm) if (annual and annual > 0) else None
        else:
            q3_c = None
            q4_c = (annual - h1_total_c) if (annual and annual > 0) else None

        if _validate_quarters(q1_val, q2_c, q3_c, q4_c, annual):
            result.update({"Q2": q2_c, "Q3": q3_c, "Q4": q4_c,
                           "h1_cum": True, "nm_cum": (nm is not None), "ok": True})
            return result

    # ── Pass 2: standalone 가정 ─────────────────────────────────────────
    if h1 is not None:
        q2_s = h1                            # H1 = Q2 standalone
        q3_s = nm                            # 9M = Q3 standalone (None ok)
        if annual and annual > 0:
            q4_s = annual - q1_val - (h1 or 0) - (nm or 0)
        else:
            q4_s = None

        if _validate_quarters(q1_val, q2_s, q3_s, q4_s, annual):
            result.update({"Q2": q2_s, "Q3": q3_s, "Q4": q4_s,
                           "h1_cum": False, "nm_cum": False, "ok": True})
            return result

    # ── Q1만 있는 경우 (H1/9M 파싱 실패) ───────────────────────────────
    # Q1만 업데이트하고 Q2~Q4는 건드리지 않음
    result["ok"] = False
    return result


# ── DB 조회/업데이트 ─────────────────────────────────────────────────────────

def get_annual_revenue(conn, sc: str, year: int) -> float | None:
    """DB에서 연간 revenue 우선 조회"""
    row = conn.execute("""
        SELECT revenue FROM financial_data
        WHERE stock_code=? AND year=? AND is_annual=1
          AND revenue IS NOT NULL AND revenue > 0
          AND data_source NOT IN ('data_quality_null')
        ORDER BY CASE data_source
          WHEN 'dart' THEN 1 WHEN 'dart_redownload' THEN 2
          WHEN 'fnguide' THEN 3 ELSE 9 END
        LIMIT 1
    """, (sc, year)).fetchone()
    return row[0] if row else None

def update_financial_quarter(conn, sc: str, year: int, quarter: int,
                              pl: dict, data_source: str = "dart_recollect"):
    """financial_data Q1~Q4 행 업데이트 (없으면 INSERT)"""
    rev = pl.get("revenue")
    op  = pl.get("operating_profit")
    net = pl.get("net_income")
    if rev is None and op is None and net is None:
        return False  # 아무 값도 없으면 skip

    existing = conn.execute("""
        SELECT id FROM financial_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
          AND COALESCE(report_type,'CFS')='CFS'
          -- 2026-10-03: 예전엔 연결/별도 구분 없이 비-legacy 행을 먼저 집어 덮어쓰고 report_type을 CFS로 바꿨다
          -- (별도 행이 연결로 둔갑, DART 재대조로 정정한 값 덮어쓰기 위험). legacy_collected 행만 갱신한다.
          AND data_source='legacy_collected'
        LIMIT 1
    """, (sc, year, quarter)).fetchone()

    if existing:
        conn.execute("""
            UPDATE financial_data
            SET revenue=COALESCE(?,revenue),
                operating_profit=COALESCE(?,operating_profit),
                net_income=COALESCE(?,net_income),
                data_source=?, report_type='CFS'
            WHERE id=?
        """, (rev, op, net, data_source, existing[0]))
    else:
        conn.execute("""
            INSERT INTO financial_data
              (stock_code, year, quarter, is_annual, revenue, operating_profit, net_income,
               data_source, report_type)
            VALUES (?,?,?,0,?,?,?,?,?)
        """, (sc, year, quarter, rev, op, net, data_source, 'CFS'))
    return True

def update_annual_pl(conn, sc: str, year: int, pl: dict,
                     data_source: str = "dart_recollect_annual") -> bool:
    """financial_data 연간 행 업데이트 (legacy_collected/data_quality_null만 덮어씀)."""
    rev = pl.get("revenue")
    op  = pl.get("operating_profit")
    net = pl.get("net_income")
    if rev is None:
        return False  # revenue 없으면 저장 불필요

    existing = conn.execute("""
        SELECT id, data_source FROM financial_data
        WHERE stock_code=? AND year=? AND is_annual=1
        ORDER BY CASE data_source
          WHEN 'dart_redownload' THEN 1 WHEN 'dart' THEN 2
          WHEN 'fnguide' THEN 3 WHEN 'legacy_collected' THEN 9
          WHEN 'data_quality_null' THEN 10 ELSE 5 END
        LIMIT 1
    """, (sc, year)).fetchone()

    if existing:
        # 더 좋은 소스가 있으면 건드리지 않음
        if existing[1] not in ('legacy_collected', 'data_quality_null', 'dart_recollect_annual'):
            return False
        conn.execute("""
            UPDATE financial_data
            SET revenue=COALESCE(?,revenue),
                operating_profit=COALESCE(?,operating_profit),
                net_income=COALESCE(?,net_income),
                data_source=?, report_type='CFS'
            WHERE id=?
        """, (rev, op, net, data_source, existing[0]))
    else:
        # 2026-08-28: quarter=NULL로 넣으면 collect_dart_financial_batch.py 등 다른 DART
        # 계열 수집기가 쓰는 "DART 연간 행 = quarter 4" 관례(존재확인 쿼리가 quarter=4로
        # 필터함)를 벗어나 같은 (stock_code, year, is_annual) 조합이 중복 저장되는 구멍이 됨
        # (실측: 연간 financial_data 12,113개 그룹 중복 발견, 그중 일부가 이 quarter=NULL 경로).
        # 이 함수는 이미 quarter 무관하게 기존 행을 먼저 찾으므로(위 existing 조회), 여기서
        # quarter=4로 저장해도 안전하게 기존 DART 연간 행 관례와 합류한다.
        conn.execute("""
            INSERT INTO financial_data
              (stock_code, year, quarter, is_annual, revenue, operating_profit, net_income,
               data_source, report_type)
            VALUES (?,?,4,1,?,?,?,?,'CFS')
        """, (sc, year, rev, op, net, data_source))
    return True


def update_cf_annual(conn, sc: str, year: int, cf: dict,
                     data_source: str = "dart_recollect_annual"):
    """cash_flow_data 연간 행 업데이트"""
    if not any(cf.get(k) is not None for k in ("ocf","icf","fcf")):
        return False
    existing = conn.execute("""
        SELECT id FROM cash_flow_data
        WHERE stock_code=? AND year=? AND is_annual=1
        ORDER BY CASE data_source
          WHEN 'dart' THEN 1 WHEN 'dart_redownload' THEN 2
          WHEN 'legacy_collected' THEN 99 ELSE 9 END
        LIMIT 1
    """, (sc, year)).fetchone()
    if existing:
        # legacy_collected만 덮어씀 (dart/fnguide는 보존)
        src_row = conn.execute("SELECT data_source FROM cash_flow_data WHERE id=?",
                               (existing[0],)).fetchone()
        if src_row and src_row[0] not in ('legacy_collected', 'data_quality_null'):
            return False
        conn.execute("""
            UPDATE cash_flow_data
            SET operating_cf=COALESCE(?,operating_cf),
                investing_cf=COALESCE(?,investing_cf),
                financing_cf=COALESCE(?,financing_cf),
                capex=COALESCE(?,capex),
                cash_end=COALESCE(?,cash_end),
                depreciation=COALESCE(?,depreciation),
                data_source=?
            WHERE id=?
        """, (cf["ocf"], cf["icf"], cf["fcf"], cf["capex"],
              cf["cash_end"], cf["depreciation"], data_source, existing[0]))
    else:
        # 2026-08-28: financial_data와 동일한 이유로 quarter=4(DART 연간 행 관례)를 명시
        # 저장 — quarter 미지정(NULL) 시 collect_dart_cashflow_batch.py 등 다른 DART 계열
        # 수집기의 quarter=4 존재확인과 어긋나 중복 저장 구멍이 됨(실측: cash_flow_data
        # 연간 20,392개 그룹 중복). 위 existing 조회는 quarter 무관하게 먼저 찾으므로 안전.
        conn.execute("""
            INSERT INTO cash_flow_data
              (stock_code, year, quarter, is_annual, operating_cf, investing_cf, financing_cf,
               capex, cash_end, depreciation, data_source)
            VALUES (?,?,4,1,?,?,?,?,?,?,?)
        """, (sc, year, cf["ocf"], cf["icf"], cf["fcf"],
              cf["capex"], cf["cash_end"], cf["depreciation"], data_source))
    return True


# ── 핵심 처리 함수 ────────────────────────────────────────────────────────────

REPRT = {1: "11013", 2: "11012", 3: "11014", 0: "11011"}  # quarter → reprt_code

def process_stock_year(conn, sc: str, year: int, corp_code: str, log_rows: list) -> str:
    # 2026-10-04: 비12월 결산은 한 DART 연도(bsns_year=기간 종료 달력 연도) 안의 보고서들이 서로 다른 회계연도라
    # 여기서 1~4분기를 계산하면 틀린다 → 건너뜀. 이 회사들은 회계 기준 키(fiscal_period, rekey_fiscal_nondec)로 관리.
    try:
        import fiscal_period as _fp
        if _fp.fiscal_month(sc) != 12:
            return "skip_non_december_fiscal"
    except Exception:
        pass
    """
    (sc, year)에 대해 DART 3개 분기보고서 호출 → Q1~Q4 재계산 → DB 업데이트
    반환: 결과 문자열
    """
    vals = {}   # reprt_code → {pl: {...}, cf: {...}}
    fs_div = "CFS"

    # ── 3개 분기 + 연간 보고서 다운로드 ──────────────────────────────────
    for q, code in [(1,"11013"), (2,"11012"), (3,"11014")]:
        rows = fetch_dart(corp_code, year, code, fs_div)
        if not rows and fs_div == "CFS":
            rows = fetch_dart(corp_code, year, code, "OFS")
            if rows:
                fs_div = "OFS"
        time.sleep(0.35)
        if rows:
            vals[q] = {"pl": extract_pl(rows), "cf": extract_cf(rows)}

    # 연간 CF (11011) — Q4 CF용 및 연간 CF 업데이트
    annual_rows = fetch_dart(corp_code, year, "11011", fs_div)
    time.sleep(0.35)
    annual_cf = extract_cf(annual_rows) if annual_rows else {}
    annual_pl = extract_pl(annual_rows) if annual_rows else {}

    # 연간 revenue (DB 우선)
    annual_rev = get_annual_revenue(conn, sc, year)
    if annual_rev is None and annual_pl.get("revenue"):
        annual_rev = annual_pl["revenue"]

    # ── 분기 계산 ─────────────────────────────────────────────────────────
    q1_pl = vals.get(1, {}).get("pl", {})
    q2_pl = vals.get(2, {}).get("pl", {})
    q3_pl = vals.get(3, {}).get("pl", {})

    q_result = compute_quarters(
        q1_val=q1_pl.get("revenue"),
        h1_val=q2_pl.get("revenue"),
        nm_val=q3_pl.get("revenue"),
        annual=annual_rev,
    )

    # ── 합산 검증: Q1+Q2+Q3 이 연간의 20%~120% 범위여야 정상 ─────────────
    if annual_rev and annual_rev > 0:
        q123 = sum(q_result.get(k) or 0 for k in ("Q1","Q2","Q3"))
        ratio = q123 / annual_rev
        if q123 > 0 and (ratio < 0.10 or ratio > 1.20):
            log_rows.append({
                "stock_code": sc, "year": year, "fs_div": fs_div,
                "q1_rev": q1_pl.get("revenue"), "h1_rev": q2_pl.get("revenue"),
                "nm_rev": q3_pl.get("revenue"), "annual_rev": annual_rev,
                "Q1": None, "Q2": None, "Q3": None, "Q4": None,
                "h1_cum": None, "nm_cum": None, "updated": 0, "cf_updated": False,
                "status": f"파싱오류(Q123합={ratio:.0%}of연간)",
            })
            return f"파싱오류 (Q1+Q2+Q3={ratio:.0%} of 연간)"

    # ── DB 업데이트 ───────────────────────────────────────────────────────
    updated = 0
    for q, qkey in [(1,"Q1"), (2,"Q2"), (3,"Q3"), (4,"Q4")]:
        rev_val = q_result.get(qkey)
        if rev_val is not None:
            # P&L 비매출 항목 (standalone에서 직접)
            if q in (1, 2, 3):
                base_pl = vals.get(q, {}).get("pl", {})
                # standalone 값이 있으면 직접 사용, 없으면 누적에서 차감값 사용
                op  = base_pl.get("operating_profit")
                net = base_pl.get("net_income")
            else:  # Q4
                op  = None  # Q4 op/net은 별도 계산 필요
                net = None

            pl_for_update = {"revenue": rev_val, "operating_profit": op, "net_income": net}
            if update_financial_quarter(conn, sc, year, q, pl_for_update):
                updated += 1

    # 연간 P&L 업데이트 (annual_pl에서 직접 저장)
    annual_pl_saved = False
    if annual_pl.get("revenue"):
        annual_pl_saved = update_annual_pl(conn, sc, year, annual_pl)

    # 연간 CF 업데이트 (legacy_collected인 경우만)
    cf_updated = False
    if annual_cf.get("ocf") is not None:
        cf_updated = update_cf_annual(conn, sc, year, annual_cf)

    status = f"Q수정={updated}"
    if annual_pl_saved:
        status += " ANN✅"
    if cf_updated:
        status += " CF✅"

    # 누락된 보고서 기록
    missing = [f"Q{q}" for q in (1,2,3) if q not in vals]
    if missing:
        status += f" 없음:{','.join(missing)}"

    log_rows.append({
        "stock_code": sc, "year": year, "fs_div": fs_div,
        "q1_rev": q1_pl.get("revenue"), "h1_rev": q2_pl.get("revenue"),
        "nm_rev": q3_pl.get("revenue"), "annual_rev": annual_rev,
        "Q1": q_result.get("Q1"), "Q2": q_result.get("Q2"),
        "Q3": q_result.get("Q3"), "Q4": q_result.get("Q4"),
        "h1_cum": q_result.get("h1_cum"), "nm_cum": q_result.get("nm_cum"),
        "updated": updated, "cf_updated": cf_updated, "status": status,
    })
    return status


# ── 체크포인트 ────────────────────────────────────────────────────────────────

def load_checkpoint() -> set[tuple]:
    """처리 완료된 (stock_code, year) set 로드"""
    if CKPT_PATH.exists():
        data = json.loads(CKPT_PATH.read_text())
        return {tuple(x) for x in data.get("done", [])}
    return set()

def save_checkpoint(done: set[tuple]):
    CKPT_PATH.write_text(json.dumps({"done": [list(x) for x in done],
                                     "saved_at": datetime.now().isoformat()}))


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year",   type=int, help="특정 연도만 (기본: 2016~2025)")
    ap.add_argument("--limit",  type=int, help="종목 수 제한")
    ap.add_argument("--resume", action="store_true", help="체크포인트에서 재개")
    ap.add_argument("--code",   type=str, help="특정 종목만")
    args = ap.parse_args()

    build_corp_map()

    conn = connect_primary_db(timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")  # 30초 잠금 대기
    conn.row_factory = sqlite3.Row

    # ── 처리 대상 목록 ────────────────────────────────────────────────────
    years = [args.year] if args.year else list(range(2016, 2026))

    # 활성 종목 (상폐·ETF·ETN·스팩 제외)
    active_q = """
        WITH latest AS (SELECT stock_code, MAX(base_date) md FROM stock_universe GROUP BY stock_code),
        recent_ph AS (
            SELECT DISTINCT stock_code
            FROM price_history
            WHERE date >= date('now', '-180 days')
        )
        SELECT su.stock_code, su.stock_name
        FROM stock_universe su
        JOIN latest l ON su.stock_code=l.stock_code AND su.base_date=l.md
        JOIN recent_ph rp ON su.stock_code=rp.stock_code
        WHERE su.market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
          AND su.stock_name NOT LIKE '%ETF%'
          AND su.stock_name NOT LIKE '%ETN%'
          AND su.stock_name NOT LIKE '%스팩%'
    """
    if args.code:
        active_q += f" AND su.stock_code='{args.code}'"
    active_stocks = {r["stock_code"]: r["stock_name"]
                     for r in conn.execute(active_q).fetchall()}

    # legacy_collected 분기 행이 있는 (stock_code, year) 쌍
    year_filter = f"AND year IN ({','.join(str(y) for y in years)})"
    targets_raw = conn.execute(f"""
        SELECT DISTINCT stock_code, year
        FROM financial_data
        WHERE data_source='legacy_collected' AND is_annual=0
          AND stock_code IN ({','.join('?' for _ in active_stocks)})
          {year_filter}
        ORDER BY year, stock_code
    """, list(active_stocks.keys())).fetchall()

    targets = [(r["stock_code"], r["year"]) for r in targets_raw
               if r["stock_code"] in _CORP_MAP]

    if args.limit:
        targets = targets[:args.limit]

    # 체크포인트 적용
    done = load_checkpoint() if args.resume else set()
    targets = [(sc, yr) for sc, yr in targets if (sc, yr) not in done]

    total = len(targets)
    print(f"\n처리 대상: {total}건 (활성종목 {len(active_stocks)}개)")
    print(f"DART 미등록 종목 제외됨 (corp_code 없음)\n")

    # ── 처리 루프 ─────────────────────────────────────────────────────────
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_rows: list[dict] = []
    stats    = {"updated": 0, "no_dart": 0, "error": 0}
    last_commit = 0

    for i, (sc, yr) in enumerate(targets, 1):
        name = active_stocks.get(sc, sc)
        corp_code = _CORP_MAP.get(sc, "")
        print(f"[{i}/{total}] {sc} {name} {yr} ... ", end="", flush=True)

        if not corp_code:
            print("DART미등록")
            stats["no_dart"] += 1
            continue

        try:
            status = process_stock_year(conn, sc, yr, corp_code, log_rows)
            print(status)
            done.add((sc, yr))
            if "Q수정=0" not in status:
                stats["updated"] += 1

        except RuntimeError as e:
            if "DART_LIMIT" in str(e):
                calls_info = ", ".join(f"KEY{i+1}:{c}회" for i,c in enumerate(_key_calls))
                print(f"\n❌ DART 모든 키 한도 초과! {i-1}건 처리 후 중단 ({calls_info})")
                conn.commit()
                save_checkpoint(done)
                print(f"체크포인트 저장 → {CKPT_PATH}")
                print(f"내일 재실행: python3 scripts/ops/legacy_dart_recollect.py --resume")
                break
            print(f"오류: {e}")
            stats["error"] += 1
        except Exception as e:
            print(f"오류: {e}")
            stats["error"] += 1

        # 50건마다 커밋 + 체크포인트
        if i - last_commit >= 50:
            conn.commit()
            save_checkpoint(done)
            last_commit = i
            print(f"  → 중간저장 ({i}/{total})", flush=True)

    conn.commit()
    save_checkpoint(done)
    conn.close()

    # ── 결과 로그 저장 ────────────────────────────────────────────────────
    if log_rows:
        out_path = OUT_DIR / f"recollect_result_{ts}.csv"
        with out_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(log_rows[0].keys()))
            w.writeheader()
            w.writerows(log_rows)
        print(f"\n로그 → {out_path}")

    print(f"\n=== 완료 ===")
    print(f"  수정: {stats['updated']}건")
    print(f"  DART미등록: {stats['no_dart']}건")
    print(f"  오류: {stats['error']}건")
    print(f"  처리완료 누적: {len(done)}건")
    print(f"  키별 API 호출: " + ", ".join(f"KEY{i+1}={c}회" for i,c in enumerate(_key_calls)))

    # 최종 현황
    conn2 = connect_primary_db()
    remaining = conn2.execute(
        "SELECT COUNT(*) FROM financial_data WHERE data_source='legacy_collected' AND is_annual=0"
    ).fetchone()[0]
    print(f"\n잔존 legacy_collected 분기: {remaining}건")
    conn2.close()

if __name__ == "__main__":
    main()
