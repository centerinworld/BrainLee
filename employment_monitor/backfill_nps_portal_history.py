#!/usr/bin/env python3
"""Backfill long-run NPS company history from data.go.kr periodic CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from employment_monitor.collect_nps_monthly import _name_patterns


EMP_DB = ROOT / "employment_monitor" / "employment.db"
PUBLIC_DATA_PK = "15083277"
CURRENT_DETAIL_PK = "uddi:365580a5-5619-4e14-b61f-b38e7abeba27"
PORTAL = "https://www.data.go.kr"
HISTORY_ENDPOINT = f"{PORTAL}/tcs/dss/selectHistAndCsvData.do"
METADATA_ENDPOINT = f"{PORTAL}/tcs/dss/selectFileDataDownload.do"
DOWNLOAD_ENDPOINT = f"{PORTAL}/cmm/cmm/fileDownload.do"
DEFAULT_ARCHIVE = Path("/Volumes/Realtek_NVME/stock_dashboard/employment_history/nps_raw")
if not DEFAULT_ARCHIVE.parent.parent.exists():
    DEFAULT_ARCHIVE = ROOT / "employment_monitor" / "nps_raw"

logger = logging.getLogger(__name__)
_SUFFIX = re.compile(r"(주식회사|유한회사|합자회사|재단법인|사단법인|\(주\)|㈜|\s+)", re.I)


def _clean_name(value: str | None) -> str:
    return _SUFFIX.sub("", str(value or "")).upper().strip()


def _extract_label_ym(title: str) -> str | None:
    match = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월", title)
    if match:
        return f"{match.group(1)}{int(match.group(2)):02d}"
    match = re.search(r"(20\d{2})(\d{2})(?:\d{2})?", title)
    if match and 1 <= int(match.group(2)) <= 12:
        return match.group(1) + match.group(2)
    match = re.search(r"(\d{2})/(\d{2})/(20\d{2})", title)
    if match:
        return match.group(3) + match.group(1)
    return None


def _request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.request(method, url, timeout=60, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed: {url}: {last_error}")


def discover_history(session: requests.Session) -> list[dict[str, str]]:
    response = _request(
        session,
        "GET",
        HISTORY_ENDPOINT,
        params={"publicDataPk": PUBLIC_DATA_PK, "publicDataDetailPk": CURRENT_DETAIL_PK},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    soup = BeautifulSoup(response.text, "html.parser")
    files: list[dict[str, str]] = []
    for link in soup.select("a.openFileDetailPopup[data-public-pk]"):
        title = link.get_text(" ", strip=True)
        detail_pk = str(link.get("data-public-pk") or "").strip()
        label_ym = _extract_label_ym(title)
        if detail_pk and label_ym:
            files.append({"detail_pk": detail_pk, "title": title, "label_ym": label_ym})
    current = get_download_metadata(session, CURRENT_DETAIL_PK)
    current_info = current.get("dataSetFileDetailInfo") or {}
    current_title = str(current_info.get("dataNm") or current_info.get("publicDataSj") or "")
    current_label_ym = _extract_label_ym(current_title)
    if current_label_ym and not any(row["detail_pk"] == CURRENT_DETAIL_PK for row in files):
        files.append({"detail_pk": CURRENT_DETAIL_PK, "title": current_title, "label_ym": current_label_ym})
    files.sort(key=lambda row: (row["label_ym"], row["detail_pk"]))
    return files


def get_download_metadata(session: requests.Session, detail_pk: str) -> dict:
    response = _request(
        session,
        "GET",
        METADATA_ENDPOINT,
        params={
            "publicDataDetailPk": detail_pk,
            "publicDataPk": PUBLIC_DATA_PK,
            "atchFileId": "",
            "fileDetailSn": "1",
            "publicDataTyCode": "PR0051",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    data = response.json()
    if not data.get("status") or not data.get("atchFileId"):
        raise RuntimeError(f"download metadata unavailable for {detail_pk}")
    return data


def download_file(session: requests.Session, file_info: dict, archive_dir: Path) -> Path:
    metadata = get_download_metadata(session, file_info["detail_pk"])
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"nps_{file_info['label_ym']}_{file_info['detail_pk'].split(':')[-1]}.csv"
    if path.exists() and path.stat().st_size > 1024:
        return path
    params = {"atchFileId": metadata["atchFileId"], "fileDetailSn": metadata.get("fileDetailSn", "1")}
    url = f"{DOWNLOAD_ENDPOINT}?{urlencode(params)}"
    response = _request(session, "GET", url, stream=True)
    temp_path = path.with_suffix(".part")
    with temp_path.open("wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                output.write(chunk)
    temp_path.replace(path)
    return path


def _open_csv(path: Path):
    for encoding in ("cp949", "utf-8-sig", "utf-8"):
        handle = path.open("r", encoding=encoding, errors="strict", newline="")
        try:
            header = next(csv.reader(handle))
            handle.seek(0)
            return handle, encoding, header
        except UnicodeDecodeError:
            handle.close()
    handle = path.open("r", encoding="cp949", errors="replace", newline="")
    header = next(csv.reader(handle))
    handle.seek(0)
    return handle, "cp949-replace", header


def _column_indexes(header: list[str]) -> dict[str, int]:
    tokens = {
        "ym": ("DATA_CRT_YM", "자료생성년월"),
        "name": ("WKPL_NM", "사업장명"),
        "biz": ("BZOWR_RGST_NO", "사업자등록번호"),
        "subscribers": ("JNNGP_CNT", "가입자수"),
        "hires": ("NW_ACQZR_CNT", "신규취득자수"),
        "terms": ("LSS_JNNGP_CNT", "상실가입자수"),
    }
    indexes: dict[str, int] = {}
    normalized = [re.sub(r"\s+", "", value).upper() for value in header]
    for key, aliases in tokens.items():
        indexes[key] = next(
            (
                index
                for index, value in enumerate(normalized)
                if any(re.sub(r"\s+", "", alias).upper() in value for alias in aliases)
            ),
            -1,
        )
    if any(value < 0 for value in indexes.values()):
        raise ValueError(f"required NPS columns missing: {indexes}")
    return indexes


def _integer(value: str | None) -> int:
    cleaned = re.sub(r"[^0-9-]", "", str(value or ""))
    return int(cleaned) if cleaned not in ("", "-") else 0


def build_match_index(conn: sqlite3.Connection) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    candidates: dict[str, set[str]] = defaultdict(set)
    aliases: dict[str, set[str]] = defaultdict(set)
    for code, name, prefix in conn.execute(
        "SELECT stock_code,stock_name,biz_no_6 FROM stock_bizno_map WHERE LENGTH(biz_no_6)=6"
    ):
        candidates[str(prefix)].add(str(code))
        for value in [name, *_name_patterns(str(name or ""))]:
            cleaned = _clean_name(value)
            if len(cleaned) >= 3:
                aliases[str(code)].add(cleaned)
    for code, name in conn.execute("SELECT DISTINCT stock_code,stock_name FROM employment_company"):
        cleaned = _clean_name(name)
        if len(cleaned) >= 3:
            aliases[str(code)].add(cleaned)
    for code, name in conn.execute("SELECT stock_code,wkpl_nm FROM nps_seq_map WHERE wkpl_nm IS NOT NULL"):
        cleaned = _clean_name(name)
        if len(cleaned) >= 3:
            aliases[str(code)].add(cleaned)
    return candidates, aliases


def match_stock_code(
    biz_prefix: str,
    workplace_name: str,
    candidates: dict[str, set[str]],
    aliases: dict[str, set[str]],
) -> tuple[str | None, str]:
    old_name = _clean_name(workplace_name)
    hits = []
    for code in candidates.get(biz_prefix, set()):
        if any(old_name == alias or old_name.startswith(alias) or alias.startswith(old_name) for alias in aliases[code]):
            hits.append(code)
    if len(hits) == 1:
        return hits[0], "prefix_name"
    return None, "ambiguous" if len(hits) > 1 else "unmatched"


def init_history_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS nps_portal_monthly (
            stock_code TEXT NOT NULL,
            data_ym TEXT NOT NULL,
            subscriber_count INTEGER NOT NULL DEFAULT 0,
            new_hires INTEGER NOT NULL DEFAULT 0,
            terminations INTEGER NOT NULL DEFAULT 0,
            net_change INTEGER NOT NULL DEFAULT 0,
            workplace_count INTEGER NOT NULL DEFAULT 0,
            matched_rows INTEGER NOT NULL DEFAULT 0,
            match_method TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_detail_pk TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, data_ym)
        );
        CREATE INDEX IF NOT EXISTS ix_nps_portal_monthly_ym ON nps_portal_monthly(data_ym);
        CREATE TABLE IF NOT EXISTS nps_portal_imports (
            source_detail_pk TEXT PRIMARY KEY,
            label_ym TEXT,
            data_ym TEXT,
            source_file TEXT,
            source_rows INTEGER,
            matched_rows INTEGER,
            matched_stocks INTEGER,
            ambiguous_rows INTEGER,
            unmatched_rows INTEGER,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def import_csv(path: Path, file_info: dict, conn: sqlite3.Connection) -> dict:
    candidates, aliases = build_match_index(conn)
    aggregate: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    source_rows = matched_rows = ambiguous_rows = unmatched_rows = 0
    handle, encoding, _ = _open_csv(path)
    try:
        reader = csv.reader(handle)
        header = next(reader)
        idx = _column_indexes(header)
        for row in reader:
            source_rows += 1
            if len(row) <= max(idx.values()):
                unmatched_rows += 1
                continue
            ym = re.sub(r"[^0-9]", "", row[idx["ym"]])[:6]
            if len(ym) != 6:
                unmatched_rows += 1
                continue
            code, reason = match_stock_code(row[idx["biz"]].strip()[:6], row[idx["name"]], candidates, aliases)
            if not code:
                if reason == "ambiguous":
                    ambiguous_rows += 1
                else:
                    unmatched_rows += 1
                continue
            values = aggregate[(code, ym)]
            values[0] += _integer(row[idx["subscribers"]])
            values[1] += _integer(row[idx["hires"]])
            values[2] += _integer(row[idx["terms"]])
            values[3] += 1
            values[4] += 1
            matched_rows += 1
    finally:
        handle.close()

    init_history_schema(conn)
    data_yms = sorted({ym for _, ym in aggregate})
    for ym in data_yms:
        conn.execute(
            "DELETE FROM nps_portal_monthly WHERE data_ym=? AND source_detail_pk=?",
            (ym, file_info["detail_pk"]),
        )
    for (code, ym), values in aggregate.items():
        subscribers, hires, terms, workplaces, row_count = values
        conn.execute(
            """
            INSERT INTO nps_portal_monthly
            (stock_code,data_ym,subscriber_count,new_hires,terminations,net_change,
             workplace_count,matched_rows,match_method,source_file,source_detail_pk)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(stock_code,data_ym) DO UPDATE SET
              subscriber_count=excluded.subscriber_count,new_hires=excluded.new_hires,
              terminations=excluded.terminations,net_change=excluded.net_change,
              workplace_count=excluded.workplace_count,matched_rows=excluded.matched_rows,
              match_method=excluded.match_method,source_file=excluded.source_file,
              source_detail_pk=excluded.source_detail_pk,imported_at=CURRENT_TIMESTAMP
            """,
            (code, ym, subscribers, hires, terms, hires - terms, workplaces, row_count,
             "prefix_name", path.name, file_info["detail_pk"]),
        )
    data_ym = data_yms[0] if len(data_yms) == 1 else ",".join(data_yms)
    conn.execute(
        """
        INSERT INTO nps_portal_imports
        (source_detail_pk,label_ym,data_ym,source_file,source_rows,matched_rows,
         matched_stocks,ambiguous_rows,unmatched_rows)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_detail_pk) DO UPDATE SET
          label_ym=excluded.label_ym,data_ym=excluded.data_ym,source_file=excluded.source_file,
          source_rows=excluded.source_rows,matched_rows=excluded.matched_rows,
          matched_stocks=excluded.matched_stocks,ambiguous_rows=excluded.ambiguous_rows,
          unmatched_rows=excluded.unmatched_rows,imported_at=CURRENT_TIMESTAMP
        """,
        (file_info["detail_pk"], file_info["label_ym"], data_ym, path.name, source_rows,
         matched_rows, len({code for code, _ in aggregate}), ambiguous_rows, unmatched_rows),
    )
    conn.commit()
    return {
        "file": path.name,
        "encoding": encoding,
        "label_ym": file_info["label_ym"],
        "data_ym": data_ym,
        "source_rows": source_rows,
        "matched_rows": matched_rows,
        "matched_stocks": len({code for code, _ in aggregate}),
        "ambiguous_rows": ambiguous_rows,
        "unmatched_rows": unmatched_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill NPS periodic CSV history")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--import-file", type=Path)
    parser.add_argument("--detail-pk")
    parser.add_argument("--label-ym")
    parser.add_argument("--from-label", default="201512")
    parser.add_argument("--to-label", default="999999")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--db", type=Path, default=EMP_DB)
    parser.add_argument("--delete-after-import", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"})

    if args.import_file:
        if not args.detail_pk or not args.label_ym:
            parser.error("--import-file requires --detail-pk and --label-ym")
        conn = sqlite3.connect(args.db, timeout=60)
        report = import_csv(args.import_file, {"detail_pk": args.detail_pk, "label_ym": args.label_ym}, conn)
        conn.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    files = discover_history(session)
    selected = [row for row in files if args.from_label <= row["label_ym"] <= args.to_label]
    if args.limit > 0:
        selected = selected[:args.limit]
    if args.list:
        print(json.dumps({"count": len(files), "oldest": files[0] if files else None,
                          "newest": files[-1] if files else None, "selected": selected}, ensure_ascii=False, indent=2))
        return 0

    conn = sqlite3.connect(args.db, timeout=60)
    init_history_schema(conn)
    imported = {row[0] for row in conn.execute("SELECT source_detail_pk FROM nps_portal_imports")}
    reports = []
    for index, file_info in enumerate(selected, start=1):
        if file_info["detail_pk"] in imported:
            logger.info("[%s/%s] already imported: %s", index, len(selected), file_info["title"])
            continue
        logger.info("[%s/%s] downloading: %s", index, len(selected), file_info["title"])
        path = download_file(session, file_info, args.archive_dir)
        report = import_csv(path, file_info, conn)
        reports.append(report)
        logger.info("imported %s", report)
        if args.delete_after_import:
            path.unlink(missing_ok=True)
    conn.close()
    print(json.dumps({"selected": len(selected), "imported": len(reports), "reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
