"""
employment_monitor/collect.py — data.go.kr 고용/산재보험 업종별 현황 수집

API: http://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomJyPstateItem
- sjGyFg: 1=산재보험, 3=고용보험
- 응답 형식: XML (JSON 미지원)
"""
from __future__ import annotations

import logging
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests

# 프로젝트 루트 config 읽기
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import config as _cfg
    API_KEY = getattr(_cfg, "EMPLOYMENT_API_KEY", "")
except Exception:
    API_KEY = ""

from .db import connect

logger = logging.getLogger(__name__)

BASE_URL = "http://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomJyPstateItem"

# 응답 XML 필드 → DB 컬럼 매핑
_FIELD_MAP = {
    "gyAgriForeFis":            "agri_fore_fis",
    "gyAgriHuntFore":           "agri_hunt_fore",
    "gyAhouGyhwaldong":         "ahou_gyhwaldong",
    "gyArtSportYeoga":          "art_sport_yeoga",
    "gyBogunShoeBokji":         "bogun_shoe_bokji",
    "gyBudongsanImdae":         "budongsanImdae",
    "gyBudongsanImdaeSaeop":    "budongsanImdaeSaeop",
    "gyEduService":             "edu_service",
    "gyFis":                    "fis",
    "gyFnnBoheom":              "fnn_boheom",
    "gyGeonseoleop":            "geonseoleop",
    "gyInteForeignGigwan":      "inte_foreign_gigwan",
    "gyJejoeop":                "jejoeop",
    "gyJeongiGasStePip":        "jeongi_gas_step",
    "gyJeongiGasTw":            "jeongi_gas_tw",
    "gyJeonmunSciGisulService": "jeonmun_sci",
    "gyMinind":                 "minind",
    "gyPublAimBrdInfoService":  "publ_aim_brd",
    "gyPubHjngShoeBjngHjng":   "pub_hjng_shoe",
    "gySaeopSiseolJiwon":       "saeop_siseol_jiwon",
    "gySewSmatRmatrevHgyngrst": "sew_smat_rmatrev",
    "gySukbakAeh":              "sukbak_aeh",
    "gyUnsu":                   "unsu",
    "gyUnsuChanggoTongsin":     "unsu_changgo_tongsin",
    "gyWholRet":                "whol_ret",
    "gyWholRetConsArtiSuri":    "whol_ret_cons",
    "saeopjangWkFg":            "saeopjang_wk_fg",
}


def _fetch_page(sj_gy_fg: str, page: int = 1, num: int = 100) -> dict:
    """data.go.kr API 호출 → XML 파싱 → dict 반환."""
    if not API_KEY:
        raise RuntimeError(
            "EMPLOYMENT_API_KEY가 설정되지 않았습니다. "
            ".env 파일에 EMPLOYMENT_API_KEY=값 을 추가하세요."
        )
    params = {
        "serviceKey": API_KEY,
        "sjGyFg":     sj_gy_fg,
        "pageNo":     page,
        "numOfRows":  num,
    }
    r = requests.get(BASE_URL, params=params, timeout=15)
    r.raise_for_status()

    # XML 파싱 (API 스펙: XML 전용, JSON 미지원)
    root = ET.fromstring(r.content)

    result_code = root.findtext(".//resultCode", "").strip()
    if result_code and result_code != "00":
        msg = root.findtext(".//resultMsg", "").strip()
        raise RuntimeError(f"API 오류 [{result_code}]: {msg}")

    items = []
    for item_el in root.findall(".//item"):
        d = {child.tag: (child.text or "").strip() for child in item_el}
        items.append(d)

    total_count = root.findtext(".//totalCount", "0").strip()
    return {"items": items, "totalCount": int(total_count or 0)}


def _parse_ym(item: dict) -> str:
    """응답 item에서 연월 추출."""
    for k in ("stdrYm", "baseYm", "ym", "yearMonth"):
        if k in item and item[k]:
            v = str(item[k]).replace("-", "")
            if len(v) >= 6:
                return f"{v[:4]}-{v[4:6]}"
    return datetime.now().strftime("%Y-%m")


def _safe_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def collect_month(sj_gy_fg: str = "3", ym: str | None = None) -> int:
    """
    한 달치 고용/산재보험 업종별 현황 수집 후 DB 저장.
    Returns: 저장된 행 수
    """
    saved = 0
    page  = 1
    conn  = connect()

    while True:
        try:
            data = _fetch_page(sj_gy_fg, page=page, num=100)
        except Exception as e:
            logger.warning(f"[고용보험] fetch 실패 p{page}: {e}")
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            period = ym or _parse_ym(item)
            row_data = {
                col: _safe_float(item.get(api_key))
                for api_key, col in _FIELD_MAP.items()
                if col != "saeopjang_wk_fg"
            }
            row_data["saeopjang_wk_fg"] = item.get("saeopjangWkFg", "")

            total_val = sum(v for v in row_data.values() if isinstance(v, float))

            cols = list(row_data.keys()) + ["ym", "sj_gy_fg", "total"]
            vals = list(row_data.values()) + [period, sj_gy_fg, total_val]
            ph   = ",".join("?" * len(cols))
            sets = ",".join(
                f"{c}=excluded.{c}"
                for c in cols
                if c not in ("ym", "sj_gy_fg", "saeopjang_wk_fg")
            )

            try:
                conn.execute(
                    f"INSERT INTO employment_monthly ({','.join(cols)}) VALUES ({ph}) "
                    f"ON CONFLICT(ym,sj_gy_fg,saeopjang_wk_fg) DO UPDATE SET {sets}",
                    vals,
                )
                saved += 1
            except Exception as e:
                logger.debug(f"[고용보험] insert 오류: {e}")

        conn.commit()

        total_count = data.get("totalCount", 0)
        if page * 100 >= total_count:
            break
        page += 1
        time.sleep(0.2)

    conn.close()
    logger.info(f"[고용보험] sj_gy_fg={sj_gy_fg} 수집 완료: {saved}건")
    return saved


def collect_all(months_back: int = 24) -> None:
    """최근 N개월치 고용/산재보험 데이터 배치 수집."""
    now = datetime.now()
    for m in range(months_back):
        dt = now - timedelta(days=30 * m)
        ym = dt.strftime("%Y-%m")
        for fg in ["3", "1"]:
            try:
                n = collect_month(sj_gy_fg=fg, ym=ym)
                logger.info(f"[고용보험] {ym} fg={fg}: {n}건")
            except Exception as e:
                logger.warning(f"[고용보험] {ym} fg={fg} 실패: {e}")
            time.sleep(0.3)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collect_all(months_back=36)
