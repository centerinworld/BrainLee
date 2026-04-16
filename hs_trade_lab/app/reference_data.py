from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import DATA_DIR


REFERENCE_DIR = DATA_DIR / "reference"


@lru_cache(maxsize=1)
def hs_lookup() -> dict[str, dict]:
    path = REFERENCE_DIR / "hs_codes.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["HS부호"]).zfill(10): row for row in rows}


@lru_cache(maxsize=1)
def country_lookup() -> dict[str, str]:
    path = REFERENCE_DIR / "countries.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["code"]: row["name"] for row in rows}


@lru_cache(maxsize=1)
def sido_lookup() -> dict[str, str]:
    path = REFERENCE_DIR / "sidos.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["code"]: row["name"] for row in rows}


@lru_cache(maxsize=1)
def unified_lookup() -> dict[str, str]:
    path = REFERENCE_DIR / "unified_codes.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["imex_tmpr_unfc_clsf_cd"]): row["imex_tmpr_unfc_clsf_nm"] for row in rows}


@lru_cache(maxsize=1)
def clear_reference_cache() -> None:
    hs_lookup.cache_clear()
    country_lookup.cache_clear()
    sido_lookup.cache_clear()
    unified_lookup.cache_clear()
