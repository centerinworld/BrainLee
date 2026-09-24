#!/usr/bin/env python3
"""Run the yfinance market-radar cache outside the FastAPI process."""
from __future__ import annotations

import asyncio
import logging

from routes.market_radar import _refresh_cache_worker


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_refresh_cache_worker())
