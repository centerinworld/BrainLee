"""
fix_employment_all.py — 전체 고용보험 데이터 재수집 (opaBoheomFg=1 보정)

[문제]
fetch_employment_insurance.py가 opaBoheomFg 없이 API 호출하여
고용보험(1)+산재보험(2) 이중집계 발생. KAI 예시: 10,635명→5,281명.

[수정]
opaBoheomFg=1 (고용보험만) 필터 추가 후 1,615개 기업 전체 재수집.

[KAI NPS 특별처리]
KAI(047810)가 nps_seq_map에 없었으나, 12개월치 NPS 데이터 발견.
전체 seq 리스트로 12개월 히스토리 저장.

실행:
  python3 employment_monitor/fix_employment_all.py
  python3 employment_monitor/fix_employment_all.py --kai-only   # KAI NPS만
  python3 employment_monitor/fix_employment_all.py --ec-only    # employment_company만
"""
import argparse
import sqlite3
import sys
import time
import os
import logging
from datetime import datetime

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
EMP_DB   = os.path.join(_DIR, 'employment.db')
STOCK_DB = os.path.join(_DIR, '..', 'stock.db')

sys.path.insert(0, os.path.join(_DIR, '..'))
try:
    from urllib.parse import unquote
    from config import PUBLIC_DATA_API_KEY
    EMP_API_KEY = unquote(PUBLIC_DATA_API_KEY)
    NPS_API_KEY = EMP_API_KEY
except Exception:
    EMP_API_KEY = os.getenv('PUBLIC_DATA_API_KEY', '')
    NPS_API_KEY = EMP_API_KEY

EMP_INS_URL = 'http://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomBsshItem'
NPS_BASE    = 'https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2'


# ── 고용보험 API (opaBoheomFg=1 강제) ──────────────────────────────────────
def fetch_sangsi(bizr_no: str) -> int | None:
    """고용보험(1)만 상시인원 합산. None = API 실패."""
    all_items = []
    page = 1
    while True:
        try:
            r = requests.get(EMP_INS_URL, params={
                'serviceKey': EMP_API_KEY,
                'v_saeopjaDrno': bizr_no,
                'opaBoheomFg': '1',   # 고용보험만 (산재보험 이중집계 방지)
                'pageNo': page,
                'numOfRows': 100,
                '_type': 'json',
            }, timeout=15)
            body = r.json().get('response', {}).get('body', {})
            total_count = int(body.get('totalCount') or 0)
            items = body.get('items', {}).get('item', [])
            if isinstance(items, dict):
                items = [items]
            all_items.extend(items)
            if len(all_items) >= total_count or not items:
                break
            page += 1
        except Exception as e:
            logger.warning(f"API 오류 bizr={bizr_no}: {e}")
            break
    if not all_items:
        return None
    return sum(int(it.get('sangsiInwonCnt') or 0) for it in all_items)


# ── NPS API ──────────────────────────────────────────────────────────────────
def _nps_get(endpoint: str, params: dict) -> dict:
    params['serviceKey'] = NPS_API_KEY
    params['dataType']   = 'json'
    for attempt in range(3):
        try:
            r = requests.get(f'{NPS_BASE}/{endpoint}', params=params, timeout=15)
            return r.json().get('response', {}).get('body', {})
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
    return {}

def _nps_items(body: dict) -> list:
    items = body.get('items', {})
    if isinstance(items, dict):
        items = items.get('item', [])
    if isinstance(items, dict):
        items = [items]
    return items if isinstance(items, list) else []


# ── KAI NPS 12개월 히스토리 수집 ──────────────────────────────────────────
KAI_SEQS_ORDERED = [
    # seq → 추정 기준월 (seq 오름차순 = 오래된 순)
    # 6521733이 202605인 것을 anchor로 역산
    (89901,   None),
    (635395,  None),
    (1184302, None),
    (1754573, None),
    (2375018, None),
    (2920833, None),
    (3513553, None),
    (4102900, None),
    (5113177, None),
    (5283672, None),
    (5928738, None),
    (6521733, None),   # 최신 = 202605
]


def _infer_kai_months(anchor_seq: int = 6521733, anchor_ym: str = '202605') -> list:
    """
    anchor_ym(202605)에서 역산하여 각 seq의 기준월 추정.
    seq 정렬 → 인덱스로 월 오프셋 계산.
    """
    seqs_sorted = sorted([s for s, _ in KAI_SEQS_ORDERED])  # ascending = oldest first
    anchor_idx  = seqs_sorted.index(anchor_seq)
    # anchor에서 뒤로 갈수록 +1month, 앞으로 갈수록 -1month
    ay, am = int(anchor_ym[:4]), int(anchor_ym[4:6])
    result = []
    for i, seq in enumerate(seqs_sorted):
        offset = i - anchor_idx  # anchor_idx 기준 상대 월 offset
        y = ay + (am + offset - 1) // 12
        m = (am + offset - 1) % 12 + 1
        result.append((seq, f'{y}{m:02d}'))
    return result  # [(seq, 'YYYYMM'), ...]


def collect_kai_nps(emp_conn: sqlite3.Connection):
    """KAI(047810) NPS seq 등록 + 12개월 월별 데이터 수집."""
    logger.info('[KAI NPS] nps_seq_map 등록 시작')

    latest_seq = 6521733
    latest_ym  = '202605'

    # nps_seq_map 업데이트
    emp_conn.execute("""
        INSERT OR REPLACE INTO nps_seq_map (stock_code, seq, wkpl_nm, biz_no_6, jnngp_cnt, updated_ym)
        VALUES (?,?,?,?,?,?)
    """, ('047810', latest_seq, '한국항공우주산업(주)본점', '110814', 5032, latest_ym))
    emp_conn.commit()
    logger.info(f'[KAI NPS] nps_seq_map 업데이트: seq={latest_seq}, ym={latest_ym}')

    # 12개월 seq → 기준월 매핑
    seq_ym_list = _infer_kai_months(latest_seq, latest_ym)
    logger.info(f'[KAI NPS] {len(seq_ym_list)}개 seq 처리 예정')

    inserted = 0
    for seq, data_ym in seq_ym_list:
        # 이미 있으면 스킵
        existing = emp_conn.execute(
            "SELECT 1 FROM nps_monthly WHERE stock_code='047810' AND data_ym=?", (data_ym,)
        ).fetchone()
        if existing:
            logger.info(f'  seq={seq} ym={data_ym}: 이미 존재, 스킵')
            continue

        # jnngpCnt (가입자수) from getDetailInfoSearchV2
        body_d = _nps_get('getDetailInfoSearchV2', {'seq': str(seq), 'numOfRows': '1'})
        items_d = _nps_items(body_d)
        jnngp_cnt = int(items_d[0].get('jnngpCnt') or 0) if items_d else 0
        time.sleep(0.3)

        # 신규취득/상실 from getPdAcctoSttusInfoSearchV2
        body_p = _nps_get('getPdAcctoSttusInfoSearchV2', {'seq': str(seq), 'numOfRows': '1'})
        items_p = _nps_items(body_p)
        new_hires    = int(items_p[0].get('nwAcqzrCnt')  or 0) if items_p else 0
        terminations = int(items_p[0].get('lssJnngpCnt') or 0) if items_p else 0
        net_change   = new_hires - terminations
        time.sleep(0.3)

        emp_conn.execute("""
            INSERT OR IGNORE INTO nps_monthly (stock_code, data_ym, new_hires, terminations, net_change)
            VALUES (?,?,?,?,?)
        """, ('047810', data_ym, new_hires, terminations, net_change))
        emp_conn.commit()
        logger.info(f'  seq={seq} ym={data_ym}: jnngp={jnngp_cnt}, 취득={new_hires}, 상실={terminations}, net={net_change}')
        inserted += 1

    logger.info(f'[KAI NPS] 완료 — {inserted}개 월 신규 저장')
    return inserted


# ── employment_company 전체 재수집 (opaBoheomFg=1 보정) ─────────────────────
def fix_employment_company(emp_conn: sqlite3.Connection, delay: float = 0.25):
    """
    2026-05 데이터가 있는 1,615개 기업을 opaBoheomFg=1 필터로 재수집.
    기존값과 비교하여 변경된 건만 UPDATE.
    """
    target_ym = '2026-05'
    rows = emp_conn.execute(
        "SELECT stock_code, stock_name, worker_count, bizr_no FROM employment_company "
        "WHERE ym=? AND bizr_no IS NOT NULL",
        (target_ym,)
    ).fetchall()

    logger.info(f'[EC 재수집] 대상: {len(rows)}개 기업 (ym={target_ym})')

    updated = skipped = errors = 0
    before_total = after_total = 0
    changed_companies = []

    for i, (code, name, old_wc, bizr_no) in enumerate(rows, 1):
        new_wc = fetch_sangsi(bizr_no)
        time.sleep(delay)

        if new_wc is None:
            errors += 1
            continue

        before_total += (old_wc or 0)
        after_total  += new_wc

        if new_wc == old_wc:
            skipped += 1
        else:
            diff = new_wc - (old_wc or 0)
            ratio = abs(diff) / max(old_wc or 1, 1) * 100
            # 전월 데이터로 mom/yoy 재계산
            prev_row = emp_conn.execute(
                "SELECT worker_count FROM employment_company WHERE stock_code=? AND ym<? ORDER BY ym DESC LIMIT 1",
                (code, target_ym)
            ).fetchone()
            prev_yr_row = emp_conn.execute(
                "SELECT worker_count FROM employment_company WHERE stock_code=? AND ym=?",
                (code, '2025-05')
            ).fetchone()
            mom = round(new_wc - prev_row[0], 2)  if prev_row   else None
            yoy = round(new_wc - prev_yr_row[0], 2) if prev_yr_row else None

            emp_conn.execute(
                "UPDATE employment_company SET worker_count=?, mom_change=?, yoy_change=?, fetched_at=CURRENT_TIMESTAMP "
                "WHERE stock_code=? AND ym=?",
                (new_wc, mom, yoy, code, target_ym)
            )
            emp_conn.commit()
            updated += 1
            if ratio >= 5:  # 5% 이상 변화만 리포트
                changed_companies.append((code, name, old_wc, new_wc, diff, ratio))

        if i % 100 == 0:
            logger.info(f'  [{i}/{len(rows)}] 수정:{updated} 동일:{skipped} 오류:{errors}')

    logger.info(f'[EC 재수집] 완료 — 수정:{updated}, 동일:{skipped}, 오류:{errors}')
    logger.info(f'  전체 합계: {before_total:,} → {after_total:,} (Δ{after_total-before_total:+,})')
    return updated, skipped, errors, changed_companies, before_total, after_total


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kai-only',  action='store_true', help='KAI NPS 수집만')
    ap.add_argument('--ec-only',   action='store_true', help='employment_company 재수집만')
    ap.add_argument('--delay',     type=float, default=0.25, help='API 딜레이(초)')
    args = ap.parse_args()

    emp_conn = sqlite3.connect(EMP_DB, timeout=60)

    report_lines = [f"=== 고용보험 전체 재수집 리포트 ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===\n"]

    # 1) KAI NPS 수집
    if not args.ec_only:
        kai_inserted = collect_kai_nps(emp_conn)
        report_lines.append(f"[KAI NPS] 신규 저장: {kai_inserted}개월")

        # 저장 확인
        kai_rows = emp_conn.execute(
            "SELECT data_ym, new_hires, terminations, net_change FROM nps_monthly "
            "WHERE stock_code='047810' ORDER BY data_ym"
        ).fetchall()
        report_lines.append(f"[KAI NPS] 전체 {len(kai_rows)}개월 데이터:")
        for r in kai_rows:
            report_lines.append(f"  {r[0]}: 취득+{r[1]} 상실-{r[2]} net={r[3]:+d}")

    # 2) employment_company 전체 재수집
    if not args.kai_only:
        upd, skip, err, changed, b_total, a_total = fix_employment_company(emp_conn, args.delay)
        report_lines.append(f"\n[EC 재수집] 수정:{upd}건, 동일:{skip}건, 오류:{err}건")
        report_lines.append(f"[EC 재수집] 전체 인원 합계: {b_total:,} → {a_total:,} ({a_total-b_total:+,}명)")
        report_lines.append(f"\n[5% 이상 변화 기업 목록] ({len(changed)}건):")
        for code, name, old_wc, new_wc, diff, ratio in sorted(changed, key=lambda x: -abs(x[4])):
            report_lines.append(f"  {name}({code}): {old_wc:,} → {new_wc:,} ({diff:+,}명, {ratio:.1f}%)")

    emp_conn.close()

    report = '\n'.join(report_lines)
    print(report)

    # 리포트 파일 저장
    report_path = os.path.join(_DIR, 'fix_report.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    logger.info(f'리포트 저장: {report_path}')


if __name__ == '__main__':
    main()
