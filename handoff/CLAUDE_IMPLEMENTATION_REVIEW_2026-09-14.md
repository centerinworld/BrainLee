# Claude 구현 검수 요청 — 2026-09-14 (Codex 재검토용 핸드오프)

작성: Claude. 소유자 지시("오늘 수정한것을 코덱스가 재검토하게 해 핸드오프로 남겨줘")에 따라 오늘(9/13 밤 ~ 9/14) 변경분을 정리한다. 운영 서비스는 각 커밋 후 재시작·실제 API 호출로 검증했다(아래 "검증 방법" 참고). 다만 재시작 자체가 운영 상태를 바꾸는 행위이므로, Codex는 재검토 시 현재 살아있는 프로세스가 이 커밋들을 반영한 최신 코드로 떠 있는지 먼저 확인할 것.

**중요 - 텔레그램 채널 구분(소유자 지적)**: "KAI Info Room"(기존 `TELEGRAM_BOT_TOKEN`/`telegram_bot_token`, ceo_briefing.db의 app_settings와 antigravity_workspace/.env가 실제로 같은 토큰 - 뉴스/브리핑 전용)과 작업 완료 알림 채널(`GOAL_INTAKE_BOT_TOKEN`, `@K_Agentic_AI_bot`, antigravity_workspace/.env)은 서로 다른 봇이다. 처음에 실수로 작업 완료 요약을 KAI Info Room으로 보냈다가 소유자 지적 후 정정했다 - 앞으로 시스템 작업/완료 보고는 반드시 `GOAL_INTAKE_BOT_TOKEN` 쪽으로 보낼 것.

**갱신(같은 날 저녁) - 실제 코드 수정 기능 완성됨**: 위 문단(git worktree 기반 자율 workspace-write 시도가 Claude Code 자체 안전 분류기에 "Create Unsafe Agents"로 차단된 것)은 그 접근 방식에만 해당하는 얘기였다. Codex/Claude가 **근본적으로 다른, 더 안전한 설계**로 다시 만들었고 이번엔 차단되지 않았다: 모델은 실행 가능한 명령이 아니라 **파일 전체 내용만 JSON으로 반환**하고, 신뢰된 컨트롤러(우리 코드)가 diff 생성·적용을 전담한다 - "자율 에이전트가 알아서 명령을 실행"하는 패턴 자체가 없다. `services/agentic_apply_worker.py`는 완전히 재작성됐다(구 `apply_change_in_isolated_worktree()`는 이제 명시적으로 RuntimeError를 던지며 `stage_patch()` 사용을 안내). 2단계 승인 흐름(`services/approved_code_jobs.py` + `approved_code_routes.py` + `frontend/agentic-code.html`)이 완성돼 커밋됐다(`052e676`) - 상세는 아래 "2. 실제 코드 수정 기능" 참고.

## 1. 오늘 커밋 목록 (시간순)

| 커밋 | 요지 |
|---|---|
| `6e0d20f` | strict_agi_orchestrator `_gemini()`가 API 키·모델을 서브프로세스에 안 넘겨 gemini CLI가 익명 무료 등급(gemini-3.6-flash, 하루 20회)으로 떨어지던 결함 수정 |
| `7c00828` | 3단계 DeepSeek 역할을 Gemini가 대체하는 경로 최초 구현(소유자 지시: "DeepSeek는 중요한 AI 아니니 Gemini가 대체") |
| `335e5ab` | 위 대체가 사전 probe(키 존재 여부만 확인) 기반이라 실제로 발동 안 하던 결함 재작업 → 실제 호출 실패 시점에 대체하도록 변경. DeepSeek가 추론 토큰으로 예산을 다 써 빈 응답을 내던 결함도 수정(max_tokens 1200→16000, 빈 응답이면 명시적 실패) |
| `24c4d80` | (D01 후속) `antigravity_workspace/execution/codex_sdk.py` — `Thread.run()`에도 model 재전달, `resolved_model`을 조용한 None 대신 "SDK가 안 알려줌"으로 명시 |
| `a14b746` | (D03) `_retry()`가 상대시간만 파싱하고 나머지는 전부 "현재+5시간"을 실제 리셋 시각처럼 표시하던 결함 — 절대시각("resets 9:40am") 파싱 추가, 미확인 시 30분으로 단축하고 note에 "추정" 명시 |
| `27bcbe5` | CEO NotebookLM 리포트의 "세계 경제"/"AI" 섹션이 실제로는 KAI 전용 국내 뉴스 재분류였던 문제 — 검증된 실제 글로벌 RSS 7개 신규 연결(별도 경로, 기존 회사뉴스 파이프라인 무변경) |

D01~D06 항목 번호는 `handoff/AGENTIC_RUNTIME_DIAGNOSIS_SOL_ONLY_2026-09-14.md`(다른 세션 작성)를 그대로 참조했다. D02(Gemini 결제 경로), D05(NEEDS_RECONCILIATION 자동 조정), D07(실제 worker)은 아래 "미완료"에 남아 있다.

## 2. 소유자가 오늘 직접 지적한 사항과 처리 결과

- **"과업 진행 이력에 변경이 없는데 정상 작동 맞아?"** → 실제로는 정상 진행 중이었다(운영 과업 하나가 WAITING_QUOTA→DRAFT_READY까지 실제로 도달, 다른 과업은 라이브 서버가 스스로 복구). 다만 **"뭘 고쳤는지 안 보이는" 지적은 정당하다** — 5단계 파이프라인은 아직 텍스트 계획/검토만 만들고 실제 코드를 고치지 않는다(아래 "미완료 1" 참고). 소유자에게 이 사실을 그대로 전달했다.
- **"Gemini 구독형인데 왜 무료 API를 쓰나?"** → 직접 확인한 결과 이 시스템 전체(오늘 고친 strict 경로 + 기존 `gemini_key_pool.py`)가 **처음부터 Gemini API 무료 등급으로 설계돼 있었다.** 근거: (1) 실제 429 오류 메시지의 GCP 지표명이 `generativelanguage.googleapis.com/generate_content_free_tier_requests`, (2) `stock_dashboard/gemini_key_pool.py` 자체가 "15 RPM/1,500 RPD"를 "완전 무료"·"무료 할당량"이라고 스스로 명시. 소유자의 개인 유료 구독(Gemini Advanced 등, Google One 계열로 추정)은 Gemini **API** 과금과 별개다 — Google Cloud 프로젝트에 결제 계정을 연결해야 API 쿼터가 올라간다. 이건 코드로 고칠 수 있는 문제가 아니라 Google Cloud Console에서 소유자가 직접 확인/설정해야 하는 부분이라고 안내했다.
- **"RSS는 회사 뉴스 전용 엔진, 넓히면 텔레그램 발송이 엉망돼"** → 코드로 직접 확인: `services/rss_ingest.py`의 `send_telegram_alert()`는 KAI 전용 `store_items()` 내부 루프에서만 호출되고(신규 글로벌 경로는 이 함수를 아예 거치지 않음), `send_telegram_briefing()`은 `WHERE published = 1`만 조회하는데 신규 `global_intelligence` 행은 `published=0`으로 저장돼 구조적으로 제외된다. 실제로 텔레그램 발송 경로에 영향이 없음을 코드 레벨로 확인 후 답변했다 — 재검토 시 이 두 지점(`services/rss_ingest.py:2814` 근방, `main.py:718` 근방)을 다시 확인해 주기 바란다.
- **"완료된 걸 텔레그램으로 보내줘"** → 기존 CEO 봇(`app_settings.telegram_bot_token/chat_id`, 매일 브리핑 발송에 쓰이는 바로 그 봇)으로 오늘 작업 요약을 발송 완료.

## 2-1. 실제 코드 수정 기능 (오늘 저녁 완성, 커밋 `052e676`)

소유자가 이후 대화에서 "이건 텔레그램으로 수정 사항을 보내서 나에게 승인 받으면 되잖아... 매 작업이 5개의 루프를 돌고 나면 최종 승인을 받아줘"라고 구체적으로 지시했고, Codex/Claude가 실제 모델 호출(Claude·Codex·Qwen·DeepSeek 4개 통과, Gemini는 실제 429로 검증 자체가 막힘 - 아래 참고)로 검증한 뒤 GPT 토큰 소진으로 중단된 지점부터 내(Claude)가 이어받았다.

**아키텍처** (`services/agentic_apply_worker.py` + `services/approved_code_jobs.py` + `approved_code_routes.py` + `frontend/agentic-code.html`):
- 모델은 실행 가능한 명령이 아니라 **파일 전체 내용만 JSON**으로 반환한다(`{"files":[{"path":...,"content":...}]}`) - 도구 호출, 셸 명령, 위임 전부 금지. 신뢰된 컨트롤러가 diff 생성·적용을 전담해 "자율 에이전트가 알아서 실행" 패턴 자체가 없다(이게 앞서 안전 분류기에 안 걸린 이유로 보인다).
- **2단계 승인**: ① `create()` → AWAITING_APPROVAL(이 지시를 시도해도 되는가) → `approve()` → 백그라운드로 `execute()`(모델 호출 → diff 생성 → `stage_patch()`로 완전 격리된 클론에 적용 → macOS `sandbox-exec`로 네트워크·클론 밖 쓰기 차단한 채 지정된 unittest 모듈만 실행) → READY_FOR_REVIEW/TEST_FAILED/FAILED. ② `apply_to_workspace()`(실제 diff+테스트 결과를 보고 나서의 두 번째 승인) → 실제 REPO_ROOT에 `git apply`만 실행(커밋·브랜치·병합·서비스 재시작 없음) → APPLIED_TO_WORKSPACE. `rollback()`으로 정확히 원복 가능.
- 승인 해시(spec의 sha256)가 매 단계 재검증되고, 대상 파일이 승인 시점 이후 바뀌었으면(다른 세션이 동시 수정 등) 명시적으로 거부한다. 테스트 실행 자체가 검토 대상 코드를 바꾸지 않았는지도 재확인한다.
- 내가 추가한 것: (a) 라이브 서비스가 재시작돼도 새 라우트가 OpenAPI에 안 잡히던 stale `__pycache__` 문제 발견 및 수정(캐시 삭제 후 재시작으로 해결 확인), (b) 텔레그램 알림 3곳(1차 승인 요청/2차 검토 요청/적용·롤백 완료) - `GOAL_INTAKE_BOT_TOKEN`(`@K_Agentic_AI_bot`) 사용, 승인/거부 자체는 여전히 `/agentic-code` 웹 화면에서만(텔레그램 답장 자동 파싱 안 함 - 다른 프로세스의 getUpdates 폴링과 충돌 방지 목적, 이전 세션들의 동일한 결정과 일관).

**미해결**: 소유자가 요구한 "매 작업이 5단계 루프를 돌고 나면 최종 승인"을 strict_agi_orchestrator.py의 자율 목표 루프(`maintain_goals()`)와 아직 연결하지 않았다 - 지금은 사람이 `/agentic-code`에서 개별적으로 요청을 만들어야 시작된다. 자율 루프가 스스로 code-job을 생성하도록 연결하는 건 다음 작업이다(무엇을 고칠지 자동으로 판단하는 부분과, 위 승인 체계를 잇는 설계가 필요).

**Gemini 검증 실패**: 실제 429(진짜 한도 초과)로 막혔다 - 이건 코드 결함이 아니라 위 "Gemini 구독형" 절에서 설명한 실제 무료 등급 한도 문제와 동일한 원인이다. 다른 provider 결과로 대신 통과시키지 않고 Gemini 자체의 실행 실패로 정직하게 남겼다(요청하신 그대로).

## 3. 미완료 — 소유자가 명시적으로 다음 작업으로 지목함

### 미완료 1 — 자율 루프와 실제 코드 수정 기능 연결

위 "2-1"에서 실제 코드 수정 기능 자체는 완성됐다. 남은 건 strict_agi_orchestrator.py의 5단계 자율 루프가 스스로 code-job을 생성·승인 요청까지 하도록 잇는 것이다.

### 미완료 2 — "리포트는 완전 다른 체계로 운영, 숫자·그래프 인사이트여야"

오늘 한 건 "가짜 글로벌 소스 0건 → 실제 소스 7개 연결"까지다. 소유자가 요구하는 수준(회사 데이터·수집 데이터·외부 뉴스/보고서를 정량적으로 융합해 차트로 보여주는 리포트)은 별도 설계가 필요한 더 큰 작업이다 — `ceo_notebooklm_service.py`는 지금도 "NotebookLM에 업로드할 소스북 텍스트"를 만드는 도구일 뿐, 자체적으로 숫자/그래프를 생성하지 않는다.

### 미완료 3 — D02(Gemini 실제 결제 경로), D05(NEEDS_RECONCILIATION 자동 조정 루프)

원본 진단 문서 그대로 남아 있다.

### 관찰 - `services/rss_ingest.py` (커밋하지 않음, 수정도 안 함)

작업 트리에 114줄 diff가 있다(다른 세션 작업으로 추정). 두 가지를 그대로 기록만 해둔다:
1. `chat_completion_content()` 근처 `db_p` 경로가 `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/data/ceo_briefing.db`에서 `/Users/brainlee/Downloads/codex/ceo-briefing-platform/data/ceo_briefing.db`로 바뀌어 있다 - 이 환경(외장 SSD)과 다른 로컬 경로라 의심스럽다. 다른 머신/세션에서 작업한 흔적일 수 있으니 재검토 시 이 경로가 맞는지 확인 필요.
2. `send_telegram_briefing()`의 `_dedup_by_topic()`에 동일 기관/기체 키워드(`교통안전공단`, `kf-21` 등) 과다 노출을 막는 주체별 최대 2개 제한이 추가돼 있다 - 겉보기엔 합리적인 개선으로 보이나, 소유자가 "RSS는 회사 뉴스 전용, 건드리면 텔레그램 발송이 망가진다"고 명시적으로 경고한 바로 그 파일이라 내가 검토하거나 커밋하지 않았다. 이 변경의 출처와 의도를 확인해 주기 바란다.

## 4. 검증 방법

각 커밋 후 `./venv/bin/python3 -m unittest <해당 테스트 파일>`로 전체 통과 확인(오늘 누적 81개 이상, `agentic_apply_worker`/`approved_code_jobs` 25개 포함), 이어서 백엔드(`uvicorn main:app`, 포트 8011)를 재시작하고 실제 운영 과업/실제 API 응답으로 재확인했다. 특히 goal_1의 실제 운영 과업(`qtask-1789299983526-810fa7`)을 stage 3부터 다시 실행시켜 DRAFT_READY까지 도달하는 것을 직접 관찰했고, CEO 리포트는 실제 DB(`ceo_briefing.db`)에 대해 수집을 실행해 `global_authority_count` 0→11을 실측했다. 실제 코드 수정 기능은 Codex/Claude가 4개 provider(Claude/Codex/Qwen/DeepSeek)를 실제 모델 호출로 검증했고, 나는 재시작 후 라이브 서비스에 새 라우트 7개가 실제로 등록됐는지(OpenAPI 스키마 대조)와 관리자 인증 게이트가 걸려 있는지(404가 아니라 401/로그인 필요 응답)를 직접 재확인했다.

## 6. 오늘 저녁 추가 작업 — 텔레그램 최종 승인 버튼 + 배포 경로 결함 발견/수정

소유자 지시: "1. 제미니는 씨엘아이로 연결된거 확인했는데 왜 계속 문제야? 2. 웹에서 어디에
표시되는건데 아무리 찾아도 없어 3. 그리고 텔레그램에서 최종 승인 누르면 나오도록 해달라고"

- **(1) Gemini**: 다시 확인한 결과 `~/.gemini/settings.json`(OAuth)은 여전히 없다 - 어제
  "CLI로 연결했다"는 건 OAuth 로그인이 아니라 직접 CLI 명령 실행 확인이었을 가능성이 높다.
  다만 실제 API 키 기반 호출을 다시 시도하니 성공했다(어제 21시경 관측된 429는 시간이 지나며
  쿼터가 리셋된 것으로 추정). 코드(`_gemini_call()`)는 이미 OAuth 설정 파일이 생기면 그쪽을
  우선하도록 돼 있으니, 소유자가 실제로 `gemini` CLI에서 `/auth` → Google 로그인을 완료하면
  자동으로 유료/OAuth 경로로 전환된다. 지금은 API 키 무료 등급 재시도로 동작 중.
- **(2) 웹 UI 발견성**: 기존 진입점은 "⚙️ 시스템" 탭 안의 작은 보조 버튼 하나뿐이었다(`kai.js`
  `renderSystemPage()`). `frontend/kai/index.html`의 최상단 nav bar에 "✅ 코드 승인" 전용
  탭을 새로 추가하고(`kai.js`에서 API 오리진으로 href 동적 설정), 브라우저로 직접 열어
  `http://127.0.0.1:5500/kai/` → 클릭 → 승인 페이지 로드까지 실측 확인했다.
- **(3) 텔레그램 최종 승인**: `services/telegram_approval_poller.py` 신규 - `GOAL_INTAKE_BOT_TOKEN`
  (`@K_Agentic_AI_bot`)으로 `getUpdates`를 폴링하되, **인라인 버튼(callback_query)만** 처리하고
  자유 텍스트 답장은 절대 파싱하지 않는다. 버튼은 READY_FOR_REVIEW 알림에 "✅ 최종 승인(실제
  적용)"으로 붙고, 누르면 `CodeJobs.apply_to_workspace(job_id, diff_hash, approved_by=
  "owner_via_telegram")`를 그대로 호출한다(웹의 2차 승인과 동일 함수, 동일 안전장치 - diff
  해시/HEAD 불일치/파일 변경 여부 전부 재검증됨). 요청자 `chat_id`가 `TELEGRAM_CHAT_ID`와
  다르면 거부, callback_data 형식이 안 맞으면 거부. `approved_code_routes.py`의
  `_execute_and_notify()`가 READY_FOR_REVIEW 알림을 보낼 때 `notify_telegram()` 대신
  `send_final_approval_prompt()`를 쓰도록 교체했고, `main.py`의 기존 `startup_scheduler()`에
  `approval_poller.start()`를 추가해(`CEO_BACKGROUND_JOBS` 게이트 동일 적용) 백엔드 기동 시
  자동 시작되게 했다. **1차 승인(코드 생성 자체를 시도할지)과 거부/롤백은 의도적으로 여전히
  웹에서만** - diff가 없는 단계라 버튼만으로 판단하기엔 정보가 부족하다는 이전 설계 그대로 유지.
  실제 텔레그램 폴링 전, 이 봇 토큰을 폴링 중인 다른 프로세스가 없는지 `ps aux`로 재확인했다
  (`goal_intake_daemon.py`/`goal_execution_scheduler` 둘 다 실행 중 아님 - 안전 확인).
  콜백 처리 로직(정상 승인/잘못된 chat_id 거부/잘못된 형식 거부 3가지 경로)은 실제 텔레그램
  메시지를 보내지 않고 `unittest.mock`으로 격리 검증했다(운영 봇 채널에 테스트용 가짜 버튼을
  보내는 건 소유자 혼란을 유발할 수 있어 피함).

### 발견 — 배포 venv가 깨져 있었음 (Homebrew python@3.13 → 3.14 자동 업그레이드, 9/12)

`backend/.venv`와 `/Users/brainlee/Downloads/codex/.../backend/.venv` 둘 다 `python3.13`
심볼릭 링크가 가리키는 `/opt/homebrew/opt/python@3.13`가 사라져(brew가 3.14로 자동 갱신)
완전히 깨져 있었다(`no such file or directory`). 이게 원인이 돼 재시작 시도가 계속
`No module named uvicorn`으로 실패했을 가능성이 있다. `python@3.11`(안정적으로 남아있는
버전)로 두 venv를 전부 재생성하고 `requirements.txt` 재설치, `fastapi==0.116.1`/
`uvicorn==0.35.0` 정상 로드 확인했다. 포트 8011 라이브 서비스를 AI System 경로에서
재기동해 신규 라우트 7개·승인 폴러 정상 기동을 재확인했다.

### 발견 — `sync_unified.sh`가 백엔드 소스 대부분을 동기화하지 않고 있었음

`start_backend.sh`/`start_app.sh`가 실제로 구동하는 경로는
`/Users/brainlee/Downloads/codex/ceo-briefing-platform/backend`인데, 기존 동기화
스크립트는 프론트엔드 3개 파일과 `backend/main.py` **딱 하나만** AI System(마스터)에서
그쪽으로 미러링했다. 즉 `approved_code_routes.py`, `services/*`,
`strict_agi_orchestrator.py`, `ceo_notebooklm_service.py` 등은 AI System에서 아무리
고쳐도 표준 실행 스크립트로 재기동하면 조용히 구버전으로 돌아가는 구조였다. `sync_unified.sh`
2번 항목을 `rsync`로 백엔드 전체 `.py` 트리를 미러링하도록 넓혔다 - `.venv`/`data`/`*.db`/
`*.json`/`.env`는 제외(각 환경 고유), **`services/rss_ingest.py`는 명시적으로 제외**(소유자가
민감하다고 밝힌 파일이고 지금 커밋 안 된 미검토 변경이 AI System 쪽에 남아 있어, 자동
동기화가 그 미검토 변경을 그대로 배포본에 밀어넣지 않도록 막음). dry-run(`rsync -an`)으로
삭제될 파일이 없음을 먼저 확인한 뒤 실행했고, 이어서 Downloads 쪽 venv도 동일하게 재생성했다.
**Codex 재검토 요청**: 이 스크립트가 이번 대화 이전부터 이런 좁은 범위였는지, 아니면 최근
어느 세션이 의도적으로 줄인 것인지 확인 부탁 - 의도적이었다면 이유를 알아야 다시 좁힐지
판단 가능하다.

## 7. 2026-09-15 낮 — "아무 응답이 없다" 진단 및 자율 루프 cadence를 진짜 Agentic 방식으로 재작성

소유자 지시: "1. 2개만 우선적으로 실행해줘 2. 왜 24시간 안돌고 24시간에 1회만 돌지 3. 1번
돌고 100%가 안되면 계속 검토를 해야지 4. Agentic Ai 개념으로 적용을 해줘"

**진단 순서 (직접 상태 파일/로그로 확인, 추측 아님):**
1. 백엔드(8011)/프론트엔드(5500) 모두 정상 응답, 에러 로그 없음 - 서버는 살아있었다.
2. `strict_agi_state.json` 직접 조회: 7개 목표 중 5개(`goal_2`~`goal_5`, `goal_7`)가
   `auto_continue: False` - 코드상 이걸 자동으로 끄는 로직이 없어(`upsert_goal`/
   `set_goal_auto`/`set_all_goals_auto`가 유일한 진입점), 과거 어느 시점에 API로
   직접 꺼진 것으로 보인다(누가/왜 껐는지는 상태 파일만으론 알 수 없음).
3. 남은 2개(`goal_1`,`goal_6`)는 켜져 있었지만 마지막 실행 후 `next_run_at`이
   +24시간으로 박혀 있어 그날 밤(20시대)까지 아무 것도 안 도는 게 정상이었다.
4. **근본 원인**: `maintain_goals()`가 사이클 **결과와 무관하게** 디스패치 직후
   무조건 `next_run_at = now + cadence_hours(24)`를 박아뒀다. 그런데 이 파이프라인은
   `core_review_reason`이 없는 일반 자율 루프 과업은 절대 `COMPLETED`에 도달하지
   못하고 항상 `DRAFT_READY`(초안·미검증, `자기 인증 방지 원칙`에 따라 Claude/Gemini/
   DeepSeek가 "완료"를 자체 인증할 수 없어서)로 끝난다 - 즉 사실상 매번 "완료 아님"인데도
   무조건 24시간을 쉬는 구조였다. 부가 발견: `main.py`의 `/`·`/health`가 참조하는
   `quota_resume_manager`는 실은 `strict_agi_orchestrator` 싱글턴의 **별칭 import**일
   뿐이고(`main.py:70`), `services/quota_resume_manager.py`라는 이름의 별도 844줄
   구현체는 완전히 미사용 dead code다 - 혼란만 주므로 정리 후보로 기록.

**수정 (`strict_agi_orchestrator.py` `maintain_goals()`)**: cadence 게이트를 "무조건
24시간"에서 "검증된 완료(COMPLETED·verdict=PASS)에 도달했을 때만 그 시점부터
cadence_hours를 쉬고, 그 외(DRAFT_READY/INVALIDATED/FAILED 등 - 아직 안 끝남)는
대기 없이 다음 60초 감시 주기에 바로 이어서 재시도"로 재작성했다. `next_run_at`은
더 이상 게이트가 아니라 화면 표시용 힌트로만 쓴다(검증된 완료 후 쿨다운 종료 시각을
보여줄 때만 값이 채워지고, 계속 진행 중일 땐 `None`). 신규 회귀 테스트 2개
(`test_unverified_draft_does_not_wait_24h_before_next_iteration`,
`test_verified_pass_applies_cadence_cooldown_before_next_iteration`) 추가, 기존
25개 포함 전체 27개 통과 확인. 이 변경 하나로 소유자의 1~4번 요청이 모두 충족된다:
`goal_2`~`goal_5`,`goal_7`은 여전히 `auto_continue: False`라 안 켜지므로 자연히
"2개만"(`goal_1`,`goal_6`) 우선 실행되고, 그 2개는 재배포 후 다음 60초 틱 안에 즉시
재개됐다(`goal_1`이 실제로 새 과업 dispatch·stage 1 실행 시작하는 것까지 실측 확인).

**주의(소유자에게 전달함)**: 이 파이프라인은 현재 자동으로 `COMPLETED+PASS`에 도달하는
코드 경로가 전혀 없다(`core_review_reason`을 넘기지 않는 자율 루프는 구조적으로
`DRAFT_READY`가 최종 상태) - 따라서 이 두 목표는 사실상 **하루 종일 쉬지 않고 계속
반복 실행**된다(5단계 x 60초 틱 ≈ 사이클당 5~15분, 하루 수십~백여 회). 무료 등급
쿼터(Gemini 1,500 RPD 등) 내에서는 지속 가능하지만, 비용/부하가 걱정되면
`cadence_hours`를 늘리거나 특정 시간대만 켜는 정책이 필요할 수 있다 - 이번 변경
범위에는 포함하지 않았다.

**Codex 재검토 요청 추가**: (a) `services/quota_resume_manager.py`가 정말 미사용
dead code인지(다른 어떤 파일도 import하지 않는지) 재확인 후 삭제 여부 판단, (b) 자율
루프가 구조적으로 `COMPLETED+PASS`에 도달할 수 없는 현재 설계가 의도된 것인지(즉
"일반 목표는 영원히 초안만 쌓고 사람이 수동으로 완료 인증한다"가 맞는 설계인지),
아니면 core_review_reason을 목표 유형별로 선택적으로 부여하는 경로가 필요한지 검토.

## 8. 2026-09-15 오후 — goal_1이 실제 DB를 한 번도 안 봤다는 것을 발견, 실제 실행 도구 연결

바로 위(7번) cadence 수정을 배포한 지 3시간 만에 goal_1이 42회 반복됐길래 실제 산출물을
직접 열어봤다. **5단계 전부 순수 LLM 텍스트 생성이었고, 어떤 단계도 실제로
`stock_dashboard`의 DB에 접속한 적이 없었다.** 매 회차 산출물이 스스로 이렇게 밝히고
있었다: "실행하지 않았다. 코드 실행·데이터 검사·저장소 접근·외부 소스 조회 전부
미수행", "라운드마다 명세는 증가, 증거는 정체". 즉 "데이터 무결점을 어떻게 판정할지"에
대한 방법론 문서를 매번 살짝 다듬어 다시 쓰기만 했다 - 바로 위에서 배포한 "100%
아니면 계속 검토" 수정이 이 무의미한 반복을 오히려 24시간에 1번에서 5분에 1번으로
증폭시켰다. 소유자에게 그대로 보고했고, "실제 실행 도구 연결"을 선택받았다.

**추가로 조사 중 발견한 실제 문제(핵심 발견)**: `stock_dashboard/stock.db`(13GB, 수백
개 테이블)를 직접 조회한 결과, 라이브 대시보드가 참조하는 핵심 가격 테이블들이
**실제로 몇 주째 갱신이 끊겨 있었다**:
- `price_history`(818만 행): 최신 데이터 2026-08-10 (36일 지연)
- `stock_price_daily`(85만 행): 최신 2026-07-28 (49일 지연)
- `us_price_history`(396만 행): 최신 2026-08-21 (25일 지연)
- `radar_price_cache`: 최신 2026-06-19 (3개월 가까이 지연)
- `kiwoom_minute_snapshot`: 0행(완전히 비어 있음)

`us_price_history`에는 비정상(NULL/0 이하) 시가·종가가 82,729건(전체의 ~2%), 이미
있는 `data_quality_issues`/`data_quality_repair_log` 등 품질 추적 테이블들도 최근
활동이 2026-05월대에 멈춰 있다(현재 계속 갱신되는 살아있는 테이블이 아님). 이건
`strict_agi_orchestrator`와 무관하게 **`stock_dashboard` 자체의 실제 운영 문제**로
보이며, 소유자에게 별도로 보고해야 한다(이번 대화에서 아직 stock_dashboard 쪽
수집 데몬을 직접 진단하지는 않음 - 범위 밖).

**구현**: `services/data_integrity_scan.py`(신규) - `stock.db`에 읽기 전용(`mode=ro`
URI, 실수로도 쓰기 불가) 연결로 실제 스캔을 수행하는 신뢰된 컨트롤러 스크립트.
큰 테이블도 인덱스를 타는 가벼운 쿼리만 쓴다(전체 스캔 금지). 판정 문구는 스캐너가
내리지 않고 숫자만 반환 - 판정은 LLM 단계의 몫으로 남긴다. 구현 중 실제 버그 하나
발견·수정: `stock_price_daily.bas_dt`가 구분자 없는 "YYYYMMDD" 문자열인데
`datetime('now')`(ISO, 대시 포함)와 그냥 문자열 비교하면 `'0' > '-'`라서 **전체
853,366행 중 193,805건이 전부 거짓 양성으로 "미래 날짜"로 잡혔다** - 컬럼 형식별로
비교 기준값을 맞추도록 수정, 회귀 테스트(`test_data_integrity_scan.py`)로 고정했다.

`strict_agi_orchestrator.py`에 `_real_evidence_for_goal(gid)` 추가 - goal_1 한정으로
디스패치 직전 이 스캐너를 실제로 호출해 결과를 프롬프트에 못박는다("실측 데이터
스캔 결과" 절, 스캐너 실패 시에도 조용히 넘기지 않고 그 실패 자체를 과제로 던짐).
지금은 goal_1 전용 - 다른 목표 일반화는 안 함(각 목표마다 무엇이 "실제 실행"인지
다르므로 goal별 개별 설계 필요).

**같은 조사 중 함께 고친 공정성 버그**: `maintain_goals()`가 due한 목표 중 항상
목록 순서상 첫 번째(goal_1)만 골랐다 - goal_1처럼 영원히 검증된 완료에 못 미치는
목표가 있으면 뒤 목표(goal_6)가 영원히 차례가 안 온다(실측: 배포 후 goal_6
`updated_at`이 전날 그대로 멈춰 있음 확인). "가장 오래 전에 마지막으로 시도한"
목표부터 공정하게 순환하도록 수정, 회귀 테스트
(`test_two_perpetually_unverified_goals_alternate_instead_of_one_starving`) 추가.

**신규 테스트**: `test_strict_agi_orchestrator.py`에 4개 추가(미검증 결과 시 24시간
대기 없음, 검증된 PASS 시에만 쿨다운, 두 목표 공정 순환, goal_1 전용 스캐너 근거
주입/다른 목표엔 안 섞임), `test_data_integrity_scan.py` 신규 2개(압축 날짜 형식
오탐 수정 고정). 전체 30개 통과 확인 후 재배포·재동기화.

**Codex 재검토 요청 추가**: (a) `stock_dashboard`의 가격 데이터 수집이 실제로 5주
이상 멈춘 것이 맞는지, 어느 수집 데몬(`agi_autonomous_daemon.py` 등)이 담당인지
확인 - 이번 대화 범위 밖이라 원인 진단은 안 했다. (b) `_real_evidence_for_goal`이
goal_1 전용으로 하드코딩된 게 맞는 설계인지, 아니면 목표별 스캐너를 등록하는
일반화된 구조(예: goal_id → 스캐너 함수 매핑)가 필요한지 검토.

**실측 확인**: 재배포 후 실제 라이브 과업(`qtask-1789456172933-eb8eb6`)의
`description`을 직접 조회해, 위 실측 숫자(818만 행/36일 지연/비정상 3건 등)가
그대로 프롬프트에 박혀 모델에게 전달되는 것을 확인했다. 같은 사이클에서 goal_6도
공정 순환에 따라 먼저 한 번 돌고 DRAFT_READY로 끝난 뒤 goal_1로 정상적으로
순번이 넘어온 것까지 실측 확인.

## 9. 2026-09-17 — 정정: "가격 데이터 5~7주 지연" 진단은 틀렸음, 그리고 새로 발견한 루프 정지

**정정부터**: 8번 항목에서 "stock_dashboard 가격 데이터가 5~7주째 멈췄다"고 보고했는데
**이건 틀렸다.** 원인: `/Volumes/Realtek_NVME/stock_dashboard/stock.db`(SQLite 파일,
13GB)를 스캔했는데, 실제로는 이 시스템이 이미 **PostgreSQL로 커트오버**했다
(`runtime/config.py`의 `POSTGRES_DATABASE_URL`, `runtime/main.py`가 `IS_POSTGRES`
분기 사용, git 이력에 "Postgres 커트오버 검토용 스냅샷" 커밋 존재). 그 SQLite
파일은 컷오버 시점에 멈춘 **죽은 스냅샷**이었고, 나는 그걸 "실제 데이터"로 착각해
확신을 갖고 틀린 보고를 했다. 실제 PostgreSQL(`postgresql://stock_dashboard@
127.0.0.1:5432/stock_dashboard`, 실측 시점 활성 커넥션 20개 이상)을 직접 조회하니
`price_history` 최신 날짜가 **오늘(2026-09-17)**, 1,023만 행 - 전혀 안 멈췄다.

**수정**: `services/data_integrity_scan.py`를 SQLite(`stock.db`)가 아니라 실제
운영 PostgreSQL을 조회하도록 다시 작성했다(`psycopg[binary]` 신규 의존성 추가,
두 배포 venv 모두 설치, `requirements.txt` 갱신 - rsync 필터가 `.py`만 옮기고
`requirements.txt`는 안 옮겨서 수동으로도 복사함). 세션 자체를
`SET default_transaction_read_only=on`으로 잠가 SQLite의 `mode=ro`와 동등한
쓰기 방지를 유지했다. 재작성 과정에서 이전 SQLite 버전 테스트가 Postgres 전용
SQL(`::float`, `to_char`)과 안 맞아 깨져서, 실제 DB 없이도 도는 fake-connection
기반 단위 테스트로 교체했다(SQL 텍스트 자체를 검증 - 압축/ISO 날짜 형식별로
맞는 비교식이 나가는지). 재배포 후 실제 라이브 과업에서 정정된 숫자(오늘 날짜,
1,023만 행)가 프롬프트에 반영되는 것까지 재확인했다.

**참고로 남겨둔 사실**: Postgres 쪽 `invalid_ohlc_count`(NULL/0 이하 시가·종가)가
SQLite 스냅샷보다 훨씬 크다(`price_history` 3건→204,802건, 행 수가 810만→1,023만로
늘어난 것과 별개로 비율 자체도 큼). 이게 실제 데이터 품질 문제인지, 컷오버 중
채워진 플레이스홀더인지는 확인 안 했다 - Codex 재검토 시 확인 요청.

**새로 발견한 별개 문제 - 자율 루프가 41시간 정지해 있었음**: goal_1·goal_6 둘 다
`WAITING_REPAIR`(2026-09-16 03:22~03:23에 파킹, 발견 시점 09-17 20시경까지 41시간
방치)로 멈춰 있었다. 원인: 해당 시점에 Gemini(일시적 503/헤더 타임아웃으로 추정 -
지금 재현 중에도 간헐적으로 재현됨)와 DeepSeek(월 10,000원 예산 상한 도달) 폴백
경로가 동시에 실패해 3회 연속 실패 임계치에 도달, `WAITING_REPAIR`로 영구 파킹됐다.
**설계상 결함**: `WAITING_REPAIR`는 자동 재시도/자동 해제 경로가 전혀 없다
(`check_and_resume()`이 이 상태를 무기한 건너뜀) - 두 공급자가 동시에 잠깐
불안정했을 뿐인데 그 이후로 영영 멈춰 있었다는 뜻이다. 임시 조치로 두 과업의
막힌 단계 실패 카운트를 지우고 `QUEUED`로 되돌려 재시도하게 했다(상태 파일 직접
수정 - 코드 변경 없음). DeepSeek 예산은 이번 달 남은 기간엔 그대로 소진 상태로
남는다(월 단위 리셋, 소유자가 한도를 올리지 않는 한 다음 달까지 회복 안 됨).
**Codex 재검토/소유자 결정 필요**: `WAITING_REPAIR`에 자동 재시도(예: 일정 시간
후 재도전, 또는 다른 목표로 우회)를 넣을지 - 지금은 사람이 상태 파일을 직접
고쳐야만 풀린다.

**관찰만 해둔 것(이번엔 고치지 않음)**: `runtime/cron_3am.sh`가 여전히
`POST /api/commands/collect-all`을 호출하는데 이 라우트는 현재 `runtime/main.py`에
존재하지 않는다(`curl ... || true`가 실패를 삼켜 로그상 "완료"로 보임). 다만
가격 데이터 자체는 다른 경로로 정상 수집되고 있는 게 확인됐으니(Postgres 최신
날짜=오늘) 이 스크립트가 정확히 뭘 위한 것이었는지, 지금도 필요한 작업인지 확인
후 정리해야 한다 - `cron_3am.log`도 2026-08-28 이후 기록이 없어 이 cron 항목
자체가 최근 실행됐는지도 불분명하다.

## 5. Codex에게 요청

- 위 커밋들의 diff를 검토하고, 특히 "미확인 사항"(Gemini 실제 결제 프로젝트 상태, Codex SDK의 turn별 실제 적용 모델)은 이 문서도 확정하지 못했다는 점을 그대로 유지해 달라 — 확인 안 된 것을 확인된 것처럼 쓰지 말 것.
- 미완료 1(자율 루프 ↔ 실제 코드 수정 기능 연결)부터 이어서 진행할 것을 제안한다 - 실제 코드 수정 기능 자체는 완성/커밋됐다.
- `services/rss_ingest.py`의 두 가지 관찰 사항(경로 변경, dedup 로직 추가)의 출처를 확인해 달라 - 소유자가 이 파일에 특히 민감하다.
- `services/rss_ingest.py`의 KAI 전용 필터(`filter_items_by_keywords`)는 이번에 전혀 수정하지 않았다 — 재검토 시 이 사실을 재확인하고, 혹시 다른 경로에서 이미 손댄 적이 있는지도 함께 확인해 주기 바란다(소유자가 이 부분에 특히 민감하다고 명시함).
