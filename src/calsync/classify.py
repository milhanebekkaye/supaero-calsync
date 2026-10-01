"""Decide which category (and therefore which calendar and colour) a lesson belongs to."""

from __future__ import annotations

from collections import defaultdict

from .config import Category, Config
from .models import Lesson


def classify(lesson: Lesson, config: Config) -> Category:
    """Return the first category whose rules match, else the default category."""
    text = lesson.searchable_text
    for category in config.categories:
        if category.exam_flag and lesson.is_exam:
            return category
        if any(rule.search(text) for rule in category.rules):
            return category
    return config.default


def is_ignored(lesson: Lesson, config: Config) -> bool:
    """True when the lesson matches an [ignore] rule and must not reach the calendar."""
    text = lesson.searchable_text
    return any(rule.search(text) for rule in config.ignore)


def filter_ignored(lessons: list[Lesson], config: Config) -> tuple[list[Lesson], list[Lesson]]:
    """Split lessons into (kept, ignored)."""
    kept: list[Lesson] = []
    ignored: list[Lesson] = []
    for lesson in lessons:
        (ignored if is_ignored(lesson, config) else kept).append(lesson)
    return kept, ignored


def classify_all(lessons: list[Lesson], config: Config) -> dict[str, Category]:
    """Map lesson id -> category for a whole timetable."""
    return {lesson.id: classify(lesson, config) for lesson in lessons}


def summarize(
    lessons: list[Lesson], assignment: dict[str, Category], config: Config
) -> dict[str, dict[str, int]]:
    """Per category, how many sessions each distinct title contributes.

    Used by the CLI preview so a misclassified course title is easy to spot and
    fix in the config file.
    """
    summary: dict[str, dict[str, int]] = {c.name: defaultdict(int) for c in config.all_categories}
    for lesson in lessons:
        label = f"{lesson.activity} · {lesson.title}" if lesson.activity else lesson.title
        summary[assignment[lesson.id].name][label] += 1
    return {name: dict(titles) for name, titles in summary.items()}
