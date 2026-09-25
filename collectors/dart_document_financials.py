"""Read core financial-statement facts from DART periodic-report XML documents.

This is a fallback for issuers where OpenDART's finstate endpoint returns no
CFS rows even though the periodic report document is available.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Mapping

import requests


DART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
_FACT_CODES: Mapping[str, tuple[str, ...]] = {
    "total_assets": ("ifrs-full_Assets",),
    "total_equity": ("ifrs-full_Equity",),
    "revenue": ("ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"),
    "operating_profit": ("ifrs-full_OperatingIncomeLoss",),
    "net_income": ("ifrs-full_ProfitLoss",),
}


def _number(value: str) -> float | None:
    try:
        return float(value.replace(",", "").strip().strip("()")) * (-1 if value.strip().startswith("(") else 1)
    except ValueError:
        return None


def extract_core_fact_details(xml_text: str) -> dict[str, tuple[float, bool]]:
    """Return current-period IFRS facts and whether the selected fact is consolidated."""
    facts: dict[str, tuple[float, bool]] = {}
    for field, codes in _FACT_CODES.items():
        candidates: list[tuple[int, float, bool]] = []
        for code in codes:
            # DART serialises some report values inside a ``<P>`` element,
            # while others are direct text.  Attribute order is not stable.
            pattern = r"<TE(?P<attrs>[^>]*)>(?P<value>.*?)</TE>"
            code_pattern = rf'\bACODE="{re.escape(code)}"'
            for match in re.finditer(pattern, xml_text, flags=re.DOTALL):
                attrs = match.group("attrs")
                if not re.search(code_pattern, attrs):
                    continue
                value = _number(re.sub(r"<[^>]+>", "", match.group("value")))
                if value is None:
                    continue
                # ADECIMAL=-6 / -3 means the table is displayed in millions / thousands of won.
                decimal = re.search(r'\bADECIMAL="(-?\d+)"', attrs)
                if decimal and int(decimal.group(1)) < 0:
                    value *= 10 ** (-int(decimal.group(1)))
                # Current-period end facts use eFY; consolidated facts take precedence.
                score = 0
                if "eFY" in attrs:
                    score += 2
                consolidated = "ConsolidatedMember" in attrs
                if consolidated:
                    score += 1
                candidates.append((score, value, consolidated))
        if candidates:
            _, value, consolidated = max(candidates, key=lambda item: item[0])
            facts[field] = (value, consolidated)
    return facts


def extract_core_facts(xml_text: str) -> dict[str, float]:
    """Extract current-period IFRS facts, preferring consolidated over separate."""
    return {field: value for field, (value, _) in extract_core_fact_details(xml_text).items()}


def _decode(raw: bytes) -> str:
    # DART document.xml declares utf-8 but many reports (incl. older/smaller filers) are cp949;
    # decoding those as utf-8 silently drops all Korean text and tables.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="ignore")


def _download_document_text(rcept_no: str, api_key: str, session=requests) -> str:
    response = session.get(DART_DOCUMENT_URL, params={"crtfc_key": api_key, "rcept_no": rcept_no}, timeout=30)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        texts = [_decode(archive.read(name)) for name in archive.namelist() if name.endswith(".xml")]
    return "\n".join(texts)


def download_core_fact_details(rcept_no: str, api_key: str, session=requests) -> dict[str, tuple[float, bool]]:
    """Download a DART periodic-report ZIP and include selected-fact basis metadata."""
    return extract_core_fact_details(_download_document_text(rcept_no, api_key, session))


def download_core_facts(rcept_no: str, api_key: str, session=requests) -> dict[str, float]:
    """Download a DART periodic-report ZIP and parse its XML facts without DB writes."""
    return extract_core_facts(_download_document_text(rcept_no, api_key, session))
