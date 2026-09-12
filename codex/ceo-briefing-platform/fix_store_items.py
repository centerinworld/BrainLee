import re

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "r", encoding="utf-8") as f:
    content = f.read()

store_items_replacement = """def store_items(
    conn: sqlite3.Connection,
    feed_type: str,
    source_name: str,
    items: List[Dict[str, Any]],
) -> tuple[int, List[Dict[str, str]]]:
    ensure_topic_memory_table(conn)
    ensure_deleted_items_table(conn)
    imported = 0
    stored_items: List[Dict[str, str]] = []
    recent_rows = conn.execute(
        \"\"\"
        SELECT id, title, summary, link, article_published_at, article_publisher, article_category, category_manual, selected, published
        FROM feed_items
        WHERE feed_type = ?
        ORDER BY COALESCE(article_published_at, published_at) DESC, id DESC
        LIMIT 500
        \"\"\",
        (feed_type,),
    ).fetchall()
    recent_items: List[Dict[str, Any]] = [dict(row) for row in recent_rows]

    # Prefer major publishers first when same-topic duplicates are imported in one run.
    ordered_items = sorted(
        items,
        key=lambda it: (
            publisher_score(str(it.get("publisher", "")), str(it.get("link", ""))),
            parse_iso_datetime(str(it.get("published_at", ""))) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    api_key = get_setting(conn, "openai_api_key")
    dedupe_model = get_setting(conn, "classification_model") or "gpt-4o-mini"
    ai_dedupe_calls = 0

    for index, item in enumerate(ordered_items, start=1):
        if is_deleted_item(conn, feed_type, str(item.get("link", ""))):
            continue
        signature = build_topic_signature(item)
        preferred_from_memory = get_memory_preferred_publisher(conn, signature)
        if preferred_from_memory:
            incoming_host = normalize_host(item.get("publisher", "") or publisher_from_link(str(item.get("link", ""))))
            if incoming_host and incoming_host != normalize_host(preferred_from_memory):
                if publisher_score(incoming_host, str(item.get("link", ""))) < publisher_score(preferred_from_memory, ""):
                    continue

        duplicate_existing: Dict[str, Any] | None = None
        for existing in recent_items:
            existing_for_compare = {
                "title": existing.get("title", ""),
                "summary": existing.get("summary", ""),
                "link": existing.get("link", ""),
                "article_published_at": existing.get("article_published_at", ""),
                "article_publisher": existing.get("article_publisher", ""),
            }
            if is_same_topic_rule(item, existing_for_compare):
                duplicate_existing = existing
                break
            if api_key and ai_dedupe_calls < 30:
                similarity = SequenceMatcher(
                    None,
                    normalize_topic_text(str(item.get("title", ""))),
                    normalize_topic_text(str(existing.get("title", ""))),
                ).ratio()
                tokens_item = set(extract_topic_tokens(str(item.get("title", "")), ""))
                tokens_existing = set(extract_topic_tokens(str(existing.get("title", "")), ""))
                overlap = len(tokens_item.intersection(tokens_existing))
                
                if (0.3 <= similarity < 0.9) or (overlap >= 1 and similarity < 0.9):
                    try:
                        ai_dedupe_calls += 1
                        same = is_same_topic_with_openai(
                            api_key,
                            dedupe_model,
                            item,
                            {"title": existing.get("title", ""), "summary": existing.get("summary", "")},
                        )
                    except Exception:
                        same = False
                    if same:
                        duplicate_existing = existing
                        break

        if duplicate_existing:
            preferred = choose_preferred_item(item, duplicate_existing)
            preferred_host = (
                normalize_host(item.get("publisher", "") or publisher_from_link(str(item.get("link", ""))))
                if preferred == "incoming"
                else normalize_host(str(duplicate_existing.get("article_publisher", "")))
            )
            update_topic_memory(conn, signature, str(item.get("title", "")), preferred_host)
            if preferred == "incoming":
                conn.execute(
                    \"\"\"
                    UPDATE feed_items
                    SET title = ?, summary = ?, link = ?, source = ?, article_published_at = ?, article_publisher = ?
                    WHERE id = ?
                    \"\"\",
                    (
                        item.get("title", ""),
                        item.get("summary", ""),
                        item.get("link", ""),
                        source_name,
                        item.get("published_at") or None,
                        item.get("publisher") or source_name,
                        duplicate_existing["id"],
                    ),
                )
                recent_items = [dict(r) if r["id"] != duplicate_existing["id"] else {**dict(r), "title": item.get("title", ""), "summary": item.get("summary", ""), "link": item.get("link", ""), "article_published_at": item.get("published_at"), "article_publisher": item.get("publisher") or source_name} for r in recent_items]
            continue

        item_id = normalize_id(feed_type, item["link"], index)
        existing = conn.execute(
            "SELECT article_category, category_manual, selected, published FROM feed_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        
        is_clear = False
        is_breaking = False
        if existing and int(existing[1] or 0) == 1 and existing[0] in ALLOWED_CATEGORIES:
            article_category = existing[0]
            is_clear = True
        else:
            article_category, is_clear, is_breaking = resolve_article_category_and_clarity(conn, feed_type, item)
            
        if is_breaking and not existing:
            send_telegram_alert(conn, item)

        if existing:
            selected_value = int(existing[2] or 0)
            published_value = int(existing[3] or 0)
            manual_value = int(existing[1] or 0)
        else:
            published_value = 1 if is_clear else 0
            selected_value = 1 if is_clear else 0
            manual_value = 0
        conn.execute(
            \"\"\"
            INSERT OR REPLACE INTO feed_items(
                id, feed_type, title, summary, link, source, article_published_at, article_publisher, article_category, category_manual, selected, published
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            \"\"\",
            (
                item_id,
                feed_type,
                item.get("title", ""),
                item.get("summary", ""),
                item.get("link", ""),
                source_name,
                item.get("published_at") or None,
                item.get("publisher") or source_name,
                article_category,
                manual_value,
                selected_value,
                published_value,
            ),
        )
        if not existing:
            imported += 1
            stored_items.append({
                "id": item_id,
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "category": article_category,
            })
    return imported, stored_items
"""

pattern = re.compile(r"def store_items\(conn: sqlite3\.Connection, feed_type: str, items: List\[Dict\[str, Any\]\]\) -> None:.*?return imported, stored_items\n", re.DOTALL)
new_content = pattern.sub(store_items_replacement, content)

# Fallback pattern if the previous one doesn't match
pattern2 = re.compile(r"def store_items\(conn: sqlite3\.Connection, feed_type: str, items: List\[Dict\[str, Any\]\]\) -> None:.*?            \}\)\n    return imported, stored_items\n", re.DOTALL)
new_content = pattern2.sub(store_items_replacement, new_content)

# Third fallback
pattern3 = re.compile(r"def store_items\(conn: sqlite3\.Connection, feed_type: str, items: List\[Dict\[str, Any\]\]\) -> None:.*?            \)\n        if not existing:\n            imported \+= 1\n            stored_items\.append\(\{\n                \"id\": item_id,\n                \"title\": item\.get\(\"title\", \"\"\),\n                \"link\": item\.get\(\"link\", \"\"\),\n                \"category\": article_category,\n            \}\)\n    return imported, stored_items", re.DOTALL)
new_content = pattern3.sub(store_items_replacement, new_content)


with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "w", encoding="utf-8") as f:
    f.write(new_content)
print("done")
