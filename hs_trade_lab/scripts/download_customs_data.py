from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from customs_reference import default_reference_paths, write_reference_snapshot


ROOT_DIR = Path(__file__).resolve().parents[1]
LOCAL_ENV = ROOT_DIR / ".env"
DOWNLOAD_ROOT = ROOT_DIR / "data" / "customs_downloads"
REFERENCE_ROOT = ROOT_DIR / "data" / "reference"


def load_local_env() -> None:
    if not LOCAL_ENV.exists():
        return
    for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class EndpointSpec:
    key: str
    base_url: str
    operation: str
    required_params: tuple[str, ...]
    optional_filters: tuple[str, ...]
    strategy: str


ENDPOINTS = {
    "itemtrade": EndpointSpec(
        key="itemtrade",
        base_url="https://apis.data.go.kr/1220000/Itemtrade",
        operation="getItemtradeList",
        required_params=("strtYymm", "endYymm"),
        optional_filters=("hsSgn",),
        strategy="year_window",
    ),
    "sidoitemtrade": EndpointSpec(
        key="sidoitemtrade",
        base_url="https://apis.data.go.kr/1220000/sidoitemtrade",
        operation="getSidoitemtradeList",
        required_params=("strtYymm", "endYymm", "sidoCd"),
        optional_filters=("hsSgn",),
        strategy="year_window_by_sido",
    ),
    "sidotempertrade": EndpointSpec(
        key="sidotempertrade",
        base_url="https://apis.data.go.kr/1220000/sidotempertrade",
        operation="getSidotempertradeList",
        required_params=("strtYymm", "endYymm", "sidoCd", "imexTpcd"),
        optional_filters=("dtlTmprYn", "imexTmprClsfCd"),
        strategy="year_window_by_sido_imex",
    ),
    "nationtrade": EndpointSpec(
        key="nationtrade",
        base_url="https://apis.data.go.kr/1220000/nationtrade",
        operation="getNationtradeList",
        required_params=("strtYymm", "endYymm"),
        optional_filters=("cntyCd",),
        strategy="year_window",
    ),
    "idfytempertrade": EndpointSpec(
        key="idfytempertrade",
        base_url="https://apis.data.go.kr/1220000/Idfytempertrade",
        operation="getIdfytempertradeList",
        required_params=("strtYymm", "endYymm", "imexTpcd"),
        optional_filters=("imexTmprClsfCd",),
        strategy="year_window_by_imex",
    ),
    "newtempertrade": EndpointSpec(
        key="newtempertrade",
        # Official public-data gateway path is `ntempertrade/getNtempertradeList`.
        # Keep the local key name for backward compatibility with existing jobs/docs.
        base_url="https://apis.data.go.kr/1220000/ntempertrade",
        operation="getNtempertradeList",
        required_params=("strtYymm", "endYymm", "imexTpcd"),
        optional_filters=("imexTmprUnfcClsfCd",),
        strategy="year_window_by_imex",
    ),
    "sidotrade": EndpointSpec(
        key="sidotrade",
        base_url="https://apis.data.go.kr/1220000/sidotrade",
        operation="getSidotradeList",
        required_params=("strtYymm", "endYymm"),
        optional_filters=("sidoCd",),
        strategy="year_window",
    ),
    "nnewtempertrade": EndpointSpec(
        key="nnewtempertrade",
        base_url="https://apis.data.go.kr/1220000/nnewtempertrade",
        operation="getNnewtempertradeList",
        required_params=("strtYymm", "endYymm", "imexTpcd", "imexTmprUnfcClsfCd", "cntyCd"),
        optional_filters=(),
        strategy="cross_product",
    ),
}


def month_windows(start_year: int, end_year: int) -> list[tuple[str, str]]:
    return [(f"{year}01", f"{year}12") for year in range(start_year, end_year + 1)]


def parse_xml_items(xml_text: str) -> tuple[str, str, list[dict[str, str]]]:
    root = ET.fromstring(xml_text)
    result_code = root.findtext(".//resultCode", default="")
    result_msg = root.findtext(".//resultMsg", default="")
    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        row = {}
        for child in item:
            row[child.tag] = child.text or ""
        items.append(row)
    return result_code, result_msg, items


def parse_openapi_service_error(xml_text: str) -> dict[str, str] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag != "OpenAPI_ServiceResponse":
        return None
    return {
        "err_msg": root.findtext(".//errMsg", default=""),
        "auth_msg": root.findtext(".//returnAuthMsg", default=""),
        "reason_code": root.findtext(".//returnReasonCode", default=""),
    }


def safe_slug(value: str) -> str:
    return value.replace("/", "-").replace(" ", "_")


def request_batches(spec: EndpointSpec, refs: dict[str, Any], start_year: int, end_year: int, include_cross_product: bool) -> list[dict[str, str]]:
    windows = month_windows(start_year, end_year)
    batches: list[dict[str, str]] = []
    if spec.strategy == "year_window":
        for start_ym, end_ym in windows:
            batches.append({"strtYymm": start_ym, "endYymm": end_ym})
    elif spec.strategy == "year_window_by_sido":
        for start_ym, end_ym in windows:
            for sido in refs["sidos"]:
                batches.append({"strtYymm": start_ym, "endYymm": end_ym, "sidoCd": sido["code"]})
    elif spec.strategy == "year_window_by_sido_imex":
        for start_ym, end_ym in windows:
            for sido in refs["sidos"]:
                for imex in ("1", "2"):
                    batches.append(
                        {
                            "strtYymm": start_ym,
                            "endYymm": end_ym,
                            "sidoCd": sido["code"],
                            "imexTpcd": imex,
                            "dtlTmprYn": "N",
                        }
                    )
    elif spec.strategy == "year_window_by_imex":
        for start_ym, end_ym in windows:
            for imex in ("1", "2"):
                batches.append({"strtYymm": start_ym, "endYymm": end_ym, "imexTpcd": imex})
    elif spec.strategy == "cross_product":
        if not include_cross_product:
            return []
        for start_ym, end_ym in windows:
            for imex in ("1", "2"):
                for unified in refs["unified_codes"]:
                    for country in refs["countries"]:
                        batches.append(
                            {
                                "strtYymm": start_ym,
                                "endYymm": end_ym,
                                "imexTpcd": imex,
                                "imexTmprUnfcClsfCd": str(unified["imex_tmpr_unfc_clsf_cd"]),
                                "cntyCd": country["code"],
                            }
                        )
    else:
        raise ValueError(f"unknown strategy: {spec.strategy}")
    return batches


def write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def fetch_endpoint(
    client: httpx.Client,
    service_key: str,
    spec: EndpointSpec,
    params: dict[str, str],
    output_root: Path,
    force: bool,
) -> dict[str, Any]:
    query = {"serviceKey": service_key, **params}
    file_stem = "__".join(f"{k}-{safe_slug(str(v))}" for k, v in sorted(params.items()))
    output_path = output_root / spec.key / f"{file_stem}.jsonl.gz"
    if output_path.exists() and not force:
        return {"status": "skipped", "path": str(output_path), "count": None, "params": params}

    url = f"{spec.base_url}/{spec.operation}"
    response = None
    for attempt in range(4):
        try:
            response = client.get(url, params=query)
            if response.status_code == 403:
                error_info = parse_openapi_service_error(response.text)
                if error_info:
                    return {
                        "status": "approval_required",
                        "path": str(output_path),
                        "count": 0,
                        "params": params,
                        "http_status": response.status_code,
                        "error_code": error_info.get("reason_code", ""),
                        "error_message": error_info.get("auth_msg", "") or error_info.get("err_msg", ""),
                    }
            if response.status_code < 500:
                response.raise_for_status()
                break
            response.raise_for_status()
        except httpx.HTTPStatusError:
            if response is not None and response.status_code < 500:
                raise
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    if response is None:
        raise RuntimeError(f"request failed without response: {url}")
    result_code, result_msg, items = parse_xml_items(response.text)
    payload = [
        {
            "endpoint": spec.key,
            "params": params,
            "result_code": result_code,
            "result_msg": result_msg,
            "row": item,
            "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        for item in items
    ]
    write_jsonl_gz(output_path, payload)
    return {
        "status": "ok",
        "path": str(output_path),
        "count": len(items),
        "result_code": result_code,
        "result_msg": result_msg,
        "params": params,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Korea Customs Itemtrade family datasets")
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=datetime.now().year)
    parser.add_argument(
        "--endpoints",
        nargs="+",
        default=[
            "itemtrade",
            "sidoitemtrade",
            "sidotempertrade",
            "nationtrade",
            "idfytempertrade",
            "newtempertrade",
            "sidotrade",
        ],
        choices=list(ENDPOINTS.keys()),
    )
    parser.add_argument("--include-cross-product", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-requests", type=int, default=0, help="0 means no limit")
    args = parser.parse_args()

    load_local_env()
    service_key = os.getenv("CUSTOMS_ITEMTRADE_SERVICE_KEY", "").strip()
    if not service_key:
        raise SystemExit("CUSTOMS_ITEMTRADE_SERVICE_KEY is empty. Run ./bin/set_customs_key.sh first.")

    refs = write_reference_snapshot(REFERENCE_ROOT, default_reference_paths())
    manifest_root = DOWNLOAD_ROOT
    manifest_root.mkdir(parents=True, exist_ok=True)

    plan: dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "start_year": args.start_year,
        "end_year": args.end_year,
        "endpoints": {},
        "warnings": [],
    }

    request_counter = 0
    with httpx.Client(timeout=60.0) as client:
        for endpoint_name in args.endpoints:
            spec = ENDPOINTS[endpoint_name]
            batches = request_batches(
                spec=spec,
                refs=refs,
                start_year=args.start_year,
                end_year=args.end_year,
                include_cross_product=args.include_cross_product,
            )
            if spec.strategy == "cross_product" and not args.include_cross_product:
                plan["warnings"].append(
                    "nnewtempertrade is excluded by default because the full cross product is extremely large."
                )
            plan["endpoints"][endpoint_name] = {
                "estimated_requests": len(batches),
                "results": [],
            }
            for params in batches:
                if args.max_requests and request_counter >= args.max_requests:
                    plan["warnings"].append(f"stopped early after max_requests={args.max_requests}")
                    (manifest_root / "manifest.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
                    return
                result = fetch_endpoint(
                    client=client,
                    service_key=service_key,
                    spec=spec,
                    params=params,
                    output_root=manifest_root,
                    force=args.force,
                )
                plan["endpoints"][endpoint_name]["results"].append(result)
                request_counter += 1

    (manifest_root / "manifest.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
