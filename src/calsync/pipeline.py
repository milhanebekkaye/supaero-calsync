"""The sync use-case: lessons in, calendar changes out. Backend-agnostic and testable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .classify import classify
from .config import Config
from .events import build_event
from .models import Lesson
from .sync import Gateway, Plan, RemoteEvent, apply_plan, build_plan, check_deletions


@dataclass
class SyncResult:
    plan: Plan
    applied: bool


def window(start: date, end: date, timezone: str) -> tuple[str, str]:
    """RFC3339 bounds [start 00:00, end+1 00:00) in the calendar's timezone."""
    tz = ZoneInfo(timezone)
    first = datetime.combine(start, time.min, tzinfo=tz)
    last = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
    return first.isoformat(), last.isoformat()


def run_sync(
    lessons: list[Lesson],
    config: Config,
    gateway: Gateway,
    start: date,
    end: date,
    *,
    dry_run: bool = False,
    force_delete: bool = False,
) -> SyncResult:
    """Reconcile Google Calendar with `lessons` over [start, end].

    Only events carrying our private marker are ever read, changed or deleted, so
    anything the user adds by hand to the same calendars is left alone.
    """
    desired = [build_event(lesson, classify(lesson, config), config.timezone) for lesson in lessons]
    needed = {event.category for event in desired}
    start_iso, end_iso = window(start, end, config.timezone)

    calendar_ids: dict[str, str] = {}
    remote: dict[str, list[RemoteEvent]] = {}
    for category in config.all_categories:
        existing = gateway.find_calendar(category.calendar)
        if existing is None and category.name in needed and not dry_run:
            existing = gateway.ensure_calendar(category.calendar, category.color_id)
        elif existing is not None and not dry_run:
            gateway.ensure_calendar(category.calendar, category.color_id)  # keeps colour in sync
        if existing is not None:
            calendar_ids[category.name] = existing
            remote[category.name] = gateway.list_managed(existing, start_iso, end_iso)

    plan = build_plan(desired, remote)
    check_deletions(plan, sum(len(events) for events in remote.values()), force=force_delete)
    if dry_run:
        return SyncResult(plan, applied=False)

    apply_plan(plan, gateway, calendar_ids)
    return SyncResult(plan, applied=True)
