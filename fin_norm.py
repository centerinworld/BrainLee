"""재무 숫자 정규화 공통 함수 — 정의는 이 파일 한 곳 (2026-10-10, 사용자·외부 검토 '중앙 정규화 없이는 더 진행하지 않는다').

정본: docs/FINANCIAL_STATEMENTS.md. 여기 정의를 바꾸려면 사용자 승인 + 그 문서 갱신 + tests/test_fin_norm_20261010.py 갱신.
수집·적용·검증 스크립트는 아래 함수만 쓴다(스크립트 안에 같은 계산을 다시 만들지 말 것 — tests/test_fin_norm_20261010.py가 검사).

1) 회계 키(fiscal year/quarter)
   - DART 표기 → 회계 키: fiscal_period.to_fiscal  (to_fiscal)
   - 기간 종료 'YYYYMM'(FnGuide·네이버 열) → 회계 키: fiscal_key_from_period_end
2) 4분기 산식(D-6, 2026-10-09 승인)
   - 손익·현금흐름 흐름 항목 4분기 3개월 = 연간 − 3분기 보고서 누적(YTD)  (q4_flow)
   - 재무상태 4분기 = 연간  (q4_bs)
   - 현금흐름 4분기 누적 칸 = 연간
   - 현금흐름 1~3분기 3개월 = 이번 누적 − 직전 분기 누적(1분기 = 누적)  (cf_three_month)
3) CFS/OFS 선택 — 섞지 않는다: 연결 칸은 연결 원문만, 별도 칸은 별도 원문만(대체 금지)  (pick_fs)
   순이익·자본: 연결 = 지배주주, 별도 = 전체  (dart_key)
4) 재작성값 우선순위(§2-5, 표시값 = 최신 재작성값, 최초값은 PIT·백업 보존)
   - 연간 Y: Y+1 사업보고서 전기 칸(frmtrm_amount) > Y 사업보고서 당기(thstrm_amount)
   - 분기 손익 Y q: Y+1 같은 분기 보고서 전년 3개월(frmtrm_q_amount) > Y q 보고서 당기 3개월
   - 분기 현금흐름 누적 Y q: Y+1 같은 분기 보고서 전년 칸(frmtrm_q_amount) > Y q 보고서 누적(thstrm_amount)
   - 반영은 외부(FnGuide) 일치가 확인된 칸만(원칙 0) — 이 파일은 후보 값만 정한다  (latest_annual, latest_quarter_3m, latest_cf_cum)
5) 원문 읽기: data_raw/dart_fnltt/<code>/<Y>_<q>_<fs>.json.gz + financial_rereview_20261002.extract  (raw)
"""
from __future__ import annotations

import gzip
import json
import sys
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "scripts" / "review"))
import fiscal_period  # noqa: E402

RAW_DART = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
FLOW_IS = ("revenue", "operating_profit", "net_income")
FLOW_CF = ("operating_cf", "investing_cf", "financing_cf")
BS = ("total_assets", "total_liabilities", "total_equity")


# ── 1) 회계 키 ─────────────────────────────────────────────────────────────
def to_fiscal(code: str, bsns_year: int, quarter: int, is_annual: bool = False) -> tuple:
    return fiscal_period.to_fiscal(code, bsns_year, quarter, is_annual=is_annual)


def fiscal_key_from_period_end(code: str, ym: str, annual: bool) -> tuple | None:
    """'YYYYMM'(기간 종료 월) → (회계연도, 분기; 연간 0). 비12월 결산 포함. 대응 없으면 None."""
    y, m = int(ym[:4]), int(ym[4:6])
    target = f"{y:04d}-{m:02d}"
    if annual:
        for fy in (y, y + 1):
            if fiscal_period.period_end(code, fy, 0) == target:
                return fy, 0
        return None
    for fy in (y, y + 1):
        for q in (1, 2, 3, 4):
            if fiscal_period.period_end(code, fy, q) == target:
                return fy, q
    return None


def dart_key_for_fiscal(code: str, fy: int, q: int) -> tuple:
    """회계 키 → DART 원문 키(bsns_year, 보고서 위치). to_fiscal의 역. 12월 결산·연간·4분기·리츠는 그대로.
    예: 9월 결산 084440 회계 2026 1분기(2025-12 종료) → DART (2025, 1)."""
    f = fiscal_period.fiscal_month(code)
    if f == 12 or q not in (1, 2, 3) or fiscal_period.is_reit(code):
        return int(fy), int(q)
    end_month = (f + 3 * q - 1) % 12 + 1
    return (int(fy) - 1 if end_month > f else int(fy)), int(q)


# ── 2) 4분기·3개월 산식 ────────────────────────────────────────────────────
def q4_flow(annual, ytd3):
    """흐름 항목 4분기 3개월 = 연간 − 3분기 누적(D-6). 하나라도 없으면 None(파생 안 함)."""
    if annual is None or ytd3 is None:
        return None
    return annual - ytd3


def q4_bs(annual):
    return annual


def cf_three_month(cum_q, cum_prev, quarter: int):
    """현금흐름 3개월 = 이번 누적 − 직전 분기 누적. 1분기는 누적 그대로."""
    if quarter == 1:
        return cum_q
    if cum_q is None or cum_prev is None:
        return None
    return cum_q - cum_prev


# ── 3) 연결/별도·계정 키 ────────────────────────────────────────────────────
def dart_key(field: str, fs: str) -> str:
    """정본 필드 → extract() 키. 순이익·자본: 연결 = 지배, 별도 = 전체."""
    m = {"revenue": "revenue", "operating_profit": "operating_profit", "total_assets": "total_assets",
         "total_liabilities": "total_liabilities", "operating_cf": "ocf", "investing_cf": "icf", "financing_cf": "fcf",
         "capex": "capex"}
    if field in m:
        return m[field]
    if field == "net_income":
        return "ni_parent" if fs == "CFS" else "ni_total"
    if field == "total_equity":
        return "equity_parent" if fs == "CFS" else "equity_total"
    raise KeyError(field)


def pick_fs(values_by_fs: dict, wanted: str):
    """연결/별도는 섞지 않는다: 요청한 구분의 값만 돌려준다(없으면 None — 다른 구분으로 대체 금지)."""
    return values_by_fs.get(wanted)


# ── 5) 원문 읽기 ────────────────────────────────────────────────────────────
@lru_cache(maxsize=200000)
def _extract(code: str, y: int, q: int, fs: str, col: str) -> dict:
    from financial_rereview_20261002 import extract
    p = RAW_DART / code / f"{y}_{q}_{fs}.json.gz"
    if not p.exists():
        return {}
    return extract(json.loads(gzip.decompress(p.read_bytes())), col)


@lru_cache(maxsize=50000)
def _loss_amounts(code: str, y: int, q: int, fs: str, col: str) -> frozenset:
    """손익계산서에서 라벨이 '…손실'(이익·손익 없음)인데 금액이 양수로 적힌 행의 금액들(2026-10-11, 052770 '영업손실' 13.24억 → −13.24억 = FnGuide)."""
    import re as _re
    p = RAW_DART / code / f"{y}_{q}_{fs}.json.gz"
    if not p.exists():
        return frozenset()
    out = set()
    for r in json.loads(gzip.decompress(p.read_bytes())):
        lab = _re.sub(r"\s", "", r.get("account_nm") or "")
        if r.get("sj_div") in ("IS", "CIS") and _re.search(r"(영업|당기|분기|반기)순?손실$", lab) and "이익" not in lab and "손익" not in lab:
            try:
                v = float(str(r.get(col) or "").replace(",", ""))
            except ValueError:
                continue
            if v > 0:
                out.add(v)
    return frozenset(out)


def raw(code: str, y: int, q: int, fs: str, field: str, col: str = "thstrm_amount"):
    """DART 원문 값(q 0 = 사업보고서). (y, q)는 **회계 키** — 비12월 결산은 DART 키로 바꿔 읽는다(12월 결산은 동일). field는 정본 필드명.
    영업이익·순이익: 원문 라벨이 '…손실'이고 금액이 양수면 손실이므로 음수로(2026-10-11)."""
    by, bq = dart_key_for_fiscal(code, y, q)
    v = _extract(code, by, bq, fs, col).get(dart_key(field, fs))
    if v is not None and v > 0 and field in ("operating_profit", "net_income") and v in _loss_amounts(code, by, bq, fs, col):
        return -v
    return v


def ytd3(code: str, y: int, fs: str, field: str):
    """3분기 보고서 누적: 손익 = thstrm_add_amount, 현금흐름 = thstrm_amount(현금흐름 원문은 누적)."""
    return raw(code, y, 3, fs, field, "thstrm_amount" if field in FLOW_CF else "thstrm_add_amount")


@lru_cache(maxsize=50000)
def raw_currency(code: str, y: int, q: int, fs: str):
    """원문 보고통화(DART 행의 currency). (y, q)는 회계 키. 외화 종목도 연도마다 통화가 바뀔 수 있다(10-11, 900070 2023·24 = CNY)."""
    by, bq = dart_key_for_fiscal(code, y, q)
    p = RAW_DART / code / f"{by}_{bq}_{fs}.json.gz"
    if not p.exists():
        return None
    rows = json.loads(gzip.decompress(p.read_bytes()))
    return (rows[0].get("currency") or None) if rows else None


@lru_cache(maxsize=50000)
def effective_fs(code: str, y: int, fs: str) -> str:
    """연결 미작성 회사(그 회계연도 1~4개 보고서 모두 연결 원문 파일 없음, 별도 원문은 있음)의 DB 'CFS' 행은 별도 재무제표 값이다(2026-10-12,
    001080·001420 등 — DB 'CFS' 연간 = 별도 원문 연간). 이때만 'OFS' 원문을 읽는다. 연결 원문이 한 보고서라도 있으면 그대로 'CFS'(대체 금지 원칙 유지).
    FnGuide 표시 기간 이전(OpenDART 단일 경로) 정렬·내부 검증 전용 — 호출자가 명시적으로 쓸 때만 적용."""
    if fs != "CFS":
        return fs
    keys = [dart_key_for_fiscal(code, y, q) for q in (0, 1, 2, 3)]
    if any((RAW_DART / code / f"{by}_{bq}_CFS.json.gz").exists() for by, bq in keys):
        return fs
    return "OFS" if any((RAW_DART / code / f"{by}_{bq}_OFS.json.gz").exists() for by, bq in keys) else fs


# ── 4) 재작성값 우선순위 ────────────────────────────────────────────────────
def latest_annual(code: str, y: int, fs: str, field: str) -> tuple:
    """(값, 'restated'|'original'|None). Y+1 사업보고서 전기 칸 > Y 사업보고서 당기."""
    v = raw(code, y + 1, 0, fs, field, "frmtrm_amount")
    if v is not None:
        return v, "restated"
    v = raw(code, y, 0, fs, field)
    return (v, "original") if v is not None else (None, None)


def latest_quarter_3m(code: str, y: int, q: int, fs: str, field: str) -> tuple:
    """손익 분기 3개월: Y+1 같은 분기 보고서 전년 3개월 > Y q 보고서 당기 3개월."""
    v = raw(code, y + 1, q, fs, field, "frmtrm_q_amount")
    if v is not None:
        return v, "restated"
    v = raw(code, y, q, fs, field)
    return (v, "original") if v is not None else (None, None)


def latest_cf_cum(code: str, y: int, q: int, fs: str, field: str) -> tuple:
    """현금흐름 누적: Y+1 같은 분기 보고서 전년 칸 > Y q 보고서 누적."""
    v = raw(code, y + 1, q, fs, field, "frmtrm_q_amount")
    if v is not None:
        return v, "restated"
    v = raw(code, y, q, fs, field)
    return (v, "original") if v is not None else (None, None)
