import sys
import sqlite3
from pathlib import Path

# Add backend and backend/services to sys.path to allow imports
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.services.rss_ingest import (
    is_same_topic_rule,
    LOW_VALUE_KEYWORDS,
    normalize_topic_text,
)

def test_low_value_filtering():
    print("--- Test 1: Low-Value / Noise Article Filtering ---")
    
    test_articles = [
        {"title": "한화에어로스페이스, 오늘 장중 3.5% 상승하며 시총 증가", "expected": True},
        {"title": "KAI 임직원, 국립현충원 묘역 참배 및 정화 활동 전개", "expected": True},
        {"title": "한화시스템, 현출원 참배 통해 애국정신 고취", "expected": True},
        {"title": "LIG넥스원, 저소득 가구 대상 무료급식 봉사활동 진행", "expected": True},
        {"title": "KAI, 2026 하반기 대졸 신입사원 채용설명회 개최", "expected": True},
        {"title": "대학교수의 추천으로 KAI에 입사한 제자들의 성공 스토리", "expected": True},
        {"title": "KAI에 들어온 한 대학교수의 제자가 화제다", "expected": True},
        {"title": "KAI, 폴란드에 FA-50 경공격기 12대 추가 수출 계약 체결", "expected": False},
        {"title": "한화에어로스페이스, 자체 개발한 고출력 항공 엔진 시운전 개시", "expected": False},
        {"title": "LIG넥스원, 사우디아라비아에 3조원 규모 천궁-II 수주 성공", "expected": False},
    ]

    def check_low_value(title: str) -> bool:
        text = title.lower()
        return any(kw in text for kw in LOW_VALUE_KEYWORDS)

    success = True
    for idx, art in enumerate(test_articles, 1):
        result = check_low_value(art["title"])
        status = "PASS" if result == art["expected"] else "FAIL"
        if status == "FAIL":
            success = False
        print(f"[{status}] {idx}. 제목: '{art['title']}' -> 필터링 여부: {result} (예상: {art['expected']})")
    
    return success

def test_deduplication_rules():
    print("\n--- Test 2: Near-Duplicate / Topic Similarity Rules ---")
    
    test_pairs = [
        {
            "a": {"title": "KAI, 성과중심 책임경영 조직개편 단행", "link": "http://a.com/news1"},
            "b": {"title": "KAI 김종출號 성과중심 ‘책임경영’ 강화 위한 조직개편 단행", "link": "http://b.com/news2"},
            "expected": True,
            "desc": "KAI 조직개편 기사 (동일 주제)"
        },
        {
            "a": {"title": "한화에어로, KAI 주식 추가 매입… 지분율 6.17%", "link": "http://a.com/news3"},
            "b": {"title": "한화에어로스페이스, KAI 지분 추가 획득해 6.17% 보유", "link": "http://b.com/news4"},
            "expected": True,
            "desc": "한화의 KAI 주식 매입 기사 (동일 주제)"
        },
        {
            "a": {"title": "KAI, 김종출 체제 첫 조직개편…책임경영 체계 재정비", "link": "http://a.com/news7"},
            "b": {"title": "KAI, 김종출 사장 취임 후 조직 슬림화…권한 분산으로 '속도경영' 전환", "link": "http://b.com/news8"},
            "expected": True,
            "desc": "KAI 김종출 조직개편/슬림화 기사 (사용자 제보 실패 케이스 1)"
        },
        {
            "a": {"title": "KAI-전략사령부, 미래 전력 발전 위한 업무협약 체결", "link": "http://a.com/news9"},
            "b": {"title": "KF-21 넘어 AI 공중전으로…KAI, 전략사령부와 미래 전력 협력", "link": "http://b.com/news10"},
            "expected": True,
            "desc": "KAI-전략사령부 업무협약 기사 (사용자 제보 실패 케이스 2)"
        },
        {
            "a": {"title": "KAI·전략사령부, 미래전 대응 '공동 전선' 구축…MUM-T·우주 등 첨단전...", "link": "http://a.com/news11"},
            "b": {"title": "KAI, 전략사령부와 손잡고 미래 전장 환경 대응 협력 체계 구축한다", "link": "http://b.com/news12"},
            "expected": True,
            "desc": "전략사령부 협약 기사 변형 (사용자 추가 제보)"
        },
        {
            "a": {"title": "분산·중첩 조직 개편···KAI, 사업 중심 '3부문 체제'로 단순화", "link": "http://a.com/news13"},
            "b": {"title": "KAI 김종출 사장, 취임 후 첫 조직개편…중복 줄이고 책임·효율 높인다", "link": "http://b.com/news14"},
            "expected": True,
            "desc": "김종출 조직개편 vs 3부문 단순화 기사 (사용자 추가 제보)"
        },
        {
            "a": {"title": "광양경자청, 위드피에스 300억 투자 유치…해룡산단 방산 거점 키운다", "link": "http://a.com/news15"},
            "b": {"title": "300억 투입…순천 해룡산단에 K-방산 핵심기업 들어온다", "link": "http://b.com/news16"},
            "expected": True,
            "desc": "해룡산단 300억 투자유치 기사 (사용자 추가 제보 2)"
        },
        {
            "a": {"title": "한화에어로, KAI 지분 확대 속도…글로벌 방산 재편 대응", "link": "http://a.com/news17"},
            "b": {"title": "한화에어로, KAI 지분율 6% 돌파…“연내 8% 확보 추진”", "link": "http://a.com/news18"},
            "expected": True,
            "desc": "한화 KAI 지분 매입 변형 및 다른 일자 마일스톤 매칭 (사용자 추가 제보 3)"
        },
        {
            "a": {"title": "방위사업청, 차세대 경공격기 핵심 부품 국산화 착수", "link": "http://a.com/news5"},
            "b": {"title": "방사청, 차세대 공격 헬기 핵심 장비 시운전 성공", "link": "http://b.com/news6"},
            "expected": False,
            "desc": "다른 종류의 방산 장비 기사 (서로 다름)"
        }
    ]

    success = True
    for idx, pair in enumerate(test_pairs, 1):
        result = is_same_topic_rule(pair["a"], pair["b"])
        status = "PASS" if result == pair["expected"] else "FAIL"
        if status == "FAIL":
            success = False
        print(f"[{status}] {idx}. [{pair['desc']}]")
        print(f"   기사 A: '{pair['a']['title']}'")
        print(f"   기사 B: '{pair['b']['title']}'")
        print(f"   -> 판정 결과: {result} (예상: {pair['expected']})")
        
    return success

if __name__ == "__main__":
    t1 = test_low_value_filtering()
    t2 = test_deduplication_rules()
    
    print("\n===============================")
    if t1 and t2:
        print("🎉 ALL FILTER & DEDUP TESTS PASS!")
        sys.exit(0)
    else:
        print("❌ SOME TESTS FAILED. PLEASE CHECK THE OUTPUT.")
        sys.exit(1)
