# 정리 계획서 — 2026-05-16

## 현황 요약

| 항목 | 수치 |
|------|------|
| 전체 파일 수 | 10,585개 (venv/git 제외) |
| 프로젝트 총 용량 | ~16 GB |
| 루트 Python 파일 | 77개 |
| DB 내 backup 테이블 | 12개 |

## 용량 상위 경로

| 경로 | 크기 | 비고 |
|------|------|------|
| reports/ | 7.1 G | PDF 보고서 — 삭제 금지 |
| stock.db | 3.0 G | 운영 DB — 삭제 금지 |
| hs_trade_lab/ | 1.7 G | 관세청 데이터 — 보존 |
| stock_corrupted_backup_20260506.db | 1.6 G | **아카이브 대상** |
| venv/ | 700 M | 활성 가상환경 — 삭제 금지 |
| frontend/ | 249 M | 빌드 포함 |
| venv_backup/ | 248 M | **아카이브 대상** |
| logs/ | 87 M | 오래된 로그 정리 대상 |
| stock.db-wal | 17 M | WAL 파일 (정상) |

---

## 위험도별 정리 대상

### ✅ 즉시 정리 가능 (저위험)

| 파일/경로 | 크기 | 이유 |
|-----------|------|------|
| `stock.db-wal.bak` | 4.4 M | WAL 백업, 현재 DB와 무관 |
| `stock.db-shm.bak` | 32 K | SHM 백업, 현재 DB와 무관 |
| `config.py.save` | ~1 K | 설정 임시 저장본 |
| `main.py.bak` | ~수백 K | 이전 버전 백업 |
| `.claude/settings.json.local.bak` | ~1 K | Claude 설정 백업 |
| `logs/dart_*.log` (30일+) | ~30 M | 오래된 수집 로그 |

### ⚠️ 승인 후 정리 (중위험)

| 파일/경로 | 크기 | 조건 |
|-----------|------|------|
| `stock_corrupted_backup_20260506.db` | 1.6 G | 2026-05-06 백업, 10일 경과. `archives/`로 이동 후 삭제 |
| `venv_backup/` | 248 M | `pip freeze > requirements_backup.txt` 후 삭제 |
| DB backup 테이블 8개 (4월~5월15일) | ~수십 MB | export 후 drop |
| `signal_logic_v1_backup.py` | ~수십 K | 코드 이력 — git에 있으면 삭제 가능 |
| `patch_data_v2.py`, `patch_data_v3.py` | 소 | 일회성 패치 스크립트 |

### 🔒 보존 권장 (고위험)

| 파일/경로 | 이유 |
|-----------|------|
| `stock.db` | 운영 DB |
| `stock.db-wal` | 미플러시 트랜잭션 가능 |
| `reports/` | 원본 PDF 보고서 |
| `venv/` | 활성 가상환경 |
| `financial_data_backup_comprehensive_sync_20260516` | 오늘 작업 백업 |
| `financial_data_backup_fnguide_sync_20260516` | 오늘 작업 백업 |
| `.claude/worktrees/exciting-pare-a84afc` | 현재 워크트리 |

---

## 아카이브 디렉토리 구조

```
archives/
└── 2026-05-16/
    ├── bak_files/          # .bak/.save 파일
    ├── corrupted_db/       # stock_corrupted_backup_20260506.db
    ├── venv_backup/        # venv_backup/ 디렉토리
    └── old_logs/           # 30일+ 로그
```

## 실행 순서

1. `scripts/ops/cleanup_dry_run.sh` — 대상 파일 목록만 출력
2. 출력 검토 후 승인
3. `scripts/ops/archive_candidates.sh` — archives/ 이동
4. DB backup 테이블 export (scripts/ops/export_db_backups.sh)
5. 사용자 승인 후 실제 삭제

## 예상 확보 공간

| 항목 | 크기 |
|------|------|
| stock_corrupted_backup | 1.6 G |
| venv_backup | 248 M |
| 오래된 로그 | ~30 M |
| .bak/.save 파일 | ~5 M |
| **합계** | **~1.9 G** |
