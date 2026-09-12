import os
import sys
import sqlite3
from pathlib import Path

# Add backend and backend/services to sys.path to allow imports
BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from services.rss_ingest import (
    is_same_topic_rule,
    publisher_score,
    has_any_keyword,
    NON_KAI_CONTEXT_KEYWORDS,
    LOW_VALUE_KEYWORDS,
    normalize_url
)

DB_PATH = BACKEND_ROOT.parent / "data" / "ceo_briefing.db"

def run_cleanup():
    if not DB_PATH.exists():
        print(f"Database not found at: {DB_PATH}")
        return

    print("Connecting to database...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Step 1: Remove Korea Accounting Institute (KAI) news & Multi-company 인사/동정 & Opinion/Infra noise
    print("\n--- Phase 1: Identifying & Removing Korea Accounting Institute, [인사], Opinion & Accident noise ---")
    rows = conn.execute(
        "SELECT id, title, summary, link, feed_type, article_category FROM feed_items"
    ).fetchall()
    
    purged_accounting_count = 0
    accounting_keywords = NON_KAI_CONTEXT_KEYWORDS + LOW_VALUE_KEYWORDS + [
        "회계", "ifrs", "국제회계", "감사원", "kssb", "issb", "공시 의무화",
        "중국이 버린", "gtx 철근", "보도육교 붕괴", "고가 붕괴", "보도 붕괴"
    ]

    for row in rows:
        title = row["title"]
        summary = row["summary"] or ""
        link = row["link"]
        combined_text = f"{title} {summary}".lower()
        title_stripped = title.strip()
        
        is_noise = False
        
        # 1. Check Accounting & Specific general noise keywords
        if has_any_keyword(combined_text, accounting_keywords):
            is_noise = True
            print(f"[PURGING NOISE - KEYWORD] Title: {title} (ID: {row['id']})")
            
        # 2. Check [인사] personnel announcements prefix or generic multi-announcements
        elif title_stripped.startswith(("[인사]", "[부음]", "(인사)", "(부음)", "◇")) or any(keyword in title_stripped for keyword in [" 종합 인사", "종합인사", "부고", "동정 "]):
            is_noise = True
            print(f"[PURGING NOISE - PERSONNEL] Title: {title} (ID: {row['id']})")
            
        if is_noise:
            # Delete from feed_items
            conn.execute("DELETE FROM feed_items WHERE id = ?", (row["id"],))
            # Insert into deleted_feed_items to blacklist from re-ingestion
            conn.execute(
                """
                INSERT OR IGNORE INTO deleted_feed_items(feed_type, link, title)
                VALUES (?, ?, ?)
                """,
                (row["feed_type"], link, title)
            )
            purged_accounting_count += 1
            continue

        # 3. [Re-classification] Auto-correct category mappings based on force rules
        title_lower = title.lower()
        title_temp = title_lower.replace("kaist", "").replace("카이스트", "")
        has_hanwha_brand = any(kw in title_lower for kw in ["한화", "한화시스템", "한화에어로", "한화오션"])
        has_lig_brand = any(kw in title_lower for kw in ["lig", "넥스원"])
        KAI_PRODUCTS = ["kf-21", "kf21", "fa-50", "fa50", "t-50", "t50", "수리온", "lah", "마린온", "상륙공격헬기", "소해헬기"]
        has_kai_brand_in_title = any(kw in title_temp for kw in ["kai", "한국항공우주", "항공우주산업"]) or any(kw in title_lower for kw in KAI_PRODUCTS)
        
        summary_lower = (summary or "").lower()
        summary_temp = summary_lower.replace("kaist", "").replace("카이스트", "")
        has_kai_brand_in_summary = any(kw in summary_temp for kw in ["kai", "한국항공우주", "항공우주산업"]) or any(kw in summary_lower for kw in KAI_PRODUCTS)
        
        has_kai_brand_anywhere = has_kai_brand_in_title or has_kai_brand_in_summary
        has_space_brand = any(kw in title_lower for kw in ["위성", "발사체", "누리호", "우주항공청", "kasa", "스페이스x", "우주산업", "우주개발", "우주탐사", "우주항공산업"])
        
        correct_category = None
        if has_space_brand:
            correct_category = "space"
        elif (has_hanwha_brand or has_lig_brand) and not has_kai_brand_in_title:
            if has_hanwha_brand:
                correct_category = "hanwha"
            elif has_lig_brand:
                correct_category = "lig"
        elif has_kai_brand_in_title:
            correct_category = "kai"
            
        # If categorized as 'kai' but does not actually contain KAI brand anywhere (false-positive due to KAIST/카이스트),
        # downgrade it to 'government' (if related to military/defense policy) or 'reference' (reference/collaboration).
        # Also, if the title contains a partner brand/keyword but does not contain KAI brand in the title, downgrade to 'reference'.
        # And if KAI is not in the title but general defense/military/event keywords are present, downgrade/reclassify to 'space' (항공/방산/우주).
        if row["article_category"] in ["kai", "space"]:
            PARTNER_KEYWORDS = ["협력사", "협력업체", "부품사", "제노코", "켄코아에어로스페이스", "켄코아", "하이즈항공", "아스트", "퍼스텍", "휴니드", "코츠테크놀로지", "이엠코리아", "타임기술", "데크항공", "율곡", "에이엔에이치스트럭쳐", "에이엔에이치", "anh"]
            has_partner_in_title = any(kw in title_lower for kw in PARTNER_KEYWORDS)
            if has_partner_in_title and not has_kai_brand_in_title:
                correct_category = "reference"
            elif row["article_category"] == "kai" and not has_kai_brand_in_title:
                has_defense = any(kw in title_lower for kw in ["방산", "방위산업", "국방", "무기", "군사", "대회", "전시회", "박람회", "세미나", "포럼"])
                if has_defense:
                    correct_category = "space"
                elif not has_kai_brand_anywhere:
                    has_gov = any(kw in title_lower for kw in ["국방", "방사청", "방위사업청", "정부", "대통령", "합참", "전력"])
                    correct_category = "government" if has_gov else "reference"
            elif row["article_category"] == "kai" and not has_kai_brand_anywhere:
                has_gov = any(kw in title_lower for kw in ["국방", "방사청", "방위사업청", "정부", "대통령", "합참", "전력"])
                correct_category = "government" if has_gov else "reference"
            
        if correct_category and row["article_category"] != correct_category:
            print(f"[RECLASSIFYING NEWS] Title: {title} | '{row['article_category']}' -> '{correct_category}'")
            conn.execute(
                "UPDATE feed_items SET article_category = ? WHERE id = ?",
                (correct_category, row["id"])
            )

    conn.commit()
    print(f"Phase 1 Complete: Purged/Corrected {purged_accounting_count} noise/accounting news items.")

    # Step 2: Global Deduplication
    print("\n--- Phase 2: Identifying & Removing duplicate articles ---")
    # Fetch remaining articles
    active_rows = conn.execute(
        """
        SELECT id, title, summary, link, source, article_published_at, article_publisher, feed_type, article_category, published, selected
        FROM feed_items
        ORDER BY COALESCE(article_published_at, published_at) DESC, id DESC
        """
    ).fetchall()
    
    active_items = [dict(row) for row in active_rows]
    unique_items = []
    duplicate_ids_to_remove = []
    
    print(f"Scanning {len(active_items)} active articles for duplicates...")

    for item in active_items:
        is_duplicate = False
        for existing in unique_items:
            # Prepare compact dicts for rule comparison
            item_comp = {
                "title": item["title"],
                "summary": item["summary"],
                "link": item["link"],
            }
            existing_comp = {
                "title": existing["title"],
                "summary": existing["summary"],
                "link": existing["link"],
            }
            if is_same_topic_rule(item_comp, existing_comp):
                is_duplicate = True
                # Choose preferred: Keep the one with higher publisher score or newer date
                incoming_score = publisher_score(str(item.get("article_publisher", "")), str(item.get("link", "")))
                existing_score = publisher_score(str(existing.get("article_publisher", "")), str(existing.get("link", "")))
                
                # If incoming is better, replace the one in unique_items, and mark existing for deletion
                if incoming_score > existing_score:
                    print(f"[REPLACING DUPLICATE] Replacing '{existing['title']}' ({existing['id']}) with better publisher '{item['title']}' ({item['id']})")
                    duplicate_ids_to_remove.append(existing["id"])
                    unique_items.remove(existing)
                    unique_items.append(item)
                else:
                    print(f"[SKIPPING DUPLICATE] Keeping existing '{existing['title']}' ({existing['id']}) and removing duplicate '{item['title']}' ({item['id']})")
                    duplicate_ids_to_remove.append(item["id"])
                break
        
        if not is_duplicate:
            unique_items.append(item)

    # Execute deletion of duplicates
    deleted_dup_count = 0
    for dup_id in duplicate_ids_to_remove:
        conn.execute("DELETE FROM feed_items WHERE id = ?", (dup_id,))
        deleted_dup_count += 1

    conn.commit()
    conn.close()
    
    print(f"Phase 2 Complete: Cleaned up {deleted_dup_count} duplicate items.")
    print(f"\nSuccessfully finished database purification! Total Cleaned: {purged_accounting_count + deleted_dup_count} items.")

if __name__ == "__main__":
    run_cleanup()
