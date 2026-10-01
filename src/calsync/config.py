"""Configuration loading: bundled defaults, optionally overridden by a user file."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "supaero-calsync"
USER_CONFIG = CONFIG_DIR / "config.toml"


class ConfigError(ValueError):
    """The configuration file is malformed."""


@dataclass(frozen=True)
class Category:
    name: str
    calendar: str  # name of the Google calendar this category lives in
    color_id: str  # Google calendar colour index, "1".."11"
    prefix: str = ""  # prepended to every event title in this category
    rules: tuple[re.Pattern[str], ...] = ()
    exam_flag: bool = False  # also match lessons the portal marks as exams
    reminders: tuple[int, ...] = ()  # popup reminders, minutes before start


@dataclass(frozen=True)
class Config:
    portal_url: str
    browser: str
    timezone: str
    categories: tuple[Category, ...]  # ordered, first match wins
    default: Category = field(default=None)  # type: ignore[assignment]

    @property
    def all_categories(self) -> tuple[Category, ...]:
        return (*self.categories, self.default)


def default_config_text() -> str:
    return resources.files("calsync").joinpath("default_config.toml").read_text("utf-8")


def _category(raw: dict, *, where: str) -> Category:
    try:
        rules = tuple(re.compile(pattern, re.IGNORECASE) for pattern in raw.get("rules", []))
    except re.error as exc:
        raise ConfigError(f"{where}: invalid regular expression: {exc}") from exc
    for key in ("name", "calendar", "color_id"):
        if key not in raw:
            raise ConfigError(f"{where}: missing '{key}'")
    return Category(
        name=raw["name"],
        calendar=raw["calendar"],
        color_id=str(raw["color_id"]),
        prefix=raw.get("prefix", ""),
        rules=rules,
        exam_flag=bool(raw.get("exam_flag", False)),
        reminders=tuple(int(m) for m in raw.get("reminders", [])),
    )


def parse_config(text: str) -> Config:
    """Build a Config from TOML text. Raises ConfigError on anything malformed."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc

    if "default" not in raw:
        raise ConfigError("missing [default] category")
    categories = tuple(
        _category(item, where=f"categories[{i}]") for i, item in enumerate(raw.get("categories", []))
    )
    default = _category(raw["default"], where="[default]")

    names = [c.name for c in (*categories, default)]
    calendars = [c.calendar for c in (*categories, default)]
    if len(set(names)) != len(names):
        raise ConfigError("category names must be unique")
    if len(set(calendars)) != len(calendars):
        raise ConfigError("each category needs its own calendar name")

    portal = raw.get("portal", {})
    return Config(
        portal_url=portal.get("url", ""),
        browser=portal.get("browser", "chrome"),
        timezone=raw.get("google", {}).get("timezone", "Europe/Paris"),
        categories=categories,
        default=default,
    )


def load_config(path: Path | None = None) -> Config:
    """Load `path`, else the user config if it exists, else the bundled defaults."""
    if path is not None:
        return parse_config(path.read_text("utf-8"))
    if USER_CONFIG.exists():
        return parse_config(USER_CONFIG.read_text("utf-8"))
    return parse_config(default_config_text())
