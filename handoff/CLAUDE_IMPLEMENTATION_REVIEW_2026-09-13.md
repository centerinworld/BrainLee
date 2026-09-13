# Claude 구현 검수 및 보완 결과

검수일: 2026-09-13. 최신 작업 트리에 다른 작업의 변경이 존재하므로 해당 변경을 되돌리지 않고 확인한 결함에 한정해 수정했다. 검수 도중 strict 실행기의 증거 검사도 추가되어 최신 내용을 다시 읽고 보존했다. 운영 서비스 재시작, 실제 LLM 호출, Telegram 전송, 실제 매매는 하지 않았다.

## 판정

접수 이후 파이프라인을 호출하는 scheduler와 CEO의 별도 5단계 CLI/SDK 실행 경로가 존재한다. 따라서 '실행 코드가 전혀 없음'은 현재 상태와 다르다. 그러나 목표에 맞는 구현·테스트·증거 검증까지 완료된 연속 자율 실행 시스템으로는 인증할 수 없다.

## 보완한 결함

| 우선순위 | 발견 | 변경 |
|---|---|---|
| P0 | strict 실행기의 최종 REVISE 또는 기타 응답도 이전 단계 문서만 있으면 COMPLETED | 최종 verdict 첫 줄을 판별하고, PASS라도 실행 증거 게이트가 없으므로 NEEDS_RECONCILIATION에 보존. 해당 상태 자동 재실행 차단 |
| P1 | 두 목표 scheduler가 같은 PENDING을 읽고 중복 실행 가능 | SQLite BEGIN IMMEDIATE로 상태 비교·선점을 원자적으로 수행. attempt ID 및 시작 시각 기록 |
| P1 | 완료 알림 함수가 False를 반환해도 notified_at 저장 | 성공에만 발송 시각 저장. SENDING 선기록 후 불명확 결과를 NEEDS_RECONCILIATION에 보존 |
| P1 | 대기 상태를 반환한 실행기도 완료 알림 처리 가능 | COMPLETED/FAILED만 결과 알림 대상으로 처리하고 비종결 결과는 원장에 보존 |
| P1 | strict 테스트 import가 운영 singleton과 감시 스레드를 시작 | 테스트에서 정의만 로딩하고 운영 singleton 시작 제외. 테스트 artifact를 인스턴스 임시 경로에 저장 |
| P2 | PM과 intake가 불필요하게 별도 인스턴스로 만들어짐 | scheduler의 intake에 동일 PM 주입 |
| P2 | 기존 QA만으로 사용자에게 목표 완료처럼 안내 | 결과 알림을 '파이프라인 종료·목표 검증 필요'로 구분 |

StateLedger의 일반 upsert도 읽기→병합→쓰기를 같은 쓰기 트랜잭션으로 묶어 동시 갱신 유실을 줄였다. 새로운 테이블이나 운영 DB 마이그레이션은 필요하지 않다.

## 남은 리뷰 항목

| 항목 | 현재 증거 | 남은 작업 |
|---|---|---|
| R01 | Antigravity SQLite와 CEO strict JSON 원장이 별개. strict에는 실제 호출 코드가 있고 최종 완료 오판은 이번에 차단 | 목표/task/run/evidence ID 매핑과 단일 완료 정책 필요. 기존 COMPLETED 기록의 실행 증거는 별도 감사 필요 |
| R05 | 목표 scheduler는 L1PMOwner의 두 고정 파이프라인으로만 위임. strict Claude는 permission-mode plan, Codex는 read-only | 사용자 원문과 수락 기준을 보존해 격리 구현·실제 검사 도구에 연결. 텍스트 5개 생성은 구현 완료가 아님 |
| R06 | scheduler가 큐를 읽고 실행하는 코드는 존재. atomic claim 보완됨. 중단 RUNNING은 자동 복구되지 않음 | lease/heartbeat·세션 대조·재개 정책, 독립 프로세스 인증 검증, 두 scheduler 중 작업 소유권 지정 필요 |
| R09 | knowledge_rag_engine.py가 DB 부재 시 seed_comprehensive_intelligence를 호출. seed 자료와 '상시 수집 가동' 표시 존재 | 원문 수집·문서 hash·수집시각·출처/실제 수집 상태·seed 구분을 구현하고 기존 자료 정체를 감사해야 함 |

Claude CLI는 Antigravity llm_client와 strict 실행기에 호출 코드가 존재하지만, goal_execution_scheduler에서 claude_cli로 직접 연결되는 일반 목표 실행 루프는 없다. 별도 프로세스에서 실제 구독 인증·한도·결과 회수 성공은 이번 검사에서 실행하지 않았다. '세션 밖이면 반드시 성공'이라는 보장도 불가능하다.

추가 관찰: 목표 intake의 허용 chat ID가 비어 있으면 허용 범위가 열리며, update offset은 처리 전 메모리에서 전진한다. 목표 ID는 초 단위이고 사용자 원문은 고정 파이프라인 payload에 보존되지 않는다. 독립 무인 실행을 가동하기 전에 인증된 발신자, 영속 update ID, 중복 접수, 원문/범위 보존을 보완해야 한다. strict JSON 원장은 프로세스 내부 lock만 사용하며 손상 파일을 blank 상태로 바꾸는 경로가 있어 다중 프로세스·손상 복구도 추가 검토가 필요하다.

## 동작 제한과 복구

- RUNNING에서 프로세스가 중단된 작업을 이 변경이 자동 재개하지는 않는다. 부작용을 확인한 뒤 상태를 조정해야 한다.
- SENDING에서 중단되거나 전송 결과가 불명확하면 알림은 자동 재전송하지 않는다. Telegram의 실제 수신 결과 확인 후 조정해야 하며 exactly-once 전송을 주장하지 않는다.
- strict의 NEEDS_RECONCILIATION 작업은 현재 전역 단일 미완료 작업 정책에 따라 뒤의 목표 진행도 막는다. 검증 없는 자동 완료 대신 미완료를 명시한 것이며, 다음 구현에서 기계 검사와 검토 후 승격 경로를 연결해야 한다.
- 실제 매매·운영 반영·서비스 재시작은 실행하지 않았다. 이 검수는 해당 기능의 운영 성공 인증이 아니다.

## 검증

네트워크/실제 AI 호출을 대체한 임시 DB·임시 artifact 환경에서 실행했다.

```sh
python3 -m unittest discover -s antigravity_workspace/tests -p test_goal_execution_scheduler.py
python3 -m unittest discover -s codex/ceo-briefing-platform/backend -p test_strict_agi_orchestrator.py
```

목표 scheduler 13개, strict 실행기 8개, 총 21개 통과. 경쟁 실행, 독립 SQLite 연결 간 선점, 알림 실패/예외, 중단된 발송, 대기 결과, PASS/REVISE/비정형 최종 응답, 증거 누락과 artifact 격리를 검사했다. 전체 저장소 diff 공백 검사에서는 이번에 수정하지 않은 기존 main.py·프론트 변경의 trailing whitespace가 발견됐다. 해당 파일 전체를 재포맷하지 않았다.

## 후속 구현 순서

1. intake 인증·중복 접수·원문 보존을 먼저 보강한다.
2. 두 원장을 연결하는 단일 task/run ID 및 실행 소유권을 확립한다.
3. 독립 프로세스에서 구독 CLI를 격리 fixture에 연결하고 세션·산출물·검사 결과를 회수한다.
4. 검토 초안과 실제 적용/검사 증거를 구분하는 게이트를 구현한다.
5. lease 만료·한도·인증·알림 결과 조정 후 자동 재개를 연결한다.
6. R09 원문 수집과 출처 계약을 구현한다.

실행 설계 정본: [통합 SDK 핸드오프](</Volumes/Realtek_NVME/AI System/handoff/AGENTIC_AI_LOCAL_SDK_INTEGRATED_HANDOFF_2026-09-12.md>).
