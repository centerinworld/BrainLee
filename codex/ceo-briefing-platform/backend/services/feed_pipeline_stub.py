from typing import Dict, List


class FeedPipelineStubService:
    """
    RSS, Python 스크래퍼, AI 요약기 연결 전의 임시 파이프라인 스텁입니다.
    실제 운영 시에는 collect -> normalize -> review queue -> publish 흐름으로 확장합니다.
    """

    def collect_candidates(self, feed_type: str) -> List[Dict[str, str]]:
        if feed_type == "company":
            return [
                {
                    "id": "company-seed-1",
                    "title": "회사 관련 샘플 기사",
                    "summary": "오프라인 개발용으로 제공되는 기본 후보 기사입니다.",
                    "source": "stub",
                    "link": "https://example.com/company-seed-1",
                }
            ]
        return [
            {
                "id": "competitor-seed-1",
                "title": "경쟁사 관련 샘플 기사",
                "summary": "오프라인 개발용 경쟁사 후보 기사입니다.",
                "source": "stub",
                "link": "https://example.com/competitor-seed-1",
            }
        ]
