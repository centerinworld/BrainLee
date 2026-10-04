# .env 관리 (2026-10-03 통합)

## 원칙
- **기준 파일 하나**: `/Volumes/Realtek_NVME/stock_dashboard/runtime/.env` (권한 600, git 제외). 키 추가·수정은 **여기서만** 한다.
- 다른 위치는 직접 고치지 않는다 — 자동으로 덮인다.

| 위치 | 방식 |
|---|---|
| `stock_dashboard/.env`(루트), `AI System/antigravity_workspace/.env` | 기준 파일로 가는 링크(symlink) |
| `.claude/worktrees/*/.env`, `runtime/.claude/worktrees/*/.env`, `~/.codex/worktrees/*/runtime/.env` | 기준 파일로 가는 링크 |
| `AI System/codex/ceo-briefing-platform/.env` | **자동 생성** = 기준 + `runtime/env_overrides/ceo-briefing.env`(예외: `DEEPSEEK_MODEL=deepseek-flash`) |
| `hs_trade_lab/.env`, `.env.gemini_web`, `~/.env`, `~/BrainLee_tmp/.env`, `AI System/_archive/...` | 용도가 달라 손대지 않음 |

- 동기화: `scripts/ops/sync_env.py`. launchd `com.stock-dashboard.env-sync`(WatchPaths)가 기준 파일·예외 파일이 바뀌면 자동 실행(로그 `/tmp/launchd_env_sync.log`(launchd는 외장 볼륨에 로그를 직접 못 써 /tmp 사용)). 새 worktree가 생기면 수동으로 한 번 실행.
- 값이 바뀐 뒤에는 그 값을 쓰는 서버를 재시작해야 반영된다: 주식 백엔드 `scripts/safe_restart_backend.sh`, ceo `launchctl kickstart -k gui/$(id -u)/com.ceo-briefing.backend`.

## 2026-10-03 정리 내역
- OpenDART 4번 키: 루트 .env에 `KRX_OPENAPI_KEY` 이름(주석 'KRX Open API #4')으로 들어가 있던 것을 `DART_API_KEY4`로 정정해 기준 파일에 추가. `config.DART_API_KEYS`가 4개 인식.
- 'KRX Open API #2·#3' 이름 아래 값은 실제로 DART 2·3번 키와 같은 값이었다 → 제거(진짜 KRX 키 1개만 남김). 그 전에는 마지막 줄이 이겨 `KRX_OPENAPI_KEY`에 DART 키가 들어가 있었다.
- `TELEGRAM_BOT_TOKEN` 중복 제거(새 토큰 유지). 루트·antigravity·ceo는 옛 토큰이었는데 이제 새 토큰을 공유.
- 다른 프로젝트에만 있던 키 8개를 기준에 추가: OpenAI_GPT_KEY, NAMU_STOCK_API_KEY/SECRET, ANTIGRAVITY_BRIDGE_API_KEY, STOCK_POSTGRES_URL, GOAL_INTAKE_BOT_TOKEN, CEO_SESSION_SECRET, DEEPSEEK_MONTHLY_BUDGET_KRW.
- `STOCKEASY_LIVE_AUTOTRADE`: 사용처는 주식 runtime뿐 → 운영 값 `false`(실거래 자동매매 꺼짐) 유지. 다른 사본의 `true`는 쓰이지 않던 값.
- 백업: `backups/env_20261003_151150/`(통합 전 4개), `backups/env_sync_20261003_151436/`(링크로 바꾼 사본 36개, `runtime/.env.save`, ceo vim 임시 파일 3개). 모두 권한 600/700.

## DART 키 사용 순서 (일괄 수집)
KEY1 → KEY3 → KEY4 → KEY2. 한 키가 일일 한도(020)에 걸리면 다음 키로 이어 받는다. KEY2는 운영 공시 수집용이라 마지막이며 일괄 작업은 하루 `KEY2_BULK_DAILY_CAP`(기본 10,000)건까지(`scripts/review/dart_keys.py`, 카운터 `data/dart_key2_bulk_usage.json`). 동시 스레드는 2개(IP 차단은 초당 ~14건에서 발생했다).
