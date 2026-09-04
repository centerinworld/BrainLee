"""SEC 10-K/10-Q에서 미국 바이오 기업의 파이프라인 근거를 보관한다.

자동 추출은 원문에 명시된 후보만 반환한다. 파서가 확신하지 못하면 빈
파이프라인과 상태값을 남겨, 존재하지 않는 후보물질을 만들어 내지 않는다.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any

import requests


US_DB_PATH = os.getenv("US_STOCK_DB_PATH", "stock.db")
SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT", "StockDashboard research contact@stock-dashboard.local"
)
PIPELINE_PARSER_VERSION = "multi_source_pipeline_v6"
_BIO_KEYWORDS = ("biotech", "biopharma", "biopharmaceutical", "pharmaceutical", "drug manufacturers")
_PHASE_RE = re.compile(
    r"\b(Phase\s*(?:1|2|3|I|II|III)(?:[abc])?(?:\s*/\s*(?:1|2|3|I|II|III)(?:[abc])?)?|"
    r"Preclinical|IND(?:-enabling)?|NDA|BLA|Approved|Commercialized)\b",
    re.IGNORECASE,
)
_ASSET_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{1,30}(?:-[A-Za-z0-9]{1,12})?)\b")
_STOP_ASSETS = {
    "FDA", "IND", "NDA", "BLA", "SEC", "USA", "US", "DNA", "RNA", "MRNA",
    "COVID", "PHASE", "TRIAL", "CLINICAL", "THE", "OUR", "AND", "FOR", "WITH",
    "NILEX", "OSA", "MCI", "NSCLC", "ADC", "EU", "US", "FDA", "IND",
    "CD19", "CD38", "CD40", "CD40L", "CART19", "BCMA", "HER2", "EGFR", "KRAS", "BRAF", "ATTR",
}
_TABLE_HEADER_ASSETS = {
    "asset", "candidate", "compound", "program", "product", "indication", "indications",
    "phase", "status", "development", "commercial", "approved", "pediatric", "adult",
    "assets", "candidates", "compounds", "programs", "products", "treatment", "treatments",
    "therapy", "therapies", "patient", "patients",
    "discovery", "research", "clinical", "study", "trial", "placebo", "safety", "efficacy",
    "suspension", "both", "preliminary", "analysis", "endpoint", "cohort", "dose", "dosing",
    "preclinical", "pharmacokinetic", "pharmacodynamics", "formulation", "combination",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}
_DRUG_NAME_SUFFIXES = (
    "mab", "nib", "vir", "cept", "gliptin", "gliflozin", "glutide", "stat", "parib",
    "ciclib", "lisib", "navir", "xaban", "oxetine", "pril", "sartan", "mycin", "leucel",
)
_DRUG_CODE_RE = re.compile(r"^[A-Z]{2,10}-?\d{2,}(?:[A-Z0-9-]*)$")
_GENERIC_DRUG_RE = re.compile(
    r"\b([A-Za-z][a-z]{3,}(?:mab|nib|vir|cept|gliptin|gliflozin|glutide|stat|parib|ciclib|lisib|navir|xaban|oxetine|pril|sartan|mycin|leucel))\b",
    re.IGNORECASE,
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(US_DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def ensure_us_biotech_tables() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS us_biotech_pipeline_snapshot (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                filing_date TEXT,
                form TEXT,
                accession_no TEXT,
                source_url TEXT,
                pipeline_json TEXT NOT NULL DEFAULT '[]',
                source_excerpt TEXT,
                extraction_status TEXT NOT NULL DEFAULT 'pending',
                source_text_hash TEXT,
                parser_version TEXT NOT NULL DEFAULT 'sec_pipeline_v1',
                last_error TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_us_biotech_pipeline_status "
            "ON us_biotech_pipeline_snapshot(extraction_status, updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS us_biotech_clinical_trials (
                ticker TEXT NOT NULL,
                nct_id TEXT NOT NULL,
                title TEXT,
                status TEXT,
                phase TEXT,
                conditions_json TEXT NOT NULL DEFAULT '[]',
                interventions_json TEXT NOT NULL DEFAULT '[]',
                start_date TEXT,
                primary_completion_date TEXT,
                completion_date TEXT,
                last_update_date TEXT,
                sponsor_name TEXT,
                source_url TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, nct_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_us_biotech_trials_ticker_date "
            "ON us_biotech_clinical_trials(ticker, primary_completion_date)"
        )
        existing_trials = {row[1] for row in conn.execute("PRAGMA table_info(us_biotech_clinical_trials)").fetchall()}
        for column, definition in (("has_results", "INTEGER DEFAULT 0"), ("primary_outcomes_json", "TEXT DEFAULT '[]'")):
            if column not in existing_trials:
                try:
                    conn.execute(f"ALTER TABLE us_biotech_clinical_trials ADD COLUMN {column} {definition}")
                except Exception:
                    pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS us_biotech_consensus_snapshot (
                ticker TEXT PRIMARY KEY,
                target_mean_price REAL,
                target_high_price REAL,
                target_low_price REAL,
                recommendation_key TEXT,
                recommendation_mean REAL,
                analyst_count INTEGER,
                source TEXT DEFAULT 'yahoo_finance',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS us_biotech_news (
                ticker TEXT NOT NULL,
                news_id TEXT NOT NULL,
                published_at TEXT,
                title TEXT,
                publisher TEXT,
                url TEXT,
                summary TEXT,
                source TEXT DEFAULT 'yahoo_finance',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, news_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_us_biotech_news_ticker_date ON us_biotech_news(ticker, published_at DESC)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS us_biotech_fda_labels (
                ticker TEXT NOT NULL,
                product_key TEXT NOT NULL,
                brand_name TEXT,
                generic_name TEXT,
                manufacturer_name TEXT,
                indications TEXT,
                boxed_warning INTEGER DEFAULT 0,
                effective_time TEXT,
                source_url TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, product_key)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def is_biotech_company(sector: str | None, industry: str | None) -> bool:
    text = f"{sector or ''} {industry or ''}".lower()
    return any(word in text for word in _BIO_KEYWORDS)


def _headers() -> dict[str, str]:
    return {
        "User-Agent": SEC_USER_AGENT,
        "Accept": "application/json,text/html,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _load_sec_map() -> dict[str, str]:
    response = requests.get("https://www.sec.gov/files/company_tickers.json", headers=_headers(), timeout=15)
    response.raise_for_status()
    payload = response.json()
    return {
        str(row.get("ticker") or "").upper(): str(row.get("cik_str") or "").zfill(10)
        for row in payload.values()
        if row.get("ticker") and str(row.get("cik_str") or "").isdigit()
    }


def _lookup_sec_cik_by_company(company_name: str, ticker: str) -> str | None:
    """활성 티커 목록에서 빠진 종목은 SEC 전문검색의 회사명·티커로 CIK를 복구한다."""
    response = requests.get(
        "https://efts.sec.gov/LATEST/search-index",
        headers=_headers(),
        params={"q": f'"{company_name}"', "forms": "10-K", "from": 0, "size": 20},
        timeout=20,
    )
    response.raise_for_status()
    ticker_pattern = re.compile(rf"\(\s*{re.escape(ticker.upper())}(?:\s*[,)]|\s*$)")
    for hit in ((response.json().get("hits") or {}).get("hits") or []):
        source = hit.get("_source") or {}
        display_names = " ".join(source.get("display_names") or []).upper()
        ciks = source.get("ciks") or []
        if ciks and ticker_pattern.search(display_names):
            cik = re.sub(r"\D", "", str(ciks[0] or ""))
            if cik:
                return cik.zfill(10)
    return None


def _upsert_snapshot(conn: sqlite3.Connection, ticker: str, **values: Any) -> None:
    columns = ["ticker", *values.keys(), "updated_at"]
    placeholders = ", ".join("?" for _ in columns[:-1]) + ", CURRENT_TIMESTAMP"
    assignments = ", ".join(f"{key}=excluded.{key}" for key in values) + ", updated_at=CURRENT_TIMESTAMP"
    conn.execute(
        f"INSERT INTO us_biotech_pipeline_snapshot ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(ticker) DO UPDATE SET {assignments}",
        [ticker, *values.values()],
    )


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw or "")
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _pipeline_windows(text: str) -> list[str]:
    lowered = text.lower()
    windows: list[str] = []
    for keyword in (
        "our pipeline and product candidates", "current product candidate pipeline",
        "development portfolio", "late-stage development pipeline", "our pipeline",
        "clinical pipeline", "development pipeline", "pipeline",
    ):
        start = 0
        found_for_keyword = 0
        while len(windows) < 20 and found_for_keyword < 4:
            pos = lowered.find(keyword, start)
            if pos < 0:
                break
            windows.append(text[max(0, pos - 800): min(len(text), pos + 15000)])
            start = pos + len(keyword)
            found_for_keyword += 1
    return windows


def _clean_phase(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _extract_indication(sentence: str) -> str | None:
    patterns = (
        r"(?:for|in|against|targeting)\s+([A-Za-z][A-Za-z0-9 ,()'/-]{4,100}?)(?:[.;:]|\s+(?:and|with|in)\s+(?:Phase|a clinical|our)|$)",
        r"(?:treatment of)\s+([A-Za-z][A-Za-z0-9 ,()'/-]{4,100}?)(?:[.;:]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, sentence, re.IGNORECASE)
        if match:
            indication = re.sub(r"\s+", " ", match.group(1)).strip(" ,.-")[:120]
            lowered = indication.lower()
            if len(indication) < 5 or re.fullmatch(r"the\s+(?:u\.?s?\.?|eu|united states)", lowered):
                return None
            return indication
    return None


def _extract_modality(sentence: str) -> str | None:
    lowered = sentence.lower()
    for term, label in (
        ("monoclonal antibody", "monoclonal antibody"), ("antibody-drug conjugate", "ADC"),
        ("cell therapy", "cell therapy"), ("gene therapy", "gene therapy"),
        ("small molecule", "small molecule"), ("mrna", "mRNA"), ("rna", "RNA therapy"),
        ("vaccine", "vaccine"), ("protein", "protein therapy"),
    ):
        if term in lowered:
            return label
    return None


def _is_valid_asset_name(name: str, *, explicit_context: bool = False) -> bool:
    cleaned = str(name or "").strip(" ,.;:()[]")
    lowered = cleaned.lower()
    if len(cleaned) < 4 or lowered in _TABLE_HEADER_ASSETS or cleaned.upper() in _STOP_ASSETS:
        return False
    if any(lowered.endswith(suffix) for suffix in _DRUG_NAME_SUFFIXES):
        return True
    if _DRUG_CODE_RE.fullmatch(cleaned):
        return True
    # "Candidate orforglipron"처럼 공시가 명시적으로 후보물질이라고 부른 고유명만 허용한다.
    return explicit_context and cleaned[0].isalpha() and not cleaned.isupper()


def _has_confirmed_approval_context(sentence: str, asset: str) -> bool:
    """승인 가능성·경쟁약 언급을 회사의 승인 자산으로 저장하지 않는다."""
    lowered = sentence.lower()
    asset_pattern = re.escape(asset.lower())
    if re.search(rf"\b(?:if|when|once|unless)\s+(?:{asset_pattern}\s+(?:is|was)\s+)?approved\b", lowered):
        return False
    if re.search(r"\b(?:potential|possible|anticipated)\s+(?:fda\s+)?approval\b", lowered):
        return False
    confirmed_patterns = (
        rf"(?:fda|ema|mhra)\s+(?:has\s+)?approved\s+{asset_pattern}\b",
        rf"{asset_pattern}.{{0,90}}\b(?:was|is|has been)\s+approved\b",
        rf"\b(?:we|our company|the company)\b.{{0,120}}\breceived\b.{{0,60}}\bapproval\b.{{0,80}}{asset_pattern}",
        rf"{asset_pattern}.{{0,100}}\bmarketing authorization\b",
    )
    return any(re.search(pattern, lowered) for pattern in confirmed_patterns)


def _is_phase_claim_valid(sentence: str, asset: str, phase: str) -> bool:
    lowered = sentence.lower()
    asset_pattern = re.escape(asset.lower())
    phase_lower = phase.lower()
    if re.search(rf"\b(?:if|when|once|unless)\s+{asset_pattern}\s+(?:is|was)\s+approved\b", lowered):
        return False
    if phase_lower in {"nda", "bla", "ind"}:
        if "for example" in lowered or "different indication" in lowered:
            return False
        return bool(
            re.search(rf"{asset_pattern}.{{0,140}}\b{phase_lower}\b", lowered)
            or re.search(rf"\b{phase_lower}\b.{{0,140}}{asset_pattern}", lowered)
        )
    return True


def extract_pipeline(text: str) -> tuple[list[dict[str, Any]], str]:
    """파이프라인 문맥에서 단계와 함께 명시된 코드명만 구조화한다."""
    windows = _pipeline_windows(text)
    # 파이프라인 그림과 떨어진 위험요인·임상 설명 문단에서도 약물 코드가 명시된 문장을 보조 탐색한다.
    for phase_occurrence in _PHASE_RE.finditer(text):
        context = text[max(0, phase_occurrence.start() - 600): min(len(text), phase_occurrence.end() + 900)]
        windows.append(context)
        if len(windows) >= 220:
            break
    if not windows:
        return [], ""
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    for window in windows:
        sentences = re.split(r"(?<=[.!?;])\s+", window)
        for sentence in sentences:
            phase_match = _PHASE_RE.search(sentence)
            inferred_phase = None
            if not phase_match and re.search(r"\blate[- ]stage(?: development)? pipeline\b", sentence, re.IGNORECASE):
                inferred_phase = "Late-stage"
            if not phase_match and not inferred_phase:
                continue
            if phase_match and phase_match.group(1).lower() == "approved":
                prefix = sentence[max(0, phase_match.start() - 20):phase_match.start()].lower()
                if re.search(r"\b(?:if|when|once|unless)\s*$", prefix):
                    continue
            all_candidates = [m.group(1) for m in _ASSET_RE.finditer(sentence)]
            all_candidates.extend(match.group(1) for match in _GENERIC_DRUG_RE.finditer(sentence))
            code_candidates = [name for name in all_candidates if _is_valid_asset_name(name)]
            drug_name_candidates = [
                name for name in all_candidates
                if any(name.lower().endswith(suffix) for suffix in _DRUG_NAME_SUFFIXES)
                and _is_valid_asset_name(name)
            ]
            # 사업보고서 표/서술의 "Compound Development <물질명>" 문맥을 우선한다.
            named = re.findall(r"(?:Compound|Program|Candidate)\s+(?:Development\s+)?([A-Z][A-Za-z0-9-]{3,30})", sentence)
            named.extend(re.findall(
                r"\b([A-Z][A-Za-z0-9-]{3,30})\s+(?:is|was|are)\s+(?:also\s+)?(?:currently\s+)?(?:being\s+)?evaluated\b",
                sentence,
                re.IGNORECASE,
            ))
            named.extend(re.findall(
                r"\b(?:study|trial|program)\s+(?:of|for|evaluating)\s+(?:oral\s+)?([A-Z][A-Za-z0-9-]{3,30})\b",
                sentence,
                re.IGNORECASE,
            ))
            named.extend(re.findall(
                r"\b(?:evaluating|evaluate)\s+(?:the\s+safety\s+and\s+efficacy\s+of\s+)?([A-Z][A-Za-z0-9-]{3,30})\b",
                sentence,
                re.IGNORECASE,
            ))
            named.extend(re.findall(
                r"\b(?:lead\s+(?:product\s+)?candidate|lead\s+compound(?:\s+in\s+development)?)\s*(?:is|,)?\s*([A-Z][A-Za-z0-9-]{3,30})\b",
                sentence,
                re.IGNORECASE,
            ))
            for candidate_group in re.findall(r"product candidates?\s*\(([^)]{5,500})\)", sentence, re.IGNORECASE):
                named.extend(re.findall(r"(?:low-dose\s+)?([A-Za-z][A-Za-z0-9-]{3,30})\s+for\s+(?:the\s+treatment\s+of\s+)?", candidate_group, re.IGNORECASE))
            named = [name for name in named if _is_valid_asset_name(name, explicit_context=True)]
            # 질환명·유전자 변이를 물질명으로 오인하지 않도록 약물 코드·의약품 접미사만 보관한다.
            candidates = list(dict.fromkeys([*named, *code_candidates, *drug_name_candidates]))
            if not candidates:
                continue
            phase = _clean_phase(phase_match.group(1)) if phase_match else inferred_phase
            if phase.lower().startswith("phase") and re.search(
                r"\b(?:planned|potential|plan(?:s|ned)?\s+to|may\s+initiate|intend(?:s|ed)?\s+to)\b",
                sentence,
                re.IGNORECASE,
            ):
                phase = f"Planned {phase}"
            if phase.lower() == "approved":
                candidates = [asset for asset in candidates if _has_confirmed_approval_context(sentence, asset)]
            # 공시가 코드명과 일반명을 같은 문장에서 별칭으로 설명하면 코드명을 대표값으로 쓴다.
            coded_assets = [asset for asset in candidates if _DRUG_CODE_RE.fullmatch(asset)]
            if coded_assets and re.search(r"\b(?:formerly|also known as|or)\b", sentence, re.IGNORECASE):
                candidates = coded_assets
            for asset in candidates:
                if asset.isdigit() or len(asset) < 3:
                    continue
                if not _is_phase_claim_valid(sentence, asset, phase):
                    continue
                indication_match = re.search(
                    rf"\b{re.escape(asset)}\b\s+for\s+(?:the\s+treatment\s+of\s+)?([^,;).]{{4,120}})",
                    sentence,
                    re.IGNORECASE,
                )
                indication = indication_match.group(1).strip() if indication_match else _extract_indication(sentence)
                key = (asset.upper(), phase.lower())
                item = {
                    "asset_name": asset,
                    "phase": phase,
                    "indication": indication,
                    "modality": _extract_modality(sentence),
                    "source_excerpt": sentence[:700],
                    "confidence": "heuristic_source_linked",
                }
                if key not in assets:
                    assets[key] = item
    return list(assets.values())[:80], windows[0][:12000]


def _eligible_row(conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT m.ticker, COALESCE(m.company_name, m.ticker) AS company_name, m.sector, m.industry,
               COALESCE(s.market_cap, m.market_cap, 0) AS market_cap
        FROM us_stock_meta m
        LEFT JOIN us_frontend_snapshot s ON s.ticker=m.ticker
        WHERE m.ticker=? AND UPPER(COALESCE(m.country,''))='US'
        """,
        (ticker,),
    ).fetchone()


def _company_tokens(name: str) -> set[str]:
    return {
        part for part in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
        if len(part) >= 4 and part not in {"company", "inc", "corp", "corporation", "incorporated", "plc", "ltd", "limited"}
    }


def refresh_clinical_trials(conn: sqlite3.Connection, ticker: str, company_name: str) -> dict[str, int]:
    """ClinicalTrials.gov의 회사 스폰서 시험을 원문 일정 그대로 저장한다."""
    response = requests.get(
        "https://clinicaltrials.gov/api/v2/studies",
        params={"query.term": company_name, "pageSize": 100, "format": "json"}, timeout=30,
    )
    response.raise_for_status()
    company_words = _company_tokens(company_name)
    conn.execute("DELETE FROM us_biotech_clinical_trials WHERE ticker=?", (ticker,))
    saved = 0
    for study in response.json().get("studies", []):
        protocol = study.get("protocolSection", {}) or {}
        sponsor_module = protocol.get("sponsorCollaboratorsModule", {}) or {}
        lead_sponsor = (sponsor_module.get("leadSponsor") or {}).get("name") or ""
        collaborators = [str(item.get("name") or "") for item in sponsor_module.get("collaborators") or []]
        sponsor_text = " ".join([lead_sponsor, *collaborators]).lower()
        # Alpha/Medical 같은 일반 단어 하나만 겹치는 타사 시험을 막기 위해 핵심어 2개 이상을 요구한다.
        required_matches = min(2, len(company_words))
        if company_words and sum(1 for word in company_words if word in sponsor_text) < required_matches:
            continue
        ident = protocol.get("identificationModule", {}) or {}
        status = protocol.get("statusModule", {}) or {}
        design = protocol.get("designModule", {}) or {}
        conditions = protocol.get("conditionsModule", {}) or {}
        arms_interventions = protocol.get("armsInterventionsModule", {}) or {}
        results_module = study.get("resultsSection", {}) or {}
        outcome_module = results_module.get("outcomeMeasuresModule", {}) or {}
        primary_outcomes = [
            {"title": item.get("title"), "description": item.get("description"), "time_frame": item.get("timeFrame"), "units": item.get("units")}
            for item in outcome_module.get("outcomeMeasures") or []
            if str(item.get("type") or "").upper() == "PRIMARY"
        ]
        nct_id = ident.get("nctId")
        if not nct_id:
            continue
        intervention_names = []
        for intervention in arms_interventions.get("interventions") or []:
            if str(intervention.get("type") or "").upper() not in {"DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT", "DEVICE"}:
                continue
            aliases = [str(value).strip() for value in intervention.get("otherNames") or [] if str(value).strip()]
            primary_name = str(intervention.get("name") or "").strip()
            # 장문의 기기 설명보다 레지스트리에 함께 등록된 가장 짧은 공식 별칭 하나를 우선한다.
            selected_names = [min(aliases, key=lambda value: (":" in value, len(value)))] if aliases else ([primary_name] if primary_name else [])
            intervention_names.extend(selected_names)
        def date_of(value):
            return (value or {}).get("date") if isinstance(value, dict) else value
        conn.execute(
            """
            INSERT INTO us_biotech_clinical_trials(
                ticker,nct_id,title,status,phase,conditions_json,interventions_json,start_date,
                primary_completion_date,completion_date,last_update_date,sponsor_name,source_url,has_results,primary_outcomes_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(ticker,nct_id) DO UPDATE SET
                title=excluded.title,status=excluded.status,phase=excluded.phase,
                conditions_json=excluded.conditions_json,interventions_json=excluded.interventions_json,
                start_date=excluded.start_date,primary_completion_date=excluded.primary_completion_date,
                completion_date=excluded.completion_date,last_update_date=excluded.last_update_date,
                sponsor_name=excluded.sponsor_name,source_url=excluded.source_url,has_results=excluded.has_results,
                primary_outcomes_json=excluded.primary_outcomes_json,updated_at=CURRENT_TIMESTAMP
            """,
            (
                ticker, nct_id, ident.get("briefTitle") or ident.get("officialTitle"),
                status.get("overallStatus"), ", ".join(design.get("phases") or []),
                json.dumps(conditions.get("conditions") or [], ensure_ascii=False),
                json.dumps(list(dict.fromkeys(intervention_names)), ensure_ascii=False),
                date_of(status.get("startDateStruct")), date_of(status.get("primaryCompletionDateStruct")),
                date_of(status.get("completionDateStruct")), date_of(status.get("lastUpdatePostDateStruct")),
                lead_sponsor, f"https://clinicaltrials.gov/study/{nct_id}", int(bool(results_module)),
                json.dumps(primary_outcomes, ensure_ascii=False),
            ),
        )
        saved += 1
    return {"saved": saved}


def _clinical_trial_pipeline(conn: sqlite3.Connection, ticker: str) -> list[dict[str, Any]]:
    """SEC 추출 실패 시 스폰서가 확인된 최근 임상 중재약물로 파이프라인을 보완한다."""
    rows = conn.execute(
        """SELECT nct_id,title,status,phase,conditions_json,interventions_json,
                  primary_completion_date,last_update_date,source_url
             FROM us_biotech_clinical_trials WHERE ticker=?
             ORDER BY COALESCE(last_update_date,'1900-01-01') DESC""",
        (ticker,),
    ).fetchall()
    excluded_statuses = {"WITHDRAWN", "TERMINATED", "SUSPENDED", "NO_LONGER_AVAILABLE"}
    excluded_names = {
        "placebo", "saline", "standard of care", "best supportive care", "no intervention",
        "physician's choice", "physician choice", "usual care", "observation",
    }
    recent_cutoff = (datetime.now() - timedelta(days=1095)).date().isoformat()
    phase_rank = {"PHASE1": 1, "PHASE1|PHASE2": 2, "PHASE2": 3, "PHASE2|PHASE3": 4, "PHASE3": 5, "PHASE4": 6}
    assets: dict[str, dict[str, Any]] = {}
    for row in rows:
        status = str(row["status"] or "").upper()
        last_update = str(row["last_update_date"] or "")[:10]
        if status in excluded_statuses or (status == "COMPLETED" and last_update and last_update < recent_cutoff):
            continue
        try:
            interventions = json.loads(row["interventions_json"] or "[]")
            conditions = json.loads(row["conditions_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        phase = str(row["phase"] or "").replace("EARLY_PHASE1", "Phase 1").replace("PHASE", "Phase ").replace("|", "/") or "임상 단계 미기재"
        for raw_name in interventions:
            name = re.sub(r"\s+", " ", str(raw_name or "")).strip(" ,.;:()[]")
            name = re.sub(r"^(?:Device|Drug|Biological|Experimental)\s*:\s*", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b.*$", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s+dose to be determined.*$", "", name, flags=re.IGNORECASE)
            if "dart" in name.lower() and any(term in name.lower() for term in ("seed", "diffus", "radiation")):
                name = "DaRT"
            lowered = name.lower()
            if not name or len(name) < 3 or len(name) > 80 or len(name.split()) > 6:
                continue
            if lowered in excluded_names or any(term in lowered for term in ("placebo", "standard of care", "supportive care")):
                continue
            if not re.search(r"[A-Za-z]", name):
                continue
            key = lowered
            item = {
                "asset_name": name,
                "phase": phase,
                "indication": ", ".join(str(value) for value in conditions[:4]) or None,
                "modality": None,
                "source_excerpt": str(row["title"] or "")[:700],
                "source_type": "ClinicalTrials.gov",
                "source_url": row["source_url"],
                "source_nct_id": row["nct_id"],
                "confidence": "registry_sponsor_intervention",
                "ownership_status": "unverified_trial_intervention",
            }
            previous = assets.get(key)
            current_rank = phase_rank.get(str(row["phase"] or "").upper(), 0)
            previous_rank = phase_rank.get(str(previous.get("_raw_phase") or "").upper(), 0) if previous else -1
            if previous is None or current_rank > previous_rank:
                item["_raw_phase"] = row["phase"]
                assets[key] = item
    result = []
    for item in assets.values():
        item.pop("_raw_phase", None)
        result.append(item)
    return result[:80]


def refresh_consensus_snapshot(conn: sqlite3.Connection, ticker: str) -> None:
    """목표주가/의견을 저장해 화면 조회 여부와 무관하게 매일 갱신한다."""
    import yfinance as yf
    info = yf.Ticker(ticker).info or {}
    def number(key):
        value = info.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    conn.execute(
        """
        INSERT INTO us_biotech_consensus_snapshot(
            ticker,target_mean_price,target_high_price,target_low_price,recommendation_key,
            recommendation_mean,analyst_count,source,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(ticker) DO UPDATE SET
            target_mean_price=excluded.target_mean_price,target_high_price=excluded.target_high_price,
            target_low_price=excluded.target_low_price,recommendation_key=excluded.recommendation_key,
            recommendation_mean=excluded.recommendation_mean,analyst_count=excluded.analyst_count,
            source=excluded.source,updated_at=CURRENT_TIMESTAMP
        """,
        (ticker, number("targetMeanPrice"), number("targetHighPrice"), number("targetLowPrice"),
         info.get("recommendationKey") or "", number("recommendationMean"),
         int(info.get("numberOfAnalystOpinions") or 0), "yahoo_finance"),
    )


def refresh_news_snapshot(conn: sqlite3.Connection, ticker: str) -> int:
    """Yahoo Finance 뉴스 원문 링크와 게시 시각을 저장한다."""
    import yfinance as yf
    saved = 0
    for item in (yf.Ticker(ticker).news or [])[:30]:
        content = item.get("content") or item
        title = content.get("title") or item.get("title")
        url = content.get("canonicalUrl", {}).get("url") or content.get("clickThroughUrl", {}).get("url") or item.get("link")
        published = content.get("pubDate") or item.get("providerPublishTime") or ""
        if not title:
            continue
        news_id = hashlib.sha1(f"{ticker}|{url or title}|{published}".encode()).hexdigest()
        conn.execute(
            """INSERT INTO us_biotech_news(ticker,news_id,published_at,title,publisher,url,summary,source,updated_at)
               VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(ticker,news_id) DO UPDATE SET title=excluded.title,publisher=excluded.publisher,
               url=excluded.url,summary=excluded.summary,updated_at=CURRENT_TIMESTAMP""",
            (ticker, news_id, str(published), title, content.get("provider", {}).get("displayName") or item.get("publisher"),
             url, content.get("summary") or item.get("summary") or "", "yahoo_finance"),
        )
        saved += 1
    return saved


def refresh_fda_labels(conn: sqlite3.Connection, ticker: str, company_name: str) -> int:
    """FDA 공개 라벨에서 승인 약품·적응증·박스경고를 별도 보관한다."""
    manufacturer = re.sub(r"\s+(incorporated|inc\.?|corp\.?|corporation|ltd\.?|plc)$", "", company_name or "", flags=re.I)
    response = requests.get(
        "https://api.fda.gov/drug/label.json",
        params={"search": f'openfda.manufacturer_name:"{manufacturer}"', "limit": 100}, timeout=30,
    )
    if response.status_code == 404:
        return 0
    response.raise_for_status()
    saved = 0
    for label in response.json().get("results", []):
        openfda = label.get("openfda") or {}
        brands = openfda.get("brand_name") or []
        generics = openfda.get("generic_name") or []
        key = label.get("id") or "|".join(brands + generics)
        if not key:
            continue
        conn.execute(
            """INSERT INTO us_biotech_fda_labels(ticker,product_key,brand_name,generic_name,manufacturer_name,indications,boxed_warning,effective_time,source_url,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(ticker,product_key) DO UPDATE SET brand_name=excluded.brand_name,generic_name=excluded.generic_name,
               manufacturer_name=excluded.manufacturer_name,indications=excluded.indications,boxed_warning=excluded.boxed_warning,
               effective_time=excluded.effective_time,source_url=excluded.source_url,updated_at=CURRENT_TIMESTAMP""",
            (ticker, str(key), ", ".join(brands), ", ".join(generics), ", ".join(openfda.get("manufacturer_name") or []),
             " ".join(label.get("indications_and_usage") or [])[:4000], int(bool(label.get("boxed_warning"))),
             label.get("effective_time"), "https://api.fda.gov/drug/label.json"),
        )
        saved += 1
    return saved


def refresh_biotech_pipeline(ticker: str) -> dict[str, Any]:
    """한 종목의 최신 10-K/10-Q를 SEC에서 읽고 파이프라인 근거를 저장한다."""
    ensure_us_biotech_tables()
    ticker = (ticker or "").upper().strip()
    conn = _connect()
    try:
        row = _eligible_row(conn, ticker)
        if not row:
            return {"ok": False, "ticker": ticker, "status": "not_found"}
        if not is_biotech_company(row["sector"], row["industry"]):
            return {"ok": False, "ticker": ticker, "status": "not_biotech"}
        pipeline: list[dict[str, Any]] = []
        excerpt = ""
        filing_url = ""
        filing_date = ""
        accession_no = ""
        form = ""
        raw_text_hash = None
        sec_error = None
        clinical_company_name = str(row["company_name"] or ticker)
        try:
            cik = _load_sec_map().get(ticker) or _lookup_sec_cik_by_company(str(row["company_name"] or ticker), ticker)
            if not cik:
                raise RuntimeError("SEC CIK mapping unavailable")
            submissions = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_headers(), timeout=20
            )
            submissions.raise_for_status()
            submissions_payload = submissions.json()
            clinical_company_name = str(submissions_payload.get("name") or clinical_company_name)
            recent = submissions_payload.get("filings", {}).get("recent", {})
            forms = recent.get("form", []) or []
            docs = recent.get("primaryDocument", []) or []
            dates = recent.get("filingDate", []) or []
            accession = recent.get("accessionNumber", []) or []
            # 분기보고서는 전년 보고서 이후의 변경사항만 쓰는 경우가 많다. 전체 후보물질은
            # 최신 연간보고서(10-K/20-F/40-F)를 우선 사용하고 없을 때만 분기·6-K를 보조한다.
            form_priority = {"10-K": 0, "10-KT": 0, "20-F": 0, "40-F": 0, "10-Q": 1, "6-K": 2}
            candidates = [
                (form_priority[str(form).upper()], idx, form)
                for idx, form in enumerate(forms)
                if str(form).upper() in form_priority and idx < len(docs) and docs[idx]
            ]
            target = (min(candidates) if candidates else None)
            if not target:
                raise RuntimeError("No recent 10-K/10-Q filing")
            _, idx, form = target
            accession_no = str(accession[idx] or "")
            accession_path = accession_no.replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{docs[idx]}"
            document = requests.get(filing_url, headers=_headers(), timeout=30)
            document.raise_for_status()
            raw_text = _html_to_text(document.text)
            pipeline, excerpt = extract_pipeline(raw_text)
            filing_date = str(dates[idx] or "")
            raw_text_hash = hashlib.sha256(raw_text.encode("utf-8", "ignore")).hexdigest()
            for asset in pipeline:
                asset.setdefault("source_type", "SEC filing")
                asset.setdefault("source_url", filing_url)
        except Exception as exc:
            sec_error = str(exc)[:500]

        clinical = {"saved": 0}
        try:
            clinical = refresh_clinical_trials(conn, ticker, clinical_company_name)
        except Exception:
            pass

        if not pipeline:
            pipeline = _clinical_trial_pipeline(conn, ticker)

        try:
            refresh_consensus_snapshot(conn, ticker)
        except Exception:
            # 컨센서스 제공 중단은 SEC/임상 수집 결과를 무효화하지 않는다.
            pass
        try:
            refresh_news_snapshot(conn, ticker)
        except Exception:
            pass
        try:
            refresh_fda_labels(conn, ticker, str(row["company_name"] or ticker))
        except Exception:
            pass

        status = "structured" if pipeline else "source_review_needed"
        review_reason = None
        if not pipeline:
            review_reason = sec_error or (
                "SEC filing linked; pipeline candidates require image/table or product-context review"
                if filing_url else "SEC filing unavailable"
            )
        _upsert_snapshot(
            conn, ticker, company_name=row["company_name"], filing_date=filing_date, form=str(form),
            accession_no=accession_no, source_url=filing_url, pipeline_json=json.dumps(pipeline, ensure_ascii=False),
            source_excerpt=excerpt, extraction_status=status, source_text_hash=raw_text_hash,
            parser_version=PIPELINE_PARSER_VERSION, last_error=review_reason,
        )
        conn.commit()
        return {
            "ok": True, "ticker": ticker, "status": status, "pipeline_count": len(pipeline),
            "pipeline_source": pipeline[0].get("source_type") if pipeline else None, **clinical,
        }
    finally:
        conn.close()


def get_biotech_pipeline(ticker: str) -> dict[str, Any]:
    ensure_us_biotech_tables()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM us_biotech_pipeline_snapshot WHERE ticker=?", ((ticker or "").upper().strip(),)).fetchone()
        if not row:
            return {"ticker": (ticker or "").upper().strip(), "status": "pending", "pipeline": []}
        data = dict(row)
        try:
            data["pipeline"] = json.loads(data.pop("pipeline_json") or "[]")
        except json.JSONDecodeError:
            data["pipeline"] = []
        trials = conn.execute(
            """SELECT nct_id,title,status,phase,conditions_json,interventions_json,start_date,
                      primary_completion_date,completion_date,last_update_date,sponsor_name,source_url,updated_at,
                      has_results,primary_outcomes_json
               FROM us_biotech_clinical_trials WHERE ticker=?
               ORDER BY CASE WHEN primary_completion_date IS NULL THEN 1 ELSE 0 END, primary_completion_date ASC""",
            ((ticker or "").upper().strip(),),
        ).fetchall()
        data["clinical_trials"] = []
        for trial in trials:
            item = dict(trial)
            for key in ("conditions_json", "interventions_json", "primary_outcomes_json"):
                try:
                    item[key[:-5]] = json.loads(item.pop(key) or "[]")
                except json.JSONDecodeError:
                    item[key[:-5]] = []
            data["clinical_trials"].append(item)
        consensus = conn.execute(
            "SELECT * FROM us_biotech_consensus_snapshot WHERE ticker=?",
            ((ticker or "").upper().strip(),),
        ).fetchone()
        data["consensus"] = dict(consensus) if consensus else None
        data["news"] = [dict(row) for row in conn.execute(
            "SELECT published_at,title,publisher,url,summary,source,updated_at FROM us_biotech_news WHERE ticker=? ORDER BY published_at DESC LIMIT 30",
            ((ticker or "").upper().strip(),),
        ).fetchall()]
        data["fda_labels"] = [dict(row) for row in conn.execute(
            "SELECT brand_name,generic_name,manufacturer_name,indications,boxed_warning,effective_time,source_url,updated_at FROM us_biotech_fda_labels WHERE ticker=? ORDER BY effective_time DESC LIMIT 50",
            ((ticker or "").upper().strip(),),
        ).fetchall()]
        return data
    finally:
        conn.close()


def collect_biotech_pipelines(min_market_cap: float = 300_000_000, limit: int = 25) -> dict[str, int]:
    """야간 배치용. 실패 종목은 다음 배치에서 다시 시도한다."""
    ensure_us_biotech_tables()
    refresh_days = max(1, int(os.getenv("US_BIOTECH_PIPELINE_REFRESH_DAYS", "7")))
    stale_before = (datetime.now() - timedelta(days=refresh_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT m.ticker, m.sector, m.industry
            FROM us_stock_meta m
            LEFT JOIN us_frontend_snapshot s ON s.ticker=m.ticker
            LEFT JOIN us_biotech_pipeline_snapshot p ON p.ticker=m.ticker
            WHERE UPPER(COALESCE(m.country,''))='US'
              AND COALESCE(s.market_cap, m.market_cap, 0) >= ?
              AND (p.updated_at IS NULL OR p.updated_at < ? OR p.extraction_status='error'
                   OR COALESCE(p.parser_version,'')<>?)
            ORDER BY COALESCE(p.updated_at, '1900-01-01') ASC, COALESCE(s.market_cap, m.market_cap, 0) DESC
            """,
            (max(0.0, float(min_market_cap)), stale_before, PIPELINE_PARSER_VERSION),
        ).fetchall()
    finally:
        conn.close()

    eligible = [
        str(row["ticker"]) for row in rows
        if is_biotech_company(row["sector"], row["industry"])
    ][:max(1, min(int(limit), 100))]
    stats = {"selected": len(eligible), "structured": 0, "review_needed": 0, "errors": 0}
    for ticker in eligible:
        result = refresh_biotech_pipeline(ticker)
        if result.get("status") == "structured":
            stats["structured"] += 1
        elif result.get("status") == "source_review_needed":
            stats["review_needed"] += 1
        else:
            stats["errors"] += 1
        time.sleep(0.2)  # SEC 요청을 짧게 분산한다.
    return stats
