# 수출입분석 데이터 정밀도 점검 핸드오프 (for Claude)

작성일: 2026-05-18 (KST)
작성자: Codex (검토 전용, 데이터 미변경)
대상 DB: `hs_trade_lab/data/hs_trade_lab.db`

## 1) 작업 원칙
- 본 문서는 **확정 결론이 아닌 검토 제안서**입니다.
- 아래 항목들은 "이렇게 보이며, 클로드 재검증 후 적용하는 것이 맞아 보인다"는 관점으로 정리했습니다.
- 실데이터 수정/삭제/추가는 수행하지 않았습니다.

## 2) 범위 확장 검토 결과 (2025-10 ~ 2026-05)
- `telegram_post_cache` 전체: 15,629건
- 기간: `2025-10-15` ~ `2026-05-15`
- 월별 unmapped:
  - 2025-10: 108
  - 2025-11: 138
  - 2025-12: 114
  - 2026-01: 130
  - 2026-02: 141
  - 2026-03: 115
  - 2026-04: 146
  - 2026-05: 204

관찰 의견(재검증 필요):
- unmapped가 특정 월만의 문제가 아니라, 월별로 반복되는 제품명 패턴 누락이 누적된 것으로 보입니다.

## 3) 우선 검토가 맞아 보이는 이슈

### A. 항공/방산 제목과 선박엔진 라벨의 동시 매칭
관찰:
- `비행기의 부분품`/`헬리콥터의 부분품` 제목에서 `matched_hs_codes_json`에 선박추진용 엔진 계열이 함께 잡힌 케이스가 다수 보입니다.
- 집계 샘플:
  - 비행기의 부분품 + 선박추진용 패턴: 60건
  - 헬리콥터의 부분품 + 선박추진용 패턴: 58건

의견(확정 아님):
- `rebuild_telegram_flow_mappings.py`의 라벨 재확장 과정에서 도메인 충돌 라벨이 누적될 가능성이 있어 보입니다.

클로드 제안:
1. 항공/방산 키워드 문맥에서 선박엔진 라벨은 우선 차단하는 방식 검토
2. `matched_hs_codes_json` 기반 후보와 `product` 기반 후보를 분리 평가
3. 적용 전후 충돌건수 비교 쿼리로 회귀검증

---

### B. 지역/국가 변형 수입 제목 파싱 누락
최근(2026-05) 사례:
- `수입(충남 천안시) 무수불산`
- `수입(일본_경기 화성시) NCF(Non Conductive Film)`
- `수입(경기 이천시) MR-MUF(Underfill)`
- `수입(글로벌_경기 이천) 반도체 조립용 인캡슐레이션(몰딩장비)`

과거월에도 반복 관찰:
- 디램/디램모듈(전국_국가 변형)
- CMP/솔더볼
- 레이더 지역 변형 제목

의견(확정 아님):
- canonical product normalization이 지역/국가 접미 변형에 충분히 강건하지 않은 것으로 보입니다.

클로드 제안:
1. 제목 전처리에서 지역/국가 패턴 제거 규칙 강화
2. NCF/Non Conductive Film, MR-MUF/Underfill 등 동의어 사전 확장
3. `posted_at >= '2025-10-15'` 범위로 재매핑 dry-run 후 증가율 비교

---

### C. 동일 콘텐츠 중복 적재 가능성
관찰:
- `message_id`는 다르지만 `title+raw_text`가 사실상 동일한 쌍이 반복됩니다.
- 특히 월별 잠정/확정 게시 반복 구간에서 알림 중복 체감 가능성이 높아 보입니다.

의견(확정 아님):
- 현재 키가 `message_id` 중심이라 동일 본문 재게시를 별건으로 흡수할 수 있어 보입니다.

클로드 제안:
1. `content_hash(normalized_title + normalized_raw_text + period_token)` 도입 검토
2. 카드/알림 표출 시 hash 단위 최신건 우선 정책 검토

## 4) provisional -> exact 승격 후보 (신중 검토)

### 4-1. 승격 후보로 "검토해볼 만한" 목록
파일: `scratch/provisional_exact_candidate_shortlist.tsv` (12건)

예시:
- 2103901030 고추장
- 2309101000 개사료
- 2005991000 김치
- 7017100000 쿼츠
- 8507100000 시동용 연산축전지

주의 의견:
- `가장 가까운`, `유사`, `1:1 exact 아님` 문구가 note에 있는 항목은 provisional 유지가 더 맞아 보입니다.
- 따라서 일괄 승격보다, 근거 강한 항목만 선별 승격하는 방식이 안전해 보입니다.

## 5) 오래된 텔레그램까지 반영한 추가 검토 포인트
(2025-10~12 샘플 기반)
- `품목합` 제목은 매핑 대상이 아니라 요약/집계성 포스트일 가능성이 높아 보임
- 디램/낸드/디램모듈(전국_국가 suffix) 계열은 정규화만 보완해도 unmapped 감소 가능성이 있어 보임
- `스마트레이더시스템 : 레이더 (...)`처럼 회사 prefix + 지역 suffix 패턴은 본문 제품명 분리가 필요해 보임
- `바우와우코리아 (오에스피 자회사)`처럼 회사명/설명 혼합 제목은 별도 템플릿 파싱이 필요해 보임

## 6) 클로드 실행 권장 순서 (검증 우선)
1. 파서/정규화/도메인필터 변경안을 feature branch에서 반영
2. dry-run 리포트 생성
   - 지표: unmapped 감소, 도메인충돌 감소, 중복표출 감소
3. 2025-10~2026-05 전체 재처리
4. 샘플 100건 수동검증
5. 이상 없으면 운영 반영

## 7) 참고 산출물
- `scratch/telegram_recent_120.tsv`
- `scratch/telegram_unmapped_80.tsv`
- `scratch/telegram_unmapped_400_latest.tsv`
- `scratch/unmapped_monthly_samples.tsv`
- `scratch/provisional_exact_candidate_shortlist.tsv`
- `scratch/telegram_exact_duplicates_50.tsv`

## 8) 참고 SQL
```sql
SELECT substr(posted_at,1,7) ym, COUNT(*) total,
       SUM(CASE WHEN mapping_status='unmapped' THEN 1 ELSE 0 END) unmapped
FROM telegram_post_cache
GROUP BY ym ORDER BY ym;

SELECT COUNT(*)
FROM telegram_post_cache
WHERE title LIKE '%비행기의 부분품%'
  AND matched_hs_codes_json LIKE '%선박추진용%';

SELECT COUNT(*)
FROM telegram_post_cache
WHERE title LIKE '%헬리콥터의 부분품%'
  AND matched_hs_codes_json LIKE '%선박추진용%';
```
