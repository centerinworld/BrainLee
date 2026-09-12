import urllib.request
import json
payload = json.dumps({"category": "hanwha"}).encode('utf-8')
req = urllib.request.Request("http://127.0.0.1:8011/feeds/company/items/123/category?role=admin", data=payload, method="PUT", headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode('utf-8'))
except Exception as e:
    print(e.read().decode('utf-8') if hasattr(e, 'read') else str(e))
