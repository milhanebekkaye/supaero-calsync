"""Shared fixtures: synthetic portal data and an in-memory calendar backend."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from calsync.config import Config, default_config_text, parse_config
from calsync.events import FINGERPRINT_KEY, LESSON_ID_KEY
from calsync.sync import RemoteEvent


def raw_session(
    id: int,
    *,
    start: str = "2026-10-05T07:30:00Z",
    end: str = "2026-10-05T09:30:00Z",
    unit: str | None = "Mécanique du vol",
    code: str | None = "CM",
    description: str = "",
    room: str | None = "A101",
    is_exam: bool = False,
) -> dict[str, Any]:
    """A fake Auriga 'intervention' shaped like the real API payload."""
    return {
        "id": id,
        "startDateTime": start,
        "endDateTime": end,
        "description": description,
        "isExam": is_exam,
        "activityType": {"code": code, "caption": {"fr": code}} if code else None,
        "interventionPedagogicalUnits": (
            [{"pedagogicalUnit": {"code": f"U{id}", "caption": {"fr": unit}}}] if unit else []
        ),
        "interventionResources": (
            [{"resource": {"caption": {"fr": room}}}] if room else []
        ),
        "interventionInstructors": [
            {"person": {"currentFirstName": "Ada", "currentLastName": "LOVELACE"}}
        ],
        "interventionPopulations": [{"population": {"caption": {"fr": "3A SDD"}}}],
    }


@pytest.fixture
def config() -> Config:
    return parse_config(default_config_text())


class FakeGateway:
    """In-memory stand-in for Google Calendar, enforcing nothing the real API wouldn't."""

    def __init__(self) -> None:
        self.calendars: dict[str, str] = {}  # name -> id
        self.colors: dict[str, str] = {}
        self.events: dict[str, dict[str, dict]] = {}  # calendar id -> event id -> body
        self._ids = itertools.count(1)
        self.writes = 0

    def find_calendar(self, name: str) -> str | None:
        return self.calendars.get(name)

    def ensure_calendar(self, name: str, color_id: str) -> str:
        if name not in self.calendars:
            self.calendars[name] = f"cal-{next(self._ids)}"
            self.events[self.calendars[name]] = {}
        self.colors[name] = color_id
        return self.calendars[name]

    def list_managed(self, calendar_id: str, start_iso: str, end_iso: str) -> list[RemoteEvent]:
        result = []
        for event_id, body in self.events[calendar_id].items():
            private = body.get("extendedProperties", {}).get("private", {})
            if private.get("calsync") == "auriga":
                result.append(
                    RemoteEvent(event_id, private[LESSON_ID_KEY], private.get(FINGERPRINT_KEY, ""))
                )
        return result

    def insert(self, calendar_id: str, body: dict) -> None:
        self.writes += 1
        self.events[calendar_id][f"ev-{next(self._ids)}"] = body

    def update(self, calendar_id: str, event_id: str, body: dict) -> None:
        self.writes += 1
        self.events[calendar_id][event_id] = body

    def delete(self, calendar_id: str, event_id: str) -> None:
        self.writes += 1
        del self.events[calendar_id][event_id]

    def all_lesson_ids(self) -> list[str]:
        return [
            body["extendedProperties"]["private"][LESSON_ID_KEY]
            for events in self.events.values()
            for body in events.values()
        ]


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()
