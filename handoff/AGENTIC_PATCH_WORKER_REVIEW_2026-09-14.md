# 코드 적용 워커 검수 및 대체 경로 — 2026-09-14

상태: 명시적 패치 적용 경로 검증. 자율 에이전트 실행·서비스 연결·자동 배포는 미구현/비활성.

사용자 요청은 Claude의 막힌 구현을 해결하는 것이다. 기존 핸드오프에 기록된 Create Unsafe Agents 차단 경로는 실행하지 않았으며 Claude/Codex 권한 설정을 바꾸지 않았다. 이 세션에서 Claude 분류기의 원본 이벤트나 `/permissions`로 해결 가능하다는 주장은 검증하지 못했다. 다른 실행 환경에서 같은 차단 경로를 다시 실행하는 것을 해결책으로 삼지 않았다.

## 확인한 코드 결함
- worktree는 동일 Git 저장소 메타데이터를 공유한다. 완전한 보안 격리가 아니다.
- 에이전트 실패 후에도 git add/commit이 실행되고 실패한 변경이 승격될 수 있었다.
- 승인 함수는 main 여부·검토한 SHA·기준 커밋·작업 트리 상태를 확인하지 않고 현재 브랜치에 merge했다.
- 작업 ID를 경로/브랜치로 검증 없이 사용했다.
- git 명령 오류를 일부 무시하고 삭제 실패에도 REJECTED를 반환했다.
- diff를 12,000자로 잘라 사람이 전체 변경을 검토할 수 없었다.
- 시간 제한 인자가 실제 SDK 요청에 반영되지 않았다.

## 현재 구현
`backend/services/agentic_apply_worker.py`:
- `stage_patch(task_id, patch_text, allowed_paths=[...])`는 호출자가 제공한 정확한 패치만 처리한다. 자연어로 새 작업을 생성하거나 모델/생성 코드를 실행하지 않는다.
- committed HEAD를 독립 Git clone으로 복사한다. 원본 미커밋 작업은 제외되며 manifest에 기록한다. 원본 refs와 Git 메타데이터를 수정하지 않는다.
- 파일 수 20개, 패치·전체 diff 각각 UTF-8 256,000바이트 상한. 정확한 경로 목록, 경로 이탈/설정·비밀 파일/심볼릭 링크/실행 파일 출력 제한.
- hook 실행을 끄고 Git 전역/시스템 설정 및 상속된 Git 환경변수의 영향을 차단한다.
- 적용 실패는 failure.json으로 남겨 승인 불가. 성공 시 proposal.patch 전체와 proposal.json의 기준 SHA·diff SHA256·파일 목록을 보존한다. 이것은 코드 의미/테스트 통과 판정이 아니다.
- `approve_change(task_id, expected_sha256=...)`는 원본 HEAD, 복사본 HEAD, index/작업 트리, 보관 patch의 일치를 검사한다. 성공 상태는 APPROVED_FOR_MANUAL_INTEGRATION이며 merge나 deploy를 수행하지 않는다.
- `reject_change`는 결정만 기록하고 증거를 보존한다. 동일 제안의 결정은 x 모드 파일 생성으로 한 번만 기록할 수 있다.
- 기존 `apply_change_in_isolated_worktree(task_id, prompt)`는 명시적 오류를 반환한다. 기존 자율 호출이 우연히 새 경로로 전환되지 않는다. 현재 저장소에서 이 모듈을 호출하는 서비스 연결은 검색되지 않았다.

이 구조의 격리는 원본과 Git 메타데이터 분리다. OS 보안 샌드박스나 적대적 로컬 사용자 방어를 주장하지 않는다. 승인 함수는 신뢰된 로컬 호출자용이며 사용자 인증 기능이 아니다. HTTP/Telegram 연동 시 소유자 인증, 작업 ID·base SHA·diff SHA에 묶인 동의, 테스트 증거 및 배포 단계가 별도로 필요하다.

## 사용 계약
```python
from services.agentic_apply_worker import stage_patch, approve_change

# patch_text는 먼저 작성한 Git diff 전체. allowed_paths는 신뢰된 호출자가 확정.
proposal = stage_patch('review-001', patch_text, allowed_paths=['path/to/file.py'])
# 전체 proposal.diff_text와 별도 테스트 결과를 확인하고, 실제 승인을 받은 호출자만:
# approve_change(proposal.task_id, expected_sha256=proposal.review_sha256)
```

승인 전엔 운영 원본이 바뀌지 않으며 승인 기록 후에도 자동 변경되지 않는다. 따라서 이 결과를 기존에 요청한 상시 자율 수정·승인·자동 배포 전체가 구현된 것으로 읽으면 안 된다.

## 검증
실행 위치: codex/ceo-briefing-platform/backend
명령: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_agentic_apply_worker -v`
결과: 11 tests, OK, exit 0. 실 Git init/clone/apply와 파일 변경을 임시 디렉터리에서 수행했다. 모델 호출이나 운영 서비스/DB 변경은 없다.
검사: 원본/다른 세션 파일 보존, 독립 .git, 다른 현재 브랜치 보존, 승인 시 미병합, 중복 결정 차단, 잘못된 해시/보관 패치 변조 차단, staged/unstaged 변조 차단, 원본 HEAD 이동 차단, 실패한 패치 승인 차단, 경로 허용 목록과 순회 차단, 작업 충돌 차단, 크기 상한, 기존 자율 진입점의 subprocess 미호출.

## 아직 남은 별도 범위
- 자율 에이전트 실행은 비활성이다. 기존 안전장치 차단은 해제하지 않았다.
- 텔레그램 승인 연결, 운영 자동 병합/배포, 전역 리포트 확장은 이번 모듈 수정에 포함하지 않았다.
- Gemini settings.json 부재만으로 OAuth 로그인 이력이 전혀 없었다고 단정할 수 없다. 인증 상태 확인이나 로그인 설정 변경은 이번 작업에서 수행하지 않았다.

부가 검사: 저장소 전체 git diff --check는 이번에 수정하지 않은 main.py/frontend 등의 기존 공백 오류로 실패했다. 이번 변경 파일의 공백 검사는 별도로 통과했다.
