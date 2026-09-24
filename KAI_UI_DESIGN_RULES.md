# Project AGI — KAI 관제 센터 UI 디자인 규칙 v5.0

> 최종 업데이트: 2026-09-12 | 유지/적용 필수

## 1. 전체 원칙

- **흰색 계열 테마 유지** (`#f5f7fa` 배경, `#ffffff` 카드)
- **stock.leanguy.cloud 스타일** 참고 (클린한 엔터프라이즈 라이트 테마)
- **1960px 표준 폭** 적용 (`max-width: 1960px; margin: 0 auto;`)

## 2. 폰트 (전 페이지 통일 - 절대 개별 변경 금지)

```css
font-family: Pretendard, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', Roboto, sans-serif !important;
```
- CDN: `https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css`
- 기본 폰트 크기: `13.5px` (html 기준)
- 모든 `* { font-family: Pretendard, ... !important; }` 강제 적용

## 3. 색상 토큰 (CSS 변수 - 임의 변경 금지)

| 변수 | 값 | 용도 |
|------|-----|------|
| `--p` | `#1a73e8` | Primary (Google Blue) |
| `--p2` | `#1557b0` | Primary Hover |
| `--pl` | `#e8f0fe` | Primary Light |
| `--bg` | `#f5f7fa` | 페이지 배경 |
| `--card` | `#ffffff` | 카드 배경 |
| `--t` | `#1a1f36` | 주 텍스트 |
| `--t2` | `#4a5568` | 보조 텍스트 |
| `--t3` | `#718096` | 흐린 텍스트 |
| `--b` | `#d8dde6` | 일반 테두리 |
| `--b2` | `#c0c8d4` | **강조 구분선** (헤더 행, 섹션 간격) |
| `--up` | `#15803d` | 상승/긍정 |
| `--down` | `#dc2626` | 하락/부정 |

## 4. 테이블 스타일 (구분선 명확성 필수)

```css
/* 테이블 헤더 행 - 강조 구분선 적용 */
.agx-table th,
.tbl th,
.review-table th {
  background: #eef1f5;        /* 헤더 배경 */
  border-bottom: 2px solid var(--b2); /* 강조 구분선 (2px) */
  font-size: 11.5px;
  font-weight: 700;
  text-transform: uppercase;
}

/* 일반 행 */
.agx-table td,
.tbl td,
.review-table td {
  border-bottom: 1px solid var(--b); /* 일반 구분선 (1px) */
  padding: 9px 13px;
}

/* 호버 */
tr:hover td { background: #f8fafc; }
```

## 5. 숫자 표시 (1000단위 쉼표 - 전역 함수 사용)

```javascript
// kai.js에 내장된 전역 유틸 함수
fmtNum(n)          // 1,234,567 (정수)
fmtNum(n, 2)       // 1,234.56 (소수점 2자리)
fmtPct(n)          // 12.34%
fmtCurrency(n)     // 1.2조원 / 34.5억원 / 1,234원
```

## 6. 네비게이션 탭 순서 (변경 금지)

1. 📰 주요기사 (`company`)
2. 🌐 시장 인텔리전스 (`global`)
3. 📊 시장분석 (`market_analysis`)
4. 📡 텔레그램 (`telegram`)
5. ⚙️ 시스템 (`system`)
6. 👑 관리자 콘솔 (`admin`)

## 7. 주요 파일 경로

| 파일 | 로컬 경로 | AI System 경로 |
|------|-----------|----------------|
| kai.css | `/Users/brainlee/Downloads/codex/ceo-briefing-platform/frontend/kai.css` | `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/frontend/kai.css` |
| kai.js | `.../frontend/kai.js` | `.../frontend/kai.js` |
| index.html | `.../frontend/kai/index.html` | `.../frontend/kai/index.html` |

> **중요**: 파일 수정 시 반드시 AI System 경로로 동기화 (shutil.copy2)

## 8. 캐시 버스터

```html
<link rel="stylesheet" href="../kai.css?v=5.0.0">
<script src="../kai.js?v=5.0.0"></script>
```

버전 번호를 올릴 때마다 두 파일 모두 동기화 필수.
