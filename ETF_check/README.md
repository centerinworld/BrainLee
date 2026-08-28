# ETF_check 프로젝트 — 종목별 ETF 편입금액 수집기

## 개요
etfcheck.co.kr에서 전체 한국 주식 종목의 ETF 편입금액(추정)을 일별로 수집하여 DB에 저장합니다.

## 파일 구조
```
ETF_check/
├── README.md              # 이 파일
├── init_db.py             # DB 초기화
├── collector.py           # 메인 수집기 (Playwright 기반)
├── scheduler.py           # 새벽 12시 자동 실행
├── test_single.py         # 단일 종목 테스트용
├── etf_check.db           # SQLite DB (수집 데이터 저장)
└── requirements.txt       # 필요 패키지
```

## DB 스키마
- `etf_inclusion_daily`: 종목별 일별 ETF 편입금액(추정) 저장

## 실행 방법
```bash
# 1. 테스트 (단일 종목)
python test_single.py --code 000660

# 2. 전체 수집 (1회)
python collector.py

# 3. 스케줄러 시작 (매일 새벽 12시 자동)
python scheduler.py
```

## 로그인 정보
- etfcheck.co.kr 구글 로그인 사용 (환경변수 또는 .env에서 관리)
- URL 패턴: https://www.etfcheck.co.kr/mobile/searchPdf/{stock_code}
