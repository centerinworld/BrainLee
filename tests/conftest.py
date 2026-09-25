"""테스트 수집 순서에 따른 `scheduler` 모듈 이름 충돌 봉인 (2026-09-25 신설).

배경(실측)
----------
`scheduler` 라는 이름의 모듈이 트리에 둘 있다:

    runtime/scheduler.py            ← 수집 스케줄러(CollectionScheduler). 테스트 대상.
    runtime/ETF_check/scheduler.py  ← ETF_check 전용 스케줄러(별개 모듈).

그리고 다음 두 곳이 **import 시점에** `ETF_check` 를 `sys.path` 맨 앞에 끼워 넣는다:

    main.py:185                                  → `import main` 하는 테스트(test_closing_index_branch_contracts 등)
    tests/test_etf_*.py 11개 파일 (sys.path.insert)

pytest 는 테스트 모듈을 수집하며 import 하고, 그 순서는 파일시스템 directory-entry 순서에
의존한다(알파벳 정렬이 아니다). 그래서 위 오염원이 `scheduler` 를 쓰는 테스트보다 먼저
수집되면 `import scheduler` 가 **ETF_check/scheduler.py** 를 집고, 그 뒤 4개 파일이 통째로

    AttributeError: module 'scheduler' has no attribute 'CollectionScheduler'
    ImportError: cannot import name 'CollectionScheduler' from 'scheduler'

로 collection error 가 된다. 실제로 새 테스트 파일 하나(2026-09-25 `test_etf_ledger_contracts.py`)
를 추가했더니 순서가 바뀌어 `test_kiwoom_realtime_silent_failure.py`·`test_kiwoom_large_trade_silent_failure.py`·
`test_sector_index_rebuild_contracts.py` 가 동시에 깨졌다.

conftest 는 어떤 테스트 모듈보다 먼저 import 되므로 여기서 런타임 루트를 최우선으로 올리고
런타임 `scheduler` 를 `sys.modules` 에 **선점(pre-cache)** 한다 → 이후 ETF_check 가 경로에
끼어들어도 `import scheduler` 는 항상 같은 객체를 돌려준다.

참고: 근본 원인은 프로덕션 코드의 import 부작용(`main.py:185` 의 sys.path 변조)이다.
그쪽 정리는 별도 승인 사항이라 여기서는 테스트 수집만 결정적으로 만든다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scheduler  # noqa: E402  (선점 캐시 — 수집 순서와 무관하게 런타임 모듈 고정)

_resolved = Path(scheduler.__file__).resolve()
assert _resolved == (ROOT / "scheduler.py"), f"conftest 가 남의 scheduler 를 집었다: {_resolved}"
