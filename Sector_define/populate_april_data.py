import sqlite3
import os

# 권한 문제로 인해 Sector_define 폴더 내에 전용 DB 생성
DB_PATH = "/Applications/stock_dashboard/Sector_define/sector_april.db"

APRIL_POSTS = [
    {"date": "2026-04-26", "title": "AI 인프라 기업의 독주 흐름", "url": "https://m.blog.naver.com/going_tothe_moon/224265571184", "summary": "AI 수요 폭증으로 인한 전력기기, 액침 냉각, 데이터센터 인프라 관련주들의 강세를 분석. 단순히 칩(GPU)을 넘어 인프라 전반으로 투심이 확산되는 흐름 강조."},
    {"date": "2026-04-25", "title": "다음 투자기회는 ESS가 될 것입니다 (by 모건스탠리)", "url": "https://m.blog.naver.com/going_tothe_moon/224265014460", "summary": "모건스탠리 리포트를 인용하여 전력 소모가 극심한 AI 시대에 에너지 저장 시스템(ESS)이 필수적임을 설명. 북미 시장 진출 중인 국내 배터리 및 ESS 부품주 언급."},
    {"date": "2026-04-24", "title": "글로벌 반도체 대표기업 한장요약", "url": "https://m.blog.naver.com/going_tothe_moon/224264230264", "summary": "엔비디아, TSMC, ASML 등 글로벌 톱 티어 반도체 기업들의 최근 실적 가이드라인과 기술 로드맵 요약. 국내 소부장 기업들에 미칠 영향 체킹."},
    {"date": "2026-04-24", "title": "다시보자 K관광 인바운드주 (feat.호텔신라 실적)", "url": "https://m.blog.naver.com/going_tothe_moon/224263776714", "summary": "호텔신라의 어닝 서프라이즈급 실적을 바탕으로 면세점, 호텔, 화장품 등 인바운드 관광 관련 섹터의 턴어라운드 가능성 시사."},
    {"date": "2026-04-24", "title": "국내 로봇관련주 원페이퍼 (주요 일정들도 체킹)", "url": "https://m.blog.naver.com/going_tothe_moon/224263730843", "summary": "5~6월 로봇 관련 주요 전시회 및 대기업 협동로봇 출시 일정을 정리. 로보티즈(휴머노이드), 두산로보틱스 등 핵심 기업 분류표 제공."},
    {"date": "2026-04-23", "title": "알아두면 도움되는 5월 6월 증시일정", "url": "https://m.blog.naver.com/going_tothe_moon/224262934008", "summary": "블로그 원문 참조"},
    {"date": "2026-04-21", "title": "반도체 기판, OSAT, 삼전파운드리 등등 관련주 모음", "url": "https://m.blog.naver.com/going_tothe_moon/224259388085", "summary": "반도체 밸류체인별 관련 종목 정리"},
    {"date": "2026-04-20", "title": "2026년 집중할 주식 키워드", "url": "https://m.blog.naver.com/going_tothe_moon/224258721864", "summary": "2026년 주도 섹터 전망"},
    {"date": "2026-04-19", "title": "로보티즈 휴머노이드로봇 AI워커 관심", "url": "https://m.blog.naver.com/going_tothe_moon/224257973595", "summary": "로봇 섹터 개별주 분석"},
    {"date": "2026-04-17", "title": "중간선거후 미국증시는 '강했다'", "url": "https://m.blog.naver.com/going_tothe_moon/224256150081", "summary": "미국 대선/선거 이후 증시 향방 분석"},
    {"date": "2026-04-14", "title": "낸드 가격 추이 (샌디스크, 키옥시아, SK하이닉스)", "url": "https://m.blog.naver.com/going_tothe_moon/224251932865", "summary": "메모리 가격 반등 데이터 분석"},
    {"date": "2026-04-14", "title": "오늘 오후장 특징주들 및 모멘텀 체킹", "url": "https://m.blog.naver.com/going_tothe_moon/224251758748", "summary": "일일 특징주 정리"},
    {"date": "2026-04-14", "title": "최근 시장 특징들 (삼전 오름, 반도체 순환매, 원자력 등)", "url": "https://m.blog.naver.com/going_tothe_moon/224251441872", "summary": "시장 주도주 순환매 분석"},
    {"date": "2026-04-08", "title": "정부의 '재생에너지 대전환' 추진계획 체킹", "url": "https://m.blog.naver.com/going_tothe_moon/224245615399", "summary": "신재생 에너지 정책 수혜주 분석"},
    {"date": "2026-04-02", "title": "중동전쟁 '피해자와 수혜자' 요약 해보면요", "url": "https://m.blog.naver.com/going_tothe_moon/22423842827", "summary": "지정학적 리스크 관련주 분석"},
    {"date": "2026-04-02", "title": "이자,배당 소득이 건강보험료에 미치는 영향", "url": "https://m.blog.naver.com/going_tothe_moon/224237741373", "summary": "금융 소득 관련 정보"},
    {"date": "2026-04-01", "title": "스페이스x관련주 스피어, 에이치브이엠 차이 쉽게", "url": "https://m.blog.naver.com/going_tothe_moon/224237528332", "summary": "우주항공 섹터 분석"},
    {"date": "2026-04-01", "title": "4월 주도섹터를 준비", "url": "https://m.blog.naver.com/going_tothe_moon/224236379653", "summary": "4월 전체 전략 및 유망 섹터 선정"}
]

def populate():
    try:
        # DB 파일 존재 여부와 상관없이 새로 연결 (없으면 생성됨)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sector_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            blog_url TEXT NOT NULL,
            post_date TEXT NOT NULL,
            ai_summary TEXT,
            telegram_sent INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        for p in APRIL_POSTS:
            cursor.execute(
                "INSERT INTO sector_posts (title, blog_url, post_date, ai_summary) VALUES (?, ?, ?, ?)",
                (p["title"], p["url"], p["date"], p["summary"])
            )
            print(f"Inserted: {p['title']}")
            
        conn.commit()
        conn.close()
        print(f"Success! Database created at {DB_PATH}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    populate()
