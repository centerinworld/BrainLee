from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
GOOGLE_CLIENT_PATH = CONFIG_DIR / "google_oauth_client.json"
GOOGLE_CLIENT_EXAMPLE_PATH = CONFIG_DIR / "google_oauth_client.example.json"
GOOGLE_TOKEN_PATH = DATA_DIR / "google_token.json"
GOOGLE_AUTH_STATE_PATH = DATA_DIR / "google_oauth_state.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
SEOUL_TZ = "Asia/Seoul"


def _load_google_modules():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise RuntimeError(
            "Google Calendar packages are not installed. Install google-api-python-client, "
            "google-auth-httplib2, and google-auth-oauthlib first."
        ) from exc
    return Request, Credentials, Flow, build, HttpError


def _load_json(path: Path, fallback: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not path.exists():
        return fallback or {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def _without_proxy_env():
    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "REQUESTS_CA_BUNDLE",
    ]
    saved = {key: os.environ.get(key) for key in proxy_keys}
    no_proxy_saved = os.environ.get("NO_PROXY")
    no_proxy_lower_saved = os.environ.get("no_proxy")
    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if no_proxy_saved is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = no_proxy_saved
        if no_proxy_lower_saved is None:
            os.environ.pop("no_proxy", None)
        else:
            os.environ["no_proxy"] = no_proxy_lower_saved


def google_client_config_exists() -> bool:
    return GOOGLE_CLIENT_PATH.exists()


def google_token_exists() -> bool:
    return GOOGLE_TOKEN_PATH.exists()


def get_google_setup_status(base_url: str) -> Dict[str, Any]:
    authenticated = False
    calendars: List[Dict[str, str]] = []
    error = ""
    try:
        if google_client_config_exists() and google_token_exists():
            authenticated = True
            calendars = list_accessible_calendars()
    except Exception as exc:
        authenticated = False
        error = str(exc)

    return {
        "credentials_file_present": google_client_config_exists(),
        "credentials_file_path": str(GOOGLE_CLIENT_PATH),
        "credentials_template_path": str(GOOGLE_CLIENT_EXAMPLE_PATH),
        "token_file_present": google_token_exists(),
        "authenticated": authenticated,
        "callback_url": f"{base_url.rstrip('/')}/google-calendar/auth/callback",
        "scopes": SCOPES,
        "calendars": calendars,
        "error": error,
    }


def _load_credentials():
    Request, Credentials, _, _, _ = _load_google_modules()
    if not GOOGLE_TOKEN_PATH.exists():
        return None

    credentials = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        with _without_proxy_env():
            credentials.refresh(Request())
        GOOGLE_TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials or not credentials.valid:
        return None
    return credentials


def build_google_auth_url(base_url: str) -> Dict[str, str]:
    _, _, Flow, _, _ = _load_google_modules()
    if not google_client_config_exists():
        raise RuntimeError(
            f"Missing Google OAuth client file. Create {GOOGLE_CLIENT_PATH.name} from the example file first."
        )

    redirect_uri = f"{base_url.rstrip('/')}/google-calendar/auth/callback"
    with _without_proxy_env():
        flow = Flow.from_client_secrets_file(str(GOOGLE_CLIENT_PATH), scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _save_json(
        GOOGLE_AUTH_STATE_PATH,
        {
            "state": state,
            "redirect_uri": redirect_uri,
            "created_at": datetime.now().isoformat(),
        },
    )
    return {"auth_url": auth_url, "redirect_uri": redirect_uri}


def complete_google_auth(state: str, code: str) -> Dict[str, Any]:
    _, _, Flow, _, _ = _load_google_modules()
    pending = _load_json(GOOGLE_AUTH_STATE_PATH)
    if not pending or pending.get("state") != state:
        raise RuntimeError("Google OAuth state mismatch. Start the connection again.")
    if not google_client_config_exists():
        raise RuntimeError("Google OAuth client file is missing.")

    with _without_proxy_env():
        flow = Flow.from_client_secrets_file(
            str(GOOGLE_CLIENT_PATH),
            scopes=SCOPES,
            state=state,
        )
        flow.redirect_uri = pending["redirect_uri"]
        flow.fetch_token(code=code)
    GOOGLE_TOKEN_PATH.write_text(flow.credentials.to_json(), encoding="utf-8")
    if GOOGLE_AUTH_STATE_PATH.exists():
        GOOGLE_AUTH_STATE_PATH.unlink()
    return get_google_setup_status(pending["redirect_uri"].rsplit("/google-calendar/auth/callback", 1)[0])


def disconnect_google_auth() -> None:
    if GOOGLE_TOKEN_PATH.exists():
        GOOGLE_TOKEN_PATH.unlink()
    if GOOGLE_AUTH_STATE_PATH.exists():
        GOOGLE_AUTH_STATE_PATH.unlink()


def _build_calendar_service():
    _, _, _, build, _ = _load_google_modules()
    credentials = _load_credentials()
    if not credentials:
        raise RuntimeError("Google Calendar is not authenticated yet.")
    with _without_proxy_env():
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def list_accessible_calendars() -> List[Dict[str, str]]:
    service = _build_calendar_service()
    result = service.calendarList().list(maxResults=50).execute()
    items = result.get("items", [])
    calendars: List[Dict[str, str]] = []
    for item in items:
        calendars.append(
            {
                "id": item.get("id", ""),
                "summary": item.get("summary", ""),
                "primary": "true" if item.get("primary") else "false",
                "access_role": item.get("accessRole", ""),
            }
        )
    return calendars


def list_calendar_events(calendar_id: str, max_results: int = 20) -> List[Dict[str, str]]:
    service = _build_calendar_service()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result = service.events().list(
        calendarId=calendar_id,
        timeMin=now,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    items = result.get("items", [])

    events: List[Dict[str, str]] = []
    for item in items:
        start = item.get("start", {})
        start_date = start.get("date")
        start_dt = start.get("dateTime", "")
        if start_dt:
            date_part, time_part = start_dt.split("T", 1)
            time_part = time_part[:5]
        else:
            date_part = start_date or ""
            time_part = "All day"
        events.append(
            {
                "id": item.get("id", ""),
                "date": date_part,
                "time": time_part,
                "title": item.get("summary", "(No title)"),
                "place": item.get("location", ""),
                "owner": item.get("organizer", {}).get("email", ""),
                "status": item.get("status", "confirmed"),
            }
        )
    return events


def create_calendar_event(calendar_id: str, payload: Dict[str, str]) -> Dict[str, Any]:
    service = _build_calendar_service()
    event_body = _calendar_event_body(payload)
    event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
    return {
        "id": event.get("id", ""),
        "html_link": event.get("htmlLink", ""),
    }


def _calendar_event_body(payload: Dict[str, str]) -> Dict[str, Any]:
    start_at = f"{payload['date']}T{payload['time']}:00"
    end_dt = datetime.strptime(start_at, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=1)
    return {
        "summary": payload["title"],
        "location": payload["place"],
        "description": f"Owner: {payload['owner']}\nStatus: {payload['status']}",
        "start": {"dateTime": start_at, "timeZone": SEOUL_TZ},
        "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": SEOUL_TZ},
    }


def update_calendar_event(calendar_id: str, event_id: str, payload: Dict[str, str]) -> Dict[str, Any]:
    service = _build_calendar_service()
    event = service.events().update(calendarId=calendar_id, eventId=event_id, body=_calendar_event_body(payload)).execute()
    return {
        "id": event.get("id", ""),
        "html_link": event.get("htmlLink", ""),
    }


def delete_calendar_event(calendar_id: str, event_id: str) -> None:
    service = _build_calendar_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
