from datetime import date

from calsync.portal import month_chunks, origin, parse_lesson, parse_lessons

from .conftest import raw_session


def test_month_chunks_cover_range_without_gaps():
    chunks = month_chunks(date(2026, 9, 15), date(2027, 1, 10))
    assert chunks[0] == (date(2026, 9, 15), date(2026, 9, 30))
    assert chunks[-1] == (date(2027, 1, 1), date(2027, 1, 10))
    assert len(chunks) == 5
    for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:], strict=False):
        assert (next_start - previous_end).days == 1


def test_month_chunks_single_day_and_empty():
    assert month_chunks(date(2026, 9, 5), date(2026, 9, 5)) == [(date(2026, 9, 5), date(2026, 9, 5))]
    assert month_chunks(date(2026, 9, 6), date(2026, 9, 5)) == []


def test_origin_strips_path_and_fragment():
    url = "https://example.org/#/mainContent/menuEntry/227/planning"
    assert origin(url) == "https://example.org"


def test_parse_lesson_converts_utc_to_paris_time():
    lesson = parse_lesson(raw_session(1, start="2026-10-05T07:30:00Z", end="2026-10-05T09:30:00Z"))
    assert lesson is not None
    assert lesson.start.isoformat() == "2026-10-05T09:30:00+02:00"  # CEST
    winter = parse_lesson(raw_session(2, start="2026-12-07T08:00:00Z", end="2026-12-07T10:00:00Z"))
    assert winter.start.isoformat() == "2026-12-07T09:00:00+01:00"  # CET


def test_parse_lesson_extracts_metadata():
    lesson = parse_lesson(raw_session(7, code="BE", unit="Aérodynamique", room="B12"))
    assert lesson.id == "7"
    assert lesson.title == "Aérodynamique"
    assert lesson.activity == "BE"
    assert lesson.rooms == ("B12",)
    assert lesson.instructors == ("Ada LOVELACE",)
    assert lesson.populations == ("3A SDD",)
    assert lesson.unit_codes == ("U7",)


def test_parse_lesson_falls_back_to_description_when_no_unit():
    lesson = parse_lesson(raw_session(3, unit=None, description="  Forum   des métiers "))
    assert lesson.title == "Forum des métiers"
    assert lesson.topic == ""


def test_parse_lesson_rejects_unusable_records():
    assert parse_lesson({"id": 1, "startDateTime": None, "endDateTime": None}) is None
    assert parse_lesson(raw_session(4, start="2026-10-05T10:00:00Z", end="2026-10-05T09:00:00Z")) is None
    assert parse_lesson({**raw_session(5), "id": None}) is None


def test_parse_lessons_sorted_chronologically():
    lessons = parse_lessons(
        [
            raw_session(2, start="2026-10-06T07:00:00Z", end="2026-10-06T08:00:00Z"),
            raw_session(1, start="2026-10-05T07:00:00Z", end="2026-10-05T08:00:00Z"),
        ]
    )
    assert [lesson.id for lesson in lessons] == ["1", "2"]


def test_ssl_context_keeps_verification_on():
    import ssl

    from calsync.portal import _ssl_context

    context = _ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
