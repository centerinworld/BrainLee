#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
TARGET_KEY = "cafe:11:2668"
API = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"

GAMES = [
    {"appid": 578080, "game": "PUBG", "stock": "크래프톤", "code": "259960"},
    {"appid": 582660, "game": "Black Desert", "stock": "펄어비스", "code": "263750"},
    {"appid": 1627720, "game": "Lies of P", "stock": "네오위즈", "code": "095660"},
    {"appid": 3489700, "game": "Stellar Blade", "stock": "시프트업", "code": "462870"},
]


def collect() -> dict:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    period = datetime.now().strftime("%Y-%m-%d")
    saved = 0
    failures = []
    for game in GAMES:
        try:
            response = requests.get(API, params={"appid": game["appid"]}, timeout=20)
            response.raise_for_status()
            payload = response.json().get("response") or {}
            if int(payload.get("result") or 0) != 1:
                raise RuntimeError(f"Steam result={payload.get('result')}")
            players = float(payload["player_count"])
            conn.execute(
                """
                INSERT INTO quant_major_indicator_series
                    (indicator_key, period, series_name, value, unit, source_name,
                     source_detail, quality, updated_at)
                VALUES (?, ?, ?, ?, '명', 'STEAM_OFFICIAL_API', ?, 'partial_pc_platform', CURRENT_TIMESTAMP)
                ON CONFLICT(indicator_key, period, series_name, source_name) DO UPDATE SET
                    value=excluded.value,
                    source_detail=excluded.source_detail,
                    quality=excluded.quality,
                    updated_at=excluded.updated_at
                """,
                (
                    TARGET_KEY,
                    period,
                    f"Steam_현재동접_{game['stock']}_{game['game']}",
                    players,
                    f"Steam app {game['appid']}; {game['stock']}({game['code']}); 호출 시점 스냅샷",
                ),
            )
            saved += 1
        except Exception as exc:
            failures.append({"appid": game["appid"], "error": str(exc)[:160]})

    conn.execute(
        """
        UPDATE quant_major_indicator_catalog
        SET status='partial_existing', source_system='Steam official current-player API',
            notes='대표 상장 게임 4종 PC 동접 스냅샷. 모바일/콘솔 및 매출을 대표하지 않으므로 부분 지표로만 사용.',
            updated_at=CURRENT_TIMESTAMP
        WHERE indicator_key=?
        """,
        (TARGET_KEY,),
    )
    conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) FROM quant_major_indicator_series WHERE indicator_key=?", (TARGET_KEY,)
    ).fetchone()[0]
    conn.close()
    return {"saved": saved, "total_rows": total, "failures": failures}


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
