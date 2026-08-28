"""hs_sector_map(HS코드-섹터) 매핑 상태를 hs_code_company_map(HS코드-기업) 검증 결과로 승격.

배경(2026-08-23): Codex가 텔레그램 채널(@BeOn_BeClear)의 그래프·표를 회사 분기 수출입
합계와 대조해 hs_code_company_map(기업 단위) 쪽은 다수의 HS코드를 'exact'/'composite'로
검증해뒀지만, 같은 HS코드를 참조하는 hs_sector_map(섹터 단위) 행은 그 검증이 새로 반영되지
않아 여전히 'provisional'로 남아있는 경우가 많았다(347건 중 exact 86건뿐, provisional 252건).

원리: mapping_status는 "이 HS코드가 실제로 이 상품을 가리키는가"에 대한 신뢰도다. 어떤
기업이 그 HS코드에 대해 exact 검증(분기 합계 일치 등 실증)을 받았다면, 그 HS코드 자체의
상품 정체성은 이미 확인된 것이므로 — 같은 HS코드를 쓰는 섹터 매핑도 동일한 신뢰도를
가질 자격이 있다. sector_key(어느 섹터에 속하는지)는 건드리지 않고 mapping_status/note만
승격한다 — 섹터 분류 판단 자체를 재심사하는 것이 아니라, 이미 확보된 증거를 동기화할 뿐이다.

멱등적으로 재실행 가능 — daily_refresh.py에서 정기적으로 호출해 새로 exact 검증되는
기업 매핑이 생길 때마다 섹터 매핑도 자동으로 따라 올라가게 한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"


def promote(conn: sqlite3.Connection) -> list[dict]:
    candidates = conn.execute(
        """
        SELECT sm.id, sm.hs_code, sm.sector_key, sm.note
        FROM hs_sector_map sm
        WHERE sm.mapping_status = 'provisional'
          AND EXISTS (
            SELECT 1 FROM hs_code_company_map hcm
            WHERE hcm.hs_code = sm.hs_code AND hcm.mapping_status = 'exact'
          )
        """
    ).fetchall()

    promoted = []
    for sector_map_id, hs_code, sector_key, note in candidates:
        evidence_rows = conn.execute(
            """
            SELECT stock_name, note FROM hs_code_company_map
            WHERE hs_code = ? AND mapping_status = 'exact'
            ORDER BY confidence DESC LIMIT 1
            """,
            (hs_code,),
        ).fetchall()
        evidence_company, evidence_note = (evidence_rows[0] if evidence_rows else (None, ""))
        appended = (
            f" | (섹터 매핑 자동 승격) hs_code={hs_code}의 기업 단위 exact 검증"
            f"({evidence_company}: {evidence_note[:120]})을 근거로 provisional→exact 동기화."
        )
        new_note = (note or "") + appended
        conn.execute(
            "UPDATE hs_sector_map SET mapping_status='exact', note=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_note, sector_map_id),
        )
        promoted.append({"id": sector_map_id, "hs_code": hs_code, "sector_key": sector_key})
    conn.commit()
    return promoted


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    before = dict(
        conn.execute(
            "SELECT mapping_status, COUNT(*) FROM hs_sector_map GROUP BY mapping_status"
        ).fetchall()
    )
    promoted = promote(conn)
    after = dict(
        conn.execute(
            "SELECT mapping_status, COUNT(*) FROM hs_sector_map GROUP BY mapping_status"
        ).fetchall()
    )
    conn.close()
    print(
        {
            "promoted_count": len(promoted),
            "before": before,
            "after": after,
        }
    )


if __name__ == "__main__":
    main()
