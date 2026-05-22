# 수출입분석 데이터 정밀도 점검 2차 핸드오프 (for Claude)

작성일: 2026-05-19 (KST)  
작성자: Claude Code (1차 핸드오프 분석 기반, 전체 1,096건 확장)  
대상 DB: `hs_trade_lab/data/hs_trade_lab.db`  
샘플 파일: `scratch/telegram_unmapped_1096_full.tsv` (전체 1,096건)

## 1) 작업 원칙

- 본 문서는 1차 핸드오프(claude_handoff_export_import_precision_2026-05-18.md) 후속입니다.
- 전체 1,096건 미매핑 레코드를 분석하여 **우선순위별 수정 방안**을 제시합니다.
- 아래 수정 제안은 확정이 아니며, 실제 적용 전 dry-run 및 샘플 검증이 필요합니다.
- 실데이터 수정은 이 문서의 실행 권장 순서(§7)를 따르되, 클로드가 재검증 후 적용합니다.

---

## 2) 전체 미매핑 현황 요약 (1,096건)

| 패턴 | 건수 | 비율 | 우선순위 |
|------|------|------|---------|
| E+B: 회사명 : 제품명 + 지역 변형 | 667 | 61% | **P1** |
| B: 지역 변형 (회사명 없음) | 383 | 35% | **P2** |
| F: 기타 (노이즈/특수 형식) | 24 | 2% | P3 |
| E: 회사명 : 제품명 (지역 없음) | 22 | 2% | P1 |

**핵심 발견**: 96%(E+B + E)는 제목에서 회사명 prefix 또는 지역 suffix만 제거하면 기존 HS 매핑 테이블로 해결 가능한 것으로 추정됩니다.

---

## 3) 우선순위 1 — 회사명 Prefix 파싱 (P1, ~689건)

### 3-1. 문제 패턴

```
제목 형식: "{회사명} : {제품명} ({지역})"
예시:
  "삼성전자 : 디램 (경기 평택시)"      → 제품명: 디램
  "대한광통신 : 광섬유 케이블 (전국)"   → 제품명: 광섬유 케이블
  "달바글로벌 : 기초화장품 (서울 마포구_글로벌)" → 제품명: 기초화장품
```

### 3-2. Top 미매핑 제품 목록 (P1 우선 처리 대상)

| 제품명 (정규화) | 건수 | 관련 회사 | 권장 HS 코드 | 비고 |
|----------------|------|----------|------------|------|
| 디램 | 129 | 삼성전자, SK하이닉스 | 854232 / 8542321010 | 회사 매핑 이미 존재 |
| 광섬유 케이블 | 95 | 대한광통신(61), 가온전선(18), LS전선(16) | 9001100000 | 회사 매핑 이미 존재 |
| 디램모듈 | 71 | 삼성전자, SK하이닉스 | 8473304060 | 회사 매핑 이미 존재 |
| 기초화장품 | 38 | 달바글로벌 | 330499 / 3304991000 | 달바글로벌 매핑 없음 → 신규 추가 필요 |
| 유기발광다이오드 OLED 제조용 | 32 | LG디스플레이, LG화학, 덕산네오룩스 | 8524911000 | 회사 매핑 이미 존재 |
| 솔더볼 | 26 | 덕산하이메탈 | 8311900000 | 회사 매핑 이미 존재 |
| CMP (웨이퍼 표면 연마장치) | 25 | 직접 제목 (회사 미노출) | — | raw_text에서 회사 추출 필요 |
| 리쥬란 | 20 | 파마리서치 | 3304991000 | 회사 매핑 이미 존재 |
| 엔진 | 17 | HD현대인프라코어 | — | 회사 매핑 확인 필요 |
| MLCC | 17 | 다이요유덴, 삼화콘덴서 | — | 회사 매핑 확인 필요 |
| 진주광택안료 | 16 | 씨큐브 | — | 씨큐브 매핑 없음 → 신규 추가 필요 |
| 유기혼합용제와 시너 | 16 | 엘티씨 | 848620 | 회사 매핑 이미 존재 |
| 변환기 | 16 | LS ELECTRIC, 일진전기 | — | 회사 매핑 확인 필요 |
| NCA (양극재) | 16 | 에코프로비엠, LG화학 | 2841909020 | 에코프로비엠 매핑 이미 존재 |
| 광섬유 (단품) | 9 | 대한광통신, 머큐리 케이블 | 9001100000 | — |

### 3-3. 수정 방안 제안

```python
# rebuild_telegram_flow_mappings.py 전처리 로직에 추가
import re

def normalize_title(title: str) -> str:
    """회사명 prefix + 지역 suffix 제거 후 제품명 추출."""
    # Step 1: "회사명 : 제품명" 패턴에서 제품명 추출
    if ' : ' in title:
        title = title.split(' : ', 1)[1].strip()
    
    # Step 2: "제품명 (지역)" 패턴에서 지역 제거
    # 지역 패턴: (경기 화성시), (전국), (전국_글로벌), (전국_미국), (서울 마포구_글로벌) 등
    title = re.sub(r'\s*\([^)]*(?:시|군|구|도|국|전국|글로벌)[^)]*\)', '', title)
    title = re.sub(r'\s*\(전국[^)]*\)', '', title)
    
    return title.strip()
```

**클로드 확인 제안**:
1. `normalize_title` 적용 후 기존 HS 매핑 히트율 측정 (dry-run)
2. 달바글로벌(330499), 씨큐브, HD현대인프라코어, 삼화콘덴서 신규 HS 매핑 추가 검토
3. `_글로벌` 접미사가 있는 경우 수출 flow로 처리하는지 확인

---

## 4) 우선순위 2 — 지역 변형 + 특수 수입 형식 (P2, ~383건)

### 4-1. 지역 변형 패턴 (회사명 없음)

```
"디램모듈 (전국)"          → 디램모듈
"디램 (전국_글로벌)"       → 디램 (수출)
"CMP (웨이퍼 표면 연마장치) (전국)" → CMP
"솔더볼 (마이크로 솔더볼) (전국)"   → 솔더볼
"영구자석 (전국)"          → 영구자석
"평판디스플레이 텔레비전용 (경기 수원시)" → 평판디스플레이 텔레비전용
```

이 그룹은 P1의 `normalize_title()` 중 Step 2만 적용하면 해결됩니다. P1과 동일한 전처리로 커버 가능.

### 4-2. 수입() 형식 파싱 (23건)

```
제목 형식: "수입({국가_지역})"
raw_text 2번째 줄: 실제 제품명
예시:
  제목: "수입(충남 천안시)"
  본문: "무수불산\n관련종목: 이엔에프테크놀로지"
  → 제품명: 무수불산, 관련종목: 이엔에프테크놀로지

  제목: "수입(일본_경기 화성시)"
  본문: "NCF(Non Conductive Film)\n관련종목 : 삼성전자(레조낙)"
  → 제품명: NCF(Non Conductive Film), 관련종목: 삼성전자
```

**수정 방안 제안**:
```python
def extract_product_from_import_title(title: str, raw_text: str) -> tuple[str, str]:
    """수입(지역) 형식에서 제품명과 관련종목 추출."""
    if title.startswith('수입(') and title.endswith(')'):
        lines = raw_text.strip().split('\n')
        product = lines[0].strip() if lines else ''
        company = ''
        for line in lines[1:]:
            if '관련종목' in line:
                company = re.sub(r'관련종목\s*[:：]\s*', '', line).strip()
                company = re.sub(r'\(.*?\)', '', company).strip()  # 공급사 괄호 제거
                break
        return product, company
    return title, ''
```

---

## 5) 우선순위 3 — 노이즈 필터링 및 특수 케이스 (P3, ~24건)

### 5-1. 매핑 불필요 레코드 (필터링 대상)

| 제목 패턴 | 건수 | 처리 방안 |
|----------|------|---------|
| `품목합` | 8 | 집계성 포스트 → `mapping_status='excluded_aggregate'` |
| 타임스탬프 형식 (`2026.04.13 10:12:42`) | 1 | 파싱 오류 → `mapping_status='excluded_noise'` |
| AI/기술 기사 (`Google Cloud Next...`, `추론 엔지니어링...`) | 2 | 비수출입 컨텐츠 → `excluded_noise` |
| 공시 요약 (`5%ㆍ임원보고 공시 정리`) | 1 | 비수출입 → `excluded_noise` |

### 5-2. 특수 복합 패턴

```
"바우와우코리아 (오에스피 자회사)"  → 16건
  - 회사명만 있고 제품명 없음
  - raw_text에서 제품명 추출 필요
  - 단독 상품명: 반려동물 사료 → HS 2309101000 (개사료) 후보

"펨트론"  → 8건
  - 회사명만 있고 제품명 없음
  - 펨트론 = 3D 검사장비 제조사 → HS 확인 필요

"LIG넥스원: 유도무기 + 로켓 발사기 + 레이더"  → 5건 (복합)
  - 방산 복합 제목 → 개별 HS 분리 매핑 필요
```

---

## 6) 신규 HS 매핑 추가 후보

회사-HS 매핑이 없어서 위 패턴 수정 후에도 여전히 미매핑될 것으로 보이는 케이스:

| 회사명 | 제품 | 권장 HS 코드 | 근거 |
|--------|------|------------|------|
| 달바글로벌 | 기초화장품 | 330499 | 화장품 제조 전문 (38건 영향) |
| 씨큐브 | 진주광택안료 | 3206499000 | 진주광택 안료/혼합물 (16건 영향) |
| 바우와우코리아 | 반려동물 사료 | 2309101000 | provisional 후보 (16건 영향) |
| HD현대인프라코어 | 엔진 | 8408200000 | 산업용 디젤엔진 (17건 영향) |
| 삼화콘덴서 | MLCC | 8532220000 | 적층세라믹콘덴서 (8건 영향) |
| 파크시스템스 | 산업용자동화원자현미경 | 9012100000 | 원자력현미경 (8건 영향) |

---

## 7) 실행 권장 순서 (클로드 재검증 필요)

```
Phase A: 전처리 규칙 적용 (임팩트 최대)
  1. normalize_title() 구현 — 회사명 prefix + 지역 suffix 제거
  2. dry-run: 전체 telegram_post_cache 적용 후 미매핑 → 매핑 전환율 측정
     목표: 1,096건 → 200건 이하로 감소
  3. 확인 후 운영 반영

Phase B: 수입() 형식 처리
  4. extract_product_from_import_title() 구현
  5. 23건 dry-run 후 제품명 추출 품질 확인

Phase C: 신규 HS 매핑 추가
  6. 달바글로벌/씨큐브 등 6개 회사 HS 매핑 수동 추가
  7. Phase A dry-run 재실행 — 추가 감소 확인

Phase D: 노이즈 필터링
  8. 품목합/타임스탬프/AI기사 → excluded 상태로 전환
  9. 잔여 미매핑 수동 검토 (예상: 50건 이내)
```

---

## 8) 예상 효과 (추정치)

| 단계 | 처리 건수 | 미매핑 감소 예상 |
|------|----------|--------------|
| Phase A (normalize_title) | ~689건 | 1,096 → ~400 |
| Phase B (수입() 형식) | ~23건 | ~400 → ~380 |
| Phase C (신규 HS 매핑) | ~95건 | ~380 → ~285 |
| Phase D (노이즈 필터링) | ~12건 | ~285 → ~273 |

**잔여 미매핑 ~273건**은 새로운 제품/회사로 HS 사전 확장이 필요하거나, raw_text 기반 추가 파싱이 필요한 케이스입니다.

---

## 9) 참고 파일

```
scratch/telegram_unmapped_1096_full.tsv            # 전체 1,096건 미매핑 목록
scratch/telegram_unmapped_400_latest.tsv           # 1차 핸드오프 기준 400건
docs/claude_handoff_export_import_precision_2026-05-18.md  # 1차 핸드오프 문서
```

## 10) 참고 SQL

```sql
-- 정규화 후 매핑 히트율 테스트용
SELECT 
  title,
  CASE WHEN title LIKE '% : %' THEN substr(title, instr(title,' : ')+3) ELSE title END as after_company_strip,
  mapping_status
FROM telegram_post_cache
WHERE mapping_status = 'unmapped'
ORDER BY posted_at DESC
LIMIT 100;

-- 신규 매핑 추가 예시 (달바글로벌)
INSERT OR IGNORE INTO hs_code_company_map
  (hs_code, hs_name, stock_code, stock_name, match_type, confidence)
VALUES
  ('330499', '화장품', '217270', '달바글로벌', 'company', 0.90);

-- 노이즈 레코드 제외 처리
UPDATE telegram_post_cache
SET mapping_status = 'excluded_aggregate'
WHERE mapping_status = 'unmapped' AND title LIKE '품목합%';

UPDATE telegram_post_cache
SET mapping_status = 'excluded_noise'
WHERE mapping_status = 'unmapped' 
  AND (
    title GLOB '[0-9][0-9][0-9][0-9].[0-9][0-9].*'
    OR title LIKE '%Google Cloud%'
    OR title LIKE '%추론 엔지니어링%'
    OR title LIKE '%공시 정리%'
  );
```
