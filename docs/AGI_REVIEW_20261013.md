# Hermes AGI 작업 독립 검토 — 2026-10-13

검토자: Codex. 대상: 현재 runtime의 AGI 계약·저장/조회·블로그 어댑터 코드와 테스트, P0 통제 기록. 미커밋 변경의 작성자를 Git만으로 모두 확정할 수 없으므로 ‘Hermes 관련 인계에 등장하는 현재 산출물’ 기준으로 검토했다.

**판정: 기반 구현은 확인됨. 실수집·공개 노출 확대 전 R1~R4 수정 및 재검증 필요. 전체 계획 완료나 운영 승격으로 볼 수 없다.** 이번 검토는 코드 수정·운영 전환 승인이 아니다.

## 1. 현재 확인한 진행 상태

- AGI-00 시스템 조사, AGI-01/09 계약 초안, AGI-02 연구 저장/조회 기반, AGI-03 블로그 어댑터·HTTP 정책 계층·합성 목록 파서가 존재한다.
- 실제 운영 프로세스 PID **37582**, CWD `/Volumes/Realtek_NVME/stock_dashboard/runtime`, `uvicorn main:app --host 127.0.0.1 --port 8000` 확인.
- PostgreSQL `stock_dashboard`를 `transaction_read_only=on`으로 조회했다. migration `0001_research_core` 원장 **1행**, 저장 checksum과 현재 코드 checksum 일치.
- `research_sources`, `research_documents`, `research_document_revisions`, `ingestion_runs` **각 0행**. AGI 실수집 데이터가 있다는 증거는 없다.
- 로컬 `/api/agi/status` HTTP **200**, `/api/agi/documents` **200 / applied=true / count=0**, 무인증 `/api/agi/admin/documents` **401**.
- 따라서 문서의 ‘미적용·재시작 전 404’는 현재 상태가 아니다. 누가 어느 시각에 재시작했는지는 이번 조회로 확인하지 않았다.
- AGI 기존 테스트 **243 passed, 12 subtests passed / 33.51초** 재현. 최초 sandbox에서는 로컬 DB/HTTP 접근 제한으로 실패했으며, 허용된 로컬 접근으로 재실행한 결과가 이 수치다.
- 추가 재현은 합성 데이터와 일회용 `agi_slice_test_*` 스키마만 사용. 검증 후 잔여 테스트 스키마 **0개**. 운영 public 데이터 쓰기·실수집·외부 발송·재시작 없음.

## 2. 수정 필요 사항 — 전부 실제 재현

### R1 [P1] 메타데이터만 저장하도록 설정해도 본문을 저장함

위치: `agi/adapters/blog.py:315`(수집 진입), `:560`(본문 저장); 공통 저장 경로 `agi/repository.py:404`. 텔레그램 서비스도 별도의 `store_original` 검사 없이 본문 저장을 호출한다.

새 소스의 기본 `store_original='metadata_only'` 상태에서 `ingest_page()`를 실행하면 `research_document_revisions.body_text`에 본문 1행이 저장된다. 소스에 정의한 원문 보관 권한이 실제 수집 경계에서 강제되지 않는다. `no`도 같은 경로에서 차단되지 않는 코드 구조다(실측은 metadata_only).

수정 방향: fetch와 원문 저장 전에 권한을 평가한다. `metadata_only`는 허용된 목록 메타데이터만, `no`는 계약에서 정한 최소 감사 정보만 취급하도록 정의하고, 공통 저장 계층에도 우회 방지 검사를 둔다. 저장 허용/불허별 테스트와 레거시 이관 경로를 함께 검사한다.

### R2 [P1] A→B→A 수정 시 현재 본문이 B로 남음

위치: `agi/repository.py:454–461`.

이미 존재하는 해시를 만나면 기존 revision 번호를 반환하고 끝나므로 현재 revision 포인터가 갱신되지 않는다. 동일 글을 A, B, A 순서로 수집했을 때 현재 본문은 **B**, 실제 마지막 관측은 **A**로 재현됐다. 기업 보고서·검색·변경 감지에서 낡은 내용을 최신으로 사용할 수 있다.

수정 방향: 내용 해시 중복 제거와 관측 이력을 분리한다. 과거 내용으로 되돌아와도 현재 포인터를 갱신하고 복귀 관측 시점을 남긴다. 해시 고유 제약을 유지한다면 별도 관측/전이 이력으로 A→B→A 순서를 복원해야 한다. 연속 A→A 중복과 A→B→A 복귀를 다른 테스트로 검증한다.

### R3 [P1] 기존 공개 글의 상세 응답이 비공개로 바뀌어도 공개 조회에 남음

위치: `agi/adapters/blog.py:475–506`, `agi/repository.py:380–383`.

공개 글을 먼저 저장하고 소스의 공개 권한을 `allowed`로 설정한 뒤, 같은 글의 HTTP 200 상세 응답에 `PostDetail(is_private=True)`를 전달했다. 결과는 **is_private=false 유지 + access_state=ok + 공개 조회에 계속 존재**였다. 목록에 비공개 표시가 있을 때의 경로는 처리하지만 상세 응답에서만 확인된 비공개 표시는 처리하지 못한다.

원인은 upsert가 기존 `is_private`를 갱신하지 않고, 어댑터가 상세의 비공개 표시를 확인한 뒤에도 본문 저장과 `ok` 전환을 실행하기 때문이다. 현재 API가 본문 전체를 반환하는 것은 아니지만 문서 메타데이터·revision 정보가 공개 경로에 남고 비공개 본문은 DB에 저장된다.

수정 방향: 상세의 비공개 판정을 저장 전에 처리하고 `restricted`로 전이한다. 공개→비공개 승격은 강제하되 역방향은 별도 정책으로 처리한다. 목록 표시·상세 표시·HTTP 401/403·기존 tombstone 각각의 경로를 검증한다.

### R4 [P1] 마지막 페이지를 전체 목록으로 오해해 앞 페이지 글을 삭제 처리함

위치: `agi/adapters/naver_blog_listing.py:367–368`, `agi/adapters/blog.py:580–604`.

목록 HTML의 `isLastPage=true`만으로 `complete=True`가 된다. 이 단일 페이지를 어댑터에 전달하면 source 전체 문서 중 해당 페이지에 없는 글을 삭제 상태로 바꾼다. 이전 페이지의 합성 글을 먼저 저장하고 마지막 페이지 글 1개를 전달한 결과, 이전 글의 `access_state`가 **deleted**로 변했다. 원문 행 자체 삭제는 아니지만 공개 조회·후속 분석에서 살아 있는 글이 사라지는 오류다.

수정 방향: ‘마지막 페이지 도달’과 ‘전체 소스의 완전한 목록 확보’를 별도 필드로 분리한다. 동일 수집 세대의 전체 페이지 ID를 모으고, 모든 페이지 성공·범위 일치·잘림 없음이 입증된 경우에만 소스 전체 삭제 스윕을 실행한다. 부분 페이지·마지막 페이지만 재수집·중간 페이지 실패·카테고리 제한·기간 제한 사례도 검사한다.

## 3. 테스트와 재현 증거

기존 테스트가 위 네 가지 상황을 다루지 않아 전체 통과와 결함 재현이 동시에 성립한다. 기존 테스트 통과만으로 운영 준비 완료라 판단하면 안 된다.

```bash
./venv/bin/python -m pytest tests/test_agi_research_core_migration.py tests/test_agi_research_ingestion_contracts.py tests/test_agi_documents_api.py tests/test_agi_blog_adapter.py tests/test_agi_blog_http_fetcher.py tests/test_agi_collection_policy.py tests/test_agi_naver_blog_listing.py -q --tb=no
PYTHONPATH=. ./venv/bin/python scripts/review/reproduce_agi_review_20261013.py
```

두 번째 명령은 운영 PostgreSQL의 public이 아닌 일회용 테스트 스키마에만 합성 데이터를 쓰고 정리한다. 운영 수집용 스크립트가 아니다. 관측 결과를 출력하는 진단이며 수정 완료 수용 테스트는 별도로 추가해야 한다.

```text
R1 store_original= metadata_only stored_body_rows= 1
R2 A->B->A current_body= B expected=A
R3 detail.is_private=True public_visible= True is_private= False
R4 last_page complete= True previous_page_post_state= deleted
```

## 4. 문서·인계 보완

- `Open_Items_AGI.md` 서두/말미의 ‘아직 구현 없음’, ‘모든 작업 planned’는 뒤쪽 구현 기록과 불일치한다. 이번에 현재 상태와 검토 기록으로 정리했다.
- AGI-03 행은 planned였지만 실제 어댑터·테스트·합성 목록 파서가 존재한다. `in-progress`로 정리하되 원천 실제 응답 검증·수집·원문 파일 아카이브 완성을 의미하지 않는다.
- AGI-02 로그에 `public` 적용 후와 적용 전 수치가 시간순으로 혼재한다. 과거 기록을 삭제하지 않고 §1의 현 시점 실측을 우선 참고한다.
- AGI 코드·테스트 상당수가 미추적 상태다. 정식 인계 시 허용된 파일만 분리해 변경 범위·기준 commit을 확정해야 한다. 이번에는 커밋하지 않았다.
- 최신 상세 계획은 최우선 P0-SREIG 승격 검증을 요구한다. `CONTROL_RECORD_P0_SREIG_20261011_02.md`는 implementing 상태지만, 지정 worktree를 읽어 보니 HEAD는 고정 base `d08d692ad72665bdd26adcebce90740bb7241cc2`이며 `git status --short` 출력이 비어 있다. **그 지정 후보에는 현재 구현 diff가 없다.** 다른 worktree/현재 runtime의 코드로 이 후보 완료를 대체하지 않았다. P0 최종 승인은 확인되지 않았다.

## 5. 다음 작업 인계

이번 검토의 담당은 Codex이며 산출물은 이 검토서·R1~R4 재현 스크립트·백로그 반영이다. 검토는 완료했으며 새 기능 착수나 외부 에이전트 메시지를 발송하지 않았다.

다음 독립 작업 제안의 담당: **Code Doer/구현 담당**. 산출물: R1~R4를 고정하는 합성 회귀 테스트와 수정 설계. 차단 조건: 최신 계획의 P0 우선순위·격리 후보 경계 및 해당 수정 범위를 먼저 확정해야 하며 운영 수집은 계속 분리한다. 승인된 수정 착수 뒤에는 위 재현 명령과 AGI 7파일 테스트를 다시 실행하고, 권한/삭제 오판 0·A 복귀 이력을 독립 검토한다.

P0의 다음 담당: **Planner**. 먼저 지정 worktree의 후보 산출물 부재를 정리하고, 신규 구현자에게 정확한 허용 범위·제출 위치·검증 명령을 연결한다. 재확인 명령은 해당 통제 기록의 고정 `git status`, `rev-parse HEAD`, Python/UI fixture 테스트다. 이 보고서가 임의로 기존 P0 승인·운영 동결을 해제하지 않는다.
