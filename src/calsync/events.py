"""Turn a Lesson + Category into the body of a Google Calendar event."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .config import Category
from .models import Lesson

# Marker stored on every event we create, so we only ever touch our own events
# and never anything the user typed into the same calendar by hand.
MANAGED_KEY = "calsync"
MANAGED_VALUE = "auriga"
LESSON_ID_KEY = "lessonId"
FINGERPRINT_KEY = "fingerprint"


@dataclass(frozen=True)
class DesiredEvent:
    lesson_id: str
    category: str  # category name
    body: dict[str, Any]
    fingerprint: str


def _description(lesson: Lesson) -> str:
    lines: list[str] = []
    if lesson.activity:
        lines.append(f"Type: {lesson.activity}")
    if lesson.topic:
        lines.append(f"Session: {lesson.topic}")
    if lesson.instructors:
        lines.append(f"Instructor(s): {', '.join(lesson.instructors)}")
    if lesson.populations:
        lines.append(f"Group(s): {', '.join(lesson.populations)}")
    if lesson.unit_codes:
        lines.append(f"Unit(s): {', '.join(lesson.unit_codes)}")
    if lesson.is_exam:
        lines.append("** EXAM **")
    return "\n".join(lines)


def _fingerprint(body: dict[str, Any]) -> str:
    """Stable hash of everything the user can see, to detect real changes cheaply."""
    visible = {k: v for k, v in body.items() if k != "extendedProperties"}
    return hashlib.sha1(json.dumps(visible, sort_keys=True).encode()).hexdigest()[:16]


def build_event(lesson: Lesson, category: Category, timezone: str) -> DesiredEvent:
    body: dict[str, Any] = {
        "summary": f"{category.prefix}{lesson.title}"
        if not lesson.activity
        else f"{category.prefix}{lesson.activity} · {lesson.title}",
        "location": ", ".join(lesson.rooms),
        "description": _description(lesson),
        "start": {"dateTime": lesson.start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": lesson.end.isoformat(), "timeZone": timezone},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in category.reminders],
        },
    }
    fingerprint = _fingerprint(body)
    body["extendedProperties"] = {
        "private": {
            MANAGED_KEY: MANAGED_VALUE,
            LESSON_ID_KEY: lesson.id,
            FINGERPRINT_KEY: fingerprint,
        }
    }
    return DesiredEvent(lesson.id, category.name, body, fingerprint)
