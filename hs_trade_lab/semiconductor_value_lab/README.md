# Semiconductor Value Lab

`hs_trade_lab` 안에서만 독립적으로 운영되는 반도체 밸류스트림 분석 페이지입니다.

## 구성

- `server.py`
  - 표준 라이브러리만 사용하는 간단한 HTTP 서버
  - 정적 페이지와 JSON API 제공
- `scripts/rebuild_cache.py`
  - 엑셀 원본과 기존 읽기 전용 DB를 바탕으로 캐시 DB 재생성
- `data/semiconductor_value_lab.db`
  - 페이지에서 즉시 읽는 사전 계산 결과 DB
- `static/`
  - 프론트엔드 페이지

## 데이터 소스

- 엑셀 원본
  - `/Users/brainlee/Downloads/반도체 업종 Value Stream의 사본.xlsx`
- 읽기 전용 기존 주가/실적 DB
  - `/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db`

## 실행 순서

```bash
cd /Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab/semiconductor_value_lab
python3 scripts/rebuild_cache.py
python3 server.py
```

기본 접속 주소:

- `http://127.0.0.1:8021`

## 편집 가능한 항목

- 관심종목
  - 기본값: `제주반도체`, `DB하이텍`, `위드텍`
- 기준일
  - `A`, `B`, `C` 3개 날짜를 바꾸면 재계산 가능

## CSV 내보내기

캐시 재생성 시 아래 파일이 다운로드 폴더에 같이 생성됩니다.

- `/Users/brainlee/Downloads/semiconductor_value_lab_sector_summary.csv`
- `/Users/brainlee/Downloads/semiconductor_value_lab_stocks.csv`
- `/Users/brainlee/Downloads/semiconductor_value_lab_watchlist.csv`
