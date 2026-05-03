#!/Applications/stock_dashboard/venv/bin/python
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/Applications/stock_dashboard")

from report_store import migrate_from_stock_db


def main() -> None:
    result = migrate_from_stock_db()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
