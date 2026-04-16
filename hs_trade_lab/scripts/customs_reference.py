from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pandas as pd


NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass(frozen=True)
class CustomsReferencePaths:
    hs_master: Path
    hsk_link: Path
    query_codes: Path


def default_reference_paths() -> CustomsReferencePaths:
    downloads = Path("/Users/brainlee/Downloads")
    return CustomsReferencePaths(
        hs_master=downloads / "관세청_HS부호_20260101.xlsx",
        hsk_link=downloads / "관세청_HSK별 신성질별_성질별 분류_20260101.xlsx",
        query_codes=downloads / "관세청조회코드_v1.2.xlsx",
    )


def _col_letters(cell_ref: str) -> str:
    out = []
    for ch in cell_ref:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


def _load_workbook_sst(zip_file: ZipFile) -> list[str]:
    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    items = []
    for si in root.findall("a:si", NS):
        items.append("".join(t.text or "" for t in si.iterfind(".//a:t", NS)))
    return items


def read_query_code_sheet(path: Path, sheet_name: str) -> list[dict[str, str]]:
    with ZipFile(path) as zip_file:
        sst = _load_workbook_sst(zip_file)
        rels = ET.fromstring(zip_file.read("xl/_rels/workbook.xml.rels"))
        rel_map = {row.attrib["Id"]: row.attrib["Target"] for row in rels}
        workbook = ET.fromstring(zip_file.read("xl/workbook.xml"))

        target = None
        for sheet in workbook.find("a:sheets", NS):
            if sheet.attrib["name"] == sheet_name:
                target = "xl/" + rel_map[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
                break
        if not target:
            raise ValueError(f"sheet not found: {sheet_name}")

        worksheet = ET.fromstring(zip_file.read(target))
        rows: list[dict[str, str]] = []
        for row in worksheet.findall(".//a:sheetData/a:row", NS):
            row_map: dict[str, str] = {}
            for cell in row.findall("a:c", NS):
                ref = _col_letters(cell.attrib["r"])
                cell_type = cell.attrib.get("t")
                value = cell.find("a:v", NS)
                if value is None:
                    row_map[ref] = ""
                elif cell_type == "s":
                    row_map[ref] = sst[int(value.text)]
                else:
                    row_map[ref] = value.text or ""
            rows.append(row_map)
        return rows


def load_reference_codes(paths: CustomsReferencePaths | None = None) -> dict[str, Any]:
    paths = paths or default_reference_paths()

    hs_df = pd.read_excel(paths.hs_master)
    hs_items = (
        hs_df.assign(
            HS부호=hs_df["HS부호"].astype(str).str.zfill(10),
            적용시작일자=hs_df["적용시작일자"].astype(str),
            적용종료일자=hs_df["적용종료일자"].astype(str),
        )
        [["HS부호", "한글품목명", "영문품목명", "적용시작일자", "적용종료일자", "성질통합분류코드", "성질통합분류코드명"]]
        .fillna("")
        .to_dict(orient="records")
    )

    hsk = pd.read_excel(paths.hsk_link, sheet_name="2026년")
    hs_code_col = [col for col in hsk.columns if "HS" in str(col) and "10단위부호" in str(col)][0]
    unified_code_col = [col for col in hsk.columns if "분류세세분류코드" in str(col)][0]
    unified_name_col = [col for col in hsk.columns if "분류세세분류명" in str(col)][0]
    unified_rows = (
        hsk[[hs_code_col, unified_code_col, unified_name_col]]
        .rename(
            columns={
                hs_code_col: "hs_code",
                unified_code_col: "imex_tmpr_unfc_clsf_cd",
                unified_name_col: "imex_tmpr_unfc_clsf_nm",
            }
        )
        .fillna("")
    )
    unified_codes = (
        unified_rows[["imex_tmpr_unfc_clsf_cd", "imex_tmpr_unfc_clsf_nm"]]
        .drop_duplicates()
        .assign(imex_tmpr_unfc_clsf_cd=lambda df: df["imex_tmpr_unfc_clsf_cd"].astype(str))
        .to_dict(orient="records")
    )

    query_path = paths.query_codes
    country_rows = read_query_code_sheet(query_path, "국가코드")[3:]
    sido_rows = read_query_code_sheet(query_path, "시도코드")[3:]
    tmpr_rows = read_query_code_sheet(query_path, "성질분류코드")[3:]

    export_tmpr: list[dict[str, str]] = []
    import_tmpr: list[dict[str, str]] = []
    for row in tmpr_rows:
        if row.get("A"):
            export_tmpr.append({"code": row["A"], "name": row.get("B", "")})
        if row.get("D"):
            import_tmpr.append({"code": row["D"], "name": row.get("E", "")})

    return {
        "countries": [{"code": row.get("A", ""), "name": row.get("B", "")} for row in country_rows if row.get("A")],
        "sidos": [{"code": row.get("A", ""), "name": row.get("B", "")} for row in sido_rows if row.get("A")],
        "export_tmpr_codes": export_tmpr,
        "import_tmpr_codes": import_tmpr,
        "hs_codes": hs_items,
        "unified_codes": unified_codes,
    }


def write_reference_snapshot(output_dir: Path, paths: CustomsReferencePaths | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = load_reference_codes(paths=paths)
    for key, value in payload.items():
        (output_dir / f"{key}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return payload
