from typing import List

from pydantic import BaseModel


class CalendarEvent(BaseModel):
    id: str
    time: str
    title: str
    place: str
    owner: str
    status: str


class GoogleCalendarStubService:
    """
    실제 Google Calendar API 연결 전까지 사용하는 경계 클래스입니다.
    추후 OAuth 토큰, calendar_id, push sync를 이 클래스 뒤로 숨기면
    상위 라우터를 크게 바꾸지 않고 교체할 수 있습니다.
    """

    def __init__(self, calendar_id: str):
        self.calendar_id = calendar_id

    def list_events(self) -> List[CalendarEvent]:
        return [
            CalendarEvent(
                id=f"{self.calendar_id}-sample-1",
                time="09:00",
                title="샘플 일정",
                place="회의실 A",
                owner="비서실",
                status="stub",
            )
        ]

    def update_event(self, event_id: str, payload: dict) -> dict:
        return {
            "calendar_id": self.calendar_id,
            "event_id": event_id,
            "updated": True,
            "payload": payload,
            "mode": "stub",
        }
