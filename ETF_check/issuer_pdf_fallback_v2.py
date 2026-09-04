"""Issuer fallback with the request profile used by the PLUS product page."""
from __future__ import annotations

import issuer_pdf_fallback as fallback


def fetch_plus(base_date: str, issuer_id: str):
    params = {"n": issuer_id, "page": 0, "d": base_date, "pageSize": 100}
    response = fallback.requests.get(
        fallback.PLUS_PDF_URL,
        params=params,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.plusetf.co.kr/product/detail?n=006368",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json(), response.url


fallback.fetch_plus = fetch_plus


if __name__ == "__main__":
    fallback.main()
