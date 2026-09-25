# 라이브러리 인벤토리 (2026-09-25, Claude)

이번 세션에서 설치·변경한 라이브러리와 환경, 라이선스, 취약점 점검 결과입니다. **라이선스는 패키지 메타데이터 기준이므로 도입 전 각 저장소에서 재확인**하십시오(vectorbt는 메타데이터에 라이선스가 비어 있음 — 오픈소스판은 Apache-2.0 + Commons Clause(재판매 금지)로 알려져 있어 사내 사용은 무방하나 서비스로 제공하면 안 됨, 확인 필요).

## 1. 환경

| 환경 | 경로 | Python | 용도 |
|---|---|---|---|
| 운영(전환됨) | `runtime/venv → .venvs/py312` | 3.12.14 | 서버·스케줄러·스크립트 (numpy 2.2.6, pandas 2.3.3, pykrx 1.2.9) |
| 운영 롤백 | `runtime/.venvs/py311` | 3.11.15 | numpy 1.26.4, pykrx 1.2.4 |
| 연구 | `stock_dashboard/research_venv` | 3.11.15 | numpy 2.4.6 등 아래 표. DB 직접 접속 없이 parquet만 읽음 |
| CEO 플랫폼 | `codex/ceo-briefing-platform/backend/.venv312` | 3.12.14 | 8011 서버, 49패키지(+psutil 7.2.2 추가, 기존 500 원인) |
| CEO 롤백 | `.../backend/.venv311` | 3.11 | |

## 2. 연구 venv 핵심 라이브러리

| 패키지 | 버전 | 라이선스(메타데이터) | 용도 |
|---|---|---|---|
| quantstats | 0.0.81 | Apache-2.0 | 성과 리포트(Sharpe/Sortino/MDD/월별/벤치마크 대비) |
| alphalens-reloaded | 0.4.6 | Apache License | 팩터 IC·분위 수익 검증 |
| vectorbt | 1.0.0 | ? | 규칙·파라미터 대량 탐색 |
| pyportfolioopt | 1.6.0 | MIT License | 비중 최적화(HRP/최소분산) |
| empyrical-reloaded | 0.5.12 | Apache License | quantstats/alphalens 지표 계산 의존 |
| cvxpy | 1.9.3 | Apache-2.0 | PyPortfolioOpt 최적화 솔버 |
| numba | 0.67.0 | BSD | vectorbt 가속(0.67.0; 문서 권장 pandas-ta용 0.61.2와 다름) |
| scipy | 1.17.1 | BSD License | 수치 계산 |
| scikit-learn | 1.9.1 | BSD-3-Clause | ML(연구용) |
| statsmodels | 0.15.0 | BSD-3-Clause | 통계 |
| plotly | 5.24.1 | MIT | vectorbt 시각화 — **5.x 고정 필요**(7.x는 vectorbt import 실패) |
| matplotlib | 3.11.2 | Python Software Foundation License | 리포트 그림 |
| seaborn | 0.13.2 | BSD License | 리포트 그림 |
| pyarrow | 25.0.1 | Apache-2.0 | parquet 입출력 |
| yfinance | 1.7.0 | Apache-2.0 | 연구 venv 의존(가격 수집엔 미사용) |

**로컬 패치**: `alphalens/utils.py`의 `df.index.levels[0].freq = freq`를 try/except로 감쌈(월말 같은 희소 날짜는 BusinessDay freq 설정 불가). 재설치하면 사라지므로 재적용 필요.

## 3. 운영 venv 변경점 (3.11 freeze 대비)

- numpy 1.26.4 → **2.2.6**, pykrx 1.2.4 → **1.2.9**. 나머지 패키지는 동일 버전.
- OpenDartReader 0.2.3: PyPI 배포가 Python ≥3.13을 요구해 pip 설치 불가 → 3.11 venv의 순수 파이썬 패키지 디렉터리를 복사.
- pykrx 1.2.9: 종목 OHLCV 동작(1.2.4와 동일값), ETF·전종목 일괄 조회는 두 버전 모두 불가, import 시 `KRX_ID/KRX_PW` 안내 출력.
- 라이선스 검토 필요(메타데이터 기준): psycopg/psycopg-binary(LGPL-3.0 — 동적 사용 통상 무방), frozendict(LGPL v3), google-crc32c·peewee(메타데이터 없음).

## 4. 알려진 취약점 (pip-audit, PyPI 권고 DB 기준, 2026-09-25)

**운영 py312** — 14개 패키지
- `aiohttp==3.13.4` → 수정 버전 ≥ 3.14.3 (권고 14건)
- `anthropic==0.86.0` → 수정 버전 ≥ 0.87.0 (권고 2건)
- `anyio==4.12.1` → 수정 버전 ≥ 4.14.2 (권고 2건)
- `click==8.3.1` → 수정 버전 ≥ 8.3.3 (권고 1건)
- `cryptography==48.0.0` → 수정 버전 ≥ 50.0.0 (권고 4건)
- `curl-cffi==0.13.0` → 수정 버전 ≥ 0.15.0 (권고 1건)
- `idna==3.11` → 수정 버전 ≥ 3.15 (권고 1건)
- `lxml==6.0.2` → 수정 버전 ≥ 6.1.0 (권고 1건)
- `pyasn1==0.6.3` → 수정 버전 ≥ 0.6.4 (권고 3건)
- `python-multipart==0.0.22` → 수정 버전 ≥ 0.0.31 (권고 5건)
- `requests==2.32.5` → 수정 버전 ≥ 2.33.0 (권고 1건)
- `soupsieve==2.8.3` → 수정 버전 ≥ 2.9.0 (권고 4건)
- `starlette==0.52.1` → 수정 버전 ≥ 1.3.1 (권고 5건)
- `urllib3==2.6.3` → 수정 버전 ≥ 2.7.0 (권고 2건)

**운영 py311(롤백)** — 16개 패키지
- `aiohttp==3.13.4` → 수정 버전 ≥ 3.14.3 (권고 14건)
- `anthropic==0.86.0` → 수정 버전 ≥ 0.87.0 (권고 2건)
- `anyio==4.12.1` → 수정 버전 ≥ 4.14.2 (권고 2건)
- `click==8.3.1` → 수정 버전 ≥ 8.3.3 (권고 1건)
- `cryptography==48.0.0` → 수정 버전 ≥ 50.0.0 (권고 4건)
- `curl-cffi==0.13.0` → 수정 버전 ≥ 0.15.0 (권고 1건)
- `idna==3.11` → 수정 버전 ≥ 3.15 (권고 1건)
- `lxml==6.0.2` → 수정 버전 ≥ 6.1.0 (권고 1건)
- `pip==26.1.1` → 수정 버전 ≥ 26.2.0 (권고 2건)
- `pyasn1==0.6.3` → 수정 버전 ≥ 0.6.4 (권고 3건)
- `python-multipart==0.0.22` → 수정 버전 ≥ 0.0.31 (권고 5건)
- `requests==2.32.5` → 수정 버전 ≥ 2.33.0 (권고 1건)
- `setuptools==82.0.0` → 수정 버전 ≥ 83.0.0 (권고 1건)
- `soupsieve==2.8.3` → 수정 버전 ≥ 2.9.0 (권고 4건)
- `starlette==0.52.1` → 수정 버전 ≥ 1.3.1 (권고 5건)
- `urllib3==2.6.3` → 수정 버전 ≥ 2.7.0 (권고 2건)

**연구 venv** — 1개 패키지
- `setuptools==82.0.0` → 수정 버전 ≥ 83.0.0 (권고 1건)

**CEO .venv312** — 1개 패키지
- `starlette==0.47.3` → 수정 버전 ≥ 1.3.1 (권고 6건)

**조치 방침**: 운영 패키지 업그레이드는 서비스 영향이 있어 이번에 적용하지 않음. 계획은 hermes.md "추가 계획" 참조(마이너 업그레이드 묶음 → 테스트 → 전환, starlette 1.x·cryptography 50은 FastAPI 호환 확인 후 별도).
