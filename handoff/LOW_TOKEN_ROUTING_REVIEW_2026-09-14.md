# 저사용량 Agentic AI 라우팅 보완

2026-09-14. 사용자의 'Gemini·DeepSeek 주력, Claude·GPT 핵심만, GPT는 Sol만' 요구에 따른 코드 변경 기록.

## Claude 사용량이 늘어나는 구조

기존 strict 파이프라인은 모든 작업마다 GPT 계획, Claude 보강, GPT 최종 검토를 요구했다. GPT 한도가 소진되면 계획도 Claude로 넘겼다. `_prior`는 앞선 단계 출력 전체를 누적 전달했고 Claude는 high effort·최대 4 turn으로 호출됐다. 같은 단계 오류도 반복됐다. 이들은 코드상 사용량 증가 요인이다. Claude 계정 전체 중 해당 시스템의 정확한 기여도나 실제 절감률은 원장만으로 산출할 수 없으며 추정 수치를 제시하지 않는다.

## 반영 내용

- 1·2단계: Gemini 계획·근거 수집. 3·4단계: DeepSeek 분석·초안·보강. 두 공급자 중 하나가 실패하면 다른 쪽만 대체 호출한다.
- 일반 작업은 Claude/GPT를 호출하지 않고 DRAFT_READY로 산출물을 보존한다. 이 상태는 목표 구현·배포 완료가 아니다.
- 명시적 core_review_reason(production_change, security_change, strategy_promotion, unresolved_material_conflict)이 있는 작업만 4단계 Claude, 5단계 Sol로 검토한다. 현재 자동 목표 생성은 기본 일반 작업이며 중요 변경을 자동 반영하지 않는다. 중요 작업 승격을 외부 API/UI에 연결하는 부분은 별도 구현이 필요하다.
- Claude 계획 fallback은 비활성화했다. 핵심 입력은 provenance와 최신 발췌를 포함한 6,000자 이하 요약으로 제한하고 부분 증거임을 명시한다. Claude는 medium effort, 최대 1 turn으로 변경했다. 이는 정확한 토큰 한도나 추가 도구 컨텍스트 전체 상한은 아니다.
- 핵심 호출은 공급자별 하루 4회 시도 상한(초기 보수적 정책)과 동일 task/stage/evidence 중복 예약 차단을 적용했다. 실패도 시도에 포함한다. 다중 프로세스 운영에서는 기존 JSON 원장 lock의 한계가 있어 단일 monitor만 운용해야 한다.
- 핵심 호출 실패는 재호출 반복 대신 WAITING_REPAIR. 일반 단계는 3회 실패 후 같은 상태로 보존한다. 자동 복구 worker는 아직 별도 구현 항목이다.
- WAITING_QUOTA/조정 대기가 독립 목표 생성을 전부 막지 않도록 수정하고 오래된 실행 가능 작업을 먼저 선택한다. 물리적 실행은 동시에 한 작업만 수행한다.
- strict SDK 요청과 공통 ExecutionRequest는 gpt-5.6-sol을 명시한다. SDK 요청의 다른 GPT 모델은 거부한다. llm_client의 Codex CLI 호출에도 Sol을 명시했다. 기존 별도 OpenAI raw API 경로 전체를 재구성한 것은 아니므로 해당 경로는 별도 감사가 필요하다.

## 검증

strict 파이프라인 22개, SDK 계약 7개: 총 29개 통과. 일반 작업의 premium 호출 0회, Gemini/DeepSeek fallback, 일일 시도 상한과 재시작 후 보존, 중복 검토 차단, 핵심 오류 반복 방지, 독립 목표 진행, Sol 시작/재개 및 비Sol 거부를 검사했다.

첫 테스트 실행에서는 기존 테스트가 바뀐 라우팅을 mock하지 않아 외부 호출 경로까지 진입할 수 있는 격리 결함이 드러났다. 이후 테스트에 subprocess/urlopen 기본 차단을 추가하고 정책이 변경된 테스트를 갱신했다. 최종 검증은 모의 공급자와 임시 원장으로 수행했다. 실제 사용량 절감 실측은 하지 않았다.

재현:

```sh
python3 -m unittest discover -s codex/ceo-briefing-platform/backend -p test_strict_agi_orchestrator.py
python3 -m unittest discover -s antigravity_workspace/tests -p test_codex_sdk_integration.py
```

## 24시간 운영에 남은 것

이번에 운영 프로세스를 재시작하거나 목표 auto_continue 값을 바꾸지는 않았다. 현재 메모리에 로드된 이전 실행 코드에는 변경이 적용되지 않았을 수 있다. 안전한 현재 단계 종료 후 단일 프로세스로 배포하고 버전·effective policy를 확인해야 한다.

남은 작업: ① 사용자 목표별 auto_continue 정합성 ② Gemini 유료 인증/프로젝트 및 실제 한도 확인 ③ 반복 오류의 원인별 복구 worker ④ 격리 수정·기계 검사·독립 검토·허용 반영 ⑤ 24시간 관측이다. DRAFT_READY와 NEEDS_RECONCILIATION을 임의로 COMPLETED로 바꾸지 않는다. GPT/Claude 한도가 막혀도 일반 조사·검사 업무는 진행하되 핵심 승인 조건은 낮추지 않는다. 연속 모델 호출 횟수보다 실제 진전과 검증된 산출물을 운영 지표로 삼는다.

과거 진단: [Sol 전용 런타임 점검](</Volumes/Realtek_NVME/AI System/handoff/AGENTIC_RUNTIME_DIAGNOSIS_SOL_ONLY_2026-09-14.md>).
