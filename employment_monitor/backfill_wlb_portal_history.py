#!/usr/bin/env python3
"""Import annual Labor Welfare Corporation workplace archives with exact biz numbers."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EMP_DB = ROOT / "employment_monitor" / "employment.db"
PORTAL = "https://www.data.go.kr"
PUBLIC_DATA_PK = "15002150"
CURRENT_DETAIL_PK = "uddi:a892c4d6-2c4b-47db-baf8-8e8c8e7b5637"
DEFAULT_ARCHIVE = Path("/Volumes/Realtek_NVME/stock_dashboard/employment_history/wlb_raw")
if not DEFAULT_ARCHIVE.parent.parent.exists():
    DEFAULT_ARCHIVE = ROOT / "employment_monitor" / "wlb_raw"


def _normalized_header(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _indexes(header: list[str]) -> dict[str, int]:
    aliases = {
        "biz": ("사업자등록번호", "SAEOPJADRNO"),
        "workers": ("고용보험상시근로자수", "GYEBHGJNRINWONSU", "SANGSIINWONCNT"),
        "name": ("사업장명", "SAEOPJANGMYEONG"),
        "address": ("사업장주소", "SAEOPJANGJUSO"),
    }
    normalized = [_normalized_header(value).upper() for value in header]
    result = {
        key: next((i for i, value in enumerate(normalized) if any(a in value for a in names)), -1)
        for key, names in aliases.items()
    }
    if result["workers"] < 0 or (result["biz"] < 0 and (result["name"] < 0 or result["address"] < 0)):
        raise ValueError(f"required WLB columns missing: {result}")
    return result


def _integer(value: str | None) -> int:
    cleaned = re.sub(r"[^0-9-]", "", str(value or ""))
    return int(cleaned) if cleaned not in ("", "-") else 0


def _identity(name: str | None, address: str | None) -> str:
    clean_name = re.sub(r"[^0-9A-Z가-힣]", "", str(name or "").upper())
    clean_address = re.sub(r"[^0-9A-Z가-힣]", "", str(address or "").upper())
    return f"{clean_name}|{clean_address}" if clean_name and clean_address else ""


def _year_from_title(title: str) -> str | None:
    match = re.search(r"(20\d{2}|19\d{2})1231\s*$", title.strip())
    return match.group(1) if match else None


def _request(session: requests.Session, url: str, **kwargs) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.get(url, timeout=90, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed: {url}: {last_error}")


def discover_archives(session: requests.Session) -> list[dict[str, str]]:
    response = _request(
        session,
        f"{PORTAL}/tcs/dss/selectHistAndCsvData.do",
        params={"publicDataPk": PUBLIC_DATA_PK, "publicDataDetailPk": CURRENT_DETAIL_PK},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    soup = BeautifulSoup(response.text, "html.parser")
    candidates = [{"detail_pk": CURRENT_DETAIL_PK, "title": "근로복지공단_고용 산재보험 가입 현황_20251231", "year": "2025"}]
    for link in soup.select("a.openFileDetailPopup[data-public-pk]"):
        title = link.get_text(" ", strip=True)
        year = _year_from_title(title)
        if year:
            candidates.append({"detail_pk": str(link["data-public-pk"]), "title": title, "year": year})
    # The portal contains two 2020 revisions; prefer the first (newer registration) entry.
    unique: dict[str, dict[str, str]] = {}
    for row in candidates:
        unique.setdefault(row["year"], row)
    return sorted(unique.values(), key=lambda row: row["year"])


def download_archive(session: requests.Session, info: dict[str, str], archive_dir: Path) -> Path:
    detail = _request(
        session,
        f"{PORTAL}/tcs/dss/selectDpkDetailInfo.do",
        params={"publicDataDetailPk": info["detail_pk"]},
    ).text
    match = re.search(
        r"fn_fileDataDown\([^,]+,[^,]+,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'",
        detail,
    )
    if not match:
        raise RuntimeError(f"download attachment unavailable: {info['title']}")
    file_id, detail_sn, extension = match.groups()
    extension = extension.lower() if extension.lower() in {"zip", "csv"} else "zip"
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"wlb_{info['year']}_{info['detail_pk'].split(':')[-1]}.{extension}"
    if path.exists() and path.stat().st_size > 1024:
        return path
    url = f"{PORTAL}/cmm/cmm/fileDownload.do?{urlencode({'atchFileId': file_id, 'fileDetailSn': detail_sn})}"
    response = _request(session, url, stream=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("wb") as output:
        for chunk in response.iter_content(1024 * 1024):
            if chunk:
                output.write(chunk)
    temporary.replace(path)
    return path


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wlb_portal_annual (
            stock_code TEXT NOT NULL,
            data_year TEXT NOT NULL,
            total_workers INTEGER NOT NULL DEFAULT 0,
            workplace_count INTEGER NOT NULL DEFAULT 0,
            source_rows INTEGER NOT NULL DEFAULT 0,
            identity_matched_rows INTEGER NOT NULL DEFAULT 0,
            source_file TEXT NOT NULL,
            source_detail_pk TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, data_year)
        );
        CREATE INDEX IF NOT EXISTS ix_wlb_portal_annual_year ON wlb_portal_annual(data_year);
        CREATE TABLE IF NOT EXISTS wlb_portal_imports (
            source_detail_pk TEXT PRIMARY KEY,
            data_year TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_rows INTEGER NOT NULL,
            matched_rows INTEGER NOT NULL,
            matched_stocks INTEGER NOT NULL,
            invalid_rows INTEGER NOT NULL,
            identity_matched_rows INTEGER NOT NULL DEFAULT 0,
            learned_identities INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS wlb_workplace_identity (
            identity_key TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            source_year TEXT NOT NULL,
            source_detail_pk TEXT NOT NULL,
            PRIMARY KEY (identity_key, stock_code)
        );
        CREATE INDEX IF NOT EXISTS ix_wlb_workplace_identity_key ON wlb_workplace_identity(identity_key);
        """
    )
    annual_columns = {row[1] for row in conn.execute("PRAGMA table_info(wlb_portal_annual)")}
    if "identity_matched_rows" not in annual_columns:
        conn.execute("ALTER TABLE wlb_portal_annual ADD COLUMN identity_matched_rows INTEGER NOT NULL DEFAULT 0")
    import_columns = {row[1] for row in conn.execute("PRAGMA table_info(wlb_portal_imports)")}
    if "identity_matched_rows" not in import_columns:
        conn.execute("ALTER TABLE wlb_portal_imports ADD COLUMN identity_matched_rows INTEGER NOT NULL DEFAULT 0")
    if "learned_identities" not in import_columns:
        conn.execute("ALTER TABLE wlb_portal_imports ADD COLUMN learned_identities INTEGER NOT NULL DEFAULT 0")


def _biz_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT DISTINCT bizr_no,stock_code FROM employment_company WHERE LENGTH(bizr_no)=10"
    )
    grouped: dict[str, set[str]] = defaultdict(set)
    for biz_no, code in rows:
        grouped[re.sub(r"\D", "", biz_no)].add(code)
    return {biz_no: next(iter(codes)) for biz_no, codes in grouped.items() if len(codes) == 1}


def import_zip(path: Path, year: str, detail_pk: str, conn: sqlite3.Connection) -> dict:
    init_schema(conn)
    mapping = _biz_map(conn)
    identity_candidates: dict[str, set[str]] = defaultdict(set)
    for identity_key, code in conn.execute("SELECT identity_key,stock_code FROM wlb_workplace_identity"):
        identity_candidates[identity_key].add(code)
    identity_map = {
        identity_key: next(iter(codes))
        for identity_key, codes in identity_candidates.items()
        if len(codes) == 1
    }
    aggregate: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    learned_identities: set[tuple[str, str, str]] = set()
    source_rows = matched_rows = identity_matched_rows = invalid_rows = 0
    existing_year_sources: dict[str, set[str]] = defaultdict(set)
    for existing_year, source_pk in conn.execute(
        "SELECT DISTINCT data_year,source_detail_pk FROM wlb_portal_annual"
    ):
        existing_year_sources[str(existing_year)].add(str(source_pk))
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.lower().endswith(".csv"):
                continue
            member_year_match = re.search(r"(20\d{2}|19\d{2})(?:1231|\D)", member.filename)
            member_year = member_year_match.group(1) if member_year_match else year
            if (
                member_year != year
                and member_year in existing_year_sources
                and detail_pk not in existing_year_sources[member_year]
            ):
                continue
            with archive.open(member) as probe:
                first_line = probe.readline()
            try:
                decoded_header = first_line.decode("utf-8-sig", errors="strict")
                encoding = "utf-8-sig"
            except UnicodeDecodeError:
                encoding = "cp949"
                decoded_header = first_line.decode(encoding, errors="replace")
            delimiter = "\t" if decoded_header.count("\t") > decoded_header.count(",") else ","
            with archive.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding=encoding, errors="replace", newline="")
                reader = csv.reader(text, delimiter=delimiter)
                idx = _indexes(next(reader))
                for row in reader:
                    source_rows += 1
                    if len(row) <= max(idx.values()):
                        invalid_rows += 1
                        continue
                    identity_key = _identity(row[idx["name"]], row[idx["address"]])
                    if idx["biz"] >= 0:
                        biz_no = re.sub(r"\D", "", row[idx["biz"]])
                        code = mapping.get(biz_no)
                        if code and identity_key:
                            learned_identities.add((identity_key, code, member_year))
                    else:
                        code = identity_map.get(identity_key)
                        if code:
                            identity_matched_rows += 1
                    if not code:
                        continue
                    values = aggregate[(code, member_year)]
                    values[0] += _integer(row[idx["workers"]])
                    values[1] += 1
                    values[2] += 1
                    if idx["biz"] < 0:
                        values[3] += 1
                    matched_rows += 1

    conn.executemany(
        "INSERT OR REPLACE INTO wlb_workplace_identity(identity_key,stock_code,source_year,source_detail_pk) VALUES (?,?,?,?)",
        [(identity_key, code, source_year, detail_pk) for identity_key, code, source_year in learned_identities],
    )
    imported_years = sorted({data_year for _, data_year in aggregate}) or [year]
    for data_year in imported_years:
        conn.execute(
            "DELETE FROM wlb_portal_annual WHERE data_year=? AND source_detail_pk=?",
            (data_year, detail_pk),
        )
    for (code, data_year), (workers, workplaces, row_count, identity_rows) in aggregate.items():
        conn.execute(
            """
            INSERT INTO wlb_portal_annual
              (stock_code,data_year,total_workers,workplace_count,source_rows,identity_matched_rows,source_file,source_detail_pk)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(stock_code,data_year) DO UPDATE SET
              total_workers=excluded.total_workers,workplace_count=excluded.workplace_count,
              source_rows=excluded.source_rows,identity_matched_rows=excluded.identity_matched_rows,
              source_file=excluded.source_file,
              source_detail_pk=excluded.source_detail_pk,imported_at=CURRENT_TIMESTAMP
            """,
            (code, data_year, workers, workplaces, row_count, identity_rows, path.name, detail_pk),
        )
    stored_year = imported_years[0] if len(imported_years) == 1 else f"{imported_years[0]}-{imported_years[-1]}"
    conn.execute(
        """
        INSERT INTO wlb_portal_imports
          (source_detail_pk,data_year,source_file,source_rows,matched_rows,matched_stocks,invalid_rows,
           identity_matched_rows,learned_identities)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_detail_pk) DO UPDATE SET
          data_year=excluded.data_year,source_file=excluded.source_file,
          source_rows=excluded.source_rows,matched_rows=excluded.matched_rows,
          matched_stocks=excluded.matched_stocks,invalid_rows=excluded.invalid_rows,
          identity_matched_rows=excluded.identity_matched_rows,
          learned_identities=excluded.learned_identities,
          imported_at=CURRENT_TIMESTAMP
        """,
        (detail_pk, stored_year, path.name, source_rows, matched_rows,
         len({code for code, _ in aggregate}), invalid_rows, identity_matched_rows,
         len(learned_identities)),
    )
    conn.commit()
    return {
        "year": stored_year,
        "years": imported_years,
        "source_rows": source_rows,
        "matched_rows": matched_rows,
        "matched_stocks": len({code for code, _ in aggregate}),
        "identity_matched_rows": identity_matched_rows,
        "learned_identities": len(learned_identities),
        "invalid_rows": invalid_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import WLB annual portal ZIP")
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--year")
    parser.add_argument("--detail-pk")
    parser.add_argument("--db", type=Path, default=EMP_DB)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--from-year", default="2015")
    parser.add_argument("--to-year", default="2025")
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    args = parser.parse_args()
    if args.path:
        if not args.year or not args.detail_pk:
            parser.error("path requires --year and --detail-pk")
        conn = sqlite3.connect(args.db, timeout=120)
        report = import_zip(args.path, args.year, args.detail_pk, conn)
        conn.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not args.list and not args.backfill:
        parser.error("use a path, --list, or --backfill")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"})
    archives = [row for row in discover_archives(session) if args.from_year <= row["year"] <= args.to_year]
    if args.list:
        print(json.dumps(archives, ensure_ascii=False, indent=2))
        return 0

    conn = sqlite3.connect(args.db, timeout=120)
    init_schema(conn)
    imported = {row[0] for row in conn.execute("SELECT source_detail_pk FROM wlb_portal_imports")}
    reports = []
    for info in archives:
        if info["detail_pk"] in imported:
            continue
        path = download_archive(session, info, args.archive_dir)
        if not zipfile.is_zipfile(path):
            raise RuntimeError(f"unsupported non-ZIP annual archive: {path}")
        reports.append(import_zip(path, info["year"], info["detail_pk"], conn))
    conn.close()
    print(json.dumps({"selected": len(archives), "imported": len(reports), "reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
