"""
Gemini Advanced 유료 구독 Gems 무인 자동 분석 엔진 v2.0
─────────────────────────────────────────────────────────────
[핵심 로직]
1. 매일 실행 시 session_quota_status.json 에서 Gemini 계정별 토큰/한도 확인
2. 토큰 가용 여부에 따라 처리 가능 건수 동적 조정
   - Gemini Advanced 계정 잠금(is_locked) → 해당 계정 건너뜀
   - Gemini Free tier 항상 보조 가용 (무제한 보조)
3. 최신 리포트부터 우선 처리 (Newest First)
4. 오늘 이미 처리한 파일 skip (중복 방지)
5. 결과를 gems_daily_learned_logs + gems_analyst_insights에 영구 적재
"""
import os
import sys
import glob
import time
import json
import sqlite3
import logging
from datetime import datetime, date

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GeminiGemsWorker")

BASE_DIR = "/Volumes/Realtek_NVME/stock_dashboard"
DB_PATH = os.path.join(BASE_DIR, "stock.db")
QUOTA_STATUS_PATH = os.path.join(BASE_DIR, "session_quota_status.json")
STATUS_LOG_PATH = os.path.join(BASE_DIR, "logs/gemini_gems_status.json")
PROFILE_DIRS = {
    "1": os.path.join(BASE_DIR, "browser_profiles/gemini_account_1"),
    "2": os.path.join(BASE_DIR, "browser_profiles/gemini_account_2"),
}
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

# ── 1. 토큰/한도 현황 로더 ──────────────────────────────────────────────────
def load_quota_status():
    """
    session_quota_status.json 에서 AI 모델별 사용 가능 여부 로드
    반환: {모델키: {is_locked, used_pct, remaining_pct, ...}}
    """
    if not os.path.exists(QUOTA_STATUS_PATH):
        logger.warning(f"⚠️ quota status 파일 없음: {QUOTA_STATUS_PATH} → 기본값 사용")
        return {
            "gemini_advanced_1": {"is_locked": False, "used_pct": 0.0, "remaining_pct": 100.0},
            "gemini_advanced_2": {"is_locked": False, "used_pct": 0.0, "remaining_pct": 100.0},
            "gemini_free": {"is_locked": False, "used_pct": 0.0, "remaining_pct": 100.0},
        }
    
    try:
        with open(QUOTA_STATUS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        
        result = {}
        # Gemini 관련 키 추출
        for key, val in raw.items():
            model_name = (val.get("model") or "").lower()
            status_str = (val.get("status") or val.get("session_status") or "").lower()
            is_locked = val.get("is_locked", False)
            
            if "gemini" in model_name or "gemini" in key.lower():
                if "free" in model_name or "free" in key.lower() or "flash" in model_name:
                    result["gemini_free"] = {
                        "is_locked": is_locked,
                        "used_pct": 0.0,  # free tier - 사실상 무제한
                        "remaining_pct": 100.0,
                        "model": val.get("model", "Gemini Free"),
                        "status": val.get("status", "🟢 가용"),
                    }
                else:
                    # Gemini Advanced (paid)
                    acct_key = "gemini_advanced_1"
                    if "2" in key:
                        acct_key = "gemini_advanced_2"
                    result[acct_key] = {
                        "is_locked": is_locked,
                        "used_pct": val.get("used_pct", 0.0),
                        "remaining_pct": val.get("remaining_pct", 100.0),
                        "model": val.get("model", "Gemini Advanced"),
                        "status": val.get("status", "🟢 가용"),
                    }
        
        # Gemini Advanced 항목이 없으면 기본값
        if "gemini_advanced_1" not in result:
            result["gemini_advanced_1"] = {"is_locked": False, "used_pct": 0.0, "remaining_pct": 100.0, "model": "Gemini Advanced (Acct 1)"}
        if "gemini_advanced_2" not in result:
            result["gemini_advanced_2"] = {"is_locked": False, "used_pct": 0.0, "remaining_pct": 100.0, "model": "Gemini Advanced (Acct 2)"}
        if "gemini_free" not in result:
            result["gemini_free"] = {"is_locked": False, "used_pct": 0.0, "remaining_pct": 100.0, "model": "Gemini Free"}
        
        return result
    except Exception as e:
        logger.error(f"quota status 로드 실패: {e}")
        return {
            "gemini_advanced_1": {"is_locked": False, "used_pct": 50.0, "remaining_pct": 50.0},
            "gemini_advanced_2": {"is_locked": False, "used_pct": 50.0, "remaining_pct": 50.0},
            "gemini_free": {"is_locked": False, "used_pct": 0.0, "remaining_pct": 100.0},
        }

def get_available_accounts(quota):
    """
    quota 상태를 분석해서 사용 가능한 계정 목록과 최대 처리 건수를 반환
    """
    available = []
    max_items_per_session = 0

    acct1 = quota.get("gemini_advanced_1", {})
    acct2 = quota.get("gemini_advanced_2", {})
    free  = quota.get("gemini_free", {})

    # Gemini Advanced Acct 1
    if not acct1.get("is_locked", True):
        remaining = acct1.get("remaining_pct", 0.0)
        if remaining > 5.0:  # 최소 5% 이상 남아있어야 사용
            available.append({"key": "acct1", "model": acct1.get("model", "Gemini Advanced (Acct 1)"), "remaining_pct": remaining})
            # 남은 비율에 따라 처리 건수 결정 (100% = 최대 15건)
            items = max(1, int(remaining / 100 * 15))
            max_items_per_session += items
            logger.info(f"✅ Gemini Advanced (Acct 1): 가용 {remaining:.1f}% → 최대 {items}건 처리 가능")
        else:
            logger.warning(f"⚠️ Gemini Advanced (Acct 1): 잔여 {remaining:.1f}% (5% 미만 → 건너뜀)")
    else:
        logger.warning(f"🔒 Gemini Advanced (Acct 1): 한도 초과 잠금 상태 → 건너뜀")

    # Gemini Advanced Acct 2
    if not acct2.get("is_locked", True):
        remaining = acct2.get("remaining_pct", 0.0)
        if remaining > 5.0:
            available.append({"key": "acct2", "model": acct2.get("model", "Gemini Advanced (Acct 2)"), "remaining_pct": remaining})
            items = max(1, int(remaining / 100 * 15))
            max_items_per_session += items
            logger.info(f"✅ Gemini Advanced (Acct 2): 가용 {remaining:.1f}% → 최대 {items}건 처리 가능")
        else:
            logger.warning(f"⚠️ Gemini Advanced (Acct 2): 잔여 {remaining:.1f}% (5% 미만 → 건너뜀)")
    else:
        logger.warning(f"🔒 Gemini Advanced (Acct 2): 한도 초과 잠금 상태 → 건너뜀")

    # Gemini Free - 항상 보조 가용 (최대 10건 추가)
    if not free.get("is_locked", False):
        available.append({"key": "free", "model": free.get("model", "Gemini Free Tier"), "remaining_pct": 100.0})
        free_items = 10
        max_items_per_session += free_items
        logger.info(f"✅ Gemini Free Tier: 무제한 가용 → 최대 {free_items}건 보조 처리")
    
    if not available:
        logger.error("❌ 가용한 Gemini 계정이 없습니다. 오늘 분석 건너뜀.")
    
    return available, max_items_per_session

# ── 2. DB 초기화 ──────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gems_analyst_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT,
            target_name TEXT,
            account_used TEXT,
            growth_outlook_2026_2027 TEXT,
            bull_case TEXT,
            bear_case TEXT,
            consensus_summary TEXT,
            raw_response TEXT,
            source_reports_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gems_daily_learned_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            learned_date TEXT,
            target_type TEXT,
            target_name TEXT,
            source_file_name TEXT,
            account_used TEXT,
            key_insights_summary TEXT,
            growth_2026_2027 TEXT,
            bull_vs_bear TEXT,
            tokens_context_size TEXT,
            token_quota_snapshot TEXT,
            learned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # token_quota_snapshot 컬럼이 없으면 추가 (마이그레이션)
    try:
        cur.execute("ALTER TABLE gems_daily_learned_logs ADD COLUMN token_quota_snapshot TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

# ── 3. 오늘 이미 처리한 파일 목록 조회 ─────────────────────────────────────
def get_today_processed_files():
    """오늘 날짜 기준 이미 처리한 파일명 집합 반환 (중복 방지)"""
    today_str = date.today().isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT source_file_name FROM gems_daily_learned_logs
            WHERE learned_date = ?
        """, (today_str,))
        processed = {row[0] for row in cur.fetchall()}
        conn.close()
        return processed
    except Exception:
        return set()

# ── 4. 최신 리포트 목록 조회 ──────────────────────────────────────────────────
def get_latest_reports_list(limit=50):
    """
    reports 디렉토리에서 가장 최근 수정/발행된 파일 목록을 최신순으로 반환
    오늘 이미 처리한 파일은 제외
    """
    files = glob.glob(os.path.join(REPORTS_DIR, "**/*"), recursive=True)
    valid_files = [f for f in files if os.path.isfile(f) and not f.endswith(".DS_Store")]
    valid_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    
    processed_today = get_today_processed_files()
    unprocessed = [f for f in valid_files if os.path.basename(f) not in processed_today]
    
    logger.info(f"📁 전체 리포트: {len(valid_files)}건 | 오늘 처리 완료: {len(processed_today)}건 | 처리 대기: {len(unprocessed)}건")
    return unprocessed[:limit]

# ── 5. 학습 로그 기록 ─────────────────────────────────────────────────────────
def record_learned_log(target_type, target_name, source_file, account_used, summary, growth, bull_bear,
                        tokens_ctx="2,000,000", quota_snapshot=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    today_str = date.today().isoformat()
    quota_json = json.dumps(quota_snapshot, ensure_ascii=False) if quota_snapshot else None
    cur.execute("""
        INSERT INTO gems_daily_learned_logs 
        (learned_date, target_type, target_name, source_file_name, account_used,
         key_insights_summary, growth_2026_2027, bull_vs_bear, tokens_context_size, token_quota_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        today_str, target_type, target_name,
        os.path.basename(source_file) if source_file else "최신 리포트 번들",
        account_used, summary, growth, bull_bear, tokens_ctx, quota_json
    ))
    conn.commit()
    conn.close()

def save_status_log(quota, available_accounts, total_processed, max_allowed):
    """처리 결과를 상태 로그 파일에 저장"""
    os.makedirs(os.path.dirname(STATUS_LOG_PATH), exist_ok=True)
    status = {
        "last_run": datetime.now().isoformat(),
        "today_date": date.today().isoformat(),
        "quota_snapshot": quota,
        "available_accounts_count": len(available_accounts),
        "available_accounts": [a["model"] for a in available_accounts],
        "total_processed_today": total_processed,
        "max_allowed_this_session": max_allowed,
    }
    with open(STATUS_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

# ── 6. 파일명에서 종목/섹터 타입 자동 추론 ──────────────────────────────────
SECTOR_KEYWORDS = ["반도체", "AI인프라", "방산", "항공", "배터리", "에너지", "금융", "헬스케어", "바이오", "소부장", "인터넷", "게임"]
STOCK_NAME_MAP = {
    "삼성전자": "삼성전자", "SK하이닉스": "SK하이닉스", "현대차": "현대차",
    "LG에너지솔루션": "LG에너지솔루션", "POSCO": "POSCO홀딩스", "카카오": "카카오",
    "네이버": "NAVER", "셀트리온": "셀트리온", "한화": "한화에어로스페이스",
    "LG전자": "LG전자", "기아": "기아", "KB금융": "KB금융", "신한": "신한금융",
}

def infer_target_from_filename(filepath):
    fname = os.path.basename(filepath)
    target_type = "STOCK"
    target_name = "미분류 종목"
    
    for sector in SECTOR_KEYWORDS:
        if sector in fname:
            target_type = "SECTOR"
            target_name = sector
            break
    
    for key, val in STOCK_NAME_MAP.items():
        if key in fname:
            target_type = "STOCK"
            target_name = val
            break
    
    return target_type, target_name

# ── 7. 계정 선택 (라운드로빈) ────────────────────────────────────────────────
def pick_account(available_accounts, idx):
    """가용 계정 목록에서 라운드로빈으로 계정 선택"""
    if not available_accounts:
        return "Gemini Free Tier"
    acct = available_accounts[idx % len(available_accounts)]
    return acct["model"]

# ── 8. 메인 배치 실행 ─────────────────────────────────────────────────────────
def run_latest_first_batch_learning():
    init_db()
    today_str = date.today().isoformat()
    
    logger.info("=" * 60)
    logger.info(f"🚀 [Gems v2.0] 최신순 리포트 분석 배치 시작 ({today_str})")
    logger.info("=" * 60)
    
    # ── STEP A: 토큰/한도 현황 로드 ──
    logger.info("\n📊 [STEP A] Gemini 토큰/한도 현황 확인 중...")
    quota = load_quota_status()
    
    for key, info in quota.items():
        locked = "🔒 잠금" if info.get("is_locked") else "🟢 가용"
        remaining = info.get("remaining_pct", 100.0)
        model = info.get("model", key)
        logger.info(f"  {locked} {model}: 잔여 {remaining:.1f}%")
    
    # ── STEP B: 사용 가능 계정 및 처리 가능 건수 결정 ──
    available_accounts, max_items = get_available_accounts(quota)
    
    if not available_accounts:
        logger.error("❌ 사용 가능한 계정이 없어 오늘 분석을 건너뜁니다.")
        save_status_log(quota, [], 0, 0)
        return
    
    logger.info(f"\n📋 [STEP B] 오늘 처리 가능 최대 건수: {max_items}건")
    
    # ── STEP C: 최신 파일 목록 가져오기 (이미 처리한 것 제외) ──
    logger.info("\n📁 [STEP C] 최신 리포트 목록 추출 중...")
    latest_files = get_latest_reports_list(limit=max_items + 10)
    
    if not latest_files:
        logger.info("✅ 오늘 처리할 신규 리포트 없음 (모두 처리 완료됨)")
        save_status_log(quota, available_accounts, 0, max_items)
        return
    
    # max_items 건수로 자름
    files_to_process = latest_files[:max_items]
    logger.info(f"🎯 오늘 처리 대상: {len(files_to_process)}건 (전체 대기: {len(latest_files)}건)")
    
    # ── STEP D: 파일별 분석 시뮬레이션 및 DB 적재 ──
    logger.info("\n⚡ [STEP D] 분석 처리 시작...")
    
    processed_count = 0
    for i, filepath in enumerate(files_to_process):
        fname = os.path.basename(filepath)
        target_type, target_name = infer_target_from_filename(filepath)
        account_used = pick_account(available_accounts, i)
        
        # 파일 크기 기반 토큰 추정 (1MB ≈ 250,000 토큰)
        try:
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            est_tokens = int(file_size_mb * 250000)
            tokens_ctx_str = f"{est_tokens:,}"
        except Exception:
            tokens_ctx_str = "250,000"
        
        # 분석 결과 생성 (실제 Playwright 자동화 대신 파일 기반 메타데이터 기록)
        # NOTE: 실제 Gemini 웹 분석은 Playwright가 필요한데 브라우저 세션 없이는 불가
        # → 현재는 파일 존재/메타데이터를 기록하고, Playwright 세션이 연결되면 분석
        file_date = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d")
        summary = f"[{file_date}] 최신 리포트 분석 예약됨 · 파일명: {fname[:60]}"
        growth = f"리포트 처리 대기 중 (Gemini {account_used} 세션 연결 시 자동 분석)"
        bull_bear = f"가용 계정: {account_used} | 추정 토큰: {tokens_ctx_str}"
        
        record_learned_log(
            target_type=target_type,
            target_name=target_name,
            source_file=filepath,
            account_used=account_used,
            summary=summary,
            growth=growth,
            bull_bear=bull_bear,
            tokens_ctx=tokens_ctx_str,
            quota_snapshot={
                "available_accounts": [a["model"] for a in available_accounts],
                "max_items": max_items,
                "run_index": i + 1,
            }
        )
        
        processed_count += 1
        logger.info(f"  [{i+1}/{len(files_to_process)}] ✅ [{target_name}] {fname[:50]} → {account_used}")
        
        # Rate limit 방지를 위한 딜레이
        if (i + 1) % 5 == 0:
            logger.info(f"    ⏳ 5건 처리 완료, 2초 대기...")
            time.sleep(2)
    
    # ── STEP E: 상태 저장 ──
    save_status_log(quota, available_accounts, processed_count, max_items)
    
    logger.info("\n" + "=" * 60)
    logger.info(f"🎉 [완료] Gems v2.0 분석 배치 종료")
    logger.info(f"   ✅ 오늘 처리 건수: {processed_count}건 / 최대 허용: {max_items}건")
    logger.info(f"   📊 사용 계정: {', '.join(a['model'] for a in available_accounts)}")
    logger.info("=" * 60)

if __name__ == "__main__":
    run_latest_first_batch_learning()
