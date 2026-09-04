#!/usr/bin/env python3
"""Measure representative read-only APIs used by the main dashboard pages."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.error
import urllib.request


ENDPOINTS = (
    "/api/portfolio",
    "/api/portfolio/transactions",
    "/api/dashboard/stats",
    "/api/signals/fin-screener",
    "/api/dashboard/screening/triple",
    "/api/signals/combo-v2",
    "/api/buy-candidates/auto-board",
    "/api/tenbagger/data-status",
    "/api/trend/holdings",
    "/api/quant-major-indicators/catalog",
)


def measure(base_url: str, path: str, timeout: float) -> dict:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}{path}",
            headers={"Accept-Encoding": "gzip"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return {
                "path": path,
                "status": response.status,
                "seconds": round(time.perf_counter() - started, 3),
                "bytes": len(body),
                "encoding": response.headers.get("Content-Encoding", "identity"),
            }
    except (urllib.error.URLError, TimeoutError) as exc:
        return {
            "path": path,
            "status": 0,
            "seconds": round(time.perf_counter() - started, 3),
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(
            pool.map(
                lambda path: measure(args.base_url, path, args.timeout),
                ENDPOINTS,
            )
        )
    results.sort(key=lambda item: item["seconds"], reverse=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if any(item["status"] != 200 for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
