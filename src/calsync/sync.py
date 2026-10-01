"""Diff the desired calendar state against what Google already holds, then apply it.

The planner is pure: it takes plain data in and returns a Plan, so every rule
(no duplicates, category moves, mass-delete guard) is testable without a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .events import DesiredEvent


@dataclass(frozen=True)
class RemoteEvent:
    event_id: str
    lesson_id: str
    fingerprint: str


@dataclass
class Plan:
    creates: list[DesiredEvent] = field(default_factory=list)
    # (category, google event id, new desired state)
    updates: list[tuple[str, str, DesiredEvent]] = field(default_factory=list)
    # (category, google event id, lesson id)
    deletes: list[tuple[str, str, str]] = field(default_factory=list)
    unchanged: int = 0

    @property
    def changes(self) -> int:
        return len(self.creates) + len(self.updates) + len(self.deletes)


class Gateway(Protocol):
    """What the sync needs from a calendar backend (Google, or a fake in tests)."""

    def find_calendar(self, name: str) -> str | None: ...
    def ensure_calendar(self, name: str, color_id: str) -> str: ...
    def list_managed(self, calendar_id: str, start_iso: str, end_iso: str) -> list[RemoteEvent]: ...
    def insert(self, calendar_id: str, body: dict) -> None: ...
    def update(self, calendar_id: str, event_id: str, body: dict) -> None: ...
    def delete(self, calendar_id: str, event_id: str) -> None: ...


def build_plan(
    desired: list[DesiredEvent], remote: dict[str, list[RemoteEvent]]
) -> Plan:
    """Compare desired events with the remote ones, grouped by category name.

    A lesson is identified by its portal id, wherever it currently lives:
    - missing remotely                 -> create
    - present, different fingerprint   -> update in place
    - present, identical               -> leave alone
    - present in the wrong category    -> delete there, create in the right one
    - present twice (an interrupted run, or a manual copy) -> keep one, delete extras
    - remote but no longer desired     -> delete
    """
    plan = Plan()
    wanted = {event.lesson_id: event for event in desired}

    seen: set[str] = set()
    for category, events in remote.items():
        for remote_event in events:
            target = wanted.get(remote_event.lesson_id)
            if target is None:
                plan.deletes.append((category, remote_event.event_id, remote_event.lesson_id))
            elif target.category != category or remote_event.lesson_id in seen:
                plan.deletes.append((category, remote_event.event_id, remote_event.lesson_id))
            else:
                seen.add(remote_event.lesson_id)
                if remote_event.fingerprint != target.fingerprint:
                    plan.updates.append((category, remote_event.event_id, target))
                else:
                    plan.unchanged += 1

    for lesson_id, event in wanted.items():
        if lesson_id not in seen:
            plan.creates.append(event)
    return plan


class UnsafePlanError(RuntimeError):
    """The plan would delete suspiciously much; refuse unless forced."""


def check_deletions(plan: Plan, existing_total: int, *, force: bool = False) -> None:
    """Guard against wiping the calendar because of an empty or truncated fetch."""
    if force or not plan.deletes:
        return
    limit = max(10, existing_total // 2)
    if len(plan.deletes) > limit:
        raise UnsafePlanError(
            f"Refusing to delete {len(plan.deletes)} of {existing_total} existing events "
            "(the portal may have returned an incomplete timetable). "
            "Re-run with --force-delete if this is really what you want."
        )


def apply_plan(plan: Plan, gateway: Gateway, calendar_ids: dict[str, str]) -> None:
    """Execute a plan. `calendar_ids` maps category name -> Google calendar id."""
    for category, event_id, _ in plan.deletes:
        gateway.delete(calendar_ids[category], event_id)
    for category, event_id, event in plan.updates:
        gateway.update(calendar_ids[category], event_id, event.body)
    for event in plan.creates:
        gateway.insert(calendar_ids[event.category], event.body)
