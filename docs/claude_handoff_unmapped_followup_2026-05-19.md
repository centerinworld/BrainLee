# Telegram Unmapped 후속 조치 핸드오프 (재검증 요청용)

작성일: 2026-05-19 (KST)
작성자: Codex
원칙: 본 문서는 확정 결론이 아닌 "이렇게 보이며 재검증이 필요"한 제안 정리.

## 1) 이번 조치 내역

### 코드 변경
1. `hs_trade_lab/scripts/rebuild_telegram_flow_mappings.py`
- `parse_flow_and_product()`
  - 회사명 prefix(`회사: 품목`) fallback 파싱 추가
  - 지역/국가 scope regex 확장 (일본/대만/홍콩/핀란드/모로코/인도네시아 등)
- `hs_entries_for_post()`
  - 회사명 제거한 `core_title` 기반 HS 탐색 추가
  - 지역/국가/행정구역 괄호 제거 규칙 확장
- 매핑 성공 시 `telegram_post_cache.mapping_status='mapped'` 동기화 추가

2. `hs_trade_lab/scripts/backfill_telegram_posts.py`
- `core_product_label()` 신규
  - 회사 prefix + 지역괄호 제거한 핵심 품목 라벨 생성
- `find_matches()`
  - 기존 `title/text` 외 `core_title` 매칭 추가
- `COMPANY_ALIAS_MAP`, `TITLE_ALIAS_MAP` 보강
  - 반복 unmapped 상위 패턴 일부 반영

## 2) 수치 변화 (실행 결과)

기준(조치 전):
- mapped: 14,533
- unmapped: 1,096

조치 후:
- mapped: 14,915
- unmapped: 714

개선 폭:
- unmapped 382건 감소 (약 -34.9%)

관련 파일:
- 기존 전체: `scratch/telegram_unmapped_1096_full.tsv`
- 조치 후 전체: `scratch/telegram_unmapped_after_patch.tsv`

## 3) 현재 남은 unmapped 패턴 (재검증 우선)

반복 상위(예시):
- `바우와우코리아 (오에스피 자회사)`
- `펨트론`
- `파크시스템스 : 산업용자동화원자현미경 (경기 수원)`
- `제룡전기 : 변압기 (서울 광진구)`
- `일진전기 : 변환기 (경기 화성시)`
- `에코프로비엠, LG화학 : NCA (...)`
- `LG화학/LG디스플레이/덕산네오룩스 : OLED 제조용 ...`

관찰 의견(확정 아님):
- 일부는 alias가 있어도 hs_sector_map의 display_name/동의어와 정확히 연결되지 않아 미매핑으로 남는 것으로 보임.
- 일부는 회사명만 있는 제목(품목이 본문 2행)이라 backfill 단계에서 title 기준 탐지 누락 가능성이 있어 보임.

## 4) 클로드에게 제안하는 다음 단계

1. P1(회사명:제품명) 우선
- 상위 반복 50개 제목에 대해 `TITLE_ALIAS_MAP` 정규화 확장
- 회사 prefix 제거 + 본문 2행 제품 라인 강제 사용 로직 추가 검토

2. P2(지역변형) 확장
- `(...시/군/구)` 외 `..._글로벌`, `..._미국+중국` 복합 suffix normalize 규칙 공통화

3. P3(노이즈) 분리
- `품목합`, 공시 요약성 제목, 비수출입 메시지 등을 `noise/skip`로 별도 상태 관리 검토

4. 검증 방법
- 변경 전/후 `unmapped` 건수 + 상위 반복 제목 TOP40 비교
- `flow_posts_with_hs_and_company` 증가 여부 확인
- 잘못 매핑(도메인 충돌) 샘플 100건 수동 확인

