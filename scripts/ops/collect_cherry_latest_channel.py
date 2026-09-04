#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["TELEGRAM_SESSION_PATH"] = "/Users/brainlee/Telstock_monitor"

import telegram_collector as collector


async def main() -> None:
    channels = [
        {
            "channel_id": "체리형부 채널 26.06.01~08.31",
            "channel_name": "체리형부 채널 26.06.01~08.31",
            "entity_hint": "-1003907826971",
        }
    ]
    await collector.run_collect(channels, limit=5000, since_days=None)


if __name__ == "__main__":
    asyncio.run(main())
