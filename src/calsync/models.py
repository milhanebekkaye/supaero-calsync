"""Plain data types shared by every layer. No I/O here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Lesson:
    """One timetable session, normalised from the portal's raw payload.

    `id` is the portal's own session id, stringified. It is the key that makes
    syncing idempotent: the same session always maps to the same calendar event.
    """

    id: str
    start: datetime  # timezone-aware, Europe/Paris
    end: datetime  # timezone-aware, Europe/Paris
    title: str
    activity: str = ""  # activity type code from the portal: CM, TP, BE, EX...
    topic: str = ""  # free-text description of this particular session
    instructors: tuple[str, ...] = ()
    rooms: tuple[str, ...] = ()
    populations: tuple[str, ...] = ()
    unit_codes: tuple[str, ...] = ()
    is_exam: bool = False

    @property
    def searchable_text(self) -> str:
        """Everything a classification rule may want to look at."""
        return " ".join(part for part in (self.activity, self.title, self.topic) if part)
