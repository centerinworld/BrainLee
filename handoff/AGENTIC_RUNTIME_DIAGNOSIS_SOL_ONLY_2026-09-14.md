# Agentic AI 중단 원인 점검 및 수정 핸드오프 — GPT Sol 전용

작성일 2026-09-14. 범위: 소스·영속 원장·산출물·설정의 읽기 전용 점검과 문서 작성. 운영 프로세스 재시작, 모델 호출, 결제 변경, 목표 자동 실행 설정 변경은 하지 않았다. 비밀값은 기록하지 않았다. 실행 중 시스템의 상태는 관찰 이후 변할 수 있다.

**최상위 요구:** 이 시스템의 모든 GPT 호출은 `gpt-5.6-sol`만 사용한다. 과거 핸드오프의 Astra 권고는 폐기한다. 기존 Codex 구독 실행을 유지하되, Sol 접근 불가 시 다른 GPT로 자동 전환하지 않는다. 이번에는 정책을 문서화했으며 런타임 설정에 적용했다고 주장하지 않는다.

## 1. 실제로 어디서 막히는가

SDK가 전혀 연결되지 않은 상태는 아니다. 최신 SDK 원장에 SUCCEEDED가 있고, 아래 두 산출물의 파일 존재와 SHA-256 일치를 직접 확인했다. 이것은 SDK 결과 생성 증거이며 목표 구현·배포 성공 증거는 아니다.

| 확인 사항 | 관찰 |
|---|---|
| 최근 SDK 성공 | `agi_qtask-1789363013166-757731_plan_1789363013`, 2026-09-14 14:18:44.690483 |
| 앞선 SDK 성공 | `agi_qtask-1789299983526-810fa7_plan_1789328386`, 2026-09-14 04:45:56.021354 |
| GPT 모델 식별 | 최근 두 run 모두 requested_model=None, resolved_model=None |
| 현재 Codex 사용자 기본 설정 | `/Users/brainlee/.codex/config.toml`의 model은 `gpt-6-astra` |
| 최신 strict 상태 관찰 | updated_at 2026-09-14 18:35:53+09:00, task 19개 |
| 신규 작업 | `qtask-1789363013166-757731`: 2단계 Gemini WAITING_QUOTA, next_retry_at 19:19:05+09:00 |
| 다른 작업 | `qtask-1789299983526-810fa7`: 4단계 Claude EXECUTION_ERROR, next_retry_at 18:38:55+09:00 |
| Claude 반복 | 18:21, 18:27, 18:33 무렵 같은 단계 시작·실패 반복 기록 |
| 자동 계속 실행 | 목표 1·6만 true. 목표 2·3·4·5·7은 false |

SDK 과거 실패에는 SDK_NOT_INSTALLED, Codex 상태 SQLite 초기화 권한 오류, 실제 구독 usage limit 오류가 함께 존재한다. 최근 성공이 있으므로 과거 설치 오류 하나를 현재 모든 실패의 원인으로 단정하지 않는다. 과거 RUNNING이 남은 run도 발견됐다.

## 2. 원인과 수정 지시

### D01 — GPT Sol 지정이 실제 호출까지 전달되지 않음 (P0)

strict `_codex()`는 `model=os.getenv("AGI_CODEX_MODEL") or None`을 전달한다. 점검한 두 .env에는 AGI_CODEX_MODEL 항목이 없었고 최근 요청도 None이다. 사용자 기본 설정은 Astra이므로 기본값 의존 시 Astra 선택 위험이 있다. resolved_model 미기록 때문에 실제 사용 모델은 이 원장만으로 확정할 수 없다. 별도 llm_client의 OpenAI API fallback은 gpt-4o-mini여서 이 경로도 Sol 전용 요구에 어긋난다.

수정자는 공통 모델 정책에서 GPT allowlist를 `{"gpt-5.6-sol"}`로 제한한다. planner·worker·reviewer·resume·CLI·SDK·API 모든 GPT 경로에 명시적으로 Sol을 전달한다. None과 다른 GPT ID를 거부하고 자동 Astra 승격·mini fallback을 제거한다. CLI는 설치 help에 맞는 명시 모델 옵션을 사용한다. SDK response/thread metadata로 실제 모델을 기록하고 미확인 상태를 숨기지 않는다. 기존 세션 resume에도 정책을 재적용한다.

환경변수 한 줄만 추가하고 완료하지 않는다. strict의 os.getenv와 다른 설정 loader의 우선순위를 통합해 실제 서비스 프로세스의 effective model을 확인한다. 전역 Codex 설정 변경은 시스템 외 작업에도 영향을 줄 수 있으므로 프로젝트 실행 경로의 명시 정책을 우선한다. 사용자 대화 자체의 모델 변경과 시스템 worker 모델 정책은 별개다.

근거: [Sol 공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.6-sol). 공식 ID 존재는 해당 구독·SDK 런타임에서 실제 실행 성공을 보장하지 않으므로 구현 후 소규모 계약 검사 필요.

### D02 — Gemini '유료 모델' 적용과 실제 결제 경로를 혼동 (P0)

현재 `_gemini_call()`에는 GEMINI_API_KEY를 자식 환경에 넣고 `--model`을 지정하는 변경이 이미 있다. 코드 기본값은 `gemini-3.7-flash`, 두 .env의 GEMINI_MODEL은 `gemini-3.6-flash`다. strict는 GEMINI_MODEL 대신 AGI_GEMINI_MODEL을 읽으므로 설정 화면/다른 client에서 모델을 바꿔도 이 경로에 반영되지 않을 수 있다. 해당 모델의 계정별 사용 가능 여부는 이번에 호출 검증하지 않았다.

두 .env 모두 GEMINI_API_KEY가 4회 반복되지만 파일별 값은 동일했다. 이번 점검에서 서로 다른 키 충돌은 확인되지 않았다. 반복 항목은 단일화하되, '키가 여러 번 있으니 잘못된 키가 선택됐다'고 단정하지 않는다. 실제 서비스 환경변수는 파일보다 우선할 수 있으며 이번에 프로세스 비밀 환경은 덤프하지 않았다.

모델명을 바꾸거나 키를 전달하는 것만으로 Paid Tier가 입증되지 않는다. Gemini API는 연결된 Cloud Billing 계정/프로젝트의 tier와 한도를 사용한다. 유료 상태에서도 한도에 걸릴 수 있으므로 WAITING_QUOTA만으로 무료 경로라고 결론 내리지 않는다. Gemini CLI의 Google 로그인·API key·Vertex 인증 중 실제 선택된 경로를 확인해야 한다. [Google 과금·tier](https://ai.google.dev/gemini-api/docs/billing), [CLI 인증](https://geminicli.com/docs/get-started/authentication/).

수정 순서: 단일 모델 설정 → 인증 경로 명시 → 키 소속 프로젝트의 Paid/결제 활성 상태 확인 → 승인된 유료 경로의 소규모 요청 → 응답 모델·사용량·프로젝트별 사용 기록 대조. Google 앱 유료 구독과 API key 과금 경로를 별개로 확인한다. 원시 키나 인증 토큰을 로그에 저장하지 않는다. 코드 주석의 '무료 하루 20회' 설명은 현재 호출 오류 원문으로 입증되지 않았으므로 관측 사실로 재사용하지 않는다.

### D03 — 임의의 5시간 대기가 실제 리셋 시간처럼 표시됨 (P1)

`_retry()`는 특정 영어 상대 시간 형식만 파싱하고 나머지는 현재 시각+5시간을 반환한다. Gemini 작업의 14:19:05 → 19:19:05가 이 기본값과 일치한다. 실제 공급자 리셋 시각을 확인한 증거가 아니다. SDK 과거 오류의 절대 날짜·시각도 이 파서로 다루지 못한다.

`provider_reset_at`과 `next_probe_at`을 분리한다. Retry-After/구조화 retry 정보가 있으면 사용하고, 없으면 리셋은 unknown으로 유지한다. 임시 제한은 상한 있는 backoff+jitter, billing/auth/model 오류는 재시도 대신 조건 복구로 분류한다. 동일 실패를 일정 간격으로 무한 반복하지 않는다.

### D04 — 한 작업의 대기가 전체 목표 생성을 막음 (P0)

`maintain_goals()`는 FINAL이 아닌 작업이 하나라도 있으면 새 목표 작업을 만들지 않는다. WAITING_QUOTA, EXECUTION_ERROR, NEEDS_RECONCILIATION도 여기에 포함된다. 서로 독립적인 목표도 공급자 하나의 장애에 묶인다. 또한 7개 목표 중 5개 auto_continue가 false라서 현재 상태는 '7개 목표를 계속 개선'하도록 설정되어 있지 않다.

목표별 큐와 provider별 실행 슬롯으로 분리한다. 쿼터가 막힌 작업은 checkpoint로 보존하고 실행 슬롯을 놓아준다. 독립적인 수집·기계 검사·문서 정리·다른 공급자 작업을 진행한다. round-robin/우선순위·기한을 적용해 특정 목표가 독점하지 않게 한다. 실제 자금 연결 보류 등 기존 범위는 유지하고, 연구·검증·진단 목표의 auto_continue 의도를 별도로 반영한다.

### D05 — 이전 검수의 차단 이후 자동 복구 연결이 미완성 (P0)

이전 검수에서 최종 문장만으로 COMPLETED 처리하지 않도록 NEEDS_RECONCILIATION 게이트를 넣었다. 현재 소스에도 남아 있다. 이 차단 자체는 허위 완료 방지에 필요하지만, 증거 보완→검사→재검토 경로를 함께 완성하지 못했고 전역 미완료 잠금과 결합해 이후 루프를 막을 수 있다. 앞선 보완을 전체 자동화 완성처럼 해석하면 안 된다.

이 상태를 무조건 COMPLETED로 바꾸지 않는다. 원인별 자동 조정 작업(artifact 대조, 검사 재실행, 근거 보완)을 생성하고, 필요한 증거가 갖춰지면 같은 spec/evidence에 대해 재검토한다. 조정 작업 동안 다른 목표는 계속 실행한다. 제한된 횟수 후에도 해결되지 않는 중대한 외부 조건은 사용자에게 구체적으로 알린다.

### D06 — 실행 오류 상세가 상태 새로고침으로 사라짐 (P1)

`refresh_providers()`는 실제 실행 오류를 단순 설치·로그인 probe의 AVAILABLE로 덮어쓸 수 있다. 현재 Claude task는 EXECUTION_ERROR인데 provider는 AVAILABLE_QUOTA_UNKNOWN이다. task에는 단계/상태만 남아 실제 실패 이유를 이번 원장으로 확정할 수 없다.

각 attempt에 stderr 분류·exit code·CLI 버전·시작/종료·effective auth/model·실행 파일을 비밀값 제거 후 저장한다. 인증 가능, 마지막 요청 실패, 잔여량 미확인을 독립 필드로 둔다. Claude 실제 오류 원문 확보 전 '로그인 문제' 또는 특정 옵션 문제라고 단정하지 않는다. 실패가 약 2초 내 반복되는 기록은 재시도보다 호출 계약 점검이 우선임을 보여준다.

### D07 — SDK run 복구 계약과 실제 구현 단계 부족 (P1)

SDK adapter는 thread.run 종료 후에야 session_id를 저장하므로 도중 중단되면 재개 정보가 빠질 수 있다. heartbeat는 초기 기록뿐이며 별도 갱신이 없다. quota 오류는 일반 FAILED로 기록된다. requested/resolved 모델 추적도 불완전하다.

thread 시작 직후 session_id 저장, heartbeat·timeout·lease, 오류 유형 분리, 재시도 attempt 연결을 구현한다. 다른 프로세스의 claim과 stale run 조정을 포함한다. 기존 성공 run의 artifact 검증 후 재사용하고 중복 실행을 막는다.

strict의 Codex는 read-only, Claude는 plan 모드다. 이런 경로는 계획·검토 산출물 생성에 적합하지만 운영 코드 변경·검사·적용까지 수행하는 worker를 대체하지 못한다. 격리 workspace-write worker → 실제 검사 → 독립 검토 → 허용된 반영 → 관측/복구를 별도로 연결해야 한다. read-only를 무차별 full access로 바꾸는 방식은 해결책이 아니다.

## 3. 수정 우선순위와 수락 기준

| 순서 | 작업 | 수락 기준 |
|---|---|---|
| 1 | Sol 전용 정책 | 모든 GPT 경로·resume에서 Sol 명시, 다른 GPT 요청 차단, 실제 모델 확인 또는 미확인 표시 |
| 2 | Gemini 결제/모델 경로 | 실제 사용 인증·프로젝트·Paid 상태·응답 모델 대조. 모델 설정과 과금 상태 분리 |
| 3 | 오류 원장 | Claude 실패 원인과 Gemini quota 오류의 구조화 근거 확보. health refresh가 오류를 지우지 않음 |
| 4 | 큐 분리 | Gemini 대기 중 다른 독립 목표/검사가 진행되는 장애 주입 시험 |
| 5 | 자동 조정 루프 | NEEDS_RECONCILIATION → 증거 보완 → 재검토, 무증거 완료 금지 |
| 6 | 실제 worker | fixture의 결함 발견→수정→검사→독립 검토→산출물까지 실제 run ID로 연결 |
| 7 | 지속 운영 | 24시간 관측에서 목표별 진전·대기 이유·재개·중복 방지 확인 후 7일 평가 |

'쉬지 않고 개선'은 무의미한 모델 호출을 계속하는 것으로 측정하지 않는다. 개선이 필요한 목표는 큐에 유지하고 가능한 독립 작업을 수행하며, 외부 한도 때문에 대기하면 다음 재개 조건을 보존한다. 지표는 목표별 마지막 실제 진전, 유효 실험/검사 수, 대기 시간, 동일 오류 반복, 변경 후 회귀, 한도 사용량이다. 공급자 전체 장애에서도 무중단 추론을 보장할 수는 없다.

## 4. 후속 구현자 지시문

> 이 문서의 D01~D07을 최신 코드에서 확인한 뒤 수정하세요. GPT는 gpt-5.6-sol만 허용하고 Astra/mini/자동 기본 모델을 사용하지 마세요. SDK 자체는 최근 성공 증거가 있으므로 전면 재설치부터 시작하지 마세요. Gemini 키 전달 변경이 이미 있는지 보존하고, 실제 인증·과금 프로젝트·모델을 확인하세요. 현재 auto_continue가 꺼진 목표들과 전역 미완료 잠금 때문에 지속 실행이 안 되는 문제를 해결하세요. 이전의 허위 완료 차단을 제거하는 대신 증거 보완·재검토를 연결하세요. Claude 오류 원문을 attempt에 보존하고 반복 실패를 제한하세요. 운영 전 격리 fixture와 장애 주입으로 검증하고 모델/SDK/CLI 버전·검사·산출물 지문·복구 증거를 남기세요. 실자금 연결 보류는 유지하세요.

## 5. 소스 근거 및 한계

- `antigravity_workspace/execution/codex_sdk.py`: 모델/세션/오류/heartbeat 기록, 성공 artifact.
- `antigravity_workspace/memory/state_ledger.sqlite3`: SDK run 읽기 전용 조회.
- `codex/ceo-briefing-platform/backend/strict_agi_orchestrator.py`: 모델 전달, Gemini 설정, 5시간 기본 대기, 전역 큐 잠금, 최종 게이트, health probe.
- `codex/ceo-briefing-platform/backend/data/strict_agi_state.json`: 목표별 자동 실행 여부와 현재 대기 단계.
- `antigravity_workspace/llm_client.py`: 별도 API fallback 모델.
- `.env` 두 파일: 허용목록의 모델명과 키 존재·중복 여부만 확인. 원본 비밀값 미기록.

Gemini 결제 콘솔, 실제 프로세스의 전체 환경변수, Claude 최신 실패 stderr는 확보하지 않았다. 현재 소스와 실행 중 프로세스가 같은 버전인지도 확정하지 않았다. 기존 COMPLETED 상태는 이번에 목표별 재인증하지 않았다. 따라서 계정 과금 원인·Claude 실패의 세부 원인은 미확인으로 남긴다.
