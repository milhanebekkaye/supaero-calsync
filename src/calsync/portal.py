"""Read the timetable from the Auriga API and normalise it into `Lesson` objects."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import certifi

from .models import Lesson

API_PATH = "/api/plannings/me"
PARIS = ZoneInfo("Europe/Paris")


class PortalError(RuntimeError):
    """The portal API rejected a request or returned something unusable."""


def _ssl_context() -> ssl.SSLContext:
    """TLS context that trusts the operating system's certificate store.

    Python often cannot verify sites that browsers accept, for two reasons: some
    servers omit their intermediate certificates (browsers fetch the missing ones,
    Python does not), and some networks inspect HTTPS with a certificate installed
    in the system keychain that Python's own bundle has never heard of. Using the
    OS store, like a browser does, handles both. Verification stays fully on.
    Falls back to certifi's bundle if the OS-store helper is unavailable.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context(cafile=certifi.where())


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split an inclusive date range into calendar-month pieces (first/last may be partial)."""
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(next_month - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _request_url(base: str, start: date, end: date) -> str:
    params = [("days", str(d)) for d in range(1, 8)]
    params += [("startDate", start.isoformat()), ("endDate", end.isoformat())]
    return f"{base}{API_PATH}?{urlencode(params)}"


def fetch_interventions(
    base_url: str, token: str, start: date, end: date, *, timeout: float = 60.0
) -> list[dict[str, Any]]:
    """Fetch every raw session between two dates, one month per request.

    Raises PortalError on a rejected token (401/403) or repeated network failure.
    """
    by_id: dict[Any, dict[str, Any]] = {}
    for chunk_start, chunk_end in month_chunks(start, end):
        request = urllib.request.Request(
            _request_url(base_url, chunk_start, chunk_end),
            headers={
                "Authorization": token,
                "Accept": "application/json",
                "x-scope": "frontend",
                "accept-language": "fr",
            },
        )
        payload: dict[str, Any] | None = None
        for attempt in (1, 2, 3):
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise PortalError(
                        "The portal rejected the session token (expired?). Run again to log in."
                    ) from exc
                if attempt == 3:
                    raise PortalError(f"Portal returned HTTP {exc.code} for {chunk_start}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == 3:
                    raise PortalError(f"Could not fetch {chunk_start}: {exc}") from exc
            time.sleep(2 * attempt)

        for position, item in enumerate((payload or {}).get("interventions") or []):
            key = item.get("id")
            by_id[key if key is not None else f"{chunk_start}/{position}"] = item
    return sorted(by_id.values(), key=lambda i: str(i.get("startDateTime") or ""))


# --------------------------------------------------------------------------- #
# Normalising
# --------------------------------------------------------------------------- #


def _caption(node: dict[str, Any] | None) -> str:
    """Bilingual label objects look like {"fr": ..., "en": ...}; prefer French."""
    if not node:
        return ""
    return (node.get("fr") or node.get("en") or "").strip()


def _parse_instant(value: str | None) -> datetime | None:
    """Portal timestamps are UTC ('...Z'); return an aware Europe/Paris datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(PARIS)


def _squash(text: str | None) -> str:
    return " ".join((text or "").split())


def parse_lesson(raw: dict[str, Any]) -> Lesson | None:
    """Turn one raw session into a Lesson; None if it has no usable time span."""
    start = _parse_instant(raw.get("startDateTime"))
    end = _parse_instant(raw.get("endDateTime"))
    if raw.get("id") is None or not start or not end or end <= start:
        return None

    units = [
        link["pedagogicalUnit"]
        for link in raw.get("interventionPedagogicalUnits") or []
        if link.get("pedagogicalUnit")
    ]
    unit_titles = [_caption(u.get("caption")) for u in units]
    topic = _squash(raw.get("description"))
    title = " + ".join(t for t in unit_titles if t) or topic or "(untitled)"

    def labels(key: str, inner: str) -> tuple[str, ...]:
        found = {_caption((link.get(inner) or {}).get("caption")) for link in raw.get(key) or []}
        return tuple(sorted(label for label in found if label))

    instructors = {
        " ".join(
            p
            for p in (
                (link.get("person") or {}).get("currentFirstName"),
                (link.get("person") or {}).get("currentLastName"),
            )
            if p
        )
        for link in raw.get("interventionInstructors") or []
    }

    return Lesson(
        id=str(raw["id"]),
        start=start,
        end=end,
        title=title,
        activity=((raw.get("activityType") or {}).get("code") or "").strip(),
        topic=topic if topic != title else "",
        instructors=tuple(sorted(name for name in instructors if name)),
        rooms=labels("interventionResources", "resource"),
        populations=labels("interventionPopulations", "population"),
        unit_codes=tuple(sorted(u["code"] for u in units if u.get("code"))),
        is_exam=bool(raw.get("isExam")),
    )


def parse_lessons(interventions: list[dict[str, Any]]) -> list[Lesson]:
    """Normalise a raw payload, silently dropping records without a valid time span."""
    lessons = [lesson for raw in interventions if (lesson := parse_lesson(raw))]
    return sorted(lessons, key=lambda lesson: (lesson.start, lesson.id))
