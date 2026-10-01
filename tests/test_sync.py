from datetime import date

import pytest

from calsync.classify import classify
from calsync.events import build_event
from calsync.pipeline import run_sync, window
from calsync.portal import parse_lessons
from calsync.sync import RemoteEvent, UnsafePlanError, build_plan, check_deletions

from .conftest import raw_session

START, END = date(2026, 9, 1), date(2027, 8, 31)


def lessons_from(*sessions):
    return parse_lessons(list(sessions))


def test_event_body_has_marker_title_prefix_and_reminders(config):
    lesson = lessons_from(raw_session(1, code="EX", unit="Thermodynamique", room="Amphi 2"))[0]
    event = build_event(lesson, classify(lesson, config), "Europe/Paris")
    assert event.body["summary"] == "🔴 EX · Thermodynamique"
    assert event.body["location"] == "Amphi 2"
    assert event.body["start"]["timeZone"] == "Europe/Paris"
    assert event.body["reminders"]["overrides"] == [
        {"method": "popup", "minutes": 1440},
        {"method": "popup", "minutes": 60},
    ]
    private = event.body["extendedProperties"]["private"]
    assert private["calsync"] == "auriga" and private["lessonId"] == "1"


def test_fingerprint_changes_when_room_changes(config):
    a = lessons_from(raw_session(1, room="A101"))[0]
    b = lessons_from(raw_session(1, room="B202"))[0]
    fa = build_event(a, classify(a, config), "Europe/Paris").fingerprint
    fb = build_event(b, classify(b, config), "Europe/Paris").fingerprint
    assert fa != fb


def test_first_sync_creates_everything_in_the_right_calendars(config, gateway):
    lessons = lessons_from(
        raw_session(1, code="CM", unit="Maths"),
        raw_session(2, code="BE", unit="Aéro"),
        raw_session(3, code="BEN", unit="Structures"),
    )
    result = run_sync(lessons, config, gateway, START, END)
    assert result.applied and len(result.plan.creates) == 3
    assert set(gateway.calendars) == {
        "Supaero · Classes", "Supaero · BE", "Supaero · Graded (BEN / Exams)"
    }
    assert gateway.colors["Supaero · Graded (BEN / Exams)"] == "11"
    assert sorted(gateway.all_lesson_ids()) == ["1", "2", "3"]


def test_second_sync_is_a_no_op_and_never_duplicates(config, gateway):
    lessons = lessons_from(raw_session(1), raw_session(2, code="BE"))
    run_sync(lessons, config, gateway, START, END)
    writes_after_first = gateway.writes
    result = run_sync(lessons, config, gateway, START, END)
    assert result.plan.changes == 0 and result.plan.unchanged == 2
    assert gateway.writes == writes_after_first
    assert sorted(gateway.all_lesson_ids()) == ["1", "2"]


def test_room_change_updates_in_place(config, gateway):
    run_sync(lessons_from(raw_session(1, room="A101")), config, gateway, START, END)
    result = run_sync(lessons_from(raw_session(1, room="C303")), config, gateway, START, END)
    assert len(result.plan.updates) == 1 and not result.plan.creates and not result.plan.deletes
    bodies = [b for events in gateway.events.values() for b in events.values()]
    assert len(bodies) == 1 and bodies[0]["location"] == "C303"


def test_cancelled_lesson_is_deleted(config, gateway):
    run_sync(lessons_from(raw_session(1), raw_session(2)), config, gateway, START, END)
    result = run_sync(lessons_from(raw_session(1)), config, gateway, START, END)
    assert len(result.plan.deletes) == 1
    assert gateway.all_lesson_ids() == ["1"]


def test_lesson_moving_category_is_moved_not_duplicated(config, gateway):
    run_sync(lessons_from(raw_session(1, code="BE")), config, gateway, START, END)
    run_sync(lessons_from(raw_session(1, code="BE", description="BE noté")), config, gateway, START, END)
    assert gateway.all_lesson_ids() == ["1"]
    assert len(gateway.events[gateway.calendars["Supaero · Graded (BEN / Exams)"]]) == 1
    assert len(gateway.events[gateway.calendars["Supaero · BE"]]) == 0


def test_duplicates_from_an_interrupted_run_are_cleaned_up(config):
    desired = [build_event(lessons_from(raw_session(1))[0], config.default, "Europe/Paris")]
    remote = {"classes": [
        RemoteEvent("a", "1", desired[0].fingerprint),
        RemoteEvent("b", "1", desired[0].fingerprint),
    ]}
    plan = build_plan(desired, remote)
    assert plan.unchanged == 1 and [d[1] for d in plan.deletes] == ["b"]


def test_manual_events_are_never_touched(config, gateway):
    run_sync(lessons_from(raw_session(1)), config, gateway, START, END)
    calendar_id = gateway.calendars["Supaero · Classes"]
    gateway.events[calendar_id]["mine"] = {"summary": "Dentist"}  # no marker
    run_sync(lessons_from(raw_session(1)), config, gateway, START, END)
    run_sync([], config, gateway, START, END, force_delete=True)
    assert gateway.events[calendar_id] == {"mine": {"summary": "Dentist"}}


def test_dry_run_writes_nothing(config, gateway):
    result = run_sync(lessons_from(raw_session(1)), config, gateway, START, END, dry_run=True)
    assert not result.applied and len(result.plan.creates) == 1
    assert gateway.writes == 0 and gateway.calendars == {}


def test_mass_delete_guard_blocks_empty_fetch(config, gateway):
    many = lessons_from(*[raw_session(i, start=f"2026-10-{(i % 27) + 1:02d}T07:00:00Z",
                                      end=f"2026-10-{(i % 27) + 1:02d}T08:00:00Z") for i in range(1, 41)])
    run_sync(many, config, gateway, START, END)
    with pytest.raises(UnsafePlanError):
        run_sync([], config, gateway, START, END)
    assert len(gateway.all_lesson_ids()) == 40
    run_sync([], config, gateway, START, END, force_delete=True)
    assert gateway.all_lesson_ids() == []


def test_small_deletions_do_not_trigger_the_guard():
    from calsync.sync import Plan

    plan = Plan(deletes=[("classes", str(i), str(i)) for i in range(3)])
    check_deletions(plan, existing_total=4)  # 3 <= max(10, 2): allowed


def test_window_is_local_midnight_to_midnight():
    first, last = window(date(2026, 9, 1), date(2026, 9, 30), "Europe/Paris")
    assert first == "2026-09-01T00:00:00+02:00"
    assert last == "2026-10-01T00:00:00+02:00"
