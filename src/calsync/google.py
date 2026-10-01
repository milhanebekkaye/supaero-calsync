"""Google Calendar backend: OAuth sign-in and the `Gateway` implementation."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from .config import CONFIG_DIR
from .events import FINGERPRINT_KEY, LESSON_ID_KEY, MANAGED_KEY, MANAGED_VALUE
from .sync import RemoteEvent

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"  # OAuth client, downloaded by the user
TOKEN_FILE = CONFIG_DIR / "google-token.json"  # created on first sign-in

T = TypeVar("T")


class GoogleSetupError(RuntimeError):
    """Google API credentials are missing or unusable."""


def get_credentials(
    credentials_file: Path = CREDENTIALS_FILE, token_file: Path = TOKEN_FILE
):
    """Return valid user credentials, running the browser sign-in flow if needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None  # revoked or expired: fall through to a fresh sign-in
        else:
            _save_token(creds, token_file)
            return creds

    if not credentials_file.exists():
        raise GoogleSetupError(
            f"Google OAuth client file not found: {credentials_file}\n"
            "Follow the 'Google setup' section of the README to create it."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds, token_file)
    return creds


def _save_token(creds, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token_file, 0o600)  # the refresh token is a credential


def _retry(call: Callable[[], T], *, attempts: int = 5) -> T:
    """Run an API call, backing off on rate limits and transient server errors."""
    from googleapiclient.errors import HttpError

    for attempt in range(1, attempts + 1):
        try:
            return call()
        except HttpError as exc:
            status = exc.resp.status
            retryable = status in (429, 500, 502, 503, 504) or (
                status == 403 and "rateLimitExceeded" in str(exc)
            )
            if not retryable or attempt == attempts:
                raise
            time.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


class GoogleGateway:
    """Reads and writes events through the Google Calendar v3 API."""

    def __init__(self, service: Any):
        self.service = service
        self._calendar_ids: dict[str, str] | None = None

    @classmethod
    def connect(cls) -> "GoogleGateway":
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)
        return cls(service)

    # -- calendars ---------------------------------------------------------- #

    def _calendars(self) -> dict[str, str]:
        if self._calendar_ids is None:
            found: dict[str, str] = {}
            token = None
            while True:
                page = _retry(
                    lambda t=token: self.service.calendarList().list(pageToken=t).execute()
                )
                for item in page.get("items", []):
                    found[item["summary"]] = item["id"]
                token = page.get("nextPageToken")
                if not token:
                    break
            self._calendar_ids = found
        return self._calendar_ids

    def find_calendar(self, name: str) -> str | None:
        return self._calendars().get(name)

    def ensure_calendar(self, name: str, color_id: str) -> str:
        """Return the id of the calendar called `name`, creating and colouring it if needed."""
        calendar_id = self.find_calendar(name)
        if calendar_id is None:
            created = _retry(
                lambda: self.service.calendars().insert(body={"summary": name}).execute()
            )
            calendar_id = created["id"]
            self._calendars()[name] = calendar_id
        # Colour is a per-user property of the calendar list entry, not of the calendar.
        _retry(
            lambda: self.service.calendarList()
            .patch(calendarId=calendar_id, body={"colorId": color_id})
            .execute()
        )
        return calendar_id

    # -- events ------------------------------------------------------------- #

    def list_managed(self, calendar_id: str, start_iso: str, end_iso: str) -> list[RemoteEvent]:
        """Every event we created in this calendar between two RFC3339 instants."""
        events: list[RemoteEvent] = []
        token = None
        while True:
            page = _retry(
                lambda t=token: self.service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=start_iso,
                    timeMax=end_iso,
                    privateExtendedProperty=f"{MANAGED_KEY}={MANAGED_VALUE}",
                    singleEvents=True,
                    showDeleted=False,
                    maxResults=2500,
                    pageToken=t,
                )
                .execute()
            )
            for item in page.get("items", []):
                private = item.get("extendedProperties", {}).get("private", {})
                if LESSON_ID_KEY in private:
                    events.append(
                        RemoteEvent(
                            event_id=item["id"],
                            lesson_id=private[LESSON_ID_KEY],
                            fingerprint=private.get(FINGERPRINT_KEY, ""),
                        )
                    )
            token = page.get("nextPageToken")
            if not token:
                return events

    def insert(self, calendar_id: str, body: dict) -> None:
        _retry(lambda: self.service.events().insert(calendarId=calendar_id, body=body).execute())

    def update(self, calendar_id: str, event_id: str, body: dict) -> None:
        _retry(
            lambda: self.service.events()
            .update(calendarId=calendar_id, eventId=event_id, body=body)
            .execute()
        )

    def delete(self, calendar_id: str, event_id: str) -> None:
        from googleapiclient.errors import HttpError

        try:
            _retry(
                lambda: self.service.events()
                .delete(calendarId=calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status not in (404, 410):  # already gone is fine
                raise
