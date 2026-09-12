import sqlite3
import urllib.request
import json

DB_PATH = r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db'

def get_recent_items():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    rows = cursor.execute("SELECT id, title, article_category, published FROM feed_items ORDER BY id DESC LIMIT 3").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def call_api(endpoint, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f"http://127.0.0.1:8011{endpoint}?role=admin",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        return {"error": e.read().decode('utf-8') if hasattr(e, 'read') else str(e)}

def main():
    items = get_recent_items()
    if not items:
        print("No items in feed_items to test with.")
        return
    
    item_ids = [item['id'] for item in items]
    print("Initial items:")
    for item in items:
        print(f"ID: {item['id']}, Title: {repr(item['title'])}, Cat: {item['article_category']}, Published: {item['published']}")
    
    # Test 1: Batch Move to 'hanwha'
    print("\n--- Testing Batch Move to 'hanwha' ---")
    move_payload = {"item_ids": item_ids, "category": "hanwha"}
    res = call_api("/feeds/company/batch-move", move_payload)
    print("Response:", res)
    
    # Verify move
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    moved_items = cursor.execute("SELECT id, article_category, published FROM feed_items WHERE id IN (?, ?, ?)", item_ids).fetchall()
    conn.close()
    print("After move:")
    for item in moved_items:
        print(f"ID: {item['id']}, Cat: {item['article_category']}, Published: {item['published']}")
        
    # Test 2: Batch Publish
    print("\n--- Testing Batch Publish ---")
    publish_payload = {"item_ids": item_ids}
    res = call_api("/feeds/company/batch-publish", publish_payload)
    print("Response:", res)
    
    # Verify publish
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    published_items = cursor.execute("SELECT id, article_category, published FROM feed_items WHERE id IN (?, ?, ?)", item_ids).fetchall()
    conn.close()
    print("After publish:")
    for item in published_items:
        print(f"ID: {item['id']}, Cat: {item['article_category']}, Published: {item['published']}")
        
    # Test 3: Batch Delete
    print("\n--- Testing Batch Delete ---")
    delete_payload = {"item_ids": item_ids}
    res = call_api("/feeds/company/batch-delete", delete_payload)
    print("Response:", res)
    
    # Verify delete
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    deleted_items = cursor.execute("SELECT id FROM feed_items WHERE id IN (?, ?, ?)", item_ids).fetchall()
    conn.close()
    print("After delete (should be empty):", [dict(row) for row in deleted_items])

if __name__ == "__main__":
    main()
